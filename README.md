# herdr-workbench

Config and glue for a terminal workbench built on [herdr](https://github.com/herdrdev/herdr), [worktrunk](https://github.com/max-sixty/worktrunk) and [hunk](https://github.com/modem-dev/hunk). It installs into `$HOME` and expects the tools themselves to be installed already, as pinned binaries in `~/.local/bin` ([wsl-starter-script](https://github.com/artislismanis/wsl-starter-script) does that, then runs this repo's installer at a pinned commit).

## What it installs

| Path | What | On re-run |
|---|---|---|
| `~/.config/worktrunk/config.toml` | A marked block at the top: worktrees at `~/.herdr/worktrees/<repo>/<branch>` (herdr's layout), the two hooks below, a `wt rm` alias, copy-ignored excludes | Block rewritten; anything below it is yours and stays |
| `~/.config/worktrunk/hooks/worktree-setup` | `pre-start`: sets up a new worktree for its stack (mise, uv, poetry, npm, terraform, dbt). Drops a `VIRTUAL_ENV` inherited from the calling shell | Refreshed if it drifts |
| `~/.config/worktrunk/hooks/sync-default-branch` | `pre-switch`: fast-forwards the local default branch to its remote, so new branches start from the latest | Refreshed if it drifts |
| `~/.claude/rules/herdr-workbench/workbench.md` | Claude rule, loaded in every session: one worktree per task, `uv run` / `poetry run`, PR size, stacks with `gh stack`. Under `$CLAUDE_CONFIG_DIR` when that's set | Refreshed if it drifts |
| `~/.config/herdr/config.toml` | A marked block at the bottom: `[update] version_check = false`, so the starter's pin is the only thing that moves herdr, plus the keys below. Checked with `herdr config check` before it's written; a clash with your settings (a table can't appear twice) stops the install | Block rewritten; everything above it is yours and stays |
| `~/.claude/skills/herdr/SKILL.md` | herdr's agent skill, from `herdr --skill`, so it matches the installed binary | Refreshed if it drifts |
| herdr's Claude integration | `herdr integration install claude`: a hook that lets herdr resume Claude sessions after a server restart | Installed if missing or not current |
| `~/.config/herdr/bin/wt-remove-current` | Removes the focused pane's worktree after a Y/n, and closes its workspace | Refreshed if it drifts |
| herdr's worktrunk plugin | [herdr-worktrunk](https://github.com/devashish2203/herdr-worktrunk) at the commit in `pins` | Installed if missing or at another commit |
| herdr's workshop plugin | `plugins/workshop`, linked with `herdr plugin link`, so it moves with this checkout | Linked if missing or linked to another checkout |
| `~/.local/bin/workshop` | Symlink to the plugin's command: `workshop doctor`, `workshop start` and so on. See [its README](plugins/workshop/README.md) | Repointed if it drifts |
| `~/.config/systemd/user/workshop-tick.{service,timer}` | `workshop tick`, every 5 minutes unless `workshop schedule` changes it. Installed but off, because turning it on starts tickets unattended | Refreshed if it drifts; never enabled |

| Key, after herdr's prefix | Does |
|---|---|
| `shift+g` | Pick or create a worktree, with worktrunk's hooks. Replaces herdr's built-in `new_worktree` |
| `shift+e` | Remove the focused worktree. `wt` refuses a dirty one and keeps an unmerged branch |

A running herdr picks up new keys after `herdr server reload-config`.

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

## Known quirks

**Unreadable active tab on herdr's `terminal` theme (herdr 0.9.5, Windows Terminal).** herdr draws the active tab's text in its panel colour, or in the divider line colour when no panel colour is set, as in this theme. That gives dark grey on blue. herdr also makes renamed tabs bold, and Windows Terminal draws bold black as bright black by default, which is grey as well.

To fix it, do both:

1. In `~/.config/herdr/config.toml`, under your `[theme]` section, add:

   ```toml
   [theme.custom]
   panel_bg = "black"
   ```

2. In Windows Terminal, set Settings > Profiles > Defaults > Appearance > Intense text style to Bold font, or add `"intenseTextStyle": "bold"` under `profiles.defaults` in `settings.json`.

The install leaves this to you, because it would break on an existing `[theme.custom]` section and only suits the `terminal` theme. Side effects: bold text in all terminal apps no longer brightens, and in colour schemes where black differs from the background, such as Dark Modern, the tab row shows as a darker strip. Don't set `surface_dim` to fix the text, as it also recolours the divider lines. An exact colour such as `panel_bg = "#1f1f1f"` works without the Windows Terminal setting, but no longer follows the colour scheme.

## Develop

```bash
./lint.sh               # bash -n + shellcheck
bats tests/             # every test uses a throwaway HOME and temp git repos
```

CI runs both on every PR. Test installs on a throwaway distro, not your daily one.

## Licence

MIT
