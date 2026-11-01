import json
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.engine import PREFIX, STEP_TIMEOUT, Engine, Park  # noqa: E402
from workshop.pipeline import load  # noqa: E402
from workshop.shell import ToolError  # noqa: E402
from workshop.sources import Comment, Item, PrepareSource, PullRequestSource, SweepSource, Ticket  # noqa: E402
from workshop.store import Run, Store  # noqa: E402
from workshop.tools import Tools  # noqa: E402


class FakeSource:
    def __init__(self):
        self.claimed, self.reports, self.finished, self.moves, self.said = [], [], [], [], []
        self.is_closed = False
        self.guard_reason = ""

    def get(self, key):
        return Ticket(key, "Title", "Body")

    def claim(self, key):
        self.claimed.append(key)

    def report(self, key, text):
        self.reports.append(text)

    def finish(self, key):
        self.finished.append(key)

    def guard(self, key):
        return self.guard_reason

    def move(self, key, stage):
        self.moves.append(stage)

    def comments(self, key, since):
        return self.said

    def closed(self, key):
        return self.is_closed


class FakeTickets(FakeSource):
    """A ticket that can be read and written, as Prepare needs."""

    def __init__(self):
        super().__init__()
        self.text, self.tags, self.labelled = "Body", [], []

    def body(self, key):
        return self.text

    def rewrite(self, key, text):
        self.text = text

    def tag(self, key, label):
        self.tags.append(label)

    def labels(self, key):
        return self.labelled


