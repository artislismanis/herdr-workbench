import collections
import curses
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.backlog import SNAPSHOT  # noqa: E402
from workshop.board import BACKLOG, DELIVERY, NOTE_ROWS, REVIEW, Board  # noqa: E402
from workshop.boardview import ago, backlog, bar, block, cell, clean, columns, deliveries, drill, first_line, limit_style, pipe, pull, pulls, run_style, stage_groups, stage_state, usage_line, usage_view  # noqa: E402
from workshop.engine import Engine  # noqa: E402
from workshop.shell import ToolError  # noqa: E402
from workshop.store import Run, Store  # noqa: E402
from workshop.tools import Tools  # noqa: E402

PIPELINE = {"name": "x", "steps": [{"id": "plan", "kind": "agent"}, {"id": "verify", "kind": "agent"}, {"id": "pr", "kind": "draft_pr"}]}
WIDE = 160


def run(key="1", status="running", **fields):
    return Run(repo="r", key=key, repo_path="/", base="main", pipeline=PIPELINE, status=status, **fields)


def event(kind, key="1", **fields):
    return {"at": "2026-10-31T12:30:00Z", "type": kind, "repo": "r", "key": key, **fields}


class Pasted:
    """Keys waiting to be read, as curses hands them over."""

    def __init__(self, data):
        self.data = list(data)

    def getch(self):
        return self.data.pop(0) if self.data else curses.ERR

    def timeout(self, delay):
        pass


def texts(lines):
    return [line.text for line in lines]


def joined(lines):
    return "\n".join(texts(lines))


class Names(unittest.TestCase):
    def test_a_stop_says_what_you_do_about_it(self):
        self.assertEqual((run(status="waiting").label, run(status="parked").label), ("decide", "fix"))


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

    def test_a_dropped_run_opens_at_the_step_it_stopped(self):
        lines = texts(drill(run(status="done", landed="dropped", step=1), [], "", "", "/f", 80))
        self.assertTrue(any(t.startswith("◐ verify") for t in lines))

    def test_a_dropped_run_shows_where_it_stopped(self):
        self.assertTrue("".join(s.text for s in pipe(run(status="done", landed="dropped", step=1))).startswith("●─◐─○"))

    def test_the_step_strip_shows_where_a_run_is(self):
        self.assertTrue("".join(s.text for s in pipe(run(step=1))).startswith("●─◐─○"))
        self.assertTrue("".join(s.text for s in pipe(run(status="done", step=3))).startswith("●─●─●"))


class PullDetail(unittest.TestCase):
    def test_threads_and_comments_show_with_what_blocks_in_red(self):
        lines = pull(pr(discussion=[{"path": "a.py", "line": 6, "resolved": False, "said": [{"who": "sam", "text": "blocking: wrong tax\x1b[2J"}]}, {"path": "b.py", "line": None, "resolved": True, "said": [{"who": "sam", "text": "nit"}]}], comments=[{"who": "alex", "text": "Thanks"}]))
        text = texts(lines)
        self.assertIn("  a.py:6", text)
        self.assertIn("  b.py:outdated  resolved", text)
        blocking = next(line for line in lines if "wrong tax" in line.text)
        self.assertEqual(blocking.style, "red")
        self.assertNotIn("\x1b", blocking.text)
        self.assertIn("    alex: Thanks", text)

    def test_an_older_snapshot_shows_no_discussion(self):
        self.assertFalse(any("Threads" in t for t in texts(pull(pr()))))


