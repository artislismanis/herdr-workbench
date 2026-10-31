import collections
import curses
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.backlog import SNAPSHOT  # noqa: E402
from workshop.board import Board  # noqa: E402
from workshop.boardview import ago, backlog, bar, cell, clean, columns, deliveries, drill, pipe, pull, pulls  # noqa: E402
from workshop.shell import ToolError  # noqa: E402
from workshop.store import Run, Store  # noqa: E402
from workshop.tools import Tools  # noqa: E402

PIPELINE = {"name": "x", "steps": [{"id": "plan", "kind": "agent"}, {"id": "verify", "kind": "agent"}, {"id": "pr", "kind": "draft_pr"}]}
WIDE = 160


def run(key="1", status="running", **fields):
    return Run(repo="r", key=key, repo_path="/", base="main", pipeline=PIPELINE, status=status, **fields)


def event(kind, key="1", **fields):
    return {"at": "2026-10-31T12:30:00Z", "type": kind, "repo": "r", "key": key, **fields}


def texts(lines):
    return [line.text for line in lines]


def joined(lines):
    return "\n".join(texts(lines))


class Text(unittest.TestCase):
    def test_controls_never_reach_the_terminal(self):
        self.assertEqual(clean("a\x1b]0;x\x07b\nc‮"), "a ]0;x b c ")

    def test_ages(self):
        self.assertEqual([ago(s) for s in (5, 120, 7200, 172800)], ["5s", "2m", "2h", "2d"])

    def test_a_cell_is_always_its_width_on_screen(self):
        self.assertEqual(cell("abc", 5), "abc  ")
        self.assertEqual(cell("abcdefgh", 5), "abcd…")
        self.assertEqual(columns(cell("日本語のタイトル", 7)), 7)

    def test_bars_scale(self):
        self.assertEqual(bar(1, 2, 4), "██░░")
        self.assertEqual(bar(0, 0, 2), "░░")

    def test_the_step_strip_shows_where_a_run_is(self):
        self.assertTrue("".join(s.text for s in pipe(run(step=1))).startswith("●─◐─○"))
        self.assertTrue("".join(s.text for s in pipe(run(status="done", step=3))).startswith("●─●─●"))


class Deliveries(unittest.TestCase):
    def test_what_needs_you_comes_first(self):
        lines = deliveries([run("1"), run("2", "parked", reason="It broke"), run("3", "waiting", reason="Which API?")], [], lambda r: "Title", WIDE)
        self.assertEqual([line.run.key for line in lines if line.run], ["2", "3", "1"])
        self.assertIn("It broke", next(line.text for line in lines if line.run and line.run.key == "2"))
        self.assertIn(" 2  needs you", lines[0].text)

    def test_usage_shows_as_meters(self):
        self.assertIn("5h ████░░░░░░ 40%", deliveries([], [], lambda r: "", WIDE, {"5h": 40})[0].text)

    def test_only_the_latest_landed_runs_stay(self):
        runs = [run(str(n), "done", landed="merged") for n in range(8)]
        events = [{**event("landed", str(n)), "at": f"2026-10-0{n + 1}T00:00:00Z"} for n in range(8)]
        self.assertEqual([line.run.key for line in deliveries(runs, events, lambda r: "", WIDE) if line.run], ["3", "4", "5", "6", "7"])

    def test_rows_stay_inside_the_width(self):
        lines = deliveries([run(reason="x" * 300, status="parked")], [], lambda r: "y" * 300, 120)
        self.assertLessEqual(max(columns(line.text) for line in lines if line.run), 120)

    def test_the_log_leaves_out_cost_lines_and_marks_trouble(self):
        lines = deliveries([run()], [event("spent", step="plan"), event("parked")], lambda r: "", WIDE)
        self.assertEqual(lines[-1].style, "red")
        self.assertNotIn("spent", joined(lines))

    def test_nothing_yet(self):
        self.assertIn("No tickets picked up yet.", joined(deliveries([], [], lambda r: "", WIDE)))


