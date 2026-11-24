import contextlib
import fcntl
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop import __main__ as cli  # noqa: E402
from workshop.__main__ import finish_choices, locked, main, repo_of, review_settings, waited  # noqa: E402
from workshop.pipeline import load  # noqa: E402
from workshop.shell import ToolError  # noqa: E402


class Merge(unittest.TestCase):
    URL = "https://github.com/o/r/pull/9"
    checks = [{"name": "ci", "state": "SUCCESS", "link": ""}]
    threads = 0

    def merge(self, table, decision):
        engine = mock.Mock()
        engine.tools.pr_state.return_value = {"reviewDecision": decision}
        engine.tools.checks.return_value = ("head", self.checks)
        engine.tools.open_threads.return_value = self.threads
        engine.store.all.return_value = []
        with mock.patch.object(cli, "repo_of", return_value=("r", "9")):
            return engine, cli.merge(engine, {"repos": {"r": table}}, self.URL)

    def test_an_unapproved_pr_waits_unless_the_repo_merges_as_admin(self):
        with self.assertRaises(ToolError):
            self.merge({}, "")
        engine, said = self.merge({"merge": "admin"}, "")
        engine.tools.merge.assert_called_once_with(self.URL, True)
        self.assertIn("admin override", said)

    def test_failing_or_running_checks_stop_even_an_admin_merge(self):
        for state in ("FAILURE", "IN_PROGRESS"):
            self.checks = [{"name": "ci", "state": state, "link": ""}]
            with self.assertRaisesRegex(ToolError, "checks"):
                self.merge({"merge": "admin"}, "")

    def test_an_open_thread_stops_even_an_admin_merge(self):
        self.threads = 2
        with self.assertRaisesRegex(ToolError, "2 open threads"):
            self.merge({"merge": "admin"}, "")

    def test_an_approved_pr_merges_with_no_override(self):
        engine, _ = self.merge({}, "APPROVED")
        engine.tools.merge.assert_called_once_with(self.URL, False)

    def test_a_pr_on_top_of_another_waits_for_the_one_below(self):
        engine = mock.Mock()
        engine.tools.checks.return_value = ("head", self.checks)
        engine.store.all.return_value = [mock.Mock(pr=self.URL, below="DE-0")]
        with mock.patch.object(cli, "repo_of", return_value=("r", "9")), self.assertRaisesRegex(ToolError, "Merge that one first"):
            cli.merge(engine, {"repos": {"r": {"merge": "admin"}}}, self.URL)
        engine.tools.merge.assert_not_called()


class StackOn(unittest.TestCase):
    def stack_on(self, runs, repo=None, reason="blocked by DE-0."):
        engine = mock.Mock()
        found = {run.key: run for run in runs}
        engine.store.exists.side_effect = lambda name, key: key in found
        engine.store.load.side_effect = lambda name, key: found[key]
        return cli.stack_on(engine, {"stack": True} if repo is None else repo, "r", reason)

    def layer(self, key, below="", **fields):
        return cli.Run(repo="r", key=key, repo_path="/x", base="main", pipeline={"steps": []}, branch=f"workshop/{key.lower()}", status="done", below=below, **{"pr": "u", **fields})

    def test_a_ticket_starts_on_its_blockers_open_pr(self):
        below = self.layer("DE-0")
        self.assertIs(self.stack_on([below]), below)
        issue = self.layer("3")
        self.assertIs(self.stack_on([issue], reason="blocked by #3."), issue)

    def test_a_repo_that_doesnt_stack_waits_for_the_merge(self):
        self.assertIsNone(self.stack_on([self.layer("DE-0")], repo={}))

    def test_it_waits_for_a_blocker_with_no_pr_yet_or_already_merged(self):
        self.assertIsNone(self.stack_on([self.layer("DE-0", pr="")]))
        self.assertIsNone(self.stack_on([self.layer("DE-0", merged_by="me")]))

    def test_two_blockers_or_one_workshop_doesnt_run_wait(self):
        self.assertIsNone(self.stack_on([self.layer("DE-0")], reason="blocked by DE-0, DE-5."))
        self.assertIsNone(self.stack_on([], reason="blocked by DE-0."))

    def test_a_stack_stops_at_five_layers(self):
        chain = [self.layer(f"DE-{n}", below=f"DE-{n + 1}" if n < 4 else "") for n in range(5)]
        self.assertIsNone(self.stack_on(chain))
        self.assertIsNotNone(self.stack_on(chain[1:], reason="blocked by DE-1."))


