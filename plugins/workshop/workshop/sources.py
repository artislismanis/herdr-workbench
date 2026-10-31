"""Where work comes from. Each source hands the runner the same Ticket."""

import base64
import json
import os
import re
import shutil
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .shell import run, run_json


@dataclass
class Ticket:
    key: str
    title: str
    body: str
    url: str = ""


class Source(Protocol):
    def ready(self) -> list[str]: ...
    def get(self, key: str) -> Ticket: ...
    def claim(self, key: str) -> None: ...
    def report(self, key: str, text: str) -> None: ...
    def finish(self, key: str) -> None: ...


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


class JiraSource:
    """Tickets assigned to you, carrying both the label and the repo's name."""

    def __init__(self, repo_name: str, label: str, skip_statuses: list[str], cfg: dict[str, str]) -> None:
        self.repo_name, self.label, self.skip = repo_name, label, skip_statuses
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


def make_source(name: str, repo: dict[str, Any], jira: dict[str, str]) -> Source:
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
