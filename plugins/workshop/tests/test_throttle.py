import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.throttle import STALE_AFTER, WEEK, Throttle  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
