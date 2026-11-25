"""The outside world: wt, herdr, git and gh. Tests swap this class for a fake."""

import calendar
import contextlib
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from . import cost
from .shell import ToolError, run, run_json

BOARD = "workshop"  # the board's workspace
BOARD_TAB = "Dashboard"
LEAD_TAB = "Lead"
LEAD_BRIEF = Path(__file__).parent.parent / "lead-chat.md"
OPEN_BOARD = "prefix+shift+b opens it on the board."
REVIEW_WAIT = 40  # half-second checks while hunk starts
# Running a repo's checks leaves these behind, even where it doesn't ignore them.
USAGE_TIMEOUT = 30
CHAT_TIMEOUT = 120
COPILOT = """query($owner: String!, $repo: String!, $number: Int!) { repository(owner: $owner, name: $repo) { pullRequest(number: $number) {
  reviews(last: 50) { nodes { author { login } submittedAt } }
  reviewThreads(first: 100) { nodes { id isResolved path line comments(first: 1) { nodes { databaseId author { login } body } } } } } } }"""
GENERATED = ("__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".venv", "node_modules")

MERGE_POLLS, MERGE_POLL_SECONDS = 20, 3  # a stack's merge runs in the background



def last_pane(layout: dict[str, Any], pane: str) -> str:
    """The last pane in the row of `pane`, so a new pane goes after the ones already open."""
    rect = next(p["rect"] for p in layout["panes"] if p["pane_id"] == pane)
    row = [p for p in layout["panes"] if p["rect"]["y"] < rect["y"] + rect["height"] and rect["y"] < p["rect"]["y"] + p["rect"]["height"]]
    return max(row, key=lambda p: p["rect"]["x"] + p["rect"]["width"])["pane_id"]

class CommitRefused(ToolError):
    """The repo's own commit checks said no. Their output says why."""


class Conflict(ToolError):
    """A branch couldn't move onto the one below it without a person's or an agent's call."""


def slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")


def claude_settings() -> dict[str, Any]:
    path = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser() / "settings.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def permission_mode() -> str:
    """Yours, from your own Claude settings. A repo's own default, such as plan, would stop an agent nobody is watching."""
    return claude_settings().get("permissions", {}).get("defaultMode") or "default"


def keep_on_resume(work: Path, model: str, advisor: str) -> None:
    """herdr brings agents back after a restart with a bare claude --resume, which drops the flags above. The checkout's local settings outlast that, and win over a repo's own, such as an opusplan that turns Sonnet once a plan is approved."""
    path = work / ".claude" / "settings.local.json"
    found = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    found |= {k: v for k, v in (("model", model), ("advisorModel", advisor)) if v}
    found.setdefault("permissions", {})["defaultMode"] = permission_mode()
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(found, indent=2) + "\n", encoding="utf-8")
    if subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=work).returncode != 0:
        # Fixes are committed with git add -A, so the file must never ride along.
        exclude = Path(run("git", "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=work).strip()) / "info" / "exclude"
        exclude.parent.mkdir(exist_ok=True)
        with exclude.open("a", encoding="utf-8") as out:
            out.write("/.claude/settings.local.json\n")


def temp_folder() -> str:
    """Where your Claude settings keep temp files, so the checks workshop runs keep theirs there too and off a /tmp held in memory."""
    env = claude_settings().get("env", {})
    return env.get("TMPDIR") or env.get("CLAUDE_CODE_TMPDIR") or ""


class Tools:
    def warm(self, read: str, *args: Any) -> Any:
        """A read made before the lock, for the next same call to use once."""
        found = getattr(self, read)(*args)
        self.__dict__.setdefault("warmed", {})[(read, *map(repr, args))] = found
        return found

    def _warmed(self, read: str, *args: Any) -> Any:
        return self.__dict__.get("warmed", {}).pop((read, *map(repr, args)), None)

    def identity_ok(self, repo: Path) -> bool:
        settings = ("user.name", "user.email")
        return all(subprocess.run(["git", "config", s], cwd=repo, capture_output=True).returncode == 0 for s in settings)

    def worktree(self, repo: Path, branch: str, base: str) -> Path:
        # wt, not herdr's own worktree command, so the setup hooks run.
        known = subprocess.run(["git", "rev-parse", "--verify", "--quiet", branch], cwd=repo, capture_output=True)
        args = [branch] if known.returncode == 0 else ["--create", branch, "--base", base]
        return Path(run_json("wt", "switch", *args, "--no-cd", "--yes", "--format=json", cwd=repo)["path"])

    def pr_worktree(self, repo: Path, number: str, base: str) -> tuple[Path, str]:
        """The PR's own branch, which is how hunk-pr-push finds the PR."""
        run("git", "fetch", "origin", base, cwd=repo)
        path = Path(run_json("wt", "switch", f"pr:{number}", "--no-cd", "--yes", "--format=json", cwd=repo)["path"])
        return path, run("git", "branch", "--show-current", cwd=path).strip()

    def follow_pr(self, work: Path, branch: str, base: str) -> None:
        run("git", "fetch", "origin", base, branch, cwd=work)
        run("git", "reset", "--keep", f"origin/{branch}", cwd=work)

    def head(self, work: Path) -> str:
        return run("git", "rev-parse", "HEAD", cwd=work).strip()

    def contains(self, work: Path, commit: str) -> bool:
        return subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=work, capture_output=True).returncode == 0

    def fork_point(self, work: Path, base: str) -> str:
        return run("git", "merge-base", f"origin/{base}", "HEAD", cwd=work).strip()

    def workspace(self, path: Path, label: str) -> str:
        made = run_json("herdr", "workspace", "create", "--cwd", str(path), "--label", label, "--no-focus")
        return made["result"]["root_pane"]["pane_id"]

    def linked(self, repo: Path, path: Path, label: str) -> str:
        # Linked, so herdr groups it under the repo's workspace, opening that if it has to. It must start from the main checkout.
        made = run_json("herdr", "worktree", "open", "--cwd", str(repo), "--path", str(path), "--label", label, "--no-focus")
        return made["result"]["root_pane"]["pane_id"]

    def copy(self, text: str) -> None:
        # Its own output, not a pipe: wl-copy stays behind holding the clipboard, and a pipe would wait for it.
        try:
            subprocess.run(["wl-copy"], input=text, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=True)
        except (OSError, subprocess.SubprocessError) as failed:
            raise ToolError(f"wl-copy failed: {failed}") from failed

    def open_link(self, url: str) -> str:
        """What the opener said, as in WSL it can only pass the link on."""
        return run("xdg-open", url).strip()

    def workshop(self, *args: str) -> None:
        # The command, not the engine, so the board takes the same lock as everything else.
        run(f"{Path.home()}/.local/bin/workshop", *args)

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

    def open_lead(self, home: Path) -> None:
        """A Claude session to talk things through with, in a tab beside the board. A second press goes back to it."""
        self.open_board()
        space = next(s["workspace_id"] for s in run_json("herdr", "workspace", "list")["result"]["workspaces"] if s.get("label") == BOARD)
        for tab in run_json("herdr", "tab", "list", "--workspace", space)["result"]["tabs"]:
            if tab["label"] == LEAD_TAB:
                run("herdr", "tab", "focus", tab["tab_id"])
                return
        home.mkdir(parents=True, exist_ok=True)
        # Files, not flags, as herdr brings an agent back with a bare claude --resume.
        (home / "CLAUDE.md").write_text(LEAD_BRIEF.read_text(encoding="utf-8"), encoding="utf-8")
        (home / ".claude").mkdir(exist_ok=True)
        (home / ".claude" / "settings.local.json").write_text(json.dumps({"model": "opus", "permissions": {"defaultMode": permission_mode()}}, indent=2) + "\n", encoding="utf-8")
        made = run_json("herdr", "tab", "create", "--workspace", space, "--cwd", str(home), "--label", LEAD_TAB, "--focus")
        talked = any((Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(home))).glob("*.jsonl"))
        # Typed, not herdr agent start, which fails while the first open waits for you to trust the folder.
        run("herdr", "pane", "run", made["result"]["root_pane"]["pane_id"], f"claude --add-dir {shlex.quote(str(Path.home()))}" + (" --continue" if talked else ""))

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
        last = last_pane(run_json("herdr", "pane", "layout", "--pane", pane)["result"]["layout"], pane)
        made = run_json("herdr", "pane", "split", last, "--direction", "right", "--cwd", str(path), "--no-focus")
        return made["result"]["pane"]["pane_id"]

    def background(self, work: Path, since: float) -> set[str]:
        return cost.background(work, since)

    def spent(self, work: Path, since: float, until: float) -> dict[str, Any]:
        return cost.spent(work, since, until)

    def rerequest(self, repo: Path, number: str) -> None:
        """Those who asked for changes, as nobody else is holding the PR."""
        reviews = run_json("gh", "pr", "view", number, "--json", "latestReviews", cwd=repo)["latestReviews"]
        who = [r["author"]["login"] for r in reviews if r["state"] == "CHANGES_REQUESTED"]
        if who:
            run("gh", "pr", "edit", number, "--add-reviewer", ",".join(who), cwd=repo)

    def pr_state(self, url: str) -> dict[str, Any]:
        if (hit := self._warmed("pr_state", url)) is not None:
            return hit
        pr = run_json("gh", "pr", "view", url, "--json", "state,isDraft,reviewDecision,headRefOid,baseRefName,body,mergeCommit,mergedBy,latestReviews")
        # GitHub leaves the decision blank on a branch whose rules ask for no review, though people approved it.
        if not pr.get("reviewDecision"):
            standing = {r["state"] for r in pr.get("latestReviews") or []}
            pr["reviewDecision"] = "APPROVED" if "APPROVED" in standing and "CHANGES_REQUESTED" not in standing else ""
        return pr

    def approve(self, url: str, body: str) -> None:
        try:
            run("gh", "pr", "review", url, "--approve", "--body", body)
        except ToolError as refused:
            if "own pull request" not in str(refused):  # GitHub never lets you approve your own, and nothing needs it to
                raise

    def merge(self, url: str, admin: bool) -> None:
        """By the first method the repo allows, squash before a merge commit before a rebase."""
        allowed = run_json("gh", "repo", "view", url.split("/pull/")[0], "--json", "squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed")
        method = next(flag for key, flag in (("squashMergeAllowed", "--squash"), ("mergeCommitAllowed", "--merge"), ("rebaseMergeAllowed", "--rebase")) if allowed[key])
        try:
            run("gh", "pr", "merge", url, method, *(["--admin"] if admin else []))
        except ToolError as refused:
            if "part of a stack" not in str(refused):
                raise
            self._merge_stacked(url, method.removeprefix("--"), admin)

    def _merge_stacked(self, url: str, method: str, admin: bool) -> None:
        """GitHub merges a stacked PR only through its async API, with those below it.

        An admin passes branch protection without asking. Bypassing a ruleset works only on the bottom PR, so it's the second try.
        """
        try:
            self._merge_async(url, method, False)
        except ToolError:
            if not admin:
                raise
            self._merge_async(url, method, True)

    def _merge_async(self, url: str, method: str, bypass: bool) -> None:
        owner, repo, _, number = url.rstrip("/").split("/")[-4:]
        path = f"repos/{owner}/{repo}/pulls/{number}/merge-async"
        found = run_json("gh", "api", "-X", "PUT", path, "-f", f"merge_method={method}", "-F", f"bypass_rules={str(bypass).lower()}")
        for _ in range(MERGE_POLLS):
            if found["status"] in ("merged", "enqueued"):
                return
            if found["status"] == "failed":
                raise ToolError(f"GitHub didn't merge {url}: {found['details'].get('message', '')}")
            time.sleep(MERGE_POLL_SECONDS)
            found = run_json("gh", "api", f"{path}/{found['details']['uuid']}")
        raise ToolError(f"GitHub is still merging {url}. The board catches up once it lands.")

    def open_threads(self, url: str) -> int:
        owner, repo, _, number = url.rstrip("/").split("/")[-4:]
        query = "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100){nodes{isResolved}}}}}"
        found = run_json("gh", "api", "graphql", "-F", f"o={owner}", "-F", f"r={repo}", "-F", f"n={number}", "-f", f"query={query}")
        return sum(not t["isResolved"] for t in found["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"])

    def checks(self, url: str) -> tuple[str, list[dict[str, str]]]:
        """The PR's head and its checks, check runs and commit statuses alike, so it doesn't matter which CI reports them."""
        found = run_json("gh", "pr", "view", url, "--json", "headRefOid,statusCheckRollup")
        checks = [{"name": c.get("name") or c.get("context", ""), "state": (c.get("conclusion") or c.get("status") or "") if c.get("__typename") == "CheckRun" else c.get("state", ""), "link": c.get("detailsUrl") or c.get("targetUrl") or ""} for c in found["statusCheckRollup"] or []]
        return found["headRefOid"], checks

    def reached(self, repo: Path, pr: str, commit: str, branches: list[str]) -> bool:
        """Whether a merged PR is on any of these branches. Promotions can squash, so if its commit isn't there, its changes can be."""
        if (hit := self._warmed("reached", repo, pr, commit, branches)) is not None:
            return hit
        changes = run("gh", "pr", "diff", pr, cwd=repo)
        for branch in branches:
            if subprocess.run(["git", "fetch", "-q", "origin", branch], cwd=repo, capture_output=True).returncode != 0:
                continue
            tip = run("git", "rev-parse", "FETCH_HEAD", cwd=repo).strip()
            if subprocess.run(["git", "merge-base", "--is-ancestor", commit, tip], cwd=repo, capture_output=True).returncode == 0:
                return True
            with tempfile.TemporaryDirectory() as scratch:
                index = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
                run("git", "read-tree", tip, cwd=repo, env=index)
                if subprocess.run(["git", "apply", "--cached", "--reverse", "--check"], cwd=repo, input=changes, text=True, capture_output=True, env=index).returncode == 0:
                    return True
        return False

    def remove_worktree(self, repo: Path, work: Path) -> None:
        # No --force: wt refuses a dirty worktree and keeps an unmerged branch, the same as the key.
        run("wt", "remove", "--foreground", "--yes", "-C", str(repo), str(work))

    def back_to_board(self, pane: str) -> None:
        """Closing the workspace you're in would drop you on whichever is next. Anywhere else, you stay put."""
        try:
            spaces = run_json("herdr", "workspace", "list")["result"]["workspaces"]
            if any(space["workspace_id"] == pane.split(":")[0] and space["focused"] for space in spaces):
                self.open_board()
        except ToolError:
            pass  # it only decides where you land

    def close_workspace(self, pane: str) -> None:
        run("herdr", "workspace", "close", pane.split(":")[0])

    def pane_alive(self, pane: str) -> bool:
        return subprocess.run(["herdr", "pane", "get", pane], capture_output=True).returncode == 0

    def close_pane(self, pane: str) -> None:
        subprocess.run(["herdr", "pane", "close", pane], capture_output=True)

    def open_review(self, pane: str, work: Path, against: str, reload: bool = False) -> list[dict[str, Any]]:
        """Opens hunk beside the agent, or reuses a review already open, and returns its files."""
        if reload and self.review_open(work):
            run("hunk", "session", "reload", "--repo", str(work), "--", "diff", against)
        if not self.review_open(work):
            run("herdr", "pane", "run", self.split(pane, work), f"hunk diff {against}")
            for _ in range(REVIEW_WAIT):
                if self.review_open(work):
                    break
                time.sleep(0.5)
            else:
                raise ToolError("hunk didn't open a review.")
        return run_json("hunk", "session", "review", "--repo", str(work), "--json")["review"]["files"]

    def review_open(self, work: Path) -> bool:
        return subprocess.run(["hunk", "session", "get", "--repo", str(work)], capture_output=True).returncode == 0

    def load_threads(self, work: Path) -> None:
        # Best effort, and the same script as a key: without them the review still stands.
        subprocess.run([f"{Path.home()}/.config/herdr/bin/hunk-pr-notes", "--quiet", str(work)], capture_output=True)

    def replace_notes(self, work: Path, notes: list[dict[str, Any]]) -> None:
        # Your own notes stay: clear only takes agent notes without --include-user.
        run("hunk", "session", "comment", "clear", "--repo", str(work), "--yes")
        if notes:
            run("hunk", "session", "comment", "apply", "--repo", str(work), "--stdin", stdin=json.dumps({"comments": notes}))

    def mark(self, pane: str, label: str) -> None:
        """The run's state beside its workspace in herdr's sidebar, as the $workshop token. Best effort: a closed workspace is fine."""
        token = ["--token", f"workshop={label}"] if label else ["--clear-token", "workshop"]
        subprocess.run(["herdr", "workspace", "report-metadata", pane.split(":")[0], "--source", "workshop", *token], capture_output=True)

    def title(self, pane: str, label: str) -> None:
        subprocess.run(["herdr", "pane", "rename", pane, *([label] if label else ["--clear"])], capture_output=True)

    def point_review(self, work: Path, pane: str) -> None:
        """Sets the pane hunk sends review comments to. hunkdiff keeps the last agent it saw in a worktree, which can be a pane workshop has closed."""
        state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "herdr/plugins/jhochenbaum.hunkdiff"
        index, lock = state / "review-index.json", state / "review-index.json.lock"
        if not state.is_dir():
            return
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)  # the lock hunkdiff takes itself
        except OSError:
            return  # best effort: hunkdiff is writing, and its own next event wins
        try:
            os.close(fd)
            entries = json.loads(index.read_text(encoding="utf-8")) if index.exists() else {}
            key = os.path.realpath(work)
            entries[key] = {**entries.get(key, {"worktree": key, "agentName": "claude", "sent": []}), "agentPaneId": pane}
            tmp = index.with_name(f"{index.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
            tmp.rename(index)
        except (OSError, ValueError):
            pass
        finally:
            lock.unlink(missing_ok=True)

    def notify(self, title: str, body: str) -> None:
        # Best effort: a notification must never stop a run.
        # A click on a notification sent this way does nothing in herdr, so it says how to reach the board.
        subprocess.run(["herdr", "notification", "show", title, "--body", f"{body} {OPEN_BOARD}", "--sound", "request"], capture_output=True)

    def screen(self, pane: str) -> str:
        """What a pane is showing, on one line: usually the prompt that stopped its agent."""
        if not pane:
            return ""
        done = subprocess.run(["herdr", "pane", "read", pane, "--lines", "14"], capture_output=True, text=True)
        return " | ".join(line.strip() for line in done.stdout.splitlines() if line.strip())[-300:]

    def agent_status(self, name: str) -> str:
        """The agent's status, or "gone" if herdr no longer knows it."""
        done = subprocess.run(["herdr", "agent", "get", name], capture_output=True, text=True)
        if done.returncode != 0:
            return "gone"
        return json.loads(done.stdout)["result"]["agent"]["agent_status"]
        return json.loads(done.stdout)["result"]["agent"]["agent_status"]

    def start_agent(self, name: str, pane: str, files: Path, model: str = "", advisor: str = "", work: Path | None = None) -> None:
        # Run files stay out of the repo, so agents need leave to reach them. Settings given here win over the repo's.
        settings = ["--settings", json.dumps({"advisorModel": advisor})] if advisor else []
        if work is not None:
            keep_on_resume(work, model, advisor)
        run("herdr", "agent", "start", name, "--kind", "claude", "--pane", pane, "--", "--add-dir", str(files), "--permission-mode", permission_mode(), *(["--model", model] if model else []), *settings)

    def talk(self, pane: str, files: Path, text: str) -> None:
        """A plain Claude session for you to talk to, not a step agent, so the run never waits on it."""
        # The prompt first: --add-dir takes several folders, and would take the prompt for one.
        run("herdr", "pane", "run", pane, f"claude {shlex.quote(text)} --add-dir {shlex.quote(str(files))}")

    def prompt(self, name: str, text: str) -> None:
        # Waits only until the agent has started, so "idle" afterwards means it finished.
        run("herdr", "agent", "prompt", name, text, "--wait", "--until", "working", "--timeout", "60000")

    def changes(self, work: Path) -> list[str]:
        lines = run("git", "status", "--porcelain", "--untracked-files=all", cwd=work).splitlines()
        return sorted(line for line in lines if not any(f"{g}/" in line for g in GENERATED))

    def tracked_changes(self, work: Path) -> list[str]:
        return run("git", "status", "--porcelain", "--untracked-files=no", cwd=work).splitlines()

    def snapshot(self, work: Path) -> str:
        """The worktree as a git tree, new files included, touching nothing. Agents leave their work uncommitted, so there's no commit to compare with."""
        excludes = [f":(exclude,glob)**/{g}/**" for g in GENERATED]
        with tempfile.TemporaryDirectory() as folder:
            index = {**os.environ, "GIT_INDEX_FILE": f"{folder}/index"}
            run("git", "add", "-A", "--", ".", *excludes, cwd=work, env=index)
            return run("git", "write-tree", cwd=work, env=index).strip()

    def stage(self, work: Path) -> None:
        excludes = [f":(exclude,glob)**/{g}/**" for g in GENERATED]
        run("git", "add", "-A", "--", ".", *excludes, cwd=work)

    def claude_usage(self) -> str:
        """Live limits, for every machine on the account. It costs no tokens, and reads stdin unless told not to."""
        try:
            return subprocess.run(["claude", "-p", "/usage", "--no-session-persistence"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=USAGE_TIMEOUT).stdout
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def claude(self, prompt: str, model: str, tools: list[str]) -> dict[str, Any]:
        """One call with only these tools, on stdin because --allowedTools takes all that follows it. Its dollars are the API equivalent, like everything else here."""
        try:
            done = subprocess.run(["claude", "-p", "--model", model, "--output-format", "json", "--no-session-persistence", "--allowedTools", *tools], cwd=Path.home(), input=prompt, capture_output=True, text=True, timeout=CHAT_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as failed:
            raise ToolError(f"claude failed: {failed}") from failed
        try:
            found = json.loads(done.stdout)
        except ValueError as bad:
            raise ToolError(f"claude failed: {done.stderr.strip() or bad}") from bad
        if found.get("is_error"):
            raise ToolError(f"claude failed: {found.get('result', '')}")
        usage = found["usage"]
        return {
            "result": found["result"],
            "model": next(iter(found.get("modelUsage", {})), model),
            "turns": found.get("num_turns", 1),
            "tokens_in": sum(usage.get(f, 0) for f in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
            "tokens_out": usage.get("output_tokens", 0),
            "minutes": round(found.get("duration_ms", 0) / 60000, 1),
            "usd": found.get("total_cost_usd"),
        }

    def start_command(self, command: str, work: Path, output: Path, finished: Path, then: str = "") -> int:
        """Runs a repo's own command in the background, because it can outlast a tick. The exit code lands in finished, then the then command runs."""
        with output.open("w", encoding="utf-8") as log:
            wrapper = 'sh -c "$1"; echo $? > "$2"; sh -c "$3" >/dev/null 2>&1'
            # Unbuffered, so the board shows Python's output as it comes, not in blocks at the end.
            return subprocess.Popen(["sh", "-c", wrapper, "sh", command, str(finished), then], cwd=work, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=os.environ | {"PYTHONUNBUFFERED": "1"}).pid

    def stop_command(self, pid: int) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGTERM)

    def commit_all(self, work: Path, message: str) -> None:
        self.stage(work)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=work).returncode != 0:
            done = subprocess.run(["git", "commit", "-m", message], cwd=work, capture_output=True, text=True)
            if done.returncode != 0:
                raise CommitRefused(f"git commit failed:\n{done.stdout.strip()}\n{done.stderr.strip()}".strip())

    def ahead(self, work: Path, base: str) -> int:
        return int(run("git", "rev-list", "--count", f"origin/{base}..HEAD", cwd=work).strip())

    def unpushed(self, work: Path, branch: str) -> int:
        return int(run("git", "rev-list", "--count", f"origin/{branch}..HEAD", cwd=work).strip())

    def push(self, work: Path, branch: str) -> None:
        run("git", "push", "-u", "origin", branch, cwd=work)

    def tip(self, work: Path, branch: str) -> str:
        run("git", "fetch", "origin", branch, cwd=work)
        return run("git", "rev-parse", f"origin/{branch}", cwd=work).strip()

    def move_onto(self, work: Path, base: str, old: str) -> None:
        """The commits after old, replayed onto the base. A conflict leaves the branch as it was."""
        if subprocess.run(["git", "rebase", "--onto", f"origin/{base}", old], cwd=work, capture_output=True).returncode != 0:
            subprocess.run(["git", "rebase", "--abort"], cwd=work, capture_output=True)
            raise Conflict(f"moving onto {base} hit a conflict")

    def force_push(self, work: Path, branch: str, lease: str = "") -> None:
        run("git", "push", f"--force-with-lease={branch}:{lease}" if lease else "--force-with-lease", "origin", branch, cwd=work)

    def retarget(self, work: Path, pr: str, base: str) -> None:
        run("gh", "pr", "edit", pr, "--base", base, cwd=work)

    def find_pr(self, work: Path, branch: str) -> str:
        found = run_json("gh", "pr", "list", "--head", branch, "--state", "open", "--json", "url", cwd=work)
        return found[0]["url"] if found else ""

    def ask_copilot(self, work: Path, pr: str) -> bool:
        """Whether GitHub took the request. A repo without Copilot accepts the edit and requests nobody."""
        run("gh", "pr", "edit", pr, "--add-reviewer", "@copilot", cwd=work)
        events = run_json("gh", "api", f"repos/{{owner}}/{{repo}}/issues/{pr.rstrip('/').rsplit('/', 1)[-1]}/timeline", "--paginate", "--slurp", cwd=work)
        return any(e.get("event") == "review_requested" and (e.get("requested_reviewer") or {}).get("login", "").lower().startswith("copilot") for page in events for e in page)

    def copilot(self, work: Path, pr: str, since: float) -> tuple[bool, list[dict[str, Any]]]:
        """Whether Copilot has reviewed since then, and its open threads: id, comment, path, line and body."""
        found = run_json("gh", "api", "graphql", "-F", "owner={owner}", "-F", "repo={repo}", "-F", f"number={pr.rstrip('/').rsplit('/', 1)[-1]}", "-f", f"query={COPILOT}", cwd=work)
        pull = found["data"]["repository"]["pullRequest"]
        bot = lambda node: (node.get("author") or {}).get("login", "").startswith("copilot")  # noqa: E731
        reviewed = any(bot(r) and r["submittedAt"] and calendar.timegm(time.strptime(r["submittedAt"], "%Y-%m-%dT%H:%M:%SZ")) >= since for r in pull["reviews"]["nodes"])
        threads = [{"id": t["id"], "comment": first["databaseId"], "path": t["path"], "line": t["line"], "body": first["body"]} for t in pull["reviewThreads"]["nodes"] if not t["isResolved"] and (first := t["comments"]["nodes"][0]) and bot(first)]
        return reviewed, threads

    def answer_thread(self, work: Path, pr: str, comment: int, thread: str, body: str) -> None:
        """Replies on the thread, then resolves it. Only ever used on a bot's thread."""
        run("gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{pr.rstrip('/').rsplit('/', 1)[-1]}/comments/{comment}/replies", "-f", f"body={body}", cwd=work)
        run("gh", "api", "graphql", "-f", "query=mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { isResolved } } }", "-F", f"id={thread}", cwd=work)

    def start_ci(self, command: str, work: Path, branch: str) -> None:
        run("sh", "-c", command, cwd=work, env=os.environ | {"BRANCH": branch})

    def mark_ready(self, work: Path, pr: str) -> None:
        run("gh", "pr", "ready", pr, cwd=work)

    def edit_pr(self, work: Path, pr: str, title: str, body: str) -> None:
        run("gh", "pr", "edit", pr, "--title", title, "--body", body, cwd=work)

    def create_pr(self, work: Path, branch: str, base: str, title: str, body: str) -> str:
        made = run("gh", "pr", "create", "--draft", "--base", base, "--head", branch, "--title", title, "--body", body, cwd=work)
        return made.strip().splitlines()[-1]


__all__ = ["CommitRefused", "ToolError", "Tools", "slug"]
