"""The board: what's with agents, what's with you and what's coming. It reads local files only, so opening it costs no tokens or API calls."""

import curses
import locale
import re
import sys
import textwrap
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from .backlog import SNAPSHOT
from .boardview import Line, Span, ago, backlog, cell, clean, columns, deliveries, document, drill, files_name, plain, pull, pulls, rule
from .engine import STOPS, Engine
from .store import Run
from .system import Meter

SKIPPED = "skipped.json"
TABS = ("Backlog", "Delivery", "Review")  # the order work moves through
BACKLOG, DELIVERY, REVIEW = range(3)
TOP = 1  # a gap under herdr's tab bar
WHEEL = 3
MESSAGE_SECONDS = 5
CLICKS = curses.BUTTON1_PRESSED | curses.BUTTON1_RELEASED | curses.BUTTON4_PRESSED | curses.BUTTON5_PRESSED | curses.REPORT_MOUSE_POSITION
DRAGS_ON, DRAGS_OFF = "\033[?1002h", "\033[?1002l"
PASTES_ON, PASTES_OFF = "\033[?2004h", "\033[?2004l"  # marks a paste, so a line break in one isn't enter
PASTE_START, PASTE_END = b"[200~", b"\033[201~"
PASTE_LIMIT = 100_000
NOTE_ROWS = 6
CURSOR = "▌"
CTRL_U, CTRL_W = 21, 23
JITTER = 2  # a press that moves less than this along a row is still a click
PAGE = 10
PANEL_WIDTH = 130  # below this a window can't hold a list and a detail side by side
LIST_SHARE = 55  # percent of the window for the list
ESCAPE = 27
TAB_KEY = ord("\t")
AI_REVIEW = ord("a")  # with nothing stopped under it, where the same key answers
RESPOND = ("changes requested", "comments to address", "fixed, re-request")
AI_REVIEW_OTHER = ord("A")  # the mode you don't usually use
CLAIM = ord("m")
PREPARE = ord("p")
RENEWED = ("re-review", "new commits")
ENTERS = (10, 13, curses.KEY_ENTER)
BACKSPACES = (8, 127, curses.KEY_BACKSPACE)
Action = str | tuple[str, str]


class Keys(Protocol):
    def getch(self) -> int: ...
    def timeout(self, delay: int) -> None: ...


class Ask(NamedTuple):
    """Waiting for your words. Enter sends the command for what's there: blank does one thing, words another. No command for blank means words are needed."""

    key: str
    label: str
    blank: str
    words: str
    hints: tuple[str, ...]


ANSWER = ord("a")
DROP = ord("d")


