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
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
STALE_AFTER = 30 * 60
DEFAULTS = {"pause_new_at": 80, "pause_steps_at": 90, "work_hours": [8, 18], "work_days": ["mon", "tue", "wed", "thu", "fri"], "five_hour_pace_margin": 5, "five_hour_pace_stop": 15, "weekly_pace_margin": 5, "weekly_pace_stop": 15}
WINDOWS = {"session": "five_hour", "week (all models)": "seven_day"}
LENGTHS = {"5h": 5 * 60 * 60, "7d": WEEK}
SHORT = {"five_hour": "5h", "seven_day": "7d"}
NAMES = {"5h": "5h limit", "7d": "weekly limit"}
USAGE_LINE = re.compile(r"^Current (session|week \(all models\)): (\d+)% used\b(?:.*?resets (?:(\w{3}) (\d+), )?(\d+)(?::(\d+))?(am|pm) \(([^)]+)\))?", re.MULTILINE)  # a window nothing has used yet has no reset


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
    if reset < here and not month:  # a bare time may be tomorrow
        reset += timedelta(days=1)
    elif reset < here - timedelta(days=180):  # the text gives no year, so January's reset read in December is next year's. One just gone is still this year's
        reset = reset.replace(year=here.year + 1)
    return reset.timestamp()


def live_windows(text: str, now: float) -> dict[str, dict[str, float]]:
    """What Claude's /usage says. It's text for people, so whatever can't be read keeps the saved reading."""
    windows = {}
    for found in USAGE_LINE.finditer(text):
        window, used = WINDOWS[found[1]], int(found[2])
        if found[5] is None:
            if used == 0:  # a new window, so whatever was saved has reset
                windows[window] = {"used_percentage": 0, "resets_at": now}
            continue
        reset = reset_time(found, now)
        if reset is not None and reset - now <= LENGTHS[SHORT[window]] + 3600:  # a reset further off than the window is long is a misreading
            windows[window] = {"used_percentage": used, "resets_at": reset}
    return windows


def until(seconds: float) -> str:
    hours, minutes = int(seconds // 3600), int(seconds % 3600 // 60)
    return f"{hours // 24}d {hours % 24}h" if hours >= 24 else f"{hours}h {minutes}m" if hours else f"{minutes}m"


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
        return DAYS[now.weekday()] in self.limits["work_days"] and start <= now.hour < end

    def _ahead(self, window: str, margin: int) -> str:
        used, left = self.pace()[window]
        if used < 100 and ahead(window, used, left) <= margin:
            return ""
        return f"The {NAMES[window]} is {used:.0f}% used with {until(left)} to go, faster than it can last."

    def _hold(self, at: str, five_margin: str, week_margin: str) -> str:
        """Which rule the 5h window follows depends on the hour. The week always has to last to its reset, so running short means a break."""
        if self.working():
            five = self.pace()["5h"][0]
            held = f"The 5h limit is {five:.0f}% used, and in working hours the rest is kept for you." if five >= self.limits[at] else ""
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