class MergeListed(unittest.TestCase):
    def listed(self, text, states):
        folder = Path(tempfile.mkdtemp())
        (folder / "merges.md").write_text(text, encoding="utf-8")
        engine = mock.Mock(mark="[ai] ")
        engine.store.files.return_value = folder
        engine.store.all.return_value = []
        engine.tools.pr_state.side_effect = lambda url: {"state": states.pop(0), "reviewDecision": "APPROVED"}
        engine.tools.checks.return_value = ("head", [])
        engine.tools.open_threads.return_value = 0
        run = mock.Mock(key="DE-1")
        with mock.patch.object(cli, "repo_of", return_value=("r", "9")):
            said = cli.merge_listed(engine, {"repos": {"r": {}}}, run)
        return engine, run, said, folder

    def test_each_listed_pr_is_approved_then_merged_and_the_run_told(self):
        engine, run, said, folder = self.listed("https://github.com/o/r/pull/1\nhttps://github.com/o/r/pull/2\n", ["OPEN", "OPEN", "OPEN", "OPEN"])
        self.assertEqual([c.args[0] for c in engine.tools.approve.call_args_list], ["https://github.com/o/r/pull/1", "https://github.com/o/r/pull/2"])
        self.assertEqual(engine.tools.merge.call_count, 2)
        engine.answer.assert_called_once_with(run, said, who="Workshop, on the person's go,")
        self.assertFalse((folder / "merges.md").exists())

    def test_one_already_merged_is_left_alone(self):
        engine, _, said, _ = self.listed("https://github.com/o/r/pull/1\n", ["MERGED"])
        engine.tools.approve.assert_not_called()
        self.assertIn("Approved and merged: https://github.com/o/r/pull/1", said)


class StackedMerge(unittest.TestCase):
    URL = "https://github.com/o/r/pull/9"
    ALLOWED = {"squashMergeAllowed": True, "mergeCommitAllowed": False, "rebaseMergeAllowed": False}

    def merge(self, *answers, admin=False):
        refused = ToolError("gh pr failed: This pull request is part of a stack and must be merged using the asynchronous merge REST API.")
        with mock.patch("workshop.tools.run", side_effect=refused), mock.patch("workshop.tools.run_json", side_effect=[self.ALLOWED, *answers]) as asked, mock.patch("workshop.tools.time.sleep"):
            from workshop.tools import Tools
            Tools.__new__(Tools).merge(self.URL, admin)
        return asked

    def test_a_stacked_pr_merges_through_the_async_api_and_waits_for_it(self):
        asked = self.merge({"status": "pending", "details": {"uuid": "u1"}}, {"status": "merged", "details": {}}, admin=True)
        self.assertEqual(asked.call_args_list[1].args, ("gh", "api", "-X", "PUT", "repos/o/r/pulls/9/merge-async", "-f", "merge_method=squash", "-F", "bypass_rules=false"))
        self.assertEqual(asked.call_args_list[2].args, ("gh", "api", "repos/o/r/pulls/9/merge-async/u1"))

    def test_an_admin_bypasses_rules_only_once_the_plain_merge_fails(self):
        asked = self.merge({"status": "failed", "details": {"message": "blocked"}}, {"status": "merged", "details": {}}, admin=True)
        self.assertEqual(asked.call_args_list[2].args[-1], "bypass_rules=true")

    def test_a_failed_async_merge_says_why(self):
        with self.assertRaisesRegex(ToolError, "not mergeable"):
            self.merge({"status": "failed", "details": {"message": "not mergeable"}})


