"""The board: what's with agents, what's with you and what's coming. It reads local files only, so opening it costs no tokens or API calls."""

import curses
import locale
import re
import sys
import textwrap
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from .backlog import SNAPSHOT
from .boardview import ASK_AGAIN, RESPOND, SIGNS, Line, Span, Work, ago, backlog, cell, clean, columns, deliveries, document, drill, files_name, full_pile, now_view, only, operations, plain, pr_rows, pull, pull_story, pulls, refs, rule, run_for, ticket_view, timeline, usage_line, usage_view, waiting, wrapped
from .engine import STOPS, SWEEP_EVERY, SWEEPS, Engine
from .prs import FAILING
from .review import decisions
from .store import Run
from .system import Meter
from .throttle import DEFAULTS
from .usage import samples

SKIPPED = "skipped.json"
TABS = ("Now", "Backlog", "Delivery", "Review", "Operate", "Usage")  # what to do, the order work moves through, then how it went
NOW, BACKLOG, DELIVERY, REVIEW, OPERATE, USAGE = range(6)
TOP = 1  # a gap under herdr's tab bar
WHEEL = 3
MESSAGE_SECONDS = 5
CLICKS = curses.BUTTON1_PRESSED | curses.BUTTON1_RELEASED | curses.BUTTON4_PRESSED | curses.BUTTON5_PRESSED | curses.REPORT_MOUSE_POSITION
DRAGS_ON, DRAGS_OFF = "\033[?1002h", "\033[?1002l"
PASTES_ON, PASTES_OFF = "\033[?2004h", "\033[?2004l"  # marks a paste, so a line break in one isn't enter
BACK_OFF_SECONDS = 600  # each poll takes from GitHub's hourly limit, so a used up one is left alone
PASTE_START, PASTE_END = b"[200~", b"\033[201~"
PASTE_LIMIT = 100_000
NOTE_ROWS = 6
BRIEF = ("What", "Why", "Background", "Bottom line")
BRIEF_ROWS = 2  # a line that runs longer is cut, as the report above has it in full
CURSOR = "▌"
CTRL_U, CTRL_W = 21, 23
JITTER = 2  # a press that moves less than this along a row is still a click
PAGE = 10
PANEL_WIDTH = 130  # below this a window can't hold a list and a detail side by side
LIST_SHARE = 55  # percent of the window for the list
ESCAPE = 27
TAB_KEY = ord("\t")
AI_REVIEW = ord("a")  # with nothing stopped under it, where the same key answers
AI_REVIEW_OTHER = ord("A")  # the mode you don't usually use
CLAIM = ord("m")
PREPARE = ord("p")
FIND = ord("/")
TALK = "talk"
CHOOSE_REPO = "choose-repo"  # stays on the board: its answer leads to the next question
RENEWED = ("re-review", "new commits")
ENTERS = (10, 13, curses.KEY_ENTER)
BACKSPACES = (8, 127, curses.KEY_BACKSPACE)
Action = str | tuple[str, str]


class Keys(Protocol):
    def getch(self) -> int: ...
    def timeout(self, delay: int) -> None: ...


class Ask(NamedTuple):
    """A question with what you need to answer it, the options, and room to say something else. Everything stays together, at the bottom."""

    key: str
    question: str
    context: tuple[str, ...]
    options: tuple[tuple[str, str, str], ...]  # label, command and words
    typed: str  # what writing your own answer means
    command: str  # and what it runs
    within: str = ""  # the repo already chosen, which the command needs in front of your words


ANSWER = ord("a")
DROP = ord("d")


