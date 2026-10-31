"""The outside world: wt, herdr, git and gh. Tests swap this class for a fake."""

import json
import os
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from . import cost
from .shell import ToolError, run, run_json

BOARD = "workshop"  # the board's workspace
BOARD_TAB = "Dashboard"
REVIEW_WAIT = 40  # half-second checks while hunk starts
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

    def workspace(self, path: Path, label: str) -> str:
        made = run_json("herdr", "workspace", "create", "--cwd", str(path), "--label", label, "--no-focus")
        return made["result"]["root_pane"]["pane_id"]

    def focus(self, pane: str) -> None:
        run("herdr", "workspace", "focus", pane.split(":")[0])

    def open_board(self) -> None:
        """A second press goes back to the same board. Quitting closes its workspace, so one that's open is live."""
        for space in run_json("herdr", "workspace", "list")["result"]["workspaces"]:
            if space.get("label") == BOARD:
                run("herdr", "workspace", "focus", space["workspace_id"])
                return
        made = run_json("herdr", "workspace", "create", "--cwd", str(Path.home()), "--label", BOARD, "--focus")
        run("herdr", "tab", "rename", made["result"]["tab"]["tab_id"], BOARD_TAB)
        run("herdr", "pane", "run", made["result"]["root_pane"]["pane_id"], f"exec {Path.home()}/.local/bin/workshop board")
        self._to_top(made["result"]["workspace"]["workspace_id"])

    def _to_top(self, workspace: str) -> None:
        # Only the socket API can reorder workspaces. The board works anywhere, so failing here doesn't matter.
        path = os.environ.get("HERDR_SOCKET_PATH", str(Path.home() / ".config/herdr/herdr.sock"))
        request = {"id": "workshop", "method": "workspace.move", "params": {"workspace_id": workspace, "insert_index": 0}}
        try:
            with socket.socket(socket.AF_UNIX) as server:
                server.settimeout(5)
                server.connect(path)
                server.sendall(json.dumps(request).encode() + b"\n")
                server.recv(4096)
        except OSError:
            pass

    def split(self, pane: str, path: Path) -> str:
        made = run_json("herdr", "pane", "split", pane, "--direction", "right", "--cwd", str(path), "--no-focus")
        return made["result"]["pane"]["pane_id"]

    def spent(self, work: Path, since: float, until: float) -> dict[str, Any]:
        return cost.spent(work, since, until)

    def pr_state(self, url: str) -> dict[str, Any]:
        return run_json("gh", "pr", "view", url, "--json", "state,isDraft,reviewDecision")

    def close_pane(self, pane: str) -> None:
        subprocess.run(["herdr", "pane", "close", pane], capture_output=True)

    def open_review(self, pane: str, work: Path, base: str) -> list[dict[str, Any]]:
        """Opens hunk beside the agent, or reuses a review already open, and returns its files."""
        if not self._review_open(work):
            run("herdr", "pane", "run", self.split(pane, work), f"hunk diff origin/{base}")
            for _ in range(REVIEW_WAIT):
                if self._review_open(work):
                    break
                time.sleep(0.5)
            else:
                raise ToolError("hunk didn't open a review.")
        return run_json("hunk", "session", "review", "--repo", str(work), "--json")["review"]["files"]

    def _review_open(self, work: Path) -> bool:
        return subprocess.run(["hunk", "session", "get", "--repo", str(work)], capture_output=True).returncode == 0

    def replace_notes(self, work: Path, notes: list[dict[str, Any]]) -> None:
        # Your own notes stay: clear only takes agent notes without --include-user.
        run("hunk", "session", "comment", "clear", "--repo", str(work), "--yes")
        if notes:
            run("hunk", "session", "comment", "apply", "--repo", str(work), "--stdin", stdin=json.dumps({"comments": notes}))

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

    def start_agent(self, name: str, pane: str, files: Path, model: str = "") -> None:
        # Run files stay out of the repo, so agents need leave to reach them.
        run("herdr", "agent", "start", name, "--kind", "claude", "--pane", pane, "--", "--add-dir", str(files), *(["--model", model] if model else []))

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
