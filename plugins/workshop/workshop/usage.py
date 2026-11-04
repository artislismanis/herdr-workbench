"""How busy the machine and the limits have been, against the work running. Sampling reads /proc and a file, so it costs no tokens."""

import json
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .system import Meter

SAMPLES = "usage.jsonl"
KEEP_DAYS = 30
DAY = 24 * 60 * 60
WINDOWS = {"5h": 5 * 60 * 60, "7d": 7 * DAY}
LOADS = (("0-2", 0, 2), ("3-4", 3, 4), ("5-8", 5, 8), ("9+", 9, 10**6))  # agents open at once
ENOUGH_USE = 50  # points of the 5h limit, before its cost to the week means much


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
    return {name: max((row.get(name, 0) for row in rows), default=0) for name in ("cpu", "ram", "five", "agents")}


def _spend(events: list[dict[str, Any]]) -> float:
    return sum(e.get("usd") or 0 for e in events)


def by_day(rows: list[dict[str, Any]], events: list[dict[str, Any]], now: float, days: int = KEEP_DAYS) -> list[dict[str, Any]]:
    """Each of the last days, oldest first, with None for a day with nothing in it, so a trend shows its gaps."""
    today = datetime.fromtimestamp(now).date()
    first = today - timedelta(days=days - 1)
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[datetime.fromtimestamp(row["at"]).date()].append(row)
    spending: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        spending[datetime.fromtimestamp(spent_at(event)).date()].append(event)
    found = []
    for n in range(days):
        day = first + timedelta(days=n)
        found.append({"day": day, **_peaks(grouped[day]), "spend": _spend(spending[day])} if grouped[day] or spending[day] else None)
    return found


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


def outlook(window: str, used: float, left: float) -> tuple[float | None, float]:
    """Where the window ends up at today's rate, and if that's past 100%, how long until it runs out. Too early in a window, the rate means nothing."""
    elapsed = WINDOWS[window] - left
    if not left or elapsed < WINDOWS[window] / 24:
        return None, 0
    rate = used / elapsed
    projected = used + rate * left
    return round(projected), (100 - used) / rate if projected > 100 and rate else 0


def week_cost(rows: list[dict[str, Any]], now: float) -> float | None:
    """Points of the week a full 5h window takes, from how the two rose together this week. A reset of either is left out."""
    recent = [r for r in rows if r["at"] >= now - 7 * DAY]
    five = week = 0.0
    for before, after in zip(recent, recent[1:]):
        rose, also = after.get("five", 0) - before.get("five", 0), after.get("week", 0) - before.get("week", 0)
        if rose > 0 and also >= 0 and after["at"] - before["at"] < 3600:
            five, week = five + rose, week + also
    return 100 * week / five if five >= ENOUGH_USE else None


def by_load(rows: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    """This week's busiest CPU and RAM by how many agents were open, to see whether more at once strains the machine."""
    recent = [r for r in rows if r["at"] >= now - 7 * DAY and "agents" in r]
    found = []
    for name, low, high in LOADS:
        group = [r for r in recent if low <= r["agents"] <= high]
        if group:
            found.append({"agents": name, "samples": len(group), "cpu": max(r["cpu"] for r in group), "ram": max(r["ram"] for r in group)})
    return found
