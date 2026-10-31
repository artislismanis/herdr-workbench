import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.review import load_notes, place  # noqa: E402

FILES = [{"path": "a.py", "hunks": [{"newRange": [3, 5]}, {"newRange": [10, 10]}]}]


def note(file="a.py", line=4):
    return {"file": file, "line": line, "note": "Look"}


class Place(unittest.TestCase):
    def test_a_note_in_a_hunk_is_placed(self):
        placed, outside = place([note()], FILES)
        self.assertEqual(placed, [{"filePath": "a.py", "newLine": 4, "summary": "Look", "author": "workshop"}])
        self.assertEqual(outside, [])

    def test_hunk_edges_count(self):
        self.assertEqual(len(place([note(line=3), note(line=5), note(line=10)], FILES)[0]), 3)

    def test_a_line_between_hunks_waits_on_that_files_first_change(self):
        placed, outside = place([note(line=7)], FILES)
        self.assertEqual(placed, [{"filePath": "a.py", "newLine": 3, "summary": "PR-level: a.py:7 Look", "author": "pr-level"}])
        self.assertEqual(outside, [])

    def test_a_file_not_in_the_diff_waits_on_the_first_change(self):
        placed = place([note(file="b.py")], FILES)[0]
        self.assertEqual((placed[0]["filePath"], placed[0]["newLine"], placed[0]["author"]), ("a.py", 3, "pr-level"))
        self.assertIn("b.py:", placed[0]["summary"])

    def test_with_no_changed_line_a_note_stays_outside(self):
        self.assertEqual(place([note()], []), ([], [note()]))


class Load(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.json"
            path.write_text(text, encoding="utf-8")
            return load_notes(path)

    def test_a_missing_file_means_no_notes(self):
        self.assertEqual(load_notes(Path("/nonexistent/notes.json")), [])

    def test_broken_json_means_no_notes(self):
        self.assertEqual(self.load("[{"), [])

    def test_the_wrong_shape_means_no_notes(self):
        self.assertEqual(self.load('{"file": "a.py"}'), [])

    def test_bad_entries_are_dropped(self):
        self.assertEqual(self.load('[{"file": "a.py", "line": 4, "note": "Look"}, {"file": "a.py"}, 3]'), [note()])


if __name__ == "__main__":
    unittest.main()
