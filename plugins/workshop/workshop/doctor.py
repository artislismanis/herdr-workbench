"""Finds what would stop a run before it spends any tokens."""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .pipeline import PipelineError, load, pipeline_source, prompt_dirs, prompts_of, shipped
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


def prompts(name: str, path: Path, pipelines: list[dict[str, Any]] | None = None) -> Result:
    """Which steps run someone else's method, so a misnamed file shows by its absence, and any step whose prompt is nowhere."""
    found: dict[str, str] = {}
    for where, folder in prompt_dirs(path, name):
        for file in sorted(folder.glob("*.md")) if folder.is_dir() else []:
            found.setdefault(file.name, where)
    missing = [n for pipeline in pipelines or [] for n in prompts_of(pipeline) if not shipped(n) and n not in found]
    detail = f"no {', '.join(dict.fromkeys(missing))} in {' or '.join(str(folder) for _, folder in prompt_dirs(path, name))}"
    return Result(f"{name} prompts", not missing, detail, ", ".join(f"{n} from {where}" for n, where in found.items()) or "workshop's own")


def pipelines(name: str, path: Path, named: list[str]) -> tuple[Result, list[dict[str, Any]]]:
    """Every pipeline the repo runs, and where each one comes from, so a file in the wrong folder shows."""
    loaded, broken = [], []
    for pipeline in dict.fromkeys(named):
        try:
            loaded.append(load(pipeline, path, name))
        except (PipelineError, OSError) as stop:
            broken.append(str(stop))
    mine = [f"{n} from {where}" for n in dict.fromkeys(named) if (where := pipeline_source(n, path, name)) != "workshop"]
    return Result(f"{name} pipelines", not broken, "; ".join(broken), ", ".join(mine) or "workshop's own"), loaded


def _repo(name: str, repo: dict[str, Any], jira: dict[str, str], launch: bool) -> list[Result]:
    path = Path(repo["path"]).expanduser()
    results = [Result(f"{name} checkout", (path / ".git").exists(), f"{path} isn't a git checkout")]
    if not results[0].ok:
        return results
    results.append(Result(f"{name} git identity", Tools().identity_ok(path), "set user.name and user.email"))
    base = repo.get("base", "main")
    found = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"origin/{base}"], cwd=path, capture_output=True)
    results.append(Result(f"{name} base branch", found.returncode == 0, f"no origin/{base}"))
    named = [repo.get("pipeline", "ticket"), repo.get("review_pipeline", "review"), repo.get("reply_pipeline", "reply"), repo.get("prepare_pipeline", "prepare"), repo.get("ci_pipeline", "fix-ci"), *(f"sweep-{sweep}" for sweep in repo.get("sweeps", {}))]
    found, loaded = pipelines(name, path, named)
    results += [found, prompts(name, path, loaded)]
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

