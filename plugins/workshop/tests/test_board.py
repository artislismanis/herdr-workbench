import curses
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.backlog import SNAPSHOT  # noqa: E402
from workshop.board import Board, ago, backlog, clean, deliveries, drill  # noqa: E402
from workshop.shell import ToolError  # noqa: E402
from workshop.store import Run, Store  # noqa: E402
from workshop.tools import Tools  # noqa: E402

PIPELINE = {"name": "x", "steps": [{"id": "plan", "kind": "agent"}, {"id": "verify", "kind": "agent"}, {"id": "pr", "kind": "draft_pr"}]}


def run(key="1", status="running", **fields):
    return Run(repo="r", key=key, repo_path="/", base="main", pipeline=PIPELINE, status=status, **fields)


def event(kind, key="1", **fields):
    return {"at": "2026-10-31T12:30:00Z", "type": kind, "repo": "r", "key": key, **fields}


def texts(lines):
    return [line.text for line in lines]


class Text(unittest.TestCase):
    def test_controls_never_reach_the_terminal(self):
        self.assertEqual(clean("a\x1b]0;x\x07b\nc‮"), "a ]0;x b c ")

    def test_ages(self):
        self.assertEqual([ago(s) for s in (5, 120, 7200, 172800)], ["5s", "2m", "2h", "2d"])


class Deliveries(unittest.TestCase):
    def test_what_needs_you_comes_first(self):
        lines = deliveries([run("1"), run("2", "parked", reason="It broke"), run("3", "waiting", reason="Which API?")], [], lambda r: "Title")
        rows = [line.run.key for line in lines if line.run]
        self.assertEqual(rows, ["2", "3", "1"])
        self.assertIn("It broke", next(line.text for line in lines if line.run and line.run.key == "2"))

    def test_only_the_latest_landed_runs_stay(self):
        runs = [run(str(n), "done", landed="merged") for n in range(8)]
        events = [{**event("landed", str(n)), "at": f"2026-10-0{n + 1}T00:00:00Z"} for n in range(8)]
        self.assertEqual([line.run.key for line in deliveries(runs, events, lambda r: "") if line.run], ["3", "4", "5", "6", "7"])

    def test_the_log_leaves_out_cost_lines(self):
        lines = deliveries([run()], [event("spent", step="plan"), event("parked")], lambda r: "")
        self.assertEqual(lines[-1].style, "red")
        self.assertFalse(any("spent" in t for t in texts(lines)))

    def test_nothing_yet(self):
        self.assertIn("No tickets picked up yet.", texts(deliveries([], [], lambda r: "")))


class Backlog(unittest.TestCase):
    snapshot = {"repos": {"r": {"polled_at": 1.0, "error": "", "items": [
        {"key": "1", "title": "New one", "url": "", "stage": "new"},
        {"key": "2", "title": "Ready", "url": "", "stage": "designed"},
        {"key": "3", "title": "Queued", "url": "", "stage": "queued"},
        {"key": "4", "title": "Started", "url": "", "stage": "queued"},
    ]}}}

    def test_counts_by_stage_and_leaves_out_started_tickets(self):
        lines = texts(backlog(self.snapshot, {}, lambda repo, key: key == "4"))
        self.assertIn("New 1   Planned 0   Designed 1   Queued 1", lines)
        self.assertFalse(any("Started" in t for t in lines))

    def test_shows_why_a_queued_ticket_was_skipped(self):
        lines = backlog(self.snapshot, {"r/3": "last edited by them"}, lambda repo, key: False)
        self.assertIn("skipped: last edited by them", next(line.text for line in lines if "Queued" in line.text and "r/3" in line.text))

    def test_shows_a_failed_source(self):
        snapshot = {"repos": {"r": {"polled_at": 1.0, "error": "gh failed", "items": []}}}
        self.assertEqual(backlog(snapshot, {}, lambda repo, key: False)[0].style, "red")

    def test_says_so_before_the_first_poll(self):
        self.assertIn("Press r", backlog({}, {}, lambda repo, key: False)[0].text)


