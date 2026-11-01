"""Paces work against Claude's limits, using the file the status line saves and each tick updates from Claude's /usage.

A reading can only rise until its window resets, so an old high reading still holds. An old
low one says little, so tickets then run one at a time. Holding outright would deadlock
overnight, if /usage can't be read and only running agents refresh the file.
"""

import json
import os
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEEK = 7 * 24 * 60 * 60
STALE_AFTER = 30 * 60
DEFAULTS = {"pause_new_at": 80, "pause_steps_at": 90, "work_hours": [8, 18], "five_hour_pace_margin": 5, "five_hour_pace_stop": 15, "weekly_pace_margin": 5, "weekly_pace_stop": 15}
WINDOWS = {"session": "five_hour", "week (all models)": "seven_day"}
LENGTHS = {"5h": 5 * 60 * 60, "7d": WEEK}
USAGE_LINE = re.compile(r"^Current (session|week \(all models\)): (\d+)% used\b.*?resets (?:(\w{3}) (\d+), )?(\d+)(?::(\d+))?(am|pm) \(([^)]+)\)", re.MULTILINE)


def usage_path() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "claude-usage.json"


def reset_time(found: re.Match[str], now: float) -> float | None:
    month, day, hour, minute, half, zone = found.group(3, 4, 5, 6, 7, 8)
    try:
        here = datetime.fromtimestamp(now, ZoneInfo(zone))
        clock = datetime.strptime(f"{hour}:{minute or '00'}{half}", "%I:%M%p")
        date = datetime.strptime(f"{month} {day} {here.year}", "%b %d %Y") if month else here
    except (ValueError, ZoneInfoNotFoundError):
        return None
    reset = date.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0, tzinfo=here.tzinfo)
    if reset < here:  # the text gives no year, and a bare time may be tomorrow
        reset = reset.replace(year=here.year + 1) if month else reset + timedelta(days=1)
    return reset.timestamp()


def live_windows(text: str, now: float) -> dict[str, dict[str, float]]:
    """What Claude's /usage says. It's text for people, so whatever can't be read keeps the saved reading."""
    windows = {}
    for found in USAGE_LINE.finditer(text):
        if (reset := reset_time(found, now)) is not None:
            windows[WINDOWS[found[1]]] = {"used_percentage": int(found[2]), "resets_at": reset}
    return windows


def ahead(window: str, used: float, left: float) -> float:
    """Points of the window used beyond an even pace to its reset. At or below zero, it lasts."""
    return used - 100 * (1 - left / LENGTHS[window])


class Throttle:
    def __init__(self, path: Path, settings: dict[str, Any], clock: Callable[[], float] = time.time) -> None:
        self.path, self.limits, self.clock = path, DEFAULTS | settings, clock

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def update(self, text: str) -> None:
        if windows := live_windows(text, self.clock()):
            fresh = self.path.with_suffix(".tmp")
            fresh.write_text(json.dumps(self._read() | windows | {"at": self.clock()}), encoding="utf-8")
            fresh.replace(self.path)

    def _used(self, data: dict[str, Any], window: str) -> float:
        found = data.get(window) or {}
        return found.get("used_percentage", 0) if found.get("resets_at", 0) > self.clock() else 0

    def snapshot(self) -> dict[str, float]:
        data = self._read()
        return {"5h": self._used(data, "five_hour"), "7d": self._used(data, "seven_day")}

    def pace(self) -> dict[str, tuple[float, float]]:
        """Each window's use, and the seconds left until it resets."""
        data = self._read()
        windows = {"5h": "five_hour", "7d": "seven_day"}
        return {name: (self._used(data, key), max(0.0, (data.get(key) or {}).get("resets_at", 0) - self.clock())) for name, key in windows.items()}

    def working(self) -> bool:
        """The hours you want some of the 5h window left for your own use."""
        now, (start, end) = datetime.fromtimestamp(self.clock()), self.limits["work_hours"]
        return now.weekday() < 5 and start <= now.hour < end

    def _ahead(self, window: str, margin: int) -> str:
        used, left = self.pace()[window]
        if used < 100 and ahead(window, used, left) <= margin:
            return ""
        return f"{window} usage is {used}%, ahead of the {100 * (1 - left / LENGTHS[window]):.0f}% of the window gone."

    def _hold(self, at: str, five_margin: str, week_margin: str) -> str:
        """Which rule the 5h window follows depends on the hour. The week always has to last to its reset, so running short means a break."""
        if self.working():
            five = self.pace()["5h"][0]
            held = f"5h usage is {five}%, and {self.limits[at]}% is the most in working hours." if five >= self.limits[at] else ""
        else:
            held = self._ahead("5h", self.limits[five_margin])
        return held or self._ahead("7d", self.limits[week_margin])

    def hold_steps(self) -> str:
        """Why no new agent step may start, or nothing. Running steps always finish."""
        return self._hold("pause_steps_at", "five_hour_pace_stop", "weekly_pace_stop")

    def hold_new(self, active: int) -> str:
        """Why no new ticket may start, or nothing."""
        if held := self._hold("pause_new_at", "five_hour_pace_margin", "weekly_pace_margin"):
            return held
        if active and self.clock() - self._read().get("at", 0) > STALE_AFTER:
            return "Usage data is missing or old, so one ticket runs at a time."
        return ""
