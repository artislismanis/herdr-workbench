"""Usage: workshop tick | advance | start <repo> <key> [--override] [--restart] | status | needs-you | explain <key> | resume <key> | pr <url> [--claimed|--silent] | claim <key> | review <key> | finish | approve <key> | reject <key> <note> | answer <key> <text> | retry <key> [note] | pass <key> [note] | drop <key> [reason] | comment <key> <text> | list | doctor [--no-launch] | schedule [<every>|off] | stats | refresh | board | open-board"""

import fcntl
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .backlog import poll
from .board import show
from .doctor import doctor
from .engine import PREFIX, STOPS, Engine, Park
from .pipeline import PipelineError, load
from .schedule import schedule
from .shell import ToolError, run, run_json
from .sources import CLAIMS, PullRequestSource, Source, make_source
from .stats import stats
from .store import Run, Store
from .throttle import Throttle, usage_path
from .tools import Tools


PATIENCE = 60  # seconds a person's command waits for a tick to finish
# Each command and how many arguments it needs.
COMMANDS = {"tick": 0, "advance": 0, "start": 2, "status": 0, "needs-you": 0, "explain": 1, "resume": 1, "pr": 1, "claim": 1, "review": 1, "finish": 0, "approve": 1, "reject": 2, "answer": 2, "retry": 1, "pass": 1, "drop": 1, "comment": 2, "list": 0, "doctor": 0, "schedule": 0, "stats": 0, "refresh": 0, "board": 0, "open-board": 0}


def config_path() -> Path:
    return Path(os.environ.get("WORKSHOP_CONFIG", "~/.config/workshop/config.toml")).expanduser()


def config() -> dict[str, Any]:
    return tomllib.loads(config_path().read_text(encoding="utf-8"))


def state_root() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "workshop"


def review_settings(cfg: dict[str, Any], repo: dict[str, Any]) -> dict[str, str]:
    """Yours, and a repo's can differ. The defaults assume nothing about how a team claims reviews."""
    found = {"mode": "silent", "claim": "none"} | cfg.get("review", {}) | repo.get("review", {})
    if found["mode"] not in ("silent", "claimed") or found["claim"] not in CLAIMS:
        raise KeyError(f"[review] takes mode silent or claimed, and claim {', '.join(CLAIMS)}")
    return found


def source_for(cfg: dict[str, Any]) -> Callable[[Run], Source]:
    def find(run: Run) -> Source:
        repo = cfg["repos"][run.repo]
        return PullRequestSource(Path(repo["path"]).expanduser(), review_settings(cfg, repo)["claim"]) if run.reviewing else make_source(run.repo, repo, cfg.get("jira", {}))

    return find


def begin(engine: Engine, cfg: dict[str, Any], name: str, key: str, override: bool = False, restart: bool = False) -> Run:
    repo = cfg["repos"][name]
    source = make_source(name, repo, cfg.get("jira", {}))
    path = Path(repo["path"]).expanduser()
    return engine.start(name, key, path, repo.get("base", "main"), load(repo.get("pipeline", "ticket")), source, override, restart, repo.get("commands"))


def repo_of(cfg: dict[str, Any], url: str) -> tuple[str, str]:
    """The configured repo and number of a PR. Matched on origin, because a repo's table can have any name."""
    found = re.fullmatch(r"https://[^/]+/([^/]+)/([^/]+)/pull/(\d+)/?", url)
    if found is None:
        raise KeyError(f"not a PR link: {url}")
    for name, repo in cfg["repos"].items():
        path = Path(repo["path"]).expanduser()
        if path.is_dir() and re.split("[:/]", run("git", "remote", "get-url", "origin", cwd=path).strip().removesuffix(".git"))[-2:] == [found[1], found[2]]:
            return name, found[3]
    raise KeyError(f"{found[1]}/{found[2]} needs a [repos] table with its path before workshop can review its PRs")


def begin_review(engine: Engine, cfg: dict[str, Any], url: str, mode: str = "") -> Run:
    name, number = repo_of(cfg, url)
    repo = cfg["repos"][name]
    path = Path(repo["path"]).expanduser()
    settings = review_settings(cfg, repo)
    claimed = (mode or settings["mode"]) == "claimed"
    source = PullRequestSource(path, settings["claim"] if claimed else "none")
    key = f"pr-{number}"
    again = engine.store.exists(name, key) and engine.review_ready(engine.store.load(name, key))
    return engine.start(name, key, path, source.details(key)["baseRefName"], load(repo.get("review_pipeline", "review")), source, restart=again, commands=repo.get("commands"), pr=url.rstrip("/"), claimed=claimed)


def advance_all(engine: Engine) -> None:
    for run in engine.store.all():
        run = engine.answered_in_pane(run)
        if run.status == "running":
            engine.advance(run)


def check_tickets(engine: Engine) -> None:
    for run in engine.store.all():
        if run.reviewing and not run.claimed and not run.landed:
            run = engine.watch(run)
        if run.blocked_at:
            engine.advance(engine.unblock(run))
        elif run.status == "done" and run.pr and not run.landed:
            engine.land(run)


