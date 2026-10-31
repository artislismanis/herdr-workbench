import json
import sys
import tempfile
import unittest
import unittest.mock
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop import cost  # noqa: E402

START = datetime(2026, 10, 31, 9, 0, tzinfo=UTC).timestamp()


def entry(id_, minute, usage, model="claude-sonnet-5-5"):
    at = datetime(2026, 10, 31, 9, minute, tzinfo=UTC).isoformat().replace("+00:00", "Z")
    return json.dumps({"type": "assistant", "timestamp": at, "message": {"id": id_, "model": model, "usage": usage}})


class Spent(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        (self.folder / "a.jsonl").write_text(
            "\n".join(
                [
                    entry("m1", 1, {"input_tokens": 1, "output_tokens": 5}),
                    entry("m1", 1, {"input_tokens": 1, "output_tokens": 100}),
                    "not json",
                    entry("m2", 2, {"cache_read_input_tokens": 1000, "cache_creation_input_tokens": 300, "cache_creation": {"ephemeral_1h_input_tokens": 100}}),
                    entry("old", 59, {"output_tokens": 999}),
                ]
            ),
            encoding="utf-8",
        )

    def spent(self):
        with unittest.mock.patch.object(cost, "transcripts", lambda work: self.folder):
            return cost.spent(Path("/w"), START, START + 600)

    def test_a_message_counts_once_at_its_final_size_inside_the_window(self):
        s = self.spent()
        self.assertEqual((s["turns"], s["tokens_in"], s["tokens_out"]), (2, 1301, 100))

    def test_prices_each_kind_of_token(self):
        # 1 in, 100 out, 200 5m writes, 100 1h writes, 1000 reads, at Sonnet 5.5 rates.
        self.assertAlmostEqual(self.spent()["usd"], round((1 * 2 + 100 * 10 + 200 * 2.5 + 100 * 4 + 1000 * 0.1) / 1e6, 4))

    def test_a_long_haiku_prompt_costs_more(self):
        short, long = {"input_tokens": 1000}, {"input_tokens": 200_000}
        self.assertAlmostEqual(cost.usd("claude-haiku-5-5", short), 0.0001)
        self.assertAlmostEqual(cost.usd("claude-haiku-5-5", long), 0.1)

    def test_an_unknown_model_is_unpriced_not_free(self):
        self.assertIsNone(cost.usd("claude-new", {"input_tokens": 1}))
        self.assertIn("unpriced", cost.summary([{"type": "spent", "step": "plan", "turns": 1, "tokens_in": 1, "tokens_out": 1, "minutes": 1.0, "usd": None}]))

    def test_transcript_folder_matches_claude_codes_naming(self):
        self.assertEqual(cost.transcripts(Path("/home/data/.herdr/worktrees/a/b")).name, "-home-data--herdr-worktrees-a-b")

    def test_summary_lists_rounds_and_a_total(self):
        rounds = [{"type": "spent", "step": s, "turns": 2, "tokens_in": 10, "tokens_out": 5, "minutes": 1.0, "usd": 0.5} for s in ("build", "build")]
        text = cost.summary(rounds)
        self.assertEqual(text.count("build:"), 2)
        self.assertIn("Total: 4 turns, 20 tokens in, 10 out, 2.0 min, $1.00", text)
        self.assertEqual(cost.summary([]), "")


if __name__ == "__main__":
    unittest.main()
