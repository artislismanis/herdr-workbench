import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_engine import Case  # noqa: E402

from workshop import lead  # noqa: E402

DECIDED = "- Databricks profile: `dev`, for every command and check."


class Lead(Case):
    def setUp(self):
        super().setUp()
        self.repos = self.root / "repos"
        (self.repos / "repo").mkdir(parents=True)
        self.workflow = self.repos / "repo" / "workflow.md"
        self.workflow.write_text(f"- Stacks: git-spice.\n\n## Standing decisions\n\n{DECIDED}\n", encoding="utf-8")
        patch = mock.patch.dict(os.environ, {"WORKSHOP_REPOS": str(self.repos), "WORKSHOP_PROMPTS": str(self.root / "none")})
        patch.start()
        self.addCleanup(patch.stop)
        self.calls = []
        self.reply = {"answer": "Use `dev`.", "rule": DECIDED, "suggest": ""}

    def claude(self, prompt, model, tools):
        self.calls.append(prompt)
        return {"result": json.dumps(self.reply), "model": model, "usd": 0.5, "turns": 1, "tokens_in": 10, "tokens_out": 5, "minutes": 0.1}

    def asking(self):
        self.tools.claude = self.claude
        run = self.start()
        (self.kept() / "ask.md").write_text("Ask: Which Databricks profile?", encoding="utf-8")
        self.tools.statuses["ws-de-1"] = "idle"
        run = self.engine.advance(run)
        self.assertEqual(run.status, "waiting")
        return run

    def test_a_question_the_decisions_settle_is_answered(self):
        run = lead.look(self.engine, self.asking(), {})
        self.assertEqual(run.status, "running")
        self.assertIn("Use `dev`.", (self.kept() / "answers.md").read_text(encoding="utf-8"))
        self.assertIn("From: " + DECIDED, self.source.reports[-1])
        self.assertIn(DECIDED, self.calls[0])

    def test_an_answer_it_cant_trace_to_a_decision_is_only_a_suggestion(self):
        self.reply = {"answer": "Use `prod`.", "rule": "- Always prod.", "suggest": ""}
        run = lead.look(self.engine, self.asking(), {})
        self.assertEqual(run.status, "waiting")
        self.assertEqual(lead.suggestion(self.engine, run), "Use `prod`.")

    def test_each_question_gets_one_look(self):
        self.reply = {"answer": "", "rule": "", "suggest": "Ask Jason."}
        run = self.asking()
        lead.look(self.engine, run, {})
        lead.look(self.engine, run, {})
        self.assertEqual(len(self.calls), 1)

    def test_a_gate_is_never_its_to_answer(self):
        run = self.asking()
        run.current["kind"] = "gate"
        lead.look(self.engine, run, {})
        self.assertEqual(self.calls, [])

    def test_it_stops_at_the_daily_cap(self):
        self.reply = {"answer": "", "rule": "", "suggest": ""}
        run = self.asking()
        self.engine.store.event(run, "spent", step="lead", usd=5.0)
        lead.look(self.engine, run, {})
        self.assertEqual(self.calls, [])

    def test_your_answer_to_a_question_it_left_joins_the_decisions(self):
        self.reply = {"answer": "", "rule": "", "suggest": ""}
        run = lead.look(self.engine, self.asking(), {})
        self.assertTrue(lead.left(self.engine, run))
        lead.remember(run, run.reason, "Use `dev`.")
        self.assertTrue(self.workflow.read_text(encoding="utf-8").endswith(f"{DECIDED}\n- Which Databricks profile? Use `dev`.\n"))


if __name__ == "__main__":
    unittest.main()
