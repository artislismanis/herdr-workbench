import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop import __main__ as cli  # noqa: E402
from workshop.backlog import SNAPSHOT, column, poll  # noqa: E402
from workshop.shell import ToolError  # noqa: E402
from workshop.sources import GitHubSource, Item, JiraSource  # noqa: E402
from workshop.store import Store  # noqa: E402


def item(*labels, queued=False):
    return Item("1", "A ticket", list(labels), queued)


class Stages(unittest.TestCase):
    def test_the_furthest_label_wins(self):
        self.assertEqual(column(item(), {}), "new")
        self.assertEqual(column(item("planned"), {}), "planned")
        self.assertEqual(column(item("planned", "designed"), {}), "designed")
        self.assertEqual(column(item("designed", queued=True), {}), "queued")

    def test_labels_can_be_renamed(self):
        self.assertEqual(column(item("spec-ok"), {"designed": "spec-ok"}), "designed")
        self.assertEqual(column(item("designed"), {"designed": "spec-ok"}), "new")


class Poll(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name))
        self.cfg = {"repos": {"a": {}, "b": {}}}
        self.lists = {"a": [item("planned")], "b": [item()]}
        self.prs = [{"url": "u"}]

    def poll(self, at=100.0):
        def source(name, repo, jira):
            found = self.lists[name]
            if isinstance(found, Exception):
                raise found
            return mock.Mock(backlog=lambda: found)

        def prs(scope, now, asked=()):
            if isinstance(self.prs, Exception):
                raise self.prs
            self.scope = scope
            return self.prs

        with mock.patch("workshop.backlog.make_source", source), mock.patch("workshop.backlog.fetch", prs):
            return poll(self.cfg, self.store, lambda: at)

    def test_saves_each_repos_tickets_with_their_stage(self):
        self.poll()
        saved = json.loads((self.store.root / SNAPSHOT).read_text(encoding="utf-8"))
        self.assertEqual(saved["repos"]["a"], {"polled_at": 100.0, "error": "", "items": [{"key": "1", "title": "A ticket", "url": "", "column": "planned"}]})

    def test_a_failed_repo_keeps_its_last_list_and_says_why(self):
        self.poll()
        self.lists["a"] = ToolError("gh issue failed")
        repos = self.poll(200.0)["repos"]
        self.assertEqual((repos["a"]["polled_at"], len(repos["a"]["items"]), repos["a"]["error"]), (100.0, 1, "gh issue failed"))
        self.assertEqual(repos["b"]["polled_at"], 200.0)

    def test_saves_prs_and_a_failure_keeps_the_last_list(self):
        self.cfg["prs"] = {"search": "org:x"}
        self.assertEqual(self.poll()["prs"], {"polled_at": 100.0, "error": "", "items": [{"url": "u"}]})
        self.assertEqual(self.scope, "org:x")
        self.prs = ToolError("gh api failed")
        self.assertEqual(self.poll(200.0)["prs"], {"polled_at": 100.0, "error": "gh api failed", "items": [{"url": "u"}]})

    def test_a_repo_that_never_worked_shows_its_error(self):
        self.lists["a"] = OSError("no network")
        self.assertEqual(self.poll()["repos"]["a"], {"polled_at": 0.0, "items": [], "error": "no network"})


class GitHub(unittest.TestCase):
    def test_lists_open_issues_and_leaves_out_claimed_ones(self):
        issues = [{"number": 1, "title": "x", "url": "u", "labels": [{"name": "ai"}]}, {"number": 2, "title": "y", "url": "v", "labels": [{"name": "ai-claimed"}]}, {"number": 3, "title": "z", "url": "w", "labels": []}]
        with mock.patch("workshop.sources.run_json", return_value=issues):
            found = GitHubSource(Path("."), "ai").backlog()
        self.assertEqual([(i.key, i.queued) for i in found], [("1", True), ("3", False)])


class Jira(unittest.TestCase):
    def test_the_repo_label_sets_the_scope(self):
        source = JiraSource("etl", "ai", [], {"base_url": "https://x.test/", "email": "a@b", "token": "t"})
        issue = {"key": "DE-1", "fields": {"summary": "x", "labels": ["etl", "ai"]}}
        with mock.patch.object(source, "_call", return_value={"issues": [issue]}) as call:
            found = source.backlog()
        self.assertIn("labels+%3D+%22etl%22", call.call_args.args[1])
        self.assertNotIn("assignee", call.call_args.args[1])
        self.assertEqual((found[0].key, found[0].queued, found[0].url), ("DE-1", True, "https://x.test/browse/DE-1"))


class Tick(unittest.TestCase):
    def test_a_full_workshop_still_polls(self):
        engine = mock.Mock()
        engine.store.all.return_value = [mock.Mock(status="running", blocked_at=0)]
        with mock.patch.object(cli, "poll") as polled:
            cli.tick(engine, {"repos": {}, "max_active": 1})
        polled.assert_called_once()


class History(unittest.TestCase):
    def test_a_half_written_line_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            (store.root / "events.jsonl").write_text('{"type": "a"}\n{"type": "b', encoding="utf-8")
            self.assertEqual(store.history(), [{"type": "a"}])


if __name__ == "__main__":
    unittest.main()
