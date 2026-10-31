"""Turns the tick timer on, off or to a new frequency. The frequency lives in a drop-in, so reinstalling, which refreshes the shipped unit, keeps it."""

import os
import re
from pathlib import Path

from .shell import ToolError, run

TIMER = "workshop-tick.timer"
DEFAULT = "5min"


def dropin() -> Path:
    config = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return config / "systemd" / "user" / f"{TIMER}.d" / "every.conf"


def systemctl(*args: str) -> str:
    return run("systemctl", "--user", *args).strip()


def every_now() -> str:
    found = re.search(r"^OnUnitActiveSec=(.+)$", dropin().read_text(encoding="utf-8"), re.MULTILINE) if dropin().exists() else None
    return found[1] if found else DEFAULT


def show() -> str:
    return f"on, every {every_now()}" if systemctl("show", TIMER, "-p", "UnitFileState", "--value") == "enabled" else "off"


def turn_on(every: str) -> str:
    if not re.fullmatch(r"[1-9][0-9]*(s|m|min|h)", every):
        raise ToolError(f"bad frequency {every}: use a number and s, m, min or h, like 10m")
    dropin().parent.mkdir(parents=True, exist_ok=True)
    # One empty line clears every shipped time, so a second would clear this one too.
    dropin().write_text(f"[Timer]\nOnActiveSec=\nOnActiveSec=1s\nOnUnitActiveSec={every}\n", encoding="utf-8")
    systemctl("daemon-reload")
    systemctl("enable", TIMER)
    systemctl("restart", TIMER)
    return f"on, every {every}. Ready tickets now start unattended."


def turn_off() -> str:
    systemctl("disable", "--now", TIMER)
    dropin().unlink(missing_ok=True)
    systemctl("daemon-reload")
    return "off"


def schedule(argv: list[str]) -> str:
    if not argv:
        return show()
    return turn_off() if argv[0] == "off" else turn_on(argv[0])
