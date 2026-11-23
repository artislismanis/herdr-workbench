This branch is built on top of the branch of another ticket's PR. That PR has moved on, so this branch has to move onto it too. Moving it hit a conflict, which is yours to resolve.

1. Run the command in `{run}/onto.md`. It replays this branch's own commits onto the new base.
2. Resolve each conflict so that both changes still do what they were for. The base is settled work under review, so change this branch's side of a conflict where you can.
3. Run the repo's checks on the files you touched, and fix what your resolution broke.
4. Finish the rebase with `git rebase --continue`, until `git status` shows no rebase in progress.

<!-- workshop -->

You may run `git` commands in this checkout, and the repo's own checks. Do not push: workshop pushes the branch once you're done. Do not merge or post anything.

Write your report to `{out}`:

- Each conflict: the file, what the two sides wanted, and how you kept both.
- What the checks showed.
- A `## Handover` section: two to four short bullets.

End it with a last line that is exactly `VERDICT: DONE` once the rebase is finished, or `VERDICT: FAILED` where the two changes can't both hold. On FAILED, run `git rebase --abort` first and say in the report what a person must decide.
