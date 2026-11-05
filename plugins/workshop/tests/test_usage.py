import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.usage import DAY, KEEP_DAYS, SAMPLES, by_load, outlook, sample, samples, top, totals, week_cost  # noqa: E402


class Sampling(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.meter = mock.Mock()
        self.meter.read.return_value = {"cpu": 12.34, "ram": 50.0}

    def take(self, now=1000.0):
        sample(self.root, {"5h": 40, "7d": 20}, 3, self.meter, lambda: now, lambda seconds: None)

    def test_a_sample_notes_the_machine_limits_and_work(self):
        self.take()
        self.assertEqual(samples(self.root), [{"at": 1000.0, "cpu": 12.3, "ram": 50.0, "five": 40, "week": 20, "agents": 3}])

    def test_cpu_gets_two_readings(self):
        self.take()
        self.assertEqual(self.meter.read.call_count, 2)

    def test_old_samples_go(self):
        now = (KEEP_DAYS + 5) * DAY
        (self.root / SAMPLES).write_text(json.dumps({"at": 1.0}) + "\n")
        self.take(now)
        self.assertEqual([s["at"] for s in samples(self.root)], [now])

    def test_a_cut_line_doesnt_lose_the_rest(self):
        (self.root / SAMPLES).write_text('{"at": 1.0}\n{"at"')
        self.assertEqual(len(samples(self.root)), 1)


class Totals(unittest.TestCase):
    def test_today_starts_at_midnight(self):
        now = datetime(2026, 11, 2, 10).timestamp()
        rows = [{"at": now - 3600, "cpu": 10, "ram": 1, "five": 1, "agents": 1}, {"at": now - 11 * 3600, "cpu": 90, "ram": 1, "five": 1, "agents": 1}]
        self.assertEqual(totals(rows, [], now, 1)["cpu"], 10)
        self.assertEqual(totals(rows, [], now, 7)["cpu"], 90)

    def test_a_sample_from_before_a_field_existed_still_reads(self):
        now = datetime(2026, 11, 2, 10).timestamp()
        self.assertEqual(totals([{"at": now - 60, "cpu": 1, "ram": 1, "five": 1}], [], now, 1)["agents"], 0)

    def test_tokens_are_counted_in_and_out(self):
        now = datetime(2026, 11, 2, 10).timestamp()
        spent = [{"at": "2026-11-02T09:00:00Z", "usd": 1.0, "tokens_in": 1000, "tokens_out": 50}, {"at": "2026-11-02T09:30:00Z", "usd": None}]
        found = totals([], spent, now, 1)
        self.assertEqual((found["tokens_in"], found["tokens_out"], found["spend"]), (1000, 50, 1.0))

    def test_old_runs_with_no_model_still_count(self):
        now = datetime(2026, 11, 2, 10).timestamp()
        event = {"at": "2026-11-02T09:00:00Z", "usd": 1.0, "step": "tasks"}
        self.assertEqual(top([event], now), ("not recorded", "tasks"))


class Outlook(unittest.TestCase):
    HOUR = 3600

    def test_a_steady_pace_that_lasts(self):
        # 6% in the first 24 minutes, 4h36m to go: 15% an hour reaches 75%
        self.assertEqual(outlook("5h", 6, 4.6 * self.HOUR), (75, 0))

    def test_a_pace_that_runs_out_says_when(self):
        self.assertEqual(outlook("5h", 50, 4 * self.HOUR), (250, self.HOUR))

    def test_too_early_to_tell(self):
        self.assertEqual(outlook("5h", 3, 5 * self.HOUR - 60), (None, 0))

    def test_no_reset_known(self):
        self.assertEqual(outlook("7d", 20, 0), (None, 0))


class WeekCost(unittest.TestCase):
    def rows(self, *pairs):
        return [{"at": n * 300.0, "five": five, "week": week} for n, (five, week) in enumerate(pairs)]

    def test_what_a_full_5h_window_takes_from_the_week(self):
        rows = self.rows(*[(n * 10, 20 + n) for n in range(11)])
        self.assertAlmostEqual(week_cost(rows, 3300.0), 10.0)

    def test_resets_dont_count(self):
        rows = self.rows((0, 20), (60, 26), (0, 26), (60, 32), (60, 0))
        self.assertAlmostEqual(week_cost(rows, 1200.0), 10.0)

    def test_too_little_use_to_say(self):
        self.assertIsNone(week_cost(self.rows((0, 20), (10, 21)), 300.0))


class Load(unittest.TestCase):
    def test_peaks_by_how_many_agents_were_open(self):
        rows = [{"at": 100.0, "cpu": 5, "ram": 10, "agents": 1}, {"at": 200.0, "cpu": 9, "ram": 20, "agents": 4}, {"at": 300.0, "cpu": 3, "ram": 15, "agents": 3}, {"at": 400.0, "cpu": 1, "ram": 99}]
        found = by_load(rows, 400.0)
        self.assertEqual([(b["agents"], b["samples"], b["cpu"], b["ram"]) for b in found], [("0-2", 1, 5, 10), ("3-4", 2, 9, 20)])


if __name__ == "__main__":
    unittest.main()
