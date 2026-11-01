import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.sources import GitHubSource, JiraSource, MarkdownSource, PullRequestSource, ReplySource  # noqa: E402
from workshop.tools import slug  # noqa: E402


class PullRequests(unittest.TestCase):
    def source(self, **pr):
        found = {"title": "T", "body": "B", "url": "u", "state": "OPEN", "isCrossRepository": False, "baseRefName": "main"} | pr
        patch = mock.patch("workshop.sources.run_json", return_value=found)
        self.addCleanup(patch.stop)
        self.gh = patch.start()
        return PullRequestSource(Path("/repo"))

    def test_an_open_pr_from_this_repo_can_run(self):
        self.assertEqual(self.source().guard("pr-7"), "")
        self.assertEqual(self.gh.call_args.args[:4], ("gh", "pr", "view", "7"))

    def test_forks_never_run(self):
        self.assertIn("fork", self.source(isCrossRepository=True).guard("pr-7"))

    def test_a_closed_pr_doesnt_run(self):
        self.assertIn("merged", self.source(state="MERGED").guard("pr-7"))

    def test_nothing_reaches_github(self):
        source = self.source()
        with mock.patch("workshop.sources.run") as run:
            source.claim("pr-7")
            source.report("pr-7", "hello")
            source.move("pr-7", "working")
            source.finish("pr-7")
        run.assert_not_called()
        self.gh.assert_not_called()


class Answering(unittest.TestCase):
    SAID = {"author": {"login": "me"}, "reviews": {"nodes": [{"state": "CHANGES_REQUESTED", "body": "Add a docstring", "author": {"login": "sam"}}, {"state": "APPROVED", "body": "", "author": {"login": "alex"}}]},
            "comments": {"nodes": [{"body": "Nice", "author": None}]},
            "reviewThreads": {"nodes": [{"isResolved": False, "path": "a.py", "line": 3, "comments": {"nodes": [{"databaseId": 11, "body": "Why?", "author": {"login": "sam"}}, {"databaseId": 12, "body": "Because", "author": {"login": "me"}}]}},
                                        {"isResolved": True, "path": "b.py", "line": 1, "comments": {"nodes": [{"databaseId": 13, "body": "Done with", "author": {"login": "sam"}}]}}]}}
    PR = {"title": "T", "body": "B", "url": "u", "state": "OPEN", "isCrossRepository": False, "baseRefName": "main", "author": {"login": "me"}}

    def source(self, **pr):
        for name, value in (("run_json", self.PR | pr), ("run", "me\n")):
            patch = mock.patch(f"workshop.sources.{name}", return_value=value)
            self.addCleanup(patch.stop)
            patch.start()
        return ReplySource(Path("/repo"))

    def test_the_brief_has_what_needs_an_answer_and_how_to_answer_it(self):
        source = self.source()
        with mock.patch.object(ReplySource, "conversation", return_value=self.SAID):
            body = source.get("reply-7").body
        self.assertIn("sam (changes_requested): Add a docstring", body)
        self.assertIn("ghost: Nice", body)
        self.assertIn("a.py:3, answered with comment 12", body)
        self.assertNotIn("Done with", body)

    def test_the_run_itself_posts_nothing(self):
        source = self.source()
        with mock.patch("workshop.sources.run") as run:
            source.claim("reply-7")
            source.report("reply-7", "Started")
            source.move("reply-7", "working")
            source.finish("reply-7")
        run.assert_not_called()

    def test_only_your_own_pr(self):
        self.assertEqual(self.source().guard("reply-7"), "")
        self.assertIn("isn't your PR", self.source(author={"login": "them"}).guard("reply-7"))

    def test_forks_never_run_here_either(self):
        self.assertIn("fork", self.source(isCrossRepository=True).guard("reply-7"))

    def test_a_reply_goes_to_its_thread_or_the_pr(self):
        source = self.source()
        with mock.patch("workshop.sources.run") as run:
            source.reply("reply-7", 12, "Fixed")
            self.assertIn("pulls/7/comments/12/replies", run.call_args.args[4])
            source.reply("reply-7", None, "Thanks")
            self.assertEqual(run.call_args.args[:3], ("gh", "pr", "comment"))


