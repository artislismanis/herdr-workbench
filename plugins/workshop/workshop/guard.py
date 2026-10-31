"""Ticket text steers the agent, so a ticket only runs if you were the last person to change it."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Change:
    at: str
    who: str
    human: bool = True


def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


def check(changes: list[Change], me: str, overrides: list[str]) -> str:
    """The reason to skip the ticket, or nothing. An override covers only what came before it."""
    people = [c for c in changes if c.human]
    if not people:
        return ""
    last = max(people, key=lambda c: _when(c.at))
    if last.who == me or any(_when(stamp) >= _when(last.at) for stamp in overrides):
        return ""
    return f"last changed by {last.who} at {last.at}. Read it, then comment or edit, or override it."
