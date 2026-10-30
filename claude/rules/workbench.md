<!-- Managed by herdr-workbench. Re-run its install.sh to update. Local edits are overwritten. -->

# Workbench: worktrees, commands, reviews, documents, PR size, stacks

## Worktrees

- One task, one worktree: `wt switch -c <branch>` from the main checkout. It opens at `~/.herdr/worktrees/<repo>/<branch>` with dependencies installed and `.env` copied, so don't set it up by hand.
- Leave the main checkout on the default branch and never commit there.
- When the branch is merged, run `wt rm` from its worktree.

## Running commands

- Never activate a venv. Use the project's tool: `uv run ...` with `uv.lock`, `poetry run ...` with `poetry.lock`, `npm run` / `npx` with `package.json`.
- Add dependencies with `uv add` / `poetry add`. A bare `pip install` is refused outside a venv, on purpose.

## Reviews

Only when `HERDR_ENV=1`. Every code review reaches me through hunk, whichever skill does the analysis. The `hunk-review` skill has the session commands; always pass `--repo`.

- Open the review yourself with `~/.config/herdr/bin/hunk-review-open`. It shows uncommitted changes, or the branch if there are none, plus the PR's GitHub threads. Your own pane must be in the worktree under review; if it isn't, ask me to open one and start you there.
- Review before `gh pr create`, and again for PR feedback. Commit first when you want the whole branch.
- Post findings as hunk notes, not in chat, in one batch, then tell me it's ready. Flag only what I'd want to see: bugs, security, data loss, and the why behind odd changes. No nits, and no note on every hunk.
- My comments arrive as your next message and are cleared from hunk, so answer in chat.
- Hunk notes can't be edited: remove a note and add a new one to change it.
- When the review is of a PR, your notes are the draft review: a confidence prefix (high, medium, low), about two sentences, a `suggestion` block where it helps. Anything that doesn't fit a changed line goes in one note with `--author pr-level` and a `PR-level:` prefix, anchored to the first hunk.
- Post only when I name the outcome: `~/.config/herdr/bin/hunk-pr-push --as comment|approve|request-changes|draft`. It speaks for me on GitHub.
- Ask before replying to or resolving GitHub threads.

## Documents

Only when `HERDR_ENV=1`. A plan, spec or write-up I need to approve goes to a Markdown file, not into chat: in the repo if it keeps the document, otherwise your scratchpad. Open it beside your pane, then end your turn:

```bash
herdr plugin pane open --plugin annotate --entrypoint doc --placement split \
  --direction right --target-pane "$HERDR_PANE_ID" --focus --cwd "$PWD" \
  --env PLANNOTATOR_TUI_FILE="$FILE" --env PLANNOTATOR_TUI_DELIVER_TO="$HERDR_PANE_ID"
```

`$FILE` is the absolute path. Don't wait, poll or read the pane: my notes arrive as your next message. Address each one, revise the file and reopen it until I approve in chat.

## PR size

Strong guidelines, not hard limits. When you go over, say so and why.

- Each PR is one small, self-contained change that builds and passes its tests on its own.
- Aim for 300 changed lines or fewer, 500 at most. Count code and tests you wrote, not lockfiles or generated files. Reviewers find markedly fewer defects past about 400 lines.
- Bigger than that: split it into a stack.
- Open PRs as drafts. Mark one ready for review, or merge it, when I ask you to; not on your own judgement.

## Stacks (gh stack)

GitHub's native stacked PRs, through `gh stack`. Its `gh-stack` skill has the commands and the non-interactive flags; follow it. On top of it:

- A stack is dependent PRs, each on the one below. Aim for 3, never deeper than 5. Needing more means the work needs re-planning, not another layer.
- Plan the layers before writing code, and start the stack at the beginning: `wt switch -c <first-branch>`, then `gh stack init <first-branch>` to adopt it. Add each next layer with `gh stack add <branch>`.
- Everything that shapes the stack goes through `gh stack`: creating, moving between, rebasing, reordering, pushing and syncing stack branches, and opening or updating their PRs. Plain git or gh leaves GitHub's record of the stack wrong. If `gh stack` has no command for what you need, stop and ask.
- Work inside one layer is ordinary: `git add`, `git commit`, `git diff`, `git status`, and `gh` for viewing PRs, checks and review comments. After committing to a layer with others above it, run `gh stack rebase --upstack`.
- Drafts and merges follow the PR rule above: `gh stack submit --auto` opens drafts, `gh stack submit --auto --open` marks them ready when I ask, and `gh stack merge <target> --yes` merges when I ask. `gh pr merge` can't merge a stack.
- Layers may sit in different worktrees; `gh stack` rebases each in the worktree that holds it. Keep those worktrees clean before rebasing or syncing, or it stops.
