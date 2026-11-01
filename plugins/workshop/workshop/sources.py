"""Where work comes from. Each source hands the runner the same Ticket."""

import base64
import json
import os
import re
import shutil
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .chat import Chat
from .guard import Change, check
from .pipeline import SECTIONS, STAGES
from .shell import run, run_json


@dataclass
class Ticket:
    key: str
    title: str
    body: str
    url: str = ""


@dataclass
class Comment:
    at: float
    mine: bool  # written, and any edit made, by the person running workshop
    who: str
    text: str


@dataclass
class Item:
    key: str
    title: str
    labels: list[str]
    queued: bool  # tagged for delivery
    url: str = ""


# Where a ticket has got to. Each source shows these its own way.
TICKET_MOVES = ("working", "blocked", "review", "approved", "preprod", "merged", "dropped")
ORIGINAL = "## Original ticket"
ORIGINAL_LINE = re.compile(rf"^{ORIGINAL}[ \t]*$", re.MULTILINE)
PREPARED = re.compile(rf"^## ({'|'.join(SECTIONS)})$", re.MULTILINE)


class Source(Protocol):
    def ready(self) -> list[str]: ...
    def get(self, key: str) -> Ticket: ...
    def claim(self, key: str) -> None: ...
    def report(self, key: str, text: str) -> None: ...
    def finish(self, key: str) -> None: ...
    def guard(self, key: str) -> str: ...
    def move(self, key: str, move: str) -> None: ...
    def comments(self, key: str, since: float) -> list[Comment]: ...
    def backlog(self) -> list[Item]: ...
    def closed(self, key: str) -> bool: ...


class EditableSource(Source, Protocol):
    def body(self, key: str) -> str: ...
    def rewrite(self, key: str, text: str) -> None: ...
    def tag(self, key: str, label: str) -> None: ...
    def labels(self, key: str) -> list[str]: ...
    def create(self, title: str, body: str) -> str: ...
    def close(self, key: str, why: str) -> None: ...


def prepared(body: str, section: str, text: str) -> str:
    """A ticket with one section written. What's prepared sits on top, so a rerun replaces it and the original stays once."""
    # GitHub hands back a web edit with CRLF, which would hide the headings and nest the sections.
    parts = ORIGINAL_LINE.split(body.replace("\r\n", "\n"), maxsplit=1)
    top, original = parts if len(parts) == 2 else ("", parts[0])
    parts = PREPARED.split(top)
    sections = dict(zip(parts[1::2], (part.strip() for part in parts[2::2]), strict=True)) | {section: text.strip()}
    written = [f"## {name}\n\n{sections[name]}" for name in SECTIONS if name in sections]
    return "\n\n".join([*written, f"{ORIGINAL}\n\n{original.strip()}"]).strip() + ("\n" if not original.strip() else "")


