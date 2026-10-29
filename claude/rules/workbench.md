<!-- Managed by herdr-workbench. Re-run its install.sh to update. Local edits are overwritten. -->

# Workbench: worktrees, commands, PR size, stacks

## Worktrees

- One task, one worktree: `wt switch -c <branch>` from the main checkout. It opens at `~/.herdr/worktrees/<repo>/<branch>` with dependencies installed and `.env` copied, so don't set it up by hand.
- Leave the main checkout on the default branch and never commit there.
- When the branch is merged, run `wt rm` from its worktree.

## Running commands

- Never activate a venv. Use the project's tool: `uv run ...` with `uv.lock`, `poetry run ...` with `poetry.lock`, `npm run` / `npx` with `package.json`.
- Add dependencies with `uv add` / `poetry add`. A bare `pip install` is refused outside a venv, on purpose.

## PR size

Strong guidelines, not hard limits. When you go over, say so and why.

- Each PR is one small, self-contained change that builds and passes its tests on its own.
- Aim for 300 changed lines or fewer, 500 at most. Count code and tests you wrote, not lockfiles or generated files. Reviewers find markedly fewer defects past about 400 lines.
- Bigger than that: split it into a stack.

## Stacks (gh stack)

GitHub's native stacked PRs, through `gh stack`. Its `gh-stack` skill has the commands and the non-interactive flags; follow it. On top of it:

- A stack is dependent PRs, each on the one below. Aim for 3, never deeper than 5. Needing more means the work needs re-planning, not another layer.
- Plan the layers before writing code, and start the stack at the beginning: `gh stack init <first-branch>`, not `wt switch -c`.
- Everything that shapes the stack goes through `gh stack`: creating, moving between, rebasing, reordering, pushing and syncing stack branches, and opening or updating their PRs. Plain git or gh leaves GitHub's record of the stack wrong. If `gh stack` has no command for what you need, stop and ask.
- Work inside one layer is ordinary: `git add`, `git commit`, `git diff`, `git status`, and `gh` for viewing PRs, checks and review comments. After committing to a layer with others above it, run `gh stack rebase --upstack`.
- PRs open as drafts (`gh stack submit --auto`). Never merge: no `gh stack merge`, no `gh pr merge`. I merge.
- Layers may sit in different worktrees; `gh stack` rebases each in the worktree that holds it. Keep those worktrees clean before rebasing or syncing, or it stops.
