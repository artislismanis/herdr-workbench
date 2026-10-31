"""Usage: workshop tick | workshop run <repo> <key> | workshop list"""

import fcntl
import os
import sys
import tomllib
from pathlib import Path
from typing import Any

from .runner import NeedsYou, run_ticket
from .shell import ToolError
from .sources import make_source


def config_path() -> Path:
    return Path(os.environ.get("WORKSHOP_CONFIG", "~/.config/workshop/config.toml")).expanduser()


def run_one(name: str, repo: dict[str, Any], jira: dict[str, str], key: str) -> None:
    source = make_source(name, repo, jira)
    run_ticket(Path(repo["path"]).expanduser(), source, key, repo.get("base", "main"), repo.get("rounds", 2))


def tick(cfg: dict[str, Any]) -> int:
    """One ticket per call, so a cron schedule works through the queue in turn."""
    state = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "workshop"
    state.mkdir(parents=True, exist_ok=True)
    with (state / "lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("A run is in progress.")
            return 0
        for name, repo in cfg["repos"].items():
            ready = make_source(name, repo, cfg.get("jira", {})).ready()
            if ready:
                print(f"{name}: running {ready[0]}")
                run_one(name, repo, cfg.get("jira", {}), ready[0])
                return 0
    print("Nothing ready.")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("tick", "run", "list"):
        print(__doc__, file=sys.stderr)
        return 2
    cfg = tomllib.loads(config_path().read_text(encoding="utf-8"))
    try:
        if argv[0] == "tick":
            return tick(cfg)
        if argv[0] == "list":
            for name, repo in cfg["repos"].items():
                print(name, *make_source(name, repo, cfg.get("jira", {})).ready())
            return 0
        run_one(argv[1], cfg["repos"][argv[1]], cfg.get("jira", {}), argv[2])
        return 0
    except (NeedsYou, ToolError) as stop:
        print(f"workshop: {stop}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