def running(engine: Engine) -> list[Run]:
    return [r for r in engine.store.all() if r.status == "running"]


def read_limits(engine: Engine) -> None:
    engine.throttle.update(engine.tools.claude_usage())


def no_room(engine: Engine, cfg: dict[str, Any]) -> bool:
    active = running(engine)
    return len(active) >= cfg.get("max_active", 2) or bool(engine.throttle.hold_new(len(active)))


def tick(engine: Engine, cfg: dict[str, Any]) -> None:
    """Move every run on, then start ready tickets while there's room. Polling costs no tokens."""
    read_limits(engine)
    advance_all(engine)
    check_tickets(engine)
    poll(cfg, engine.store)  # before the limits below, or a full workshop stops the board updating
    if no_room(engine, cfg):
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
            if no_room(engine, cfg):
                save_skipped(skipped)
                return
    save_skipped(skipped)


def save_skipped(skipped: dict[str, str]) -> None:
    (state_root() / "skipped.json").write_text(json.dumps(skipped, indent=2), encoding="utf-8")


def show_status(engine: Engine) -> None:
    for run in engine.store.all():
        step = run.current["id"] if run.current else "-"
        print(f"{run.label:9} {run.repo}/{run.key} {step} {run.held or run.reason or run.pr}")
    if held := engine.throttle.hold_new(len(running(engine))):
        print(f"held     new tickets: {held}")
    skipped = state_root() / "skipped.json"
    if skipped.exists():
        for ticket, reason in json.loads(skipped.read_text(encoding="utf-8")).items():
            if not engine.store.exists(*ticket.split("/", 1)):
                print(f"skipped  {ticket} {reason}")


def show_needs_you(engine: Engine) -> None:
    for run in engine.store.all():
        if run.status in ("parked", "waiting"):
            print(f"{run.label:9} {run.repo}/{run.key} {run.reason}\n          {engine.next_action(run)}")


def show_refresh(cfg: dict[str, Any], store: Store) -> None:
    snapshot = poll(cfg, store)
    for name, repo in snapshot["repos"].items():
        print(f"{name}: {repo['error'] or str(len(repo['items'])) + ' open'}")
    print(f"PRs: {snapshot['prs']['error'] or len(snapshot['prs']['items'])}")


def show_doctor(cfg: dict[str, Any], launch: bool) -> int:
    results = doctor(cfg, launch)
    for r in results:
        print(f"{'ok  ' if r.ok else 'FAIL'} {r.name}{': ' + r.detail if r.detail and (not r.ok or 'ready' in r.detail) else ''}")
    return 0 if all(r.ok for r in results) else 1


def locked(root: Path, work: Callable[[], None], patience: float = 0) -> bool:
    deadline = time.monotonic() + patience
    with (root / "lock").open("w") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return False  # another tick has this; the next one catches up
                time.sleep(0.2)
        work()
        return True


def waited(root: Path, work: Callable[[], None]) -> None:
    """For what a person asks for, which no later tick would pick up."""
    if not locked(root, work, PATIENCE):
        raise ToolError("Workshop is busy, so nothing happened. Try again in a moment.")


def here() -> str:
    """The checkout of the pane the popup opened over."""
    pane = os.environ.get("HERDR_ACTIVE_PANE_ID")
    cwd = run_json("herdr", "pane", "get", pane)["result"]["pane"]["cwd"] if pane else os.getcwd()
    return run("git", "rev-parse", "--show-toplevel", cwd=cwd).strip()


def post_review(path: str, *args: str) -> int:
    return subprocess.run([str(Path.home() / ".config/herdr/bin/hunk-pr-push"), *args, path]).returncode


def finish_choices(changed: list[str], draft: bool) -> list[tuple[str, str, str]]:
    """Only what can happen now: posting needs a clean tree, and a PR is marked ready once."""
    found = [("p", "push the fixes", "push")] if changed else []
    found += [("r", "push any fixes and mark the PR ready" if changed else "mark the PR ready", "ready")] if draft else []
    return found + ([("c", "post the review notes as a PR comment", "comment")] if not changed else [])


def finish_review(engine: Engine, run: Run, path: str) -> int:
    """Going visible in one step: anything new shows first, then after your go the claim goes out just before the review."""
    source = engine.source_for(run)
    if run.claimed or source.action == "none":
        return post_review(path)
    for seen in source.activity(run.key, run.begun):
        print(f"Since you started: {seen}")
    return post_review(path, "--before", f"{Path.home()}/.local/bin/workshop claim {run.key}")


