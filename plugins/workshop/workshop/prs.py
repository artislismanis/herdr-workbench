"""Open PRs by whose move it is. One GraphQL call covers them all, and the board only reads what this saves."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from .shell import run_json

RECENT_DAYS = 7
COMMENTS = 10
FAILING = ("FAILURE", "ERROR")
FIELDS = """
fragment pr on PullRequest {
  number title url isDraft updatedAt state
  author { login }
  repository { name }
  commits(last: 1) { nodes { commit { oid statusCheckRollup { state } } } }
  reviews(last: 50) { nodes { state body createdAt author { login __typename } commit { oid } } }
  reviewRequests(first: 20) { nodes { requestedReviewer { ... on User { login } ... on Team { name } } } }
  reviewThreads(first: 100) { nodes { isResolved path line comments(first: 5) { nodes { body author { login } } } } }
  comments(last: 10) { nodes { body createdAt author { login } } }
}
"""


def searches(scope: str, now: float) -> dict[str, tuple[str, int]]:
    since = (datetime.fromtimestamp(now, UTC) - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")
    merged = f"is:pr is:merged merged:>={since} sort:updated-desc {scope}"
    return {
        "mine": (f"is:open is:pr author:@me {scope}", 50),
        "requested": (f"is:open is:pr review-requested:@me {scope}", 50),
        "reviewed": (f"is:open is:pr reviewed-by:@me -author:@me {scope}", 50),
        "merged_mine": (f"{merged} author:@me", 5),
        "merged_reviewed": (f"{merged} reviewed-by:@me -author:@me", 5),
    }


def latest_reviews(pr: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Each person's standing review. As on GitHub, a later comment doesn't clear an approval or a block. Bots advise, and dismissed or unsent reviews don't count."""
    found: dict[str, dict[str, Any]] = {}
    for review in pr["reviews"]["nodes"]:
        if not review["author"] or review["author"]["__typename"] == "Bot" or review["state"] in ("PENDING", "DISMISSED"):
            continue
        who = review["author"]["login"]
        if review["state"] != "COMMENTED" or found.get(who, review)["state"] == "COMMENTED":
            found[who] = review
    return found


def waiting_on(pr: dict[str, Any]) -> list[str]:
    asked = [n["requestedReviewer"] or {} for n in pr["reviewRequests"]["nodes"]]
    return [person.get("login") or person.get("name", "") for person in asked]


def note(node: dict[str, Any]) -> dict[str, str]:
    return {"who": (node["author"] or {}).get("login", "ghost"), "text": node["body"]}  # an account can be deleted


def threads_of(pr: dict[str, Any]) -> list[dict[str, Any]]:
    found = [{"path": t["path"], "line": t["line"], "resolved": t["isResolved"], "said": [note(c) for c in t["comments"]["nodes"]]} for t in pr["reviewThreads"]["nodes"]]
    return sorted(found, key=lambda t: t["resolved"])


def comments_of(pr: dict[str, Any]) -> list[dict[str, str]]:
    """The conversation on the PR itself: its comments and what reviews said overall, newest last."""
    spoken = [n for n in pr["comments"]["nodes"] + pr["reviews"]["nodes"] if n["body"].strip()]
    return [note(n) for n in sorted(spoken, key=lambda n: n["createdAt"])[-COMMENTS:]]


def reviewed_commit(review: dict[str, Any]) -> str:
    return (review["commit"] or {}).get("oid", "")  # a force-push can lose the commit


def my_move(pr: dict[str, Any], reviews: dict[str, dict[str, Any]], head: str, failing: bool, threads: int) -> tuple[str, str]:
    asked = {who: r for who, r in reviews.items() if r["state"] == "CHANGES_REQUESTED"}
    if pr["isDraft"]:
        return "you", "draft"
    if failing:
        return "you", "checks failing"
    if asked:
        if all(reviewed_commit(r) == head for r in asked.values()):
            return "you", "changes requested"
        return ("others", "re-review requested") if set(asked) & set(waiting_on(pr)) else ("you", "ask for re-review")
    if threads:
        return "you", "comments to address"
    if any(r["state"] == "APPROVED" for r in reviews.values()):
        return "you", "approved"
    return "others", "awaiting review"


def their_move(pr: dict[str, Any], reviews: dict[str, dict[str, Any]], head: str, me: str, requested: bool) -> tuple[str, str]:
    mine = reviews.get(me)
    if pr["isDraft"]:
        return "others", "draft"
    if requested:
        return "you", "re-review" if mine else "needs review"
    if mine is None:
        return "others", "open"
    if mine["state"] == "APPROVED":
        return "others", "you approved"
    # Without a new request a push may still be work in progress, so it stays with them.
    return "others", "new commits" if reviewed_commit(mine) != head else "waiting for fixes"


def classify(pr: dict[str, Any], me: str, requested: bool = False, merged: bool = False) -> dict[str, Any]:
    commit = pr["commits"]["nodes"][-1]["commit"]
    head, checks = commit["oid"], (commit["statusCheckRollup"] or {}).get("state", "")
    reviews = latest_reviews(pr)
    threads = sum(not t["isResolved"] for t in pr["reviewThreads"]["nodes"])
    author = (pr["author"] or {}).get("login", "")
    mine = author == me
    if merged:
        move, state = "done", "merged"
    elif mine:
        move, state = my_move(pr, reviews, head, checks in FAILING, threads)
    else:
        move, state = their_move(pr, reviews, head, me, requested)
    # Someone else got there before you did: the sign to drop a silent review, or make it a second opinion.
    beaten = sorted(who for who in reviews if who not in (me, author)) if state == "needs review" else []
    return {
        "repo": pr["repository"]["name"],
        "number": pr["number"],
        "title": pr["title"],
        "url": pr["url"],
        "author": author,
        "me": me,
        "mine": mine,
        "move": move,
        "state": state,
        "checks": checks,
        "threads": threads,
        "reviews": {who: r["state"] for who, r in reviews.items()},
        "waiting_on": waiting_on(pr),
        "beaten": beaten,
        "discussion": threads_of(pr),
        "comments": comments_of(pr),
        "updated": pr["updatedAt"],
    }


def fetch(scope: str, now: float, asked: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Asked is PRs someone asked you about elsewhere, which GitHub can't search for."""
    parts = searches(scope, now)
    body = "".join(f"{alias}: search(query: {json.dumps(query)}, type: ISSUE, first: {size}) {{ nodes {{ ...pr }} }}\n" for alias, (query, size) in parts.items())
    body += "".join(f"asked{n}: resource(url: {json.dumps(url)}) {{ ...pr }}\n" for n, url in enumerate(asked))
    data = run_json("gh", "api", "graphql", "-f", f"query=query {{ viewer {{ login }}\n{body}}}\n{FIELDS}")["data"]
    me = data["viewer"]["login"]
    requested = {pr["url"] for pr in data["requested"]["nodes"]}
    found: dict[str, dict[str, Any]] = {}
    for alias in parts:
        merged = alias.startswith("merged")
        for pr in data[alias]["nodes"]:
            found[pr["url"]] = classify(pr, me, pr["url"] in requested, merged)
    for n, url in enumerate(asked):
        pr = data[f"asked{n}"]
        if pr and pr["state"] == "OPEN":
            found[pr["url"]] = classify(pr, me, True) | {"chat": True}
    return sorted(found.values(), key=lambda pr: pr["updated"], reverse=True)