GITHUB_HISTORY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      createdAt lastEditedAt
      author { login __typename }
      editor { login __typename }
      blockedBy(first: 20) { nodes { number state } }
      comments(last: 100) { nodes {
        createdAt lastEditedAt body
        author { login __typename }
        editor { login __typename }
      } }
      timelineItems(last: 100, itemTypes: [LABELED_EVENT, RENAMED_TITLE_EVENT]) { nodes {
        __typename
        ... on LabeledEvent { createdAt label { name } actor { login __typename } }
        ... on RenamedTitleEvent { createdAt actor { login __typename } }
      } }
    }
  }
}
"""


def _stamp(at: str) -> float:
    return datetime.fromisoformat(at).timestamp()


def _gh_change(at: str | None, who: dict[str, str] | None) -> list[Change]:
    if not at or not who:
        return []
    return [Change(at, who["login"], who["__typename"] != "Bot")]


class GitHubSource:
    """Issues in the repo itself, picked up by label."""

    def __init__(self, repo: Path, label: str) -> None:
        self.repo, self.label = repo, label

    def ready(self) -> list[str]:
        issues = run_json("gh", "issue", "list", "--label", self.label, "--json", "number", cwd=self.repo)
        return sorted((str(i["number"]) for i in issues), key=int)

    def closed(self, key: str) -> bool:
        return run_json("gh", "issue", "view", key, "--json", "state", cwd=self.repo)["state"] == "CLOSED"

    def backlog(self) -> list[Item]:
        issues = run_json("gh", "issue", "list", "--state", "open", "--limit", "200", "--json", "number,title,labels,url", cwd=self.repo)
        items = [Item(str(i["number"]), i["title"], [n["name"] for n in i["labels"]], False, i["url"]) for i in issues]
        return [Item(i.key, i.title, i.labels, self.label in i.labels, i.url) for i in items if f"{self.label}-claimed" not in i.labels]

    def body(self, key: str) -> str:
        return run_json("gh", "issue", "view", key, "--json", "body", cwd=self.repo)["body"]

    def rewrite(self, key: str, text: str) -> None:
        run("gh", "issue", "edit", key, "--body", text, cwd=self.repo)

    def create(self, title: str, body: str) -> str:
        return run("gh", "issue", "create", "--title", title, "--body", body, cwd=self.repo).strip().rsplit("/", 1)[-1]

    def close(self, key: str, why: str) -> None:
        run("gh", "issue", "close", key, "--reason", "not planned", "--comment", why, cwd=self.repo)

    def tag(self, key: str, label: str) -> None:
        run("gh", "label", "create", label, "--force", cwd=self.repo)
        run("gh", "issue", "edit", key, "--add-label", label, cwd=self.repo)

    def labels(self, key: str) -> list[str]:
        return [n["name"] for n in run_json("gh", "issue", "view", key, "--json", "labels", cwd=self.repo)["labels"]]

    def get(self, key: str) -> Ticket:
        i = run_json("gh", "issue", "view", key, "--json", "title,body,url,comments", cwd=self.repo)
        comments = [f"{c['author']['login']}: {c['body']}" for c in i["comments"]]
        body = "\n\n".join([i["body"], *comments])
        return Ticket(key, i["title"], body, i["url"])

    def claim(self, key: str) -> None:
        # The claimed label is what stops the next tick picking it up again.
        claimed = f"{self.label}-claimed"
        run("gh", "label", "create", claimed, "--force", cwd=self.repo)
        run("gh", "issue", "edit", key, "--add-label", claimed, "--remove-label", self.label, cwd=self.repo)

    def report(self, key: str, text: str) -> None:
        run("gh", "issue", "comment", key, "--body", text, cwd=self.repo)

    def finish(self, key: str) -> None:
        pass

    def move(self, key: str, move: str) -> None:
        current = run_json("gh", "issue", "view", key, "--json", "labels", cwd=self.repo)["labels"]
        stale = [f"{self.label}-{s}" for s in TICKET_MOVES if s != move and f"{self.label}-{s}" in [c["name"] for c in current]]
        run("gh", "label", "create", f"{self.label}-{move}", "--force", cwd=self.repo)
        removals = [arg for name in stale for arg in ("--remove-label", name)]
        run("gh", "issue", "edit", key, "--add-label", f"{self.label}-{move}", *removals, cwd=self.repo)
        if move == "merged":
            run("gh", "issue", "close", key, cwd=self.repo)

    def comments(self, key: str, since: float) -> list[Comment]:
        issue, me = self._history(key)
        found = []
        for c in issue["comments"]["nodes"]:
            who = (c["author"] or {}).get("login", "")
            mine = who == me and (c["editor"] or {"login": me})["login"] == me
            if (at := _stamp(c["createdAt"])) > since:
                found.append(Comment(at, mine, who, c["body"]))
        return found

    def _history(self, key: str) -> tuple[dict[str, Any], str]:
        repo = run_json("gh", "repo", "view", "--json", "owner,name", cwd=self.repo)
        fields = ["-F", f"owner={repo['owner']['login']}", "-F", f"name={repo['name']}", "-F", f"number={key}"]
        issue = run_json("gh", "api", "graphql", "-f", f"query={GITHUB_HISTORY}", *fields, cwd=self.repo)
        me = run("gh", "api", "user", "--jq", ".login", cwd=self.repo).strip()
        return issue["data"]["repository"]["issue"], me

    def guard(self, key: str) -> str:
        issue, me = self._history(key)
        open_blockers = [str(b["number"]) for b in issue["blockedBy"]["nodes"] if b["state"] == "OPEN"]
        if open_blockers:
            return f"blocked by #{', #'.join(open_blockers)}."
        changes = _gh_change(issue["createdAt"], issue["author"]) + _gh_change(issue["lastEditedAt"], issue["editor"])
        for comment in issue["comments"]["nodes"]:
            changes += _gh_change(comment["createdAt"], comment["author"])
            changes += _gh_change(comment["lastEditedAt"], comment["editor"])
        overrides = []
        for item in issue["timelineItems"]["nodes"]:
            if item["__typename"] == "RenamedTitleEvent":
                changes += _gh_change(item["createdAt"], item["actor"])
            elif item["label"]["name"] == f"{self.label}-override" and item["actor"]["login"] == me:
                overrides.append(item["createdAt"])  # someone else's label counts for nothing
        return check(changes, me, overrides)


class JiraSource:
    """Tickets assigned to you, carrying both the label and the repo's name."""

    def __init__(self, repo_name: str, label: str, skip_statuses: list[str], cfg: dict[str, Any]) -> None:
        self.repo_name, self.label, self.skip = repo_name, label, skip_statuses
        self.moves, self.route, self.project = cfg.get("moves", {}), cfg.get("route", []), cfg.get("project", "")
        self.base = cfg["base_url"].rstrip("/")
        token = os.environ.get("JIRA_TOKEN") or cfg["token"]
        pair = f"{cfg['email']}:{token}".encode()
        self.auth = "Basic " + base64.b64encode(pair).decode()

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base}/rest/api/2/{path}", data=data, method=method)
        req.add_header("Authorization", self.auth)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else None

    def jql(self) -> str:
        parts = [
            f'labels = "{self.label}"',
            f'labels = "{self.repo_name}"',
            f'labels != "{self.label}-claimed"',
            "assignee = currentUser()",
            "statusCategory != Done",
        ]
        if self.skip:
            names = ", ".join(f'"{s}"' for s in self.skip)
            parts.append(f"status not in ({names})")
        return " AND ".join(parts) + " ORDER BY priority DESC, updated ASC"

    def ready(self) -> list[str]:
        query = urllib.parse.urlencode({"jql": self.jql(), "fields": "key", "maxResults": 20})
        return [i["key"] for i in self._call("GET", f"search/jql?{query}")["issues"]]

    def closed(self, key: str) -> bool:
        return self._call("GET", f"issue/{key}?fields=status")["fields"]["status"]["statusCategory"]["key"] == "done"

    def backlog(self) -> list[Item]:
        # The repo's label is the scope, so new tickets show before anyone is assigned.
        jql = f'labels = "{self.repo_name}" AND labels != "{self.label}-claimed" AND statusCategory != Done ORDER BY updated DESC'
        query = urllib.parse.urlencode({"jql": jql, "fields": "summary,labels", "maxResults": 100})
        issues = self._call("GET", f"search/jql?{query}")["issues"]
        return [Item(i["key"], i["fields"]["summary"], i["fields"]["labels"], self.label in i["fields"]["labels"], f"{self.base}/browse/{i['key']}") for i in issues]

    def body(self, key: str) -> str:
        return self._call("GET", f"issue/{key}?fields=description")["fields"].get("description") or ""

    def rewrite(self, key: str, text: str) -> None:
        self._call("PUT", f"issue/{key}", {"fields": {"description": text}})

    def create(self, title: str, body: str) -> str:
        if not self.project:
            raise KeyError("[jira] needs project to create tickets")
        fields = {"project": {"key": self.project}, "summary": title, "description": body, "issuetype": {"name": "Task"}, "labels": [self.repo_name]}
        return self._call("POST", "issue", {"fields": fields})["key"]

    def close(self, key: str, why: str) -> None:
        if "dropped" not in self.moves:
            raise ValueError("[jira.moves] needs a dropped status to close tickets")
        self.report(key, why)
        self.move(key, "dropped")

    def tag(self, key: str, label: str) -> None:
        self._call("PUT", f"issue/{key}", {"update": {"labels": [{"add": label}]}})

    def labels(self, key: str) -> list[str]:
        return self._call("GET", f"issue/{key}?fields=labels")["fields"]["labels"]

    def get(self, key: str) -> Ticket:
        f = self._call("GET", f"issue/{key}?fields=summary,description,comment")["fields"]
        comments = [f"{c['author']['displayName']}: {c['body']}" for c in f["comment"]["comments"]]
        body = "\n\n".join([f.get("description") or "", *comments])
        return Ticket(key, f["summary"], body, f"{self.base}/browse/{key}")

    def claim(self, key: str) -> None:
        labels = {"update": {"labels": [{"add": f"{self.label}-claimed"}]}}
        self._call("PUT", f"issue/{key}", labels)

    def report(self, key: str, text: str) -> None:
        self._call("POST", f"issue/{key}/comment", {"body": text})

    def finish(self, key: str) -> None:
        pass

    def move(self, key: str, move: str) -> None:
        target = self.moves.get(move)
        if not target:
            return
        # Boards often allow only the next column, so a longer move walks the route. Only forwards
        # towards a status on it: a ticket never steps along the route to reach one that isn't.
        before = self.route[: self.route.index(target)] if target in self.route else []
        for _ in range(len(self.route) + 1):
            options = self._call("GET", f"issue/{key}/transitions")["transitions"]
            direct = [t for t in options if target in (t["to"]["name"], t["name"])]
            onward = sorted((t for t in options if t["to"]["name"] in before), key=lambda t: before.index(t["to"]["name"]))
            if not direct and not onward:
                raise ValueError(f"{key} has no route to {target}")
            self._call("POST", f"issue/{key}/transitions", {"transition": {"id": (direct or onward[::-1])[0]["id"]}})
            if direct:
                return
        raise ValueError(f"{key} didn't reach {target}")

    def comments(self, key: str, since: float) -> list[Comment]:
        me = self._call("GET", "myself")["accountId"]
        found = []
        for c in self._call("GET", f"issue/{key}?fields=comment")["fields"]["comment"]["comments"]:
            mine = c["author"]["accountId"] == me and c["updateAuthor"]["accountId"] == me
            if (at := _stamp(c["created"])) > since:
                found.append(Comment(at, mine, c["author"]["displayName"], c["body"]))
        return found

    def _history(self, key: str) -> list[dict[str, Any]]:
        entries, start = [], 0
        while True:
            page = self._call("GET", f"issue/{key}/changelog?startAt={start}&maxResults=100")
            entries += page["values"]
            start += len(page["values"])
            if page.get("isLast", True) or not page["values"]:
                return entries

    def guard(self, key: str) -> str:
        me = self._call("GET", "myself")["accountId"]
        f = self._call("GET", f"issue/{key}?fields=creator,created,comment,issuelinks")["fields"]
        blockers = [
            link["inwardIssue"]["key"]
            for link in f.get("issuelinks", [])
            if link["type"]["inward"] == "is blocked by"
            and "inwardIssue" in link
            and link["inwardIssue"]["fields"]["status"]["statusCategory"]["key"] != "done"
        ]
        if blockers:
            return f"blocked by {', '.join(blockers)}."

        def person(user: dict[str, str]) -> tuple[str, bool]:
            return user["accountId"], user.get("accountType") != "app"  # automation and apps don't count

        changes = [Change(f["created"], *person(f["creator"]))]
        for comment in f["comment"]["comments"]:
            changes.append(Change(comment["created"], *person(comment["author"])))
            changes.append(Change(comment["updated"], *person(comment["updateAuthor"])))
        overrides = []
        for entry in self._history(key):
            for item in entry["items"]:
                if item["field"] in ("description", "summary"):
                    changes.append(Change(entry["created"], *person(entry["author"])))
                elif item["field"] == "labels" and f"{self.label}-override" in (item.get("toString") or "").split():
                    if entry["author"]["accountId"] == me:  # someone else's label counts for nothing
                        overrides.append(entry["created"])
        return check(changes, me, overrides)