class Board:
    def __init__(self, engine: Engine, cfg: dict[str, Any], clock: Callable[[], float] = time.time) -> None:
        self.engine, self.store, self.cfg, self.clock = engine, engine.store, cfg, clock
        self.meter = Meter()
        self.tab, self.offset = DELIVERY, 0
        self.picked: tuple[str, str] | None = None
        self.open: tuple[str, str] | None = None
        self.viewing = ""
        self.side = 0  # how far the panel has scrolled
        self.earlier = False  # older rounds' reports showing
        self.message, self.said, self.refreshing, self.seen = "", 0.0, False, None
        self.grab: tuple[int, int] | None = None  # where a press landed, until its release
        self.reach: tuple[int, int] | None = None  # how far the drag has got
        self.painted: dict[int, list[tuple[int, str]]] = {}  # what's on each row, since a drag selects what's drawn
        self.edge, self.right, self.rows = 0, 0, 0
        self.asking: Ask | None = None
        self.typed = ""
        self.cursor = 0
        self.drafts: dict[tuple[str, str], str] = {}  # what esc left, for when you come back
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

    def brief(self, run: Run) -> str:
        path = self.store.files(run) / "brief.md"
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    def reports(self, run: Run) -> tuple[str, ...]:
        found = [p for p in self.store.files(run).iterdir() if p.suffix in (".md", ".json")]
        return tuple(str(p) for p in sorted(found, key=lambda p: p.stat().st_mtime, reverse=True))

    def panelled(self, width: int) -> bool:
        return width >= PANEL_WIDTH and self.tab != BACKLOG and not self.open

    def list_width(self, width: int) -> int:
        return width * LIST_SHARE // 100

    def viewed(self, width: int) -> list[Line]:
        try:
            return document(self.viewing, Path(self.viewing).read_text(encoding="utf-8", errors="replace"), width)
        except OSError as failed:
            return document(self.viewing, str(failed), width)

    def stop_report(self, run: Run) -> tuple[str, str]:
        """What the stopped step wrote, so the reason for stopping needs no second look."""
        if (run.status not in ("parked", "waiting") and run.landed != "dropped") or run.current is None:
            return "", ""
        step = run.current
        if step["kind"] == "gate":  # what you're deciding on is the report it names
            step = run.pipeline["steps"][run.index_of(step["report"])]
        name = files_name(step)
        path = self.store.files(run) / name
        return (name, path.read_text(encoding="utf-8", errors="replace")) if path.exists() else ("", "")

    def detail(self, ref: tuple[str, str] | None, width: int) -> list[Line]:
        if pr := next((p for p in self.snapshot.get("prs", {}).get("items", []) if ("#pr", p["url"]) == ref), None):
            return pull(pr, width)
        run = next((r for r in self.runs if (r.repo, r.key) == ref), None)
        if run is None:
            return []
        action = self.engine.next_action(run) if run.status in ("parked", "waiting") else ""
        events = [e for e in self.events if (e["repo"], e["key"]) == ref]
        return drill(run, events, self.title(run), action, str(self.store.files(run)), width, self.reports(run), self.stop_report(run), self.earlier, self.engine.handover(run) if run.status == "done" else "", self.brief(run))

    def panel(self, width: int) -> list[Line]:
        """The selected item beside the list. Its actions are on the bottom row, as everywhere."""
        size = width - self.list_width(width) - 3
        lines = self.lines(width)
        if self.viewing:
            return [Line([Span(" esc close ", "button", f"key:{ESCAPE}")]), plain(), *self.viewed(size)]
        return self.detail(self.selected(lines), size)

    def lines(self, width: int) -> list[Line]:
        if self.panelled(width):
            return self.listing(self.list_width(width))
        if self.viewing:
            return self.viewed(width)
        if self.open:
            return self.detail(self.open, width)
        return self.listing(width)

    def listing(self, width: int) -> list[Line]:
        if self.tab == DELIVERY:
            return deliveries(self.runs, self.events, self.title, width, self.engine.throttle.pace(), self.meter.read())
        return backlog(self.snapshot, self.skipped, self.store.exists, width) if self.tab == BACKLOG else pulls(self.snapshot, width)

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
            self.viewing, self.side, self.earlier = "", 0, False
        else:
            self.offset = max(0, self.offset + by)

    def press(self, key: int, width: int = 120) -> bool:
        """Handles one key. False means leave."""
        if key == curses.ERR:
            return True  # the timeout that redraws, so a message stays until a real key
        if self.asking:
            self.type_key(key)
            return True
        lines = self.lines(width)
        self.message = ""
        if (run := self.target(lines)) and key in self.run_actions(run):
            self.begin_note(self.answer(run) if key == ANSWER else Ask(run.key, f"{run.key}: drop this run", "drop", "drop", ("enter drops it", "or type why first")))
            return True
        if key == AI_REVIEW and (pr := self.answerable(lines)):
            self.send("reply", pr)
            return True
        if key == AI_REVIEW and (pr := self.unreviewed(lines)):
            self.send("pr", pr)
            return True
        if key == AI_REVIEW_OTHER and (pr := self.unreviewed(lines)):
            self.send("pr", pr, f"--{self.other_mode()}")
            return True
        if key == CLAIM and (found := self.claimable(lines)):
            self.send("claim", found.key)
            return True
        if key == PREPARE and self.tab == BACKLOG and not self.open:
            self.begin_note(Ask("", "Prepare", "", "prepare", ("type a ticket number, or an idea to make into a ticket",)))
            return True
        if key in (curses.KEY_UP, ord("k")):
            self.move(-1, lines)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.move(1, lines)
        elif key in ENTERS:
            self.enter(self.selected(lines))
        elif key == ESCAPE and (self.open or self.viewing):
            self.back()
        elif key == ord("q"):
            if not self.open and not self.viewing:
                return False
            self.back()
        elif key == ord("e"):
            self.earlier = not self.earlier
        elif key in (curses.KEY_NPAGE, curses.KEY_PPAGE):
            self.scroll(PAGE if key == curses.KEY_NPAGE else -PAGE, width)
        elif key in (TAB_KEY, curses.KEY_BTAB) and not self.open:
            self.tab, self.viewing, self.offset, self.side = (self.tab + (1 if key == TAB_KEY else -1)) % len(TABS), "", 0, 0
        elif key == ord("r"):
            self.refresh()
        elif key == ord("g"):
            self.jump(self.open or self.selected(lines))
        return True

    def enter(self, ref: tuple[str, str] | None) -> None:
        if ref and ref[0] == "#file":
            self.viewing, self.offset, self.side = ref[1], 0, 0
        elif ref and not self.open:
            self.picked, self.open, self.offset = ref, ref, 0

    def back(self) -> None:
        if self.viewing:
            self.viewing = ""
        else:
            self.open = None
        self.offset, self.side = 0, 0

    def scroll(self, by: int, width: int) -> None:
        if self.panelled(width):
            self.side = max(0, self.side + by)
        else:
            self.offset = max(0, self.offset + by)

    def target(self, lines: list[Line]) -> Run | None:
        wanted = self.open or self.selected(lines)
        return next((r for r in self.runs if (r.repo, r.key) == wanted), None)

    def unreviewed(self, lines: list[Line]) -> str:
        """The selected PR, if it can go to an AI review: an open PR with no run yet, or something new since the last one."""
        wanted = self.open or self.selected(lines)
        pr = next((p for p in self.snapshot.get("prs", {}).get("items", []) if ("#pr", p["url"]) == wanted), None)
        if pr is None or pr["move"] == "done":
            return ""
        run = next((r for r in self.runs if r.pr == pr["url"]), None)
        return "" if pr["mine"] and pr["state"] in RESPOND else pr["url"] if run is None or (self.engine.review_ready(run) and pr["state"] in RENEWED) else ""

    def answerable(self, lines: list[Line]) -> str:
        """The selected PR, if it's yours with comments an agent could answer."""
        wanted = self.open or self.selected(lines)
        pr = next((p for p in self.snapshot.get("prs", {}).get("items", []) if ("#pr", p["url"]) == wanted), None)
        if pr is None or not pr["mine"] or pr["state"] not in RESPOND:
            return ""
        run = next((r for r in self.runs if r.replying and r.pr == pr["url"]), None)
        return pr["url"] if run is None or self.engine.review_ready(run) else ""

    def other_mode(self) -> str:
        return "silent" if self.cfg.get("review", {}).get("mode") == "claimed" else "claimed"

    def claimable(self, lines: list[Line]) -> Run | None:
        wanted = self.open or self.selected(lines)
        run = next((r for r in self.runs if wanted in ((r.repo, r.key), ("#pr", r.pr))), None)
        return run if run and run.reviewing and not run.claimed and not run.landed else None

    def run_actions(self, run: Run | None) -> dict[int, str]:
        """The same two keys wherever a run stops. The box says what an answer does there."""
        if run is not None and run.your_turn:
            return {DROP: "d drop"}
        if run is None or run.status not in ("parked", "waiting"):
            return {}
        return {ANSWER: "a answer", DROP: "d drop"}

    def answer(self, run: Run) -> Ask:
        go, change = self.engine.choices(run)
        gate = run.status == "waiting" and bool(run.current) and run.current["kind"] == "gate"
        return Ask(run.key, f"{run.key}: answer", ("approve" if gate else "retry") if go else "", "reject" if gate else "retry", (f"enter to {go}", f"or type {change}") if go else (f"type {change}",))

    def begin_note(self, asking: Ask) -> None:
        self.asking, self.typed = asking, self.drafts.get(asking.key + asking.blank + asking.words, "")
        self.cursor = len(self.typed)

    def insert(self, text: str) -> None:
        self.typed = self.typed[: self.cursor] + text + self.typed[self.cursor :]
        self.cursor += len(text)

    def cut(self, start: int, end: int) -> None:
        self.typed = self.typed[:start] + self.typed[end:]
        self.cursor = start

    def type_key(self, key: int) -> None:
        asking = self.asking
        if asking is None:
            return
        if key == ESCAPE:
            self.drafts[asking.key + asking.blank + asking.words] = self.typed
            self.asking = None
        elif key in ENTERS:
            words = self.typed.strip()
            command = asking.words if words else asking.blank
            # While another command runs, the words stay put for a second enter.
            if command and not self.refreshing:
                self.asking = None
                self.drafts.pop(asking.key + asking.blank + asking.words, None)
                self.send(*filter(None, (command, asking.key, words)))
        elif key in BACKSPACES:
            self.cut(max(self.cursor - 1, 0), self.cursor)
        elif key == curses.KEY_DC:
            self.cut(self.cursor, self.cursor + 1)
        elif key == CTRL_U:
            self.cut(0, self.cursor)
        elif key == CTRL_W:
            self.cut(len(re.sub(r"\S*\s*$", "", self.typed[: self.cursor])), self.cursor)
        elif key == curses.KEY_LEFT:
            self.cursor = max(self.cursor - 1, 0)
        elif key == curses.KEY_RIGHT:
            self.cursor = min(self.cursor + 1, len(self.typed))
        elif key == curses.KEY_HOME:
            self.cursor = 0
        elif key == curses.KEY_END:
            self.cursor = len(self.typed)
        elif 32 <= key < curses.KEY_MIN and key != 127:
            self.insert(chr(key))

    def paste(self, screen: Keys) -> int:
        """Reads what follows an escape. A paste goes in whole, and anything else is an escape, plus keys to handle next."""
        screen.timeout(0)
        seen = bytearray()
        while len(seen) < len(PASTE_START) and (key := screen.getch()) != curses.ERR:
            seen.append(key & 0xFF)
        if bytes(seen) != PASTE_START:
            screen.timeout(1000)
            for key in reversed(seen):
                curses.ungetch(key)
            return ESCAPE
        pasted = bytearray()
        while not pasted.endswith(PASTE_END) and len(pasted) < PASTE_LIMIT:
            if (key := screen.getch()) != curses.ERR and 0 <= key < 256:
                pasted.append(key)
        screen.timeout(1000)
        self.insert(pasted.removesuffix(PASTE_END).decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n"))
        return curses.ERR

    def send(self, *args: str) -> None:
        """Commands can take a while on GitHub or Jira, so the board keeps drawing."""
        if self.refreshing:
            return
        self.refreshing = True

        def work() -> None:
            try:
                self.engine.tools.workshop(*args)
            except STOPS as failed:
                self.say(str(failed))
            self.refreshing = False

        threading.Thread(target=work, daemon=True).start()

    def say(self, text: str) -> None:
        self.message, self.said = text, self.clock()

    def jump(self, target: tuple[str, str] | None) -> None:
        run = next((r for r in self.runs if target in ((r.repo, r.key), ("#pr", r.pr))), None)
        try:
            if run is None or not run.pane:
                raise KeyError("no workspace")
            self.engine.tools.focus(run.pane)
            if self.engine.review_ready(run):
                self.send("review", run.key)  # hunk may have been closed since
        except STOPS:
            self.say("That run has no workspace now.")

    def refresh(self) -> None:
        self.send("refresh")

    def counts(self) -> tuple[int, ...]:
        waiting = sum(r.status in ("parked", "waiting") or r.your_turn for r in self.runs)
        tickets = sum(not self.store.exists(repo, i["key"]) for repo, r in self.snapshot.get("repos", {}).items() for i in r["items"])
        return tickets, waiting, sum(p["move"] == "you" for p in self.snapshot.get("prs", {}).get("items", []))

    def header(self) -> Line:
        tabs = [part for i, (tab, count) in enumerate(zip(TABS, self.counts(), strict=True)) for part in (Span(f" {tab} {count} ", "tab-on" if i == self.tab and not self.open else "tab", f"tab:{i}"), Span(" "))]
        kind = "PRs" if self.tab == REVIEW else "backlog"
        sources = [self.snapshot.get("prs", {})] if self.tab == REVIEW else self.snapshot.get("repos", {}).values()
        times = [source.get("polled_at", 0) for source in sources]
        age = f"{kind} {ago(self.clock() - min(times))} old" if times and min(times) else f"{kind} not polled"
        held = self.engine.throttle.hold_new(len([r for r in self.runs if r.status == "running"]))
        return Line([*tabs, Span(f" {age}", "dim"), Span(f"   held: {held}" if held else "", "yellow")])

    def footer(self, width: int = 120) -> Line:
        if self.refreshing:
            return Line([Span("Working...", "dim")])
        if self.message and self.clock() - self.said < MESSAGE_SECONDS:
            return Line([Span(self.message, "yellow")])
        moves = [("esc back", ESCAPE)] if self.open else [("tab switch", TAB_KEY), ("enter open", 10)]
        lines = self.lines(width)
        run = self.target(lines)
        stopped = moves[:1] * bool(self.open) + [(label, key) for key, label in self.run_actions(run).items()]
        offers = [("a ai review", AI_REVIEW), (f"A ai review, {self.other_mode()}", AI_REVIEW_OTHER)] if self.unreviewed(lines) else []
        offers = [("a ai reply", AI_REVIEW)] if self.answerable(lines) else offers
        offers += [("m claim", CLAIM)] * bool(self.claimable(lines))
        offers += [("p prepare", PREPARE)] * (self.tab == BACKLOG and not self.open)
        common = [("g review" if run and self.engine.review_ready(run) else "g workspace", ord("g")), ("r refresh", ord("r"))] + ([] if self.open else [("q quit", ord("q"))])
        return Line([part for label, key in (stopped or moves) + offers + common for part in (Span(f" {label} ", "button", f"key:{key}"), Span(" "))])

    def note(self, width: int) -> list[Line]:
        """Grows upward as you type, so what you're answering stays in view."""
        if self.asking is None:
            return [self.footer(width)]
        room = max(width - 2, 20)
        marked = self.typed[: self.cursor] + CURSOR + self.typed[self.cursor :]
        rows = [part for line in marked.split("\n") for part in textwrap.wrap(clean(line), room, drop_whitespace=False, replace_whitespace=False, break_on_hyphens=False) or [""]]
        here = next(i for i, row in enumerate(rows) if CURSOR in row)
        first = max(0, min(here - NOTE_ROWS + 1, len(rows) - NOTE_ROWS))
        return [rule(cell(self.asking.label, width - 6).rstrip(), width), *(plain(f" {row}") for row in rows[first : first + NOTE_ROWS]), *(plain(f" {hint}", "dim") for hint in self.asking.hints), plain(" esc keeps a draft", "dim")]

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
                if self.panelled(width) and action[0] != "#file":
                    self.picked, self.viewing, self.side, self.earlier = action, "", 0, False  # the panel shows it, so there's nothing to open
                else:
                    self.enter(action)
                return True
            kind, _, value = action.partition(":")
            if kind == "tab":
                self.tab, self.open, self.viewing, self.offset, self.side = int(value), None, "", 0, 0
                return True
            return self.press(int(value), width)
        return True

    def mouse(self, x: int, y: int, state: int, width: int) -> bool:
        if state & (curses.BUTTON4_PRESSED | curses.BUTTON5_PRESSED):
            by = -WHEEL if state & curses.BUTTON4_PRESSED else WHEEL
            if self.panelled(width) and x > self.list_width(width):
                self.scroll(by, width)
            else:
                self.wheel(by, width)
        elif state & curses.BUTTON1_PRESSED:
            self.grab, self.reach = (x, y), None
        elif state & curses.REPORT_MOUSE_POSITION and self.grab:
            self.reach = (x, y)
        elif state & curses.BUTTON1_RELEASED and self.grab:
            start, self.grab, self.reach = self.grab, None, None
            if y == start[1] and abs(x - start[0]) < JITTER:
                return self.click(start[0], start[1], width)
            self.copy_selection(start, (x, y))
        return True

    def selection(self, start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int, int]]:
        """Rows as (y, from, to), held to the column the drag began in, so the list and panel never mix."""
        left, right = (0, self.edge or self.right) if not self.edge or start[0] < self.edge else (self.edge + 2, self.right)
        first, last = TOP + 2, self.rows - 2
        (top, x1), (bottom, x2) = sorted(((min(max(y, first), last), max(min(x, right - 1), left)) for x, y in (start, end)))
        return [(y, x1 if y == top else left, x2 + 1 if y == bottom else right) for y in range(top, bottom + 1)]

    def row_text(self, y: int, left: int, right: int) -> str:
        cells = [" "] * right
        for at, text in self.painted.get(y, []):
            for letter in text:
                wide = columns(letter)
                if wide and left <= at < right:
                    cells[at] = letter
                    if wide == 2 and at + 1 < right:
                        cells[at + 1] = ""  # the letter fills both cells
                elif not wide and left < at <= right:
                    cells[at - 1] += letter
                at += wide
        return "".join(cells[left:right]).rstrip()

    def copy_selection(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        text = "\n".join(self.row_text(*row) for row in self.selection(start, end)).strip("\n")
        if not text:
            return
        try:
            self.engine.tools.copy(text)
            self.say(f"Copied {len(text)} characters.")
        except STOPS as failed:
            self.say(str(failed))

    def window(self, lines: list[Line], height: int) -> list[Line]:
        chosen = self.selected(lines)
        at = next((i for i, line in enumerate(lines) if line.ref and line.ref == chosen), None)
        if at is not None:
            self.offset = max(min(self.offset, at), at - height + 1)
        self.offset = max(0, min(self.offset, len(lines) - height))
        return lines[self.offset : self.offset + height]

    def put(self, screen: curses.window, y: int, line: Line, styles: dict[str, int], width: int, selected: bool = False, left: int = 0) -> None:
        x = left
        for span in line.spans:
            text = clean(span.text)
            if x >= width - 1:
                break
            attr = styles.get(f"row-{span.style}", styles[span.style]) if selected else styles[span.style]
            screen.addnstr(y, x, text, width - 1 - x, attr)
            self.painted.setdefault(y, []).append((x, text))
            if span.action:
                self.regions.append((y, x, x + columns(text), span.action))
            x += columns(text)
        if selected and x < width - 1:
            screen.addnstr(y, x, cell("", width - 1 - x), width - 1 - x, styles["row-"])
        if line.ref:
            self.regions.append((y, left, width, line.ref))

    def draw(self, screen: curses.window, styles: dict[str, int]) -> None:
        rows, width = screen.getmaxyx()
        lines = self.lines(width - 1)
        chosen = self.selected(lines)
        self.regions, self.painted = [], {}
        self.right, self.rows = width - 1, rows
        screen.erase()
        self.put(screen, TOP, self.header(), styles, width)
        foot = self.note(width - 1)
        height = rows - TOP - 2 - len(foot)
        panelled = self.panelled(width - 1)
        edge = self.list_width(width - 1) + 1 if panelled else width
        self.edge = edge if panelled else 0
        for y, line in enumerate(self.window(lines, height), TOP + 2):
            self.put(screen, y, line, styles, edge, bool(line.ref) and line.ref == chosen)
        if panelled:
            side = self.panel(width - 1)
            self.side = max(0, min(self.side, len(side) - height))
            for y in range(TOP + 2, TOP + 2 + height):
                screen.addnstr(y, edge, "│", 1, styles["dim"])
            for y, line in enumerate(side[self.side : self.side + height], TOP + 2):
                self.put(screen, y, line, styles, width, left=edge + 2)
        for y, line in enumerate(foot, rows - len(foot)):
            self.put(screen, y, line, styles, width)
        if self.grab and self.reach:
            for y, left, right in self.selection(self.grab, self.reach):
                screen.chgat(y, left, max(right - left, 0), curses.A_REVERSE)
        screen.refresh()

    def colours(self) -> dict[str, int]:
        """The terminal's own colours as curses styles, so the board follows its scheme. Default text stays the terminal's, so it's readable whatever the background."""
        curses.use_default_colors()
        grey, red = (8, 9) if curses.COLORS >= 16 else (0, 1)  # bright colours fall back to their base hue
        pairs = iter(range(1, 64))

        def pair(front: int, behind: int) -> int:
            number = next(pairs)
            curses.init_pair(number, front, behind)
            return curses.color_pair(number)

        styles = {"": 0, "bold": curses.A_BOLD, "row-": pair(-1, grey), "row-bold": pair(-1, grey) | curses.A_BOLD, "button": pair(-1, grey)}
        for style, hue in {"red": red, "yellow": 3, "green": 2, "mine": 4, "others": 6, "dim": -1}.items():
            styles[style], styles[f"row-{style}"] = pair(hue, -1), pair(hue, grey)
            if style != "dim":
                styles[f"on-{style}"] = pair(0, hue)
        styles["tab"] = styles["on-dim"] = styles["button"]
        styles["tab-on"] = styles["on-mine"]  # bold black turns grey where bold means bright, as in Windows Terminal
        styles["row-dim"] = styles["row-"]  # faint text on the selection background is unreadable
        styles["dim"] |= curses.A_DIM
        return styles

    def loop(self, screen: curses.window) -> None:
        curses.curs_set(0)
        styles = self.colours()
        curses.mousemask(CLICKS)
        curses.mouseinterval(0)
        sys.stdout.write(DRAGS_ON + PASTES_ON)  # curses' own mouse mode reports presses, not movement
        sys.stdout.flush()
        screen.timeout(1000)
        while True:
            self.reload()
            self.draw(screen, styles)
            key, width = screen.getch(), screen.getmaxyx()[1]
            if key == ESCAPE and self.asking:
                key = self.paste(screen)
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
    try:
        curses.wrapper(Board(engine, cfg).loop)
    finally:
        sys.stdout.write(DRAGS_OFF + PASTES_OFF)
        sys.stdout.flush()