class Backlog(unittest.TestCase):
    snapshot = {"repos": {"r": {"polled_at": 1.0, "error": "", "items": [
        {"key": "1", "title": "New one", "url": "", "stage": "new"},
        {"key": "2", "title": "Ready", "url": "", "stage": "designed"},
        {"key": "3", "title": "Queued", "url": "", "stage": "queued"},
        {"key": "4", "title": "Started", "url": "", "stage": "queued"},
    ]}}}

    def test_counts_by_stage_and_leaves_out_started_tickets(self):
        lines = joined(backlog(self.snapshot, {}, lambda repo, key: key == "4", WIDE))
        self.assertIn("Queued 1", lines)
        self.assertIn("Planned 0", lines)
        self.assertNotIn("Started", lines)

    def test_a_wide_window_shows_columns_side_by_side(self):
        lines = texts(backlog(self.snapshot, {}, lambda repo, key: False, WIDE))
        self.assertTrue(any("r/1" in line and "r/2" in line for line in lines))

    def test_a_narrow_one_stacks_them(self):
        lines = texts(backlog(self.snapshot, {}, lambda repo, key: False, 60))
        self.assertFalse(any("r/1" in line and "r/2" in line for line in lines))
        self.assertTrue(any("r/2" in line and "Ready" in line for line in lines))

    def test_shows_why_a_queued_ticket_was_skipped(self):
        for width in (WIDE, 60):
            self.assertIn("skipped: last edited", joined(backlog(self.snapshot, {"r/3": "last edited by them"}, lambda repo, key: False, width)))

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
        self.assertTrue(any(t.strip().startswith("◐ pr") for t in lines))

    def test_steps_behind_the_run_are_done_even_without_events(self):
        self.assertTrue(any(t.strip().startswith("● plan") for t in texts(drill(run(step=1), [], "", "", "/f", 80))))

    def test_a_stopped_run_says_why_and_what_next(self):
        lines = texts(drill(run(status="parked", reason="word " * 40), [], "", "workshop resume 1", "/f", 40))
        self.assertTrue(all(len(t) <= 40 for t in lines[2:6]))
        self.assertIn("Then: workshop resume 1", lines)

    def test_long_text_wraps_rather_than_cuts(self):
        lines = drill(run(pr="https://github.com/o/r/pull/" + "1" * 80), [], "A very long title " * 8, "", "/f", 40)
        self.assertTrue(all(columns(line.text) <= 40 for line in lines[:12]))
        self.assertIn("A very long title", joined(lines))
        self.assertEqual(joined(lines[:9]).count("title"), 8)

    def test_unpriced_cost_is_labelled(self):
        self.assertIn("unpriced", joined(drill(run(), [event("spent", step="plan", minutes=1.0, usd=None)], "", "", "/f", 80)))


def pr(url="u", move="you", mine=False, state="needs review", **fields):
    return {"repo": "r", "number": 7, "title": "A change", "url": url, "author": "them", "mine": mine, "move": move, "state": state, "checks": "SUCCESS", "threads": 0, "reviews": {}, "waiting_on": [], "beaten": [], **fields}


class PRs(unittest.TestCase):
    def rows(self, *items):
        return [line for line in pulls({"prs": {"polled_at": 1.0, "error": "", "items": list(items)}}, WIDE) if line.pr]

    def test_groups_by_whose_move_it_is(self):
        lines = pulls({"prs": {"polled_at": 1.0, "error": "", "items": [pr("a", "others", True), pr("b"), pr("c", "done", True)]}}, WIDE)
        self.assertEqual([line.pr["url"] for line in lines if line.pr], ["b", "a", "c"])
        self.assertIn("Your move 1", joined(lines))

    def test_mine_and_others_differ_by_symbol_and_colour(self):
        mine, theirs = self.rows(pr("a", mine=True), pr("b"))
        self.assertEqual((mine.text[2], mine.style), ("●", "mine"))
        self.assertEqual((theirs.text[2], theirs.style), ("○", "others"))

    def test_someone_getting_there_first_stands_out(self):
        row = self.rows(pr(beaten=["sam"]))[0]
        self.assertEqual(row.style, "yellow")
        self.assertIn("reviewed by sam", row.text)

    def test_shows_a_failed_poll_and_a_first_run(self):
        self.assertEqual(pulls({"prs": {"error": "gh failed", "items": []}})[0].style, "red")
        self.assertIn("Press r", pulls({})[0].text)

    def test_the_detail_wraps_a_long_title_and_url(self):
        lines = pull(pr(url="https://x.test/" + "a" * 80, title="Long words here " * 10), 40)
        self.assertTrue(all(columns(line.text) <= 40 for line in lines))
        self.assertEqual(joined(lines).count("words"), 10)
        self.assertIn("a" * 20, joined(lines))

    def test_the_detail_lists_reviews_and_who_is_asked(self):
        lines = texts(pull(pr(reviews={"sam": "CHANGES_REQUESTED"}, waiting_on=["alex"])))
        self.assertIn("  ✗ sam: changes requested", lines)
        self.assertIn("  … alex: asked", lines)


