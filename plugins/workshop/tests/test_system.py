import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.system import EVERY, Meter, sessions  # noqa: E402


class Machine(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.proc, self.now = Path(tmp.name), 100.0
        self.meter = Meter(self.proc, lambda: self.now)

    def write(self, busy, idle, available=4194304):
        (self.proc / "stat").write_text(f"cpu  {busy} 0 0 {idle} 0 0 0 0 0 0\ncpu0 1 0 0 1 0 0 0 0 0 0\ncpu1 1 0 0 1 0 0 0 0 0 0\nintr 1\n")
        (self.proc / "meminfo").write_text(f"MemTotal:       16777216 kB\nMemFree:        1 kB\nMemAvailable:   {available} kB\nHugePages_Total:       0\n")

    def later(self):
        self.now += EVERY

    def test_every_claude_counts_as_a_session(self):
        for pid, name in (("1", "claude"), ("2", "claude"), ("3", "zsh")):
            (self.proc / pid).mkdir()
            (self.proc / pid / "comm").write_text(name + "\n")
        self.assertEqual(sessions(self.proc), 2)

    def test_cpu_is_the_share_busy_since_the_last_reading(self):
        self.write(100, 100)
        self.assertEqual(self.meter.read()["cpu"], 0)
        self.later()
        self.write(175, 125)
        found = self.meter.read()
        self.assertEqual((found["cpu"], found["cores"]), (75, 2))

    def test_ram_counts_what_is_available_not_what_is_free(self):
        self.write(1, 1)
        found = self.meter.read()
        self.assertEqual((found["ram"], found["memory"]), (75, 16))

    def test_peaks_stay_after_the_load_drops(self):
        self.write(100, 100)
        self.meter.read()
        self.later()
        self.write(200, 100, 1048576)
        self.meter.read()
        self.later()
        self.write(200, 200, 8388608)
        found = self.meter.read()
        self.assertEqual((found["cpu"], found["ram"], found["cpu_peak"]), (0, 50, 100))
        self.assertAlmostEqual(found["ram_peak"], 93.75)

    def test_quick_reads_share_a_sample(self):
        self.write(100, 100)
        first = self.meter.read()
        self.write(200, 100)
        self.assertIs(self.meter.read(), first)

    def test_no_proc_means_no_line(self):
        self.assertEqual(self.meter.read(), {})


if __name__ == "__main__":
    unittest.main()
