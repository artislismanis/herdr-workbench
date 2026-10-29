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

## Stacks (git-spice)

- A stack is dependent PRs, each on the one below. Aim for 3, never deeper than 5. Needing more means the work needs re-planning, not another layer.
- Manage a stack only through git-spice. Never `git rebase`, `git checkout -b`, `git branch -D`, `git push` or `gh pr create` a stack branch by hand: git-spice tracks each branch's base, and doing it in plain git or gh leaves that record wrong. If git-spice has no command for what you need, stop and ask.
- Call it `git-spice`. `gs` is the same tool, but only as an alias in interactive zsh.
- One stack, one worktree:
  1. `wt switch -c <first-branch>`, then `git-spice branch track --base <default-branch>`. git-spice doesn't track branches it didn't create.
  2. Each next layer: stage its changes, then `git-spice branch create <name> -m "<message>"`.
  3. Move with `git-spice up` / `down` / `top` / `bottom`. After changing a lower branch, commit with `git-spice commit create -m "<message>"`; the branches above restack.
  4. Open or update the PRs: `git-spice stack submit --fill --draft`.
  5. After a merge: `git-spice repo sync --restack` pulls trunk, deletes merged branches and restacks the rest.
- Never check out a stack branch in another worktree or the main checkout. git allows a branch in only one worktree, so moving through the stack would fail.
