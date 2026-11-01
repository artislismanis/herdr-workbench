import contextlib
import fcntl
import io
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


class Finish(unittest.TestCase):
    def actions(self, changed, draft):
        return [action for _, _, action in finish_choices(changed, draft)]

    def test_a_clean_draft_can_go_ready_or_post(self):
        self.assertEqual(self.actions([], True), ["ready", "comment"])

    def test_fixes_must_be_pushed_before_anything_is_posted(self):
        self.assertEqual(self.actions([" M a.py"], True), ["push", "ready"])

    def test_a_pr_already_ready_is_never_marked_again(self):
        self.assertEqual(self.actions([" M a.py"], False), ["push"])
        self.assertEqual(self.actions([], False), ["comment"])


class Filling(unittest.TestCase):
    def tick(self, ready, max_active=2, held=lambda active: ""):
        started = []
        engine = mock.Mock()
        engine.store.exists.return_value = False
        engine.throttle.hold_new.side_effect = held
        source = mock.Mock()
        source.ready.return_value = ready
        source.guard.return_value = ""
        with (
            mock.patch.object(cli, "read_limits"),
            mock.patch.object(cli, "advance_all"),
            mock.patch.object(cli, "check_tickets"),
            mock.patch.object(cli, "poll"),
            mock.patch.object(cli, "save_skipped"),
            mock.patch.object(cli, "make_source", return_value=source),
            mock.patch.object(cli, "running", side_effect=lambda _: started),
            mock.patch.object(cli, "begin", side_effect=lambda _e, _c, _n, key: started.append(key)),
        ):
            cli.tick(engine, {"repos": {"r": {}}, "max_active": max_active})
        return started

    def test_one_tick_fills_every_free_slot_and_no_more(self):
        self.assertEqual(self.tick(["1", "2", "3"]), ["1", "2"])

    def test_limits_are_checked_before_each_start(self):
        self.assertEqual(self.tick(["1", "2"], held=lambda active: "held" if active else ""), ["1"])


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
        self.engine.store.all.return_value = [mock.Mock(status="running", preparing=False, sweeping="", landed="", repo="r")]
        self.assertEqual(self.due(max_active=1), 0)

    def test_limits_hold_a_sweep_back(self):
        self.engine.throttle.hold_new.return_value = "held"
        self.assertEqual(self.due(), 0)

    def test_an_unanswered_sweep_isnt_joined_by_another(self):
        self.engine.store.all.return_value = [mock.Mock(status="waiting", preparing=False, sweeping="docs", landed="", repo="r")]
        self.assertEqual(self.due(30), 0)

    def test_an_unknown_cadence_says_so(self):
        with self.assertRaisesRegex(KeyError, "weekly or monthly"):
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

    def test_it_runs_beside_the_ticket_not_as_it(self):
        call = self.begin("r/7")
        self.assertEqual(call.args[:2], ("r", "prep-7"))
        self.assertTrue(call.kwargs["preparing"])

    def test_a_bare_number_means_your_only_repo(self):
        self.assertEqual(self.begin("7").args[0], "r")

    def test_two_repos_need_naming(self):
        with self.assertRaisesRegex(KeyError, "repo/key"):
            self.begin("7", ("a", "b"))

    def test_a_ticket_past_requirements_starts_at_design(self):
        self.source.stages.return_value = ["planned"]
        self.assertEqual(self.begin("r/7").kwargs["at"], 2)

    def test_a_fully_prepared_ticket_starts_over(self):
        self.source.stages.return_value = ["planned", "designed"]
        self.assertEqual(self.begin("r/7").kwargs["at"], 0)

    def test_a_ticket_in_delivery_is_refused(self):
        self.engine.store.exists.side_effect = lambda repo, key: key == "7"
        with self.assertRaisesRegex(KeyError, "already"):
            self.begin("r/7")

    def test_a_finished_prepare_can_run_again(self):
        self.engine.store.exists.side_effect = lambda repo, key: key == "prep-7"
        self.engine.store.load.return_value = mock.Mock(landed="prepared")
        self.assertTrue(self.begin("r/7").kwargs["restart"])

    def test_preparing_never_takes_a_delivery_slot(self):
        self.engine.store.all.return_value = [mock.Mock(status="running", preparing=True), mock.Mock(status="running", preparing=False)]
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
        patch = mock.patch("workshop.__main__.run", return_value=url + "\n")
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
                cli.finish(mock.Mock(), Path(tmp))
            here.assert_not_called()
            post.assert_not_called()


class ReviewSettings(unittest.TestCase):
    def test_the_defaults_assume_no_team(self):
        self.assertEqual(review_settings({}, {}), {"mode": "silent", "claim": "none"})

    def test_a_repo_can_differ_from_you(self):
        found = review_settings({"review": {"mode": "claimed", "claim": "github-reviewer"}}, {"review": {"claim": "none"}})
        self.assertEqual(found, {"mode": "claimed", "claim": "none"})

    def test_a_typo_is_an_error(self):
        with self.assertRaisesRegex(KeyError, "review"):
            review_settings({"review": {"claim": "slack"}}, {})


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
