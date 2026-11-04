"""Finds what would stop a run before it spends any tokens."""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .pipeline import PipelineError, load, prompt_dirs
from .shell import ToolError, run
from .sources import make_source
from .tools import Tools, slug

TOOLS = ("git", "gh", "wt", "herdr", "claude")
LAUNCH_TIMEOUT_MS = "60000"


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""  # why it failed
    note: str = ""  # what to know when it didn't


def _tools() -> list[Result]:
    return [Result(f"tool {t}", shutil.which(t) is not None, "not on PATH") for t in TOOLS]


def _try(name: str, *argv: str) -> Result:
    done = subprocess.run(argv, capture_output=True, text=True)
    lines = (done.stderr or done.stdout).strip().splitlines()
    return Result(name, done.returncode == 0, lines[-1] if lines and done.returncode else "")


def launch_test(repo: Path, name: str, base: str) -> Result:
    """Start a real agent where a run would, so first-launch prompts show up here and not at 3am."""
    title = f"launch {name}"
    branch = f"workshop-doctor-{slug(name)}"
    agent = f"ws-doctor-{slug(name)}"
    path = None
    pane = ""
    try:
        # Through wt, so it's the kind of worktree a run gets, in the same place.
        path = Tools().worktree(repo, branch, base)
        pane = Tools().workspace(path, f"doctor {name}")
        Tools().start_agent(agent, pane, path)
        run("herdr", "agent", "prompt", agent, "Reply with the single word ready.", "--wait", "--timeout", LAUNCH_TIMEOUT_MS)
        if Tools().agent_status(agent) == "blocked":
            return Result(title, False, f"agent is blocked: {_screen(pane)}")
        return Result(title, True)
    except ToolError as stop:
        return Result(title, False, f"{stop} {_screen(pane)}".strip())
    finally:
        if pane:
            Tools().close_pane(pane)
        if path:
            subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=repo, capture_output=True)
        subprocess.run(["git", "branch", "-D", branch], cwd=repo, capture_output=True)


def _screen(pane: str) -> str:
    """What the agent is showing: usually the prompt that stopped it."""
    if not pane:
        return ""
    done = subprocess.run(["herdr", "pane", "read", pane, "--lines", "14"], capture_output=True, text=True)
    return " | ".join(line.strip() for line in done.stdout.splitlines() if line.strip())[-300:]


def prompts(name: str, path: Path) -> Result:
    """Which steps run someone else's method, so a misnamed file shows by its absence."""
    found: dict[str, str] = {}
    for where, folder in prompt_dirs(path):
        for file in sorted(folder.glob("*.md")) if folder.is_dir() else []:
            found.setdefault(file.name, where)
    return Result(f"{name} prompts", True, note=", ".join(f"{n} from {where}" for n, where in found.items()) or "workshop's own")


def _repo(name: str, repo: dict[str, Any], jira: dict[str, str], launch: bool) -> list[Result]:
    path = Path(repo["path"]).expanduser()
    results = [Result(f"{name} checkout", (path / ".git").exists(), f"{path} isn't a git checkout")]
    if not results[0].ok:
        return results
    results.append(Result(f"{name} git identity", Tools().identity_ok(path), "set user.name and user.email"))
    base = repo.get("base", "main")
    found = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"origin/{base}"], cwd=path, capture_output=True)
    results.append(Result(f"{name} base branch", found.returncode == 0, f"no origin/{base}"))
    try:
        load(repo.get("pipeline", "ticket"))
        results.append(Result(f"{name} pipeline", True))
    except (PipelineError, OSError) as stop:
        results.append(Result(f"{name} pipeline", False, str(stop)))
    results.append(prompts(name, path))
    try:
        ready = make_source(name, repo, jira).ready()
        results.append(Result(f"{name} source", True, note=f"{len(ready)} ready"))
    except (ToolError, OSError, KeyError, ValueError) as stop:
        results.append(Result(f"{name} source", False, str(stop)))
    if launch:
        results.append(launch_test(path, name, base))
    return results


def doctor(cfg: dict[str, Any], launch: bool = True) -> list[Result]:
    results = _tools()
    if all(r.ok for r in results):
        results.append(_try("gh login", "gh", "auth", "status"))
        results.append(_try("herdr server", "herdr", "workspace", "list"))
    for name, repo in cfg["repos"].items():
        results += _repo(name, repo, cfg.get("jira", {}), launch)
    return results