class Finish(unittest.TestCase):
    def actions(self, changed, draft):
        return [action for _, _, action in finish_choices(changed, draft)]

    def test_a_clean_draft_can_go_ready_or_post(self):
        self.assertEqual(self.actions([], True), ["ready", "comment"])

    def test_a_draft_goes_ready_and_pushes_in_one_go(self):
        self.assertEqual(self.actions([" M a.py"], True), ["ready"])

    def test_a_pr_already_ready_is_never_marked_again(self):
        self.assertEqual(self.actions([" M a.py"], False), ["push"])
        self.assertEqual(self.actions([], False), ["comment"])


class Filling(unittest.TestCase):
    def tick(self, ready, max_active=2, held=lambda active: "", max_waiting=0, pile=()):
        started = []
        engine = mock.Mock()
        engine.store.all.return_value = list(pile)
        engine.store.read.return_value = {}
        engine.store.exists.return_value = False
        engine.in_order.side_effect = lambda repo, keys: keys
        engine.throttle.hold_new.side_effect = held
        source = mock.Mock()
        source.ready.return_value = ready
        source.guard.return_value = ""
        source.held.return_value = []
        with (
            mock.patch.object(cli, "read_limits"),
            mock.patch.object(cli, "advance_all"),
            mock.patch.object(cli, "ask_lead"),
            mock.patch.object(cli, "check_tickets"),
            mock.patch.object(cli, "poll"),
            mock.patch.object(cli, "save_skipped"),
            mock.patch.object(cli, "make_source", return_value=source),
            mock.patch.object(cli, "running", side_effect=lambda _: started),
            mock.patch.object(cli, "begin", side_effect=lambda _e, _c, _n, key, below=None: started.append(key)),
        ):
            cli.tick(engine, {"repos": {"r": {}}, "max_active": max_active, "max_waiting": max_waiting})
        return started

    def test_one_tick_fills_every_free_slot_and_no_more(self):
        self.assertEqual(self.tick(["1", "2", "3"]), ["1", "2"])

    def test_a_full_pile_starts_nothing_until_it_clears(self):
        stopped = mock.Mock(status="parked", pr="u", preparing=False)
        self.assertEqual(self.tick(["1"], max_waiting=1, pile=[stopped]), [])
        self.assertEqual(self.tick(["1"], max_waiting=2, pile=[stopped]), ["1"])

    def test_limits_are_checked_before_each_start(self):
        self.assertEqual(self.tick(["1", "2"], held=lambda active: "held" if active else ""), ["1"])


class Refresh(unittest.TestCase):
    def test_a_talk_whose_agent_has_gone_is_noticed(self):
        talk = mock.Mock(status="running", phase="prompted", current={"interactive": True})
        working = mock.Mock(status="running", phase="prompted", current={"interactive": False})
        engine = mock.Mock()
        engine.store.all.return_value = [talk, working]
        cli.advance_talks(engine)
        engine.advance.assert_called_once_with(talk)


class SweepByHand(unittest.TestCase):
    def begin(self, runs, exists=False):
        engine = mock.Mock()
        engine.clock.return_value = 86400.0
        engine.store.read.return_value = {}
        engine.store.all.return_value = runs
        engine.store.exists.return_value = exists
        with mock.patch.object(cli, "make_source"), mock.patch.object(cli, "load"):
            cli.begin_sweep(engine, {"repos": {"r": {"path": "/x"}}}, "r", "docs")
        return engine.start.call_args.kwargs

    def test_one_already_run_today_runs_again(self):
        self.assertTrue(self.begin([mock.Mock(repo="r", sweeping="docs", landed="dropped")], exists=True)["restart"])

    def test_one_still_going_is_not_doubled(self):
        with self.assertRaisesRegex(cli.Park, "still going"):
            self.begin([mock.Mock(repo="r", sweeping="docs", landed="", key="sweep-docs-19700101")])