class MarkdownSource:
    """Intent files in a folder. The first heading is the title."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def ready(self) -> list[str]:
        return sorted(p.stem for p in self.folder.glob("*.md"))

    def closed(self, key: str) -> bool:
        return False  # moving a file out of claimed/ isn't a signal anyone sends

    def backlog(self) -> list[Item]:
        return [Item(key, self.get(key).title, [], True, str(self.folder / f"{key}.md")) for key in self.ready()]

    def _claimed(self, key: str) -> Path:
        return self.folder / "claimed" / f"{key}.md"

    def _file(self, key: str) -> Path:
        path = self._claimed(key)
        return path if path.exists() else self.folder / f"{key}.md"

    def body(self, key: str) -> str:
        return self._file(key).read_text(encoding="utf-8")

    def rewrite(self, key: str, text: str) -> None:
        self._file(key).write_text(text, encoding="utf-8")

    def create(self, title: str, body: str) -> str:
        key = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        (self.folder / f"{key}.md").write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
        return key

    def close(self, key: str, why: str) -> None:
        (self.folder / "closed").mkdir(exist_ok=True)
        self._file(key).rename(self.folder / "closed" / f"{key}.md")

    def tag(self, key: str, label: str) -> None:
        pass  # a folder of files has no labels

    def labels(self, key: str) -> list[str]:
        return []

    def get(self, key: str) -> Ticket:
        body = self.body(key)
        heading = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        return Ticket(key, heading[1] if heading else key, body, str(self._file(key)))

    def claim(self, key: str) -> None:
        self._claimed(key).parent.mkdir(exist_ok=True)
        shutil.move(self.folder / f"{key}.md", self._claimed(key))

    def report(self, key: str, text: str) -> None:
        with (self.folder / "claimed" / f"{key}.log.md").open("a", encoding="utf-8") as log:
            log.write(text + "\n\n")

    def finish(self, key: str) -> None:
        done = self.folder / "done"
        done.mkdir(exist_ok=True)
        shutil.move(self._claimed(key), done / f"{key}.md")

    def move(self, key: str, move: str) -> None:
        pass

    def comments(self, key: str, since: float) -> list[Comment]:
        return []  # no conversation here: use workshop answer

    def guard(self, key: str) -> str:
        return ""  # local files you put there yourself


def _who(node: dict[str, Any]) -> str:
    return (node["author"] or {}).get("login", "ghost")


def number_of(key: str) -> str:
    return key.rsplit("-", 1)[-1]


# How a review shows as yours: the API path and field. Teams that assign reviewers themselves use none.
CLAIMS = {
    "none": ("", ""),
    "github-reviewer": ("repos/{owner}/{repo}/pulls/%s/requested_reviewers", "reviewers[]"),
    "github-assignee": ("repos/{owner}/{repo}/issues/%s/assignees", "assignees[]"),
    "chat-reaction": ("", ""),  # the eyes reaction on the message that asked
}


class PullRequestSource:
    """A PR under review. It only reads, except for the claim you chose: what else reaches GitHub is what you post from hunk."""

    def __init__(self, repo: Path, action: str = "none", chat: Chat | None = None) -> None:
        self.repo, self.action, self.chat = repo, action, chat

    def details(self, key: str) -> dict[str, Any]:
        return run_json("gh", "pr", "view", number_of(key), "--json", "title,body,url,state,isCrossRepository,baseRefName,author", cwd=self.repo)

    def guard(self, key: str) -> str:
        pr = self.details(key)
        if pr["isCrossRepository"]:
            return "it comes from a fork, and forks never run"
        return "" if pr["state"] == "OPEN" else f"it is {pr['state'].lower()}"

    def get(self, key: str) -> Ticket:
        pr = self.details(key)
        return Ticket(key, pr["title"], pr["body"], pr["url"])

    def closed(self, key: str) -> bool:
        return self.details(key)["state"] != "OPEN"

    def ready(self) -> list[str]:
        return []

    def backlog(self) -> list[Item]:
        return []

    def comments(self, key: str, since: float) -> list[Comment]:
        return []

    def claim(self, key: str) -> None:
        path, field_name = CLAIMS[self.action]
        if path:
            me = run("gh", "api", "user", "--jq", ".login").strip()
            run("gh", "api", "-X", "POST", path % number_of(key), "-f", f"{field_name}={me}", cwd=self.repo)
        if self.action == "chat-reaction" and self.chat:
            self.chat.react(self.details(key)["url"])

    def decided(self, key: str, since: float) -> str:
        """What your own review said, if you've posted one since then."""
        pr = run_json("gh", "pr", "view", number_of(key), "--json", "reviews", cwd=self.repo)
        me = run("gh", "api", "user", "--jq", ".login").strip()
        mine = [r for r in pr["reviews"] if _who(r) == me and r.get("submittedAt") and _stamp(r["submittedAt"]) >= since]
        return mine[-1]["state"] if mine else ""

    def activity(self, key: str, since: float) -> list[str]:
        """What other people did on the PR since then, to flag a review you haven't claimed."""
        pr = run_json("gh", "pr", "view", number_of(key), "--json", "author,reviews,comments", cwd=self.repo)
        people = {run("gh", "api", "user", "--jq", ".login").strip(), _who(pr)}
        done = [(_who(r), r["submittedAt"], f"reviewed: {r['state'].lower().replace('_', ' ')}") for r in pr["reviews"]]
        done += [(_who(c), c["createdAt"], "commented") for c in pr["comments"]]
        return sorted({f"{who} {what}" for who, at, what in done if who not in people and _stamp(at) > since})

    def report(self, key: str, text: str) -> None:
        pass

    def finish(self, key: str) -> None:
        pass

    def move(self, key: str, move: str) -> None:
        pass


