"""The board: what's with agents, what's with you and what's coming. It reads local files only, so opening it costs no tokens or API calls."""

import curses
import locale
import threading
import time
from collections.abc import Callable
from typing import Any

from .backlog import SNAPSHOT, poll
from .boardview import Line, Span, ago, backlog, cell, clean, columns, deliveries, drill, pull, pulls
from .engine import STOPS, Engine
from .store import Run

SKIPPED = "skipped.json"
TABS = ("Deliveries", "Backlog", "PRs")
WHEEL = 3
ESCAPE = 27
TAB_KEY = ord("\t")
ENTERS = (10, 13, curses.KEY_ENTER)
Action = str | tuple[str, str]


class Board:
    def __init__(self, engine: Engine, cfg: dict[str, Any], clock: Callable[[], float] = time.time) -> None:
        self.engine, self.store, self.cfg, self.clock = engine, engine.store, cfg, clock
        self.tab, self.offset = 0, 0
        self.picked: tuple[str, str] | None = None
        self.open: tuple[str, str] | None = None
        self.message, self.refreshing, self.seen = "", False, None
        self.regions: list[tuple[int, int, int, Action]] = []
        self.reload()

    def stamp(self) -> tuple[int, ...]:
        paths = (self.store.root / "runs", self.store.root / "events.jsonl", self.store.root / SNAPSHOT, self.store.root / SKIPPED)
        return tuple(p.stat().st_mtime_ns if p.exists() else 0 for p in paths)

    def reload(self) -> None:
        if (stamp := self.stamp()) != self.seen:
            self.seen = stamp
            self.runs, self.events = self.store.all(), self.store.history()
            self.snapshot, self.skipped = self.store.read(SNAPSHOT), self.store.read(SKIPPED)

    def title(self, run: Run) -> str:
        brief = self.store.files(run) / "brief.md"
        first = brief.read_text(encoding="utf-8").partition("\n")[0] if brief.exists() else ""
        return first.removeprefix("# ").removeprefix(f"{run.key}: ")

    def drilled(self) -> Run | None:
        return next((r for r in self.runs if (r.repo, r.key) == self.open), None)

    def opened_pr(self) -> dict[str, Any] | None:
        return next((p for p in self.snapshot.get("prs", {}).get("items", []) if ("#pr", p["url"]) == self.open), None)

    def lines(self, width: int) -> list[Line]:
        if pr := self.opened_pr():
            return pull(pr, width)
        if run := self.drilled():
            action = self.engine.next_action(run) if run.status in ("parked", "waiting") else ""
            events = [e for e in self.events if (e["repo"], e["key"]) == self.open]
            return drill(run, events, self.title(run), action, str(self.store.files(run)), width)
        if self.tab == 0:
            return deliveries(self.runs, self.events, self.title, width, self.engine.throttle.snapshot())
        return backlog(self.snapshot, self.skipped, self.store.exists, width) if self.tab == 1 else pulls(self.snapshot, width)

    def choices(self, lines: list[Line]) -> list[tuple[str, str]]:
        return [line.ref for line in lines if line.ref]

    def selected(self, lines: list[Line]) -> tuple[str, str] | None:
        choices = self.choices(lines)
        return self.picked if self.picked in choices else choices[0] if choices else None

    def move(self, by: int, lines: list[Line]) -> None:
        choices = self.choices(lines)
        if choices:
            index = choices.index(self.selected(lines)) + by
            self.picked = choices[max(0, min(index, len(choices) - 1))]
        else:
            self.offset = max(0, self.offset + by)

    def press(self, key: int, width: int = 120) -> bool:
        """Handles one key. False means leave."""
        if key == curses.ERR:
            return True  # the timeout that redraws, so a message stays until a real key
        lines = self.lines(width)
        self.message = ""
        if key in (curses.KEY_UP, ord("k")):
            self.move(-1, lines)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.move(1, lines)
        elif key in ENTERS and not self.open:
            self.open, self.offset = self.selected(lines), 0
        elif key == ESCAPE and self.open:
            self.open, self.offset = None, 0
        elif key == ord("q"):
            if not self.open:
                return False
            self.open, self.offset = None, 0
        elif key in (TAB_KEY, curses.KEY_BTAB) and not self.open:
            self.tab, self.offset = (self.tab + (1 if key == TAB_KEY else -1)) % len(TABS), 0
        elif key == ord("r"):
            self.refresh()
        elif key == ord("g"):
            self.jump(self.open or self.selected(lines))
        return True

    def jump(self, target: tuple[str, str] | None) -> None:
        run = next((r for r in self.runs if target in ((r.repo, r.key), ("#pr", r.pr))), None)
        try:
            if run is None or not run.pane:
                raise KeyError("no workspace")
            self.engine.tools.focus(run.pane)
        except STOPS:
            self.message = "That run has no workspace now."

    def refresh(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True

        def work() -> None:
            try:
                poll(self.cfg, self.store, self.clock)
            except STOPS as failed:
                self.message = f"Refresh failed: {failed}"
            self.refreshing = False

        threading.Thread(target=work, daemon=True).start()

    def counts(self) -> tuple[int, ...]:
        waiting = sum(r.status in ("parked", "waiting") for r in self.runs)
        tickets = sum(not self.store.exists(repo, i["key"]) for repo, r in self.snapshot.get("repos", {}).items() for i in r["items"])
        return waiting, tickets, sum(p["move"] == "you" for p in self.snapshot.get("prs", {}).get("items", []))

    def header(self) -> Line:
        tabs = [part for i, (tab, count) in enumerate(zip(TABS, self.counts(), strict=True)) for part in (Span(f" {tab} {count} ", "tab-on" if i == self.tab and not self.open else "tab", f"tab:{i}"), Span(" "))]
        kind = "PRs" if self.tab == 2 else "backlog"
        sources = [self.snapshot.get("prs", {})] if self.tab == 2 else self.snapshot.get("repos", {}).values()
        times = [source.get("polled_at", 0) for source in sources]
        age = f"{kind} {ago(self.clock() - min(times))} old" if times and min(times) else f"{kind} not polled"
        held = self.engine.throttle.hold_new(len([r for r in self.runs if r.status == "running"]))
        return Line([*tabs, Span(f" {age}", "dim"), Span(f"   held: {held}" if held else "", "yellow")])

    def footer(self) -> Line:
        if self.refreshing:
            return Line([Span("Refreshing...", "dim")])
        if self.message:
            return Line([Span(self.message, "yellow")])
        buttons = [("esc back", ESCAPE), ("g workspace", ord("g")), ("r refresh", ord("r"))] if self.open else [("tab switch", TAB_KEY), ("enter open", 10), ("g workspace", ord("g")), ("r refresh", ord("r")), ("q quit", ord("q"))]
        return Line([part for label, key in buttons for part in (Span(f" {label} ", "button", f"key:{key}"), Span(" "))])

    def wheel(self, by: int, width: int) -> None:
        lines = self.lines(width)
        for _ in range(abs(by)):
            self.move(1 if by > 0 else -1, lines)

    def click(self, x: int, y: int, width: int) -> bool:
        """Does what the thing under the pointer does. False means leave."""
        for top, left, right, action in self.regions:
            if y != top or not left <= x < right:
                continue
            if isinstance(action, tuple):
                self.picked, self.open, self.offset = action, action, 0
                return True
            kind, _, value = action.partition(":")
            if kind == "tab":
                self.tab, self.open, self.offset = int(value), None, 0
                return True
            return self.press(int(value), width)
        return True

    def mouse(self, x: int, y: int, state: int, width: int) -> bool:
        if state & curses.BUTTON4_PRESSED:
            self.wheel(-WHEEL, width)
        elif state & curses.BUTTON5_PRESSED:
            self.wheel(WHEEL, width)
        elif state & curses.BUTTON1_PRESSED:
            return self.click(x, y, width)
        return True

    def window(self, lines: list[Line], height: int) -> list[Line]:
        chosen = self.selected(lines)
        at = next((i for i, line in enumerate(lines) if line.ref and line.ref == chosen), None)
        if at is not None:
            self.offset = max(min(self.offset, at), at - height + 1)
        self.offset = max(0, min(self.offset, len(lines) - height))
        return lines[self.offset : self.offset + height]

    def put(self, screen: curses.window, y: int, line: Line, styles: dict[str, int], width: int, mark: int = 0) -> None:
        x = 0
        for span in line.spans:
            text = clean(span.text)
            if x >= width - 1:
                break
            screen.addnstr(y, x, text, width - 1 - x, styles[span.style] | mark)
            if span.action:
                self.regions.append((y, x, x + columns(text), span.action))
            x += columns(text)
        if mark and x < width - 1:
            screen.addnstr(y, x, cell("", width - 1 - x), width - 1 - x, mark)
        if line.ref:
            self.regions.append((y, 0, width, line.ref))

    def draw(self, screen: curses.window, styles: dict[str, int]) -> None:
        rows, width = screen.getmaxyx()
        lines = self.lines(width - 1)
        chosen = self.selected(lines)
        self.regions = []
        screen.erase()
        self.put(screen, 0, self.header(), styles, width)
        for y, line in enumerate(self.window(lines, rows - 3), 2):
            self.put(screen, y, line, styles, width, curses.A_REVERSE if line.ref and line.ref == chosen and not self.open else 0)
        self.put(screen, rows - 1, self.footer(), styles, width)
        screen.refresh()

    def colours(self) -> dict[str, int]:
        curses.use_default_colors()
        hues = {"red": curses.COLOR_RED, "yellow": curses.COLOR_YELLOW, "green": curses.COLOR_GREEN, "mine": curses.COLOR_CYAN, "others": curses.COLOR_MAGENTA}
        styles = {"": 0, "bold": curses.A_BOLD, "dim": curses.A_DIM}
        for pair, (style, hue) in enumerate(hues.items(), 1):
            curses.init_pair(pair, hue, -1)
            curses.init_pair(pair + len(hues), curses.COLOR_BLACK, hue)
            styles[style], styles[f"on-{style}"] = curses.color_pair(pair), curses.color_pair(pair + len(hues))
        grey = 2 * len(hues) + 1
        curses.init_pair(grey, -1, 238 if curses.COLORS >= 256 else curses.COLOR_BLACK)
        styles["button"] = styles["tab"] = styles["on-dim"] = curses.color_pair(grey)
        styles["tab-on"] = styles["on-mine"] | curses.A_BOLD
        return styles

    def loop(self, screen: curses.window) -> None:
        curses.curs_set(0)
        styles = self.colours()
        curses.mousemask(curses.BUTTON1_PRESSED | curses.BUTTON4_PRESSED | curses.BUTTON5_PRESSED)
        curses.mouseinterval(0)
        screen.timeout(1000)
        while True:
            self.reload()
            self.draw(screen, styles)
            key, width = screen.getch(), screen.getmaxyx()[1]
            if key == curses.KEY_MOUSE:
                try:
                    _, x, y, _, state = curses.getmouse()
                except curses.error:
                    continue  # an event curses couldn't read, such as one past the edge
                if not self.mouse(x, y, state, width - 1):
                    return
            elif not self.press(key, width - 1):
                return


def show(engine: Engine, cfg: dict[str, Any]) -> None:
    locale.setlocale(locale.LC_ALL, "")
    curses.set_escdelay(25)
    curses.wrapper(Board(engine, cfg).loop)
