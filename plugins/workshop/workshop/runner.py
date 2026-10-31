"""One ticket, start to draft PR. Agents do the thinking; this owns git and every hand-off."""

import json
import re
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .shell import ToolError, run, run_json
from .sources import Source, Ticket

STEP_TIMEOUT_MS = 45 * 60 * 1000
SETTLED = ("idle", "done", "blocked")


class NeedsYou(RuntimeError):
    """The run stopped where only a person can move it on."""


@dataclass
class Workspace:
    path: Path
    pane: str
    branch: str


def prompt(name: str) -> str:
    return resources.files("workshop.prompts").joinpath(f"{name}.md").read_text(encoding="utf-8")


def slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")


def open_workspace(repo: Path, ticket: Ticket, base: str) -> Workspace:
    # wt, not herdr's own worktree command, so the setup hooks run.
    branch = f"workshop/{slug(ticket.key)}"
    made = run_json("wt", "switch", "--create", branch, "--base", base, "--no-cd", "--yes", "--format=json", cwd=repo)
    path = Path(made["path"])
    pane = run_json("herdr", "workspace", "create", "--cwd", str(path), "--label", ticket.key, "--no-focus")
    return Workspace(path, pane["result"]["root_pane"]["pane_id"], branch)


def write_brief(work: Path, ticket: Ticket) -> Path:
    # Kept out of git: the ticket text stays local, it never reaches the PR.
    common = run("git", "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=work).strip()
    with (Path(common) / "info" / "exclude").open("a+", encoding="utf-8") as exclude:
        exclude.seek(0)
        if ".workshop/" not in exclude.read():
            exclude.write(".workshop/\n")
    brief = work / ".workshop" / "brief.md"
    brief.parent.mkdir(exist_ok=True)
    brief.write_text(f"# {ticket.key}: {ticket.title}\n\n{ticket.body}\n", encoding="utf-8")
    return brief


def start_agent(name: str, pane: str) -> None:
    run("herdr", "agent", "start", name, "--kind", "claude", "--pane", pane)


def ask(agent: str, text: str) -> None:
    """Prompt an agent and wait for it to settle. A blocked agent needs a person."""
    state = ["--until", SETTLED[0], "--until", SETTLED[1], "--until", SETTLED[2]]
    run("herdr", "agent", "prompt", agent, text, "--wait", "--timeout", str(STEP_TIMEOUT_MS), *state)
    status = run_json("herdr", "agent", "get", agent)["result"]["agent"]["agent_status"]
    if status == "blocked":
        raise NeedsYou(f"Agent {agent} is blocked and waiting for an answer in its pane.")


def verify(work: Workspace, name: str) -> tuple[str, str]:
    """A fresh agent checks the change. It didn't write it, so it can't mark its own work."""
    split = run_json("herdr", "pane", "split", work.pane, "--direction", "right", "--cwd", str(work.path), "--no-focus")
    pane = split["result"]["pane"]["pane_id"]
    report = work.path / ".workshop" / "verify.md"
    report.unlink(missing_ok=True)
    try:
        start_agent(name, pane)
        ask(name, prompt("verify"))
    finally:
        run("herdr", "pane", "close", pane)
    if not report.exists():
        return "UNKNOWN", "The verify step wrote no report."
    text = report.read_text(encoding="utf-8")
    found = re.findall(r"^VERDICT: (PASS|CHANGES_NEEDED)\s*$", text, re.MULTILINE)
    return (found[-1] if found else "UNKNOWN"), text


def commit_all(work: Path, message: str) -> bool:
    run("git", "add", "-A", cwd=work)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=work)
    if staged.returncode == 0:
        return False
    run("git", "commit", "-m", message, cwd=work)
    return True


def open_draft_pr(work: Workspace, ticket: Ticket, base: str) -> str:
    run("git", "push", "-u", "origin", work.branch, cwd=work.path)
    body_file = work.path / ".workshop" / "pr.md"
    body = body_file.read_text(encoding="utf-8") if body_file.exists() else f"Delivers {ticket.key}."
    title = f"{ticket.key}: {ticket.title}"
    created = run("gh", "pr", "create", "--draft", "--base", base, "--head", work.branch,
                  "--title", title, "--body", body, cwd=work.path)
    return created.strip().splitlines()[-1]


def preflight(repo: Path) -> None:
    """Stop before claiming: a missing identity otherwise fails after the agents' work is done."""
    for setting in ("user.name", "user.email"):
        if subprocess.run(["git", "config", setting], cwd=repo, capture_output=True).returncode != 0:
            raise NeedsYou(f"Set git {setting} before running workshop: it commits for the agents.")


def run_ticket(repo: Path, source: Source, key: str, base: str = "main", rounds: int = 2) -> None:
    preflight(repo)
    source.claim(key)
    try:
        ticket = source.get(key)
        work = open_workspace(repo, ticket, base)
        write_brief(work.path, ticket)
        build = f"ws-{slug(ticket.key)}"
        start_agent(build, work.pane)
        ask(build, prompt("build"))

        verdict, report = "UNKNOWN", ""
        for attempt in range(1, rounds + 2):
            verdict, report = verify(work, f"{build}-v{attempt}")
            if verdict != "CHANGES_NEEDED" or attempt > rounds:
                break
            ask(build, prompt("fix"))

        if not commit_all(work.path, f"{ticket.key}: {ticket.title}"):
            raise NeedsYou("The agents changed nothing, so there is no PR to open.")
        pr = open_draft_pr(work, ticket, base)
        source.report(key, f"Draft PR ready: {pr}\nVerify verdict: {verdict}.\n\n{needs_you(report)}".rstrip())
        source.finish(key)
    except (NeedsYou, ToolError, OSError, json.JSONDecodeError, KeyError) as stop:
        # Claimed stays claimed, so a failure is reported once and never loops.
        source.report(key, f"Workshop stopped: {stop}")
        raise


def needs_you(report: str) -> str:
    match = re.search(r"^## Needs you\n(.*?)(?=^## |^VERDICT:|\Z)", report, re.MULTILINE | re.DOTALL)
    return f"Needs you:\n{match[1].strip()}" if match and match[1].strip() else ""
