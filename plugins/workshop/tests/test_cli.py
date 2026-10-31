import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.__main__ import finish_choices, main  # noqa: E402


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
