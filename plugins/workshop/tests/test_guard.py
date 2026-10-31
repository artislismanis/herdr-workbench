import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.guard import Change, check  # noqa: E402
from workshop.sources import GitHubSource, JiraSource  # noqa: E402

T1, T2, T3 = "2026-10-01T10:00:00Z", "2026-10-02T10:00:00Z", "2026-10-03T10:00:00Z"


class Check(unittest.TestCase):
    def test_passes_when_you_were_last(self):
        self.assertEqual(check([Change(T1, "bob"), Change(T2, "me")], "me", []), "")

    def test_stops_when_a_colleague_was_last(self):
        self.assertIn("bob", check([Change(T1, "me"), Change(T2, "bob")], "me", []))

    def test_automation_doesnt_count(self):
        self.assertEqual(check([Change(T1, "me"), Change(T2, "jira", human=False)], "me", []), "")

    def test_an_override_covers_earlier_changes(self):
        self.assertEqual(check([Change(T1, "bob")], "me", [T2]), "")

    def test_a_later_edit_cancels_the_override(self):
        self.assertIn("bob", check([Change(T1, "bob"), Change(T3, "bob")], "me", [T2]))

    def test_mixed_time_formats_compare(self):
        changes = [Change("2026-10-01T10:00:00.000+0000", "bob"), Change(T2, "me")]
        self.assertEqual(check(changes, "me", []), "")


def person(login, kind="User"):
    return {"login": login, "__typename": kind}


def github(*, comments=(), timeline=(), blocked=()):
    return {"data": {"repository": {"issue": {
        "createdAt": T1, "lastEditedAt": None, "author": person("me"), "editor": None,
        "blockedBy": {"nodes": list(blocked)},
        "comments": {"nodes": list(comments)}, "timelineItems": {"nodes": list(timeline)},
    }}}}


class GitHub(unittest.TestCase):
    def guard(self, payload):
        source = GitHubSource(Path("."), "ai")
        repo = {"owner": {"login": "o"}, "name": "r"}
        with mock.patch("workshop.sources.run_json", side_effect=[repo, payload]), mock.patch("workshop.sources.run", return_value="me\n"):
            return source.guard("7")

    def comment(self, who, kind="User"):
        return {"createdAt": T2, "lastEditedAt": None, "author": person(who, kind), "editor": None}

    def label(self, name, who, at=T3):
        return {"__typename": "LabeledEvent", "createdAt": at, "label": {"name": name}, "actor": person(who)}

    def test_your_own_ticket_passes(self):
        self.assertEqual(self.guard(github()), "")

    def test_a_colleagues_comment_stops_it(self):
        self.assertIn("bob", self.guard(github(comments=[self.comment("bob")])))

    def test_a_bots_comment_doesnt(self):
        self.assertEqual(self.guard(github(comments=[self.comment("ci", "Bot")])), "")

    def test_your_override_label_lets_it_through(self):
        payload = github(comments=[self.comment("bob")], timeline=[self.label("ai-override", "me")])
        self.assertEqual(self.guard(payload), "")

    def test_a_colleagues_override_label_counts_for_nothing(self):
        payload = github(comments=[self.comment("bob")], timeline=[self.label("ai-override", "bob")])
        self.assertIn("bob", self.guard(payload))

    def test_other_labels_dont_count_as_changes(self):
        self.assertEqual(self.guard(github(timeline=[self.label("urgent", "bob")])), "")

    def test_an_open_blocker_stops_it(self):
        self.assertIn("#3", self.guard(github(blocked=[{"number": 3, "state": "OPEN"}])))


ME = {"accountId": "me", "accountType": "atlassian"}
BOB = {"accountId": "bob", "accountType": "atlassian"}
APP = {"accountId": "auto", "accountType": "app"}


class Jira(unittest.TestCase):
    def guard(self, fields, history=()):
        source = JiraSource("etl", "ai", [], {"base_url": "https://x.test", "email": "a@b", "token": "t"})
        replies = {"myself": {"accountId": "me"}}

        def call(method, path, body=None):
            if path == "myself":
                return replies[path]
            if "changelog" in path:
                return {"values": list(history), "isLast": True}
            return {"fields": {"creator": ME, "created": T1, "comment": {"comments": []}, "issuelinks": [], **fields}}

        with mock.patch.object(source, "_call", side_effect=call):
            return source.guard("DE-1")

    def edit(self, who, field="description", at=T2, to=""):
        return {"author": who, "created": at, "items": [{"field": field, "toString": to}]}

    def test_your_own_ticket_passes(self):
        self.assertEqual(self.guard({}), "")

    def test_a_colleagues_description_edit_stops_it(self):
        self.assertIn("bob", self.guard({}, [self.edit(BOB)]))

    def test_automation_doesnt_count(self):
        self.assertEqual(self.guard({}, [self.edit(APP)]), "")

    def test_a_status_change_isnt_a_content_change(self):
        self.assertEqual(self.guard({}, [self.edit(BOB, "status")]), "")

    def test_your_override_label_lets_it_through(self):
        history = [self.edit(BOB), self.edit(ME, "labels", T3, "ai ai-override")]
        self.assertEqual(self.guard({}, history), "")

    def test_a_colleagues_override_label_counts_for_nothing(self):
        history = [self.edit(BOB), self.edit(BOB, "labels", T3, "ai-override")]
        self.assertIn("bob", self.guard({}, history))

    def test_an_unfinished_blocker_stops_it(self):
        link = {"type": {"inward": "is blocked by"}, "inwardIssue": {"key": "DE-9", "fields": {"status": {"statusCategory": {"key": "new"}}}}}
        self.assertIn("DE-9", self.guard({"issuelinks": [link]}))


if __name__ == "__main__":
    unittest.main()
