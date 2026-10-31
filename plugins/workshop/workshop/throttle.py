"""Paces work against Claude's limits, using the file the status line saves.

A reading can only rise until its window resets, so an old high reading still holds. An old
low one says little, so tickets then run one at a time. Holding outright would deadlock
overnight, because only running agents refresh the file.
"""

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

WEEK = 7 * 24 * 60 * 60
STALE_AFTER = 30 * 60
DEFAULTS = {"pause_new_at": 60, "pause_steps_at": 85, "weekly_pace_margin": 5, "weekly_reserve": 25}


def usage_path() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "claude-usage.json"


class Throttle:
    def __init__(self, path: Path, settings: dict[str, int], clock: Callable[[], float] = time.time) -> None:
        self.path, self.limits, self.clock = path, DEFAULTS | settings, clock

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _used(self, data: dict[str, Any], window: str) -> float:
        found = data.get(window) or {}
        return found.get("used_percentage", 0) if found.get("resets_at", 0) > self.clock() else 0

    def snapshot(self) -> dict[str, float]:
        data = self._read()
        return {"5h": self._used(data, "five_hour"), "7d": self._used(data, "seven_day")}

    def hold_steps(self) -> str:
        """Why no new agent step may start, or nothing. Running steps always finish."""
        used = self.snapshot()
        if used["5h"] > self.limits["pause_steps_at"]:
            return f"5h usage is {used['5h']}%, above {self.limits['pause_steps_at']}%."
        if used["7d"] > 100 - self.limits["weekly_reserve"]:
            return f"7d usage is {used['7d']}%, inside the {self.limits['weekly_reserve']}% reserve."
        return ""

    def hold_new(self, active: int) -> str:
        """Why no new ticket may start, or nothing."""
        if held := self.hold_steps():
            return held
        data = self._read()
        five, week = self._used(data, "five_hour"), self._used(data, "seven_day")
        if five > self.limits["pause_new_at"]:
            return f"5h usage is {five}%, above {self.limits['pause_new_at']}%."
        if week:
            gone = 100 * (1 - (data["seven_day"]["resets_at"] - self.clock()) / WEEK)
            if week > gone + self.limits["weekly_pace_margin"]:
                return f"7d usage is {week}%, ahead of the {gone:.0f}% of the week gone."
        if active and self.clock() - data.get("at", 0) > STALE_AFTER:
            return "Usage data is missing or old, so one ticket runs at a time."
        return ""
