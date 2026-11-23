{pr} has merged into `{base}`. Its description lists checks to run after the merge. Run each one now and say whether it holds.

1. Read the description with `gh pr view {pr} --json body`. The checks sit under an `After merge` heading, or in checklist items that start with "After merge".
2. Run each check as written. Where a check names a date or a time, take it from `gh pr view {pr} --json mergedAt`.
3. For each check, decide which it is:
   - Holds: the output matches what the check says it must.
   - Not yet: GitHub or another service has not done its part since the merge, such as a scheduled run that has not started.
   - Fails: the output is wrong, and the check says what to change, or the cause is plain.
4. Where a check fails and the fix is a change to files in the repo, make it on a new branch from `origin/{base}` in this checkout. Commit it, push the branch and open a draft PR with `gh pr create --draft`. Its description links {pr} and says which check it fixes, with the output that shows it.

<!-- workshop -->

You may run read-only `gh` and `git` commands, push your fix branch and open its draft PR. Do not merge, deploy, post a comment or change any setting on GitHub.

Write your report to `{out}`:

- For each check: the check, the command you ran, the output that decides it, and which of the three it is.
- The link to any draft PR you opened.
- A `## Needs you` section with what only a person can do, or nothing under it.
- A `## Handover` section: two to four short bullets.

End it with a last line that is exactly `VERDICT: PASS` if every check holds, `VERDICT: LATER` if any is not yet due and none fails, or `VERDICT: FAILED` if any fails.
