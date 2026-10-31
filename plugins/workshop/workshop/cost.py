"""What each step spent, from Claude Code's transcripts: agents run in panes, so no result event reports it. On a subscription the dollars are the API equivalent, not a bill."""

import json
import os
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

# USD per million tokens: input, 5-minute cache write, 1-hour cache write, cache read, output.
# From platform.claude.com/docs/en/about-claude/pricing, 2026-10-31. A model not listed shows as unpriced.
RATES = {
    "claude-fable-5-1": (10, 12.5, 20, 0.25, 50),
    "claude-fable-5": (10, 12.5, 20, 1, 50),
    "claude-opus-5-5": (4, 5, 8, 0.2, 20),
    "claude-opus-5": (5, 6.25, 10, 0.5, 25),
    "claude-opus-4-8": (5, 6.25, 10, 0.5, 25),
    "claude-sonnet-5-5": (2, 2.5, 4, 0.1, 10),
    "claude-sonnet-5": (2, 2.5, 4, 0.2, 10),
    "claude-sonnet-4-6": (3, 3.75, 6, 0.3, 15),
    "claude-haiku-5-5": (0.1, 0.125, 0.2, 0.01, 0.5),
    "claude-haiku-4-5": (1, 1.25, 2, 0.1, 5),
}
HAIKU_LONG = 100_000  # Haiku 5.5 costs five times as much for a prompt over this


def transcripts(work: Path) -> Path:
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()
    return config / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", str(work))


def usd(model: str, usage: dict[str, Any]) -> float | None:
    if model not in RATES:
        return None
    written = usage.get("cache_creation_input_tokens", 0)
    long_writes = usage.get("cache_creation", {}).get("ephemeral_1h_input_tokens", 0)
    counts = (usage.get("input_tokens", 0), written - long_writes, long_writes, usage.get("cache_read_input_tokens", 0), usage.get("output_tokens", 0))
    total = sum(n * rate for n, rate in zip(counts, RATES[model], strict=True)) / 1_000_000
    return total * 5 if model == "claude-haiku-5-5" and sum(counts[:4]) > HAIKU_LONG else total


def messages(folder: Path, since: float, until: float) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for path in folder.glob("**/*.jsonl"):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
                if entry.get("type") == "assistant" and since <= datetime.fromisoformat(entry["timestamp"]).timestamp() <= until:
                    # One message spans several lines; the last holds its final count.
                    found[entry["message"]["id"]] = entry["message"]
            except (ValueError, KeyError, TypeError):
                continue
    return found


def spent(work: Path, since: float, until: float) -> dict[str, Any]:
    sent = list(messages(transcripts(work), since, until).values())
    prices = [usd(m.get("model", ""), m.get("usage", {})) for m in sent]
    fields = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return {
        "turns": len(sent),
        "tokens_in": sum(m.get("usage", {}).get(f, 0) for m in sent for f in fields),
        "tokens_out": sum(m.get("usage", {}).get("output_tokens", 0) for m in sent),
        "minutes": round((until - since) / 60, 1),
        "usd": None if None in prices else round(sum(p or 0 for p in prices), 4),
    }


def line(label: str, s: dict[str, Any]) -> str:
    cost = "unpriced" if s["usd"] is None else f"${s['usd']:.2f}"
    return f"{label}: {s['turns']} turns, {s['tokens_in']:,} tokens in, {s['tokens_out']:,} out, {s['minutes']:.1f} min, {cost}"


def summary(events: Iterable[dict[str, Any]]) -> str:
    rounds = [e for e in events if e["type"] == "spent"]
    if not rounds:
        return ""
    prices = [e["usd"] for e in rounds]
    total = {k: sum(e[k] for e in rounds) for k in ("turns", "tokens_in", "tokens_out", "minutes")}
    total["usd"] = None if None in prices else sum(prices)
    return "\n".join(["Cost (API-equivalent USD):", *(line(e["step"], e) for e in rounds), line("Total", total)])
