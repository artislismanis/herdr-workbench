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

from .guard import Change, check
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


# Where a ticket has got to. Each source shows these its own way.
STAGES = ("working", "blocked", "review", "approved", "merged")


class Source(Protocol):
    def ready(self) -> list[str]: ...
    def get(self, key: str) -> Ticket: ...
    def claim(self, key: str) -> None: ...
    def report(self, key: str, text: str) -> None: ...
    def finish(self, key: str) -> None: ...
    def guard(self, key: str) -> str: ...
    def move(self, key: str, stage: str) -> None: ...
    def comments(self, key: str, since: float) -> list[Comment]: ...


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

    def move(self, key: str, stage: str) -> None:
        current = run_json("gh", "issue", "view", key, "--json", "labels", cwd=self.repo)["labels"]
        stale = [f"{self.label}-{s}" for s in STAGES if s != stage and f"{self.label}-{s}" in [c["name"] for c in current]]
        run("gh", "label", "create", f"{self.label}-{stage}", "--force", cwd=self.repo)
        removals = [arg for name in stale for arg in ("--remove-label", name)]
        run("gh", "issue", "edit", key, "--add-label", f"{self.label}-{stage}", *removals, cwd=self.repo)
        if stage == "merged":
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
        self.moves, self.route = cfg.get("moves", {}), cfg.get("route", [])
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

    def move(self, key: str, stage: str) -> None:
        target = self.moves.get(stage)
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

    def _claimed(self, key: str) -> Path:
        return self.folder / "claimed" / f"{key}.md"

    def get(self, key: str) -> Ticket:
        path = self._claimed(key)
        if not path.exists():
            path = self.folder / f"{key}.md"
        body = path.read_text(encoding="utf-8")
        heading = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        return Ticket(key, heading[1] if heading else key, body, str(path))

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

    def move(self, key: str, stage: str) -> None:
        pass

    def comments(self, key: str, since: float) -> list[Comment]:
        return []  # no conversation here: use workshop answer

    def guard(self, key: str) -> str:
        return ""  # local files you put there yourself


def make_source(name: str, repo: dict[str, Any], jira: dict[str, Any]) -> Source:
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
