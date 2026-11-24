import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workshop.tools import Tools  # noqa: E402


def decision(given: str, *states: str) -> str:
    reply = {"reviewDecision": given, "latestReviews": [{"author": {"login": f"p{n}"}, "state": s} for n, s in enumerate(states)]}
    with mock.patch("workshop.tools.run_json", return_value=reply):
        return Tools().pr_state("u")["reviewDecision"]


class ApprovalWithoutARule(unittest.TestCase):
    def test_github_decision_stands_when_given(self):
        self.assertEqual(decision("REVIEW_REQUIRED", "APPROVED"), "REVIEW_REQUIRED")

    def test_an_approval_counts_where_the_branch_asks_for_no_review(self):
        self.assertEqual(decision("", "COMMENTED", "APPROVED"), "APPROVED")

    def test_a_change_request_holds_it_back(self):
        self.assertEqual(decision("", "APPROVED", "CHANGES_REQUESTED"), "")
        self.assertEqual(decision("", "COMMENTED"), "")




class WarmedReads(unittest.TestCase):
    def test_a_warmed_read_is_used_once_then_read_live(self):
        tools = Tools()
        with mock.patch("workshop.tools.run_json", side_effect=[{"reviewDecision": "APPROVED", "n": 1}, {"reviewDecision": "APPROVED", "n": 2}]) as live:
            tools.warm("pr_state", "u")
            self.assertEqual([tools.pr_state("u")["n"], tools.pr_state("u")["n"]], [1, 2])
        self.assertEqual(live.call_count, 2)


if __name__ == "__main__":
    unittest.main()