class Briefs(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.tools, self.source = FakeTools(self.root), FakeSource()
        self.engine = Engine(self.tools, Store(self.root / "state"), lambda run: self.source, FakeThrottle(), clock=lambda: 1000.0)

    def finish(self, text):
        run = self.engine.start("r", "7", self.root, "main", load("gated"), self.source)
        (self.engine.store.files(run) / "tasks.md").write_text(text)
        self.tools.statuses[run.agent] = "idle"
        return self.engine.advance(run)

    def test_a_report_for_a_gate_without_a_brief_goes_back_once(self):
        run = self.finish("Tasks.\n\nVERDICT: READY\n")
        self.assertEqual((run.status, run.current["id"]), ("running", "tasks"))
        self.assertIn("yours has none", self.tools.prompts[-1][1])
        self.tools.statuses[run.agent] = "idle"
        run = self.engine.advance(run)
        self.assertEqual(run.current["id"], "approve")

    def test_a_report_with_a_brief_goes_straight_on(self):
        self.assertEqual(self.finish("## Decision\n\nWhat: A\nAsk: B?\n\nVERDICT: READY\n").current["id"], "approve")

    def test_a_step_with_no_gate_needs_no_brief(self):
        pipeline = {"name": "x", "steps": [{"id": "tasks", "kind": "agent", "prompt": "tasks.md"}]}
        run = self.engine.start("r", "8", self.root, "main", pipeline, self.source)
        (self.engine.store.files(run) / "tasks.md").write_text("Tasks.\n")
        self.tools.statuses[run.agent] = "idle"
        self.engine.advance(run)
        self.assertNotIn("yours has none", self.tools.prompts[-1][1])


class TicketRecord(unittest.TestCase):
    def test_the_ticket_gets_the_asks_not_the_brief(self):
        root = Path(tempfile.mkdtemp())
        tools, source = FakeTools(root), FakeSource()
        engine = Engine(tools, Store(root / "state"), lambda run: source, FakeThrottle(), clock=lambda: 1000.0)
        run = engine.start("r", "7", root, "main", load("ticket"), source)
        (engine.store.files(run) / "ask.md").write_text("Overview: Two calls\nWhat: The name\nWhy: It shows\nAsk: Call it whisper?\nWhat: Short form\nAsk: Add -w?\n")
        tools.statuses[run.agent] = "idle"
        engine.advance(run)
        self.assertEqual(source.reports[-1], f"{PREFIX} Workshop needs a decision: Call it whisper? Add -w?")


class OldRunFiles(unittest.TestCase):
    def test_a_field_thats_gone_doesnt_stop_a_run_loading(self):
        store = Store(Path(tempfile.mkdtemp()))
        run = Run(repo="r", key="1", repo_path="/", base="main", pipeline={"name": "x", "steps": []})
        store.save(run)
        path = store.path("r", "1")
        path.write_text(json.dumps(json.loads(path.read_text()) | {"skipped": [1]}))
        self.assertEqual((store.load("r", "1").key, [found.key for found in store.all()]), ("1", ["1"]))


class Sweeps(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.tools, self.ticket = FakeTools(self.root), mock.Mock()
        self.ticket.backlog.return_value = [Item("1", "Old", [], False), Item("2", "Stale", [], False), Item("3", "Queued", ["ai"], True)]
        self.ticket.create.return_value = "9"
        self.source = SweepSource(self.ticket, "docs", [])
        self.engine = Engine(self.tools, Store(self.root / "state"), lambda run: self.source, FakeThrottle(), clock=lambda: 1000.0)

    def start(self):
        return self.engine.start("r", "sweep-docs-1", self.root, "main", load("sweep-docs"), self.source, sweeping="docs")

    def swept(self, proposals):
        run = self.start()
        files = self.engine.store.files(run)
        (files / "sweep.md").write_text("Looked around.\n\nVERDICT: DONE\n")
        (files / "proposals.json").write_text(json.dumps(proposals))
        self.tools.statuses[run.step_agent] = "idle"
        return self.engine.advance(run)

    CREATE = {"action": "create", "title": "Add docs", "body": "Body", "why": "Missing"}
    CLOSE = {"action": "close", "key": "1", "why": "Done already"}
    COMMENT = {"action": "comment", "key": "2", "text": "Which one?", "why": "Vague"}

    def test_a_long_key_still_makes_a_name_herdr_takes(self):
        run = self.engine.start("r", "sweep-backlog-20261101", self.root, "main", load("sweep-backlog"), self.source, sweeping="backlog")
        self.assertLessEqual(len(run.step_agent), 32)
        self.assertTrue(run.step_agent.endswith("-sweep-1"))

    def test_the_prompt_says_how_to_propose(self):
        self.start()
        self.assertIn("proposals.json", self.tools.prompts[-1][1])

    def test_nothing_changes_before_your_go(self):
        run = self.swept([self.CREATE, self.CLOSE])
        self.assertEqual((run.status, run.current["id"]), ("waiting", "decide"))
        self.ticket.create.assert_not_called()

    def test_its_checkout_goes_once_its_agent_is_done(self):
        run = self.swept([self.CLOSE])
        self.assertEqual((run.worktree, run.pane, len(self.tools.removed)), ("", "", 1))
        self.assertEqual(self.engine.approve(run).landed, "swept")
        self.assertEqual((len(self.tools.removed), self.tools.worktrees), (1, 1))

    def test_guidance_brings_a_checkout_back_for_the_agent(self):
        run = self.engine.reject(self.swept([self.CLOSE]), "Comment instead")
        self.assertEqual((run.current["id"], self.tools.worktrees), ("sweep", 2))

    def test_the_answer_says_what_it_does(self):
        run = self.swept([self.CLOSE])
        self.assertEqual(self.engine.choices(run)[0][0][0], "Close it")

    def test_it_asks_about_one_at_a_time(self):
        run = self.swept([self.CLOSE, self.CREATE])
        self.assertEqual(run.reason, "1 of 2: Close 1, Old?")
        self.assertEqual(self.engine.proposal(run), "Why: Done already")
        self.ticket.close.assert_not_called()

    def test_nothing_happens_until_every_answer_is_in(self):
        run = self.engine.approve(self.swept([self.CLOSE, self.COMMENT]))
        self.ticket.close.assert_not_called()
        self.assertEqual((run.status, run.reason), ("waiting", "2 of 2: Comment on 2, Stale?"))
        run = self.engine.approve(run)
        self.ticket.close.assert_called_once_with("1", f"{PREFIX} Done already")
        self.ticket.report.assert_called_once_with("2", f"{PREFIX} Which one?")
        self.assertEqual(run.landed, "swept")

    def test_the_last_go_ends_it(self):
        run = self.engine.approve(self.swept([self.CREATE]))
        self.assertEqual((run.status, run.landed), ("done", "swept"))
        self.assertIn("docs sweep", self.ticket.create.call_args.args[1])

    def test_nothing_to_propose_ends_quietly(self):
        run = self.swept([])
        self.assertEqual((run.status, run.landed, run.label), ("done", "swept", "swept"))
        self.assertEqual(len(self.tools.removed), 1)

    def test_dropping_one_takes_its_checkout(self):
        run = self.engine.drop(self.swept([self.CREATE]), "Not now")
        self.assertEqual((run.landed, len(self.tools.removed)), ("dropped", 1))
        self.ticket.create.assert_not_called()

    def test_a_broken_file_is_a_stop_not_a_quiet_nothing(self):
        run = self.start()
        files = self.engine.store.files(run)
        (files / "sweep.md").write_text("VERDICT: DONE\n")
        (files / "proposals.json").write_text("{half")
        self.tools.statuses[run.step_agent] = "idle"
        self.assertEqual(self.engine.advance(run).status, "parked")

    def test_a_sweep_never_queues_anything(self):
        self.engine.approve(self.swept([{**self.CREATE, "labels": ["ai"]}]))
        self.assertEqual(len(self.ticket.create.call_args.args), 2)
        self.ticket.tag.assert_not_called()

    def test_n_skips_it_and_asks_the_next(self):
        run = self.engine.reject(self.swept([self.CREATE, self.CLOSE]), "n")
        self.assertEqual(run.reason, "2 of 2: Close 1, Old?")
        self.engine.approve(run)
        self.ticket.create.assert_not_called()
        self.ticket.close.assert_called_once()

    def test_skipped_proposals_are_remembered(self):
        self.engine.reject(self.swept([self.CREATE]), "n")
        self.assertEqual(self.engine.store.read("sweeps.json")["r/docs"]["declined"], ["Add docs"])

    def test_skipping_the_last_ends_it(self):
        self.assertEqual(self.engine.reject(self.swept([self.CREATE]), "N").landed, "swept")

    def guided(self):
        run = self.engine.reject(self.swept([self.CLOSE, self.COMMENT]), "Keep it open and comment instead")
        return self.engine.approve(run)

    def agent_rewrites(self, run, proposals):
        files = self.engine.store.files(run)
        (files / "sweep.md").write_text("Revised.\n\nVERDICT: DONE\n")
        (files / "proposals.json").write_text(json.dumps(proposals))
        self.tools.statuses[run.step_agent] = "idle"
        return self.engine.advance(run)

    def test_guidance_waits_for_every_answer_then_goes_to_the_agent(self):
        run = self.engine.reject(self.swept([self.CLOSE, self.COMMENT]), "Keep it open")
        self.assertEqual((run.status, run.reason), ("waiting", "2 of 2: Comment on 2, Stale?"))
        run = self.engine.approve(run)
        self.assertEqual((run.status, run.current["id"]), ("running", "sweep"))
        self.assertIn("Proposal 1: Keep it open", (self.engine.store.files(run) / "feedback.md").read_text())
        self.assertIn("answered them", self.tools.prompts[-1][1])
        self.assertNotIn("five at most", self.tools.prompts[-1][1])
        self.ticket.close.assert_not_called()

    def test_what_the_agent_does_with_guidance_goes_ahead_without_asking_again(self):
        run = self.agent_rewrites(self.guided(), [{"action": "comment", "key": "1", "text": "Duplicate of 2", "why": "Asked"}, self.COMMENT])
        self.assertEqual((run.status, run.landed), ("done", "swept"))
        self.ticket.close.assert_not_called()
        self.assertEqual([c.args for c in self.ticket.report.call_args_list], [("1", f"{PREFIX} Duplicate of 2"), ("2", f"{PREFIX} Which one?")])

    def test_the_agent_can_drop_one_on_your_guidance(self):
        run = self.agent_rewrites(self.guided(), [{"action": "skip"}, self.COMMENT])
        self.assertEqual(run.landed, "swept")
        self.ticket.close.assert_not_called()

    def test_a_real_question_from_the_agent_comes_back_to_you(self):
        run = self.guided()
        (self.engine.store.files(run) / "ask.md").write_text("Which ticket do you mean?")
        self.tools.statuses[run.step_agent] = "idle"
        run = self.engine.advance(run)
        self.assertEqual(run.status, "waiting")
        self.assertIn("Which ticket", run.reason)
        self.ticket.close.assert_not_called()

    def test_a_proposal_with_a_field_missing_stops_before_anything_is_done(self):
        run = self.agent_rewrites(self.guided(), [self.CLOSE, {"action": "comment", "key": "2", "why": "No text"}])
        self.assertEqual(run.status, "parked")
        self.assertIn("proposal 2 has no text", run.reason)
        self.ticket.close.assert_not_called()

    def test_retrying_a_broken_file_asks_the_agent_again(self):
        run = self.agent_rewrites(self.guided(), [{"action": "comment", "key": "2", "why": "No text"}])
        run = self.engine.retry(run)
        self.assertEqual((run.status, run.current["id"]), ("running", "sweep"))
        self.assertIn("answered them", self.tools.prompts[-1][1])

    def test_a_ticket_out_of_reach_isnt_touched(self):
        queued = {**self.CLOSE, "key": "3"}
        run = self.engine.approve(self.swept([queued, {**self.COMMENT, "key": "77"}]))
        self.engine.approve(run)
        self.ticket.close.assert_not_called()
        self.ticket.report.assert_not_called()

    def test_a_ticket_with_a_run_isnt_touched(self):
        run = self.swept([self.CLOSE])
        self.engine.store.save(replace(run, key="1"))
        self.engine.approve(run)
        self.ticket.close.assert_not_called()

    def test_a_failure_doesnt_repeat_what_went_through(self):
        run = self.engine.approve(self.swept([self.CREATE, self.CLOSE]))
        self.ticket.close.side_effect = ToolError("gh down")
        run = self.engine.approve(run)
        self.assertEqual(run.status, "parked")
        self.ticket.close.side_effect = None
        run = self.engine.retry(run)
        self.assertEqual(run.landed, "swept")
        self.ticket.create.assert_called_once()
        self.assertEqual(self.ticket.close.call_count, 2)


class Deploy(unittest.TestCase):
    URL = "https://example.test/o/r/pull/9"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.tools, self.source = FakeTools(self.root), FakeSource()
        self.engine = Engine(self.tools, Store(self.root / "state"), lambda run: self.source, FakeThrottle(), clock=lambda: 1000.0)

    def delivered(self, **fields):
        run = Run(repo="r", key="7", repo_path=str(self.root), base="dev", pipeline=load("ticket"), status="done", step=5, pr=self.URL, opened=True, worktree=str(self.tools.work), **fields)
        self.tools.work.mkdir(exist_ok=True)
        self.engine.store.save(run)
        return run

    def merged(self, base, body="", commit="abc"):
        self.tools.pr = {"state": "MERGED", "isDraft": False, "reviewDecision": "APPROVED", "headRefOid": "one", "baseRefName": base, "body": body, "mergeCommit": {"oid": commit}}

    def test_a_merge_to_main_is_prod_and_ends_it(self):
        self.merged("main")
        run = self.engine.land(self.delivered())
        self.assertEqual((run.landed, run.deployed, run.label, self.source.moves[-1]), ("merged", "prod", "in prod", "merged"))

    def test_a_merge_to_preprod_keeps_the_ticket_open(self):
        for base in ("dev", "preprod"):
            self.source.moves.clear()
            self.merged(base)
            run = self.engine.land(self.delivered())
            self.assertEqual((run.landed, run.deployed, run.label, self.source.moves), ("", "preprod", "preprod", ["preprod"]))

    def test_preprod_takes_the_worktree_but_keeps_watching(self):
        self.merged("dev")
        run = self.engine.land(self.delivered())
        self.assertEqual((run.worktree, len(self.tools.removed)), ("", 1))
        self.assertEqual((run.status, run.landed), ("done", ""))

    def test_it_lands_once_it_reaches_prod(self):
        self.merged("dev")
        run = self.engine.land(self.delivered())
        self.tools.reaches = True
        run = self.engine.land(run)
        self.assertEqual((run.landed, run.deployed, self.source.moves[-1]), ("merged", "prod", "merged"))
        self.assertEqual(self.tools.asked_reach[-1][1:], (self.URL, "abc", ["main", "master"]))

    def test_preprod_is_told_once(self):
        self.merged("dev")
        run = self.engine.land(self.delivered())
        self.engine.land(run)
        self.assertEqual(self.source.moves, ["preprod"])

    def test_a_feature_branch_is_no_environment(self):
        self.merged("feature/x")
        run = self.engine.land(self.delivered())
        self.assertEqual((run.landed, run.deployed), ("merged", ""))

    def test_a_repo_can_name_its_own_branches(self):
        self.merged("trunk")
        run = self.engine.land(self.delivered(environments={"trunk": "prod"}))
        self.assertEqual(run.deployed, "prod")

    def test_a_review_never_deploys(self):
        self.merged("main")
        run = self.engine.land(self.delivered(reviewing=True))
        self.assertEqual((run.landed, run.deployed), ("merged", ""))

    def test_release_steps_on_the_pr_wait_for_you_at_prod(self):
        self.merged("main", "What changed\n\n## Release steps\n\n- Refresh the data\n\n## Other\n\nx")
        run = self.engine.land(self.delivered())
        self.assertEqual((run.status, run.landed, run.deployed, run.current["id"]), ("waiting", "", "prod", "release"))
        self.assertIn("Refresh the data", (self.engine.store.files(run) / "release.md").read_text())
        self.assertNotIn("merged", self.source.moves)
        self.assertIn("They're done", self.engine.next_action(run))

    def test_release_steps_in_the_design_count_too(self):
        self.merged("main")
        self.source.get = lambda key: Ticket(key, "T", "## Design\n\n### Release steps\n\n- Run the backfill\n\n## Original ticket\n")
        run = self.engine.land(self.delivered())
        self.assertIn("Run the backfill", (self.engine.store.files(run) / "release.md").read_text())

    def test_your_go_on_the_steps_ends_it(self):
        self.merged("main", "## Release steps\n\n- Refresh the data\n")
        run = self.engine.approve(self.engine.land(self.delivered()))
        self.assertEqual((run.status, run.landed, run.label, self.source.moves[-1]), ("done", "merged", "in prod", "merged"))
        self.assertFalse([r for r in self.source.reports if "Draft PR" in r])

    def test_a_step_that_went_wrong_at_release_can_come_back(self):
        self.merged("main", "## Release steps\n\n- Refresh the data\n")
        run = self.engine.reject(self.engine.land(self.delivered()), "The refresh failed")
        self.assertEqual(run.status, "parked")
        self.assertEqual(self.engine.retry(run).status, "waiting")
        self.assertEqual(self.engine.approve(run).landed, "merged")

    def test_a_tick_leaves_a_release_gate_alone(self):
        self.merged("main", "## Release steps\n\n- Refresh the data\n")
        run = self.engine.land(self.delivered())
        self.assertEqual(self.engine.advance(run).status, "waiting")


class Prepare(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.tools, self.ticket = FakeTools(self.root), FakeTickets()
        self.source = PrepareSource(self.ticket, {})
        self.now = [1000.0]
        self.engine = Engine(self.tools, Store(self.root / "state"), lambda run: self.source, FakeThrottle(), clock=lambda: self.now[0])

    def start(self, at=0):
        return self.engine.start("r", "prep-7", self.root, "main", load("prepare"), self.source, preparing=True, at=at)

    def agree(self, run, name="requirements", text="## Decision\n\nWhat: A\nAsk: B?\n\n### What\n\nA thing.\n\nVERDICT: READY\n"):
        (self.engine.store.files(run) / f"{name}.md").write_text(text)
        self.tools.statuses[run.step_agent] = "idle"
        return self.engine.advance(run)

    def test_nothing_reaches_the_ticket_while_you_talk(self):
        run = self.start()
        self.tools.statuses[run.step_agent] = "idle"
        self.engine.advance(run)
        self.assertEqual((self.ticket.reports, self.ticket.moves, self.ticket.claimed, self.ticket.tags, self.ticket.text), ([], [], [], [], "Body"))

    def test_an_agent_going_quiet_is_your_turn_not_a_stop(self):
        run = self.start()
        for status in ("idle", "blocked", "done"):
            self.tools.statuses[run.step_agent] = status
            run = self.engine.advance(run)
            self.assertEqual((run.status, run.label), ("running", "your turn"))

    def test_your_turn_is_announced_once(self):
        run = self.start()
        for status in ("idle", "working", "idle"):
            self.tools.statuses[run.step_agent] = status
            run = self.engine.advance(run)
        self.assertEqual(len([n for n in self.tools.notes if "prep-7" in n]), 1)

    def test_a_long_conversation_has_no_time_limit(self):
        run = self.start()
        self.now[0] += 10 * STEP_TIMEOUT
        self.assertEqual(self.engine.advance(run).status, "running")

    def test_a_draft_without_a_verdict_is_not_agreed(self):
        run = self.start()
        run = self.agree(run, text="Still working this out\n")
        self.assertEqual((run.status, run.current["id"], run.label), ("running", "requirements", "your turn"))

    def test_an_agent_that_dies_stops_the_run(self):
        run = self.start()
        self.tools.statuses[run.step_agent] = "gone"
        self.assertEqual(self.engine.advance(run).status, "parked")

    def test_agreeing_stops_at_a_go_and_writes_nothing(self):
        run = self.agree(self.start())
        self.assertEqual((run.status, run.current["id"]), ("waiting", "sign-requirements"))
        self.assertEqual((self.ticket.text, self.ticket.tags, self.ticket.reports), ("Body", [], []))

    def test_the_brief_is_for_you_not_the_ticket(self):
        report = "## Decision\n\nWhat: A thing\nAsk: Good?\n\n### What\n\nA thing.\n\nVERDICT: READY\n"
        self.engine.approve(self.agree(self.start(), text=report))
        self.assertNotIn("Decision", self.ticket.text)
        self.assertNotIn("Ask:", self.ticket.text)
        self.assertIn("### What", self.ticket.text)

    def test_a_report_for_a_person_opens_with_a_brief(self):
        self.start()
        self.assertIn("## Decision", self.tools.prompts[-1][1])

    def test_a_pr_description_isnt_given_one_but_a_question_is(self):
        pipeline = {"name": "x", "steps": [{"id": "build", "kind": "agent", "prompt": "implement.md"}]}
        self.engine.start("r", "prep-7", self.root, "main", pipeline, self.source)
        self.assertNotIn("## Decision", self.tools.prompts[-1][1])
        self.assertIn("Bottom line:", self.tools.prompts[-1][1])

    def test_your_go_writes_that_section_and_label(self):
        run = self.engine.approve(self.agree(self.start()))
        self.assertTrue(self.ticket.text.startswith("## Requirements\n\n### What"))
        self.assertTrue(self.ticket.text.endswith("## Original ticket\n\nBody"))
        self.assertNotIn("VERDICT", self.ticket.text)
        self.assertEqual(self.ticket.tags, ["planned"])
        self.assertEqual(self.ticket.reports, [f"{PREFIX} Signed off the requirements."])
        self.assertEqual(run.current["id"], "design")

    def test_a_failed_write_keeps_the_go_waiting(self):
        run = self.agree(self.start())
        self.ticket.rewrite = mock.Mock(side_effect=ToolError("gh down"))
        with self.assertRaises(ToolError):
            self.engine.approve(run)
        self.assertEqual(self.engine.store.load("r", "prep-7").current["id"], "sign-requirements")

    def test_design_is_a_fresh_agent(self):
        first = self.start()
        agent, pane = first.step_agent, first.step_pane
        run = self.engine.approve(self.agree(first))
        self.assertNotEqual(run.step_agent, agent)
        self.assertIn(pane, self.tools.closed)

    def test_rejecting_goes_back_to_the_same_talk(self):
        run = self.agree(self.start())
        run = self.engine.reject(run, "Too wide")
        self.assertEqual((run.current["id"], run.status), ("requirements", "running"))
        self.assertIn("Too wide", (self.engine.store.files(run) / "feedback.md").read_text())
        self.assertEqual(self.ticket.text, "Body")

    def finished(self):
        run = self.engine.approve(self.agree(self.start()))
        return self.engine.approve(self.agree(run, "design", "## Decision\n\nWhat: A\nAsk: B?\n\n### How\n\nLike so.\n\nVERDICT: READY\n"))

    def test_the_last_go_ends_the_run_with_no_pr(self):
        run = self.finished()
        self.assertEqual((run.status, run.landed, run.pr, run.label), ("done", "prepared", "", "prepared"))
        self.assertEqual(self.ticket.tags, ["planned", "designed"])
        self.assertIn("## Design\n\n### How", self.ticket.text)
        self.assertEqual(self.tools.reviews, [])
        self.assertFalse([r for r in self.ticket.reports if "Draft PR" in r])

    def test_the_end_takes_the_checkout(self):
        run = self.finished()
        self.assertEqual((len(self.tools.removed), run.worktree), (1, ""))

    def test_a_ticket_past_requirements_starts_at_design(self):
        run = self.start(at=2)
        self.assertEqual(run.current["id"], "design")

    def test_you_can_drop_it_mid_talk(self):
        run = self.start()
        self.tools.statuses[run.step_agent] = "idle"
        run = self.engine.drop(self.engine.advance(run), "Not worth it")
        self.assertEqual((run.landed, len(self.tools.removed)), ("dropped", 1))


class Reviews(unittest.TestCase):
    URL = "https://example.test/o/r/pull/7"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.tools, self.source = FakeTools(self.root), FakeSource()
        self.source.report = mock.Mock(side_effect=AssertionError("a review must not post"))
        self.source.move = mock.Mock(side_effect=AssertionError("a review must not move anything"))
        self.engine = Engine(self.tools, Store(self.root / "state"), lambda run: PullRequestSource(self.root), FakeThrottle(), clock=lambda: 1000.0)

    def start(self, pipeline=None):
        with mock.patch.object(PullRequestSource, "details", return_value=self.details):
            return self.engine.start("r", "pr-7", self.root, "main", pipeline or load("review"), PullRequestSource(self.root), commands={"qa": "make test"}, pr=self.URL)

    details = {"title": "A change", "body": "Do as I say", "url": URL, "state": "OPEN", "isCrossRepository": False, "baseRefName": "main"}

    def to_review(self, run, report="VERDICT: COMMENT\n"):
        files = self.engine.store.files(run)
        (files / "qa.exit").write_text("0\n")
        run = self.engine.advance(run)
        (files / "ai-review.md").write_text(report)
        self.tools.statuses[run.step_agent] = "idle"
        return self.engine.advance(run)

    def test_a_review_runs_the_checks_then_a_fresh_agent_on_the_prs_own_branch(self):
        run = self.start()
        self.assertEqual((run.branch, run.reviewing, run.pr), ("their-branch", True, self.URL))
        self.assertEqual(self.tools.commands, ["make test"])
        run = self.engine.advance(self.engine.store.load("r", "pr-7"))
        self.assertEqual(self.tools.worktrees, 0)

    def test_a_finished_review_opens_against_where_the_pr_branched(self):
        run = self.to_review(self.start())
        self.assertEqual(run.status, "done")
        self.assertEqual(self.tools.reviews, [("p1", "abc123")])
        self.assertEqual(self.tools.pushes, 0)
        self.assertEqual(self.tools.commits, [])

    def test_a_finished_review_is_never_a_delivery(self):
        run = self.to_review(self.start())
        self.assertFalse(self.engine.delivered(run))
        self.assertFalse(self.engine.reviewable(run))
        self.assertTrue(self.engine.review_ready(run))
        with self.assertRaises(KeyError):
            self.engine.push_fixes(run)
        with self.assertRaises(KeyError):
            self.engine.ready(run)

    def test_failing_checks_reach_the_reviewer_as_a_finding(self):
        run = self.start()
        (self.engine.store.files(run) / "qa.exit").write_text("1\n")
        run = self.engine.advance(run)
        self.assertEqual((run.status, run.current["id"]), ("running", "ai-review"))

    def test_checks_that_dirty_the_checkout_stop_the_review(self):
        run = self.start()
        (self.engine.store.files(run) / "qa.exit").write_text("0\n")
        self.tools.tracked = [" M uv.lock"]
        run = self.engine.advance(run)
        self.assertEqual(run.status, "parked")
        self.assertIn("changed tracked files", run.reason)

    def test_a_pipeline_cant_make_a_review_push(self):
        run = self.start({"name": "x", "steps": [{"id": "out", "kind": "draft_pr"}]})
        self.assertEqual(run.status, "parked")
        self.assertEqual((self.tools.pushes, self.tools.commits, self.tools.created), (0, [], 0))

    def test_the_prompt_is_told_its_base(self):
        run = self.start()
        (self.engine.store.files(run) / "qa.exit").write_text("0\n")
        self.engine.advance(run)
        self.assertIn("origin/main...HEAD", self.tools.prompts[-1][1])
        self.assertNotIn("{base}", self.tools.prompts[-1][1])

    def again(self):
        with mock.patch.object(PullRequestSource, "details", return_value=self.details):
            return self.engine.start("r", "pr-7", self.root, "main", load("review"), PullRequestSource(self.root), restart=True, commands={"qa": "make test"}, pr=self.URL)

    def test_the_second_round_looks_only_at_what_changed(self):
        first = self.to_review(self.start())
        self.assertEqual(first.reviewed, "one")
        self.assertEqual(self.tools.reloads, [True])
        run = self.again()
        self.assertEqual((run.reviewed, run.worktree, self.tools.followed), ("one", first.worktree, 1))
        (self.engine.store.files(run) / "notes.json").write_text("[]")
        run = self.to_review(run, "VERDICT: APPROVE\n")
        self.assertIn("git diff one..HEAD", self.tools.prompts[-1][1])
        self.assertIn(self.URL, self.tools.prompts[-1][1])
        self.assertEqual(run.reviewed, "two")
        self.assertTrue((self.engine.store.files(run) / "ai-review-1.md").exists())
        self.assertTrue((self.engine.store.files(run) / "notes-1.json").exists())

    def test_a_force_push_means_a_full_review(self):
        self.to_review(self.start())
        self.tools.ancestor = False
        run = self.again()
        self.assertEqual(run.reviewed, "")
        self.to_review(run)
        self.assertIn("origin/main...HEAD", self.tools.prompts[-1][1])

    def test_nothing_new_is_not_reviewed_again(self):
        self.to_review(self.start())
        self.tools.heads = ["one", "one"]
        with self.assertRaisesRegex(Park, "no new commits"):
            self.again()
        self.assertEqual(self.engine.store.load("r", "pr-7").status, "done")

    def test_a_changed_checkout_is_left_alone(self):
        self.to_review(self.start())
        self.tools.tracked = [" M a.py"]
        with self.assertRaisesRegex(Park, "changed since"):
            self.again()
        self.tools.tracked, self.tools.heads = [], ["mine", "two"]
        with self.assertRaisesRegex(Park, "changed since"):
            self.again()
        self.assertEqual(self.tools.followed, 0)

    def test_threads_load_into_a_review(self):
        self.to_review(self.start())
        self.assertEqual(self.tools.threads_loaded, 1)

    def landed(self, head="one"):
        run = self.to_review(self.start())
        self.tools.pr = {"state": "MERGED", "isDraft": False, "reviewDecision": "", "headRefOid": head}
        return self.engine.land(run)

    def test_a_landed_run_takes_its_worktree_and_workspace_with_it(self):
        work = self.tools.work
        run = self.landed()
        self.assertEqual(self.tools.removed, [work])
        self.assertEqual(self.tools.closed_workspaces, ["p1"])
        self.assertEqual((run.worktree, run.pane, run.landed), ("", "", "merged"))

    def test_commits_not_on_the_pr_keep_the_worktree(self):
        run = self.landed("someone-elses")
        self.assertEqual(self.tools.removed, [])
        self.assertTrue(run.worktree)
        self.assertEqual(run.landed, "merged")

    def test_uncommitted_work_keeps_the_worktree_and_the_workspace(self):
        self.tools.remove_fails = True
        run = self.landed()
        self.assertEqual(self.tools.closed_workspaces, [])
        self.assertTrue(run.worktree)
        self.assertIn("uncommitted", [e for e in self.engine.store.events(run) if e["type"] == "kept_worktree"][0]["reason"])

    def watched(self, *seen):
        self.engine.source_for = lambda run: mock.Mock(**{"activity.return_value": list(seen)})

    def test_someone_else_reaching_a_silent_review_is_told_once(self):
        run = self.to_review(self.start())
        self.tools.notes.clear()
        self.watched("sam reviewed: approved")
        run = self.engine.watch(self.engine.watch(run))
        self.assertEqual(run.noticed, ["sam reviewed: approved"])
        self.assertEqual(self.tools.notes, ["Workshop: pr-7 moved on"])
        self.assertEqual(run.status, "done")

    def test_a_silent_review_claims_on_your_go_and_once(self):
        run = self.to_review(self.start())
        source = mock.Mock()
        self.engine.source_for = lambda run: source
        run = self.engine.claim(self.engine.claim(run))
        self.assertTrue(run.claimed)
        source.claim.assert_called_once_with("pr-7")

    def test_a_delivery_cant_be_claimed(self):
        run = self.to_review(self.start())
        run.reviewing = False
        with self.assertRaises(KeyError):
            self.engine.claim(run)

    def test_what_to_do_next(self):
        run = self.to_review(self.start())
        self.assertIn("prefix+shift+u", self.engine.next_action(run))


class DeliveryRounds(unittest.TestCase):
    STEPS = {"name": "x", "steps": [{"id": "build", "kind": "agent", "prompt": "implement.md"}, {"id": "ai-review", "kind": "agent", "prompt": "ai-review.md", "again": "ai-re-review.md", "fresh": True, "read_only": True, "verdict": ["PASS", "CHANGES_NEEDED"], "then": {"CHANGES_NEEDED": {"goto": "build", "max": 2, "prompt": "fix.md"}}}]}

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.tools, self.source = FakeTools(self.root), FakeSource()
        self.engine = Engine(self.tools, Store(self.root / "state"), lambda run: self.source, FakeThrottle(), clock=lambda: 1000.0)
        self.run = self.engine.start("r", "1", self.root, "main", self.STEPS, self.source)

    def built(self):
        (self.engine.store.files(self.run) / "build.md").write_text("Done\n")
        self.tools.statuses[self.run.agent] = "idle"
        self.run = self.engine.advance(self.run)

    def reviewed(self, verdict):
        (self.engine.store.files(self.run) / "ai-review.md").write_text(f"VERDICT: {verdict}\n")
        self.tools.statuses[self.run.step_agent] = "idle"
        self.run = self.engine.advance(self.run)

    def test_the_second_review_looks_only_at_what_changed(self):
        self.built()
        self.assertIn("Check every requirement", self.tools.prompts[-1][1])
        self.reviewed("CHANGES_NEEDED")
        self.assertEqual(self.run.reviewed, "tree1")
        self.built()
        self.assertIn("git diff tree1 tree2", self.tools.prompts[-1][1])
        self.assertNotIn("Check every requirement", self.tools.prompts[-1][1])

    def test_a_review_that_stopped_hasnt_reviewed_anything(self):
        self.built()
        self.tools.statuses[self.run.step_agent] = "idle"
        self.run = self.engine.advance(self.run)
        self.assertEqual((self.run.status, self.run.reviewed), ("parked", ""))
        self.engine.resume(self.run)
        self.assertIn("Check every requirement", self.tools.prompts[-1][1])


class Snapshots(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp())
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test"]
        subprocess.run(["git", "init", "-q", str(self.work)], check=True)
        (self.work / "a.txt").write_text("one\n")
        subprocess.run([*git, "add", "-A"], cwd=self.work, check=True)
        subprocess.run([*git, "commit", "-qm", "one"], cwd=self.work, check=True)

    def diff(self, *refs):
        return subprocess.run(["git", "diff", "--name-only", *refs], cwd=self.work, capture_output=True, text=True, check=True).stdout.split()

    def test_changes_between_snapshots_include_new_files_and_touch_nothing(self):
        tools = Tools()
        (self.work / "a.txt").write_text("two\n")
        first = tools.snapshot(self.work)
        (self.work / "b.txt").write_text("new\n")
        (self.work / "__pycache__").mkdir()
        (self.work / "__pycache__" / "c.pyc").write_text("x\n")
        second = tools.snapshot(self.work)
        self.assertEqual(self.diff(first, second), ["b.txt"])
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.work, capture_output=True, text=True).stdout.splitlines()
        self.assertEqual(status[0], " M a.txt")


class Replies(unittest.TestCase):
    URL = "https://example.test/o/r/pull/7"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.tools, self.source = FakeTools(self.root), mock.Mock()
        self.source.get.return_value = Ticket("reply-7", "Mine", "sam: please fix", self.URL)
        self.source.guard.return_value = ""
        self.tools.behind = 1
        self.engine = Engine(self.tools, Store(self.root / "state"), lambda run: self.source, FakeThrottle(), clock=lambda: 1000.0)

    def start(self, restart=False):
        return self.engine.start("r", "reply-7", self.root, "main", load("reply"), self.source, restart=restart, pr=self.URL, replying=True)

    def finished(self, replies='[{"comment": 12, "reply": "Fixed"}, {"comment": null, "reply": "Thanks"}]'):
        run = self.start()
        files = self.engine.store.files(run)
        (files / "reply.md").write_text("Done\n")
        (files / "replies.json").write_text(replies)
        self.tools.statuses[run.agent] = "idle"
        return self.engine.advance(run)

    def test_nothing_leaves_until_your_go(self):
        run = self.finished()
        self.assertEqual((run.status, run.label, self.tools.pushes, self.tools.commits), ("done", "in review", 0, []))
        self.source.reply.assert_not_called()
        self.assertEqual(self.tools.reloads, [True])
        self.assertFalse(self.engine.delivered(run))

    def test_your_go_pushes_the_fixes_then_posts_marked_replies(self):
        run = self.engine.send_replies(self.finished())
        self.assertEqual((self.tools.commits, self.tools.pushes), (["Address review comments on 7"], 1))
        self.assertEqual([c.args for c in self.source.reply.call_args_list], [("reply-7", 12, f"{PREFIX} Fixed"), ("reply-7", None, f"{PREFIX} Thanks")])
        self.assertEqual((run.replied, run.label), (True, "replied"))

    def test_a_send_that_stops_halfway_doesnt_repeat_itself(self):
        run = self.finished()
        self.source.reply.side_effect = [None, ToolError("gh failed")]
        with self.assertRaises(ToolError):
            self.engine.send_replies(run)
        self.source.reply.reset_mock(side_effect=True)
        self.engine.send_replies(self.engine.store.load("r", "reply-7"))
        self.assertEqual([c.args[1] for c in self.source.reply.call_args_list], [None])

    def test_no_fixes_means_no_push(self):
        self.tools.behind = 0
        self.engine.send_replies(self.finished())
        self.assertEqual(self.tools.pushes, 0)

    def test_a_running_reply_cant_be_sent(self):
        with self.assertRaises(KeyError):
            self.engine.send_replies(self.start())

    def test_a_bad_replies_file_sends_nothing(self):
        run = self.finished("not json")
        self.assertEqual(self.engine.replies(run), [])

    def test_a_new_round_reads_the_comments_again(self):
        self.finished()
        self.source.get.return_value = Ticket("reply-7", "Mine", "sam: one more", self.URL)
        run = self.start(restart=True)
        self.assertIn("one more", (self.engine.store.files(run) / "brief.md").read_text())

    def test_the_agent_may_change_code_but_not_read_as_a_delivery(self):
        run = self.finished()
        self.assertFalse(run.reviewing)
        self.assertTrue(self.engine.review_ready(run))
        self.assertIn("prefix+shift+u", self.engine.next_action(run))


class Commands(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.tools, self.source = FakeTools(self.root), FakeSource()
        self.now = 1000.0
        self.engine = Engine(self.tools, Store(self.root / "state"), lambda run: self.source, FakeThrottle(), clock=lambda: self.now)
        self.steps = {"name": "x", "steps": [{"id": "work", "kind": "agent", "prompt": "implement.md"}, {"id": "qa", "kind": "command", "verdict": ["PASS", "FAILED"], "then": {"FAILED": {"goto": "work", "max": 1, "prompt": "fix-qa.md"}}, "minutes": 5}, {"id": "after", "kind": "gate", "report": "qa"}]}

    def at_qa(self, commands):
        run = self.engine.start("r", "1", self.root, "main", self.steps, self.source, commands=commands)
        run.step, run.phase = 1, ""
        return self.engine.advance(run)

    def end(self, run, code):
        (self.engine.store.files(run) / "qa.exit").write_text(f"{code}\n", encoding="utf-8")
        return self.engine.advance(run)

    def test_a_repo_without_the_command_skips_it_and_says_so(self):
        run = self.at_qa({})
        self.assertEqual(run.current["id"], "after")
        done = [e for e in self.engine.store.events(run) if e.get("step") == "qa"]
        self.assertEqual(done[-1]["verdict"], "SKIPPED")

    def test_it_runs_in_the_background_and_waits(self):
        run = self.at_qa({"qa": "make test"})
        self.assertEqual(self.tools.commands, ["make test"])
        self.assertEqual((run.current["id"], run.phase), ("qa", "prompted"))
        self.assertEqual(self.tools.then, "workshop advance")
        run = self.engine.advance(run)
        self.assertEqual(self.tools.commands, ["make test"])

    def test_a_pass_moves_on(self):
        self.assertEqual(self.end(self.at_qa({"qa": "x"}), 0).current["id"], "after")

    def test_a_failure_goes_back_with_its_own_prompt(self):
        run = self.end(self.at_qa({"qa": "x"}), 1)
        self.assertEqual(run.current["id"], "work")
        self.assertIn("checks failed", self.tools.prompts[-1][1])

    def test_rounds_of_failure_run_out_and_park(self):
        run = self.at_qa({"qa": "x"})
        run.loops["qa"] = 1
        run = self.end(run, 1)
        self.assertEqual(run.status, "parked")
        self.assertIn("still says FAILED", run.reason)

    def test_past_its_time_it_stops_and_parks(self):
        run = self.at_qa({"qa": "x"})
        self.now += 301
        run = self.engine.advance(run)
        self.assertEqual(run.status, "parked")
        self.assertEqual(self.tools.stopped, [4242])

    def stopped(self):
        run = self.at_qa({"qa": "x"})
        run.phase, run.status, run.reason = "", "parked", "qa failed"
        self.engine.store.save(run)
        return run

    def test_guidance_on_a_retry_reaches_the_next_agent(self):
        run = self.stopped()
        self.engine.retry(run, "It's the environment")
        self.assertIn("It's the environment", (self.engine.store.files(run) / "answers.md").read_text(encoding="utf-8"))

    def test_a_note_on_a_pass_reaches_the_next_agent(self):
        run = self.stopped()
        self.engine.accept(run, "Known flake")
        self.assertIn("Known flake", (self.engine.store.files(run) / "answers.md").read_text(encoding="utf-8"))

    def test_a_retry_runs_it_again_and_keeps_the_last_output(self):
        run = self.at_qa({"qa": "x"})
        files = self.engine.store.files(run)
        (files / "qa.md").write_text("old", encoding="utf-8")
        run.phase, run.status = "", "parked"
        self.engine.store.save(run)
        self.engine.retry(run)
        self.assertEqual(len(self.tools.commands), 2)
        self.assertEqual((files / "qa-1.md").read_text(encoding="utf-8"), "old")


class RealCommand(unittest.TestCase):
    def test_output_and_exit_code_are_kept(self):
        root = Path(tempfile.mkdtemp())
        Tools().start_command("echo hello; exit 3", root, root / "qa.md", root / "qa.exit", "touch woke")
        for _ in range(200):
            if (root / "woke").exists():
                break
            time.sleep(0.01)
        self.assertEqual((root / "qa.exit").read_text(encoding="utf-8").strip(), "3")
        self.assertEqual((root / "qa.md").read_text(encoding="utf-8"), "hello\n")


class FakeThrottle:
    def __init__(self):
        self.held = ""

    def hold_steps(self):
        return self.held

    def snapshot(self):
        return {"5h": 10, "7d": 20}


class FakeTools:
    """Agents are scripted: status says what each one is doing, write_on_prompt what it leaves."""

    def __init__(self, root):
        self.root, self.work = root, root / "wt"
        self.identity = True
        self.statuses = {}
        self.models = {}
        self.notes = []
        self.reviews, self.review_notes, self.open_already = [], None, False
        self.prompts, self.started, self.closed = [], [], []
        self.commands, self.stopped = [], []
        self.changed, self.commits, self.pushes = [], [], 0
        self.commit_fails = False
        self.pr = ""
        self.created = 0
        self.ahead_count = 1
        self.worktrees = 0
        self.tracked = []
        self.reloads, self.threads_loaded, self.followed = [], 0, 0
        self.snapshots = 0
        self.removed, self.closed_workspaces, self.remove_fails = [], [], False
        self.heads, self.ancestor = ["one", "two"], True


    def start_command(self, command, work, output, finished, then=""):
        self.commands.append(command)
        self.then = then
        return 4242

    def stop_command(self, pid):
        self.stopped.append(pid)
    def identity_ok(self, repo):
        return self.identity

    def pr_worktree(self, repo, number, base):
        self.work.mkdir(exist_ok=True)
        return self.work, "their-branch"

    def fork_point(self, work, base):
        return "abc123"

    def remove_worktree(self, repo, work):
        if self.remove_fails:
            raise ToolError("wt: worktree has uncommitted changes")
        self.removed.append(work)

    def close_workspace(self, pane):
        self.closed_workspaces.append(pane)

    def unpushed(self, work, branch):
        return self.behind

    def snapshot(self, work):
        self.snapshots += 1
        return f"tree{self.snapshots}"

    def load_threads(self, work):
        self.threads_loaded += 1

    def head(self, work):
        return self.heads[0]

    def follow_pr(self, work, branch, base):
        self.followed += 1
        self.heads.pop(0)

    def contains(self, work, commit):
        return self.ancestor

    def tracked_changes(self, work):
        return list(self.tracked)

    def worktree(self, repo, branch, base):
        self.worktrees += 1
        self.work.mkdir(exist_ok=True)
        return self.work

    def linked(self, repo, path, label):
        return "p1"

    def split(self, pane, path):
        return "p2"

    def open_review(self, pane, work, base, reload=False):
        self.reviews.append((pane, base))
        self.reloads.append(reload)
        return [{"path": "a.py", "hunks": [{"newRange": [1, 5]}]}]

    def replace_notes(self, work, notes):
        self.review_notes = notes

    def notify(self, title, body):
        self.notes.append(title)

    def pr_state(self, url):
        return self.pr

    reaches = False

    def reached(self, repo, pr, commit, branches):
        self.asked_reach = [*getattr(self, "asked_reach", []), (repo, pr, commit, branches)]
        return self.reaches

    def spent(self, work, since, until):
        return {"turns": 3, "tokens_in": 1000, "tokens_out": 50, "minutes": 1.5, "usd": 0.25}

    def close_pane(self, pane):
        self.closed.append(pane)

    def agent_status(self, name):
        return self.statuses.get(name, "gone")

    def start_agent(self, name, pane, files, model=""):
        self.started.append(name)
        self.files = files
        self.models[name] = model
        self.statuses[name] = "idle"

    def prompt(self, name, text):
        self.prompts.append((name, text))
        self.statuses[name] = "working"

    def changes(self, work):
        return list(self.changed)

    def commit_all(self, work, message):
        if self.commit_fails:
            raise ToolError("git commit failed: empty ident name")
        self.commits.append(message)

    def ahead(self, work, base):
        return self.ahead_count

    def mark_ready(self, work, pr):
        self.readied = pr

    def review_open(self, work):
        return self.open_already

    def push(self, work, branch):
        self.pushes += 1

    def find_pr(self, work, branch):
        return self.pr

    def create_pr(self, work, branch, base, title, body):
        self.created += 1
        self.pr = "https://example.test/pr/1"
        return self.pr


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.tools = FakeTools(self.root)
        self.source = FakeSource()
        self.now = 1000.0
        self.throttle = FakeThrottle()
        self.engine = self.fresh_engine()

    def fresh_engine(self):
        # A new engine on the same state folder: what a crash and restart leaves.
        return Engine(self.tools, Store(self.root / "state"), lambda run: self.source, self.throttle, lambda: self.now)

    def kept(self):
        path = self.engine.store.root / "runs" / "repo--DE-1"
        path.mkdir(exist_ok=True)
        return path

    def start(self):
        return self.engine.start("repo", "DE-1", self.root, "main", load("ticket"), self.source)

    def finish_agent(self, run, agent, step, text="done"):
        (self.kept() / f"{step}.md").write_text(f"## Decision\n\nWhat: A\nAsk: B?\n\n{text}", encoding="utf-8")
        self.tools.statuses[agent] = "idle"
        return self.engine.advance(run)

    def to_verify(self):
        run = self.start()
        run = self.finish_agent(run, "ws-de-1", "tasks", "VERDICT: READY\n")
        return self.finish_agent(run, "ws-de-1", "implement")


class HappyPath(Case):
    def test_a_ticket_reaches_a_draft_pr_and_reports_it(self):
        run = self.to_verify()
        run = self.finish_agent(run, "ws-de-1-ai-review-1", "ai-review", "VERDICT: PASS\n")
        self.assertEqual(run.status, "done")
        self.assertEqual(self.tools.commits, ["DE-1: Title"])
        self.assertEqual(self.source.finished, ["DE-1"])
        self.assertIn("Draft PR ready", self.source.reports[-1])

    def test_the_brief_is_written_for_the_agents(self):
        self.start()
        self.assertIn("Body", (self.kept() / "brief.md").read_text(encoding="utf-8"))

    def test_an_agent_still_working_leaves_the_run_where_it_is(self):
        run = self.engine.advance(self.start())
        self.assertEqual((run.status, run.current["id"]), ("running", "tasks"))


class Concluding(Case):
    def parked_after_rounds(self):
        run = self.to_verify()
        for attempt in (1, 2):
            run = self.finish_agent(run, f"ws-de-1-ai-review-{attempt}", "ai-review", "VERDICT: CHANGES_NEEDED\n")
            run = self.finish_agent(run, "ws-de-1", "implement")
        run = self.finish_agent(run, "ws-de-1-ai-review-3", "ai-review", "VERDICT: CHANGES_NEEDED\n")
        self.assertEqual((run.status, run.current["id"]), ("parked", "ai-review"))
        return run

    def test_retry_with_guidance_gets_fresh_rounds_and_the_note_is_kept(self):
        run = self.engine.retry(self.parked_after_rounds(), "Use v2")
        self.assertEqual(run.status, "running")
        self.assertNotIn("ai-review", run.loops)
        self.assertIn("A: Use v2", (self.kept() / "answers.md").read_text(encoding="utf-8"))
        self.assertIn("[ai] Retrying ai-review. Use v2", self.source.reports)
        run = self.finish_agent(run, "ws-de-1-ai-review-4", "ai-review", "VERDICT: CHANGES_NEEDED\n")
        self.assertEqual((run.status, run.current["id"]), ("running", "implement"))

    def test_retry_without_a_note_runs_the_step_again(self):
        run = self.engine.retry(self.parked_after_rounds())
        self.assertEqual((run.status, run.phase), ("running", "prompted"))

    def test_pass_moves_on_past_a_reviewer(self):
        run = self.engine.accept(self.parked_after_rounds(), "Fine by me")
        self.assertEqual(run.status, "done")
        self.assertIn("[ai] Passed ai-review by hand. Fine by me", self.source.reports)

    def test_pass_refuses_a_gate(self):
        run = self.engine.start("repo", "DE-1", self.root, "main", load("gated"), self.source)
        run = self.finish_agent(run, "ws-de-1", "tasks", "VERDICT: READY\n")
        with self.assertRaisesRegex(KeyError, "no stopped step"):
            self.engine.accept(run)

    def test_drop_ends_the_run_and_keeps_it_on_record(self):
        run = self.engine.drop(self.parked_after_rounds(), "Not needed")
        self.assertEqual((run.status, run.landed, run.reason, run.blocked_at, self.source.moves[-1]), ("done", "dropped", "Not needed", 0.0, "dropped"))
        self.assertIn("[ai] Dropped. Not needed", self.source.reports)

    def test_a_running_run_cant_be_concluded(self):
        run = self.start()
        for action in (self.engine.retry, self.engine.accept, self.engine.drop):
            with self.assertRaisesRegex(KeyError, "isn't stopped"):
                action(run)

    def test_my_ticket_comment_can_drop_and_pass(self):
        run = self.parked_after_rounds()
        self.source.said = [Comment(run.blocked_at + 1, True, "me", "/ai drop No longer needed")]
        self.assertEqual(self.engine.unblock(run).landed, "dropped")

    def test_someone_elses_command_does_nothing(self):
        run = self.parked_after_rounds()
        self.source.said = [Comment(run.blocked_at + 1, False, "them", "/ai drop now")]
        self.assertEqual(self.engine.unblock(run).status, "parked")

    def test_closing_the_ticket_drops_a_stopped_run(self):
        run = self.parked_after_rounds()
        self.source.is_closed = True
        self.assertEqual(self.engine.unblock(run).landed, "dropped")

    def gate(self):
        run = self.engine.start("repo", "DE-1", self.root, "main", load("gated"), self.source)
        return self.finish_agent(run, "ws-de-1", "tasks", "VERDICT: READY\n")

    def test_a_command_can_put_its_note_on_the_next_line(self):
        self.assertEqual(self.engine._ticket_action("/ai drop\nNo longer needed"), ("drop", "No longer needed"))

    def test_gate_decisions_leave_a_record_on_the_ticket(self):
        self.engine.approve(self.gate())
        self.assertIn("[ai] Approved.", self.source.reports)

    def test_a_gate_can_be_dropped(self):
        self.assertEqual(self.engine.drop(self.gate()).landed, "dropped")

    def test_the_command_follows_the_prefix(self):
        engine = Engine(self.tools, self.engine.store, lambda run: self.source, self.throttle, lambda: self.now, "[bot]")
        self.assertEqual(engine.command, "/bot")


class Restart(Case):
    def test_a_ticket_with_a_run_isnt_replaced_by_accident(self):
        self.start()
        with self.assertRaisesRegex(Park, "already has a run"):
            self.start()
        self.assertEqual(self.tools.worktrees, 1)

    def test_restart_starts_over_in_the_same_workspace(self):
        first = self.to_verify()
        run = self.engine.start("repo", "DE-1", self.root, "main", load("ticket"), self.source, restart=True)
        self.assertEqual((run.step, run.pane, run.worktree), (0, first.pane, first.worktree))
        self.assertEqual(self.tools.worktrees, 1)


class Resume(Case):
    def test_a_new_process_carries_on_from_the_saved_state(self):
        self.to_verify()
        self.engine = self.fresh_engine()
        run = self.engine.store.find("DE-1")
        run = self.finish_agent(run, "ws-de-1-ai-review-1", "ai-review", "VERDICT: PASS\n")
        self.assertEqual(run.status, "done")
        self.assertEqual(self.tools.worktrees, 1)

    def test_a_failed_commit_parks_and_resume_finishes_without_rerunning_agents(self):
        self.tools.commit_fails = True
        run = self.to_verify()
        run = self.finish_agent(run, "ws-de-1-ai-review-1", "ai-review", "VERDICT: PASS\n")
        self.assertEqual(run.status, "parked")
        self.assertIn("empty ident name", run.reason)
        prompts = len(self.tools.prompts)
        self.tools.commit_fails = False
        run = self.engine.resume(run)
        self.assertEqual(run.status, "done")
        self.assertEqual(len(self.tools.prompts), prompts)

    def test_resume_doesnt_open_a_second_pr(self):
        self.tools.pr = "https://example.test/pr/9"
        run = self.to_verify()
        run = self.finish_agent(run, "ws-de-1-ai-review-1", "ai-review", "VERDICT: PASS\n")
        self.assertEqual(self.tools.created, 0)
        self.assertEqual(run.pr, "https://example.test/pr/9")

    def test_resume_checks_a_live_agent_instead_of_prompting_it_again(self):
        run = self.start()
        run.status, run.reason = "parked", "herdr not found"
        prompts = len(self.tools.prompts)
        (self.kept() / "tasks.md").write_text("VERDICT: READY\n", encoding="utf-8")
        self.tools.statuses["ws-de-1"] = "idle"
        run = self.engine.resume(run)
        self.assertEqual(run.current["id"], "implement")
        self.assertEqual(len(self.tools.prompts), prompts + 1)
        self.assertNotIn("`brief.md`", self.tools.prompts[-1][1])

    def test_resume_starts_the_step_again_when_its_agent_has_gone(self):
        run = self.start()
        run.status = "parked"
        self.tools.statuses.pop("ws-de-1")
        run = self.engine.resume(run)
        self.assertEqual(self.tools.started.count("ws-de-1"), 2)

    def test_a_read_only_breach_reruns_the_step_on_resume(self):
        run = self.start()
        self.tools.changed = ["M src/a.py"]
        run = self.finish_agent(run, "ws-de-1", "tasks", "VERDICT: READY\n")
        self.tools.changed = []
        prompts = len(self.tools.prompts)
        run = self.engine.resume(run)
        self.assertEqual(len(self.tools.prompts), prompts + 1)
        self.assertEqual(run.current["id"], "tasks")

    def test_only_a_parked_run_can_be_resumed(self):
        with self.assertRaises(KeyError):
            self.engine.resume(self.start())


class Parking(Case):
    def test_a_missing_git_identity_stops_before_claiming(self):
        self.tools.identity = False
        with self.assertRaises(Park):
            self.start()
        self.assertEqual(self.source.claimed, [])

    def test_a_blocked_agent_parks_the_run_and_says_so_once(self):
        run = self.start()
        self.tools.statuses["ws-de-1"] = "blocked"
        run = self.engine.advance(run)
        self.assertEqual(run.status, "parked")
        self.engine.advance(run)
        self.assertEqual(len([r for r in self.source.reports if "Started" not in r]), 1)

    def test_going_quiet_without_a_report_parks(self):
        run = self.start()
        self.tools.statuses["ws-de-1"] = "idle"
        self.assertIn("without writing", self.engine.advance(run).reason)

    def test_a_blocked_plan_parks(self):
        run = self.finish_agent(self.start(), "ws-de-1", "tasks", "Too vague\nVERDICT: BLOCKED\n")
        self.assertIn("clarifying", run.reason)

    def test_an_invented_verdict_parks(self):
        run = self.finish_agent(self.start(), "ws-de-1", "tasks", "VERDICT: MAYBE\n")
        self.assertIn("no valid verdict", run.reason)

    def test_a_read_only_step_that_edits_parks(self):
        run = self.start()
        self.tools.changed = ["M src/a.py"]
        self.assertIn("read-only", self.finish_agent(run, "ws-de-1", "tasks", "VERDICT: READY\n").reason)

    def test_a_step_past_its_time_limit_parks(self):
        run = self.start()
        self.now += STEP_TIMEOUT + 1
        self.assertIn("time limit", self.engine.advance(run).reason)

    def test_no_changes_means_no_pr(self):
        self.tools.ahead_count = 0
        run = self.to_verify()
        run = self.finish_agent(run, "ws-de-1-ai-review-1", "ai-review", "VERDICT: PASS\n")
        self.assertIn("changed nothing", run.reason)
        self.assertEqual(self.tools.pushes, 0)


class Guard(Case):
    def test_a_colleagues_edit_stops_the_ticket_before_it_is_claimed(self):
        self.source.guard_reason = "last changed by bob"
        with self.assertRaisesRegex(Park, "bob"):
            self.start()
        self.assertEqual(self.source.claimed, [])

    def test_an_explicit_override_runs_it_and_is_logged(self):
        self.source.guard_reason = "last changed by bob"
        self.engine.start("repo", "DE-1", self.root, "main", load("ticket"), self.source, override=True)
        self.assertEqual(self.source.claimed, ["DE-1"])
        run = self.engine.store.find("DE-1")
        self.assertTrue(self.engine.store.events(run)[0]["override"])


class Throttled(Case):
    def test_a_held_step_starts_nothing_then_carries_on(self):
        self.throttle.held = "5h usage is 90%, above 85%."
        run = self.start()
        self.assertEqual((run.status, run.phase, self.tools.prompts), ("running", "", []))
        self.assertIn("Held: 5h usage", self.engine.explain(run))
        self.throttle.held = ""
        run = self.engine.advance(run)
        self.assertEqual((run.phase, run.held), ("prompted", ""))
        self.assertEqual(len(self.tools.prompts), 1)

    def test_a_running_step_finishes_and_the_next_one_holds(self):
        run = self.start()
        self.throttle.held = "7d usage is 80%, inside the 25% reserve."
        run = self.finish_agent(run, "ws-de-1", "tasks", "VERDICT: READY\n")
        self.assertEqual((run.status, run.current["id"], len(self.tools.prompts)), ("running", "implement", 1))

    def test_a_hold_is_logged_once_and_so_is_its_end(self):
        self.throttle.held = "held"
        run = self.start()
        self.engine.advance(run)
        self.engine.advance(run)
        self.throttle.held = ""
        self.engine.advance(run)
        self.assertEqual([e["reason"] for e in self.engine.store.events(run) if e["type"] == "held"], ["held", ""])

    def test_a_parked_run_held_after_resume_stays_running(self):
        run = self.start()
        self.tools.statuses["ws-de-1"] = "gone"
        run = self.engine.advance(run)
        self.assertEqual(run.status, "parked")
        self.throttle.held = "held"
        run = self.engine.resume(run)
        self.assertEqual((run.status, run.held), ("running", "held"))

    def test_steps_log_usage_at_start_and_end(self):
        run = self.start()
        self.finish_agent(run, "ws-de-1", "tasks", "VERDICT: READY\n")
        tasks = [e for e in self.engine.store.events(run) if e["type"] == "step" and e["step"] == "tasks"]
        self.assertEqual([(e["state"], e["usage"]) for e in tasks], [("running", {"5h": 10, "7d": 20}), ("done", {"5h": 10, "7d": 20})])


class Gates(Case):
    def setUp(self):
        super().setUp()
        self.run_ = self.engine.start("repo", "DE-1", self.root, "main", load("gated"), self.source)
        self.run_ = self.finish_agent(self.run_, "ws-de-1", "tasks", "VERDICT: READY\n")

    def test_a_gate_waits_and_says_so(self):
        run = self.run_
        self.assertEqual((run.status, run.current["id"]), ("waiting", "approve"))
        self.assertIn("right tasks to build", run.reason)
        self.assertEqual(self.tools.notes, ["Workshop: DE-1 needs you"])
        self.assertIn("waiting", self.source.reports[-1])
        self.assertIn("Start building", self.engine.explain(run))

    def test_a_waiting_run_doesnt_move_by_itself(self):
        prompts = len(self.tools.prompts)
        self.assertEqual(self.engine.advance(self.run_).status, "waiting")
        self.assertEqual(len(self.tools.prompts), prompts)

    def test_approval_carries_on(self):
        run = self.engine.approve(self.run_)
        self.assertEqual((run.status, run.current["id"], run.phase), ("running", "implement", "prompted"))
        self.assertEqual(run.reason, "")

    def test_rejection_goes_back_with_the_note(self):
        run = self.engine.reject(self.run_, "Use the existing client.")
        self.assertEqual((run.status, run.current["id"]), ("running", "tasks"))
        self.assertIn("Use the existing client.", (self.kept() / "feedback.md").read_text(encoding="utf-8"))
        self.assertIn("feedback.md", self.tools.prompts[-1][1])

    def test_a_person_can_send_it_back_as_often_as_they_like(self):
        run = self.run_
        for _ in range(5):
            run = self.engine.reject(run, "Not yet")
            run = self.finish_agent(run, "ws-de-1", "tasks", "VERDICT: READY\n")
            self.assertEqual((run.status, run.current["id"]), ("waiting", "approve"))

    def test_only_a_gate_can_be_approved(self):
        run = self.engine.approve(self.run_)
        with self.assertRaises(KeyError):
            self.engine.approve(run)
        with self.assertRaises(KeyError):
            self.engine.reject(run, "No.")


class Questions(Case):
    def asked(self):
        run = self.start()
        (self.kept() / "ask.md").write_text("Which API version?\n", encoding="utf-8")
        self.tools.statuses["ws-de-1"] = "idle"
        return self.engine.advance(run)

    def test_a_question_waits_for_a_person(self):
        run = self.asked()
        self.assertEqual((run.status, run.reason), ("waiting", "tasks asks: Which API version?"))
        self.assertIn("Your answer", self.engine.explain(run))

    def test_a_question_beats_a_report(self):
        run = self.start()
        (self.kept() / "ask.md").write_text("Which?", encoding="utf-8")
        run = self.finish_agent(run, "ws-de-1", "tasks", "VERDICT: READY\n")
        self.assertEqual((run.status, run.current["id"]), ("waiting", "tasks"))

    def test_an_answer_goes_to_the_same_agent_and_the_step_goes_on(self):
        run = self.asked()
        self.now += 100
        run = self.engine.answer(run, "v2")
        self.assertEqual((run.status, run.phase, run.started_at), ("running", "prompted", self.now))
        self.assertIn("answers.md", self.tools.prompts[-1][1])
        self.assertEqual(self.tools.prompts[-1][0], "ws-de-1")
        self.assertIn("A: v2", (self.kept() / "answers.md").read_text(encoding="utf-8"))
        self.assertFalse((self.kept() / "ask.md").exists())

    def test_an_answer_in_the_pane_carries_the_run_on(self):
        run = self.asked()
        (self.kept() / "ask.md").unlink()
        run = self.engine.answered_in_pane(run)
        self.assertEqual((run.status, run.reason, run.blocked_at, self.source.moves[-1]), ("running", "", 0.0, "working"))
        run = self.finish_agent(run, "ws-de-1", "tasks", "VERDICT: READY\n")
        self.assertEqual(run.current["id"], "implement")

    def test_a_pane_answer_still_being_worked_on_leaves_the_step_open(self):
        run = self.asked()
        (self.kept() / "ask.md").unlink()
        self.tools.statuses["ws-de-1"] = "working"
        run = self.engine.advance(self.engine.answered_in_pane(run))
        self.assertEqual((run.status, run.current["id"]), ("running", "tasks"))

    def test_an_open_question_keeps_waiting(self):
        run = self.asked()
        self.assertEqual(self.engine.answered_in_pane(run).status, "waiting")

    def test_a_gate_never_clears_itself(self):
        run = self.engine.start("repo", "DE-1", self.root, "main", load("gated"), self.source)
        run = self.finish_agent(run, "ws-de-1", "tasks", "VERDICT: READY\n")
        self.assertEqual(run.current["kind"], "gate")
        self.assertEqual(self.engine.answered_in_pane(run).status, "waiting")

    def test_cost_isnt_counted_twice_after_a_question(self):
        self.now = 1100.0
        run = self.asked()
        self.assertEqual(run.started_at, 1100.0)

    def test_an_answer_restarts_the_step_when_its_agent_has_gone(self):
        run = self.asked()
        self.tools.statuses.pop("ws-de-1")
        run = self.engine.answer(run, "v2")
        self.assertEqual(self.tools.started.count("ws-de-1"), 2)
        self.assertEqual((run.status, run.current["id"]), ("running", "tasks"))

    def test_a_failed_prompt_parks_the_run(self):
        run = self.asked()
        self.tools.prompt = mock.Mock(side_effect=ToolError("herdr failed"))
        self.assertEqual(self.engine.answer(run, "v2").status, "parked")

    def test_a_question_in_a_fresh_step_keeps_its_pane(self):
        run = self.to_verify()
        (self.kept() / "ask.md").write_text("Which?", encoding="utf-8")
        self.tools.statuses["ws-de-1-ai-review-1"] = "idle"
        run = self.engine.advance(run)
        self.assertEqual((run.status, run.step_agent), ("waiting", "ws-de-1-ai-review-1"))
        self.assertEqual(self.tools.closed, [])
        self.assertEqual(self.engine.answer(run, "x").step_agent, "ws-de-1-ai-review-1")

    def test_only_a_question_can_be_answered(self):
        with self.assertRaises(KeyError):
            self.engine.answer(self.start(), "v2")


class Review(Case):
    def finished(self):
        run = self.to_verify()
        (self.kept() / "notes.json").write_text('[{"file": "a.py", "line": 2, "note": "Look"}, {"file": "b.py", "line": 9, "note": "Elsewhere"}]', encoding="utf-8")
        return self.finish_agent(run, "ws-de-1-ai-review-1", "ai-review", "VERDICT: PASS\n")

    def test_notes_land_in_hunk_and_the_rest_are_listed(self):
        text = self.engine.review(self.finished())
        self.assertEqual(self.tools.reviews[-1], ("p1", "origin/main"))
        self.assertEqual([n["summary"] for n in self.tools.review_notes], ["Look", "PR-level: b.py:9 Elsewhere"])
        self.assertIn("Loaded 2 note(s)", text)

    def test_missing_notes_still_open_the_review(self):
        run = self.finished()
        (self.kept() / "notes.json").unlink()
        self.assertIn("Loaded 0 note(s)", self.engine.review(run))
        self.assertEqual(self.tools.review_notes, [])

    def test_a_finished_run_opens_its_own_review(self):
        self.finished()
        self.assertEqual(self.tools.reviews, [("p1", "origin/main")])

    def test_a_review_that_wont_open_leaves_the_run_delivered(self):
        def fails(pane, work, base, reload=False):
            raise ToolError("hunk didn't open a review.")

        self.tools.open_review = fails
        run = self.finished()
        self.assertEqual(run.status, "done")
        self.assertIn("review_failed", [e["type"] for e in self.engine.store.events(run)])

    def test_an_open_review_keeps_what_was_added_since(self):
        run = self.finished()
        self.tools.review_notes = "kept"
        self.tools.open_already = True
        self.assertIn("Already open", self.engine.review(run))
        self.assertEqual(self.tools.review_notes, "kept")

    def test_a_landed_run_takes_no_more_pushes(self):
        run = self.finished()
        run.landed = "merged"
        with self.assertRaises(KeyError):
            self.engine.push_fixes(run)

    def test_ready_pushes_fixes_then_opens_the_pr(self):
        run = self.finished()
        pushes = self.tools.pushes
        self.tools.pr = {"state": "OPEN", "isDraft": False, "reviewDecision": "", "headRefOid": "one"}
        run = self.engine.ready(run)
        self.assertEqual(self.tools.pushes, pushes + 1)
        self.assertEqual(self.tools.readied, run.pr)
        self.assertIn("1: Address review comments", self.tools.commits[-1])
        self.assertEqual(run.moved, "review")
        self.assertFalse(self.engine.reviewable(run))

    def test_only_a_draft_can_be_marked_ready(self):
        with self.assertRaises(KeyError):
            self.engine.ready(self.start())

    def test_the_handover_reaches_the_ticket(self):
        run = self.start()
        self.engine.store.files(run).mkdir(exist_ok=True)
        (self.engine.store.files(run) / "ai-review.md").write_text("## Handover\n- New flag\nRecommendation: approve\n\nVERDICT: PASS\n", encoding="utf-8")
        self.assertIn("Recommendation: approve", self.engine.handover(run))

    def test_a_run_without_a_pr_has_nothing_to_review(self):
        with self.assertRaises(KeyError):
            self.engine.review(self.start())


class Telling(Case):
    def test_a_parked_run_notifies(self):
        run = self.start()
        self.tools.statuses["ws-de-1"] = "gone"
        self.engine.advance(run)
        self.assertEqual(self.tools.notes, ["Workshop: DE-1 needs you"])

    def test_a_finished_run_notifies(self):
        run = self.to_verify()
        self.finish_agent(run, "ws-de-1-ai-review-1", "ai-review", "VERDICT: PASS\n")
        self.assertEqual(self.tools.notes, ["Workshop: DE-1 ready"])


class Steps(Case):
    def pipeline(self, **extra):
        return {"name": "x", "steps": [{"id": "tasks", "kind": "agent", "prompt": "tasks.md", **extra}]}

    def start_with(self, pipeline):
        return self.engine.start("repo", "DE-1", self.root, "main", pipeline, self.source)

    def test_a_step_starts_its_agent_on_its_model(self):
        self.start_with(self.pipeline(model="haiku"))
        self.assertEqual(self.tools.models["ws-de-1"], "haiku")

    def test_a_fresh_step_gets_its_own_model(self):
        self.start_with(self.pipeline(model="opus", fresh=True))
        self.assertEqual(self.tools.models["ws-de-1-tasks-1"], "opus")

    def test_a_steps_own_limit_replaces_the_default(self):
        run = self.start_with(self.pipeline(minutes=5))
        self.now += 5 * 60 + 1
        run = self.engine.advance(run)
        self.assertEqual(run.status, "parked")
        self.assertIn("time limit", run.reason)

    def test_a_step_within_its_limit_keeps_going(self):
        run = self.start_with(self.pipeline(minutes=5))
        self.now += 5 * 60 - 1
        self.assertEqual(self.engine.advance(run).status, "running")


class Rework(Case):
    def test_changes_needed_goes_back_with_the_fix_prompt_and_a_new_verifier(self):
        run = self.to_verify()
        run = self.finish_agent(run, "ws-de-1-ai-review-1", "ai-review", "VERDICT: CHANGES_NEEDED\n")
        self.assertEqual(run.current["id"], "implement")
        self.assertIn("asked for changes", self.tools.prompts[-1][1])
        run = self.finish_agent(run, "ws-de-1", "implement")
        self.assertIn("ws-de-1-ai-review-2", self.tools.started)
        self.assertEqual(run.current["id"], "ai-review")
        self.assertIn("CHANGES_NEEDED", (self.kept() / "ai-review-1.md").read_text(encoding="utf-8"))

    def test_each_round_is_costed_and_the_ticket_gets_the_summary(self):
        run = self.to_verify()
        run = self.finish_agent(run, "ws-de-1-ai-review-1", "ai-review", "VERDICT: PASS\n")
        self.assertIn("Cost (API-equivalent USD)", self.engine.explain(run))
        spent = [e["step"] for e in self.engine.store.events(run) if e["type"] == "spent"]
        self.assertEqual(spent, ["tasks", "implement", "ai-review"])

    def test_run_files_stay_out_of_the_worktree(self):
        run = self.start()
        self.assertIn(str(self.kept()), self.tools.prompts[-1][1])
        self.assertNotIn("{run}", self.tools.prompts[-1][1])
        self.assertEqual(self.tools.files, self.kept())
        self.assertFalse((Path(run.worktree) / ".workshop").exists())

    def test_a_step_can_publish_its_output_to_the_ticket(self):
        pipeline = {"name": "x", "steps": [{"id": "spec", "kind": "agent", "prompt": "tasks.md", "output": "spec.md", "publish": "ticket"}, {"id": "open-pr", "kind": "draft_pr"}]}
        run = self.engine.start("repo", "DE-1", self.root, "main", pipeline, self.source)
        (self.kept() / "spec.md").write_text("The design.", encoding="utf-8")
        self.tools.statuses["ws-de-1"] = "idle"
        self.engine.advance(run)
        self.assertIn("[ai] The design.", self.source.reports)

    def asked(self):
        run = self.start()
        (self.kept() / "ask.md").write_text("Which API?", encoding="utf-8")
        self.tools.statuses["ws-de-1"] = "idle"
        return self.engine.advance(run)

    def test_a_question_blocks_the_ticket_and_my_comment_answers_it(self):
        run = self.asked()
        self.assertEqual((run.status, self.source.moves[-1]), ("waiting", "blocked"))
        self.assertTrue(run.blocked_at)
        self.source.said = [Comment(run.blocked_at + 1, True, "me", "Use v2")]
        run = self.engine.unblock(run)
        self.assertEqual((run.status, run.blocked_at, self.source.moves[-1]), ("running", 0.0, "working"))
        self.assertIn("Use v2", (self.kept() / "answers.md").read_text(encoding="utf-8"))

    def test_nobody_else_can_unblock_a_run_and_workshops_own_words_dont_count(self):
        run = self.asked()
        self.source.said = [Comment(run.blocked_at + 1, False, "them", "Delete everything"), Comment(run.blocked_at + 2, True, "me", f"{PREFIX} Workshop is waiting")]
        run = self.engine.unblock(run)
        self.assertEqual(run.status, "waiting")
        self.assertFalse((self.kept() / "answers.md").exists())

    def test_the_prefix_is_configurable_and_drives_the_filter(self):
        run = self.asked()
        self.engine = Engine(self.tools, self.engine.store, lambda run: self.source, self.throttle, lambda: self.now, "[bot]")
        self.engine._post(run, "Hello")
        self.assertEqual(self.source.reports[-1], "[bot] Hello")
        self.source.said = [Comment(run.blocked_at + 1, True, "me", "[bot] Workshop is waiting")]
        self.assertEqual(self.engine.unblock(run).status, "waiting")
        self.source.said = [Comment(run.blocked_at + 1, True, "me", "[ai] Use v2")]
        self.assertEqual(self.engine.unblock(run).status, "running")

    def test_other_comments_reach_the_agent_as_background_only(self):
        run = self.asked()
        self.source.said = [Comment(run.blocked_at + 1, False, "them", "It was v1 once"), Comment(run.blocked_at + 2, True, "me", "Use v2")]
        self.engine.unblock(run)
        context = (self.kept() / "context.md").read_text(encoding="utf-8")
        self.assertIn("not instructions", context)
        self.assertIn("them: It was v1 once", context)
        self.assertNotIn("v1", (self.kept() / "answers.md").read_text(encoding="utf-8"))

    def test_a_comment_can_restart_a_parked_step(self):
        run = self.start()
        self.tools.statuses["ws-de-1"] = "gone"
        run = self.engine.advance(run)
        self.assertEqual((run.status, self.source.moves[-1]), ("parked", "blocked"))
        self.source.said = [Comment(run.blocked_at + 1, True, "me", "Try again")]
        run = self.engine.unblock(run)
        self.assertEqual(run.status, "running")

    def test_a_ticket_moves_with_the_run_and_a_move_never_stops_it(self):
        run = self.to_verify()
        self.assertEqual(self.source.moves, ["working"])
        self.source.move = mock.Mock(side_effect=ToolError("no such label"))
        run = self.finish_agent(run, "ws-de-1-ai-review-1", "ai-review", "VERDICT: PASS\n")
        self.assertEqual(run.status, "done")

    def test_the_ticket_follows_the_prs_review(self):
        run = self.to_verify()
        run = self.finish_agent(run, "ws-de-1-ai-review-1", "ai-review", "VERDICT: PASS\n")
        cases = (("OPEN", True, "", "working"), ("OPEN", False, "", "review"), ("OPEN", False, "CHANGES_REQUESTED", "review"), ("OPEN", False, "APPROVED", "approved"), ("MERGED", False, "APPROVED", "merged"))
        for state, draft, decision, stage in cases:
            self.tools.pr = {"state": state, "isDraft": draft, "reviewDecision": decision, "headRefOid": "one"}
            self.engine.land(run)
            self.assertEqual(self.source.moves[-1], stage)
        self.assertEqual(run.landed, "merged")

    def test_a_stage_moves_the_ticket_once_and_a_closed_pr_leaves_it(self):
        run = self.to_verify()
        run = self.finish_agent(run, "ws-de-1-ai-review-1", "ai-review", "VERDICT: PASS\n")
        moved = len(self.source.moves)
        self.tools.pr = {"state": "OPEN", "isDraft": True, "reviewDecision": "", "headRefOid": "one"}
        self.engine.land(run)
        self.assertEqual(len(self.source.moves), moved)
        self.tools.pr = {"state": "CLOSED", "isDraft": False, "reviewDecision": "", "headRefOid": "one"}
        self.assertEqual(self.engine.land(run).landed, "closed")
        self.assertEqual(len(self.source.moves), moved)

    def test_rework_stops_at_the_limit(self):
        run = self.to_verify()
        for attempt in (1, 2):
            run = self.finish_agent(run, f"ws-de-1-ai-review-{attempt}", "ai-review", "VERDICT: CHANGES_NEEDED\n")
            run = self.finish_agent(run, "ws-de-1", "implement")
        run = self.finish_agent(run, "ws-de-1-ai-review-3", "ai-review", "VERDICT: CHANGES_NEEDED\n")
        self.assertEqual(run.status, "parked")
        self.assertIn("after 2 rounds", run.reason)


class Generated(unittest.TestCase):
    def repo(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name)
        subprocess.run(["git", "init", "-q", str(work)], check=True)
        (work / "pkg" / "__pycache__").mkdir(parents=True)
        (work / "pkg" / "__pycache__" / "a.pyc").write_text("x", encoding="utf-8")
        (work / "pkg" / "a.py").write_text("x", encoding="utf-8")
        return work

    def test_caches_dont_count_as_changes(self):
        self.assertEqual(Tools().changes(self.repo()), ["?? pkg/a.py"])

    def test_a_cache_alone_isnt_a_change(self):
        work = self.repo()
        (work / "pkg" / "a.py").unlink()
        self.assertEqual(Tools().changes(work), [])

    def test_caches_arent_committed(self):
        work = self.repo()
        Tools().stage(work)
        names = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=work, capture_output=True, text=True).stdout
        self.assertEqual(names.split(), ["pkg/a.py"])


class Explain(Case):
    def test_a_parked_run_says_why_and_what_to_run(self):
        run = self.finish_agent(self.start(), "ws-de-1", "tasks", "VERDICT: BLOCKED\n")
        text = self.engine.explain(run)
        self.assertIn("Why:", text)
        self.assertIn("Run it again", text)


if __name__ == "__main__":
    unittest.main()
