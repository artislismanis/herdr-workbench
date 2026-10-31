"""Moves runs through their pipelines. It holds no state between calls: every decision
reads the run's file and an outside fact, so any process can take over from any point."""

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .pipeline import prompt_text
from .shell import ToolError
from .sources import Source
from .store import Run, Store
from .throttle import Throttle
from .tools import IGNORED, Tools, slug

STEP_TIMEOUT = 45 * 60
STOPS = (ToolError, OSError, KeyError, ValueError, json.JSONDecodeError)


class Park(Exception):
    """Stop here for a person. The message says why."""


class Engine:
    def __init__(self, tools: Tools, store: Store, source_for: Callable[[Run], Source], throttle: Throttle, clock: Callable[[], float] = time.time) -> None:
        self.tools, self.store, self.source_for, self.throttle, self.clock = tools, store, source_for, throttle, clock

    def start(self, repo: str, key: str, repo_path: Path, base: str, pipeline: dict[str, Any], source: Source, override: bool = False) -> Run:
        if not self.tools.identity_ok(repo_path):
            raise Park("Set git user.name and user.email first: workshop commits for the agents.")
        reason = "" if override else source.guard(key)
        if reason:
            raise Park(f"{key}: {reason}")
        source.claim(key)
        run = Run(repo=repo, key=key, repo_path=str(repo_path), base=base, pipeline=pipeline)
        self.store.save(run)
        self.store.event(run, "claimed", override=override)
        return self.advance(run)

    def advance(self, run: Run) -> Run:
        """Move a run as far as it can go now. Parked and finished runs stay put."""
        while run.status == "running":
            step = run.current
            if step is None:
                self._finish(run)
                break
            before = (run.step, run.phase)
            try:
                self._prepare(run)
                if step["kind"] == "agent":
                    self._agent(run, step)
                else:
                    self._draft_pr(run)
            except Park as stop:
                self._park(run, str(stop))
            except STOPS as stop:
                self._park(run, f"{step['id']} failed: {stop}")
            self.store.save(run)
            if (run.step, run.phase) == before:
                break
        return run

    def resume(self, run: Run) -> Run:
        """Try the stopped step again. Finished steps never run twice."""
        if run.status != "parked":
            raise KeyError(f"{run.key} isn't parked")
        # A live agent may already have finished its step, so check it before prompting again.
        alive = run.phase == "prompted" and self.tools.agent_status(run.step_agent or run.agent) != "gone"
        run.status, run.reason, run.phase = "running", "", run.phase if alive else ""
        self.store.event(run, "resumed")
        self.store.save(run)
        return self.advance(run)

    def explain(self, run: Run) -> str:
        step = run.current["id"] if run.current else "finished"
        lines = [f"{run.key} on {run.repo}: {run.status} at {step}"]
        if run.status == "parked":
            lines += [f"Why: {run.reason}", f"Then: workshop resume {run.key}"]
        elif run.held:
            lines.append(f"Held: {run.held} The next tick after it clears carries on.")
        elif run.phase == "prompted":
            lines.append(f"Waiting for agent {run.step_agent or run.agent}. See it in herdr workspace {run.key}.")
        if run.pr:
            lines.append(f"PR: {run.pr}")
        lines += [f"  {e['at']} {e['type']} {e.get('step', '')}".rstrip() for e in self.store.events(run)[-8:]]
        return "\n".join(lines)

    def _prepare(self, run: Run) -> None:
        # Each stage records what it made, so a resume skips what already exists.
        if not run.worktree:
            run.branch = f"workshop/{slug(run.key)}"
            path = self.tools.worktree(Path(run.repo_path), run.branch, run.base)
            run.worktree = str(path)
            self.tools.ignore_scratch(path)
            ticket = self.source_for(run).get(run.key)
            scratch = path / ".workshop"
            scratch.mkdir(exist_ok=True)
            (scratch / "brief.md").write_text(f"# {ticket.key}: {ticket.title}\n\n{ticket.body}\n", encoding="utf-8")
            self.store.event(run, "worktree", path=run.worktree)
        if not run.pane:
            run.pane = self.tools.workspace(Path(run.worktree), run.key)
        if not run.agent:
            run.agent = f"ws-{slug(run.key)}"

    def _report(self, run: Run, step: dict[str, Any]) -> Path:
        return Path(run.worktree) / IGNORED / f"{step['id']}.md"

    def _agent(self, run: Run, step: dict[str, Any]) -> None:
        if run.phase == "":
            if not self._held(run):
                self._prompt_agent(run, step)
            return
        agent = run.step_agent or run.agent
        status = self.tools.agent_status(agent)
        if status == "working":
            if self.clock() - run.started_at > step.get("minutes", STEP_TIMEOUT / 60) * 60:
                raise Park(f"{step['id']} ran past its time limit. Look at agent {agent}.")
            return
        if status == "blocked":
            raise Park(f"Agent {agent} is waiting for an answer in its pane.")
        if status == "gone":
            raise Park(f"Agent {agent} has gone, so {step['id']} didn't finish.")
        self._agent_finished(run, step)

    def _held(self, run: Run) -> bool:
        held = self.throttle.hold_steps()
        if bool(held) != bool(run.held):
            self.store.event(run, "held", reason=held)
        run.held = held
        return bool(held)

    def _prompt_agent(self, run: Run, step: dict[str, Any]) -> None:
        work = Path(run.worktree)
        self._report(run, step).unlink(missing_ok=True)
        run.baseline = self.tools.changes(work) if step.get("read_only") else []
        if step.get("fresh"):
            # A new agent each time: nothing checks its own work.
            run.loops[f"{step['id']}#"] = attempt = run.loops.get(f"{step['id']}#", 0) + 1
            run.step_agent = f"{run.agent}-{step['id']}-{attempt}"
            run.step_pane = self.tools.split(run.pane, work)
            self.tools.start_agent(run.step_agent, run.step_pane, step.get("model", ""))
            agent = run.step_agent
        else:
            agent = run.agent
            if self.tools.agent_status(agent) == "gone":
                self.tools.start_agent(agent, run.pane, step.get("model", ""))
        text = prompt_text(run.next_prompt or step["prompt"])
        run.next_prompt = ""
        self.tools.prompt(agent, text)
        run.phase, run.started_at = "prompted", self.clock()
        self.store.event(run, "step", step=step["id"], state="running", usage=self.throttle.snapshot())

    def _agent_finished(self, run: Run, step: dict[str, Any]) -> None:
        # The agent going quiet only triggers the check. The report decides.
        if run.step_pane:
            self.tools.close_pane(run.step_pane)
        # Done with this attempt: if it parks below, a resume runs the step afresh.
        run.step_pane, run.step_agent, run.phase = "", "", ""
        report = self._report(run, step)
        if step.get("read_only") and self.tools.changes(Path(run.worktree)) != run.baseline:
            raise Park(f"{step['id']} is read-only but changed files.")
        if not report.exists():
            raise Park(f"{step['id']} finished without writing {IGNORED}{report.name}.")
        verdict = ""
        if allowed := step.get("verdict"):
            found = re.findall(r"^VERDICT: ([A-Z_]+)\s*$", report.read_text(encoding="utf-8"), re.MULTILINE)
            verdict = found[-1] if found else ""
            if verdict not in allowed:
                raise Park(f"{step['id']} gave no valid verdict.")
        self.store.event(run, "step", step=step["id"], state="done", verdict=verdict, usage=self.throttle.snapshot())
        rule = step.get("then", {}).get(verdict)
        if rule is None:
            run.step, run.phase = run.step + 1, ""
        elif "park" in rule:
            raise Park(f"{rule['park']} See {report}.")
        else:
            run.loops[step["id"]] = used = run.loops.get(step["id"], 0) + 1
            if used > rule["max"]:
                raise Park(f"{step['id']} still says {verdict} after {rule['max']} rounds. See {report}.")
            run.step, run.phase = run.index_of(rule["goto"]), ""
            run.next_prompt = rule.get("prompt", "")

    def _draft_pr(self, run: Run) -> None:
        work = Path(run.worktree)
        ticket = self.source_for(run).get(run.key)
        title = f"{ticket.key}: {ticket.title}"
        self.tools.commit_all(work, title)
        if self.tools.ahead(work, run.base) == 0:
            raise Park("The agents changed nothing, so there is no PR to open.")
        self.tools.push(work, run.branch)
        run.pr = self.tools.find_pr(work, run.branch)
        if not run.pr:
            body_file = work / IGNORED / "build.md"
            body = body_file.read_text(encoding="utf-8") if body_file.exists() else f"Delivers {ticket.key}."
            run.pr = self.tools.create_pr(work, run.branch, run.base, title, body)
        self.store.event(run, "step", step="pr", state="done", pr=run.pr)
        run.step, run.phase = run.step + 1, ""

    def _finish(self, run: Run) -> None:
        source = self.source_for(run)
        source.report(run.key, f"Draft PR ready: {run.pr}")
        source.finish(run.key)
        run.status = "done"
        self.store.event(run, "finished", pr=run.pr)
        self.store.save(run)

    def _park(self, run: Run, reason: str) -> None:
        # Reported once: a parked run waits for a person, it never retries by itself.
        run.status, run.reason = "parked", reason
        self.store.event(run, "parked", reason=reason)
        try:
            self.source_for(run).report(run.key, f"Workshop stopped: {reason}")
        except STOPS as failed:
            self.store.event(run, "report_failed", reason=str(failed))
