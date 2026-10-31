import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.engine import PREFIX, STEP_TIMEOUT, Engine, Park  # noqa: E402
from workshop.pipeline import load  # noqa: E402
from workshop.shell import ToolError  # noqa: E402
from workshop.sources import Comment, Ticket  # noqa: E402
from workshop.store import Store  # noqa: E402
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
        self.changed, self.commits, self.pushes = [], [], 0
        self.commit_fails = False
        self.pr = ""
        self.created = 0
        self.ahead_count = 1
        self.worktrees = 0

    def identity_ok(self, repo):
        return self.identity

    def worktree(self, repo, branch, base):
        self.worktrees += 1
        self.work.mkdir(exist_ok=True)
        return self.work

    def linked(self, repo, path, label):
        return "p1"

    def split(self, pane, path):
        return "p2"

    def open_review(self, pane, work, base):
        self.reviews.append((pane, base))
        return [{"path": "a.py", "hunks": [{"newRange": [1, 5]}]}]

    def replace_notes(self, work, notes):
        self.review_notes = notes

    def notify(self, title, body):
        self.notes.append(title)

    def pr_state(self, url):
        return self.pr

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
        (self.kept() / f"{step}.md").write_text(text, encoding="utf-8")
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
        with self.assertRaisesRegex(KeyError, "no stopped agent step"):
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

    def test_a_terminal_comment_is_a_real_comment_and_acts_now(self):
        run = self.engine.comment(self.parked_after_rounds(), "Use v2")
        self.assertIn("Use v2", self.source.reports)
        self.assertEqual(run.status, "running")

    def test_a_terminal_comment_takes_commands(self):
        run = self.engine.comment(self.parked_after_rounds(), "/ai drop Done with this")
        self.assertEqual((run.landed, run.reason), ("dropped", "Done with this"))

    def test_a_command_needs_no_note(self):
        self.assertEqual(self.engine.comment(self.parked_after_rounds(), "/ai drop").landed, "dropped")

    def gate(self):
        run = self.engine.start("repo", "DE-1", self.root, "main", load("gated"), self.source)
        return self.finish_agent(run, "ws-de-1", "tasks", "VERDICT: READY\n")

    def test_a_comment_cant_move_a_gate_and_posts_nothing(self):
        run = self.gate()
        before = len(self.source.reports)
        for text in ("Looks fine", "/ai retry", "/ai pass"):
            with self.assertRaises(KeyError):
                self.engine.comment(run, text)
        self.assertEqual((run.status, len(self.source.reports)), ("waiting", before))

    def test_a_command_can_put_its_note_on_the_next_line(self):
        self.assertEqual(self.engine.comment(self.parked_after_rounds(), "/ai drop\nNo longer needed").reason, "No longer needed")

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
        self.assertIn("tasks needs your approval", run.reason)
        self.assertEqual(self.tools.notes, ["Workshop: DE-1 needs you"])
        self.assertIn("waiting", self.source.reports[-1])
        self.assertIn("workshop approve DE-1", self.engine.explain(run))

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

    def test_rejection_rounds_are_limited(self):
        run = self.run_
        for _ in range(2):
            run = self.engine.reject(run, "No.")
            run = self.finish_agent(run, "ws-de-1", "tasks", "VERDICT: READY\n")
        run = self.engine.reject(run, "No.")
        self.assertEqual(run.status, "parked")
        self.assertIn("after 2 rounds", run.reason)

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
        self.assertIn("workshop answer DE-1", self.engine.explain(run))

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
        self.assertEqual(self.tools.reviews[-1], ("p1", "main"))
        self.assertEqual([n["summary"] for n in self.tools.review_notes], ["Look", "PR-level: b.py:9 Elsewhere"])
        self.assertIn("Loaded 2 note(s)", text)

    def test_missing_notes_still_open_the_review(self):
        run = self.finished()
        (self.kept() / "notes.json").unlink()
        self.assertIn("Loaded 0 note(s)", self.engine.review(run))
        self.assertEqual(self.tools.review_notes, [])

    def test_a_finished_run_opens_its_own_review(self):
        self.finished()
        self.assertEqual(self.tools.reviews, [("p1", "main")])

    def test_a_review_that_wont_open_leaves_the_run_delivered(self):
        def fails(pane, work, base):
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
        self.tools.pr = {"state": "OPEN", "isDraft": False, "reviewDecision": ""}
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
            self.tools.pr = {"state": state, "isDraft": draft, "reviewDecision": decision}
            self.engine.land(run)
            self.assertEqual(self.source.moves[-1], stage)
        self.assertEqual(run.landed, "merged")

    def test_a_stage_moves_the_ticket_once_and_a_closed_pr_leaves_it(self):
        run = self.to_verify()
        run = self.finish_agent(run, "ws-de-1-ai-review-1", "ai-review", "VERDICT: PASS\n")
        moved = len(self.source.moves)
        self.tools.pr = {"state": "OPEN", "isDraft": True, "reviewDecision": ""}
        self.engine.land(run)
        self.assertEqual(len(self.source.moves), moved)
        self.tools.pr = {"state": "CLOSED", "isDraft": False, "reviewDecision": ""}
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
        self.assertIn("workshop retry, pass or drop DE-1", text)


if __name__ == "__main__":
    unittest.main()
