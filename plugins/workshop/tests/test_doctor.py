import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.doctor import launch_test, prompts  # noqa: E402
from workshop.tools import Tools  # noqa: E402


class Launch(unittest.TestCase):
    def test_starts_an_agent_the_way_a_run_does(self):
        tools = mock.create_autospec(Tools, instance=True)
        tools.worktree.return_value = Path("/w")
        tools.workspace.return_value = "p1"
        with mock.patch("workshop.doctor.Tools", return_value=tools), mock.patch("workshop.doctor.run"), mock.patch("workshop.doctor.subprocess.run"):
            self.assertTrue(launch_test(Path("/r"), "r", "main").ok)
        tools.close_pane.assert_called_once_with("p1")


class Prompts(unittest.TestCase):
    def test_says_which_step_files_win_and_from_where(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "r" / ".workshop" / "prompts").mkdir(parents=True)
        (tmp / "mine").mkdir()
        with mock.patch.dict(os.environ, {"WORKSHOP_PROMPTS": str(tmp / "mine")}):
            self.assertEqual(prompts("r", tmp / "r").note, "workshop's own")
            (tmp / "mine" / "ai-review.md").write_text("Mine.")
            (tmp / "mine" / "fix.md").write_text("Mine.")
            (tmp / "r" / ".workshop" / "prompts" / "ai-review.md").write_text("Theirs.")
            self.assertEqual(prompts("r", tmp / "r").note, "ai-review.md from repo, fix.md from yours")


class BackToBoard(unittest.TestCase):
    def leave(self, focused):
        tools = Tools()
        spaces = {"result": {"workspaces": [{"workspace_id": "w1", "focused": focused}]}}
        with mock.patch("workshop.tools.run_json", return_value=spaces), mock.patch.object(Tools, "open_board") as board:
            tools.back_to_board("w1:p1")
        return board.called

    def test_only_if_you_are_in_the_workspace_that_is_going(self):
        self.assertTrue(self.leave(True))
        self.assertFalse(self.leave(False))


if __name__ == "__main__":
    unittest.main()
