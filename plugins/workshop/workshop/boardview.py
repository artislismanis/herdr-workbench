"""What the board shows, as plain data. Nothing here touches curses, so tests read it directly."""

import re
import textwrap
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import zip_longest
from typing import Any, NamedTuple

from .backlog import COLUMNS
from .review import section
from .store import Run

Usage = dict[str, float]

HEADINGS = {"parked": "Needs you", "waiting": "Needs you", "running": "With agents", "done": "With people"}
ORDER = ("parked", "waiting", "running", "done")
COLOURS = {"parked": "red", "waiting": "yellow", "running": "green", "done": "others"}
BAD_EVENTS = ("parked", "check_failed", "move_failed", "report_failed")
GOOD_EVENTS = ("finished", "approved", "landed")
MOVES = (("you", "Your move"), ("others", "Waiting on others"), ("done", "Done lately"))
SYMBOLS = {True: "●", False: "○"}  # with colour, so it reads in any theme
COLUMN_COLOURS = {"new": "dim", "planned": "others", "designed": "mine", "queued": "green"}
REVIEW_MARKS = {"APPROVED": ("✓", "green"), "CHANGES_REQUESTED": ("✗", "red")}
MAX_LINES = 2000
FINDINGS = 12  # lines of a stopped step's findings in the detail. The whole report is a click away
BAD_VERDICTS = ("CHANGES_NEEDED", "BLOCKED", "FAILED")
FILE_NOTES = {"brief.md": "the ticket", "answers.md": "your answers", "context.md": "others' comments", "feedback.md": "your rejections", "notes.json": "the reviewer's line notes", "ask.md": "the open question"}
RECENT = 10
LANDED = 5
BAR = 30
KANBAN_WIDTH = 80
PIPE_WIDTH = 13


class Span(NamedTuple):
    text: str
    style: str = ""
    action: str = ""


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


def bar(value: float, total: float, size: int) -> str:
    filled = round(size * value / total) if total else 0
    return "█" * filled + "░" * (size - filled)


def pipe(run: Run) -> list[Span]:
    """The run's steps at a glance: done, here and still to come."""
    spans: list[Span] = []
    steps = run.pipeline["steps"]
    for index in range(len(steps)):
        if index:
            spans.append(Span("─", "dim"))
        if completed(run) or index < run.step:
            spans.append(Span("●", "green"))
        elif index == run.step:
            spans.append(Span("◐", "red" if run.landed == "dropped" else COLOURS[run.status]))
        else:
            spans.append(Span("○", "dim"))
    return [*spans, Span(" " * max(PIPE_WIDTH - 2 * len(steps) + 1, 0))]


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


def tiles(runs: list[Run], usage: Usage) -> Line:
    waiting = [("parked", "waiting"), ("running",), ("done",)]
    labels = ("on-red", "needs you"), ("on-green", "with agents"), ("on-others", "with people")
    spans = []
    for statuses, (style, label) in zip(waiting, labels, strict=True):
        spans += [Span(f" {sum(r.status in statuses and not r.landed for r in runs)} ", style), Span(f" {label}    ")]
    for window, used in usage.items():
        spans += [Span(f"{window} ", "dim"), Span(bar(used, 100, 10), "red" if used > 80 else "yellow" if used > 50 else "green"), Span(f" {used:.0f}%   ", "dim")]
    return Line(spans)


def delivery(run: Run, title: str, width: int) -> Line:
    number = run.pr.rstrip("/").rsplit("/", 1)[-1] if run.pr else ""
    detail = run.held or run.reason or run.landed or (f"PR #{number}" if number else "")
    room = max(width - 60, 30)
    title_width = room * 3 // 5
    return Line([Span("  "), Span(" " + cell(run.label, 9), "dim" if run.landed else COLOURS[run.status]), Span(" "), Span(cell(name(run), 22) + " "), *pipe(run), Span(" "), Span(cell(step_id(run), 9) + " ", "dim"), Span(cell(title, title_width) + " "), Span(cell(detail, room - title_width - 1), "dim")], run)


