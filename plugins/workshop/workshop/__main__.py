"""Usage: workshop tick | advance | start <repo> <key> [--override] | status | explain <key> | resume <key> | list | doctor [--no-launch]"""

import fcntl
import json
import os
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .doctor import doctor
from .engine import Engine, Park
from .pipeline import PipelineError, load
from .shell import ToolError
from .sources import Source, make_source
from .store import Run, Store
from .tools import Tools


def config_path() -> Path:
    return Path(os.environ.get("WORKSHOP_CONFIG", "~/.config/workshop/config.toml")).expanduser()


def config() -> dict[str, Any]:
    return tomllib.loads(config_path().read_text(encoding="utf-8"))


def state_root() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "workshop"


def source_for(cfg: dict[str, Any]) -> Callable[[Run], Source]:
    return lambda run: make_source(run.repo, cfg["repos"][run.repo], cfg.get("jira", {}))


def begin(engine: Engine, cfg: dict[str, Any], name: str, key: str, override: bool = False) -> Run:
    repo = cfg["repos"][name]
    source = make_source(name, repo, cfg.get("jira", {}))
    path = Path(repo["path"]).expanduser()
    return engine.start(name, key, path, repo.get("base", "main"), load(repo.get("pipeline", "ticket")), source, override)


def advance_all(engine: Engine) -> None:
    for run in engine.store.all():
        if run.status == "running":
            engine.advance(run)


def tick(engine: Engine, cfg: dict[str, Any]) -> None:
    """Move every run on, then start one ready ticket if there's room. Polling costs no tokens."""
    advance_all(engine)
    active = [r for r in engine.store.all() if r.status == "running"]
    if len(active) >= cfg.get("max_active", 2):
        return
    skipped: dict[str, str] = {}
    for name, repo in cfg["repos"].items():
        source = make_source(name, repo, cfg.get("jira", {}))
        for key in source.ready():
            if engine.store.exists(name, key):
                continue
            if reason := source.guard(key):
                skipped[f"{name}/{key}"] = reason
                continue
            print(f"{name}: starting {key}")
            begin(engine, cfg, name, key)
            save_skipped(skipped)
            return
    save_skipped(skipped)


def save_skipped(skipped: dict[str, str]) -> None:
    (state_root() / "skipped.json").write_text(json.dumps(skipped, indent=2), encoding="utf-8")


def show_status(engine: Engine) -> None:
    for run in engine.store.all():
        step = run.current["id"] if run.current else "-"
        print(f"{run.status:8} {run.repo}/{run.key} {step} {run.reason or run.pr}")
    skipped = state_root() / "skipped.json"
    if skipped.exists():
        for ticket, reason in json.loads(skipped.read_text(encoding="utf-8")).items():
            print(f"skipped  {ticket} {reason}")


def show_doctor(cfg: dict[str, Any], launch: bool) -> int:
    results = doctor(cfg, launch)
    for r in results:
        print(f"{'ok  ' if r.ok else 'FAIL'} {r.name}{': ' + r.detail if r.detail and (not r.ok or 'ready' in r.detail) else ''}")
    return 0 if all(r.ok for r in results) else 1


def locked(root: Path, work: Callable[[], None]) -> None:
    with (root / "lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # another tick has this; the next one catches up
        work()


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    if command not in ("tick", "advance", "start", "status", "explain", "resume", "list", "doctor"):
        print(__doc__, file=sys.stderr)
        return 2
    if command == "advance" and not config_path().exists():
        return 0  # herdr fires this for everyone who has the plugin, configured or not
    if not config_path().exists():
        print(f"workshop: no config at {config_path()}. Copy config.example.toml there.", file=sys.stderr)
        return 1
    cfg = config()
    root = state_root()
    root.mkdir(parents=True, exist_ok=True)
    engine = Engine(Tools(), Store(root), source_for(cfg))
    try:
        match command:
            case "tick":
                locked(root, lambda: tick(engine, cfg))
            case "advance":
                locked(root, lambda: advance_all(engine))
            case "start":
                locked(root, lambda: print(engine.explain(begin(engine, cfg, argv[1], argv[2], "--override" in argv))))
            case "status":
                show_status(engine)
            case "explain":
                print(engine.explain(engine.store.find(argv[1])))
            case "resume":
                locked(root, lambda: print(engine.explain(engine.resume(engine.store.find(argv[1])))))
            case "list":
                for name, repo in cfg["repos"].items():
                    print(name, *make_source(name, repo, cfg.get("jira", {})).ready())
            case "doctor":
                return show_doctor(cfg, "--no-launch" not in argv)
        return 0
    except (Park, PipelineError, ToolError, KeyError) as stop:
        print(f"workshop: {stop}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