REPLY_THREADS = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) { pullRequest(number: $number) {
    author { login }
    reviews(last: 30) { nodes { state body author { login } } }
    comments(last: 30) { nodes { body author { login } } }
    reviewThreads(first: 100) { nodes {
      isResolved path line
      comments(first: 20) { nodes { databaseId body author { login } } }
    } }
  } }
}
"""


class ReplySource(PullRequestSource):
    """Your own PR with comments to answer. Replies go out only through Engine.send_replies, on your go."""

    def conversation(self, key: str) -> dict[str, Any]:
        query = ["gh", "api", "graphql", "-F", "owner={owner}", "-F", "repo={repo}", "-F", f"number={number_of(key)}", "-f", f"query={REPLY_THREADS}"]
        return run_json(*query, cwd=self.repo)["data"]["repository"]["pullRequest"]

    def guard(self, key: str) -> str:
        if self.details(key)["author"]["login"] != run("gh", "api", "user", "--jq", ".login", cwd=self.repo).strip():
            return "it isn't your PR"
        return super().guard(key)

    def get(self, key: str) -> Ticket:
        pr, said = self.details(key), self.conversation(key)
        parts = [pr["body"]]
        reviews = [f"{_who(r)} ({r['state'].lower()}): {r['body']}" for r in said["reviews"]["nodes"] if r["body"].strip()]
        comments = [f"{_who(c)}: {c['body']}" for c in said["comments"]["nodes"]]
        if reviews or comments:
            parts += ["## On the PR, answered with comment null", *reviews, *comments]
        for thread in said["reviewThreads"]["nodes"]:
            if not thread["isResolved"]:
                notes = thread["comments"]["nodes"]
                parts += [f"## Thread on {thread['path']}:{thread['line'] or 'outdated'}, answered with comment {notes[-1]['databaseId']}", *(f"{_who(n)}: {n['body']}" for n in notes)]
        return Ticket(key, pr["title"], "\n\n".join(parts), pr["url"])

    def reply(self, key: str, comment: int | None, text: str) -> None:
        if comment is None:
            run("gh", "pr", "comment", number_of(key), "--body", text, cwd=self.repo)
        else:
            run("gh", "api", "-X", "POST", f"repos/{{owner}}/{{repo}}/pulls/{number_of(key)}/comments/{comment}/replies", "-f", f"body={text}", cwd=self.repo)


class PrepareSource:
    """A talk about a ticket. It reads the ticket, and changes it only through publish and signed, which your go calls."""

    def __init__(self, ticket: EditableSource, labels: dict[str, str]) -> None:
        self.ticket, self.names = ticket, labels

    def _key(self, key: str) -> str:
        return key.removeprefix("prep-")

    def _label(self, stage: str) -> str:
        return self.names.get(stage, stage)

    def guard(self, key: str) -> str:
        return "the ticket is closed." if self.ticket.closed(self._key(key)) else ""

    def get(self, key: str) -> Ticket:
        return self.ticket.get(self._key(key))

    def closed(self, key: str) -> bool:
        return self.ticket.closed(self._key(key))

    def stages(self, key: str) -> list[str]:
        """How far the ticket has been signed off, in the plain names pipelines use."""
        found = self.ticket.labels(self._key(key))
        return [stage for stage in STAGES if self._label(stage) in found]

    def publish(self, key: str, section: str, text: str, stage: str) -> None:
        ticket = self._key(key)
        self.ticket.rewrite(ticket, prepared(self.ticket.body(ticket), section, text))
        self.ticket.tag(ticket, self._label(stage))

    def signed(self, key: str, text: str) -> None:
        self.ticket.report(self._key(key), text)

    def ready(self) -> list[str]:
        return []

    def backlog(self) -> list[Item]:
        return []

    def comments(self, key: str, since: float) -> list[Comment]:
        return []

    def claim(self, key: str) -> None:
        pass

    def report(self, key: str, text: str) -> None:
        pass

    def finish(self, key: str) -> None:
        pass

    def move(self, key: str, move: str) -> None:
        pass


class SweepSource:
    """A scheduled look at a repo. It has no ticket of its own, so its brief is the backlog, and what it proposes goes through act, on your go."""

    def __init__(self, tickets: EditableSource, name: str, declined: list[str]) -> None:
        self.tickets, self.name, self.declined = tickets, name, declined

    def _open(self) -> list[Item]:
        return [item for item in self.tickets.backlog() if not item.queued]  # queued work is yours, so a sweep can't reach it

    def open_keys(self) -> list[str]:
        return [item.key for item in self._open()]

    def get(self, key: str) -> Ticket:
        tickets = [f"- {item.key}: {item.title}" for item in self._open()] or ["None."]
        declined = [f"- {title}" for title in self.declined] or ["None."]
        return Ticket(key, f"{self.name} sweep", "\n".join(["Open tickets:", *tickets, "", "Declined before:", *declined]))

    def create(self, title: str, body: str) -> str:
        return self.tickets.create(title, f"{body}\n\nProposed by workshop's {self.name} sweep.")

    def close_ticket(self, key: str, why: str) -> None:
        self.tickets.close(key, why)

    def comment(self, key: str, text: str) -> None:
        self.tickets.report(key, text)

    def guard(self, key: str) -> str:
        return ""

    def closed(self, key: str) -> bool:
        return False

    def ready(self) -> list[str]:
        return []

    def backlog(self) -> list[Item]:
        return []

    def comments(self, key: str, since: float) -> list[Comment]:
        return []

    def claim(self, key: str) -> None:
        pass

    def report(self, key: str, text: str) -> None:
        pass

    def finish(self, key: str) -> None:
        pass

    def move(self, key: str, move: str) -> None:
        pass


def make_source(name: str, repo: dict[str, Any], jira: dict[str, Any]) -> EditableSource:
    path = Path(repo["path"]).expanduser()
    label = repo.get("label", "ai")
    match repo["source"]:
        case "github":
            return GitHubSource(path, label)
        case "jira":
            return JiraSource(name, label, repo.get("skip_statuses", []), jira)
        case "markdown":
            return MarkdownSource(Path(repo["intents"]).expanduser())
    raise ValueError(f"{name}: unknown source {repo['source']}")