def deliveries(runs: list[Run], events: list[dict[str, Any]], title: Callable[[Run], str], width: int = 120, usage: Usage | None = None) -> list[Line]:
    last = {(e["repo"], e["key"]): e["at"] for e in events}
    landed = sorted((r for r in runs if r.landed), key=lambda r: last.get((r.repo, r.key), ""))[-LANDED:]
    active = sorted((r for r in runs if not r.landed), key=lambda r: ORDER.index(r.status))
    lines = [tiles(runs, usage or {}), plain()]
    for heading in dict.fromkeys(HEADINGS[r.status] for r in active):
        group = [r for r in active if HEADINGS[r.status] == heading]
        lines += [rule(f"{heading} {len(group)}", width), *(delivery(r, title(r), width) for r in group), plain()]
    if landed:
        lines += [rule("Landed", width), *(delivery(r, title(r), width) for r in landed), plain()]
    if not runs:
        lines += [plain("  No tickets picked up yet.", "dim"), plain()]
    return [*lines, rule("Recent", width), *recent(events, RECENT)]


def card(ticket: str, item: dict[str, Any], skipped: dict[str, str]) -> list[tuple[str, str]]:
    found = [(ticket, "dim"), (item["title"], "")]
    if ticket in skipped:
        found.append((f"skipped: {skipped[ticket]}", "yellow"))
    return [*found, ("", "")]


def backlog(snapshot: dict[str, Any], skipped: dict[str, str], started: Callable[[str, str], bool], width: int = 120) -> list[Line]:
    repos = snapshot.get("repos", {})
    if not repos:
        return [plain("Nothing saved yet. Press r, or wait for the next tick.")]
    lines = [plain(f"{repo}: couldn't update: {r['error']}", "red") for repo, r in repos.items() if r["error"]]
    found = {column: [(f"{repo}/{i['key']}", i) for repo, r in repos.items() for i in r["items"] if i["column"] == column and not started(repo, i["key"])] for column in COLUMNS}
    most = max(len(items) for items in found.values())
    for column, items in found.items():
        lines.append(Line([Span(f"  {cell(column.title(), 10)}", "bold"), Span(bar(len(items), most, BAR), COLUMN_COLOURS[column]), Span(f" {len(items)}")]))
    lines.append(plain())
    if width < KANBAN_WIDTH:
        for column, items in found.items():
            lines.append(rule(f"{column.title()} {len(items)}", width))
            for ticket, item in items:
                why = f"  skipped: {skipped[ticket]}" if ticket in skipped else ""
                lines.append(plain(f"  {cell(ticket, 22)} {clean(item['title'])}{clean(why)}", "yellow" if why else ""))
        return lines
    size = (width - 3) // len(COLUMNS) - 1
    lines.append(Line([part for column, items in found.items() for part in (Span(" " + cell(f"{column.title()} {len(items)}", size - 1), f"on-{COLUMN_COLOURS[column]}"), Span(" "))]))
    cards = [[row for ticket, item in items for row in card(ticket, item, skipped)] for items in found.values()]
    for row in zip_longest(*cards, fillvalue=("", "")):
        lines.append(Line([part for text, style in row for part in (Span(cell(text, size), style), Span(" "))]))
    return lines


def facts(pr: dict[str, Any]) -> str:
    checks = {"": "no CI", "SUCCESS": "CI green", "FAILURE": "CI failing", "ERROR": "CI failing", "PENDING": "CI running"}.get(pr["checks"], "")
    threads = f"{pr['threads']} thread(s)" if pr["threads"] else ""
    first = f"reviewed by {', '.join(pr['beaten'])}" if pr["beaten"] else ""
    return "  ".join(part for part in (first, threads, checks) if part)


def pr_style(pr: dict[str, Any]) -> str:
    if pr["move"] == "done":
        return "dim"
    if pr["beaten"]:
        return "yellow"
    if pr["state"] == "checks failing":
        return "red"
    return "mine" if pr["mine"] else "others"


