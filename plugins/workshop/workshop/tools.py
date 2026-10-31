"""The outside world: wt, herdr, git and gh. Tests swap this class for a fake."""

import json
import re
import subprocess
from pathlib import Path

from .shell import ToolError, run, run_json

IGNORED = ".workshop/"
# Running a repo's checks leaves these behind, even where it doesn't ignore them.
GENERATED = ("__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".venv", "node_modules")


def slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")


class Tools:
    def identity_ok(self, repo: Path) -> bool:
        settings = ("user.name", "user.email")
        return all(subprocess.run(["git", "config", s], cwd=repo, capture_output=True).returncode == 0 for s in settings)

    def worktree(self, repo: Path, branch: str, base: str) -> Path:
        # wt, not herdr's own worktree command, so the setup hooks run.
        known = subprocess.run(["git", "rev-parse", "--verify", "--quiet", branch], cwd=repo, capture_output=True)
        args = [branch] if known.returncode == 0 else ["--create", branch, "--base", base]
        return Path(run_json("wt", "switch", *args, "--no-cd", "--yes", "--format=json", cwd=repo)["path"])

    def ignore_scratch(self, work: Path) -> None:
        # Kept out of git: the ticket text stays local and never reaches the PR.
        common = run("git", "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=work).strip()
        with (Path(common) / "info" / "exclude").open("a+", encoding="utf-8") as exclude:
            exclude.seek(0)
            if IGNORED not in exclude.read():
                exclude.write(IGNORED + "\n")

    def workspace(self, path: Path, label: str) -> str:
        made = run_json("herdr", "workspace", "create", "--cwd", str(path), "--label", label, "--no-focus")
        return made["result"]["root_pane"]["pane_id"]

    def split(self, pane: str, path: Path) -> str:
        made = run_json("herdr", "pane", "split", pane, "--direction", "right", "--cwd", str(path), "--no-focus")
        return made["result"]["pane"]["pane_id"]

    def close_pane(self, pane: str) -> None:
        subprocess.run(["herdr", "pane", "close", pane], capture_output=True)

    def notify(self, title: str, body: str) -> None:
        # Best effort: a notification must never stop a run.
        subprocess.run(["herdr", "notification", "show", title, "--body", body, "--sound", "request"], capture_output=True)

    def agent_status(self, name: str) -> str:
        """The agent's status, or "gone" if herdr no longer knows it."""
        done = subprocess.run(["herdr", "agent", "get", name], capture_output=True, text=True)
        if done.returncode != 0:
            return "gone"
        return json.loads(done.stdout)["result"]["agent"]["agent_status"]
        return json.loads(done.stdout)["result"]["agent"]["agent_status"]

    def start_agent(self, name: str, pane: str, model: str = "") -> None:
        run("herdr", "agent", "start", name, "--kind", "claude", "--pane", pane, *(["--", "--model", model] if model else []))

    def prompt(self, name: str, text: str) -> None:
        # Waits only until the agent has started, so "idle" afterwards means it finished.
        run("herdr", "agent", "prompt", name, text, "--wait", "--until", "working", "--timeout", "60000")

    def changes(self, work: Path) -> list[str]:
        lines = run("git", "status", "--porcelain", "--untracked-files=all", cwd=work).splitlines()
        return sorted(line for line in lines if not any(f"{g}/" in line for g in GENERATED))

    def stage(self, work: Path) -> None:
        excludes = [f":(exclude,glob)**/{g}/**" for g in GENERATED]
        run("git", "add", "-A", "--", ".", *excludes, cwd=work)

    def commit_all(self, work: Path, message: str) -> None:
        self.stage(work)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=work).returncode != 0:
            run("git", "commit", "-m", message, cwd=work)

    def ahead(self, work: Path, base: str) -> int:
        return int(run("git", "rev-list", "--count", f"origin/{base}..HEAD", cwd=work).strip())

    def push(self, work: Path, branch: str) -> None:
        run("git", "push", "-u", "origin", branch, cwd=work)

    def find_pr(self, work: Path, branch: str) -> str:
        found = run_json("gh", "pr", "list", "--head", branch, "--state", "open", "--json", "url", cwd=work)
        return found[0]["url"] if found else ""

    def create_pr(self, work: Path, branch: str, base: str, title: str, body: str) -> str:
        made = run("gh", "pr", "create", "--draft", "--base", base, "--head", branch, "--title", title, "--body", body, cwd=work)
        return made.strip().splitlines()[-1]


__all__ = ["ToolError", "Tools", "slug"]
