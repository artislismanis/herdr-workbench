"""The board: what's with agents, what's with you and what's coming. It reads local files only, so opening it costs no tokens or API calls."""

import curses
import locale
import textwrap
import threading
import time
from collections.abc import Callable
from typing import Any, NamedTuple

from .backlog import COLUMNS, SNAPSHOT, poll
from .engine import STOPS, Engine
from .store import Run

SKIPPED = "skipped.json"
TABS = ("Deliveries", "Backlog")
HEADINGS = {"parked": "Needs you", "waiting": "Needs you", "running": "With agents", "done": "With people"}
ORDER = ("parked", "waiting", "running", "done")
COLOURS = {"parked": "red", "waiting": "yellow", "running": "green", "done": ""}
BAD_EVENTS = ("parked", "check_failed", "move_failed", "report_failed")
RECENT = 10
LANDED = 5


class Line(NamedTuple):
    text: str
    style: str = ""
    run: Run | None = None


def clean(text: str) -> str:
    """Ticket text comes from other people, so nothing in it reaches the terminal as a control."""
    return "".join(c if c.isprintable() else " " for c in text)


def ago(seconds: float) -> str:
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{int(seconds // size)}{unit}"
    return f"{int(seconds)}s"


def name(run: Run) -> str:
    return f"{run.repo}/{run.key}"


def step_id(run: Run) -> str:
    return run.current["id"] if run.current else "-"


def recent(events: list[dict[str, Any]], count: int) -> list[Line]:
    shown = [e for e in events if e["type"] != "spent"][-count:]
    return [Line(f"{e['at'][5:16].replace('T', ' ')} {e['repo']}/{e['key']} {e['type']} {e.get('step', '')} {e.get('state', '')}".rstrip(), "red" if e["type"] in BAD_EVENTS else "dim") for e in shown]


def deliveries(runs: list[Run], events: list[dict[str, Any]], title: Callable[[Run], str]) -> list[Line]:
    last = {(e["repo"], e["key"]): e["at"] for e in events}
    landed = sorted((r for r in runs if r.landed), key=lambda r: last.get((r.repo, r.key), ""))[-LANDED:]
    lines: list[Line] = []
    heading = ""
    for run in sorted((r for r in runs if not r.landed), key=lambda r: ORDER.index(r.status)):
        if HEADINGS[run.status] != heading:
            heading = HEADINGS[run.status]
            lines += [Line(""), Line(heading, "bold")]
        detail = run.held or run.reason or run.pr
        lines.append(Line(f"  {run.status:8} {name(run):24} {step_id(run):10} {title(run)[:40]:40} {detail}", COLOURS[run.status], run))
    if landed:
        lines += [Line(""), Line("Landed", "bold")]
        lines += [Line(f"  {r.landed:8} {name(r):24} {title(r)}", "dim", r) for r in landed]
    if not runs:
        lines.append(Line("No tickets picked up yet."))
    return [*lines, Line(""), Line("Recent", "bold"), *recent(events, RECENT)]


def backlog(snapshot: dict[str, Any], skipped: dict[str, str], started: Callable[[str, str], bool]) -> list[Line]:
    repos = snapshot.get("repos", {})
    if not repos:
        return [Line("Nothing saved yet. Press r, or wait for the next tick.")]
    lines = [Line(f"{repo}: couldn't update: {r['error']}", "red") for repo, r in repos.items() if r["error"]]
    found = {column: [(repo, i) for repo, r in repos.items() for i in r["items"] if i["stage"] == column and not started(repo, i["key"])] for column in COLUMNS}
    lines.append(Line("   ".join(f"{column.title()} {len(items)}" for column, items in found.items()), "bold"))
    for column, items in found.items():
        lines += [Line(""), Line(column.title(), "bold")]
        for repo, item in items:
            ticket = f"{repo}/{item['key']}"
            why = f"  skipped: {skipped[ticket]}" if ticket in skipped else ""
            lines.append(Line(f"  {ticket:24} {item['title']}{why}", "yellow" if why else ""))
    return lines


def steps(run: Run, events: list[dict[str, Any]]) -> list[Line]:
    lines = []
    for index, step in enumerate(run.pipeline["steps"]):
        seen = [e for e in events if e.get("step") == step["id"]]
        spent = [e for e in seen if e["type"] == "spent"]
        finished = [e for e in seen if e["type"] == "step" and e.get("state") == "done"]
        current = index == run.step and run.status != "done"
        state = run.status if current else "done" if finished else "pending"
        facts = [f"{len(spent)} round(s)"] if spent else []
        if finished and finished[-1].get("verdict"):
            facts.append(finished[-1]["verdict"])
        if spent:
            prices = [e["usd"] for e in spent]
            facts += [f"{sum(e['minutes'] for e in spent):.1f} min", "unpriced" if None in prices else f"${sum(prices):.2f}"]
        lines.append(Line(f"  {state:8} {step['id']:12} {'  '.join(facts)}".rstrip(), COLOURS.get(state, "dim") if current else "dim" if state == "pending" else ""))
    return lines


def drill(run: Run, events: list[dict[str, Any]], title: str, next_action: str, files: str, width: int) -> list[Line]:
    lines = [Line(f"{name(run)}  {title}", "bold"), Line(f"{run.status} at {step_id(run)}", COLOURS[run.status])]
    if run.reason:
        lines += [Line(text, "red" if run.status == "parked" else "yellow") for text in textwrap.wrap(run.reason, max(width - 2, 20))]
    if next_action:
        lines.append(Line(f"Then: {next_action}"))
    if run.held:
        lines.append(Line(f"Held: {run.held}", "yellow"))
    if run.pr:
        lines.append(Line(f"PR: {run.pr}"))
    return [*lines, Line(f"Files: {files}", "dim"), Line(""), Line("Steps", "bold"), *steps(run, events), Line(""), Line("Log", "bold"), *recent(events, 12)]


class Board:
    def __init__(self, engine: Engine, cfg: dict[str, Any], clock: Callable[[], float] = time.time) -> None:
        self.engine, self.store, self.cfg, self.clock = engine, engine.store, cfg, clock
        self.tab, self.offset = 0, 0
        self.picked: tuple[str, str] | None = None
        self.open: tuple[str, str] | None = None
        self.message, self.refreshing, self.seen = "", False, None
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

    def lines(self, width: int) -> list[Line]:
        if run := self.drilled():
            action = self.engine.next_action(run) if run.status in ("parked", "waiting") else ""
            events = [e for e in self.events if (e["repo"], e["key"]) == self.open]
            return drill(run, events, self.title(run), action, str(self.store.files(run)), width)
        if self.tab == 0:
            return deliveries(self.runs, self.events, self.title)
        return backlog(self.snapshot, self.skipped, self.store.exists)

    def choices(self, lines: list[Line]) -> list[tuple[str, str]]:
        return [(line.run.repo, line.run.key) for line in lines if line.run]

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
        lines = self.lines(width)
        self.message = ""
        if key in (curses.KEY_UP, ord("k")):
            self.move(-1, lines)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.move(1, lines)
        elif key in (10, 13, curses.KEY_ENTER) and not self.open:
            self.open, self.offset = self.selected(lines), 0
        elif key == 27 and self.open:
            self.open, self.offset = None, 0
        elif key == ord("q"):
            if not self.open:
                return False
            self.open, self.offset = None, 0
        elif key == ord("\t") and not self.open:
            self.tab, self.offset = (self.tab + 1) % len(TABS), 0
        elif key == ord("r"):
            self.refresh()
        elif key == ord("g"):
            self.jump(self.open or self.selected(lines))
        return True

    def jump(self, target: tuple[str, str] | None) -> None:
        run = next((r for r in self.runs if (r.repo, r.key) == target), None)
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

    def header(self) -> str:
        tabs = "  ".join(f"[{t}]" if i == self.tab else f" {t} " for i, t in enumerate(TABS))
        times = [r["polled_at"] for r in self.snapshot.get("repos", {}).values()]
        age = f"backlog {ago(self.clock() - min(times))} old" if times and min(times) else "backlog not polled"
        held = self.engine.throttle.hold_new(len([r for r in self.runs if r.status == "running"]))
        return "  ".join(part for part in (f"Workshop  {tabs}", age, f"held: {held}" if held else "") if part)

    def footer(self) -> str:
        if self.refreshing:
            return "Refreshing..."
        if self.message:
            return self.message
        return "esc back  g go to workspace  r refresh  q back" if self.open else "tab switch  up/down move  enter open  g go to workspace  r refresh  q quit"

    def window(self, lines: list[Line], height: int) -> list[Line]:
        chosen = self.selected(lines)
        at = next((i for i, line in enumerate(lines) if line.run and (line.run.repo, line.run.key) == chosen), None)
        if at is not None:
            self.offset = max(min(self.offset, at), at - height + 1)
        self.offset = max(0, min(self.offset, len(lines) - height))
        return lines[self.offset : self.offset + height]

    def draw(self, screen: curses.window, styles: dict[str, int]) -> None:
        rows, width = screen.getmaxyx()
        lines = self.lines(width)
        chosen = self.selected(lines)
        screen.erase()
        screen.addnstr(0, 0, clean(self.header()), width - 1, curses.A_BOLD)
        for y, line in enumerate(self.window(lines, rows - 2), 1):
            mark = curses.A_REVERSE if line.run and (line.run.repo, line.run.key) == chosen and not self.open else 0
            screen.addnstr(y, 0, clean(line.text), width - 1, styles[line.style] | mark)
        screen.addnstr(rows - 1, 0, clean(self.footer()), width - 1, curses.A_DIM)
        screen.refresh()

    def loop(self, screen: curses.window) -> None:
        curses.curs_set(0)
        curses.use_default_colors()
        for pair, colour in enumerate((curses.COLOR_RED, curses.COLOR_YELLOW, curses.COLOR_GREEN), 1):
            curses.init_pair(pair, colour, -1)
        styles = {"": 0, "bold": curses.A_BOLD, "dim": curses.A_DIM, "red": curses.color_pair(1), "yellow": curses.color_pair(2), "green": curses.color_pair(3)}
        screen.timeout(1000)
        while True:
            self.reload()
            self.draw(screen, styles)
            if not self.press(screen.getch(), screen.getmaxyx()[1]):
                return


def show(engine: Engine, cfg: dict[str, Any]) -> None:
    locale.setlocale(locale.LC_ALL, "")
    curses.set_escdelay(25)
    curses.wrapper(Board(engine, cfg).loop)
