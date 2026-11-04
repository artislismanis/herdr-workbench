"""What the board shows, as plain data. Nothing here touches curses, so tests read it directly."""

import re
import textwrap
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import groupby, zip_longest
from typing import Any, NamedTuple

from .backlog import COLUMNS
from .pipeline import shipped
from .review import section
from .store import Run
from .throttle import DAYS, DEFAULTS, ahead
from .usage import KEEP_DAYS, by_day, by_hour, top, totals

Usage = dict[str, tuple[float, float]]
Work = tuple[tuple[int, int], list[str]]  # working hours and days, from the throttle's settings
WORK: Work = (tuple(DEFAULTS["work_hours"]), DEFAULTS["work_days"])

HEADINGS = {"parked": "Needs you", "waiting": "Needs you", "running": "With agents", "done": "With people"}
ORDER = ("parked", "waiting", "running", "done")
COLOURS = {"parked": "red", "waiting": "yellow", "running": "green", "done": "others"}
BAD_EVENTS = ("parked", "check_failed", "move_failed", "report_failed")
GOOD_EVENTS = ("finished", "approved", "landed")
MOVES = {"you": "Needs you", "others": "With people", "done": "Done lately"}
REVIEW_GROUPS = ("Needs you", "With agents", "With people", "Done lately")
RESPOND = ("changes requested", "comments to address")
ASK_AGAIN = "re-review"  # on your own PR, where it's yours to ask for
STATUS = {"needs review": "review", "awaiting review": "review", "open": "review", "changes requested": "changes", "waiting for fixes": "changes", "comments to address": "changes", "checks failing": "changes", "new commits": "changes", "re-review requested": "re-review", "you approved": "approved"}  # where a PR is. The group above says whose move it is
REASONS = {"needs review": "asked of you", "open": "not asked of you", "checks failing": ""}  # the facts beside it say CI failing
OWNER = {True: "mine", False: "dim"}  # the colour of a row's tab symbol: yours in the accent, others' grey
SIGNS = {"Now": "✱", "Backlog": "☰", "Delivery": "▸", "Review": "⌕", "Operate": "⟳", "Usage": "⧖"}  # on each tab, and on Now rows to say which tab they belong to. One column wide, so rows line up
NEXT_UP = ("fix", "decide", "check", "merge", "respond", "review")  # what waiting on you says: what you do next, closest to done first, as finishing frees someone. Unstopping a run comes before all of it
YOUR_MOVE = {"checks failing": "fix", "approved": "merge", "you approved": "merge"}  # your own PR, by its state. The rest of yours is respond
CI_DOTS = {"SUCCESS": "green", "FAILURE": "red", "ERROR": "red", "PENDING": "yellow", "EXPECTED": "yellow"}
PACE_AMBER, PACE_RED = 10, 25  # points ahead of an even pace, so a busy start isn't an alarm
MACHINE_AMBER, MACHINE_RED = 75, 90
COLUMN_COLOURS = {"new": "dim", "planned": "others", "designed": "mine", "queued": "green"}
REVIEW_MARKS = {"APPROVED": ("✓", "green"), "CHANGES_REQUESTED": ("✗", "red")}
MAX_LINES = 2000
TICKET_LINES = 8  # of the ticket under Needs you. All of it is in brief.md
FINDINGS = 12  # lines of a stopped step's findings in the detail. The whole report is a click away
BAD_VERDICTS = ("CHANGES_NEEDED", "BLOCKED", "FAILED")
COMPANIONS = ("notes.json", "replies.json", "proposals.json")  # what the last agent writes beside its report, round by round
FILE_NOTES = {"replies.json": "draft replies", "proposals.json": "proposals", "answers.md": "your answers", "context.md": "others' comments", "feedback.md": "your rejections", "notes.json": "line notes", "ask.md": "the open question"}
LIMITED = "GitHub's hourly limit is used up, so this is from the last update. It clears within the hour."
LANDED = 5
KANBAN_WIDTH = 80
WHO_WIDTH = 100  # narrower than this, the who column goes, as the title says more. By width, so every tab drops it together
STALE_AMBER = 3600  # working seconds waiting on you


class Span(NamedTuple):
    text: str
    style: str = ""
    action: str | tuple[str, str] = ""  # a key or tab to press, or what a click selects


@dataclass
class Line:
    spans: list[Span] = field(default_factory=list)
    run: Run | None = None
    pr: dict[str, Any] | None = None
    path: str = ""

    @property
    def text(self) -> str:
        return "".join(span.text for span in self.spans)

    @property
    def style(self) -> str:
        return self.spans[0].style if self.spans else ""

    @property
    def ref(self) -> tuple[str, str] | None:
        if self.path:
            return ("#file", self.path)
        if self.pr:
            return ("#pr", self.pr["url"])
        return (self.run.repo, self.run.key) if self.run else None


def refs(line: Line) -> list[tuple[int, tuple[str, str]]]:
    """What a line lets you select, with its column. A Backlog row holds a card from each column."""
    if line.ref:
        return [(0, line.ref)]
    found, x = [], 0
    for span in line.spans:
        if isinstance(span.action, tuple):
            found.append((x, span.action))
        x += columns(span.text)
    return found


def only(lines: list[Line], text: str) -> list[Line]:
    """The rows that match, under their headings. A heading with none goes, and so does what isn't a row."""
    kept: list[Line] = []
    heading: Line | None = None
    for line in lines:
        if refs(line) and text.lower() in line.text.lower():
            kept += [*([plain()] if kept and heading else []), *([heading] if heading else []), line]
            heading = None
        elif line.text.startswith("─ "):
            heading = line
    return kept or [plain(f"  Nothing matches {text}.", "dim")]


def plain(text: str = "", style: str = "") -> Line:
    return Line([Span(text, style)])


def wrapped(text: str, width: int, style: str = "") -> list[Line]:
    """For the detail views: lists stay one line per row, so the full text lives here."""
    return [plain(part, style) for part in textwrap.wrap(clean(text), max(width, 20), break_on_hyphens=False)]


def clean(text: str) -> str:
    """Ticket text comes from other people, so nothing in it reaches the terminal as a control."""
    return "".join(c if c.isprintable() else " " for c in text)


