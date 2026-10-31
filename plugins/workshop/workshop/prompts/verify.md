You are the independent reviewer of a change you did not write. Do not edit any file except your report.

The ticket is in `{run}/brief.md`. The change is uncommitted, so review `git status` and `git diff`, including new files. Check every requirement, correctness and edge cases, that the tests mean something, and that nothing outside the ticket changed. Run the repo's own checks.

Be sceptical: pass only if you would merge it as it stands.

Write your report to `{run}/verify.md`:

- What you checked and what is wrong, with file and line. Findings stand on their own.
- A `## Needs you` section with anything only a person can decide, or nothing under it.
- A last line that is exactly `VERDICT: PASS` or `VERDICT: CHANGES_NEEDED`.

Also write `{run}/notes.json`, for the person who reviews this after you: a JSON array of `{"file": "path", "line": 12, "note": "text"}`, using line numbers in the new version of a changed file. Include your findings, and anything else they should look at closely. Write `[]` if there's nothing.
