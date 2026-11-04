"""Open tickets by how far they've been prepared. Polling costs no tokens, and the board only reads what this saves."""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .chat import CHAT
from .engine import STOPS
from .prs import fetch
from .sources import Item, make_source
from .store import SNAPSHOT, Store

COLUMNS = ("new", "planned", "designed", "queued")


def column(item: Item, labels: dict[str, str]) -> str:
    if item.queued:
        return "queued"
    if labels.get("designed", "designed") in item.labels:
        return "designed"
    return "planned" if labels.get("planned", "planned") in item.labels else "new"


def asked_in_chat(store: Store) -> dict[str, float]:
    return {url: float(found["ts"]) for url, found in store.read(CHAT).get("found", {}).items() if not found.get("taken")}


def waiting_since(items: list[dict[str, Any]], before: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    """When each PR became yours to move. GitHub's updated time moves with any comment, yours included, so it can't say how long something has waited."""
    earlier = {pr["url"]: pr.get("since", "") for pr in before if pr.get("move") == "you"}
    stamp = datetime.fromtimestamp(now, UTC).isoformat()
    return [{**pr, "since": earlier.get(pr["url"]) or stamp} if pr.get("move") == "you" else pr for pr in items]


def poll(cfg: dict[str, Any], store: Store, clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """One repo failing keeps its last good list, so the board shows it stale, not empty."""
    previous = store.read(SNAPSHOT)
    before = previous.get("repos", {})
    labels = cfg.get("backlog", {})
    repos: dict[str, Any] = {}
    for name, repo in cfg["repos"].items():
        try:
            items = make_source(name, repo, cfg.get("jira", {})).backlog()
            repos[name] = {"polled_at": clock(), "error": "", "items": [{"key": i.key, "title": i.title, "url": i.url, "column": column(i, labels)} for i in items]}
        except STOPS as failed:
            repos[name] = {"polled_at": 0.0, "items": [], **before.get(name, {}), "error": str(failed)}
    try:
        prs = {"polled_at": clock(), "error": "", "items": waiting_since(fetch(cfg.get("prs", {}).get("search", ""), clock(), asked_in_chat(store), cfg.get("prs", {}).get("intake") == "chat"), previous.get("prs", {}).get("items", []), clock())}
    except STOPS as failed:
        prs = {"polled_at": 0.0, "items": [], **previous.get("prs", {}), "error": str(failed)}
    snapshot = {"repos": repos, "prs": prs}
    store.write(SNAPSHOT, snapshot)
    return snapshot
