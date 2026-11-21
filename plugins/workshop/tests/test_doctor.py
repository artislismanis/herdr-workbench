import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.doctor import launch_test, pipelines, prompts, run_folder, source_check, workflow, worktree_files  # noqa: E402
from workshop.tools import Tools  # noqa: E402


class Launch(unittest.TestCase):
    def test_starts_an_agent_the_way_a_run_does(self):
        tools = mock.create_autospec(Tools, instance=True)
        tools.worktree.return_value = Path("/w")
        tools.workspace.return_value = "p1"
        with mock.patch("workshop.doctor.Tools", return_value=tools), mock.patch("workshop.doctor.run"), mock.patch("workshop.doctor.subprocess.run"):
            self.assertTrue(launch_test(Path("/r"), "r", "main").ok)
        tools.close_pane.assert_called_once_with("p1")


class PermissionMode(unittest.TestCase):
    def start(self, settings):
        tmp = Path(tempfile.mkdtemp())
        if settings is not None:
            (tmp / "settings.json").write_text(settings)
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(tmp)}), mock.patch("workshop.tools.run") as called:
            Tools().start_agent("a", "p1", Path("/runs/x"))
        argv = called.call_args.args
        return argv[argv.index("--permission-mode") + 1]

    def test_an_agent_starts_in_your_mode_not_the_repos(self):
        self.assertEqual(self.start(json.dumps({"permissions": {"defaultMode": "auto"}})), "auto")

    def test_an_advisor_goes_in_as_a_setting(self):
        with mock.patch("workshop.tools.run") as called:
            Tools().start_agent("a", "p1", Path("/runs/x"), "sonnet", "opus")
        argv = called.call_args.args
        self.assertEqual(json.loads(argv[argv.index("--settings") + 1]), {"advisorModel": "opus"})
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet")

    def test_without_a_mode_of_yours_it_starts_in_the_default_one(self):
        self.assertEqual(self.start(None), "default")
        self.assertEqual(self.start("{}"), "default")


class RunFolder(unittest.TestCase):
    def check(self, projects):
        tmp = Path(tempfile.mkdtemp())
        (tmp / ".claude.json").write_text(json.dumps({"projects": projects}))
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(tmp)}):
            return run_folder(Path("/s/workshop/runs")).ok

    def test_a_trusted_run_folder_passes(self):
        self.assertTrue(self.check({"/s/workshop/runs": {"hasTrustDialogAccepted": True}}))

    def test_trust_on_a_folder_above_covers_it(self):
        self.assertTrue(self.check({"/s": {"hasTrustDialogAccepted": True}}))

    def test_an_untrusted_run_folder_fails(self):
        self.assertFalse(self.check({"/s/workshop/runs/x": {"hasTrustDialogAccepted": True}, "/s/workshop": {}}))


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

    def test_names_the_workflow_file_in_use(self):
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.dict(os.environ, {"WORKSHOP_PROMPTS": str(tmp / "mine" / "prompts"), "WORKSHOP_REPOS": str(tmp / "repos")}):
            self.assertEqual(workflow("r", tmp / "r").note, "workshop's own")
            (tmp / "repos" / "r").mkdir(parents=True)
            (tmp / "repos" / "r" / "workflow.md").write_text("- Stacks: git-spice")
            self.assertEqual(workflow("r", tmp / "r").note, str(tmp / "repos" / "r" / "workflow.md"))

    def test_warns_when_a_gitignored_env_wont_reach_worktrees(self):
        tmp = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(tmp)], check=True)
        self.assertEqual(worktree_files("r", tmp).note, "")
        (tmp / ".env").write_text("A=1\n")
        self.assertEqual(worktree_files("r", tmp).note, "")
        (tmp / ".gitignore").write_text(".env\n")
        self.assertIn(".worktreeinclude is missing", worktree_files("r", tmp).note)
        (tmp / ".worktreeinclude").write_text("**/CLAUDE.local.md\n")
        self.assertIn("doesn't list .env", worktree_files("r", tmp).note)
        (tmp / ".worktreeinclude").write_text("# personal\n/.env\n")
        self.assertEqual(worktree_files("r", tmp).note, ".env copied through .worktreeinclude")
        self.assertTrue(worktree_files("r", tmp).ok)

    def test_a_step_whose_prompt_is_nowhere_fails(self):
        tmp = Path(tempfile.mkdtemp())
        pipeline = {"name": "x", "steps": [{"id": "a", "kind": "agent", "prompt": "security.md"}, {"id": "b", "kind": "agent", "prompt": "implement.md"}]}
        with mock.patch.dict(os.environ, {"WORKSHOP_PROMPTS": str(tmp / "mine")}):
            found = prompts("r", tmp / "r", [pipeline])
            self.assertFalse(found.ok)
            self.assertEqual(found.detail, f"no security.md in {os.environ['WORKSHOP_REPOS']}/r/prompts or {tmp}/r/.workshop/prompts or {tmp}/mine")
            (tmp / "mine").mkdir()
            (tmp / "mine" / "security.md").write_text("Check it.")
            self.assertTrue(prompts("r", tmp / "r", [pipeline]).ok)


    def test_says_where_each_pipeline_comes_from_and_which_are_broken(self):
        tmp = Path(tempfile.mkdtemp())
        mine = tmp / "repos" / "r" / "pipelines"
        mine.mkdir(parents=True)
        with mock.patch.dict(os.environ, {"WORKSHOP_REPOS": str(tmp / "repos")}):
            found, loaded = pipelines("r", tmp / "r", ["ticket", "review"])
            self.assertEqual((found.ok, found.note, len(loaded)), (True, "workshop's own", 2))
            (mine / "ticket.toml").write_text('steps = ["implement", "securty"]\n')
            found, loaded = pipelines("r", tmp / "r", ["ticket", "review"])
            self.assertEqual((found.ok, found.note, len(loaded)), (False, "ticket from yours for this repo", 1))
            self.assertEqual(found.detail, "ticket: workshop has no step called securty, so define it with [step.securty]")


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


class Source(unittest.TestCase):
    JIRA = {"base_url": "https://x.test", "email": "a@b", "token": ""}

    def test_a_jira_repo_without_a_token_fails_before_searching(self):
        # Jira answers a search without a login, with nothing found, so "0 ready" proved nothing.
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("workshop.sources.JiraSource.ready") as ready:
            found = source_check("etl", {"path": "/r", "source": "jira"}, self.JIRA)
        self.assertFalse(found.ok)
        self.assertIn("no Jira token", found.detail)
        ready.assert_not_called()

    def test_a_jira_repo_says_who_it_signs_in_as(self):
        with mock.patch("workshop.sources.JiraSource.login", return_value="Sam"), mock.patch("workshop.sources.JiraSource.ready", return_value=["AB-1"]):
            found = source_check("etl", {"path": "/r", "source": "jira"}, {**self.JIRA, "token": "t"})
        self.assertTrue(found.ok)
        self.assertEqual(found.note, "1 ready, as Sam")

    def test_a_resolution_jira_doesnt_have_fails(self):
        moves = {"merged": {"status": "Done", "resolution": "Done"}, "dropped": {"status": "Done", "resolution": "Won't do"}}
        with mock.patch("workshop.sources.JiraSource.login", return_value="Sam"), mock.patch("workshop.sources.JiraSource._call", return_value=[{"name": "Done"}, {"name": "Won't Do"}]):
            found = source_check("etl", {"path": "/r", "source": "jira"}, {**self.JIRA, "token": "t", "moves": moves})
        self.assertFalse(found.ok)
        self.assertIn("Won't do", found.detail)