class Board:
    def __init__(self, engine: Engine, cfg: dict[str, Any], clock: Callable[[], float] = time.time) -> None:
        self.engine, self.store, self.cfg, self.clock = engine, engine.store, cfg, clock
        self.meter = Meter()
        self.caught = 0.0
        self.tab, self.offset = NOW, 0
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
        self.choice = 0  # which option the card has selected. Past the last is your own words
        self.follow = ""  # the run just answered, to carry on with once its command is done
        self.series = False  # several decisions, answered one card at a time
        self.queue: list[Ask] = []
        self.given: list[str] = []
        self.paused: dict[str, tuple[Ask, list[Ask], list[str]]] = {}
        self.failed = False  # a failure stays until a key, so it can be read
        self.drafts: dict[tuple[str, str], str] = {}  # what esc left, for when you come back
        self.regions: list[tuple[int, int, int, Action]] = []
        self.finding, self.filter = False, ""  # typing a filter, and what the list is narrowed to
        self.reload()
        self.looked = self.piles()  # what each tab held when you last looked, so what's arrived since gets a dot

    def stamp(self) -> tuple[int, ...]:
        paths = (self.store.root / "runs", self.store.root / "events.jsonl", self.store.root / SNAPSHOT, self.store.root / SKIPPED)
        return tuple(p.stat().st_mtime_ns if p.exists() else 0 for p in paths)

    def reload(self) -> None:
        if (stamp := self.stamp()) != self.seen:
            self.seen = stamp
            waiting = self.picked and self.picked in self.your_pile()
            self.runs, self.events = self.store.all(), self.store.history()
            self.snapshot, self.skipped = self.store.read(SNAPSHOT), self.store.read(SKIPPED)
            if waiting and self.picked not in self.your_pile():  # dealt with, so the next one is ready
                self.picked, self.viewing, self.side, self.earlier = None, "", 0, False

    def your_pile(self) -> set[tuple[str, str]]:
        return {found.ref for found in self.waiting()}

    def waiting(self) -> list[Any]:
        return waiting(self.runs, self.snapshot.get("prs", {}).get("items", []), self.engine.reviewable, self.events)

    def piles(self) -> list[set[tuple[str, str]]]:
        """What waits on you, tab by tab, for the dot that says something new arrived."""
        found = self.waiting()
        return [{w.ref for w in found if TABS[tab] in ("Now", w.tab)} for tab in range(len(TABS))]

    def title(self, run: Run) -> str:
        brief = self.store.files(run) / "brief.md"
        first = brief.read_text(encoding="utf-8").partition("\n")[0] if brief.exists() else ""
        return first.removeprefix("# ").removeprefix(f"{run.key}: ")

    def brief(self, run: Run) -> str:
        path = self.store.files(run) / "brief.md"
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    def reports(self, run: Run) -> tuple[str, ...]:
        found = [p for p in self.store.files(run).iterdir() if p.suffix in (".md", ".json") and not p.name.startswith("prompt-")]
        return tuple(str(p) for p in sorted(found, key=lambda p: p.stat().st_mtime, reverse=True))

    def panelled(self, width: int) -> bool:
        return width >= PANEL_WIDTH and self.tab in (NOW, DELIVERY, REVIEW, OPERATE) and not self.open

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
        if step.get("proposals"):
            return "proposals.json", self.engine.proposal(run)
        if step["kind"] == "gate":  # what you're deciding on is the report it names
            step = run.pipeline["steps"][run.index_of(step["report"])]
        name = files_name(step)
        path = self.store.files(run) / name
        return (name, path.read_text(encoding="utf-8", errors="replace")) if path.exists() else ("", "")

    def ticket(self, ref: tuple[str, str] | None) -> dict[str, Any] | None:
        if not ref or ref[0] != "#ticket":
            return None
        repo, _, key = ref[1].partition("/")
        return next((i for i in self.snapshot.get("repos", {}).get(repo, {}).get("items", []) if i["key"] == key), None)

    def detail(self, ref: tuple[str, str] | None, width: int) -> list[Line]:
        if (item := self.ticket(ref)) and ref:
            return ticket_view(ref[1], item, self.skipped, width)
        if pr := next((p for p in self.pr_items() if ("#pr", p["url"]) == ref), None):
            return pull(pr, width, self.pull_context(pr, width), self.linked(pr))
        run = next((r for r in self.runs if (r.repo, r.key) == ref), None)
        if run is None:
            return []
        events = [e for e in self.events if (e["repo"], e["key"]) == ref]
        return drill(run, events, self.title(run), "", str(self.store.files(run)), width, self.reports(run), self.stop_report(run), self.earlier, self.engine.handover(run) if run.status == "done" else "", self.brief(run))

    def work(self) -> Work:
        limits = DEFAULTS | self.cfg.get("throttle", {})
        return tuple(limits["work_hours"]), limits["work_days"]

    def ci(self, url: str) -> str:
        """A run's PR checks, drafts included, from the last poll."""
        return next((p["checks"] for p in self.snapshot.get("prs", {}).get("items", []) if p["url"] == url), "") if url else ""

    def building(self) -> list[Run]:
        """Delivery holds a ticket until its PR is opened to your team. From then the PR is on Review, so it isn't shown twice."""
        return [r for r in self.runs if not r.on_a_pr and not r.operating and not r.opened]

    def schedule(self) -> list[dict[str, Any]]:
        seen = self.store.read(SWEEPS)
        return [{"repo": name, "sweep": sweep, "cadence": cadence, "every": SWEEP_EVERY.get(cadence, 0), "last": seen.get(f"{name}/{sweep}", {}).get("last", 0)} for name, repo in self.cfg["repos"].items() for sweep, cadence in repo.get("sweeps", {}).items()]

    def pr_items(self) -> list[dict[str, Any]]:
        return pr_rows(self.runs, self.snapshot.get("prs", {}).get("items", []), self.engine.reviewable)

    def run_of(self, ref: tuple[str, str] | None) -> Run | None:
        return run_for(self.runs, ref)

    def advice(self, run: Run | None) -> str:
        """The agent's bottom line, for the top of the detail."""
        if run is None or not run.with_you:
            return ""
        said = [line.strip(" -*") for line in self.engine.handover(run).splitlines()]
        word = next((line.removeprefix("Recommendation:").split(". ")[0].strip(" .") for line in said if line.startswith("Recommendation:")), "")
        notes = next((e["notes"] for e in reversed(self.events) if (e["repo"], e["key"], e["type"]) == (run.repo, run.key, "review")), None)
        return ", ".join(part for part in (f"AI: {word}" if word else "", "" if notes is None else f"{notes} note(s)") if part)

    def linked(self, pr: dict[str, Any]) -> Run | None:
        return self.run_of(("#pr", pr["url"]))

    def pull_context(self, pr: dict[str, Any], width: int) -> list[Line]:
        run = self.linked(pr)
        if run is None:
            return pull_story(pr, None, "", "", "", [], width)
        events = [e for e in self.events if (e["repo"], e["key"]) == (run.repo, run.key)]
        stopped = self.stop_report(run)
        return pull_story(pr, run, self.advice(run), "" if run.replied else self.engine.handover(run), stopped[1], timeline(run, events, self.reports(run), width, ("", "") if run.current and run.current["kind"] == "gate" else stopped, self.earlier), width, self.engine.replies(run) if run.replying and run.with_you else [])

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

    def queued(self) -> list[tuple[str, str]]:
        found = [(repo, i) for repo, r in self.snapshot.get("repos", {}).items() for i in r["items"] if i["column"] == "queued" and not self.store.exists(repo, i["key"])]
        return [(f"{repo}/{i['key']}", i["title"]) for repo, i in sorted(found, key=lambda pair: self.engine.queue_place(pair[0], pair[1]["key"]))]

    def listing(self, width: int) -> list[Line]:
        if self.tab == USAGE:
            return usage_view(samples(self.store.root), self.events, self.meter.read(), self.clock(), width)
        if self.tab == BACKLOG:
            return backlog(self.snapshot, self.skipped, self.store.exists, width, self.engine.queue_place, self.filter)
        now, work = self.clock(), self.work()
        if self.tab == NOW:
            lines = now_view(self.runs, self.pr_items(), self.linked, self.title, self.queued(), self.events, now, width, self.ci, work)
            return [*self.repo_chips(), *(only(lines, self.filter) if self.filter else lines)]
        elif self.tab == DELIVERY:
            lines = deliveries(self.building(), self.events, self.title, width, now, self.ci, work)
        elif self.tab == OPERATE:
            lines = operations([r for r in self.runs if r.operating], self.events, self.title, self.schedule(), now, width, self.ci, work)
        else:
            lines = pulls({"prs": {**self.snapshot["prs"], "items": self.pr_items()}} if "prs" in self.snapshot else {}, width, self.linked, now, work)
        return only(lines, self.filter) if self.filter else lines

    def choices(self, lines: list[Line]) -> list[tuple[str, str]]:
        """Down a column, then on to the next, so Backlog cards go in the order they're read."""
        found = sorted((x, y, ref) for y, line in enumerate(lines) for x, ref in refs(line))
        return list(dict.fromkeys(ref for _, _, ref in found))

    def selected(self, lines: list[Line]) -> tuple[str, str] | None:
        choices = self.choices(lines)
        return self.picked if self.picked in choices else choices[0] if choices else None

    def grid(self, across: int, down: int, lines: list[Line]) -> None:
        """Backlog cards sit in columns: up and down within one, left and right to the same place in the next."""
        cards: dict[tuple[str, str], tuple[int, int]] = {}
        for y, line in enumerate(lines):
            for x, ref in refs(line):
                cards.setdefault(ref, (x, y))
        if not cards:
            return
        here = cards.get(self.selected(lines) or ("", ""), min(cards.values()))
        edges = sorted({x for x, _ in cards.values()})
        stack = [ref for ref, _ in sorted(cards.items(), key=lambda found: found[1][1]) if cards[ref][0] == here[0]]
        place = next((n for n, ref in enumerate(stack) if cards[ref] == here), 0)
        column = edges[max(0, min(edges.index(here[0]) + across, len(edges) - 1))]
        stack = [ref for ref, _ in sorted(cards.items(), key=lambda found: found[1][1]) if cards[ref][0] == column] or stack
        self.picked = stack[max(0, min(place + down, len(stack) - 1))]
        self.viewing, self.side, self.earlier = "", 0, False

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
        if self.finding:
            self.find_key(key)
            return True
        lines = self.lines(width)
        self.message, self.failed = "", False
        if (run := self.target(lines)) and key in self.run_actions(run):
            if key == ANSWER:
                self.begin_series(run)
            else:
                self.begin_note(Ask(run.key, f"Drop {run.key}?", (), (("Drop it", "drop", ""),), "Drop it, and say why", "drop"))
            return True
        if key == AI_REVIEW and (pr := self.askable(lines)):
            self.send("rerequest", pr)
            return True
        if key == AI_REVIEW and (pr := self.fixable(lines)):
            self.send("fix-ci", pr)
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
        if key == PREPARE and self.tab == BACKLOG:
            self.ask_to_prepare(self.open or self.selected(lines))
            return True
        if key == FIND and self.tab != USAGE and not self.open:
            self.finding, self.picked = True, None
            return True
        if key in (curses.KEY_UP, ord("k"), curses.KEY_DOWN, ord("j")) and self.tab == BACKLOG and not self.open:
            self.grid(0, -1 if key in (curses.KEY_UP, ord("k")) else 1, lines)
        elif key in (curses.KEY_LEFT, ord("h"), curses.KEY_RIGHT, ord("l")) and self.tab == BACKLOG and not self.open:
            self.grid(-1 if key in (curses.KEY_LEFT, ord("h")) else 1, 0, lines)
        elif key in (curses.KEY_UP, ord("k")):
            self.move(-1, lines)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.move(1, lines)
        elif key in ENTERS:
            self.enter(self.selected(lines))
        elif key == ESCAPE and (self.open or self.viewing):
            self.back()
        elif key == ESCAPE and self.filter:
            self.filter = ""
        elif key == ord("q"):
            if not self.open and not self.viewing:
                return False
            self.back()
        elif key == ord("e"):
            self.earlier = not self.earlier
        elif key in (curses.KEY_NPAGE, curses.KEY_PPAGE):
            self.scroll(PAGE if key == curses.KEY_NPAGE else -PAGE, width)
        elif key in (TAB_KEY, curses.KEY_BTAB) and not self.open:
            self.tab, self.viewing, self.offset, self.side, self.filter = (self.tab + (1 if key == TAB_KEY else -1)) % len(TABS), "", 0, 0, ""
        elif key == ord("r"):
            self.refresh()
        elif key == ord("g"):
            self.jump(self.open or self.selected(lines))
        elif key == ord("o") and (url := self.link(lines)):
            self.open_link(url)
        return True

    def link(self, lines: list[Line]) -> str:
        ref = self.open or self.selected(lines)
        if item := self.ticket(ref):
            return item["url"]
        return ref[1] if ref and ref[0] == "#pr" else run.pr if (run := self.target(lines)) else ""

    def find_key(self, key: int) -> None:
        """Narrows the list as you type. Enter keeps it, esc drops it."""
        if key == ESCAPE:
            self.finding, self.filter = False, ""
        elif key in ENTERS:
            self.finding = False
        elif key in BACKSPACES:
            self.filter = self.filter[:-1]
        elif 32 <= key < curses.KEY_MIN and key != 127:
            self.filter += chr(key)

    def open_link(self, url: str) -> None:
        try:
            self.say(self.engine.tools.open_link(url) or f"Opened {url}")
        except (ToolError, OSError) as failed:
            self.say(str(failed))

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
        return self.run_of(self.open or self.selected(lines))

    def unreviewed(self, lines: list[Line]) -> str:
        """The selected PR, if it can go to an AI review: an open PR with no run yet, or something new since the last one."""
        wanted = self.open or self.selected(lines)
        pr = next((p for p in self.snapshot.get("prs", {}).get("items", []) if ("#pr", p["url"]) == wanted), None)
        if pr is None or pr["move"] == "done":
            return ""
        run = next((r for r in self.runs if r.pr == pr["url"]), None)
        return "" if pr["mine"] else pr["url"] if run is None or (self.engine.review_ready(run) and pr["state"] in RENEWED) else ""

    def askable(self, lines: list[Line]) -> str:
        """The selected PR, if yours is fixed and only needs the reviewer back."""
        wanted = self.open or self.selected(lines)
        pr = next((p for p in self.pr_items() if ("#pr", p["url"]) == wanted), None)
        return pr["url"] if pr and pr["mine"] and pr["state"] == ASK_AGAIN else ""

    def fixable(self, lines: list[Line]) -> str:
        """Your PR with failing CI: its row, or the delivery that made the draft. CI comes before comments, as a fix can change what they're about."""
        wanted = self.open or self.selected(lines)
        run = self.run_of(wanted)
        url = wanted[1] if wanted and wanted[0] == "#pr" else run.pr if run and self.engine.delivered(run) else ""
        pr = next((p for p in self.snapshot.get("prs", {}).get("items", []) if p["url"] == url), None)
        if pr is None or not pr["mine"] or pr["checks"] not in FAILING:
            return ""
        return "" if any(r.fixing and r.pr == url and r.status != "done" for r in self.runs) else url

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
        run = self.target(lines)
        return run if run and run.reviewing and not run.claimed and not run.landed else None

    def run_actions(self, run: Run | None) -> dict[int, str]:
        """The same two keys wherever a run stops. The box says what an answer does there."""
        if run is not None and run.your_turn:
            return {DROP: "d drop"}
        if run is None or run.status not in ("parked", "waiting"):
            return {}
        return {ANSWER: "a answer", DROP: "d drop"}

    def answers(self, run: Run) -> list[Ask]:
        """Each card can also open a talk about it, for when one answer isn't enough."""
        talk = (("Talk it through with Claude first", TALK, ""),)
        return [found._replace(options=(*found.options, *talk)) for found in self.cards(run)]

    def cards(self, run: Run) -> list[Ask]:
        """One card per decision, each with how it fits in. No brief from the agent means the reason, and the report stays above, because a wall of it here helps nobody."""
        options, typed, command = self.engine.choices(run)
        report = self.stop_report(run)[1]
        overview, said = decisions(run.reason)
        if not said:
            overview, said = decisions(report)
        fits = [f"Overview: {overview}"] * bool(overview)
        if not options and any("Bottom line" in item for item in said):  # a recommendation can be taken with a key, not only typed back
            options = [("Agree", command, "Agreed, go with your recommendation")]
        if not said:
            return [Ask(run.key, run.reason.splitlines()[0], (*fits, *self.plain_lines(report)), tuple(options), typed, command)]
        if len(said) == 1:
            return [Ask(run.key, said[0].get("Ask", run.reason.splitlines()[0]), (*fits, *(f"{label}: {said[0][label]}" for label in BRIEF if label in said[0])), tuple(options), typed, command)]
        agree = (("Agree", *options[0][1:]),) if options else ()
        return [Ask(run.key, f"{n} of {len(said)}: {item.get('Ask', '')}", (*fits, *(f"{label}: {item[label]}" for label in BRIEF if label in item)), agree, "Say what to do instead", command) for n, item in enumerate(said, 1)]

    def plain_lines(self, report: str) -> tuple[str, ...]:
        """A proposal's own lines are a brief already, and anything else stays in the panel above."""
        lines = tuple(line for line in report.splitlines() if line.startswith(("Why:", "Background:", "Bottom line:", "Overview:")))
        return lines or (("The full report is above.",) if report else ())

    def begin_series(self, run: Run) -> None:
        """Answers are collected, and sent as one, so several decisions are one go. Esc leaves your place."""
        if run.key in self.paused:
            first, self.queue, self.given = self.paused.pop(run.key)
            self.begin_note(first)
            return
        first, *self.queue = self.answers(run)
        self.given = []
        self.series = bool(self.queue)
        self.begin_note(first)

    def carry_on(self) -> None:
        """An answer doesn't end the conversation: the same run's next question opens by itself. When it's done, you're told, and left on what needs you next, but never answering for another run on a stray enter."""
        if not self.follow or self.refreshing:
            return
        run = next((r for r in self.runs if r.key == self.follow), None)
        self.follow = ""
        if run is None or self.failed:
            return
        if self.run_actions(run).get(ANSWER):
            self.begin_series(run)
            return
        self.say(f"{run.key}: {run.label}")
        if waiting := next((r for r in self.runs if self.run_actions(r).get(ANSWER)), None):
            self.picked = (waiting.repo, waiting.key)

    def ask_to_prepare(self, ref: tuple[str, str] | None) -> None:
        """The selected card is the likely answer, but an idea or another ticket can still be typed."""
        repos = list(self.cfg["repos"])
        item = self.ticket(ref)
        if item is None or ref is None:
            if len(repos) > 1:  # a ticket number means nothing without one
                self.begin_note(Ask("", "Prepare in which repo?", (), tuple((name, CHOOSE_REPO, name) for name in repos), "Or write it as repo/ticket or repo/idea", "prepare"))
            else:
                self.ask_what_to_prepare()
            return
        within = repos[0] if len(repos) == 1 else ""
        typed = "Or another ticket number, or an idea to make into a ticket" if within else "Or write it as repo/ticket or repo/idea"
        self.begin_note(Ask("", "What should be prepared?", (), ((f"{ref[1]}  {item['title']}", "prepare", ref[1].partition("/")[2] if within else ref[1]),), typed, "prepare", within))

    def ask_what_to_prepare(self, repo: str = "") -> None:
        self.begin_note(Ask("", f"What should be prepared in {repo}?" if repo else "What should be prepared?", (), (), "A ticket number, or an idea to make into a ticket", "prepare", repo))

    def begin_note(self, asking: Ask) -> None:
        self.asking, self.typed = asking, self.drafts.get(asking.key + asking.command, "")
        self.cursor = len(self.typed)
        self.choice = len(asking.options) if self.typed or all(command == TALK for _, command, _ in asking.options) else 0  # a talk is offered, never the default

    def insert(self, text: str) -> None:
        self.typed = self.typed[: self.cursor] + text + self.typed[self.cursor :]
        self.cursor += len(text)
        if self.asking:
            self.choice = len(self.asking.options)  # writing is choosing to write

    def cut(self, start: int, end: int) -> None:
        self.typed = self.typed[:start] + self.typed[end:]
        self.cursor = start

    def type_key(self, key: int) -> None:
        asking = self.asking
        if asking is None:
            return
        if key == ESCAPE:
            self.drafts[asking.key + asking.command] = self.typed
            if self.series:
                self.paused[asking.key] = (asking, self.queue, self.given)
            self.asking, self.series = None, False
        elif key in ENTERS:
            typing = self.choice == len(asking.options)
            command, words = (asking.command, self.typed.strip()) if typing else asking.options[self.choice][1:]
            # While another command runs, the words stay put for a second enter.
            if command == TALK and not self.refreshing:  # the run stays stopped, and its card is here when you come back
                self.asking, self.series = None, False
                self.send(TALK, asking.key)
                return
            if (words or not typing) and not self.refreshing:
                self.drafts.pop(asking.key + asking.command, None)
                if command == CHOOSE_REPO:
                    self.ask_what_to_prepare(words)
                    return
                if self.series:
                    self.given.append(f"{asking.question.partition(': ')[2]} {words}" if words else "")
                    if self.queue:
                        self.begin_note(self.queue.pop(0))
                        return
                    notes = [note for note in self.given if note]
                    command, words = (asking.command, "\n".join(notes)) if notes else (command, "")
                self.asking, self.series = None, False
                self.follow = asking.key
                self.send(*filter(None, (command, asking.key, f"{asking.within}/{words}" if asking.within else words)))
        elif key in (curses.KEY_UP, curses.KEY_DOWN):
            self.choice = max(0, min(self.choice + (1 if key == curses.KEY_DOWN else -1), len(asking.options)))
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
        if self.asking:
            self.insert(pasted.removesuffix(PASTE_END).decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n"))
        return curses.ERR

    def catch_up(self) -> None:
        """Quietly, unlike r: you did something, and the board shouldn't get in the way of your next key."""
        if "rate limit" in self.snapshot.get("prs", {}).get("error", "") and self.clock() - self.caught < BACK_OFF_SECONDS:
            return
        self.caught = self.clock()

        def work() -> None:
            try:
                self.engine.tools.workshop("refresh")
            except STOPS:
                pass  # the header shows how old it is

        threading.Thread(target=work, daemon=True).start()

    def send(self, *args: str) -> None:
        """Commands can take a while on GitHub or Jira, so the board keeps drawing."""
        if self.refreshing:
            return
        self.refreshing = True

        def work() -> None:
            try:
                self.engine.tools.workshop(*args)
                if args[0] != "refresh":
                    self.catch_up()
            except STOPS as failed:
                self.say(str(failed))
                self.failed = True
            self.refreshing = False

        threading.Thread(target=work, daemon=True).start()

    def say(self, text: str) -> None:
        self.message, self.said = text, self.clock()

    def jump(self, target: tuple[str, str] | None) -> None:
        run = self.run_of(target)
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

    def header(self) -> Line:
        """Each tab's count is what waits on you there, and turns yellow when something new has arrived since you last looked."""
        piles = self.piles()
        self.looked[self.tab] = piles[self.tab]
        parts = []
        for i, tab in enumerate(TABS):
            style = "tab-on" if i == self.tab and not self.open else "tab"
            count = f"{len(piles[i])} " if piles[i] else ""
            parts += [Span(f" {SIGNS[tab]} {tab} ", style, f"tab:{i}"), Span(count, "row-yellow" if piles[i] - self.looked[i] else style, f"tab:{i}"), Span(" ")]
        return Line(parts)

    def status(self) -> Line:
        """One quiet line at the bottom, under the keys, the same on every tab: what's left of the limits and machine, when the lists were last fetched, and why new work waits."""
        sources = {**{f"{name}'s backlog": found for name, found in self.snapshot.get("repos", {}).items()}, **({"PRs": self.snapshot["prs"]} if "prs" in self.snapshot else {})}
        times = [found.get("polled_at", 0) for found in sources.values() if found.get("polled_at")]
        age = f"updated {ago(self.clock() - min(times))} ago" if times else "not updated yet"
        failed = [name for name, found in sources.items() if found.get("error")]
        held = self.engine.throttle.hold_new(len([r for r in self.runs if r.status == "running"])) or full_pile(self.runs, self.snapshot.get("prs", {}).get("items", []), self.cfg.get("max_waiting", 0))
        return Line([*usage_line(self.engine.throttle.pace(), self.meter.read()), Span(f"  {age}", "dim"), Span(f"   couldn't update {', '.join(failed)}" if failed else "", "yellow"), Span(f"   held: {held}" if held else "", "yellow")])

    def repo_chips(self) -> list[Line]:
        """Where your pile is, once it spans repos. A click narrows the list to one, and again to undo."""
        repos = Counter(found.repo for found in self.waiting())
        if len(repos) < 2:
            return []
        chips = [part for repo, count in repos.most_common() for part in (Span(f" {repo} {count} ", "tab-on" if self.filter == repo else "tab", f"filter:{repo}"), Span(" "))]
        return [Line([Span(" "), *chips]), plain()]

    def footer(self, width: int = 120) -> Line:
        if self.refreshing:
            return Line([Span("Working...", "dim")])
        if self.message and self.clock() - self.said < MESSAGE_SECONDS:
            return Line([Span(self.message, "yellow")])
        if self.finding:
            return Line([Span(f" / {self.filter}{CURSOR}", "bold"), Span("   enter keeps it, esc clears it", "dim")])
        lines = self.lines(width)
        moves = [("esc back", ESCAPE)] if self.open else [("tab switch", TAB_KEY), *[("enter open", 10)] * bool(self.choices(lines))]
        run = self.target(lines)
        stopped = moves[:1] * bool(self.open) + [(label, key) for key, label in self.run_actions(run).items()]
        offers = [("a ai review", AI_REVIEW), (f"A ai review, {self.other_mode()}", AI_REVIEW_OTHER)] if self.unreviewed(lines) else []
        offers = [("a ai reply", AI_REVIEW)] if self.answerable(lines) else offers
        offers = [("a re-review", AI_REVIEW)] if self.askable(lines) else offers
        offers = [("a fix CI", AI_REVIEW)] if self.fixable(lines) else offers
        offers += [("m claim", CLAIM)] * bool(self.claimable(lines))
        offers += [("p prepare", PREPARE)] * (self.tab == BACKLOG)
        offers += [(f"esc clear /{self.filter}", ESCAPE)] if self.filter and not self.open else [("/ filter", FIND)] * (self.tab != USAGE and not self.open)
        common = [("g review" if run and self.engine.review_ready(run) else "g workspace", ord("g"))] * bool(run and run.pane) + [("o link", ord("o"))] * bool(self.link(lines)) + [("r refresh", ord("r"))] + ([] if self.open else [("q quit", ord("q"))])
        return Line([part for label, key in (stopped or moves) + offers + common for part in (Span(f" {label} ", "button", f"key:{key}"), Span(" "))])

    def note(self, width: int) -> list[Line]:
        """Grows upward as you type, so what you're answering stays in view."""
        if self.failed and self.message:
            return [*wrapped(self.message, width, "yellow"), plain(" any key clears this", "dim")]
        if self.asking is None:
            return [self.footer(width)]
        asking, room = self.asking, max(width - 4, 20)
        typing = self.choice == len(asking.options)
        context = [part.text for line in asking.context for part in wrapped(line, room)[:BRIEF_ROWS]]
        lines = [rule(cell(asking.key or "Answer", width - 6).rstrip(), width), *wrapped(asking.question, width - 1, "bold")]
        lines += [plain(f" {text}") for text in context]
        lines += [plain()]
        lines += [plain(f" {'›' if i == self.choice else ' '} {label}", "bold" if i == self.choice else "") for i, (label, _, _) in enumerate(asking.options)]
        lines += [plain(f" {'›' if typing else ' '} {asking.typed}:", "bold" if typing else "")]
        marked = self.typed[: self.cursor] + (CURSOR if typing else "") + self.typed[self.cursor :]
        rows = [part for line in marked.split("\n") for part in textwrap.wrap(clean(line), room, drop_whitespace=False, replace_whitespace=False, break_on_hyphens=False) or [""]]
        here = next((i for i, row in enumerate(rows) if CURSOR in row), 0)
        first = max(0, min(here - NOTE_ROWS + 1, len(rows) - NOTE_ROWS))
        lines += [plain(f"   {row}") for row in rows[first : first + NOTE_ROWS]]
        return [*lines, *wrapped("up and down choose, enter confirms, esc keeps a draft" if asking.options else "enter sends, esc keeps a draft", width - 1, "dim")]

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
            return self.click_action(action, width)
        return True

    def click_action(self, action: str, width: int = 120) -> bool:
        kind, _, value = action.partition(":")
        if kind == "tab":
            self.tab, self.open, self.viewing, self.offset, self.side = int(value), None, "", 0, 0
            return True
        if kind == "filter":
            self.filter, self.picked = "" if self.filter == value else value, None
            return True
        return self.press(int(value), width)

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

    def drain(self, screen: Keys, width: int) -> None:
        """A drag sends an event per cell, and a redraw for each falls behind the hand, so all that's waiting is one redraw."""
        screen.timeout(0)
        while (key := screen.getch()) == curses.KEY_MOUSE:
            try:
                _, x, y, _, state = curses.getmouse()
            except curses.error:
                continue
            if state & curses.REPORT_MOUSE_POSITION and self.grab:
                self.reach = (x, y)
            else:
                self.mouse(x, y, state, width)
                break
        if key not in (curses.ERR, curses.KEY_MOUSE):
            curses.ungetch(key)
        screen.timeout(1000)

    def selection(self, start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int, int]]:
        """Rows as (y, from, to), held to the column the drag began in, so the list and panel never mix."""
        left, right = (0, self.edge or self.right) if not self.edge or start[0] < self.edge else (self.edge + 2, self.right)
        first, last = TOP + 2, self.rows - 4
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
        at = next((i for i, line in enumerate(lines) if any(ref == chosen for _, ref in refs(line))), None)
        if at is not None:
            self.offset = max(min(self.offset, at), at - height + 1)
        self.offset = max(0, min(self.offset, len(lines) - height))
        return lines[self.offset : self.offset + height]

    def put(self, screen: curses.window, y: int, line: Line, styles: dict[str, int], width: int, selected: bool = False, left: int = 0, chosen: tuple[str, str] | None = None) -> None:
        x = left
        for span in line.spans:
            text = clean(span.text)
            if x >= width - 1:
                break
            attr = styles.get(f"row-{span.style}", styles[span.style]) if selected or (chosen and span.action == chosen) else styles[span.style]
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
        foot = self.note(width - 1)[-(rows - TOP - 5) :]
        height = rows - TOP - 4 - len(foot)  # the status line takes the bottom row, with a gap above it
        panelled = self.panelled(width - 1)
        edge = self.list_width(width - 1) + 1 if panelled else width
        self.edge = edge if panelled else 0
        for y, line in enumerate(self.window(lines, height), TOP + 2):
            self.put(screen, y, line, styles, edge, bool(line.ref) and line.ref == chosen, chosen=chosen)
        if panelled:
            side = self.panel(width - 1)
            self.side = max(0, min(self.side, len(side) - height))
            for y in range(TOP + 2, TOP + 2 + height):
                screen.addnstr(y, edge, "│", 1, styles["dim"])
            for y, line in enumerate(side[self.side : self.side + height], TOP + 2):
                self.put(screen, y, line, styles, width, left=edge + 2)
        for y, line in enumerate(foot, rows - 2 - len(foot)):
            self.put(screen, y, line, styles, width)
        self.put(screen, rows - 1, self.status(), styles, width)  # under the keys, out of the way of the page
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
        styles["theirs"], styles["row-theirs"] = styles["dim"], pair(0, grey)  # selected, faint grey would vanish into the grey behind it
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
            self.carry_on()
            screen.timeout(100 if self.follow else 1000)  # the next question shouldn't wait for the redraw
            self.draw(screen, styles)
            key, width = screen.getch(), screen.getmaxyx()[1]
            if key == ESCAPE:
                key = self.paste(screen)
            if key == curses.KEY_MOUSE:
                try:
                    _, x, y, _, state = curses.getmouse()
                except curses.error:
                    continue  # an event curses couldn't read, such as one past the edge
                if not self.mouse(x, y, state, width - 1):
                    return
                self.drain(screen, width - 1)
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
