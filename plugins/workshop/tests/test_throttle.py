import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.throttle import STALE_AFTER, WEEK, Throttle, ahead, live_windows  # noqa: E402

NOW = 10_000_000.0
LATER = NOW + 3600
HALF_WEEK_LEFT = NOW + WEEK / 2


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "usage.json"
        self.throttle = Throttle(self.path, {}, lambda: NOW)

    def usage(self, five=None, week=None, at=NOW, five_resets=LATER, week_resets=HALF_WEEK_LEFT):
        data = {"at": at}
        if five is not None:
            data["five_hour"] = {"used_percentage": five, "resets_at": five_resets}
        if week is not None:
            data["seven_day"] = {"used_percentage": week, "resets_at": week_resets}
        self.path.write_text(json.dumps(data), encoding="utf-8")


class Steps(Case):
    def test_clear_at_the_limit(self):
        self.usage(five=85, week=75)
        self.assertEqual(self.throttle.hold_steps(), "")

    def test_held_above_the_5h_limit(self):
        self.usage(five=86)
        self.assertIn("5h usage is 86%", self.throttle.hold_steps())

    def test_held_inside_the_weekly_reserve(self):
        self.usage(week=76)
        self.assertIn("25% reserve", self.throttle.hold_steps())

    def test_a_window_past_its_reset_counts_as_zero(self):
        self.usage(five=99, week=99, five_resets=NOW - 1, week_resets=NOW - 1)
        self.assertEqual(self.throttle.hold_steps(), "")
        self.assertEqual(self.throttle.snapshot(), {"5h": 0, "7d": 0})

    def test_an_old_high_reading_still_holds(self):
        self.usage(five=90, at=NOW - STALE_AFTER * 4)
        self.assertNotEqual(self.throttle.hold_steps(), "")

    def test_missing_data_never_holds_a_step(self):
        self.assertEqual(self.throttle.hold_steps(), "")

    def test_a_missing_window_counts_as_zero(self):
        self.usage(week=25)
        self.assertEqual(self.throttle.snapshot(), {"5h": 0, "7d": 25})

    def test_a_broken_file_counts_as_missing(self):
        self.path.write_text("{half", encoding="utf-8")
        self.assertEqual(self.throttle.hold_steps(), "")


class Tickets(Case):
    def test_clear_when_low(self):
        self.usage(five=10, week=10)
        self.assertEqual(self.throttle.hold_new(1), "")

    def test_held_above_pause_new_at(self):
        self.usage(five=61)
        self.assertIn("above 60%", self.throttle.hold_new(0))

    def test_held_when_steps_are_held(self):
        self.usage(five=90)
        self.assertIn("above 85%", self.throttle.hold_new(0))

    def test_held_ahead_of_the_weekly_pace(self):
        self.usage(week=56)
        self.assertIn("ahead of the 50%", self.throttle.hold_new(0))

    def test_clear_on_the_weekly_pace_with_the_margin(self):
        self.usage(week=55)
        self.assertEqual(self.throttle.hold_new(0), "")

    def test_the_weekly_pace_forgets_a_past_reset(self):
        self.usage(week=70, week_resets=NOW - 1)
        self.assertEqual(self.throttle.hold_new(0), "")

    def test_missing_data_runs_one_ticket(self):
        self.assertEqual(self.throttle.hold_new(0), "")
        self.assertIn("one ticket", self.throttle.hold_new(1))

    def test_stale_data_runs_one_ticket(self):
        self.usage(five=1, at=NOW - STALE_AFTER - 1)
        self.assertEqual(self.throttle.hold_new(0), "")
        self.assertIn("one ticket", self.throttle.hold_new(1))

    def test_settings_replace_the_defaults(self):
        self.usage(five=61)
        self.assertEqual(Throttle(self.path, {"pause_new_at": 70}, lambda: NOW).hold_new(0), "")


class Live(unittest.TestCase):
    TEXT = (
        "You are currently using your subscription to power your Claude Code usage\n\n"
        "Current session: 83% used · resets Oct 31, 7:10pm (Europe/London)\n"
        "Current week (all models): 45% used · resets Nov 2, 1am (Europe/London)\n\n"
        "Last 24h · 475 requests · 65 sessions\n"
    )
    NOW = 1793470000.0  # 31 Oct 2026, 18:06 UTC

    def path(self):
        return Path(tempfile.mkdtemp()) / "usage.json"

    def test_both_windows_are_read(self):
        found = live_windows(self.TEXT, self.NOW)
        self.assertEqual(found["five_hour"], {"used_percentage": 83, "resets_at": 1793473800.0})
        self.assertEqual(found["seven_day"], {"used_percentage": 45, "resets_at": 1793581200.0})

    def test_a_reset_after_new_year_is_next_year(self):
        text = "Current session: 1% used · resets Jan 1, 12:10am (Europe/London)\n"
        self.assertEqual(live_windows(text, self.NOW)["five_hour"]["resets_at"], 1798762200.0)

    def test_a_bare_time_earlier_today_is_tomorrow(self):
        text = "Current session: 1% used · resets 5pm (UTC)\n"
        self.assertEqual(live_windows(text, self.NOW)["five_hour"]["resets_at"], 1793552400.0)

    def test_an_unknown_zone_is_skipped(self):
        self.assertEqual(live_windows("Current session: 1% used · resets 5pm (Mars/Base)\n", self.NOW), {})

    def test_an_update_replaces_what_it_read_and_keeps_the_rest(self):
        path = self.path()
        path.write_text(json.dumps({"seven_day": {"used_percentage": 9, "resets_at": 1}, "extra": 1}), encoding="utf-8")
        Throttle(path, {}, clock=lambda: self.NOW).update("Current session: 20% used · resets 8pm (UTC)\n")
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((saved["five_hour"]["used_percentage"], saved["seven_day"]["used_percentage"], saved["extra"], saved["at"]), (20, 9, 1, self.NOW))

    def test_text_it_cant_read_leaves_the_file_alone(self):
        path = self.path()
        path.write_text('{"at": 1}', encoding="utf-8")
        Throttle(path, {}, clock=lambda: self.NOW).update("Usage is shown in the app\n")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"at": 1})



class Pace(unittest.TestCase):
    def test_ahead_is_measured_against_an_even_pace_to_the_reset(self):
        self.assertEqual(ahead("5h", 50, 2.5 * 3600), 0)
        self.assertEqual(ahead("5h", 50, 4.5 * 3600), 40)
        self.assertEqual(ahead("7d", 50, WEEK), 50)

    def test_pace_gives_use_and_time_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "u.json"
            path.write_text(json.dumps({"five_hour": {"used_percentage": 40, "resets_at": 1600}, "seven_day": {"used_percentage": 9, "resets_at": 900}}))
            self.assertEqual(Throttle(path, {}, lambda: 1000).pace(), {"5h": (40, 600), "7d": (0, 0)})


if __name__ == "__main__":
    unittest.main()
