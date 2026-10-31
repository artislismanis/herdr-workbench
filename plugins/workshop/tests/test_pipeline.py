import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.pipeline import PipelineError, load, validate  # noqa: E402


def agent(**extra):
    return {"id": "build", "kind": "agent", "prompt": "build.md", **extra}


class Validate(unittest.TestCase):
    def rejects(self, raw, text):
        with self.assertRaisesRegex(PipelineError, text):
            validate(raw)

    def test_the_shipped_pipeline_is_valid(self):
        self.assertEqual([s["id"] for s in load("ticket")["steps"]], ["plan", "build", "verify", "pr"])

    def test_unknown_step_kind(self):
        self.rejects({"name": "x", "steps": [{"id": "a", "kind": "merge"}]}, "kind must be")

    def test_unknown_step_key(self):
        self.rejects({"name": "x", "steps": [agent(push=True)]}, "unknown keys")

    def test_model_and_minutes_are_allowed(self):
        validate({"name": "x", "steps": [agent(model="haiku", minutes=5)]})

    def test_minutes_must_be_positive(self):
        self.rejects({"name": "x", "steps": [agent(minutes=0)]}, "minutes")

    def test_a_pr_step_takes_no_model(self):
        self.rejects({"name": "x", "steps": [{"id": "a", "kind": "draft_pr", "model": "haiku"}]}, "takes no options")

    def test_steps_sharing_an_agent_must_agree_on_the_model(self):
        steps = [agent(id="a", model="opus"), agent(id="b", model="haiku")]
        self.rejects({"name": "x", "steps": steps}, "same model")

    def test_a_model_on_only_one_shared_step_is_rejected(self):
        self.rejects({"name": "x", "steps": [agent(id="a", model="opus"), agent(id="b")]}, "same model")

    def test_a_fresh_step_can_differ(self):
        validate({"name": "x", "steps": [agent(id="a", model="opus"), agent(id="b", model="haiku", fresh=True)]})

    def test_the_gated_pipeline_is_valid(self):
        self.assertEqual([s["kind"] for s in load("gated")["steps"]], ["agent", "gate", "agent", "agent", "draft_pr"])

    def test_a_gate_names_a_report(self):
        self.rejects({"name": "x", "steps": [{"id": "a", "kind": "gate"}]}, "report")

    def test_a_gate_takes_no_prompt(self):
        self.rejects({"name": "x", "steps": [agent(), {"id": "a", "kind": "gate", "report": "build", "prompt": "plan.md"}]}, "takes only")

    def test_a_gate_only_rejects(self):
        steps = [agent(), {"id": "a", "kind": "gate", "report": "build", "then": {"NO": {"park": "x"}}}]
        self.rejects({"name": "x", "steps": steps}, "isn't in verdict")

    def test_duplicate_ids(self):
        self.rejects({"name": "x", "steps": [agent(), agent()]}, "unique id")

    def test_a_loop_needs_a_max(self):
        step = agent(verdict=["NO"], then={"NO": {"goto": "build"}})
        self.rejects({"name": "x", "steps": [step]}, "max")

    def test_goto_must_exist(self):
        step = agent(verdict=["NO"], then={"NO": {"goto": "gone", "max": 1}})
        self.rejects({"name": "x", "steps": [step]}, "unknown step")

    def test_rules_must_match_a_verdict(self):
        step = agent(verdict=["YES"], then={"NO": {"park": "stop"}})
        self.rejects({"name": "x", "steps": [step]}, "isn't in verdict")

    def test_a_missing_prompt_file_fails_on_load(self):
        with self.assertRaises(FileNotFoundError):
            validate({"name": "x", "steps": [{"id": "a", "kind": "agent", "prompt": "nope.md"}]})

    def test_draft_pr_takes_no_options(self):
        self.rejects({"name": "x", "steps": [{"id": "p", "kind": "draft_pr", "prompt": "x"}]}, "takes no options")

    def test_validate_leaves_the_pipeline_unchanged(self):
        raw = load("ticket")
        self.assertEqual(validate(copy.deepcopy(raw)), raw)


if __name__ == "__main__":
    unittest.main()
