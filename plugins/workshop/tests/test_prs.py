import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.prs import classify, fetch, searches  # noqa: E402

ME = "me"


def review(who, state, commit="head", kind="User"):
    return {"state": state, "author": {"login": who, "__typename": kind}, "commit": {"oid": commit} if commit else None}


def pr(author=ME, reviews=(), draft=False, checks="SUCCESS", waiting=(), threads=(), url="u"):
    return {
        "number": 1,
        "title": "A change",
        "url": url,
        "isDraft": draft,
        "updatedAt": "2026-10-31T00:00:00Z",
        "author": {"login": author},
        "repository": {"name": "r"},
        "commits": {"nodes": [{"commit": {"oid": "head", "statusCheckRollup": {"state": checks} if checks else None}}]},
        "reviews": {"nodes": list(reviews)},
        "reviewRequests": {"nodes": [{"requestedReviewer": {"login": w}} for w in waiting]},
        "reviewThreads": {"nodes": [{"isResolved": t} for t in threads]},
    }


def move(*args, requested=False, **fields):
    found = classify(pr(*args, **fields), ME, requested)
    return found["move"], found["state"]


class MyPRs(unittest.TestCase):
    def test_a_draft_and_failing_checks_are_mine_to_act_on(self):
        self.assertEqual(move(draft=True), ("you", "draft"))
        self.assertEqual(move(checks="FAILURE"), ("you", "checks failing"))

    def test_waiting_for_a_first_review(self):
        self.assertEqual(move(waiting=["them"]), ("others", "awaiting review"))
        self.assertEqual(move(checks=""), ("others", "awaiting review"))

    def test_changes_requested_then_fixed_then_re_requested(self):
        asked = [review("them", "CHANGES_REQUESTED")]
        self.assertEqual(move(reviews=asked), ("you", "changes requested"))
        old = [review("them", "CHANGES_REQUESTED", "older")]
        self.assertEqual(move(reviews=old), ("you", "fixed, re-request"))
        self.assertEqual(move(reviews=old, waiting=["them"]), ("others", "re-review requested"))

    def test_open_threads_then_approved(self):
        self.assertEqual(move(threads=[False, True]), ("you", "comments to address"))
        self.assertEqual(move(reviews=[review("them", "APPROVED")]), ("you", "approved"))

    def test_only_each_persons_last_review_counts(self):
        self.assertEqual(move(reviews=[review("them", "CHANGES_REQUESTED"), review("them", "APPROVED")]), ("you", "approved"))

    def test_bots_and_dismissed_reviews_dont_count(self):
        self.assertEqual(move(reviews=[review("copilot", "CHANGES_REQUESTED", kind="Bot"), review("them", "DISMISSED")]), ("others", "awaiting review"))


class TheirPRs(unittest.TestCase):
    def test_asked_to_review(self):
        self.assertEqual(move("them", requested=True), ("you", "needs review"))
        self.assertEqual(move("them", reviews=[review(ME, "COMMENTED", "older")], requested=True), ("you", "re-review"))

    def test_a_draft_waits_even_when_requested(self):
        self.assertEqual(move("them", draft=True, requested=True), ("others", "draft"))

    def test_after_my_review_it_waits_on_them(self):
        self.assertEqual(move("them", reviews=[review(ME, "CHANGES_REQUESTED")]), ("others", "waiting for fixes"))
        self.assertEqual(move("them", reviews=[review(ME, "CHANGES_REQUESTED", "older")]), ("others", "new commits"))
        self.assertEqual(move("them", reviews=[review(ME, "APPROVED", "older")]), ("others", "approved"))

    def test_a_force_push_that_lost_my_commit_is_new_commits(self):
        self.assertEqual(move("them", reviews=[review(ME, "COMMENTED", None)]), ("others", "new commits"))

    def test_shows_who_got_there_first(self):
        found = classify(pr("them", reviews=[review("other", "APPROVED"), review("them", "COMMENTED")]), ME, True)
        self.assertEqual(found["beaten"], ["other"])


class Fetch(unittest.TestCase):
    def data(self, **lists):
        empty = {"nodes": []}
        return {"data": {"viewer": {"login": ME}, "mine": empty, "requested": empty, "reviewed": empty, "merged_mine": empty, "merged_reviewed": empty, **{k: {"nodes": v} for k, v in lists.items()}}}

    def test_a_pr_found_twice_shows_once_and_keeps_the_request(self):
        same = pr("them", reviews=[review(ME, "COMMENTED", "older")])
        with mock.patch("workshop.prs.run_json", return_value=self.data(requested=[same], reviewed=[same])):
            found = fetch("", 0.0)
        self.assertEqual([(p["move"], p["state"]) for p in found], [("you", "re-review")])

    def test_merged_ones_are_done(self):
        with mock.patch("workshop.prs.run_json", return_value=self.data(merged_mine=[pr()])):
            self.assertEqual(fetch("", 0.0)[0]["move"], "done")

    def test_scope_reaches_every_search(self):
        self.assertTrue(all("org:x" in query for query, size in searches("org:x", 0.0).values()))
        self.assertIn("merged:>=1969-12-25", searches("", 0.0)["merged_mine"][0])


if __name__ == "__main__":
    unittest.main()
