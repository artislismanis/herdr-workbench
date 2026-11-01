"""How busy the machine and the limits have been, against the work running. Sampling reads /proc and a file, so it costs no tokens."""

import json
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .system import Meter

SAMPLES = "usage.jsonl"
KEEP_DAYS = 30
HOURS = 24
DAY = 24 * 60 * 60


def sample(root: Path, limits: dict[str, float], agents: int, meter: Meter, clock: Callable[[], float] = time.time, wait: Callable[[float], None] = time.sleep) -> None:
    """CPU is what changed between two readings, so it needs a moment to pass."""
    meter.read()
    meter.at = 0.0
    wait(1)
    found = meter.read()
    line = {"at": clock(), "cpu": round(found.get("cpu", 0), 1), "ram": round(found.get("ram", 0), 1), "five": limits.get("5h", 0), "week": limits.get("7d", 0), "agents": agents}
    path = root / SAMPLES
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(line) + "\n")
    kept = samples(root)
    if kept and kept[0]["at"] < clock() - (KEEP_DAYS + 1) * DAY:
        fresh = [s for s in kept if s["at"] >= clock() - KEEP_DAYS * DAY]
        path.write_text("".join(json.dumps(s) + "\n" for s in fresh), encoding="utf-8")


def samples(root: Path) -> list[dict[str, Any]]:
    path = root / SAMPLES
    found = []
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        try:
            found.append(json.loads(line))
        except ValueError:
            continue  # a line cut short by a crash shouldn't lose the rest
    return found


def spent_at(event: dict[str, Any]) -> float:
    return datetime.strptime(event["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()


def _peaks(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {name: max((row[name] for row in rows), default=0) for name in ("cpu", "ram", "five", "agents")}


def _spend(events: list[dict[str, Any]]) -> float:
    return sum(e.get("usd") or 0 for e in events)


def by_hour(rows: list[dict[str, Any]], events: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    """The last hours with anything in them, oldest first."""
    since = now - HOURS * 60 * 60
    def hour(at: float) -> datetime:
        return datetime.fromtimestamp(at).replace(minute=0, second=0, microsecond=0)
    grouped: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["at"] >= since:
            grouped[hour(row["at"])].append(row)
    spending: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if (at := spent_at(event)) >= since:
            spending[hour(at)].append(event)
    return [{"hour": when, **_peaks(grouped[when]), "spend": _spend(spending[when])} for when in sorted({*grouped, *spending})]


def totals(rows: list[dict[str, Any]], events: list[dict[str, Any]], now: float, days: float) -> dict[str, Any]:
    since = now - days * DAY if days != 1 else datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return {**_peaks([r for r in rows if r["at"] >= since]), "spend": _spend([e for e in events if spent_at(e) >= since])}


def top(events: list[dict[str, Any]], now: float) -> tuple[str, str]:
    """Where a week's spend went, as the place to look when tuning models and prompts."""
    recent = [e for e in events if spent_at(e) >= now - 7 * DAY]
    models, steps = Counter[str](), Counter[str]()
    for event in recent:
        models[event.get("model") or "not recorded"] += event.get("usd") or 0
        steps[event["step"]] += event.get("usd") or 0
    return (models.most_common(1) or [("", 0)])[0][0], (steps.most_common(1) or [("", 0)])[0][0]