class Deliveries(unittest.TestCase):
    def test_what_needs_you_comes_first(self):
        lines = deliveries([run("1"), run("2", "parked", reason="It broke"), run("3", "waiting", reason="Which API?")], [], lambda r: "Title", WIDE)
        self.assertEqual([line.run.key for line in lines if line.run], ["2", "3", "1"])
        self.assertIn("It broke", next(line.text for line in lines if line.run and line.run.key == "2"))
        self.assertIn(" 2  needs you", lines[1].text)

    def test_limits_show_on_one_line(self):
        self.assertIn("5h 40%, 2h left", deliveries([], [], lambda r: "", WIDE, {"5h": (40, 7200)})[0].text)

    def test_usage_sits_above_the_counts(self):
        self.assertIn("needs you", deliveries([], [], lambda r: "", WIDE, {"5h": (40, 7200)})[1].text)

    def test_limits_are_coloured_by_whether_they_last_until_the_reset(self):
        hour, day = 3600, 86400
        self.assertEqual(limit_style("5h", 50, hour / 4), "green")
        self.assertEqual(limit_style("7d", 50, 2 * day), "green")
        self.assertEqual(limit_style("5h", 50, 3.5 * hour), "yellow")
        self.assertEqual(limit_style("5h", 50, 4 * hour), "red")
        self.assertEqual(limit_style("7d", 50, 6 * day), "red")
        self.assertEqual(limit_style("5h", 100, 60), "red")
        self.assertEqual(limit_style("5h", 0, 0), "green")

    def test_the_machine_shows_when_it_can_be_read(self):
        found = {"cpu": 40, "cores": 8, "ram": 80, "memory": 31.4, "cpu_peak": 90, "ram_peak": 85}
        line = deliveries([], [], lambda r: "", WIDE, {"5h": (58.0, 2880)}, found)[0]
        self.assertEqual(line.text.split(), ["Agents:", "5h", "58%,", "48m", "left", "OS:", "cpu", "40%", "ram", "80%"])

    def test_all_is_well_is_quiet_and_only_trouble_has_colour(self):
        line = usage_line({}, {"cpu": 40, "ram": 80})
        self.assertEqual([s.style for s in line.spans if s.text.strip()], ["bold", "", "yellow"])

    def test_the_usage_tab_says_when_theres_nothing_yet(self):
        self.assertIn("No samples yet", joined(usage_view([], [], None, 1000.0)))

    def test_the_usage_tab_shows_machine_limits_work_and_spend_together(self):
        now = datetime(2026, 11, 2, 10, 30).timestamp()
        rows = [{"at": now - 600, "cpu": 61.0, "ram": 18.0, "five": 58, "week": 40, "agents": 4}, {"at": now - 300, "cpu": 20.0, "ram": 20.0, "five": 59, "week": 40, "agents": 1}]
        spent = [event("spent", step="ai-review", usd=1.5, model="claude-sonnet-5-5") | {"at": datetime.fromtimestamp(now - 60, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}]
        text = joined(usage_view(rows, spent, {"cores": 16, "memory": 47.0}, now))
        self.assertIn("16 cores", text)
        self.assertRegex(text, r"10:00\s+61%\s+20%\s+59%\s+4\s+\$1.50")
        self.assertIn("Most of this week's spend: claude-sonnet-5-5, on ai-review", text)

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

    def test_the_latest_activity_comes_first(self):
        events = [event("claimed"), event("step", step="plan", state="done"), event("finished")]
        lines = texts(deliveries([], events, lambda r: "", WIDE))
        self.assertIn("finished", lines[-3])
        self.assertIn("claimed", lines[-1])

    def test_nothing_yet(self):
        self.assertIn("No tickets picked up yet.", joined(deliveries([], [], lambda r: "", WIDE)))


class Backlog(unittest.TestCase):
    snapshot = {"repos": {"r": {"polled_at": 1.0, "error": "", "items": [
        {"key": "1", "title": "New one", "url": "", "column": "new"},
        {"key": "2", "title": "Ready", "url": "", "column": "designed"},
        {"key": "3", "title": "Queued", "url": "", "column": "queued"},
        {"key": "4", "title": "Started", "url": "", "column": "queued"},
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
        self.assertTrue(any("plan" in t and "1 round" in t and "$0.50" in t for t in lines))
        self.assertTrue(any("verify" in t and "2 rounds" in t and "PASS" in t and "4.0 min" in t and "$1.00" in t for t in lines))
        self.assertTrue(any(t.startswith("◐ pr") for t in lines))

    def test_steps_behind_the_run_are_done_even_without_events(self):
        self.assertTrue(any(t.strip().startswith("● plan") for t in texts(drill(run(step=1), [], "", "", "/f", 80))))

    def test_steps_are_grouped_by_stage_when_the_pipeline_names_them(self):
        steps = [{"id": "a", "kind": "agent", "stage": "Build"}, {"id": "b", "kind": "agent", "stage": "Build"}, {"id": "c", "kind": "agent", "stage": "Test"}, {"id": "d", "kind": "agent"}]
        staged = run()
        staged.pipeline = {"name": "x", "steps": steps}
        lines = texts(drill(staged, [], "", "", "/f", 80))
        heads = [i for i, t in enumerate(lines) if t.startswith(" Build ")]
        self.assertEqual(len(heads), 1)
        self.assertLess(heads[0], next(i for i, t in enumerate(lines) if t.startswith(" Test ")))
        self.assertTrue(lines[heads[0] + 1].startswith(" ├ "))
        self.assertTrue(lines[heads[0] + 2].startswith(" └ "))
        self.assertEqual(lines[heads[0] + 3], "")

    def test_rows_under_a_step_follow_the_tree(self):
        steps = [{"id": "a", "kind": "agent", "stage": "Build"}, {"id": "b", "kind": "agent", "stage": "Build"}]
        staged = run(step=0, status="parked")
        staged.pipeline = {"name": "x", "steps": steps}
        lines = texts(drill(staged, [], "", "", "/f", 90, ("/f/a.md", "/f/notes.json", "/f/brief.md"), ("a.md", "## Findings\n- Broken\n")))
        self.assertTrue(any(t.startswith(" │   ┌ Findings") for t in lines))
        last = next(t for t in lines if "notes.json" in t)
        self.assertTrue(last.startswith("     "))
        self.assertNotIn("│", last)

    def test_a_flat_pipeline_has_no_tree(self):
        where = texts(drill(run(), [], "", "", "/f", 80))
        self.assertFalse(any(t.startswith(("│", "├", "└")) for t in where))

    def test_a_stage_shows_its_status_and_total(self):
        steps = [{"id": "a", "kind": "agent", "stage": "Build"}, {"id": "b", "kind": "agent", "stage": "Build"}, {"id": "c", "kind": "agent", "stage": "Test"}]
        staged = run(step=2, status="parked")
        staged.pipeline = {"name": "x", "steps": steps}
        spent = [event("spent", step="a", minutes=1.0, usd=0.1), event("spent", step="b", minutes=2.0, usd=0.2)]
        lines = drill(staged, spent, "", "", "/f", 80)
        build = next(line for line in lines if line.text.startswith(" Build "))
        test = next(line for line in lines if line.text.startswith(" Test "))
        self.assertEqual(build.text, " Build   done   3.0 min  $0.30")
        self.assertEqual((build.spans[0].style, test.spans[0].style), ("on-green", "on-red"))
        self.assertIn("fix", test.text)

    def test_a_pipeline_without_stages_shows_plain_steps(self):
        lines = texts(drill(run(), [], "", "", "/f", 80))
        self.assertNotIn("Build", lines)
        self.assertTrue(any(t.startswith("○ ") or t.startswith("◐ ") for t in lines))

    def test_a_stopped_run_says_why_and_what_next(self):
        lines = texts(drill(run(status="parked", reason="word " * 40), [], "", "workshop resume 1", "/f", 40))
        self.assertTrue(all(len(t) <= 40 for t in lines[2:6]))
        self.assertIn("Then: workshop resume 1", lines)

    def test_long_text_wraps_rather_than_cuts(self):
        lines = drill(run(pr="https://github.com/o/r/pull/" + "1" * 80), [], "A very long title " * 8, "", "/f", 40)
        self.assertTrue(all(columns(line.text) <= 40 for line in lines[:8]))
        self.assertIn("A very long title", joined(lines))
        self.assertEqual(joined(lines[:9]).count("title"), 8)

    def test_a_stopped_step_shows_its_report(self):
        lines = drill(run(status="parked"), [], "", "", "/f", 80, stopped=("verify.md", "## Needs you\nIs this right?"))
        self.assertIn("Is this right?", joined(lines[:6]))

    report = "# Review\n\n## Findings\n- Nothing was built.\n\n## Needs you\n- Is an empty change right?\n\nVERDICT: CHANGES_NEEDED\n"

    def stopped_view(self, **fields):
        return drill(run(status="parked", step=1, reason="Ran out of rounds", **fields), [], "T", "", "/f", 90, ("/f/verify.md", "/f/verify-1.md", "/f/verify-2.md", "/f/brief.md"), ("verify.md", self.report))

    def test_needs_you_comes_before_the_steps(self):
        where = texts(self.stopped_view())
        self.assertLess(where.index("  - Is an empty change right?"), where.index("─ Workflow " + "─" * 77))

    def test_subheadings_stay_in_their_section(self):
        text = "## Findings\n### Critical\n- Broken\n## Needs you\n- Decide\n"
        self.assertIn("Broken", joined(drill(run(status="parked", step=1), [], "", "", "/f", 90, ("/f/verify.md",), ("verify.md", text))))

    def test_a_narrow_panel_keeps_the_report_in_view(self):
        row = next(line for line in self.stopped_view_at(56) if line.text.startswith("◐ verify"))
        self.assertLessEqual(columns(row.text), 56)
        self.assertIn("+2 older", row.text)

    def stopped_view_at(self, width):
        return drill(run(status="parked", step=1, reason="R"), [], "T", "", "/f", width, ("/f/verify.md", "/f/verify-1.md", "/f/verify-2.md"), ("verify.md", self.report))

    def test_a_report_without_needs_you_falls_back_to_the_reason(self):
        lines = drill(run(status="parked", reason="Timed out"), [], "", "", "/f", 80, stopped=("plan.md", "No headings here"))
        self.assertIn("  Timed out", texts(lines))

    def test_nothing_under_needs_you_counts_as_empty(self):
        text = "## Needs you\nNothing.\n"
        self.assertIn("  Timed out", texts(drill(run(status="parked", reason="Timed out"), [], "", "", "/f", 80, stopped=("plan.md", text))))

    def test_the_stopped_step_shows_findings_in_place(self):
        where = texts(self.stopped_view())
        self.assertLess(next(i for i, t in enumerate(where) if t.startswith("◐ verify")), next(i for i, t in enumerate(where) if "Nothing was built" in t))

    def test_a_step_shows_its_latest_report_and_hides_earlier_rounds(self):
        row = next(line for line in self.stopped_view() if line.text.startswith("◐ verify"))
        self.assertEqual(row.path, "/f/verify.md")
        self.assertIn("+2 older", row.text)
        self.assertNotIn("verify-1.md", joined(self.stopped_view()))

    def test_the_report_to_read_first_is_marked(self):
        row = next(line for line in self.stopped_view() if line.text.startswith("◐ verify"))
        self.assertIn("▸ verify.md", row.text)

    def test_older_rounds_name_their_round(self):
        lines = drill(run(status="parked", step=1), [], "", "", "/f", 90, ("/f/verify.md", "/f/verify-2.md"), earlier=True)
        self.assertIn("verify-2.md  round 2", joined(lines))

    def test_ticket_files_say_what_they_are(self):
        self.assertIn("the ticket", joined(self.stopped_view()))

    def test_a_dropped_run_still_shows_why_it_stopped(self):
        lines = drill(run(status="done", landed="dropped", step=1), [], "", "", "/f", 90, ("/f/verify.md",), ("verify.md", self.report))
        self.assertIn("Nothing was built", joined(lines))

    def test_earlier_rounds_expand_newest_first(self):
        lines = drill(run(status="parked", step=1), [], "", "", "/f", 90, ("/f/verify.md", "/f/verify-1.md", "/f/verify-2.md"), earlier=True)
        rows = [line.path for line in lines if line.path]
        self.assertEqual(rows, ["/f/verify.md", "/f/verify-2.md", "/f/verify-1.md"])

    def test_the_ticket_leads_the_steps_and_notes_sit_under_the_last_agent(self):
        where = texts(self.stopped_view_with_notes())
        ticket = next(i for i, t in enumerate(where) if "brief.md" in t)
        note = next(i for i, t in enumerate(where) if "notes.json" in t)
        self.assertTrue(where[ticket].lstrip("● ").startswith("ticket"))
        self.assertLess(ticket, next(i for i, t in enumerate(where) if t.startswith("◐ verify")))
        self.assertGreater(note, next(i for i, t in enumerate(where) if t.startswith("◐ verify")))
        self.assertFalse(any("Ticket and notes" in t for t in where))

    def stopped_view_with_notes(self):
        return drill(run(status="parked", step=1), [], "T", "", "/f", 90, ("/f/verify.md", "/f/notes.json", "/f/brief.md"), ("verify.md", self.report))

    def test_a_ticket_stage_heads_the_ticket_and_counts_in_the_strip(self):
        staged = run(status="done", step=3)
        staged.pipeline = {**PIPELINE, "ticket_stage": "Intake"}
        where = texts(drill(staged, [], "T", "", "/f", 90, ("/f/brief.md",)))
        self.assertTrue(any(t.startswith(" Intake   done") for t in where))
        self.assertEqual([s.text for s in pipe(staged) if s.text in ("●", "◐", "○")].count("●"), len(PIPELINE["steps"]) + 1)


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


class Strip(unittest.TestCase):
    STEPS = [{"id": "a", "kind": "agent", "stage": "Build"}, {"id": "b", "kind": "agent", "stage": "Build"}, {"id": "c", "kind": "agent", "stage": "Test"}, {"id": "d", "kind": "draft_pr", "stage": "Deploy"}]

    def marks(self, **fields):
        staged = run(**fields)
        staged.pipeline = {"name": "x", "steps": self.STEPS}
        return [(s.text, s.style) for s in pipe(staged) if s.text in "●◐○" and s.text]

    def test_one_mark_per_stage(self):
        self.assertEqual(self.marks(step=1), [("◐", "green"), ("○", "dim"), ("○", "dim")])

    def test_finished_stages_are_green(self):
        self.assertEqual(self.marks(status="done", step=4), [("●", "green")] * 3)

    def test_a_stopped_stage_is_red(self):
        self.assertEqual(self.marks(status="parked", step=2), [("●", "green"), ("◐", "red"), ("○", "dim")])

    def test_waiting_on_a_person_is_amber(self):
        self.assertEqual(self.marks(status="waiting", step=0)[0], ("◐", "yellow"))

    def test_a_stage_that_went_round_again_stays_amber(self):
        self.assertEqual(self.marks(status="done", step=4, loops={"c": 1}), [("●", "green"), ("●", "yellow"), ("●", "green")])

    def test_each_stage_has_a_word(self):
        staged = run(status="done", step=4, loops={"c": 1})
        staged.pipeline = {"name": "x", "steps": self.STEPS}
        self.assertEqual([stage_state(staged, g)[2] for g in stage_groups(self.STEPS)], ["done", "done after rework", "done"])

    def test_attempt_counters_arent_rework(self):
        self.assertEqual(self.marks(status="done", step=4, loops={"c#": 2})[1], ("●", "green"))

    def test_a_dropped_stage_is_red(self):
        self.assertEqual(self.marks(status="done", landed="dropped", step=2)[1], ("◐", "red"))


class Errors(unittest.TestCase):
    def test_only_the_first_line_of_a_tools_error_shows(self):
        self.assertEqual(first_line("gh api failed: run gh auth login\nAlternatively, set GH_TOKEN"), "gh api failed: run gh auth login")


class Statuses(unittest.TestCase):
    def test_waiting_on_people_and_finished_work_dont_look_like_buttons(self):
        self.assertEqual(run_style(run(status="done")), "on-others")
        self.assertEqual(run_style(run(status="done", landed="dropped")), "dim")
        self.assertEqual(block("dim"), "dim")


class Colours(unittest.TestCase):
    def hues(self, available):
        with mock.patch.multiple(curses, use_default_colors=mock.DEFAULT, color_pair=mock.DEFAULT, COLORS=available, create=True), mock.patch.object(curses, "init_pair") as made:
            curses.color_pair.side_effect = lambda number: number
            styles = Board.colours(mock.Mock())
        return styles, {number: (front, behind) for number, front, behind in (c.args for c in made.call_args_list)}

    def test_someone_elses_work_is_cyan_on_the_terminals_own_scheme(self):
        styles, pairs = self.hues(256)
        self.assertEqual(pairs[styles["others"]], (6, -1))
        self.assertEqual(pairs[styles["on-others"]], (0, 6))
        self.assertEqual(pairs[styles["row-"]], (-1, 8))

    def test_bright_colours_fall_back_on_eight(self):
        styles, pairs = self.hues(8)
        self.assertEqual(pairs[styles["red"]], (1, -1))
        self.assertEqual(pairs[styles["row-"]], (-1, 0))


class Screen:
    """Enough of a curses window to draw on."""

    def __init__(self, width=120):
        self.width, self.marked = width, []

    def getmaxyx(self):
        return 30, self.width

    def erase(self):
        pass

    def refresh(self):
        pass

    def addnstr(self, y, x, text, size, attr):
        pass

    def chgat(self, y, x, size, attr):
        self.marked.append((y, x, size))


class Keys(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name))
        for key in ("1", "2"):
            self.store.save(run(key, pane=f"w{key}:p1"))
            self.store.event(run(key), "claimed")
        self.tools = mock.Mock()
        engine = mock.Mock(store=self.store, tools=self.tools, **{"reviewable.return_value": False, "review_ready.return_value": False, "delivered.return_value": False, "handover.return_value": ""})
        engine.throttle.hold_new.return_value = ""
        engine.throttle.pace.return_value = {"5h": (10, 3600)}
        engine.choices = lambda found: Engine.choices(engine, found)
        self.board = Board(engine, {"repos": {}}, lambda: 500.0)

    def tap(self, x, y, width):
        self.board.mouse(x, y, curses.BUTTON1_PRESSED, width)
        return self.board.mouse(x, y, curses.BUTTON1_RELEASED, width)

    def drag(self, start, end, width=119):
        self.board.mouse(*start, curses.BUTTON1_PRESSED, width)
        self.board.mouse(*end, curses.REPORT_MOUSE_POSITION, width)
        return self.board.mouse(*end, curses.BUTTON1_RELEASED, width)

    def drawn(self, width=120):
        self.board.draw(Screen(width), collections.defaultdict(int))

    def spot(self, wanted):
        return next((left, top) for top, left, _, action in self.board.regions if action == wanted)

    def test_enter_opens_the_selected_run_and_esc_goes_back(self):
        self.board.press(curses.KEY_DOWN)
        self.board.press(10)
        self.assertEqual(self.board.open, ("r", "2"))
        self.assertIn("Workflow", joined(self.board.lines(100)))
        self.board.press(27)
        self.assertIsNone(self.board.open)

    def test_q_backs_out_of_a_run_then_leaves(self):
        self.board.press(10)
        self.assertTrue(self.board.press(ord("q")))
        self.assertFalse(self.board.press(ord("q")))

    def test_it_opens_on_delivery_with_tabs_in_workflow_order(self):
        self.assertEqual(self.board.tab, 1)
        self.assertLess(self.board.header().text.index("Backlog"), self.board.header().text.index("Delivery"))
        self.assertLess(self.board.header().text.index("Delivery"), self.board.header().text.index("Review"))

    def test_shift_tab_goes_to_backlog_where_nothing_opens(self):
        self.board.press(curses.KEY_BTAB)
        self.board.press(10)
        self.assertEqual((self.board.tab, self.board.open), (0, None))

    def test_shift_tab_goes_back_and_wraps_round(self):
        self.board.press(curses.KEY_BTAB)
        self.assertEqual(self.board.tab, 0)
        self.board.press(curses.KEY_BTAB)
        self.assertEqual(self.board.tab, 3)

    def test_a_pr_opens_and_g_finds_its_run(self):
        self.store.save(run("1", pane="w1:p1", pr="u"))
        self.store.write(SNAPSHOT, {"repos": {}, "prs": {"polled_at": 200.0, "error": "", "items": [pr()]}})
        self.board.reload()
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
        self.assertIn(" Delivery 1 ", self.board.header().text)

    def test_r_refreshes_once_at_a_time(self):
        with mock.patch("workshop.board.threading.Thread") as thread:
            self.board.press(ord("r"))
            self.board.press(ord("r"))
            self.assertEqual(thread.call_count, 1)
            thread.call_args.kwargs["target"]()
        self.tools.workshop.assert_called_once_with("refresh")
        self.assertFalse(self.board.refreshing)

    def stopped(self, status="parked", step=0):
        self.store.save(run("1", status, step=step, reason="Stuck", pane="w1:p1"))
        self.board.seen = None
        self.board.reload()

    def type_in(self, text):
        for letter in text:
            self.board.press(ord(letter))

    def sent(self):
        with mock.patch("workshop.board.threading.Thread") as thread:
            self.board.press(10)
            thread.call_args.kwargs["target"]()
        return self.tools.workshop.call_args.args

    def test_a_running_run_offers_no_actions(self):
        self.assertNotIn("drop", self.board.footer().text)
        self.board.press(ord("d"))
        self.assertIsNone(self.board.asking)

    def test_a_stopped_run_offers_answer_and_drop(self):
        self.stopped()
        footer = self.board.footer().text
        self.assertIn("a answer", footer)
        self.assertIn("d drop", footer)
        self.assertEqual(self.board.run_actions(self.board.runs[0]), {ord("a"): "a answer", ord("d"): "d drop"})

    def test_a_blank_answer_on_a_stop_runs_it_again(self):
        self.stopped()
        self.board.press(ord("a"))
        self.assertIn("› Run it again", joined(self.board.note(80)))
        self.assertEqual(self.sent(), ("retry", "1"))

    def test_words_on_a_stop_steer_the_next_try(self):
        self.stopped()
        self.board.press(ord("a"))
        self.type_in("use q and r")
        self.assertEqual(self.sent(), ("retry", "1", "use q and r"))

    def noted(self, text, width=40):
        self.stopped()
        self.board.press(ord("a"))
        self.type_in(text)
        return texts(self.board.note(width))

    def test_a_long_note_wraps_and_stays_in_view(self):
        shown = self.noted("word " * 30)
        self.assertGreater(len(shown), 3)
        self.assertTrue(all(columns(line) <= 40 for line in shown))
        self.assertIn("▌", "".join(shown))

    def test_a_very_long_note_stops_growing_and_follows_the_cursor(self):
        shown = self.noted("word " * 200)
        self.assertLessEqual(len(shown), NOTE_ROWS + 8)
        self.assertIn("▌", "".join(shown))
        for _ in range(900):
            self.board.press(curses.KEY_LEFT)
        self.assertIn("▌", joined(self.board.note(40)))

    def test_a_note_keeps_the_report_in_view(self):
        self.noted("short")
        self.assertLess(len(self.board.note(100)), NOTE_ROWS + 3)

    def test_the_cursor_moves_and_typing_goes_there(self):
        self.noted("helo")
        self.board.press(curses.KEY_LEFT)
        self.type_in("l")
        self.assertEqual(self.board.typed, "hello")
        self.board.press(curses.KEY_HOME)
        self.type_in("o")
        self.board.press(curses.KEY_END)
        self.type_in("!")
        self.assertEqual(self.board.typed, "ohello!")

    def test_delete_takes_the_next_letter(self):
        self.noted("abc")
        self.board.press(curses.KEY_HOME)
        self.board.press(curses.KEY_DC)
        self.assertEqual(self.board.typed, "bc")

    def test_backspace_works_mid_note(self):
        self.noted("abc")
        self.board.press(curses.KEY_LEFT)
        self.board.press(127)
        self.assertEqual(self.board.typed, "ac")

    def test_ctrl_w_takes_a_word_and_ctrl_u_the_rest(self):
        self.noted("one two three")
        self.board.press(23)
        self.assertEqual(self.board.typed, "one two ")
        self.board.press(21)
        self.assertEqual(self.board.typed, "")

    def test_esc_keeps_a_draft_for_that_key_and_run(self):
        self.noted("half a thought")
        self.board.press(27)
        self.board.press(ord("a"))
        self.assertEqual(self.board.typed, "half a thought")
        self.type_in(" done")
        self.board.press(27)
        self.board.press(ord("d"))
        self.assertEqual(self.board.typed, "")

    def test_sending_clears_the_draft(self):
        self.noted("words")
        self.assertEqual(self.sent(), ("retry", "1", "words"))
        self.board.press(ord("a"))
        self.assertEqual(self.board.typed, "")

    def test_a_paste_goes_in_whole_even_with_line_breaks(self):
        self.noted("start ")
        self.board.paste(Pasted(b"[200~one\r\ntwo\xc2\xa3\033[201~"))
        self.assertEqual(self.board.typed, "start one\ntwo£")
        self.assertIsNotNone(self.board.asking)

    def test_a_pasted_note_shows_its_lines(self):
        self.noted("")
        self.board.paste(Pasted(b"[200~one\ntwo\033[201~"))
        self.assertIn("   one", texts(self.board.note(80)))

    def test_a_plain_escape_is_still_an_escape(self):
        self.noted("x")
        with mock.patch("workshop.board.curses.ungetch"):
            self.assertEqual(self.board.paste(Pasted(b"")), 27)

    def test_keys_after_an_escape_arent_lost(self):
        self.noted("x")
        with mock.patch("workshop.board.curses.ungetch") as back:
            self.assertEqual(self.board.paste(Pasted(b"ab")), 27)
        self.assertEqual([c.args[0] for c in back.call_args_list], [ord("b"), ord("a")])

    def test_a_failure_stays_until_a_key_and_shows_in_full(self):
        self.tools.workshop.side_effect = ToolError("word " * 40)
        self.stopped()
        self.board.press(ord("d"))
        self.sent()
        self.board.clock = lambda: 5000.0
        self.assertGreater(len(self.board.note(40)), 3)
        self.assertEqual(joined(self.board.note(40)).count("word"), 40)
        self.board.press(ord("j"))
        self.assertEqual(len(self.board.note(40)), 1)

    def test_typing_takes_backspace_and_esc_cancels(self):
        self.stopped()
        self.board.press(ord("a"))
        self.type_in("ab")
        self.board.press(127)
        self.assertEqual(self.board.typed, "a")
        self.board.press(27)
        self.assertIsNone(self.board.asking)
        self.tools.workshop.assert_not_called()

    def test_enter_during_another_command_keeps_your_words(self):
        self.stopped()
        self.board.press(ord("a"))
        self.type_in("keep me")
        self.board.refreshing = True
        self.board.press(10)
        self.assertEqual((self.board.asking.command, self.board.typed), ("retry", "keep me"))

    def reported(self):
        self.board.press(10)
        path = self.store.files(run("1")) / "plan.md"
        path.write_text("Line one\n\nLine two\n", encoding="utf-8")
        return str(path)

    def test_a_report_opens_and_esc_returns_to_the_run(self):
        path = self.reported()
        self.assertIn("plan.md", joined(self.board.lines(100)))
        self.board.press(10)
        self.assertEqual(self.board.viewing, path)
        self.assertIn("Line two", joined(self.board.lines(100)))
        self.board.press(27)
        self.assertEqual((self.board.viewing, self.board.open), ("", ("r", "1")))

    def test_clicking_a_report_opens_it(self):
        path = self.reported()
        self.drawn()
        x, y = self.spot(("#file", path))
        self.tap(x + 3, y, 119)
        self.assertEqual(self.board.viewing, path)

    def test_the_status_follows_the_title_and_pr(self):
        lines = texts(drill(run(status="done", pr="http://p/1"), [], "T", "", "/f", 90, handover="- New flag\nRecommendation: approve"))
        self.assertTrue(lines[1].startswith("PR:"))
        self.assertIn("in review", lines[2])
        self.assertLess(2, next(i for i, t in enumerate(lines) if "Recommendation" in t))

    def draft(self):
        self.board.engine.reviewable.return_value = self.board.engine.review_ready.return_value = True
        self.store.save(run("7", status="done", pr="http://p/7", pane="w1:p1"))
        self.board.picked = ("r", "7")
        self.board.seen = None
        self.board.reload()

    def test_a_draft_only_offers_the_review(self):
        self.draft()
        self.assertIn("g review", self.board.footer().text)
        self.assertNotIn("ready", self.board.footer().text)
        self.board.press(ord("m"))
        self.assertIsNone(self.board.asking)

    def test_g_on_a_draft_goes_there_then_reopens_the_review(self):
        self.draft()
        with mock.patch("workshop.board.threading.Thread") as thread:
            self.board.press(ord("g"))
            self.tools.focus.assert_called_once_with("w1:p1")
            thread.call_args.kwargs["target"]()
        self.tools.workshop.assert_called_once_with("review", "7")

    def on_pr(self, **fields):
        self.store.write(SNAPSHOT, {"repos": {}, "prs": {"polled_at": 200.0, "error": "", "items": [pr(**fields)]}})
        self.board.seen = None
        self.board.reload()
        self.board.tab = REVIEW
        self.board.picked = ("#pr", self.board.snapshot["prs"]["items"][0]["url"])

    def test_a_key_starts_an_ai_review_of_the_selected_pr(self):
        self.on_pr()
        self.assertIn("a ai review", self.board.footer().text)
        url = self.board.picked[1]
        with mock.patch("workshop.board.threading.Thread") as thread:
            self.board.press(ord("a"))
            thread.call_args.kwargs["target"]()
        self.tools.workshop.assert_called_once_with("pr", url)

    def test_a_pr_with_a_run_or_merged_offers_no_ai_review(self):
        self.on_pr(move="done")
        self.assertNotIn("a ai review", self.board.footer().text)
        self.on_pr()
        self.store.save(run("pr-7", pr=self.board.picked[1]))
        self.board.seen = None
        self.board.reload()
        self.assertNotIn("a ai review", self.board.footer().text)
        self.board.press(ord("a"))
        self.tools.workshop.assert_not_called()

    def test_a_capital_key_uses_the_other_mode_and_m_claims(self):
        self.on_pr()
        url = self.board.picked[1]
        self.assertIn("A ai review, claimed", self.board.footer().text)
        with mock.patch("workshop.board.threading.Thread") as thread:
            self.board.press(ord("A"))
            thread.call_args.kwargs["target"]()
        self.tools.workshop.assert_called_once_with("pr", url, "--claimed")
        self.tools.workshop.reset_mock()
        self.store.save(run("pr-7", status="done", pr=url, reviewing=True))
        self.board.seen = None
        self.board.reload()
        self.assertIn("m claim", self.board.footer().text)
        with mock.patch("workshop.board.threading.Thread") as thread:
            self.board.press(ord("m"))
            thread.call_args.kwargs["target"]()
        self.tools.workshop.assert_called_once_with("claim", "pr-7")

    def test_a_claimed_review_offers_no_claim(self):
        self.on_pr()
        self.store.save(run("pr-7", status="done", pr=self.board.picked[1], reviewing=True, claimed=True))
        self.board.seen = None
        self.board.reload()
        self.assertNotIn("m claim", self.board.footer().text)

    def test_comments_on_your_pr_offer_an_ai_reply(self):
        self.on_pr(mine=True, state="comments to address")
        self.assertIn("a ai reply", self.board.footer().text)
        self.assertNotIn("a ai review", self.board.footer().text)
        url = self.board.picked[1]
        with mock.patch("workshop.board.threading.Thread") as thread:
            self.board.press(ord("a"))
            thread.call_args.kwargs["target"]()
        self.tools.workshop.assert_called_once_with("reply", url)

    def test_a_reply_under_way_offers_no_second_one(self):
        self.on_pr(mine=True, state="changes requested")
        self.store.save(run("reply-7", pr=self.board.picked[1], replying=True))
        self.board.seen = None
        self.board.reload()
        self.assertNotIn("a ai reply", self.board.footer().text)

    def test_new_commits_offer_another_ai_review(self):
        self.board.engine.review_ready.return_value = True
        self.on_pr(state="re-review")
        url = self.board.picked[1]
        self.store.save(run("pr-7", status="done", pr=url))
        self.board.seen = None
        self.board.reload()
        self.assertIn("a ai review", self.board.footer().text)
        self.on_pr(state="waiting for fixes")
        self.assertNotIn("a ai review", self.board.footer().text)

    def test_a_message_clears_by_itself(self):
        now = [500.0]
        self.board.clock = lambda: now[0]
        self.board.say("Hello")
        self.assertIn("Hello", self.board.footer().text)
        now[0] += 6
        self.assertIn("g workspace", self.board.footer().text)

    def test_a_failed_command_shows_why(self):
        self.tools.workshop.side_effect = ToolError("no such run")
        self.stopped()
        self.board.press(ord("d"))
        self.sent()
        self.assertIn("no such run", self.board.footer().text)

    def test_p_on_the_backlog_asks_which_ticket_to_prepare(self):
        self.board.tab = BACKLOG
        self.assertIn("p prepare", self.board.footer().text)
        self.board.press(ord("p"))
        self.assertIn("idea", joined(self.board.note(100)))
        self.type_in("7")
        self.assertEqual(self.sent(), ("prepare", "7"))

    def test_no_prepare_key_away_from_the_backlog(self):
        self.board.tab = REVIEW
        self.assertNotIn("p prepare", self.board.footer().text)

    def test_your_turn_counts_as_waiting_on_you_and_can_be_dropped(self):
        self.store.save(Run(repo="r", key="prep-7", repo_path="/", base="main", pipeline=PIPELINE, preparing=True, your_turn=True))
        self.board.seen = None
        self.board.reload()
        self.board.tab = DELIVERY
        self.assertEqual(self.board.counts()[1], 1)
        self.assertEqual(list(self.board.run_actions(self.board.runs[-1]).values()), ["d drop"])

    def at_gate(self):
        steps = [{"id": "tasks", "kind": "agent"}, {"id": "approve", "kind": "gate", "report": "tasks", "ask": "Are these the right tasks?", "go": "Start building"}]
        found = Run(repo="r", key="1", repo_path="/", base="main", pipeline={"name": "x", "steps": steps}, status="waiting", step=1, reason="Are these the right tasks?")
        self.store.save(found)
        (self.store.files(found) / "tasks.md").write_text("Add the flag in main.\n\nVERDICT: READY\n", encoding="utf-8")
        self.board.seen = None
        self.board.reload()
        self.board.tab = DELIVERY
        return found

    def test_a_gate_shows_what_youre_deciding(self):
        found = self.at_gate()
        text = joined(self.board.detail((found.repo, found.key), 100))
        self.assertIn("Are these the right tasks?", text)
        self.assertIn("Add the flag in main.", text)
        self.assertNotIn("VERDICT", text)

    def test_a_gate_says_what_the_work_is_for(self):
        found = self.at_gate()
        (self.store.files(found) / "brief.md").write_text("# 1: A title\n\nPeople can't greet a team.\n", encoding="utf-8")
        lines = texts(self.board.detail((found.repo, found.key), 100))
        self.assertLess(next(i for i, t in enumerate(lines) if "can't greet" in t), next(i for i, t in enumerate(lines) if "right tasks" in t))

    def test_a_long_ticket_is_cut_and_says_where_the_rest_is(self):
        found = self.at_gate()
        (self.store.files(found) / "brief.md").write_text("# 1: A title\n\n" + "line\n" * 30, encoding="utf-8")
        text = joined(self.board.detail((found.repo, found.key), 100))
        self.assertIn("more in brief.md", text)
        self.assertLess(text.count("│ line"), 10)

    def test_a_sweep_has_no_ticket_to_show(self):
        found = self.at_gate()
        found.sweeping = "docs"
        self.store.save(found)
        (self.store.files(found) / "brief.md").write_text("# k\n\nOpen tickets:\n- 1: One\n", encoding="utf-8")
        self.board.seen = None
        self.board.reload()
        self.assertNotIn("The ticket", joined(self.board.detail((found.repo, found.key), 100)))

    def test_a_gates_report_shows_once(self):
        found = self.at_gate()
        self.assertEqual(joined(self.board.detail((found.repo, found.key), 100)).count("Add the flag in main."), 1)

    def test_the_question_and_its_context_live_with_the_answer(self):
        self.at_gate()
        self.board.press(ord("a"))
        card = texts(self.board.note(100))
        self.assertLess(next(i for i, t in enumerate(card) if "right tasks" in t), next(i for i, t in enumerate(card) if "Start building" in t))

    DECISION = "## Decision\n\nWhat: Add a flag\nWhy: People asked\nBackground: Nothing parses arguments yet\nBottom line: Use argparse\nAsk: Build it this way?\n\n## Tasks\n\nLong version.\n"

    def test_the_card_is_the_brief_and_nothing_else(self):
        found = self.at_gate()
        (self.store.files(found) / "tasks.md").write_text(self.DECISION, encoding="utf-8")
        self.board.press(ord("a"))
        text = joined(self.board.note(100))
        self.assertEqual(self.board.asking.question, "Build it this way?")
        for line in ("What: Add a flag", "Why: People asked", "Background: Nothing parses", "Bottom line: Use argparse"):
            self.assertIn(line, text)
        self.assertNotIn("Long version", text)

    def test_a_brief_line_that_runs_on_is_cut(self):
        found = self.at_gate()
        (self.store.files(found) / "tasks.md").write_text("## Decision\n\nWhat: " + "word " * 200 + "\nAsk: Go?\n", encoding="utf-8")
        self.board.press(ord("a"))
        self.assertLess(joined(self.board.note(100)).count("word"), 45)

    def test_no_brief_means_no_wall_of_text(self):
        found = self.at_gate()
        (self.store.files(found) / "tasks.md").write_text("step\n" * 30, encoding="utf-8")
        self.board.press(ord("a"))
        text = joined(self.board.note(100))
        self.assertNotIn("step", text)
        self.assertIn("full report is above", text)

    MANY = "## Decision\n\nOverview: Two calls before building\n\nWhat: The name\nAsk: Call it --whisper?\nWhat: The short form\nAsk: Add -w?\n\n## Tasks\n\nLong.\n"

    def many(self):
        found = self.at_gate()
        (self.store.files(found) / "tasks.md").write_text(self.MANY, encoding="utf-8")
        self.board.press(ord("a"))
        return found

    def test_several_decisions_get_a_card_each_with_how_they_fit(self):
        self.many()
        self.assertEqual(self.board.asking.question, "1 of 2: Call it --whisper?")
        self.assertIn("Overview: Two calls before building", joined(self.board.note(100)))
        self.board.press(10)
        self.assertEqual(self.board.asking.question, "2 of 2: Add -w?")
        self.tools.workshop.assert_not_called()

    def test_agreeing_with_them_all_is_one_go(self):
        self.many()
        self.board.press(10)
        self.assertEqual(self.sent(), ("approve", "1"))

    def test_your_words_on_any_of_them_go_back_together(self):
        self.many()
        self.board.press(10)
        self.type_in("no short form")
        self.assertEqual(self.sent(), ("reject", "1", "Add -w? no short form"))

    def test_esc_keeps_your_place_in_a_series(self):
        self.many()
        self.board.press(10)
        self.board.press(27)
        self.board.press(ord("a"))
        self.assertEqual(self.board.asking.question, "2 of 2: Add -w?")

    def asked(self, reason):
        self.store.save(Run(repo="r", key="1", repo_path="/", base="main", pipeline={"name": "x", "steps": [{"id": "plan", "kind": "agent"}]}, status="waiting", reason=reason))
        self.board.seen = None
        self.board.reload()
        self.board.press(ord("a"))

    def test_an_agents_recommendation_can_be_agreed_with(self):
        self.asked("plan asks: What: Two APIs\nBottom line: Use v2\nAsk: Which one?")
        self.assertIn("› Agree", joined(self.board.note(100)))
        self.assertEqual(self.sent(), ("retry", "1", "Agreed, go with your recommendation"))

    def test_several_questions_go_back_numbered(self):
        self.asked("plan asks: Overview: Two\nWhat: A\nBottom line: X\nAsk: One?\nWhat: B\nBottom line: Y\nAsk: Two?")
        self.board.press(10)
        self.type_in("no")
        self.assertEqual(self.sent(), ("retry", "1", "One? Agreed, go with your recommendation\nTwo? no"))

    def test_a_question_with_no_recommendation_still_needs_words(self):
        self.asked("Which one?")
        self.assertNotIn("Agree", joined(self.board.note(100)))

    def test_a_question_from_an_agent_can_be_a_brief(self):
        self.store.save(Run(repo="r", key="1", repo_path="/", base="main", pipeline={"name": "x", "steps": [{"id": "plan", "kind": "agent"}]}, status="waiting", reason="plan asks: What: Two APIs\nAsk: Which one?"))
        self.board.seen = None
        self.board.reload()
        self.board.press(ord("a"))
        self.assertEqual(self.board.asking.question, "Which one?")
        self.assertIn("What: Two APIs", joined(self.board.note(100)))

    def test_up_and_down_choose_and_typing_chooses_your_own_words(self):
        self.at_gate()
        self.board.press(ord("a"))
        self.board.press(curses.KEY_DOWN)
        self.assertIn("› Say what to change", joined(self.board.note(100)))
        self.board.press(curses.KEY_UP)
        self.assertIn("› Start building", joined(self.board.note(100)))
        self.type_in("rename it")
        self.assertIn("› Say what to change", joined(self.board.note(100)))

    def test_your_own_words_need_words(self):
        self.at_gate()
        self.board.press(ord("a"))
        self.board.press(curses.KEY_DOWN)
        self.board.press(10)
        self.assertIsNotNone(self.board.asking)

    def test_a_proposal_offers_do_skip_or_your_words(self):
        steps = [{"id": "sweep", "kind": "agent"}, {"id": "decide", "kind": "gate", "report": "sweep", "proposals": True, "go": "Do it", "change": "Tell the agent what to do instead"}]
        self.store.save(Run(repo="r", key="1", repo_path="/", base="main", pipeline={"name": "x", "steps": steps}, status="waiting", step=1, reason="1 of 2: Close 3?"))
        self.board.engine.proposal.return_value = "Why: Already done."
        self.board.engine._proposals.return_value = [{"action": "close", "key": "3", "why": "Already done."}]
        self.board.seen = None
        self.board.reload()
        self.board.press(ord("a"))
        text = joined(self.board.note(100))
        self.assertIn("1 of 2: Close 3?", text)
        self.assertIn("Already done.", text)
        self.assertIn("Close it", text)
        self.assertIn("Skip it", text)
        self.board.press(curses.KEY_DOWN)
        self.assertEqual(self.sent(), ("reject", "1", "n"))

    def question(self, reason="1 of 2: Close 3?"):
        steps = [{"id": "sweep", "kind": "agent"}, {"id": "decide", "kind": "gate", "report": "sweep", "proposals": True}]
        found = Run(repo="r", key="1", repo_path="/", base="main", pipeline={"name": "x", "steps": steps}, status="waiting", step=1, reason=reason)
        self.store.save(found)
        self.board.engine.proposal.return_value = "Why: It's done."
        self.board.engine._proposals.return_value = [{"action": "close", "key": "3", "why": "Why."}] * 2
        self.board.seen = None
        self.board.reload()
        return found

    def test_the_next_question_opens_by_itself(self):
        self.question()
        self.board.press(ord("a"))
        self.sent()
        self.question("2 of 2: Close 4?")
        self.board.carry_on()
        self.assertEqual(self.board.asking.question, "2 of 2: Close 4?")
        self.assertEqual(self.board.typed, "")

    def test_it_waits_for_the_command_to_finish(self):
        self.question()
        self.board.press(ord("a"))
        self.sent()
        self.board.refreshing = True
        self.board.carry_on()
        self.assertIsNone(self.board.asking)
        self.assertEqual(self.board.follow, "1")

    def test_a_run_thats_done_says_so_and_moves_to_what_needs_you(self):
        self.question()
        self.board.press(ord("a"))
        self.sent()
        self.store.save(Run(repo="r", key="1", repo_path="/", base="main", pipeline=PIPELINE, status="done", landed="swept"))
        self.store.save(Run(repo="r", key="2", repo_path="/", base="main", pipeline=PIPELINE, status="waiting", reason="Next"))
        self.board.seen = None
        self.board.reload()
        self.board.carry_on()
        self.assertIsNone(self.board.asking)
        self.assertEqual(self.board.picked, ("r", "2"))
        self.assertIn("swept", self.board.footer().text)

    def test_a_failed_answer_stays_on_screen_rather_than_asking_on(self):
        self.question()
        self.tools.workshop.side_effect = ToolError("gh down")
        self.board.press(ord("a"))
        self.sent()
        self.board.carry_on()
        self.assertIsNone(self.board.asking)
        self.assertIn("gh down", joined(self.board.note(80)))

    def test_the_card_names_the_run_and_doesnt_dim_what_you_read(self):
        self.question()
        self.board.press(ord("a"))
        card = self.board.note(80)
        self.assertTrue(card[0].text.startswith("─ 1 "))
        self.assertEqual(next(line for line in card if "It's done" in line.text).style, "")

    def test_no_workspace_means_no_g(self):
        self.store.save(Run(repo="r", key="1", repo_path="/", base="main", pipeline=PIPELINE, status="waiting", reason="x"))
        self.board.seen = None
        self.board.reload()
        self.assertNotIn("g workspace", self.board.footer().text)

    def test_a_blank_answer_at_a_gate_does_what_the_box_says(self):
        self.at_gate()
        self.board.press(ord("a"))
        self.assertIn("› Start building", joined(self.board.note(80)))
        self.assertEqual(self.sent(), ("approve", "1"))

    def test_words_at_a_gate_send_it_back(self):
        self.at_gate()
        self.board.press(ord("a"))
        self.type_in("call it --whisper")
        self.assertEqual(self.sent(), ("reject", "1", "call it --whisper"))

    def test_a_question_needs_words(self):
        steps = [{"id": "plan", "kind": "agent"}]
        self.store.save(Run(repo="r", key="1", repo_path="/", base="main", pipeline={"name": "x", "steps": steps}, status="waiting", reason="Which one?"))
        self.board.seen = None
        self.board.reload()
        self.board.press(ord("a"))
        self.assertIn("› Your answer", joined(self.board.note(80)))
        self.board.press(10)
        self.assertIsNotNone(self.board.asking)
        self.type_in("the first")
        self.assertEqual(self.sent(), ("retry", "1", "the first"))

    def test_a_drop_needs_no_reason(self):
        self.stopped()
        self.board.press(ord("d"))
        self.assertEqual(self.sent(), ("drop", "1"))


class Mouse(Keys):
    def test_a_drag_catches_up_in_one_go(self):
        self.board.grab = (1, 1)
        events = iter([(0, x, 5, 0, curses.REPORT_MOUSE_POSITION) for x in range(2, 40)])
        screen = Pasted(bytes())
        screen.data = [curses.KEY_MOUSE] * 38
        with mock.patch("workshop.board.curses.getmouse", side_effect=lambda: next(events)):
            self.board.drain(screen, 120)
        self.assertEqual(self.board.reach, (39, 5))

    def test_a_key_during_a_drag_isnt_lost(self):
        self.board.grab = (1, 1)
        screen = Pasted(b"j")
        with mock.patch("workshop.board.curses.ungetch") as back:
            self.board.drain(screen, 120)
        back.assert_called_once_with(ord("j"))

    def test_letting_go_during_a_drag_still_copies(self):
        self.board.grab = (1, 1)
        screen = Pasted(b"")
        screen.data = [curses.KEY_MOUSE]
        with mock.patch("workshop.board.curses.getmouse", return_value=(0, 30, 5, 0, curses.BUTTON1_RELEASED)), mock.patch.object(self.board, "copy_selection") as copied:
            self.board.drain(screen, 120)
        copied.assert_called_once()

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
        self.tap(x + 1, y, 119)
        self.assertEqual((self.board.tab, self.board.open), (2, None))

    def test_clicking_a_row_opens_it(self):
        self.drawn()
        x, y = self.spot(("r", "2"))
        self.tap(x + 40, y, 119)
        self.assertEqual(self.board.open, ("r", "2"))

    def test_a_button_does_what_its_key_does(self):
        self.board.press(10)
        self.drawn()
        x, y = self.spot("key:27")
        self.tap(x, y, 119)
        self.assertIsNone(self.board.open)

    def test_the_quit_button_leaves(self):
        self.drawn()
        x, y = self.spot(f"key:{ord('q')}")
        self.assertFalse(self.tap(x, y, 119))

    def test_clicking_nothing_does_nothing(self):
        self.drawn()
        self.assertTrue(self.tap(100, 20, 119))
        self.assertIsNone(self.board.open)

    def test_the_wheel_moves_through_rows(self):
        self.board.mouse(0, 0, curses.BUTTON5_PRESSED, 119)
        self.assertEqual(self.board.selected(self.board.lines(149)), ("r", "2"))
        self.board.mouse(0, 0, curses.BUTTON4_PRESSED, 119)
        self.assertEqual(self.board.selected(self.board.lines(149)), ("r", "1"))


class Selecting(Keys):
    def paint(self, width=170):
        self.board.draw(Screen(width), collections.defaultdict(int))
        return width - 1

    def test_a_drag_copies_what_was_dragged_over_and_doesnt_click(self):
        self.paint(120)
        self.board.painted[6] = [(0, "hello world")]
        self.drag((0, 6), (4, 6))
        self.tools.copy.assert_called_once_with("hello")
        self.assertIsNone(self.board.open)
        self.assertIn("Copied 5", self.board.footer().text)

    def test_a_drag_across_rows_takes_the_whole_rows_between(self):
        self.paint(120)
        self.board.painted.update({6: [(0, "first line")], 7: [(0, "middle")], 8: [(0, "last line")]})
        self.drag((6, 6), (3, 8))
        self.tools.copy.assert_called_once_with("line\nmiddle\nlast")

    def test_wide_letters_take_two_cells(self):
        self.paint(120)
        self.board.painted[6] = [(0, "日本 ok"), (8, "next")]
        self.drag((0, 6), (11, 6))
        self.tools.copy.assert_called_once_with("日本 ok next")

    def test_a_drag_upwards_works_too(self):
        self.paint(120)
        self.board.painted[6] = [(0, "hello")]
        self.drag((4, 6), (0, 6))
        self.tools.copy.assert_called_once_with("hello")

    def test_a_drag_stays_in_the_column_it_began_in(self):
        self.paint()
        edge = self.board.edge
        self.board.painted[6] = [(0, "list side"), (edge + 2, "panel side")]
        self.drag((edge + 11, 6), (0, 6), 169)
        self.tools.copy.assert_called_once_with("panel side")

    def test_the_drag_shows_as_it_goes(self):
        screen = Screen(120)
        self.board.draw(screen, collections.defaultdict(int))
        self.board.mouse(0, 6, curses.BUTTON1_PRESSED, 119)
        self.board.mouse(9, 6, curses.REPORT_MOUSE_POSITION, 119)
        self.board.draw(screen, collections.defaultdict(int))
        self.assertEqual(screen.marked, [(6, 0, 10)])

    def test_a_shaky_click_is_still_a_click(self):
        self.paint(120)
        x, y = self.spot(("r", "2"))
        self.board.mouse(x + 5, y, curses.BUTTON1_PRESSED, 119)
        self.board.mouse(x + 6, y, curses.BUTTON1_RELEASED, 119)
        self.assertEqual(self.board.open, ("r", "2"))
        self.tools.copy.assert_not_called()

    def test_dragging_over_nothing_copies_nothing(self):
        self.paint(120)
        self.board.painted = {}
        self.drag((0, 20), (5, 20))
        self.tools.copy.assert_not_called()

    def test_a_failed_copy_says_why(self):
        self.tools.copy.side_effect = ToolError("wl-copy failed")
        self.paint(120)
        self.board.painted[6] = [(0, "hello")]
        self.drag((0, 6), (4, 6))
        self.assertIn("wl-copy failed", self.board.footer().text)


class Panel(Mouse):
    def wide(self):
        self.drawn(170)
        return 169

    def test_the_selected_item_shows_beside_the_list(self):
        self.assertIn("Workflow", joined(self.board.panel(self.wide())))

    def test_a_stopped_run_explains_itself_with_no_click(self):
        self.store.save(run("1", "parked", pane="w1:p1", reason="Stuck"))
        (self.store.files(run("1")) / "plan.md").write_text("The real reason\n", encoding="utf-8")
        self.board.seen = None
        self.board.reload()
        self.assertIn("The real reason", joined(self.board.panel(self.wide())))

    def test_a_narrow_window_keeps_one_column(self):
        self.assertFalse(self.board.panelled(120))

    def test_backlog_has_no_panel(self):
        self.board.press(curses.KEY_BTAB)
        self.assertFalse(self.board.panelled(169))

    def test_a_click_selects_rather_than_opens(self):
        width = self.wide()
        x, y = self.spot(("r", "2"))
        self.tap(x + 5, y, width)
        self.assertEqual((self.board.picked, self.board.open), (("r", "2"), None))

    def test_a_stopped_run_has_its_buttons_on_the_bottom_row_only(self):
        self.store.save(run("1", "parked", pane="w1:p1"))
        self.board.seen = None
        self.board.reload()
        width = self.wide()
        self.assertNotIn("d drop", joined(self.board.panel(width)))
        self.assertIn("d drop", self.board.footer(width).text)
        spots = [region for region in self.board.regions if region[3] == f"key:{ord('d')}"]
        self.tap(spots[0][1], spots[0][0], width)
        self.assertEqual(self.board.asking.command, "drop")

    def test_a_report_shows_in_the_panel_and_esc_closes_it(self):
        path = self.store.files(run("1")) / "plan.md"
        path.write_text("The plan\n", encoding="utf-8")
        width = self.wide()
        x, y = self.spot(("#file", str(path)))
        self.tap(x, y, width)
        self.assertIn("The plan", joined(self.board.panel(width)))
        self.assertIn("r/1", joined(self.board.lines(width)))
        self.board.press(27, width)
        self.assertEqual(self.board.viewing, "")

    def test_tab_leaves_the_report(self):
        self.board.viewing = "x"
        self.board.press(ord("\t"), 169)
        self.assertEqual(self.board.viewing, "")

    def test_moving_on_leaves_the_report(self):
        self.board.viewing = "x"
        self.board.press(curses.KEY_DOWN, 169)
        self.assertEqual(self.board.viewing, "")

    def test_the_wheel_over_the_panel_scrolls_it(self):
        width = self.wide()
        self.board.mouse(160, 10, curses.BUTTON5_PRESSED, width)
        self.assertEqual((self.board.side, self.board.selected(self.board.lines(width))), (3, ("r", "1")))


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
