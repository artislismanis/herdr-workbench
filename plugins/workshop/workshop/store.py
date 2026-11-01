"""Everything a run knows lives in one JSON file, so any process can pick it up."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ACTIVE = ("running", "parked")
SNAPSHOT = "board.json"  # what was last seen on the backlog and PRs
NAMES = {"waiting": "decide", "parked": "fix"}  # what you do about it, which is what the screen says


@dataclass
class Run:
    repo: str
    key: str
    repo_path: str
    base: str
    pipeline: dict[str, Any]  # copied in, so editing the file can't change a run in flight
    commands: dict[str, str] = field(default_factory=dict)  # the repo's, by step id
    step: int = 0
    phase: str = ""  # "" before a step starts, "prompted" while an agent or command works on it
    status: str = "running"  # running, waiting (for a person, as planned), parked (stopped), done
    reason: str = ""
    held: str = ""  # why the throttle is keeping the next step back
    worktree: str = ""
    branch: str = ""
    pane: str = ""
    agent: str = ""
    step_pane: str = ""
    step_agent: str = ""
    command_pid: int = 0
    started_at: float = 0.0
    baseline: list[str] = field(default_factory=list)
    next_prompt: str = ""
    loops: dict[str, int] = field(default_factory=dict)
    pr: str = ""
    blocked_at: float = 0.0  # set while a comment on the ticket can unblock it
    landed: str = ""  # what became of the PR
    moved: str = ""  # where the ticket last moved to
    opened: bool = False  # the PR has left draft
    reviewing: bool = False  # someone's PR under review, not a delivery, so workshop only reads it
    replying: bool = False  # comments on your own PR: fixes and replies wait for your go
    replied: bool = False  # those went out
    posted: str = ""  # the commit whose review went out
    landed_as: str = ""  # where the queue talk left its ticket, if it moved it
    sent: int = 0  # how many replies have, should a send stop halfway
    reviewed: str = ""  # what the last review looked at, a commit on a PR, otherwise a snapshot
    checking: str = ""  # the snapshot this review is looking at
    begun: float = 0.0
    claimed: bool = False  # others can see this review is yours
    noticed: list[str] = field(default_factory=list)  # what others did on the PR, once told
    preparing: bool = False  # a talk about a ticket, so it delivers nothing and writes only on your go
    your_turn: bool = False  # an interactive step's agent is waiting for you, as planned
    told: str = ""  # the interactive step you've been told about
    environments: dict[str, str] = field(default_factory=dict)  # the repo's, branch to environment, if it names its own
    deployed: str = ""  # the furthest environment its PR has reached
    sweeping: str = ""  # which scheduled look at the repo this is
    decided: dict[str, str] = field(default_factory=dict)  # your answer to each proposal: do, skip or guide
    revised: bool = False  # the agent has acted on your guidance
    applied: list[int] = field(default_factory=list)  # proposals done, should a failure stop it halfway

    @property
    def current(self) -> dict[str, Any] | None:
        steps = self.pipeline["steps"]
        return steps[self.step] if self.step < len(steps) else None

    @property
    def label(self) -> str:
        """A finished run is still with people until its PR lands."""
        if self.deployed == "prod" and self.landed == "merged":
            return "in prod"
        finished = "replied" if self.replied else "your turn" if self.with_you else "posted" if self.on_a_pr else "in review"
        working = "reviewing" if self.reviewing else "replying" if self.replying else "running"
        return self.landed or self.deployed or (finished if self.status == "done" else "your turn" if self.your_turn else working if self.status == "running" else NAMES[self.status])

    @property
    def on_a_pr(self) -> bool:
        """Started from a PR, so its source is that PR and it delivers nothing."""
        return self.reviewing or self.replying

    @property
    def with_you(self) -> bool:
        """Done, and waiting in hunk: a review or reply until you send it, a delivery until your self-review lets it go."""
        if self.status != "done" or self.landed:
            return False
        if self.on_a_pr:
            return not self.replied and not (self.posted and self.posted == self.reviewed)
        return bool(self.pr) and not self.opened and not self.deployed

    @property
    def solo(self) -> bool:
        """Only its step agents do anything here, so one of them has the workspace to itself."""
        return self.reviewing or self.preparing or bool(self.sweeping)

    @property
    def operating(self) -> bool:
        """Past the merge, or looking after what's live. What comes of it starts as new tickets."""
        return bool(self.sweeping or self.deployed or self.landed == "merged")

    def index_of(self, step_id: str) -> int:
        return [s["id"] for s in self.pipeline["steps"]].index(step_id)


def run_from(text: str) -> Run:
    """Run files outlive the code that wrote them, so a field that's gone is ignored rather than a board that won't open."""
    known = {f.name for f in fields(Run)}
    return Run(**{name: value for name, value in json.loads(text).items() if name in known})


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "runs").mkdir(parents=True, exist_ok=True)

    def path(self, repo: str, key: str) -> Path:
        return self.root / "runs" / f"{repo}--{key}.json"

    def files(self, run: Run) -> Path:
        """Briefs, reports and notes: the plugin's, so they outlive the worktree."""
        path = self.root / "runs" / f"{run.repo}--{run.key}"
        path.mkdir(exist_ok=True)
        return path

    def exists(self, repo: str, key: str) -> bool:
        return self.path(repo, key).exists()

    def save(self, run: Run) -> None:
        self._replace(self.path(run.repo, run.key), asdict(run))

    def write(self, name: str, data: dict[str, Any]) -> None:
        self._replace(self.root / name, data)

    def read(self, name: str) -> dict[str, Any]:
        path = self.root / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def _replace(self, path: Path, data: dict[str, Any]) -> None:
        # Renamed into place, so a crash or a reader never sees half a file.
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=2)
        os.replace(tmp, path)

    def load(self, repo: str, key: str) -> Run:
        return run_from(self.path(repo, key).read_text(encoding="utf-8"))

    def all(self) -> list[Run]:
        return [run_from(p.read_text(encoding="utf-8")) for p in sorted((self.root / "runs").glob("*.json"))]

    def find(self, key: str) -> Run:
        found = [r for r in self.all() if r.key == key]
        if len(found) != 1:
            raise KeyError(f"{len(found)} runs match {key}")
        return found[0]

    def event(self, run: Run, kind: str, **fields: Any) -> None:
        self.note(run.repo, run.key, kind, **fields)

    def note(self, repo: str, key: str, kind: str, **fields: Any) -> None:
        at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = {"at": at, "type": kind, "repo": repo, "key": key, **fields}
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(line) + "\n")

    def history(self) -> list[dict[str, Any]]:
        log = self.root / "events.jsonl"
        found = []
        for line in log.read_text(encoding="utf-8").splitlines() if log.exists() else []:
            try:
                found.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a reader can catch a line still being written
        return found

    def events(self, run: Run) -> list[dict[str, Any]]:
        return [e for e in self.history() if e["repo"] == run.repo and e["key"] == run.key]
