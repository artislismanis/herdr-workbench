"""Places the fresh reviewer's notes on the diff, for a person to read in hunk."""

import json
import re
from pathlib import Path
from typing import Any


BRIEF_LINE = re.compile(r"\b(What|Why|Background|Bottom line|Ask):\*{0,2}\s*(.+)$")


def section(text: str, heading: str) -> list[str]:
    """The lines under a report's heading, subheadings included."""
    found: list[str] = []
    depth = 0
    for line in text.splitlines():
        level = len(line) - len(line.lstrip("#"))
        if level and not depth and line.lstrip("#").strip().lower() == heading:
            depth = level
        elif level and depth and level <= depth:
            depth = 0
        elif depth and line.strip() and not line.startswith("VERDICT:"):
            found.append(line)
    return found


def brief(text: str) -> dict[str, str]:
    """The lines an agent writes for a person deciding. Lists what it found, so a report without them is plain to see."""
    found = {}
    for line in text.splitlines():
        if match := BRIEF_LINE.search(line.strip()):
            found.setdefault(match[1], match[2].strip())
    return found


def load_notes(path: Path) -> list[dict[str, Any]]:
    """The reviewer's notes. It writes this file by hand, so a bad one means no notes, not a stop."""
    try:
        notes = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(notes, list):
        return []
    return [n for n in notes if isinstance(n, dict) and isinstance(n.get("file"), str) and isinstance(n.get("line"), int) and n.get("note")]


def load_replies(path: Path) -> list[dict[str, Any]]:
    try:
        replies = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(replies, list):
        return []
    return [r for r in replies if isinstance(r, dict) and r.get("reply") and (r.get("comment") is None or isinstance(r.get("comment"), int))]


def place(notes: list[dict[str, Any]], files: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Splits notes into hunk comments and those with nowhere to go. Hunk refuses a note outside the diff, so those wait on the first changed line, and hunk-pr-push posts them as the PR-level comment."""
    ranges = {f["path"]: [h["newRange"] for h in f["hunks"] if h["newRange"][0] >= 1 and h["newRange"][0] <= h["newRange"][1]] for f in files}
    first = next(((path, found[0][0]) for path, found in ranges.items() if found), None)
    placed, outside = [], []
    for note in notes:
        if any(first <= note["line"] <= last for first, last in ranges.get(note["file"], [])):
            placed.append({"filePath": note["file"], "newLine": note["line"], "summary": note["note"], "author": "workshop"})
        elif first:
            where = next(iter(ranges.get(note["file"], [])), None)
            path, line = (note["file"], where[0]) if where else first
            placed.append({"filePath": path, "newLine": line, "summary": f"PR-level: {note['file']}:{note['line']} {note['note']}", "author": "pr-level"})
        else:
            outside.append(note)
    return placed, outside