class SweepBrief(unittest.TestCase):
    def test_what_you_declined_reaches_the_next_sweeps_brief(self):
        state = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": state}), mock.patch.object(cli, "make_source") as tickets:
            tickets.return_value.backlog.return_value = []
            store = cli.Store(cli.state_root())
            store.write(cli.SWEEPS, {"r/docs": {"declined": ["Add docs"]}})
            run = mock.Mock(repo="r", sweeping="docs", preparing=False)
            source = cli.source_for({"repos": {"r": {"path": "/x"}}})(run)
            self.assertIn("Add docs", source.get("k").body)


class Sweeping(unittest.TestCase):
    NOW = 100 * 86400.0

    def setUp(self):
        self.engine = mock.Mock()
        self.engine.clock.return_value = self.NOW
        self.engine.store.read.return_value = {}
        self.engine.store.all.return_value = []
        self.engine.throttle.hold_new.return_value = ""
        self.begin = mock.patch.object(cli, "begin_sweep").start()
        self.addCleanup(mock.patch.stopall)

    def due(self, last=0.0, every="weekly", max_active=2):
        if last:
            self.engine.store.read.return_value = {"r/docs": {"last": self.NOW - last * 86400}}
        cli.start_sweeps(self.engine, {"repos": {"r": {"sweeps": {"docs": every}}}, "max_active": max_active})
        return self.begin.call_count

    def test_a_new_sweep_is_due(self):
        self.assertEqual(self.due(), 1)

    def test_weekly_waits_a_week(self):
        self.assertEqual(self.due(6.9), 0)
        self.assertEqual(self.due(7), 1)

    def test_monthly_waits_a_month(self):
        self.assertEqual(self.due(7, "monthly"), 0)
        self.assertEqual(self.due(30, "monthly"), 1)

    def test_a_sweep_waits_for_a_free_slot(self):
        self.engine.store.all.return_value = [mock.Mock(status="running", preparing=False, sweeping="", landed="", repo="r", wake_at=0.0)]
        self.assertEqual(self.due(max_active=1), 0)

    def test_limits_hold_a_sweep_back(self):
        self.engine.throttle.hold_new.return_value = "held"
        self.assertEqual(self.due(), 0)

    def test_an_unanswered_sweep_isnt_joined_by_another(self):
        self.engine.store.all.return_value = [mock.Mock(status="waiting", preparing=False, sweeping="docs", landed="", repo="r")]
        self.assertEqual(self.due(30), 0)

    def test_a_manual_sweep_never_starts_by_itself(self):
        self.assertEqual(self.due(every="manual"), 0)

    def test_an_unknown_cadence_says_so(self):
        with self.assertRaisesRegex(KeyError, "weekly, monthly or manual"):
            self.due(every="daily")


