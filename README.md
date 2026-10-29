# herdr-workbench

Config and glue for a terminal workbench built on [herdr](https://github.com/herdrdev/herdr), [worktrunk](https://github.com/max-sixty/worktrunk) and [hunk](https://github.com/modem-dev/hunk). It installs into `$HOME` and expects the tools themselves to be installed already, as pinned binaries in `~/.local/bin` ([wsl-starter-script](https://github.com/artislismanis/wsl-starter-script) does that, then runs this repo's installer at a pinned commit).

## What it installs

| Path | What | On re-run |
|---|---|---|
| `~/.config/worktrunk/config.toml` | A marked block at the top: worktrees at `~/.herdr/worktrees/<repo>/<branch>` (herdr's layout), the two hooks below, a `wt rm` alias, copy-ignored excludes | Block rewritten; anything below it is yours and stays |
| `~/.config/worktrunk/hooks/worktree-setup` | `pre-start`: sets up a new worktree for its stack (mise, uv, poetry, npm, terraform, dbt). Drops a `VIRTUAL_ENV` inherited from the calling shell | Refreshed if it drifts |
| `~/.config/worktrunk/hooks/sync-default-branch` | `pre-switch`: fast-forwards the local default branch to its remote, so new branches start from the latest | Refreshed if it drifts |

`wt rm` fetches, then runs `wt remove`. worktrunk decides whether a branch is merged before any hook runs, so a fetch inside a `pre-remove` hook would be too late to spot a branch that was squash-merged on GitHub.

Both hooks warn and carry on rather than fail: a failing `pre-*` hook aborts the switch.

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
