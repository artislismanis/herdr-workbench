import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.stats import stats  # noqa: E402


def spent(key, step, usd=0.5, repo="a"):
    return {"type": "spent", "repo": repo, "key": key, "step": step, "turns": 2, "tokens_in": 100, "tokens_out": 10, "minutes": 1.0, "usd": usd}


class Stats(unittest.TestCase):
    def test_nothing_yet(self):
        self.assertEqual(stats([]), "Nothing recorded yet.")

    def test_counts_across_runs_and_repos(self):
        events = [spent("1", "build"), spent("1", "verify"), {"type": "finished", "repo": "a", "key": "1"}]
        events += [spent("2", "build", repo="b"), spent("2", "verify", repo="b"), spent("2", "build", repo="b"), {"type": "finished", "repo": "b", "key": "2"}]
        text = stats(events)
        self.assertIn("Tickets finished: 2", text)
        self.assertIn("First pass: 1 of 2", text)
        self.assertIn("build: 3 rounds, 300 tokens in, 30 out, 3.0 min, $1.50", text)
        self.assertIn("  b: 3 rounds", text)
        self.assertIn("Average cost per ticket (API-equivalent USD): $1.25", text)

    def test_a_round_that_stopped_to_ask_isnt_rework(self):
        events = [{**spent("1", "plan"), "asked": True}, spent("1", "plan"), {"type": "finished", "repo": "a", "key": "1"}]
        self.assertIn("First pass: 1 of 1", stats(events))

    def test_an_unfinished_run_counts_in_spend_only(self):
        text = stats([spent("1", "plan")])
        self.assertIn("Tickets finished: 0", text)
        self.assertNotIn("First pass", text)

    def test_an_unknown_model_is_flagged_and_why_runs_stopped_is_listed(self):
        text = stats([spent("1", "plan", usd=None), {"type": "parked", "repo": "a", "key": "1", "reason": "Agent gone"}])
        self.assertIn("plus unpriced", text)
        self.assertIn("1 x Agent gone", text)


if __name__ == "__main__":
    unittest.main()
