"""Numbers across every run, from the one event log. Cost is copied there at step end, so this outlives worktrees and transcripts."""

from collections import Counter, defaultdict
from typing import Any


def _table(title: str, rows: dict[str, list[dict[str, Any]]]) -> list[str]:
    lines = [title]
    for name, spent in sorted(rows.items()):
        priced = [e["usd"] for e in spent if e["usd"] is not None]
        cost = f"${sum(priced):.2f}" + ("" if len(priced) == len(spent) else " plus unpriced")
        minutes = sum(e["minutes"] for e in spent)
        lines.append(f"  {name}: {len(spent)} rounds, {sum(e['tokens_in'] for e in spent):,} tokens in, {sum(e['tokens_out'] for e in spent):,} out, {minutes:.1f} min, {cost}")
    return lines


def stats(events: list[dict[str, Any]]) -> str:
    spent = [e for e in events if e["type"] == "spent"]
    if not spent:
        return "Nothing recorded yet."
    by_step, by_repo, by_run, by_model, by_ticket = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for e in spent:
        by_step[e["step"]].append(e)
        by_repo[e["repo"]].append(e)
        by_run[(e["repo"], e["key"])].append(e)
        by_model[e.get("model") or "not recorded"].append(e)
        by_ticket[f"{e['repo']}/{e['key'].removeprefix('prep-')}"].append(e)  # preparing a ticket is part of what it cost
    done = {(e["repo"], e["key"]) for e in events if e["type"] == "finished"} & set(by_run)
    # A step that ran twice in a ticket means something sent it back. Stopping to ask a question doesn't.
    first_pass = sum(max(Counter(e["step"] for e in by_run[run] if not e.get("asked")).values()) == 1 for run in done)
    priced = [sum(e["usd"] or 0 for e in by_ticket[f"{repo}/{key}"]) for repo, key in done]
    lines = [f"Tickets finished: {len(done)}"]
    if done:
        lines += [f"First pass: {first_pass} of {len(done)}", f"Average cost per ticket (API-equivalent USD): ${sum(priced) / len(done):.2f}"]
    lines += _table("By step:", by_step) + _table("By model:", by_model) + _table("By repo:", by_repo) + _table("By ticket:", by_ticket)
    if parks := Counter(e["reason"][:70] for e in events if e["type"] == "parked"):
        lines += ["Why runs stopped:", *(f"  {n} x {reason}" for reason, n in parks.most_common(5))]
    return "\n".join(lines)