class Screen:
    """Enough of a curses window to draw on."""

    def getmaxyx(self):
        return 30, 150

    def erase(self):
        pass

    def refresh(self):
        pass

    def addnstr(self, y, x, text, size, attr):
        pass


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
        engine.throttle.snapshot.return_value = {"5h": 10}
        self.board = Board(engine, {"repos": {}}, lambda: 500.0)

    def drawn(self):
        self.board.draw(Screen(), collections.defaultdict(int))

    def spot(self, wanted):
        return next((left, top) for top, left, _, action in self.board.regions if action == wanted)

    def test_enter_opens_the_selected_run_and_esc_goes_back(self):
        self.board.press(curses.KEY_DOWN)
        self.board.press(10)
        self.assertEqual(self.board.open, ("r", "2"))
        self.assertIn("Steps", joined(self.board.lines(100)))
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

    def test_shift_tab_goes_back_and_wraps_round(self):
        self.board.press(curses.KEY_BTAB)
        self.assertEqual(self.board.tab, 2)
        self.board.press(curses.KEY_BTAB)
        self.assertEqual(self.board.tab, 1)

    def test_a_pr_opens_and_g_finds_its_run(self):
        self.store.save(run("1", pane="w1:p1", pr="u"))
        self.store.write(SNAPSHOT, {"repos": {}, "prs": {"polled_at": 200.0, "error": "", "items": [pr()]}})
        self.board.reload()
        self.board.press(ord("\t"))
        self.board.press(ord("\t"))
        self.assertIn("PRs 5m old", self.board.header().text)
        self.board.press(10)
        self.assertEqual(self.board.open, ("#pr", "u"))
        self.assertIn("Reviews", joined(self.board.lines(100)))
        self.board.press(ord("g"))
        self.tools.focus.assert_called_once_with("w1:p1")

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
        self.assertIn("no workspace", self.board.footer().text)
        self.board.press(curses.ERR)
        self.assertIn("no workspace", self.board.footer().text)
        self.board.press(curses.KEY_DOWN)
        self.assertNotIn("no workspace", self.board.footer().text)

    def test_a_new_file_shows_on_the_next_reload_and_opening_makes_no_calls(self):
        self.assertIn("not polled", self.board.header().text)
        self.store.write(SNAPSHOT, {"repos": {"r": {"polled_at": 200.0, "error": "", "items": []}}})
        self.board.reload()
        self.assertIn("backlog 5m old", self.board.header().text)
        self.tools.assert_not_called()

    def test_tabs_show_what_needs_attention(self):
        self.store.save(run("1", "parked"))
        self.board.seen = None
        self.board.reload()
        self.assertIn(" Deliveries 1 ", self.board.header().text)

    def test_r_polls_once_at_a_time(self):
        with mock.patch("workshop.board.poll") as polled, mock.patch("workshop.board.threading.Thread") as thread:
            self.board.press(ord("r"))
            self.board.press(ord("r"))
            self.assertEqual(thread.call_count, 1)
            thread.call_args.kwargs["target"]()
            polled.assert_called_once()
        self.assertFalse(self.board.refreshing)