class Claims(unittest.TestCase):
    def test_each_action_is_one_call_as_me(self):
        for action, path, field in (("github-reviewer", "pulls/7/requested_reviewers", "reviewers[]=me"), ("github-assignee", "issues/7/assignees", "assignees[]=me")):
            with mock.patch("workshop.sources.run", return_value="me\n") as run:
                PullRequestSource(Path("/repo"), action).claim("pr-7")
            self.assertIn(path, run.call_args.args[4])
            self.assertEqual(run.call_args.args[6], field)

    def test_activity_is_other_people_since_then(self):
        pr = {"author": {"login": "them"}, "reviews": [{"author": {"login": "sam"}, "submittedAt": "2026-11-01T02:00:00Z", "state": "CHANGES_REQUESTED"}, {"author": {"login": "old"}, "submittedAt": "2026-10-01T02:00:00Z", "state": "APPROVED"}, {"author": {"login": "me"}, "submittedAt": "2026-11-01T02:00:00Z", "state": "COMMENTED"}],
              "comments": [{"author": {"login": "them"}, "createdAt": "2026-11-01T02:00:00Z"}, {"author": {"login": "alex"}, "createdAt": "2026-11-01T03:00:00Z"}]}
        since = datetime(2026, 11, 1, tzinfo=UTC).timestamp()
        with mock.patch("workshop.sources.run_json", return_value=pr), mock.patch("workshop.sources.run", return_value="me\n"):
            self.assertEqual(PullRequestSource(Path("/repo")).activity("pr-7", since), ["alex commented", "sam reviewed: changes requested"])


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

    def test_backlog_is_the_queued_intents(self):
        item = self.source.backlog()[0]
        self.assertEqual((item.key, item.title, item.queued), ("add-json", "Add JSON", True))
        self.source.claim("add-json")
        self.assertEqual(self.source.backlog(), [])

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
        options = {"issue/DE-1/transitions": {"transitions": [{"id": "7", "name": "Finish", "to": {"name": "Done"}}, {"id": "9", "name": "Pause", "to": {"name": "Blocked"}}]}}
        self.source(options).move("DE-1", "blocked")
        self.assertEqual(self.calls[-1], ("POST", "issue/DE-1/transitions", {"transition": {"id": "9"}}))

    def test_an_unmapped_stage_does_nothing_and_a_missing_transition_says_so(self):
        source = self.source({"issue/DE-1/transitions": {"transitions": []}})
        source.move("DE-1", "review")
        self.assertEqual(self.calls, [])
        with self.assertRaises(ValueError):
            source.move("DE-1", "blocked")

    def test_a_long_move_walks_the_route_to_the_status(self):
        source = JiraSource("etl", "ai", [], {"base_url": "https://x.test/", "email": "a@b", "token": "t", "moves": {"merged": "Ready"}, "route": ["In Progress", "Review", "Dev Done", "Ready"]})
        board = {"In Progress": ["Review"], "Review": ["Dev Done", "In Progress"], "Dev Done": ["Ready"]}
        at, walked = ["In Progress"], []

        def call(method, path, body=None):
            if method == "GET":
                return {"transitions": [{"id": to, "name": f"Go {to}", "to": {"name": to}} for to in board[at[0]]]}
            at[0] = body["transition"]["id"]
            walked.append(at[0])

        source._call = call
        source.move("DE-1", "merged")
        self.assertEqual(walked, ["Review", "Dev Done", "Ready"])

    def test_a_status_off_the_route_is_never_reached_by_walking_it(self):
        source = JiraSource("etl", "ai", [], {"base_url": "https://x.test/", "email": "a@b", "token": "t", "moves": {"blocked": "Blocked"}, "route": ["In Progress", "Review"]})
        posted = []
        source._call = lambda method, path, body=None: posted.append(body) if method == "POST" else {"transitions": [{"id": "1", "name": "Go", "to": {"name": "Review"}}]}
        with self.assertRaises(ValueError):
            source.move("DE-1", "blocked")
        self.assertEqual(posted, [])

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

    def test_a_stage_replaces_the_last_one_and_merged_closes_the_issue(self):
        calls = []

        def fake(*argv, cwd=None):
            calls.append(argv)
            return ""

        source = GitHubSource(Path("/r"), "ai")
        with mock.patch("workshop.sources.run_json", return_value={"labels": [{"name": "ai-review"}, {"name": "ai-claimed"}]}), mock.patch("workshop.sources.run", fake):
            source.move("3", "merged")
        self.assertIn(("gh", "issue", "edit", "3", "--add-label", "ai-merged", "--remove-label", "ai-review"), calls)
        self.assertEqual(calls[-1], ("gh", "issue", "close", "3"))


class Helpers(unittest.TestCase):
    def test_slug_makes_a_valid_agent_name(self):
        self.assertEqual(slug("DE-42"), "de-42")
        self.assertEqual(slug("Add JSON!"), "add-json")


if __name__ == "__main__":
    unittest.main()
