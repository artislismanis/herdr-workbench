"""Places the fresh reviewer's notes on the diff, for a person to read in hunk."""

import json
from pathlib import Path
from typing import Any


def load_notes(path: Path) -> list[dict[str, Any]]:
    """The reviewer's notes. It writes this file by hand, so a bad one means no notes, not a stop."""
    try:
        notes = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(notes, list):
        return []
    return [n for n in notes if isinstance(n, dict) and isinstance(n.get("file"), str) and isinstance(n.get("line"), int) and n.get("note")]


def place(notes: list[dict[str, Any]], files: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Splits notes into hunk comments and those outside the diff: hunk refuses a batch with any of those."""
    ranges = {f["path"]: [h["newRange"] for h in f["hunks"]] for f in files}
    placed, outside = [], []
    for note in notes:
        if any(first <= note["line"] <= last for first, last in ranges.get(note["file"], [])):
            placed.append({"filePath": note["file"], "newLine": note["line"], "summary": note["note"], "author": "workshop"})
        else:
            outside.append(note)
    return placed, outside
