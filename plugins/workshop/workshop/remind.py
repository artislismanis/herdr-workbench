"""Reminders about what waits on you, as one notification is easy to miss. Run from the sample timer, so it reads local files only."""

from collections.abc import Callable
from datetime import datetime

from .boardview import WORK, Waiting, Work, working
from .store import Store
from .throttle import DAYS

REMINDED = "reminded.json"
AFTER = 3600  # working seconds before the first reminder


def at_work(now: float, work: Work) -> bool:
    moment, ((first, last), days) = datetime.fromtimestamp(now), work
    return DAYS[moment.weekday()] in days and first <= moment.hour < last


def remind(store: Store, found: list[Waiting], now: float, notify: Callable[[str, str], None], work: Work = WORK) -> None:
    """Once when something has waited an hour, then once each working morning while it still waits. Everything due goes in one notification."""
    if not at_work(now, work):
        return
    seen, today, due, kept = store.read(REMINDED), datetime.fromtimestamp(now).date().isoformat(), [], {}
    for item in found:
        key = "|".join(item.ref)
        day = seen.get(key, "")
        if (not day and item.since and working(datetime.fromisoformat(item.since).timestamp(), now, work) >= AFTER) or (day and day != today):
            due.append(item)
            day = today
        if day:
            kept[key] = day
    store.write(REMINDED, kept)  # what no longer waits drops out, so it's reminded afresh if it comes back
    if due:
        notify(f"{len(due)} waiting on you", ", ".join(f"{item.name} {item.label}" for item in due))
