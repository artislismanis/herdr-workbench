import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.usage import DAY, KEEP_DAYS, SAMPLES, sample, samples, top, totals  # noqa: E402


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

    def test_old_runs_with_no_model_still_count(self):
        now = datetime(2026, 11, 2, 10).timestamp()
        event = {"at": "2026-11-02T09:00:00Z", "usd": 1.0, "step": "tasks"}
        self.assertEqual(top([event], now), ("not recorded", "tasks"))
