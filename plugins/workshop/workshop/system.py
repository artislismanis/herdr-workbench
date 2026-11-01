"""What the machine has to give, from /proc, to see whether WSL needs more."""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

EVERY = 2  # seconds, so a key press can't shrink the sample to nothing


def agents_open(proc: Path = Path("/proc")) -> int:
    """Every Claude open on this machine, working or not, so work outside the workshop shows too."""
    found = 0
    for name in proc.glob("[0-9]*/comm"):
        try:
            found += name.read_text(encoding="utf-8").strip() == "claude"
        except OSError:
            continue  # it ended while we looked
    return found


class Meter:
    """Peaks last as long as the board stays open."""

    def __init__(self, proc: Path = Path("/proc"), clock: Callable[[], float] = time.time) -> None:
        self.proc, self.clock = proc, clock
        self.at, self.idle, self.total = 0.0, 0, 0
        self.found: dict[str, Any] = {}

    def _memory(self) -> tuple[float, float]:
        fields = {line.split(":")[0]: int(line.split()[1]) for line in (self.proc / "meminfo").read_text(encoding="utf-8").splitlines() if line.endswith("kB")}
        total = fields["MemTotal"]
        return 100 * (1 - fields["MemAvailable"] / total), total / 1024 / 1024

    def _cpu(self) -> tuple[float, int]:
        lines = (self.proc / "stat").read_text(encoding="utf-8").splitlines()
        ticks = [int(n) for n in lines[0].split()[1:9]]
        cores = sum(line.startswith("cpu") for line in lines) - 1
        idle, total = ticks[3] + ticks[4], sum(ticks)
        used = 100 * (1 - (idle - self.idle) / (total - self.total)) if self.total and total > self.total else self.found.get("cpu", 0.0)
        self.idle, self.total = idle, total
        return used, cores

    def read(self) -> dict[str, Any]:
        if self.clock() - self.at < EVERY:
            return self.found
        self.at = self.clock()
        try:
            cpu, cores = self._cpu()
            ram, memory = self._memory()
        except (OSError, ValueError, KeyError, IndexError):
            return {}  # not Linux, or /proc changed: the board goes without
        self.found = {"cpu": cpu, "cores": cores, "ram": ram, "memory": memory, "cpu_peak": max(cpu, self.found.get("cpu_peak", 0)), "ram_peak": max(ram, self.found.get("ram_peak", 0))}
        return self.found
