# herdr-workbench

Config and glue for a terminal workbench built on [herdr](https://github.com/herdrdev/herdr), [worktrunk](https://github.com/max-sixty/worktrunk) and [hunk](https://github.com/modem-dev/hunk). It installs into `$HOME` and expects the tools themselves to be installed already, as pinned binaries in `~/.local/bin` ([wsl-starter-script](https://github.com/artislismanis/wsl-starter-script) does that, then runs this repo's installer at a pinned commit).

## What it installs

| Path | What | On re-run |
|---|---|---|
| `~/.config/worktrunk/config.toml` | A marked block at the top: worktrees at `~/.herdr/worktrees/<repo>/<branch>` (herdr's layout), the two hooks below, a `wt rm` alias, copy-ignored excludes | Block rewritten; anything below it is yours and stays |
| `~/.config/worktrunk/hooks/worktree-setup` | `pre-start`: sets up a new worktree for its stack (mise, uv, poetry, npm, terraform, dbt). Drops a `VIRTUAL_ENV` inherited from the calling shell | Refreshed if it drifts |
| `~/.config/worktrunk/hooks/sync-default-branch` | `pre-switch`: fast-forwards the local default branch to its remote, so new branches start from the latest | Refreshed if it drifts |
| `~/.claude/rules/herdr-workbench/workbench.md` | Claude rule, loaded in every session: one worktree per task, `uv run` / `poetry run`, PR size, stacks with `gh stack`. Under `$CLAUDE_CONFIG_DIR` when that's set | Refreshed if it drifts |
| `~/.config/herdr/config.toml` | A marked block at the bottom: `[update] version_check = false`, so the starter's pin is the only thing that moves herdr. Checked with `herdr config check` before it's written; a clash with your settings (a table can't appear twice) stops the install | Block rewritten; everything above it is yours and stays |
| `~/.claude/skills/herdr/SKILL.md` | herdr's agent skill, from `herdr --skill`, so it matches the installed binary | Refreshed if it drifts |
| herdr's Claude integration | `herdr integration install claude`: a hook that lets herdr resume Claude sessions after a server restart | Installed if missing or not current |

`wt rm` fetches, then runs `wt remove`. worktrunk decides whether a branch is merged before any hook runs, so a fetch inside a `pre-remove` hook would be too late to spot a branch that was squash-merged on GitHub.

Both hooks warn and carry on rather than fail: a failing `pre-*` hook aborts the switch.

## Running commands in a worktree

Nothing activates a venv. Commands go through the project's own tool, which always picks the worktree's `.venv`, in any shell, for people and agents alike:

| Project has | Run |
|---|---|
| `uv.lock` | `uv run pytest`, `uv run python -m app` |
| `poetry.lock` | `poetry run pytest` |
| `package.json` | `npm run test`, `npx eslint` (node finds `node_modules` itself) |

Don't `source .venv/bin/activate`: an activated venv stays on PATH after you switch worktrees, so `python` quietly runs the other worktree's environment. `uv run` also syncs the venv from `uv.lock` first, so it's never stale after a pull.

herdr's parts are skipped with a warning when `herdr` isn't on PATH. In herdr's `config.toml`, add your own tables above our block, not below it.

Machine-local worktrunk settings, such as `[projects."<id>"]` entries, go below the block. Never commit them here.

## Install

```bash
./install.sh            # install or refresh
./install.sh --dry-run  # show what would change
./install.sh --check    # exit non-zero if anything differs from this repo
./install.sh --rollback # print an undo recipe; runs nothing
```

Runs as your own user. Re-running is a no-op when nothing changed.

## Develop

```bash
./lint.sh               # bash -n + shellcheck
bats tests/             # every test uses a throwaway HOME and temp git repos
```

CI runs both on every PR. Test installs on a throwaway distro, not your daily one.

## Licence

MIT
