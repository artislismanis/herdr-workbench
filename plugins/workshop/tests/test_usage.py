import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.usage import DAY, KEEP_DAYS, SAMPLES, at_once, sample, samples, top, totals  # noqa: E402


class Sampling(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.meter = mock.Mock()
        self.meter.read.return_value = {"cpu": 12.34, "ram": 50.0}

    def take(self, now=1000.0):
        sample(self.root, {"5h": 40, "7d": 20}, 3, self.meter, lambda: now, lambda seconds: None)

    def test_a_sample_notes_the_machine_limits_and_work(self):
        self.take()
        self.assertEqual(samples(self.root), [{"at": 1000.0, "cpu": 12.3, "ram": 50.0, "five": 40, "week": 20, "sessions": 3}])

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


def spent(key, hour, minute, minutes):
    return {"at": datetime(2026, 11, 2, hour, minute, tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "repo": "r", "key": key, "minutes": minutes}


class Totals(unittest.TestCase):
    def test_today_starts_at_midnight(self):
        now = datetime(2026, 11, 2, 10).timestamp()
        rows = [{"at": now - 3600, "cpu": 10, "ram": 1, "five": 1, "agents": 1}, {"at": now - 11 * 3600, "cpu": 90, "ram": 1, "five": 1, "agents": 1}]
        self.assertEqual(totals(rows, [], now, 1)["cpu"], 10)
        self.assertEqual(totals(rows, [], now, 7)["cpu"], 90)

    def test_a_round_between_samples_still_counts_as_work(self):
        now = datetime(2026, 11, 2, 10).timestamp()
        found = totals([], [spent("1", 9, 30, 5), spent("2", 9, 32, 5), spent("2", 9, 50, 5)], now, 1)
        self.assertEqual((found["agents"], found["runs"]), (2, 2))

    def test_one_round_after_another_isnt_two_at_once(self):
        self.assertEqual(at_once([spent("1", 9, 30, 5), spent("1", 9, 35, 5)]), 1)

    def test_old_runs_with_no_model_still_count(self):
        now = datetime(2026, 11, 2, 10).timestamp()
        event = {"at": "2026-11-02T09:00:00Z", "usd": 1.0, "step": "tasks"}
        self.assertEqual(top([event], now), ("not recorded", "tasks"))
