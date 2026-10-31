"""Everything a run knows lives in one JSON file, so any process can pick it up."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ACTIVE = ("running", "parked")


@dataclass
class Run:
    repo: str
    key: str
    repo_path: str
    base: str
    pipeline: dict[str, Any]  # copied in, so editing the file can't change a run in flight
    step: int = 0
    phase: str = ""  # "" before a step starts, "prompted" while an agent works on it
    status: str = "running"  # running, parked, done
    reason: str = ""
    held: str = ""  # why the throttle is keeping the next step back
    worktree: str = ""
    branch: str = ""
    pane: str = ""
    agent: str = ""
    step_pane: str = ""
    step_agent: str = ""
    started_at: float = 0.0
    baseline: list[str] = field(default_factory=list)
    next_prompt: str = ""
    loops: dict[str, int] = field(default_factory=dict)
    pr: str = ""

    @property
    def current(self) -> dict[str, Any] | None:
        steps = self.pipeline["steps"]
        return steps[self.step] if self.step < len(steps) else None

    def index_of(self, step_id: str) -> int:
        return [s["id"] for s in self.pipeline["steps"]].index(step_id)


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "runs").mkdir(parents=True, exist_ok=True)

    def path(self, repo: str, key: str) -> Path:
        return self.root / "runs" / f"{repo}--{key}.json"

    def exists(self, repo: str, key: str) -> bool:
        return self.path(repo, key).exists()

    def save(self, run: Run) -> None:
        # Renamed into place, so a crash or a reader never sees half a file.
        fd, tmp = tempfile.mkstemp(dir=self.root / "runs", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(asdict(run), out, indent=2)
        os.replace(tmp, self.path(run.repo, run.key))

    def load(self, repo: str, key: str) -> Run:
        return Run(**json.loads(self.path(repo, key).read_text(encoding="utf-8")))

    def all(self) -> list[Run]:
        return [Run(**json.loads(p.read_text(encoding="utf-8"))) for p in sorted((self.root / "runs").glob("*.json"))]

    def find(self, key: str) -> Run:
        found = [r for r in self.all() if r.key == key]
        if len(found) != 1:
            raise KeyError(f"{len(found)} runs match {key}")
        return found[0]

    def event(self, run: Run, kind: str, **fields: Any) -> None:
        at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = {"at": at, "type": kind, "repo": run.repo, "key": run.key, **fields}
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(line) + "\n")

    def events(self, run: Run) -> list[dict[str, Any]]:
        log = self.root / "events.jsonl"
        if not log.exists():
            return []
        lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return [e for e in lines if e["repo"] == run.repo and e["key"] == run.key]
