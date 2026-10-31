import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.engine import STEP_TIMEOUT, Engine, Park  # noqa: E402
from workshop.pipeline import load  # noqa: E402
from workshop.shell import ToolError  # noqa: E402
from workshop.sources import Ticket  # noqa: E402
from workshop.store import Store  # noqa: E402
from workshop.tools import Tools  # noqa: E402


class FakeSource:
    def __init__(self):
        self.claimed, self.reports, self.finished = [], [], []
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


class FakeTools:
    """Agents are scripted: status says what each one is doing, write_on_prompt what it leaves."""

    def __init__(self, root):
        self.root, self.work = root, root / "wt"
        self.identity = True
        self.statuses = {}
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

    def ignore_scratch(self, work):
        pass

    def workspace(self, path, label):
        return "p1"

    def split(self, pane, path):
        return "p2"

    def close_pane(self, pane):
        self.closed.append(pane)

    def agent_status(self, name):
        return self.statuses.get(name, "gone")

    def start_agent(self, name, pane):
        self.started.append(name)
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
        self.engine = self.fresh_engine()

    def fresh_engine(self):
        # A new engine on the same state folder: what a crash and restart leaves.
        return Engine(self.tools, Store(self.root / "state"), lambda run: self.source, lambda: self.now)

    def start(self):
        return self.engine.start("repo", "DE-1", self.root, "main", load("ticket"), self.source)

    def finish_agent(self, run, agent, step, text="done"):
        (self.tools.work / ".workshop" / f"{step}.md").write_text(text, encoding="utf-8")
        self.tools.statuses[agent] = "idle"
        return self.engine.advance(run)

    def to_verify(self):
        run = self.start()
        run = self.finish_agent(run, "ws-de-1", "plan", "VERDICT: READY\n")
        return self.finish_agent(run, "ws-de-1", "build")


class HappyPath(Case):
    def test_a_ticket_reaches_a_draft_pr_and_reports_it(self):
        run = self.to_verify()
        run = self.finish_agent(run, "ws-de-1-verify-1", "verify", "VERDICT: PASS\n")
        self.assertEqual(run.status, "done")
        self.assertEqual(self.tools.commits, ["DE-1: Title"])
        self.assertEqual(self.source.finished, ["DE-1"])
        self.assertIn("Draft PR ready", self.source.reports[-1])

    def test_the_brief_is_written_for_the_agents(self):
        self.start()
        self.assertIn("Body", (self.tools.work / ".workshop" / "brief.md").read_text(encoding="utf-8"))

    def test_an_agent_still_working_leaves_the_run_where_it_is(self):
        run = self.engine.advance(self.start())
        self.assertEqual((run.status, run.current["id"]), ("running", "plan"))


class Resume(Case):
    def test_a_new_process_carries_on_from_the_saved_state(self):
        self.to_verify()
        self.engine = self.fresh_engine()
        run = self.engine.store.find("DE-1")
        run = self.finish_agent(run, "ws-de-1-verify-1", "verify", "VERDICT: PASS\n")
        self.assertEqual(run.status, "done")
        self.assertEqual(self.tools.worktrees, 1)

    def test_a_failed_commit_parks_and_resume_finishes_without_rerunning_agents(self):
        self.tools.commit_fails = True
        run = self.to_verify()
        run = self.finish_agent(run, "ws-de-1-verify-1", "verify", "VERDICT: PASS\n")
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
        run = self.finish_agent(run, "ws-de-1-verify-1", "verify", "VERDICT: PASS\n")
        self.assertEqual(self.tools.created, 0)
        self.assertEqual(run.pr, "https://example.test/pr/9")

    def test_resume_checks_a_live_agent_instead_of_prompting_it_again(self):
        run = self.start()
        run.status, run.reason = "parked", "herdr not found"
        prompts = len(self.tools.prompts)
        (self.tools.work / ".workshop" / "plan.md").write_text("VERDICT: READY\n", encoding="utf-8")
        self.tools.statuses["ws-de-1"] = "idle"
        run = self.engine.resume(run)
        self.assertEqual(run.current["id"], "build")
        self.assertEqual(len(self.tools.prompts), prompts + 1)
        self.assertNotIn("Read `.workshop/brief.md`", self.tools.prompts[-1][1])

    def test_resume_starts_the_step_again_when_its_agent_has_gone(self):
        run = self.start()
        run.status = "parked"
        self.tools.statuses.pop("ws-de-1")
        run = self.engine.resume(run)
        self.assertEqual(self.tools.started.count("ws-de-1"), 2)

    def test_a_read_only_breach_reruns_the_step_on_resume(self):
        run = self.start()
        self.tools.changed = ["M src/a.py"]
        run = self.finish_agent(run, "ws-de-1", "plan", "VERDICT: READY\n")
        self.tools.changed = []
        prompts = len(self.tools.prompts)
        run = self.engine.resume(run)
        self.assertEqual(len(self.tools.prompts), prompts + 1)
        self.assertEqual(run.current["id"], "plan")

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
        self.assertEqual(len(self.source.reports), 1)

    def test_going_quiet_without_a_report_parks(self):
        run = self.start()
        self.tools.statuses["ws-de-1"] = "idle"
        self.assertIn("without writing", self.engine.advance(run).reason)

    def test_a_blocked_plan_parks(self):
        run = self.finish_agent(self.start(), "ws-de-1", "plan", "Too vague\nVERDICT: BLOCKED\n")
        self.assertIn("clarifying", run.reason)

    def test_an_invented_verdict_parks(self):
        run = self.finish_agent(self.start(), "ws-de-1", "plan", "VERDICT: MAYBE\n")
        self.assertIn("no valid verdict", run.reason)

    def test_a_read_only_step_that_edits_parks(self):
        run = self.start()
        self.tools.changed = ["M src/a.py"]
        self.assertIn("read-only", self.finish_agent(run, "ws-de-1", "plan", "VERDICT: READY\n").reason)

    def test_a_step_past_its_time_limit_parks(self):
        run = self.start()
        self.now += STEP_TIMEOUT + 1
        self.assertIn("time limit", self.engine.advance(run).reason)

    def test_no_changes_means_no_pr(self):
        self.tools.ahead_count = 0
        run = self.to_verify()
        run = self.finish_agent(run, "ws-de-1-verify-1", "verify", "VERDICT: PASS\n")
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


class Rework(Case):
    def test_changes_needed_goes_back_with_the_fix_prompt_and_a_new_verifier(self):
        run = self.to_verify()
        run = self.finish_agent(run, "ws-de-1-verify-1", "verify", "VERDICT: CHANGES_NEEDED\n")
        self.assertEqual(run.current["id"], "build")
        self.assertIn("asked for changes", self.tools.prompts[-1][1])
        run = self.finish_agent(run, "ws-de-1", "build")
        self.assertIn("ws-de-1-verify-2", self.tools.started)
        self.assertEqual(run.current["id"], "verify")

    def test_rework_stops_at_the_limit(self):
        run = self.to_verify()
        for attempt in (1, 2):
            run = self.finish_agent(run, f"ws-de-1-verify-{attempt}", "verify", "VERDICT: CHANGES_NEEDED\n")
            run = self.finish_agent(run, "ws-de-1", "build")
        run = self.finish_agent(run, "ws-de-1-verify-3", "verify", "VERDICT: CHANGES_NEEDED\n")
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
        run = self.finish_agent(self.start(), "ws-de-1", "plan", "VERDICT: BLOCKED\n")
        text = self.engine.explain(run)
        self.assertIn("Why:", text)
        self.assertIn("workshop resume DE-1", text)


if __name__ == "__main__":
    unittest.main()
