import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.runner import needs_you, slug  # noqa: E402
from workshop.sources import JiraSource, MarkdownSource  # noqa: E402


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


class Helpers(unittest.TestCase):
    def test_slug_makes_a_valid_agent_name(self):
        self.assertEqual(slug("DE-42"), "de-42")
        self.assertEqual(slug("Add JSON!"), "add-json")

    def test_needs_you_stops_at_the_verdict(self):
        report = "Fine\n## Needs you\n- pick a name\n\nVERDICT: PASS\n"
        self.assertEqual(needs_you(report), "Needs you:\n- pick a name")

    def test_an_empty_needs_you_says_nothing(self):
        self.assertEqual(needs_you("## Needs you\n\nVERDICT: PASS\n"), "")


if __name__ == "__main__":
    unittest.main()