class Mouse(Keys):
    def test_a_selected_row_keeps_its_colour_on_the_selection_background(self):
        drawn = []
        screen = mock.Mock(addnstr=lambda y, x, text, size, attr: drawn.append((text, attr)))
        line = deliveries([run()], [], lambda r: "", 150)[3]
        self.board.put(screen, 3, line, collections.defaultdict(int, {"row-": 1, "row-dim": 2}), 200, True)
        self.assertTrue(any(attr == 2 for text, attr in drawn))
        self.assertEqual(drawn[-1][1], 1)

    def test_the_header_sits_below_a_gap(self):
        self.drawn()
        self.assertEqual(self.spot("tab:0")[1], 1)

    def test_clicking_a_tab_switches_to_it_even_from_a_run(self):
        self.board.press(10)
        self.drawn()
        x, y = self.spot("tab:2")
        self.board.mouse(x + 1, y, curses.BUTTON1_PRESSED, 149)
        self.assertEqual((self.board.tab, self.board.open), (2, None))

    def test_clicking_a_row_opens_it(self):
        self.drawn()
        x, y = self.spot(("r", "2"))
        self.board.mouse(x + 40, y, curses.BUTTON1_PRESSED, 149)
        self.assertEqual(self.board.open, ("r", "2"))

    def test_a_button_does_what_its_key_does(self):
        self.board.press(10)
        self.drawn()
        x, y = self.spot("key:27")
        self.board.mouse(x, y, curses.BUTTON1_PRESSED, 149)
        self.assertIsNone(self.board.open)

    def test_the_quit_button_leaves(self):
        self.drawn()
        x, y = self.spot(f"key:{ord('q')}")
        self.assertFalse(self.board.mouse(x, y, curses.BUTTON1_PRESSED, 149))

    def test_clicking_nothing_does_nothing(self):
        self.drawn()
        self.assertTrue(self.board.mouse(140, 20, curses.BUTTON1_PRESSED, 149))
        self.assertIsNone(self.board.open)

    def test_the_wheel_moves_through_rows(self):
        self.board.mouse(0, 0, curses.BUTTON5_PRESSED, 149)
        self.assertEqual(self.board.selected(self.board.lines(149)), ("r", "2"))
        self.board.mouse(0, 0, curses.BUTTON4_PRESSED, 149)
        self.assertEqual(self.board.selected(self.board.lines(149)), ("r", "1"))


class OpenBoard(unittest.TestCase):
    def test_asks_herdr_to_move_it_first_and_a_failure_is_harmless(self):
        with mock.patch("workshop.tools.socket.socket") as made:
            Tools()._to_top("w3")
            sent = json.loads(made.return_value.__enter__.return_value.sendall.call_args.args[0])
            self.assertEqual((sent["method"], sent["params"]), ("workspace.move", {"workspace_id": "w3", "insert_index": 0}))
            made.return_value.__enter__.return_value.connect.side_effect = OSError("no server")
            Tools()._to_top("w3")

    def test_goes_back_to_an_open_board(self):
        spaces = {"result": {"workspaces": [{"workspace_id": "w1", "label": "repo"}, {"workspace_id": "w2", "label": "workshop"}]}}
        with mock.patch("workshop.tools.run_json", return_value=spaces), mock.patch("workshop.tools.run") as called:
            Tools().open_board()
        called.assert_called_once_with("herdr", "workspace", "focus", "w2")

    def test_opens_one_when_there_is_none(self):
        answers = [{"result": {"workspaces": []}}, {"result": {"workspace": {"workspace_id": "w3"}, "tab": {"tab_id": "w3:t1"}, "root_pane": {"pane_id": "w3:p1"}}}]
        with mock.patch("workshop.tools.run_json", side_effect=answers), mock.patch("workshop.tools.run") as called, mock.patch.object(Tools, "_to_top") as top:
            Tools().open_board()
        top.assert_called_once_with("w3")
        called.assert_any_call("herdr", "tab", "rename", "w3:t1", "Dashboard")
        self.assertEqual(called.call_args.args[:3], ("herdr", "pane", "run"))
        self.assertTrue(called.call_args.args[4].startswith("exec ") and called.call_args.args[4].endswith("workshop board"))


if __name__ == "__main__":
    unittest.main()
