import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.themes import ALIASES, DEFAULT, TERMINAL, THEMES, TOKENS, config_path, nearest, palette  # noqa: E402


class Palette(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "config.toml"

    def theme(self, text):
        self.path.write_text(text, encoding="utf-8")
        return palette(self.path)

    def test_follows_the_named_theme(self):
        self.assertEqual(self.theme('[theme]\nname = "dracula"\n'), THEMES["dracula"])
        self.assertEqual(self.theme('[theme]\nname = "terminal"\n'), TERMINAL)

    def test_names_are_read_the_way_herdr_reads_them(self):
        self.assertEqual(self.theme('[theme]\nname = "Tokyo Night"\n'), THEMES["tokyo-night"])
        self.assertEqual(self.theme('[theme]\nname = "dawn"\n'), THEMES["rose-pine-dawn"])

    def test_no_theme_a_bad_name_or_a_bad_file_use_herdrs_default(self):
        self.assertEqual(self.theme(""), THEMES[DEFAULT])
        self.assertEqual(self.theme('[theme]\nname = "nope"\n'), THEMES[DEFAULT])
        self.assertEqual(self.theme("not toml ["), THEMES[DEFAULT])
        self.assertEqual(palette(self.path.with_name("missing")), THEMES[DEFAULT])

    def test_custom_colours_go_on_top(self):
        found = self.theme('[theme]\nname = "nord"\n[theme.custom]\naccent = "#112233"\nred = "reset"\n')
        self.assertEqual((found["accent"], found["red"]), ("#112233", THEMES["nord"]["red"]))

    def test_auto_switch_follows_the_dark_theme(self):
        self.assertEqual(self.theme('[theme]\nname = "nord"\nauto_switch = true\ndark_name = "dracula"\n'), THEMES["dracula"])

    def test_every_theme_is_complete_and_aliases_resolve(self):
        self.assertEqual(len(THEMES), 18)
        self.assertTrue(all(set(theme) == set(TOKENS) for theme in THEMES.values()))
        self.assertTrue(all(target in THEMES for target in ALIASES.values()))

    def test_config_path_follows_herdrs_override(self):
        with mock.patch.dict(os.environ, {"HERDR_CONFIG_PATH": "/x/c.toml"}):
            self.assertEqual(config_path(), Path("/x/c.toml"))


class Nearest(unittest.TestCase):
    def test_exact_cube_and_grey_colours(self):
        self.assertEqual(nearest("#ff0000"), 196)
        self.assertEqual(nearest("#000000"), 16)
        self.assertEqual(nearest("#808080"), 244)

    def test_terminal_colours_pass_through(self):
        self.assertEqual(nearest(4), 4)
        self.assertEqual(nearest(-1), -1)

    def test_every_theme_colour_lands_on_a_real_index(self):
        self.assertTrue(all(0 <= nearest(c) <= 255 for t in THEMES.values() for c in t.values() if c != -1))


if __name__ == "__main__":
    unittest.main()