def finish(engine: Engine, root: Path) -> int:
    """Ends a review from the workspace it happened in. Anything that isn't a delivered run is a plain PR review."""
    path = here()
    if reviewing := next((r for r in engine.store.all() if r.worktree == path and r.reviewing), None):
        return finish_review(engine, reviewing, path)
    delivered = next((r for r in engine.store.all() if r.worktree == path and engine.delivered(r)), None)
    if delivered is None:
        return post_review(path)
    behind = engine.tools.unpushed(Path(path), delivered.branch)
    changed = engine.tools.changes(Path(path)) + ([f"{behind} commit(s) not pushed"] if behind else [])
    print(f"{delivered.repo}/{delivered.key}  {delivered.pr}")
    print("\n".join(["Changed since the PR:", *changed]) if changed else "Nothing has changed since the PR.")
    print("\nTo change it, comment in hunk and send those to the agent with prefix+shift+s.\n")
    choices = finish_choices(changed, engine.reviewable(delivered))
    for key, label, _ in choices:
        print(f"[{key}] {label}")
    try:
        answer = input("[n] cancel: ").strip().lower()
    except EOFError:
        answer = "n"
    action = next((a for key, _, a in choices if key == answer), "")
    if action == "comment":
        return post_review(path, "--as", "comment")
    if action:
        outcome = ["Workshop is busy, so nothing happened. Try again in a moment."]

        def act() -> None:
            try:
                # You may have taken a while to choose, and a tick can have moved it on.
                now = engine.store.load(delivered.repo, delivered.key)
                engine.push_fixes(now) if action == "push" else engine.ready(now)
                outcome[0] = "Pushed." if action == "push" else "Marked ready."
            except STOPS as failed:
                outcome[0] = str(failed)

        locked(root, act)
        print(outcome[0])
        input("Enter closes this.")
    return 0


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    if command not in COMMANDS or len(argv) <= COMMANDS[command]:
        print(__doc__, file=sys.stderr)
        return 2
    if command == "advance" and not config_path().exists():
        return 0  # herdr fires this for everyone who has the plugin, configured or not
    if command == "finish" and not config_path().exists():
        return post_review(here())  # no runs here, so it's a plain PR review
    if not config_path().exists():
        print(f"workshop: no config at {config_path()}. Copy config.example.toml there.", file=sys.stderr)
        return 1
    try:
        cfg = config()
    except tomllib.TOMLDecodeError as bad:
        print(f"workshop: {config_path()} isn't valid TOML: {bad}", file=sys.stderr)
        return 1
    prefix = str(cfg.get("comment_prefix", PREFIX)).strip()
    if not prefix:
        # Without one, workshop's comments would read as yours and nothing could tell them apart.
        print(f"workshop: comment_prefix in {config_path()} can't be empty", file=sys.stderr)
        return 1
    root = state_root()
    root.mkdir(parents=True, exist_ok=True)
    engine = Engine(Tools(), Store(root), source_for(cfg), Throttle(usage_path(), cfg.get("throttle", {})), prefix=prefix)
    try:
        match command:
            case "tick":
                locked(root, lambda: tick(engine, cfg))
            case "advance":
                locked(root, lambda: advance_all(engine))
            case "start":
                waited(root, lambda: print(engine.explain(begin(engine, cfg, argv[1], argv[2], "--override" in argv, "--restart" in argv))))
            case "status":
                show_status(engine)
            case "needs-you":
                show_needs_you(engine)
            case "explain":
                print(engine.explain(engine.store.find(argv[1])))
            case "resume":
                waited(root, lambda: print(engine.explain(engine.resume(engine.store.find(argv[1])))))
            case "pr":
                mode = "claimed" if "--claimed" in argv else "silent" if "--silent" in argv else ""
                waited(root, lambda: print(engine.explain(begin_review(engine, cfg, argv[1], mode))))
            case "claim":
                waited(root, lambda: print(engine.explain(engine.claim(engine.store.find(argv[1])))))
            case "review":
                waited(root, lambda: print(engine.review(engine.store.find(argv[1]))))
            case "finish":
                return finish(engine, root)
            case "approve":
                waited(root, lambda: print(engine.explain(engine.approve(engine.store.find(argv[1])))))
            case "reject":
                waited(root, lambda: print(engine.explain(engine.reject(engine.store.find(argv[1]), argv[2]))))
            case "answer":
                waited(root, lambda: print(engine.explain(engine.answer(engine.store.find(argv[1]), argv[2]))))
            case "retry":
                waited(root, lambda: print(engine.explain(engine.retry(engine.store.find(argv[1]), " ".join(argv[2:])))))
            case "pass":
                waited(root, lambda: print(engine.explain(engine.accept(engine.store.find(argv[1]), " ".join(argv[2:])))))
            case "drop":
                waited(root, lambda: print(engine.explain(engine.drop(engine.store.find(argv[1]), " ".join(argv[2:])))))
            case "comment":
                waited(root, lambda: print(engine.explain(engine.comment(engine.store.find(argv[1]), " ".join(argv[2:])))))
            case "list":
                for name, repo in cfg["repos"].items():
                    print(name, *make_source(name, repo, cfg.get("jira", {})).ready())
            case "doctor":
                return show_doctor(cfg, "--no-launch" not in argv)
            case "schedule":
                print(schedule(argv[1:]))
            case "stats":
                print(stats(engine.store.history()))
            case "refresh":
                read_limits(engine)
                locked(root, lambda: check_tickets(engine))
                show_refresh(cfg, engine.store)
            case "board":
                show(engine, cfg)
            case "open-board":
                engine.tools.open_board()
        return 0
    except (Park, PipelineError, ToolError, KeyError) as stop:
        print(f"workshop: {stop}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
