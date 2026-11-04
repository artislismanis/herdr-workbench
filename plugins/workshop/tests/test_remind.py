import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.boardview import Waiting  # noqa: E402
from workshop.remind import remind  # noqa: E402
from workshop.store import Store  # noqa: E402

MONDAY_NINE = datetime(2026, 11, 9, 9).timestamp()


def item(since, ref=("r", "1")):
    return Waiting(ref, "r/1", "fix", datetime.fromtimestamp(since, UTC).isoformat(), "Delivery", "r")


class Reminders(unittest.TestCase):
    def setUp(self):
        self.store = Store(Path(tempfile.mkdtemp()))
        self.sent = []

    def at(self, now, *found):
        remind(self.store, list(found), now, lambda title, body: self.sent.append((title, body)))

    def test_nothing_before_an_hour(self):
        self.at(MONDAY_NINE + 1800, item(MONDAY_NINE))
        self.assertEqual(self.sent, [])

    def test_once_after_an_hour_then_not_again_that_day(self):
        self.at(MONDAY_NINE + 3600, item(MONDAY_NINE))
        self.at(MONDAY_NINE + 7200, item(MONDAY_NINE))
        self.assertEqual(self.sent, [("1 waiting on you", "r/1 fix")])

    def test_again_the_next_working_morning(self):
        self.at(MONDAY_NINE + 3600, item(MONDAY_NINE))
        self.at(MONDAY_NINE + 86400 - 3600, item(MONDAY_NINE))
        self.assertEqual(len(self.sent), 2)

    def test_quiet_out_of_working_hours(self):
        self.at(MONDAY_NINE + 12 * 3600, item(MONDAY_NINE - 86400))
        self.assertEqual(self.sent, [])

    def test_a_weekend_isnt_a_wait(self):
        friday_half_five = datetime(2026, 11, 6, 17, 30).timestamp()
        self.at(MONDAY_NINE - 2400, item(friday_half_five))  # 30 minutes on Friday, 20 on Monday
        self.assertEqual(self.sent, [])

    def test_everything_due_goes_in_one(self):
        self.at(MONDAY_NINE + 3600, item(MONDAY_NINE), item(MONDAY_NINE, ("#pr", "u")))
        self.assertEqual(self.sent[0][0], "2 waiting on you")

    def test_what_stops_waiting_is_forgotten(self):
        self.at(MONDAY_NINE + 3600, item(MONDAY_NINE))
        self.at(MONDAY_NINE + 3700)
        self.at(MONDAY_NINE + 3800, item(MONDAY_NINE + 3750))
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