def pull_row(pr: dict[str, Any], width: int) -> Line:
    style = pr_style(pr)
    room = max(width - 56, 30)
    return Line([Span(f"  {SYMBOLS[pr['mine']]} ", style), Span(cell(f"{pr['repo']}#{pr['number']}", 26) + " "), Span(" " + cell(pr["state"], 19), style), Span(" "), Span(cell(pr["title"], room * 3 // 5) + " "), Span(cell(facts(pr), room - room * 3 // 5 - 1), "dim")], pr=pr)


def pulls(snapshot: dict[str, Any], width: int = 120) -> list[Line]:
    found = snapshot.get("prs")
    if not found:
        return [plain("Nothing saved yet. Press r, or wait for the next tick.")]
    lines = [plain(f"Couldn't update: {found['error']}", "red")] if found["error"] else []
    lines.append(Line([Span(" ● ", "mine"), Span("yours    "), Span(" ○ ", "others"), Span("someone else's    "), Span(" ▌", "yellow"), Span("someone reviewed first")]))
    lines.append(plain())
    for move, heading in MOVES:
        items = [p for p in found["items"] if p["move"] == move]
        lines += [rule(f"{heading} {len(items)}", width), *(pull_row(p, width) for p in items), plain()]
    return lines


def pull(pr: dict[str, Any], width: int = 120) -> list[Line]:
    style = pr_style(pr)
    lines = [*wrapped(f"{SYMBOLS[pr['mine']]} {pr['repo']}#{pr['number']}  {pr['title']}", width, "bold"), *wrapped(pr["url"], width, "dim"), Line([Span(f" {pr['state']} ", block(style)), Span(f"  by {pr['author']}")])]
    lines += wrapped(facts(pr), width)
    lines += [plain(), rule("Reviews", width)]
    for who, state in pr["reviews"].items():
        mark, colour = REVIEW_MARKS.get(state, ("•", "dim"))
        lines.append(Line([Span(f"  {mark} ", colour), Span(f"{who}: {state.lower().replace('_', ' ')}")]))
    lines += [plain(f"  … {who}: asked", "dim") for who in pr["waiting_on"]]
    return lines if pr["reviews"] or pr["waiting_on"] else [*lines, plain("  None yet", "dim")]


def body(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip() and not line.startswith(("#", "VERDICT:"))]


def needs_you(run: Run, report: str, width: int) -> list[Line]:
    """What a person has to settle, in the report's words where it has any."""
    items = [item for item in section(report, "needs you") if item.strip(" -*.").lower() not in ("nothing", "none")] or [run.reason]
    lines = [Line([Span("▌ Needs you", "red" if run.status == "parked" else "yellow")])]
    return lines + [plain("  " + part.text) for item in items for part in wrapped(item, width - 2)]


def files_name(step: dict[str, Any]) -> str:
    return step.get("output", f"{step['id']}.md")


def files_of(step: dict[str, Any], reports: tuple[str, ...]) -> tuple[str, list[tuple[int, str]]]:
    """A step's latest report, and earlier rounds newest first. The engine renames each one as the next round starts: name-1.md is the first."""
    name = files_name(step)
    stem, _, suffix = name.rpartition(".")
    rounds = [(int(found[1]), path) for path in reports if (found := re.fullmatch(rf"{re.escape(stem)}-(\d+)\.{re.escape(suffix)}", path.rsplit("/", 1)[-1]))]
    latest = next((path for path in reports if path.rsplit("/", 1)[-1] == name), "")
    return latest, sorted(rounds, reverse=True)


def findings(report: str, name: str, width: int) -> list[Line]:
    items = section(report, "findings") or body(report)
    parts = [part.text for item in items for part in wrapped(item, width)]
    lines = [plain("│   ┌ Findings", "dim"), *(Line([Span("│   │ ", "dim"), Span(text)]) for text in parts[:FINDINGS])]
    return [*lines, plain(f"│   └ more in {name}" if len(parts) > FINDINGS else "│   └", "dim")]


def timeline(run: Run, events: list[dict[str, Any]], reports: tuple[str, ...], width: int, stopped: tuple[str, str] = ("", ""), earlier: bool = False) -> list[Line]:
    lines: list[Line] = []
    steps = run.pipeline["steps"]
    margin = "  " if any(step.get("stage") for step in steps) else ""
    width -= len(margin)
    for index, step in enumerate(steps):
        if step.get("stage") and (index == 0 or steps[index - 1].get("stage") != step["stage"]):
            lines.append(plain(step["stage"], "bold"))
        seen = [e for e in events if e.get("step") == step["id"]]
        spent = [e for e in seen if e["type"] == "spent"]
        finished = [e for e in seen if e["type"] == "step" and e.get("state") == "done"]
        rounds = len([e for e in seen if e["type"] == "step" and e.get("state") == "running"])
        current = index == run.step and not completed(run)
        mark, style = ("◐", "red" if run.landed == "dropped" else COLOURS[run.status]) if current else ("●", "green") if finished or index < run.step or completed(run) else ("○", "dim")
        verdict = finished[-1].get("verdict", "") if finished else ""
        notes = [f"{rounds} round{'' if rounds == 1 else 's'}"] if rounds else []
        if spent:
            prices = [e["usd"] for e in spent]
            notes += [f"{sum(e['minutes'] for e in spent):.1f} min", "unpriced" if None in prices else f"${sum(prices):.2f}"]
        latest, older = files_of(step, reports)
        row = [Span(f"{margin}{mark} ", style), Span(cell(step["id"], 10), "bold" if current else ""), Span(cell(verdict, 16), "red" if verdict in BAD_VERDICTS else "dim"), Span(cell("  ".join(notes), max(min(28, width - 53), 5)), "dim")]  # the report beats the numbers on a narrow panel
        if latest:
            row += [Span("▸ " if current else "  ", "yellow"), Span(latest.rsplit("/", 1)[-1], "mine")]
        if older and not earlier:
            row += [Span("  "), Span(f" +{len(older)} older ", "button", f"key:{ord('e')}")]
        lines.append(Line(row, path=latest))
        if earlier:
            lines += [Line([Span(f"{margin}│ ", "dim"), Span(" " * max(width - 40, 12)), Span(path.rsplit("/", 1)[-1], "mine"), Span(f"  round {number}", "dim")], path=path) for number, path in older]
        if current and stopped[0] == files_name(step):
            lines += [Line([Span(margin), *line.spans], path=line.path) for line in findings(stopped[1], stopped[0], width - 6)]
        if index < len(steps) - 1:
            lines.append(plain(f"{margin}│", "dim"))
    return lines


def document(path: str, text: str, width: int) -> list[Line]:
    lines = [plain(path, "dim"), plain()]
    for line in text.splitlines()[:MAX_LINES]:
        lines += wrapped(line, width) or [plain()]
    return lines


def drill(run: Run, events: list[dict[str, Any]], title: str, next_action: str, files: str, width: int, reports: tuple[str, ...] = (), stopped: tuple[str, str] = ("", ""), earlier: bool = False, handover: str = "") -> list[Line]:
    spent = [e for e in events if e["type"] == "spent"]
    prices = [e["usd"] for e in spent]
    total = f"{sum(e['minutes'] for e in spent):.1f} min  " + ("unpriced" if None in prices else f"${sum(prices):.2f}") if spent else ""
    lines = wrapped(f"{name(run)}  {title}", width, "bold")
    lines += wrapped(f"PR: {run.pr}" if run.pr else "", width)
    lines += [Line([Span(f" {run.label} ", run_style(run)), Span(f"  at {step_id(run)}" if run.current else ""), Span(f"   {total}", "dim")])]
    if run.status in ("parked", "waiting"):
        lines += [plain(), *needs_you(run, stopped[1], width)]
    elif run.reason:
        lines += wrapped(run.reason, width, "dim")
    lines += wrapped(f"Held: {run.held}" if run.held else "", width, "yellow")
    if handover:
        lines += [plain(), Line([Span("▌ Handover", "green")]), *(plain("  " + part.text) for item in handover.splitlines() for part in wrapped(item, width - 2))]
    lines += wrapped(f"Then: {next_action}" if next_action else "", width, "dim")
    owned = {path for step in run.pipeline["steps"] for path in (files_of(step, reports)[0], *(p for _, p in files_of(step, reports)[1]))}
    others = [Line([Span("  "), Span(cell(path.rsplit("/", 1)[-1], 14), "mine"), Span(FILE_NOTES.get(path.rsplit("/", 1)[-1], ""), "dim")], path=path) for path in reports if path not in owned]
    return [*lines, plain(), rule("Steps", width), *timeline(run, events, reports, width, stopped, earlier), plain(), rule("Ticket and notes", width), *others, *wrapped(f"Files: {files}", width, "dim"), plain(), rule("Log", width), *recent(events, 12)]