def columns(text: str) -> int:
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def cell(text: str, width: int) -> str:
    """Exactly `width` columns on screen, so rows line up whatever the text."""
    text = clean(text)
    if columns(text) > width:
        while text and columns(text) > width - 1:
            text = text[:-1]
        text += "…"
    return text + " " * (width - columns(text))


def ago(seconds: float) -> str:
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{int(seconds // size)}{unit}"
    return f"{int(seconds)}s"


def name(run: Run) -> str:
    return f"{run.repo}/{run.key}"


def completed(run: Run) -> bool:
    return run.status == "done" and run.landed != "dropped"


def step_id(run: Run) -> str:
    return run.current["id"] if run.current else "finished"


def rule(title: str, width: int) -> Line:
    return Line([Span(f"─ {title} ", "bold"), Span("─" * max(width - columns(title) - 5, 0), "dim")])


def first_line(error: str) -> str:
    """Tools explain themselves over many lines, and the first says what to do."""
    return error.splitlines()[0]


def stage_groups(steps: list[dict[str, Any]]) -> list[list[int]]:
    """Step indexes by stage, in order. A step without a stage stands alone."""
    return [[index for index, _ in group] for _, group in groupby(enumerate(steps), lambda pair: pair[1].get("stage") or pair[0])]


def done_all(run: Run) -> bool:
    """A run that stopped after one stage has only done that far."""
    return completed(run) and not run.until


def stage_state(run: Run, indexes: list[int]) -> tuple[str, str, str]:
    """A stage's mark, colour and word, summed from its steps. Amber waits on a person or had to go round again, and stays amber."""
    steps = run.pipeline["steps"]
    if run.step < indexes[0] and not done_all(run) or (completed(run) and run.until and run.step <= indexes[0]):
        return "○", "dim", "not started"
    here = not completed(run) and run.step in indexes
    reworked = any(run.loops.get(steps[index]["id"]) for index in indexes)
    if here and run.landed == "dropped":
        return "◐", "red", "dropped"
    if here and run.status == "parked":
        return "◐", "red", "fix"
    if here and run.status == "waiting":
        return "◐", "yellow", "decide"
    if here:
        return "◐", "yellow" if reworked else "green", "reworking" if reworked else "working"
    return "●", "yellow" if reworked else "green", "done after rework" if reworked else "done"


def recent(events: list[dict[str, Any]], count: int) -> list[Line]:
    lines = []
    for e in reversed([e for e in events if e["type"] != "spent"][-count:]):
        style = "red" if e["type"] in BAD_EVENTS else "green" if e["type"] in GOOD_EVENTS or e.get("state") == "done" else "dim"
        what = " ".join(part for part in (e["type"] if e["type"] != "step" else "", e.get("step", ""), e.get("state", "")) if part)
        lines.append(Line([Span("  ● ", style), Span(e["at"][5:16].replace("T", " ") + "  ", "dim"), Span(cell(f"{e['repo']}/{e['key']}", 24) + " ", "dim"), Span(clean(what))]))
    return lines


def block(style: str) -> str:
    """A status as a coloured block. Finished work is plain text, so grey blocks stay the buttons'."""
    return style if style == "dim" else f"on-{style}"


def run_style(run: Run) -> str:
    return block("dim" if run.landed else COLOURS[run.status])


def limit_style(window: str, used: float, left: float) -> str:
    """Green lasts until the reset at this rate, however much is used. Amber and red are ahead of that."""
    over = ahead(window, used, left)
    return "red" if used >= 100 or over > PACE_RED else "yellow" if over > PACE_AMBER else "green"


def calm(style: str) -> str:
    """Colour means something needs a look, so all's well is plain."""
    return "" if style == "green" else style


def to_go(seconds: float) -> str:
    """Time to a reset, the way Claude's status line shows it."""
    days, hours, minutes = int(seconds // 86400), int(seconds % 86400 // 3600), int(seconds % 3600 // 60)
    return f"{days}d{hours}h" if days else f"{hours}h{minutes}m" if hours else f"{minutes}m"


def machine_style(value: float) -> str:
    return "red" if value > MACHINE_RED else "yellow" if value > MACHINE_AMBER else "green"


def usage_line(usage: Usage, machine: dict[str, Any] | None) -> list[Span]:
    """What you've got left, quietly, under the tabs. A number is coloured only when it needs a look."""
    parts = [(window, used, calm(limit_style(window, used, left)), f" ({to_go(left)})" if left else "") for window, (used, left) in usage.items()]
    parts += [(name.upper(), machine[name], calm(machine_style(machine[name])), "") for name in ("cpu", "ram")] if machine else []
    return [span for name, value, style, time in parts for span in (Span(f" {name} ", "dim"), Span(f"{value:.0f}%", style), Span(time, "dim"), Span(" "))]


EIGHTHS = " ▁▂▃▄▅▆▇█"
CHART_ROWS = 4
DAY_WIDE = 4  # a day's column, wide enough for its name underneath


def columns_of(values: list[float | None], top: float, wide: int) -> list[str]:
    """A column chart's rows, top first. Each value is a column `wide` cells across with a cell's gap after it, built in eighths of a row. Nothing recorded is an empty column."""
    rows = []
    for level in range(CHART_ROWS, 0, -1):
        cells = [EIGHTHS[0 if v is None or not top else max(0, min(8, round(v / top * CHART_ROWS * 8 - (level - 1) * 8)))] for v in values]
        rows.append("".join(c * wide + " " for c in cells))
    return rows


def chart(title: str, note: str, values: list[float | None], top: float, ticks: tuple[str, str], labels: list[tuple[int, str]], gutter: int, wide: int) -> list[Line]:
    """One chart on the shared grid: the same gutter and column width as the others, so charts over the same hours line up."""
    span = (wide + 1) * len(values)
    lines = [Line([Span(f"  {' ' * gutter}  "), Span(title, "bold"), Span(f"{note:>{max(span - columns(title), 0)}}", "dim")])]
    for n, row in enumerate(columns_of(values, top, wide)):
        tick = ticks[0] if n == 0 else ticks[1] if n == CHART_ROWS // 2 else ""
        lines.append(Line([Span(f"  {tick:>{gutter}} │", "dim"), Span(row.rstrip(), "mine")]))
    under = [" "] * (span + 8)
    for at, text in labels:
        for n, letter in enumerate(text):
            under[at * (wide + 1) + n] = letter
    return [*lines, plain(f"  {' ' * gutter} └" + "─" * span, "dim"), plain(f"  {' ' * gutter}  " + "".join(under).rstrip(), "dim"), plain()]


def trends(rows: list[dict[str, Any]], spent: list[dict[str, Any]], now: float, width: int) -> list[Line]:
    """The 5h limit and spend over the last day, hour by hour on one time axis, then spend day by day. The limit is scaled to 100%, so a quiet day looks quiet."""
    hours = {row["hour"]: row for row in by_hour(rows, spent, now)}
    start = datetime.fromtimestamp(now).replace(minute=0, second=0, microsecond=0)
    slots = [start - timedelta(hours=back) for back in range(23, -1, -1)]
    five = [hours[h]["five"] if h in hours else None for h in slots]
    spend = [hours[h]["spend"] if h in hours else None for h in slots]
    days = by_day(rows, spent, now, 7)  # a week, as in the totals below
    daily = [day["spend"] if day else None for day in days]
    first = datetime.fromtimestamp(now).date() - timedelta(days=len(days) - 1)
    hour_top, day_top = max((v for v in spend if v), default=0), max((v for v in daily if v), default=0)
    ticks = {"five": ("100%", "50%"), "spend": (f"${hour_top:.2f}", f"${hour_top / 2:.2f}"), "daily": (f"${day_top:.0f}", f"${day_top / 2:.0f}")}
    gutter = max(columns(tick) for pair in ticks.values() for tick in pair)
    wide = 2 if gutter + 6 + 3 * len(slots) <= width else 1
    by_hours = [(n, slot.strftime("%H:%M")) for n, slot in enumerate(slots) if n % 6 == 0]
    by_days = [(n, (first + timedelta(days=n)).strftime("%a")) for n in range(len(days))]
    return [
        rule("Last 24 hours, by the hour", width),
        *chart("5h limit used", f"peak {max((v for v in five if v is not None), default=0):.0f}%", five, 100, ticks["five"], by_hours, gutter, wide),
        *chart("Spend", f"${sum(v for v in spend if v):.2f}", spend, hour_top, ticks["spend"], by_hours, gutter, wide),
        rule("Last 7 days, by the day", width),
        *chart("Spend", f"${sum(v for v in daily if v):.2f}", daily, day_top, ticks["daily"], by_days, gutter, DAY_WIDE),
    ]


def usage_view(rows: list[dict[str, Any]], events: list[dict[str, Any]], machine: dict[str, Any] | None, now: float, width: int = 120) -> list[Line]:
    """Machine, limits and work side by side, to show whether WSL needs more and what uses the limits."""
    spent = [e for e in events if e["type"] == "spent"]
    lines = [plain(f"  {machine['cores']} cores, {machine['memory']:.0f}G of memory", "dim"), plain()] if machine else []
    if not rows and not spent:
        return [*lines, plain("  No samples yet. One arrives every 5 minutes.", "dim")]
    lines += [*trends(rows, spent, now, width), rule("Totals", width)]
    for label, days in (("Today", 1), ("Week", 7), ("Month", KEEP_DAYS)):
        found = totals(rows, spent, now, days)
        lines.append(plain(f"  {label:8}peak cpu {found['cpu']:.0f}%   ram {found['ram']:.0f}%   {found['agents']:.0f} agents at once   ${found['spend']:.2f}"))
    model, step = top(spent, now)
    lines += [plain(f"  Most of this week's spend: {model}, on {step}", "dim")] if model else []
    return lines


def doing(run: Run) -> str:
    """Waiting on you, a run says what you do next. Otherwise it says where it is."""
    if heading(run) != "Needs you" or run.landed:
        return run.label
    return "fix" if run.status == "parked" else "decide" if run.status == "waiting" or run.your_turn else "check"


def rank(word: str) -> int:
    return NEXT_UP.index(word) if word in NEXT_UP else len(NEXT_UP)


def heading(run: Run) -> str:
    return "Needs you" if run.with_you or run.your_turn else HEADINGS[run.status]


def deliveries(runs: list[Run], events: list[dict[str, Any]], title: Callable[[Run], str], width: int = 120, now: float = 0.0, ci: Callable[[str], str] = lambda url: "", work: Work = WORK) -> list[Line]:
    return run_groups(runs, events, title, width, now, ci, work) or [plain("  No tickets picked up yet.", "dim")]


def run_groups(runs: list[Run], events: list[dict[str, Any]], title: Callable[[Run], str], width: int, now: float = 0.0, ci: Callable[[str], str] = lambda url: "", work: Work = WORK) -> list[Line]:
    last = {(e["repo"], e["key"]): e["at"] for e in events}
    landed = sorted((r for r in runs if r.landed), key=lambda r: last.get((r.repo, r.key), ""))[-LANDED:]
    active = sorted((r for r in runs if not r.landed), key=lambda r: (rank(doing(r)), last.get((r.repo, r.key), "")) if heading(r) == "Needs you" else (len(NEXT_UP) + 1 + ORDER.index(r.status), ""))
    groups = {name: [r for r in active if heading(r) == name] for name in dict.fromkeys(heading(r) for r in active)} | ({"Done lately": landed} if landed else {})
    items = {name: [run_item(r, title(r), last.get((r.repo, r.key), ""), ci(r.pr)) for r in group] for name, group in groups.items()}
    return rows_under(items, width, now, work)


def rows_under(groups: dict[str, list["Item"]], width: int, now: float, work: Work) -> list[Line]:
    """Each group under its heading and count, in one row shape, with columns sized across them all so they line up."""
    shown = [item for items in groups.values() for item in items]
    sizes = max((columns(item.name) for item in shown), default=0), max((columns(item.who) for item in shown), default=0)
    return [line for heading_name, items in groups.items() if items for line in (rule(f"{heading_name} {len(items)}", width), *(item_row(item, sizes, width, now, work) for item in items), plain())]


def operations(runs: list[Run], events: list[dict[str, Any]], title: Callable[[Run], str], schedule: list[dict[str, Any]], now: float, width: int = 120, ci: Callable[[str], str] = lambda url: "", work: Work = WORK) -> list[Line]:
    """After the merge: getting it live, then looking after it. What a sweep turns up becomes a ticket on the Backlog."""
    lines = run_groups(runs, events, title, width, now, ci, work) or [plain("  Nothing merged or swept yet.", "dim"), plain()]
    return [*lines, rule("Sweeps", width), *(sweep_row(item, now) for item in schedule)] if schedule else [*lines, rule("Sweeps", width), plain("  None scheduled. A repo lists them in its config, as `sweeps = { docs = \"monthly\" }`.", "dim")]


def sweep_row(item: dict[str, Any], now: float) -> Line:
    last = f"ran {ago(now - item['last'])} ago" if item["last"] else "not run yet"
    left = item["last"] + item["every"] - now if item["last"] else 0
    return plain(f"  {cell(item['repo'], 22)} {cell(item['sweep'], 9)} {cell(item['cadence'], 9)} {cell(last, 16)} {'next in ' + to_go(left) if left > 0 else 'due'}")


def card(ticket: str, item: dict[str, Any], skipped: dict[str, str]) -> list[tuple[str, str, tuple[str, str] | str]]:
    ref = ("#ticket", ticket)
    found: list[tuple[str, str, tuple[str, str] | str]] = [(ticket, "dim", ref), (item["title"], "", ref)]
    if ticket in skipped:
        found.append((f"skipped: {skipped[ticket]}", "yellow", ref))
    return [*found, ("", "", "")]


def backlog(snapshot: dict[str, Any], skipped: dict[str, str], started: Callable[[str, str], bool], width: int = 120, place: Callable[[str, str], int] = lambda repo, key: 0, keep: str = "") -> list[Line]:
    repos = snapshot.get("repos", {})
    if not repos:
        return [plain("Nothing saved yet. Press r, or wait for the next tick.")]
    limited = any("rate limit" in r["error"] for r in repos.values())
    lines = [plain(LIMITED, "yellow")] if limited else [plain(f"{repo}: couldn't update: {first_line(r['error'])}", "red") for repo, r in repos.items() if r["error"]]
    found = {column: [(f"{repo}/{i['key']}", i) for repo, r in repos.items() for i in r["items"] if i["column"] == column and not started(repo, i["key"]) and keep.lower() in f"{repo}/{i['key']} {i['title']}".lower()] for column in COLUMNS}
    found["queued"].sort(key=lambda pair: place(*pair[0].split("/", 1)))  # first to go at the top
    if width < KANBAN_WIDTH:
        for column, items in found.items():
            lines.append(rule(f"{column.title()} {len(items)}", width))
            for ticket, item in items:
                why = f"  skipped: {skipped[ticket]}" if ticket in skipped else ""
                lines.append(Line([Span(f"  {cell(ticket, 22)} {clean(item['title'])}{clean(why)}", "yellow" if why else "", ("#ticket", ticket))]))
        return lines
    size = (width - 3) // len(COLUMNS) - 1
    heads = [(f"{column.title()} {len(items)} ", COLUMN_COLOURS[column]) for column, items in found.items()]
    lines.append(Line([part for title, colour in heads for part in (Span(title, f"{colour}" if colour != "dim" else "bold"), Span("─" * max(size - columns(title), 0) + " ", "dim"))]))
    cards = [[row for ticket, item in items for row in card(ticket, item, skipped)] for items in found.values()]
    for row in zip_longest(*cards, fillvalue=("", "", "")):
        lines.append(Line([part for text, style, ref in row for part in (Span(cell(text, size), style, ref), Span(" "))]))
    return lines


def ticket_view(ticket: str, item: dict[str, Any], skipped: dict[str, str], width: int) -> list[Line]:
    """A card on the Backlog, opened: enough to decide whether to prepare it now."""
    lines = [*wrapped(f"{ticket}  {item['title']}", width, "bold"), *wrapped(f"Ticket: {item['url']}", width), Line([Span(f" {item['column']} ", f"on-{COLUMN_COLOURS[item['column']]}")])]
    lines += wrapped(f"Skipped: {skipped[ticket]}", width, "yellow") if ticket in skipped else []
    step = "It's queued, so delivery picks it up in turn. p asks again if you want to change it." if item["column"] == "queued" else "p starts the next Prepare talk on it, with the agent beside you."
    return [*lines, plain(), Line([Span("▌ Next", "yellow")]), *(plain("  " + part.text) for part in wrapped(step, width - 2))]


def facts(pr: dict[str, Any]) -> str:
    checks = {"": "no CI", "SUCCESS": "CI green", "FAILURE": "CI failing", "ERROR": "CI failing", "PENDING": "CI running"}.get(pr["checks"], "")
    threads = f"{pr['threads']} thread(s)" if pr["threads"] else ""
    first = f"reviewed by {', '.join(pr['beaten'])}" if pr["beaten"] else ""
    return "  ".join(part for part in ("asked in chat" if pr.get("chat") else "", first, threads, checks) if part)


def pr_style(pr: dict[str, Any]) -> str:
    if pr["move"] == "done":
        return "dim"
    return "yellow" if pr["move"] == "you" else "others"


def live(run: Run | None) -> bool:
    """A run says where a PR has got to while it's working or waiting on you. After that, GitHub does."""
    return run is not None and (run.status != "done" or run.with_you)


def pull_group(pr: dict[str, Any], run: Run | None) -> str:
    if not live(run):
        return MOVES[pr["move"]]
    return "With agents" if run.status == "running" else "Needs you"


def pull_rank(pr: dict[str, Any], run: Run | None) -> tuple[int, str]:
    """Oldest first within a status, so nothing sinks."""
    word = pull_status(pr, run)[0]
    return rank(word), pr["updated"]


def run_for(runs: list[Run], ref: tuple[str, str] | None) -> Run | None:
    """The run behind a row. On a PR, what's working on it now comes before the delivery that made it."""
    found = [r for r in runs if ref in ((r.repo, r.key), ("#pr", r.pr))]
    return next((r for r in found if r.on_a_pr and not r.landed), found[0] if found else None)


def pr_rows(runs: list[Run], items: list[dict[str, Any]], reviewable: Callable[[Run], bool]) -> list[dict[str, Any]]:
    """A draft from delivery is still your self-review, on Delivery, until you let it go. Other drafts aren't in review, but a run on a PR shows nowhere else."""
    drafts = {r.pr for r in runs if reviewable(r)}
    return [p for p in items if ((run := run_for(runs, ("#pr", p["url"]))) and run.on_a_pr and live(run)) or (p["url"] not in drafts and p["state"] != "draft")]


class Waiting(NamedTuple):
    ref: tuple[str, str]
    name: str
    label: str
    since: str  # when it started waiting on you
    tab: str
    repo: str


def waiting(runs: list[Run], items: list[dict[str, Any]], reviewable: Callable[[Run], bool], events: list[dict[str, Any]]) -> list[Waiting]:
    """Everything waiting on you. The board's Needs you, its tab dots and the reminders all read this one list, so they never disagree."""
    last = {(e["repo"], e["key"]): e["at"] for e in events}
    found, shown = [], set()
    for pr in pr_rows(runs, items, reviewable):
        run = run_for(runs, ("#pr", pr["url"]))
        shown.add(pr["url"])
        if pull_group(pr, run) == "Needs you":
            found.append(Waiting(("#pr", pr["url"]), pr_name(pr), pull_status(pr, run)[0], pr.get("since") or pr["updated"], "Review", pr["repo"]))
    # A run on a PR that's listed is that PR's row, so it isn't counted twice.
    return found + [Waiting((r.repo, r.key), name(r), doing(r), last.get((r.repo, r.key), ""), "Review" if r.on_a_pr else "Operate" if r.operating else "Delivery", r.repo) for r in runs if heading(r) == "Needs you" and not (r.on_a_pr and r.pr in shown)]


def full_pile(runs: list[Run], items: list[dict[str, Any]], limit: int) -> str:
    """Why no new work starts, if your pile has reached `max_waiting`. Agents make work faster than a person clears it."""
    waiting = {r.pr or r.key for r in runs if not r.preparing and (r.status in ("parked", "waiting") or r.with_you)}
    waiting |= {p["url"] for p in items if p["move"] == "you" and p["state"] != "draft"}
    return f"{len(waiting)} waiting on you" if 0 < limit <= len(waiting) else ""


def pr_name(pr: dict[str, Any]) -> str:
    return f"{pr['repo']}#{pr['number']}"


def author(pr: dict[str, Any]) -> str:
    return "you" if pr["mine"] else pr["author"]


def pull_status(pr: dict[str, Any], run: Run | None) -> tuple[str, str]:
    """The same word and colour in the row and the detail."""
    if live(run):
        return doing(run), "yellow" if run.with_you else COLOURS[run.status]
    if pr["move"] == "you":
        return YOUR_MOVE.get(pr["state"], "respond") if pr["mine"] else "review", pr_style(pr)
    return STATUS.get(pr["state"], pr["state"]), pr_style(pr)


def next_move(pr: dict[str, Any], run: Run | None, advice: str) -> str:
    """The one thing to do next, so nobody has to work it out from the state."""
    if pr["mine"] and pr["state"] == ASK_AGAIN and not (run and run.status != "done"):
        return "Your fixes are pushed, but GitHub shows changes requested until it's reviewed again. a asks for another look."
    if run and run.status == "running":
        return "An agent is on it. This moves to Needs you when it's done."
    if run and run.status != "done":
        return ""  # what's stopped it says what it needs
    if run and run.reviewing and run.with_you:
        return f"{advice}. g opens it in hunk, beside the agent, so you can ask it anything. prefix+shift+u there posts what you keep.".removeprefix(". ")
    if run and run.with_you:
        return "Check the fix and the replies below. g opens the fix in hunk, beside the agent: tell it what to change. prefix+shift+u there pushes the fix and sends the replies."
    if pr["mine"] and pr["state"] == "checks failing":
        return "CI is failing. a has an agent find out why and fix it, for you to check before it's pushed."
    if pr["mine"] and pr["state"] in RESPOND:
        return "a has an agent draft the fixes and replies, for you to check."
    if pr["mine"] and pr["state"] == "approved":
        return "It's approved, so it's ready to merge."
    if pr["move"] == "you" and not pr["mine"] and pr["state"] != "draft":
        return "a starts the AI review: a brief to read first, then notes on the diff to keep or drop, with the reviewer beside you."
    return ""


def drafted(replies: list[dict[str, Any]], width: int) -> list[Line]:
    """What will go out, while it can still be changed."""
    lines = [plain(), Line([Span("▌ Replies to post", "yellow")])] if replies else []
    for reply in replies:
        lines += [*wrapped(f"  On {reply.get('about') or reply['comment'] or 'the PR'}" + (", with the commit" if reply.get("fixed") else ""), width, "dim"), *(plain("    " + part.text) for part in wrapped(reply["reply"], width - 4))]
    return lines


def pull_story(pr: dict[str, Any], run: Run | None, advice: str, handover: str, stopped: str, workflow: list[Line], width: int, replies: list[dict[str, Any]] | None = None) -> list[Line]:
    """What a person needs to move on a PR, in the order they need it."""
    lines = [plain(), Line([Span("▌ Next", "yellow")]), *(plain("  " + part.text) for part in wrapped(step, width - 2))] if (step := next_move(pr, run, advice)) else []
    lines += [plain(), *needs_you(run, stopped, width)] if run and run.status in ("parked", "waiting") else []
    return lines + drafted(replies or [], width) + briefing(handover, width) + ([plain(), rule("Workflow", width), *workflow] if workflow else [])


def pulls(snapshot: dict[str, Any], width: int = 120, linked: Callable[[dict[str, Any]], Run | None] = lambda pr: None, now: float = 0.0, work: Work = WORK) -> list[Line]:
    found = snapshot.get("prs")
    if not found:
        return [plain("Nothing saved yet. Press r, or wait for the next tick.")]
    limited = "rate limit" in found["error"]
    lines = [plain(LIMITED, "yellow")] if limited else [plain(f"Couldn't update: {first_line(found['error'])}", "red")] if found["error"] else []
    grouped = {heading: [(p, linked(p)) for p in found["items"] if pull_group(p, linked(p)) == heading] for heading in REVIEW_GROUPS}
    grouped["Needs you"].sort(key=lambda found: pull_rank(*found))
    return [*lines, *rows_under({heading: [pull_item(p, run) for p, run in items] for heading, items in grouped.items()}, width, now, work)]


def since(stamp: str, now: float) -> str:
    return ago(max(now - datetime.fromisoformat(stamp).timestamp(), 0)) if stamp else ""


@dataclass
class Item:
    """One row of the Now tab: a PR or a run, in the same columns."""

    place: tuple[int, str]
    owner: str  # the colour of its tab symbol
    status: tuple[str, str]
    name: str
    title: str
    who: str
    stamp: str
    dot: Span
    kind: str = "Review"
    run: Run | None = None
    pr: dict[str, Any] | None = None
    waiting: bool = False  # on you, so its age turns amber then red
    tone: str = ""  # the title's colour: yellow when someone reviewed first


def pull_item(pr: dict[str, Any], run: Run | None) -> Item:
    waiting = pull_group(pr, run) == "Needs you"
    stamp = pr.get("since") or pr["updated"] if waiting else pr["updated"]
    return Item(pull_rank(pr, run), OWNER[pr["mine"]], pull_status(pr, run), pr_name(pr), pr["title"], author(pr), stamp, Span("●", CI_DOTS.get(pr["checks"], "dim")), pr=pr, waiting=waiting, tone="yellow" if pr["beaten"] else "")


def run_item(run: Run, title: str, stamp: str, checks: str = "") -> Item:
    colour = "dim" if run.landed else "yellow" if run.with_you or run.your_turn else COLOURS[run.status]
    kind = "Review" if run.on_a_pr else "Operate" if run.operating else "Delivery"  # the tab it lives on
    dot = Span("●", CI_DOTS.get(checks, "dim")) if run.pr else Span(" ")
    return Item((rank(doing(run)), stamp), OWNER[True], (doing(run), colour), name(run), title, "you" if run.with_you else "" if run.status == "done" else step_id(run), stamp, dot, kind, run=run, waiting=heading(run) == "Needs you" and not run.landed)


def working(start: float, end: float, work: Work = WORK) -> float:
    """Seconds of working time between two moments, so what stopped on Friday evening isn't red on Monday morning."""
    (first, last), days = work
    total, at = 0.0, max(start, end - 30 * 86400)
    while at < end:
        moment = datetime.fromtimestamp(at)
        step = min(end, (moment.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).timestamp())
        if DAYS[moment.weekday()] in days and first <= moment.hour < last:
            total += step - at
        at = step
    return total


def stale(stamp: str, now: float, work: Work = WORK) -> str:
    """Amber after an hour waiting on you, red after a working day."""
    waited = working(datetime.fromisoformat(stamp).timestamp(), now, work) if stamp else 0
    day = (work[0][1] - work[0][0]) * 3600
    return "red" if waited >= day else "yellow" if waited >= STALE_AMBER else "dim"


def item_row(item: Item, sizes: tuple[int, int], width: int, now: float, work: Work = WORK) -> Line:
    """The one row shape, on every tab. On a narrow panel the who column goes first, then the title gives way, so the age and CI dot stay."""
    who = sizes[1] if width >= WHO_WIDTH else 0
    room = width - sizes[0] - who - (25 if who else 24)
    told = [Span(cell(item.who, who) + " ", item.owner)] if who else []
    age = Span(f"{since(item.stamp, now):>4} ", stale(item.stamp, now, work) if item.waiting else "dim")
    return Line([Span(" "), Span(SIGNS[item.kind] + " ", item.owner), Span(cell(item.status[0], 10) + " ", item.status[1]), Span(cell(item.name, sizes[0]) + " "), Span(cell(item.title, max(room, 10)) + " ", item.tone), *told, age, item.dot], item.run, item.pr)


def now_view(runs: list[Run], prs: list[dict[str, Any]], linked: Callable[[dict[str, Any]], Run | None], title: Callable[[Run], str], queued: list[tuple[str, str]], events: list[dict[str, Any]], now: float, width: int, ci: Callable[[str], str] = lambda url: "", work: Work = WORK) -> list[Line]:
    """Everything in one list, in the order to do it, so the board doesn't need reading tab by tab."""
    last = {(e["repo"], e["key"]): e["at"] for e in events}
    on_prs = {p["url"] for p in prs}
    groups: dict[str, list[Item]] = {"Needs you": [], "With agents": [], "With people": []}
    for pr in prs:
        if pr["move"] != "done":
            run = linked(pr)
            groups[pull_group(pr, run)].append(pull_item(pr, run))
    for run in runs:
        if not run.landed and not run.on_a_pr and run.pr not in on_prs and heading(run) != "With people" and not (run.pr == "" and run.status == "done"):
            groups[heading(run)].append(run_item(run, title(run), last.get((run.repo, run.key), ""), ci(run.pr)))
    lines = rows_under({"Needs you": sorted(groups["Needs you"], key=lambda item: item.place), "With agents": sorted(groups["With agents"], key=lambda item: item.place)}, width, now, work)
    if not groups["Needs you"]:
        lines = [plain("  Nothing needs you.", "dim"), plain(), *lines]
    if queued:
        lines += [rule(f"Up next {len(queued)}", width), *(plain(f"   {cell(ticket, 22)} {clean(text)}", "dim") for ticket, text in queued[:3]), plain()]
    return lines


def said(who: str, text: str, width: int, style: str = "") -> list[Line]:
    critical = clean(text).strip().lower().startswith("blocking")
    return [plain("    " + line.text, "red" if critical and not style else style) for line in wrapped(f"{who}: {text}", width - 4)]


def discussion(pr: dict[str, Any], width: int) -> list[Line]:
    """What's been said on GitHub, open threads first. It's from the last poll, and other people's words are only shown."""
    threads, comments, lines = pr.get("discussion", []), pr.get("comments", []), []
    if threads:
        lines += [plain(), rule("Threads", width)]
    for thread in threads:
        style = "dim" if thread["resolved"] else ""
        lines += wrapped(f"  {thread['path']}:{thread['line'] or 'outdated'}" + ("  resolved" if thread["resolved"] else ""), width, "dim")
        lines += [line for note in thread["said"] for line in said(note["who"], note["text"], width, style)]
    if comments:
        lines += [plain(), rule("Comments", width)]
    return lines + [line for note in comments for line in said(note["who"], note["text"], width)]


def pull(pr: dict[str, Any], width: int = 120, story: list[Line] | None = None, run: Run | None = None) -> list[Line]:
    label, colour = pull_status(pr, run)
    why = REASONS.get(pr["state"], pr["state"])
    beside = "   ".join(part for part in ("" if why == STATUS.get(pr["state"], pr["state"]) else why, facts(pr)) if part)
    lines = [*wrapped(f"{pr_name(pr)}  {pr['title']}", width, "bold"), *wrapped(f"PR: {pr['url']}", width), *wrapped(f"Author: {author(pr)}", width)]
    lines += [Line([Span(f" {label} ", block(colour)), Span(f"   {beside}" if beside else "", "dim")])]
    lines += [*(story or []), plain(), rule("Reviews", width)]
    for who, state in pr["reviews"].items():
        mark, tone = REVIEW_MARKS.get(state, ("•", "dim"))
        lines.append(Line([Span(f"  {mark} ", tone), Span(f"{'you' if who == pr.get('me') else who}: {state.lower().replace('_', ' ')}")]))
    lines += [plain(f"  … {who}: asked", "dim") for who in pr["waiting_on"]]
    if not pr["reviews"] and not pr["waiting_on"]:
        lines.append(plain("  None yet", "dim"))
    return lines + discussion(pr, width)


def body(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip() and not line.startswith(("#", "VERDICT:"))]


def needs_you(run: Run, report: str, width: int) -> list[Line]:
    """What a person has to settle, in the report's words where it has any."""
    items = [item for item in section(report, "needs you") if item.strip(" -*.").lower() not in ("nothing", "none")] or [run.reason]
    lines = [Line([Span("▌ Needs you", "red" if run.status == "parked" else "yellow")])]
    if run.current and run.current["kind"] == "gate":  # the decision needs all of it, here, not a trip to the workspace
        items = [run.reason, "", *(line for line in report.splitlines() if not line.startswith("VERDICT:"))]
    return lines + [plain("  " + part.text) for item in items for part in (wrapped(item, width - 2) or [plain()])]


def ticket_context(brief: str, width: int) -> list[Line]:
    """What the work is for, so a decision doesn't need a trip to the ticket."""
    parts = [part.text for line in brief.partition("\n")[2].splitlines() if line.strip() for part in wrapped(line, width - 4)]
    if not parts:
        return []
    more = [plain("  └ more in brief.md", "dim")] if len(parts) > TICKET_LINES else []
    return [plain("  The ticket", "dim"), *(plain(f"  │ {text}", "dim") for text in parts[:TICKET_LINES]), *more, plain()]


def files_name(step: dict[str, Any]) -> str:
    return step.get("output", f"{step['id']}.md")


def files_of(step: dict[str, Any], reports: tuple[str, ...]) -> tuple[str, list[tuple[int, str]]]:
    return versions(files_name(step), reports)


def versions(name: str, reports: tuple[str, ...]) -> tuple[str, list[tuple[int, str]]]:
    """A file's latest version, and earlier rounds newest first. The engine renames each one as the next round starts: name-1.md is the first."""
    stem, _, suffix = name.rpartition(".")
    rounds = [(int(found[1]), path) for path in reports if (found := re.fullmatch(rf"{re.escape(stem)}-(\d+)\.{re.escape(suffix)}", path.rsplit("/", 1)[-1]))]
    latest = next((path for path in reports if path.rsplit("/", 1)[-1] == name), "")
    return latest, sorted(rounds, reverse=True)


def findings(report: str, name: str, width: int, lead: str = "") -> list[Line]:
    items = section(report, "findings") or body(report)
    parts = [part.text for item in items for part in wrapped(item, width)]
    lines = [plain(f"{lead}┌ Findings", "dim"), *(Line([Span(f"{lead}│ ", "dim"), Span(text)]) for text in parts[:FINDINGS])]
    return [*lines, plain(f"{lead}└ more in {name}" if len(parts) > FINDINGS else f"{lead}└", "dim")]


def stage_heading(run: Run, stage: str, indexes: list[int], events: list[dict[str, Any]]) -> Line:
    _, style, word = stage_state(run, indexes)
    ids = {run.pipeline["steps"][index]["id"] for index in indexes}
    spent = [e for e in events if e["type"] == "spent" and e.get("step") in ids]
    prices = [e["usd"] for e in spent]
    total = f"{sum(e['minutes'] for e in spent):.1f} min  " + ("unpriced" if None in prices else f"${sum(prices):.2f}") if spent else ""
    return Line([Span(f" {stage} ", block(style) if style != "dim" else "bold"), Span(f"  {word}", style), Span(f"   {total}" if total else "", "dim")])


def file_row(lead: list[Span], path: str, room: int, label: str = "") -> Line:
    name = path.rsplit("/", 1)[-1]
    note = FILE_NOTES.get(re.sub(r"-\d+(\.\w+)$", r"\1", name), "")
    if label == note:
        note = ""
    return Line([*lead, Span(cell(label, 25)), Span(cell(note, room), "dim"), Span("  "), Span(name, "mine")], path=path)


def branch(staged: bool, in_stage: bool, last: bool) -> str:
    """Where a row hangs: the same tree as herdr's sidebar, so a stage's steps read as one group."""
    if not staged:
        return ""
    return " " + ("  " if not in_stage else "└ " if last else "├ ")


def ticket_rows(run: Run, files: list[str], staged: bool, room: int) -> list[Line]:
    """What the run started from. There's no step for it yet, so it leads the timeline. Everything people added sits with it."""
    stage = run.pipeline.get("ticket_stage")
    heading = [Line([Span(f" {stage} ", "on-green"), Span("  done", "green")])] if stage else []
    start = "PR" if run.on_a_pr else "brief" if run.sweeping else "ticket"
    rows = [file_row([Span(branch(staged, bool(stage), index == len(files) - 1), "dim"), Span("● ", "green")], path, room, FILE_NOTES.get(path.rsplit("/", 1)[-1], path.rsplit("/", 1)[-1]) if index else start) for index, path in enumerate(files)]  # nothing but where it started and what people added gets here
    return [*heading, *rows, plain()] if heading or rows else []


def timeline(run: Run, events: list[dict[str, Any]], reports: tuple[str, ...], width: int, stopped: tuple[str, str] = ("", ""), earlier: bool = False) -> list[Line]:
    steps = run.pipeline["steps"]
    staged = any(step.get("stage") for step in steps) or bool(run.pipeline.get("ticket_stage"))
    width -= len(branch(staged, True, True))
    room = max(min(28, width - 52), 5)
    alongside = [versions(name, reports) for name in COMPANIONS]
    every = [*(files_of(step, reports) for step in steps), *alongside]
    owned = {path for latest, older in every for path in (latest, *(p for _, p in older))}
    lines = ticket_rows(run, sorted((path for path in reports if path not in owned), key=lambda path: not path.endswith("/brief.md")), staged, room)
    # Only workshop's own prompts and proposing steps are asked for these, so a step of your own after them doesn't take them.
    writes_notes = max((index for index, step in enumerate(steps) if step["kind"] == "agent" and (shipped(step.get("prompt", "")) or step.get("proposes"))), default=-1)
    groups = stage_groups(steps)
    starts = {indexes[0]: indexes for indexes in groups}
    ends = {indexes[-1] for indexes in groups}
    for index, step in enumerate(steps):
        if step.get("stage") and index in starts:
            lines.append(stage_heading(run, step["stage"], starts[index], events))
        hang = branch(staged, bool(step.get("stage")), index in ends)
        under = f"{hang.replace('├', '│').replace('└', ' ')}  "
        seen = [e for e in events if e.get("step") == step["id"]]
        spent = [e for e in seen if e["type"] == "spent"]
        finished = [e for e in seen if e["type"] == "step" and e.get("state") == "done"]
        rounds = len([e for e in seen if e["type"] == "step" and e.get("state") == "running"])
        current = index == run.step and not completed(run)
        mark, style = ("◐", "red" if run.landed == "dropped" else COLOURS[run.status]) if current else ("●", "green") if index < run.step or done_all(run) or (finished and not run.until) else ("○", "dim")
        verdict = finished[-1].get("verdict", "") if finished else ""
        facts = [f"{rounds} round{'' if rounds == 1 else 's'}"] if rounds else []
        if spent:
            prices = [e["usd"] for e in spent]
            facts += [f"{sum(e['minutes'] for e in spent):.1f} min", "unpriced" if None in prices else f"${sum(prices):.2f}"]
        latest, older = files_of(step, reports)
        beside = alongside if index == writes_notes else []
        past = sorted([*older, *(found for _, rounds in beside for found in rounds)], key=lambda found: -found[0])
        row = [Span(hang, "dim"), Span(f"{mark} ", style), Span(cell(step["id"], 10), "bold" if current else ""), Span(cell(verdict, 15), "red" if verdict in BAD_VERDICTS else "dim"), Span(cell("  ".join(facts), room), "dim")]  # the report beats the numbers on a narrow panel
        if latest:
            row += [Span("▸ " if current else "  ", "yellow"), Span(latest.rsplit("/", 1)[-1], "mine")]
        if past and not earlier:
            row += [Span("  "), Span(f" +{len({number for number, _ in past})} older ", "button", f"key:{ord('e')}")]
        lines.append(Line(row, path=latest))
        lines += [file_row([Span(under, "dim")], path, room) for path, _ in beside if path]
        if earlier:
            lines += [file_row([Span(under, "dim")], path, room, f"round {number}") for number, path in past]
        if current and stopped[0] == files_name(step):
            lines += findings(stopped[1], stopped[0], width - 4, under)
        if staged and index in ends and index < len(steps) - 1:
            lines.append(plain())
    return lines


def document(path: str, text: str, width: int) -> list[Line]:
    lines = [plain(path, "dim"), plain()]
    for line in text.splitlines()[:MAX_LINES]:
        lines += wrapped(line, width) or [plain()]
    return lines


def briefing(handover: str, width: int) -> list[Line]:
    return [plain(), Line([Span("▌ Handover", "green")]), *(plain("  " + part.text) for item in handover.splitlines() for part in wrapped(item, width - 2))] if handover else []


def drill(run: Run, events: list[dict[str, Any]], title: str, next_action: str, files: str, width: int, reports: tuple[str, ...] = (), stopped: tuple[str, str] = ("", ""), earlier: bool = False, handover: str = "", brief: str = "") -> list[Line]:
    spent = [e for e in events if e["type"] == "spent"]
    prices = [e["usd"] for e in spent]
    total = f"{sum(e['minutes'] for e in spent):.1f} min  " + ("unpriced" if None in prices else f"${sum(prices):.2f}") if spent else ""
    lines = wrapped(f"{name(run)}  {title}", width, "bold")
    lines += wrapped(f"PR: {run.pr}" if run.pr else "", width)
    lines += [Line([Span(f" {doing(run)} ", run_style(run)), Span(f"  at {step_id(run)}" if run.current else ""), Span(f"   {total}", "dim")])]
    if run.status in ("parked", "waiting"):
        context = ticket_context(brief, width) if not run.sweeping else []
        head, *rest = needs_you(run, stopped[1], width)
        lines += [plain(), head, *context, *rest]
    elif run.reason:
        lines += wrapped(run.reason, width, "dim")
    lines += wrapped(f"Held: {run.held}" if run.held else "", width, "yellow")
    lines += [line for seen in run.noticed for line in wrapped(f"Since you started: {seen}", width, "yellow")]
    lines += briefing(handover, width)
    lines += wrapped(f"Then: {next_action}" if next_action else "", width, "dim")
    return [*lines, plain(), rule("Workflow", width), *timeline(run, events, reports, width, ("", "") if run.current and run.current["kind"] == "gate" else stopped, earlier), *wrapped(f"Files: {files}", width, "dim"), plain(), rule("Log", width), *recent(events, 12)]
