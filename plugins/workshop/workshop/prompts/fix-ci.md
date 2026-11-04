CI is failing on {pr}. Find out why before you change anything.

1. List the PR's checks with `gh pr checks {pr}`, and read the full log of each one that failed, with whatever this repo's CI uses.
2. For each failure, find the cause, with the log lines that show it, and decide which kind it is:
   - Code: the change, a test or the build is wrong. Fix it, as small as the fix can be, and don't weaken or skip a test to get past it. Run the repo's own checks to show it's fixed.
   - Flaky: nothing in the change explains it, such as a timeout or a network blip. Rerun only the failed checks, with whatever this repo's CI uses. If you fixed code too, rerun nothing: the push runs every check again.
   - Outside the code: a secret, permission, quota, runner or service is wrong. Change nothing, and say what a person needs to do.

<!-- workshop -->

You may rerun failed checks. Do not commit, push, post or change anything else outside this checkout: workshop pushes your fix.

`{run}/ci.md`, if it exists, lists the checks that failed after workshop's last push, and `{run}/qa.md` has the output of the repo's own checks, if they ran. `{run}/feedback.md`, if it exists, says what the PR's author wants changed in your fix: do that first.

Write your report to `{out}`:

- For each failing check: what failed, the cause with the log lines that show it, its kind, and what you did.
- A `## Needs you` section with what only a person can do, or nothing under it.
- A `## Handover` section: two to four short bullets on what changed and what's worth a look.

End it with a last line that is exactly `VERDICT: FIXED` if you changed code, `VERDICT: RERUN` if you only reran checks, or `VERDICT: NEEDS_YOU` if a person has to act first.
