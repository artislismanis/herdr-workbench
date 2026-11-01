import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.pipeline import PipelineError, load, validate  # noqa: E402


def agent(**extra):
    return {"id": "build", "kind": "agent", "prompt": "implement.md", **extra}


class Validate(unittest.TestCase):
    def rejects(self, raw, text):
        with self.assertRaisesRegex(PipelineError, text):
            validate(raw)

    def test_every_sweep_ends_at_a_go(self):
        for name in ("deps", "docs", "backlog", "logs"):
            steps = load(f"sweep-{name}")["steps"]
            self.assertTrue(steps[-1]["proposals"])
            self.assertTrue(steps[-2]["read_only"] and steps[-2]["proposes"])

    def test_every_shipped_gate_asks_a_plain_question(self):
        for name in ("gated", "prepare", "sweep-deps", "sweep-docs", "sweep-backlog", "sweep-logs"):
            self.assertTrue(all(s.get("ask") for s in load(name)["steps"] if s["kind"] == "gate"), name)

    def test_prepare_is_two_talks_each_with_a_go(self):
        steps = load("prepare")["steps"]
        self.assertEqual([s["id"] for s in steps], ["requirements", "sign-requirements", "design", "sign-design"])
        self.assertEqual([(s["section"], s["label"]) for s in steps if s["kind"] == "gate"], [("Requirements", "planned"), ("Design", "designed")])
        self.assertTrue(all(s["fresh"] and s["read_only"] and s["interactive"] for s in steps if s["kind"] == "agent"))

    def test_an_interactive_step_needs_a_verdict_to_know_when_youve_agreed(self):
        self.rejects({"name": "x", "steps": [agent(interactive=True)]}, "needs a verdict")

    def test_a_gate_that_writes_names_both_section_and_label(self):
        gate = {"id": "go", "kind": "gate", "report": "build", "section": "Design"}
        self.rejects({"name": "x", "steps": [agent(), gate]}, "section and a label")

    def test_a_gate_writes_only_known_sections_and_labels(self):
        gate = {"id": "go", "kind": "gate", "report": "build", "section": "Anything", "label": "planned"}
        self.rejects({"name": "x", "steps": [agent(), gate]}, "section must be one of")
        self.rejects({"name": "x", "steps": [agent(), gate | {"section": "Design", "label": "ai"}]}, "label must be one of")

    def test_the_shipped_pipeline_is_valid(self):
        self.assertEqual([s["id"] for s in load("ticket")["steps"]], ["tasks", "implement", "qa", "ai-review", "open-pr"])

    def test_the_review_pipeline_is_valid_and_can_post_nothing(self):
        steps = load("review")["steps"]
        self.assertEqual([s["id"] for s in steps], ["qa", "ai-review"])
        self.assertNotIn("draft_pr", [s["kind"] for s in steps])

    def test_the_reply_pipeline_is_valid_and_opens_nothing(self):
        self.assertEqual([s["kind"] for s in load("reply")["steps"]], ["agent"])

    def test_again_names_a_prompt_that_exists(self):
        with self.assertRaises(FileNotFoundError):
            validate({"name": "x", "steps": [{"id": "a", "kind": "agent", "prompt": "implement.md", "again": "nope.md"}]})

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
        self.assertEqual([s["kind"] for s in load("gated")["steps"]], ["agent", "gate", "agent", "command", "agent", "draft_pr"])

    def test_outputs_and_publishing_are_checked(self):
        self.rejects({"name": "x", "steps": [agent(output="../secret")]}, "plain file name")
        self.rejects({"name": "x", "steps": [agent(publish="slack")]}, "publish must be")

    def test_a_gate_names_a_report(self):
        self.rejects({"name": "x", "steps": [{"id": "a", "kind": "gate"}]}, "report")

    def test_a_gate_takes_no_prompt(self):
        self.rejects({"name": "x", "steps": [agent(), {"id": "a", "kind": "gate", "report": "build", "prompt": "tasks.md"}]}, "takes only")

    def test_a_gate_only_rejects(self):
        steps = [agent(), {"id": "a", "kind": "gate", "report": "build", "then": {"NO": {"park": "x"}}}]
        self.rejects({"name": "x", "steps": steps}, "isn't in verdict")

    def test_duplicate_ids(self):
        self.rejects({"name": "x", "steps": [agent(), agent()]}, "unique id")

    def test_a_loop_needs_a_max(self):
        step = agent(verdict=["NO"], then={"NO": {"goto": "build"}})
        self.rejects({"name": "x", "steps": [step]}, "max")

    def test_any_step_can_name_a_stage_of_any_name(self):
        steps = [{"id": "a", "stage": "Our build", "kind": "agent", "prompt": "tasks.md"}, {"id": "b", "stage": "Sign-off", "kind": "gate", "report": "a"}, {"id": "c", "stage": "Ship", "kind": "draft_pr"}]
        self.assertEqual(validate({"name": "x", "steps": steps})["steps"][1]["stage"], "Sign-off")

    def test_a_stage_is_a_name(self):
        with self.assertRaisesRegex(PipelineError, "stage"):
            validate({"name": "x", "steps": [{"id": "a", "stage": 1, "kind": "draft_pr"}]})

    def test_a_command_only_passes_or_fails(self):
        with self.assertRaisesRegex(PipelineError, "verdict"):
            validate({"name": "x", "steps": [{"id": "qa", "kind": "command", "verdict": ["OK"]}]})
        with self.assertRaisesRegex(PipelineError, "takes only"):
            validate({"name": "x", "steps": [{"id": "qa", "kind": "command", "verdict": ["PASS", "FAILED"], "prompt": "x"}]})

    def test_a_ticket_stage_is_allowed_but_nothing_else(self):
        steps = [{"id": "c", "kind": "draft_pr"}]
        self.assertEqual(validate({"name": "x", "ticket_stage": "Intake", "steps": steps})["ticket_stage"], "Intake")
        with self.assertRaises(PipelineError):
            validate({"name": "x", "other": 1, "steps": steps})

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
