import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop import schedule as sched  # noqa: E402
from workshop.shell import ToolError  # noqa: E402


class Schedule(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.state = "disabled"
        self.calls = []
        patch = mock.patch.object(sched, "run", self.fake)
        patch.start()
        self.addCleanup(patch.stop)

    def fake(self, *argv):
        self.calls.append(argv[2:])
        return self.state if argv[2] == "show" else ""

    def test_off_is_the_default(self):
        self.assertEqual(sched.schedule([]), "off")

    def test_a_frequency_turns_it_on_and_replaces_the_shipped_value(self):
        self.assertIn("every 10m", sched.schedule(["10m"]))
        text = sched.dropin().read_text(encoding="utf-8")
        self.assertEqual(text, "[Timer]\nOnActiveSec=\nOnActiveSec=10m\nOnUnitActiveSec=10m\n")
        self.assertEqual(self.calls, [("daemon-reload",), ("enable", sched.TIMER), ("restart", sched.TIMER)])
        self.state = "enabled"
        self.assertEqual(sched.schedule([]), "on, every 10m")

    def test_on_without_a_frequency_of_ours_shows_the_shipped_one(self):
        self.state = "enabled"
        self.assertEqual(sched.schedule([]), f"on, every {sched.DEFAULT}")

    def test_off_stops_it_and_clears_the_frequency(self):
        sched.schedule(["10m"])
        self.calls.clear()
        self.assertEqual(sched.schedule(["off"]), "off")
        self.assertEqual(self.calls[0], ("disable", "--now", sched.TIMER))
        self.assertFalse(sched.dropin().exists())

    def test_a_bad_frequency_changes_nothing(self):
        for bad in ("0m", "soon", "5", "5m; rm"):
            with self.assertRaises(ToolError):
                sched.schedule([bad])
        self.assertEqual(self.calls, [])
        self.assertFalse(sched.dropin().exists())


if __name__ == "__main__":
    unittest.main()
