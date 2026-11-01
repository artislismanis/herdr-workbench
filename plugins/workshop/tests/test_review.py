import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workshop.review import decisions, load_notes, place  # noqa: E402

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



class Decisions(unittest.TestCase):
    def test_one_decision_and_its_overview(self):
        self.assertEqual(decisions("## Decision\n\nOverview: O\nWhat: W\nAsk: A\n\n## Rest\n\nWhy: not this\n"), ("O", [{"What": "W", "Ask": "A"}]))

    def test_a_repeated_line_starts_the_next(self):
        self.assertEqual(len(decisions("What: A\nAsk: B\nWhat: C\nAsk: D\n")[1]), 2)

    def test_prose_in_a_report_isnt_a_brief(self):
        self.assertEqual(decisions("We asked Why: because\n"), ("", []))

    def test_bold_and_bullets_are_fine(self):
        self.assertEqual(decisions("- **What:** A\n")[1], [{"What": "A"}])

    def test_a_question_keeps_its_lead_in(self):
        self.assertEqual(decisions("tasks asks: What: A\nAsk: B\n")[1], [{"What": "A", "Ask": "B"}])


if __name__ == "__main__":
    unittest.main()