class Preparing(unittest.TestCase):
    def setUp(self):
        self.engine = mock.Mock()
        self.engine.store.exists.return_value = False
        self.source = mock.Mock()
        self.source.stages.return_value = []
        patches = [mock.patch.object(cli, "make_source"), mock.patch.object(cli, "PrepareSource", return_value=self.source), mock.patch.object(cli, "load", return_value=load("prepare"))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def begin(self, ticket, repos=("r",)):
        cli.begin_prepare(self.engine, {"repos": {name: {"path": "/x"} for name in repos}}, ticket)
        return self.engine.start.call_args

    def stage(self):
        call = self.begin("r/7")
        return [step["id"] for step in call.args[4]["steps"][call.kwargs["at"] : call.kwargs["until"]]]

    def test_it_runs_beside_the_ticket_not_as_it(self):
        call = self.begin("r/7")
        self.assertEqual(call.args[:2], ("r", "prep-7"))
        self.assertTrue(call.kwargs["preparing"])

    def test_words_are_an_idea_that_becomes_a_ticket_first(self):
        self.source.create.return_value = "12"
        with mock.patch.object(cli, "make_source") as tickets:
            tickets.return_value.create.return_value = "12"
            call = self.begin("Let people greet a team")
        tickets.return_value.create.assert_called_once_with("Let people greet a team", "Let people greet a team")
        self.assertEqual(call.args[:2], ("r", "prep-12"))

    def test_a_long_idea_makes_a_short_title(self):
        with mock.patch.object(cli, "make_source") as tickets:
            tickets.return_value.create.return_value = "12"
            self.begin("Greet a team. It should work for any size of team, and read names from a file if asked.")
        title, body = tickets.return_value.create.call_args.args
        self.assertEqual(title, "Greet a team")
        self.assertIn("read names from a file", body)

    def test_an_idea_can_name_its_repo(self):
        with mock.patch.object(cli, "make_source") as tickets:
            tickets.return_value.create.return_value = "12"
            call = self.begin("b/Greet a team", ("a", "b"))
        self.assertEqual(call.args[0], "b")

    def test_a_jira_key_is_a_ticket_not_an_idea(self):
        self.assertEqual(self.begin("AB-12").args[1], "prep-AB-12")

    def test_an_idea_with_a_slash_in_it_stays_whole(self):
        with mock.patch.object(cli, "make_source") as tickets:
            tickets.return_value.create.return_value = "12"
            self.begin("Support and/or in filters")
        self.assertEqual(tickets.return_value.create.call_args.args[0], "Support and/or in filters")

    def test_a_bare_number_means_your_only_repo(self):
        self.assertEqual(self.begin("7").args[0], "r")

    def test_two_repos_need_naming(self):
        with self.assertRaisesRegex(KeyError, "repo/key"):
            self.begin("7", ("a", "b"))

    def test_a_ticket_past_requirements_starts_at_design(self):
        self.source.stages.return_value = ["planned"]
        self.assertEqual(self.stage(), ["design"])

    def test_a_new_ticket_does_only_the_requirements(self):
        self.assertEqual(self.stage(), ["requirements"])

    def test_a_designed_ticket_goes_to_the_queue_talk(self):
        self.source.stages.return_value = ["planned", "designed"]
        self.assertEqual(self.stage(), ["queue"])

    def test_a_ticket_in_delivery_is_refused(self):
        self.engine.store.exists.side_effect = lambda repo, key: key == "7"
        with self.assertRaisesRegex(KeyError, "already"):
            self.begin("r/7")

    def test_a_finished_prepare_can_run_again(self):
        self.engine.store.exists.side_effect = lambda repo, key: key == "prep-7"
        self.engine.store.load.return_value = mock.Mock(landed="prepared")
        self.assertTrue(self.begin("r/7").kwargs["restart"])

    def test_preparing_never_takes_a_delivery_slot(self):
        self.engine.store.all.return_value = [mock.Mock(status="running", preparing=True), mock.Mock(status="running", preparing=False, wake_at=0.0)]
        self.assertEqual(len(cli.running(self.engine)), 1)

    def test_a_ticket_being_prepared_isnt_started(self):
        self.engine.store.exists.return_value = True
        self.engine.store.load.return_value = mock.Mock(landed="")
        self.assertTrue(cli.being_prepared(self.engine, "r", "7"))
        self.engine.store.load.return_value = mock.Mock(landed="prepared")
        self.assertFalse(cli.being_prepared(self.engine, "r", "7"))


class ReviewRepo(unittest.TestCase):
    CFG = {"repos": {"mine": {"path": "/tmp"}}}

    def origin(self, url):
        patch = mock.patch("workshop.backlog.run", return_value=url + "\n")
        self.addCleanup(patch.stop)
        patch.start()

    def test_finds_the_repo_by_origin_not_by_table_name(self):
        for origin in ("git@github.com:o/r.git", "https://github.com/o/r.git", "https://github.com/o/r"):
            self.origin(origin)
            self.assertEqual(repo_of(self.CFG, "https://github.com/o/r/pull/12"), ("mine", "12"))

    def test_a_similar_name_is_not_a_match(self):
        self.origin("git@github.com:o/my-r.git")
        with self.assertRaisesRegex(KeyError, "needs a .repos. table"):
            repo_of(self.CFG, "https://github.com/o/r/pull/12")

    def test_anything_else_is_not_a_pr(self):
        with self.assertRaisesRegex(KeyError, "not a PR link"):
            repo_of(self.CFG, "12; rm -rf /")


class Lock(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def held(self):
        lock = (self.root / "lock").open("w")
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX)

    def test_a_tick_gives_way_quietly(self):
        self.held()
        done = []
        self.assertFalse(locked(self.root, lambda: done.append(1)))
        self.assertEqual(done, [])

    def test_an_agent_event_waits_for_a_busy_tick(self):
        lock = (self.root / "lock").open("w")
        fcntl.flock(lock, fcntl.LOCK_EX)
        threading.Timer(0.3, lock.close).start()
        done = []
        self.assertTrue(locked(self.root, lambda: done.append(1), 2))
        self.assertEqual(done, [1])

    def test_a_persons_command_says_when_it_couldnt_run(self):
        self.held()
        with mock.patch("workshop.__main__.PATIENCE", 0.3), self.assertRaisesRegex(ToolError, "busy"):
            waited(self.root, lambda: None)

    def test_a_persons_command_waits_its_turn(self):
        lock = (self.root / "lock").open("w")
        fcntl.flock(lock, fcntl.LOCK_EX)
        threading.Timer(0.3, lock.close).start()
        done = []
        waited(self.root, lambda: done.append(1))
        self.assertEqual(done, [1])


class FinishWaits(unittest.TestCase):
    def test_nothing_posts_while_something_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("workshop.__main__.PATIENCE", 0.3), mock.patch("workshop.__main__.here") as here, mock.patch("workshop.__main__.post_review") as post:
            lock = (Path(tmp) / "lock").open("w")
            self.addCleanup(lock.close)
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaisesRegex(ToolError, "busy"):
                cli.finish(mock.Mock(), Path(tmp), {})
            here.assert_not_called()
            post.assert_not_called()


class ReviewSettings(unittest.TestCase):
    def test_the_defaults_assume_no_team(self):
        self.assertEqual(review_settings({}, {}), {"mode": "silent", "claim": "none", "after_post": "none", "block_failing_ci": False, "copilot": False})

    def test_a_repo_can_differ_from_you(self):
        found = review_settings({"review": {"mode": "claimed", "claim": "github-reviewer"}}, {"review": {"claim": "none"}})
        self.assertEqual(found, {"mode": "claimed", "claim": "none", "after_post": "none", "block_failing_ci": False, "copilot": False})

    def test_a_typo_is_an_error(self):
        with self.assertRaisesRegex(KeyError, "review"):
            review_settings({"review": {"claim": "slack"}}, {})


class Delivering(unittest.TestCase):
    def steps(self, cfg):
        engine = mock.Mock()
        with mock.patch.object(cli, "make_source"):
            cli.begin(engine, {"repos": {"r": {"path": "/x"}}} | cfg, "r", "7")
        return [step["id"] for step in engine.start.call_args.args[4]["steps"]]

    def test_copilot_reviews_the_draft_only_when_you_turn_it_on(self):
        self.assertNotIn("copilot", self.steps({}))
        self.assertEqual(self.steps({"review": {"copilot": True}})[-2:], ["ci", "copilot"])


class Status(unittest.TestCase):
    def test_json_lists_runs_held_and_skipped(self):
        state = tempfile.mkdtemp()
        engine = mock.Mock()
        run = cli.Run("r", "7", "/x", "main", {"steps": [{"id": "implement"}]}, status="parked", reason="tests fail", pr="https://pr/1", begun=5.0)
        engine.store.all.return_value = [run]
        engine.store.exists.return_value = False
        engine.throttle.hold_new.return_value = "5h pace"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": state}), contextlib.redirect_stdout(io.StringIO()) as out:
            cli.state_root().mkdir(parents=True)
            (cli.state_root() / "skipped.json").write_text('{"r/9": "blocked by #3."}', encoding="utf-8")
            cli.show_status(engine, {}, as_json=True)
        shown = json.loads(out.getvalue())
        self.assertEqual(shown["held"], "5h pace")
        self.assertEqual(shown["skipped"], {"r/9": "blocked by #3."})
        self.assertEqual({k: shown["runs"][0][k] for k in ("key", "status", "step", "reason", "pr", "begun")}, {"key": "7", "status": "parked", "step": "implement", "reason": "tests fail", "pr": "https://pr/1", "begun": 5.0})


class Cli(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = Path(tmp.name) / "config.toml"
        env = mock.patch.dict(os.environ, {"WORKSHOP_CONFIG": str(self.config), "XDG_STATE_HOME": tmp.name})
        env.start()
        self.addCleanup(env.stop)

    def run_main(self, *argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return main(list(argv)), err.getvalue()

    def test_missing_arguments_show_usage(self):
        for argv in (["start"], ["start", "repo"], ["explain"], ["resume"]):
            code, err = self.run_main(*argv)
            self.assertEqual(code, 2)
            self.assertIn("Usage: workshop", err)

    def test_an_unknown_run_is_said_plainly(self):
        self.config.write_text("[repos]\n", encoding="utf-8")
        code, err = self.run_main("explain", "r/9")
        self.assertEqual((code, err), (1, "workshop: no run matches r/9\n"))

    def test_a_broken_config_is_an_error_not_a_traceback(self):
        self.config.write_text('key = "a"\nkey = "b"\n', encoding="utf-8")
        code, err = self.run_main("status")
        self.assertEqual(code, 1)
        self.assertIn("isn't valid TOML", err)

    def test_an_empty_comment_prefix_is_an_error(self):
        self.config.write_text('comment_prefix = " "\n[repos]\n', encoding="utf-8")
        code, err = self.run_main("status")
        self.assertEqual(code, 1)
        self.assertIn("comment_prefix", err)


if __name__ == "__main__":
    unittest.main()


class TempFolder(unittest.TestCase):
    def settings(self, env):
        tmp = tempfile.mkdtemp()
        Path(tmp, "settings.json").write_text(json.dumps({"env": env}), encoding="utf-8")
        return tmp

    def started(self, env, before=None):
        claude = self.settings(env)
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": claude, "WORKSHOP_CONFIG": str(Path(claude, "none.toml"))}):
            os.environ.pop("TMPDIR", None)
            if before:
                os.environ["TMPDIR"] = before
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                main(["list"])
            return os.environ.get("TMPDIR")

    def test_the_checks_keep_temp_files_where_claude_does(self):
        folder = str(Path(tempfile.mkdtemp(), "claude-tmp"))
        self.assertEqual(self.started({"CLAUDE_CODE_TMPDIR": folder}), folder)
        self.assertTrue(Path(folder).is_dir())

    def test_tmpdir_in_the_settings_wins(self):
        folder = tempfile.mkdtemp()
        self.assertEqual(self.started({"CLAUDE_CODE_TMPDIR": tempfile.mkdtemp(), "TMPDIR": folder}), folder)

    def test_a_tmpdir_you_set_stays(self):
        mine = tempfile.mkdtemp()
        self.assertEqual(self.started({"TMPDIR": "/elsewhere"}, before=mine), mine)


class QueueHeld(unittest.TestCase):
    def test_a_ticket_labelled_in_jira_leaves_backlog(self):
        source = mock.Mock()
        source.held.return_value = ["DE-9"]
        with mock.patch.object(cli, "make_source", return_value=source), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.queue_held({"repos": {"r": {}}}), {})
        source.move.assert_called_once_with("DE-9", "queued")

    def test_one_it_cant_move_says_why(self):
        source = mock.Mock()
        source.held.return_value = ["DE-9"]
        source.move.side_effect = ValueError("DE-9 has no route to To Do")
        with mock.patch.object(cli, "make_source", return_value=source):
            self.assertIn("no route to To Do", cli.queue_held({"repos": {"r": {}}})["r/DE-9"])

    def test_a_source_with_no_statuses_is_left_alone(self):
        with mock.patch.object(cli, "make_source", return_value=object()):
            self.assertEqual(cli.queue_held({"repos": {"r": {}}}), {})
