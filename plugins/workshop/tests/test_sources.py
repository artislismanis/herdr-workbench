import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.sources import GitHubSource, JiraSource, MarkdownSource  # noqa: E402
from workshop.tools import slug  # noqa: E402


class Markdown(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.folder = Path(self.dir.name)
        (self.folder / "add-json.md").write_text("# Add JSON\n\nDetails\n", encoding="utf-8")
        self.source = MarkdownSource(self.folder)

    def test_ready_lists_unclaimed_intents(self):
        self.assertEqual(self.source.ready(), ["add-json"])

    def test_title_comes_from_the_first_heading(self):
        self.assertEqual(self.source.get("add-json").title, "Add JSON")

    def test_a_claimed_intent_is_no_longer_ready_but_stays_readable(self):
        self.source.claim("add-json")
        self.assertEqual(self.source.ready(), [])
        self.assertEqual(self.source.get("add-json").title, "Add JSON")

    def test_report_and_finish(self):
        self.source.claim("add-json")
        self.source.report("add-json", "Draft PR ready")
        self.source.finish("add-json")
        self.assertTrue((self.folder / "done" / "add-json.md").exists())
        self.assertIn("Draft PR", (self.folder / "claimed" / "add-json.log.md").read_text(encoding="utf-8"))


class Jira(unittest.TestCase):
    def source(self, skip):
        return JiraSource("etl", "ai", skip, {"base_url": "https://x.test/", "email": "a@b", "token": "t"})

    def test_jql_needs_both_labels_and_excludes_claimed(self):
        jql = self.source([]).jql()
        for part in ('labels = "ai"', 'labels = "etl"', 'labels != "ai-claimed"', "assignee = currentUser()"):
            self.assertIn(part, jql)
        self.assertNotIn("status not in", jql)

    def test_jql_skips_listed_statuses(self):
        self.assertIn('status not in ("Backlog", "Icebox")', self.source(["Backlog", "Icebox"]).jql())


class JiraBoard(unittest.TestCase):
    def source(self, responses):
        source = JiraSource("etl", "ai", [], {"base_url": "https://x.test/", "email": "a@b", "token": "t", "moves": {"blocked": "Blocked"}})
        self.calls = []

        def call(method, path, body=None):
            self.calls.append((method, path, body))
            return responses.get(path)

        source._call = call
        return source

    def test_a_move_takes_the_transition_to_the_mapped_status(self):
        options = {"issue/DE-1/transitions": {"transitions": [{"id": "7", "to": {"name": "Done"}}, {"id": "9", "to": {"name": "Blocked"}}]}}
        self.source(options).move("DE-1", "blocked")
        self.assertEqual(self.calls[-1], ("POST", "issue/DE-1/transitions", {"transition": {"id": "9"}}))

    def test_an_unmapped_stage_does_nothing_and_a_missing_transition_says_so(self):
        source = self.source({"issue/DE-1/transitions": {"transitions": []}})
        source.move("DE-1", "review")
        self.assertEqual(self.calls, [])
        with self.assertRaises(ValueError):
            source.move("DE-1", "blocked")

    def test_a_comment_is_mine_only_if_nobody_else_touched_it(self):
        def comment(author, editor, body, at="2026-10-31T10:00:00.000+0000"):
            return {"author": {"accountId": author, "displayName": author}, "updateAuthor": {"accountId": editor}, "body": body, "created": at}

        fields = {"comment": {"comments": [comment("me", "me", "old", "2026-10-30T10:00:00.000+0000"), comment("me", "me", "mine"), comment("me", "them", "edited"), comment("them", "them", "theirs")]}}
        source = self.source({"myself": {"accountId": "me"}, "issue/DE-1?fields=comment": {"fields": fields}})
        since = datetime(2026, 10, 31, tzinfo=UTC).timestamp()
        self.assertEqual([(c.text, c.mine) for c in source.comments("DE-1", since)], [("mine", True), ("edited", False), ("theirs", False)])


class GitHubBoard(unittest.TestCase):
    def test_a_comment_someone_else_edited_isnt_mine(self):
        def comment(author, editor, body):
            return {"createdAt": "2026-10-31T10:00:00Z", "author": {"login": author}, "editor": editor and {"login": editor}, "body": body}

        source = GitHubSource(Path("/r"), "ai")
        issue = {"comments": {"nodes": [comment("me", None, "mine"), comment("me", "them", "edited"), comment("them", None, "theirs")]}}
        with mock.patch.object(source, "_history", return_value=(issue, "me")):
            found = source.comments("3", 0)
        self.assertEqual([c.mine for c in found], [True, False, False])

    def test_a_stage_replaces_the_last_one_and_done_closes_the_issue(self):
        calls = []

        def fake(*argv, cwd=None):
            calls.append(argv)
            return ""

        source = GitHubSource(Path("/r"), "ai")
        with mock.patch("workshop.sources.run_json", return_value={"labels": [{"name": "ai-review"}, {"name": "ai-claimed"}]}), mock.patch("workshop.sources.run", fake):
            source.move("3", "done")
        self.assertIn(("gh", "issue", "edit", "3", "--add-label", "ai-done", "--remove-label", "ai-review"), calls)
        self.assertEqual(calls[-1], ("gh", "issue", "close", "3"))


class Helpers(unittest.TestCase):
    def test_slug_makes_a_valid_agent_name(self):
        self.assertEqual(slug("DE-42"), "de-42")
        self.assertEqual(slug("Add JSON!"), "add-json")


if __name__ == "__main__":
    unittest.main()