class Drill(unittest.TestCase):
    def test_shows_each_steps_rounds_verdict_and_cost(self):
        spent = {"minutes": 2.0, "usd": 0.5}
        started = event("step", step="verify", state="running")
        events = [event("step", step="plan", state="running"), event("spent", step="plan", **spent), event("step", step="plan", state="done", verdict=""), started, started, event("spent", step="verify", **spent), event("spent", step="verify", **spent), event("step", step="verify", state="done", verdict="PASS")]
        lines = texts(drill(run(step=2), events, "Title", "", "/files", 100))
        self.assertTrue(any("plan" in t and "1 round(s)" in t and "$0.50" in t for t in lines))
        self.assertTrue(any("verify" in t and "2 round(s)" in t and "PASS" in t and "4.0 min" in t and "$1.00" in t for t in lines))
        self.assertTrue(any(t.strip().startswith("running") and "pr" in t for t in lines))

    def test_a_stopped_run_says_why_and_what_next(self):
        lines = texts(drill(run(status="parked", reason="word " * 40), [], "", "workshop resume 1", "/f", 40))
        self.assertTrue(all(len(t) <= 40 for t in lines[2:6]))
        self.assertIn("Then: workshop resume 1", lines)

    def test_unpriced_cost_is_labelled(self):
        lines = texts(drill(run(), [event("spent", step="plan", minutes=1.0, usd=None)], "", "", "/f", 80))
        self.assertTrue(any("unpriced" in t for t in lines))


class Keys(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name))
        for key in ("1", "2"):
            self.store.save(run(key, pane=f"w{key}:p1"))
            self.store.event(run(key), "claimed")
        self.tools = mock.Mock()
        engine = mock.Mock(store=self.store, tools=self.tools)
        engine.throttle.hold_new.return_value = ""
        self.board = Board(engine, {"repos": {}}, lambda: 500.0)

    def test_enter_opens_the_selected_run_and_esc_goes_back(self):
        self.board.press(curses.KEY_DOWN)
        self.board.press(10)
        self.assertEqual(self.board.open, ("r", "2"))
        self.assertIn("Steps", texts(self.board.lines(100)))
        self.board.press(27)
        self.assertIsNone(self.board.open)

    def test_q_backs_out_of_a_run_then_leaves(self):
        self.board.press(10)
        self.assertTrue(self.board.press(ord("q")))
        self.assertFalse(self.board.press(ord("q")))

    def test_tab_switches_and_nothing_on_backlog_opens(self):
        self.board.press(ord("\t"))
        self.board.press(10)
        self.assertIsNone(self.board.open)

    def test_the_selection_follows_the_run_when_rows_reorder(self):
        self.board.press(curses.KEY_DOWN)
        self.store.save(run("1", "parked", pane="w1:p1"))
        self.board.seen = None
        self.board.reload()
        self.assertEqual(self.board.selected(self.board.lines(100)), ("r", "2"))

    def test_g_goes_to_the_run_and_reports_a_lost_workspace(self):
        self.board.press(ord("g"))
        self.tools.focus.assert_called_once_with("w1:p1")
        self.tools.focus.side_effect = ToolError("gone")
        self.board.press(ord("g"))
        self.assertIn("no workspace", self.board.footer())
        self.board.press(curses.ERR)
        self.assertIn("no workspace", self.board.footer())
        self.board.press(curses.KEY_DOWN)
        self.assertNotIn("no workspace", self.board.footer())

    def test_a_new_file_shows_on_the_next_reload_and_opening_makes_no_calls(self):
        self.assertIn("not polled", self.board.header())
        self.store.write(SNAPSHOT, {"repos": {"r": {"polled_at": 200.0, "error": "", "items": []}}})
        self.board.reload()
        self.assertIn("backlog 5m old", self.board.header())
        self.tools.assert_not_called()

    def test_r_polls_once_at_a_time(self):
        with mock.patch("workshop.board.poll") as polled, mock.patch("workshop.board.threading.Thread") as thread:
            self.board.press(ord("r"))
            self.board.press(ord("r"))
            self.assertEqual(thread.call_count, 1)
            thread.call_args.kwargs["target"]()
            polled.assert_called_once()
        self.assertFalse(self.board.refreshing)


class OpenBoard(unittest.TestCase):
    def test_goes_back_to_an_open_board(self):
        spaces = {"result": {"workspaces": [{"workspace_id": "w1", "label": "repo"}, {"workspace_id": "w2", "label": "workshop"}]}}
        with mock.patch("workshop.tools.run_json", return_value=spaces), mock.patch("workshop.tools.run") as called:
            Tools().open_board()
        called.assert_called_once_with("herdr", "workspace", "focus", "w2")

    def test_opens_one_when_there_is_none(self):
        answers = [{"result": {"workspaces": []}}, {"result": {"root_pane": {"pane_id": "w3:p1"}}}]
        with mock.patch("workshop.tools.run_json", side_effect=answers), mock.patch("workshop.tools.run") as called:
            Tools().open_board()
        self.assertEqual(called.call_args.args[:3], ("herdr", "pane", "run"))
        self.assertTrue(called.call_args.args[4].startswith("exec ") and called.call_args.args[4].endswith("workshop board"))


if __name__ == "__main__":
    unittest.main()
