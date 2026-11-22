---
name: workshop-config
description: Guided setup of workshop's config at ~/.config/workshop/config.toml. Asks the critical questions one at a time, detects what it can, then writes or updates the file and checks it with workshop doctor. Use when setting up workshop, adding or changing a repo in it, connecting Jira or chat, or when the user is unsure what a workshop setting does or whether they need it.
---

# Workshop config

Walk the user through `~/.config/workshop/config.toml` (or `$WORKSHOP_CONFIG`), critical settings first. The reference for every key is `config.example.toml`, laid out in the same three tiers as below, and `README.md`, both in the plugin folder: `$(dirname "$(dirname "$(readlink -f ~/.local/bin/workshop)")")`. Read them when a detail isn't covered here, rather than guessing.

## How to ask

The user came here because the config has too many knobs. Every question should be a real decision, not a tour of the settings. A fresh one-repo setup is usually 3 to 5 questions; with Jira and chat, 6 to 8.

- Take what the user already said as answered. "Tickets are in Jira", "reviews come through Slack" and a named org settle those questions, and pull the matching settings forward from whatever tier they sit in.
- Detect before asking: base branch, the qa command, the org from the remotes, chat tools from the session. Don't ask to confirm each detected value or each default. Show them together as one proposal ("I'll use: source github, label `ai`, base `master`, qa `uv run pytest`") with "use these" as the recommended option and room to change any.
- Ask singly only what has no good default and can't be found: where tickets live, whether the repo is trusted to run `qa`, how reviews show to the team, the Jira status mapping.
- One `AskUserQuestion` per decision, recommended option first.
- Never ask for a secret in chat. The user types it into the file themselves (see Jira).
- Write only what differs from the default. A default written out reads as a choice and hides the real ones. When updating, follow the file's own style instead: if its other repo tables write `label`, so does the new one.

## Start

1. If the config exists, read it and say in two lines what's set. Unless the request already says, ask which of these to do: add a repo, change one, fill in what's missing, or review the optional settings. Skip every question whose answer is already in the file.
2. If not, start at Tier 1 and create it at the end.

## Tier 1: needed to run at all

Per repo, repeat until the user has no more:

| Key | Default | How to settle it |
|---|---|---|
| table name `[repos.<name>]` | none | Suggest the folder name. On Jira it must equal a label on the tickets (below) |
| `path` | none | Must be a git checkout. Write it as `~/...` or absolute, never relative: the tick timer runs from elsewhere |
| `source` | none | `github` (issues), `jira` or `markdown` (intent files). Suggest from where the team keeps tickets |
| `label` | `ai` | The label that queues a ticket for delivery. Ask what the team will tolerate seeing on tickets |
| `intents` | none | markdown only, and required: folder of `*.md` intents |
| `skip_statuses` | `[]` | jira only: statuses never picked up, such as `["Backlog"]` |
| `base` | `main` | Detect with `git -C <path> symbolic-ref --short refs/remotes/origin/HEAD`. Write it only if not `main` |
| `[repos.<name>.commands] qa` | none | Most valuable optional key: without it the `qa` step is skipped and the reviewer runs checks itself. Suggest from the repo (`uv.lock` → `uv run pytest`, `poetry.lock` → `poetry run pytest`, `package.json` scripts → `npm test`, `Makefile` test target). It runs as the user on PR branches under review too, so ask only for repos they trust |

A Jira ticket is picked up only when assigned to the user and carrying both `label` and a label equal to the repo's table name. Ask what the tickets are labelled, and name the table to match.

**The repo's own flows.** For each new repo, look for guidance that competes with workshop's way of moving work: a `CLAUDE.md` naming a stack tool or rules on reviews, Copilot threads or prose; its own review, prose or requirements skills in `.claude/skills/`; a delivery plugin in `.claude/settings.json` `enabledPlugins`; hooks that insist on a skill by name. Workshop's way wins by default, and the repo's flows are declined. If you find any, list them and ask once which to keep or replace with the user's own skill, recommended option "workshop's way for all". Write the answers to `<name>/workflow.md` under `$WORKSHOP_REPOS` (default `~/.config/workshop/repos`), one plain line per area (see the README, Changing how a step works). Write nothing when the user keeps workshop's way.

If any repo is `jira`, `[jira]` needs:

| Key | How to settle it |
|---|---|
| `base_url` | `https://<site>.atlassian.net`. The Atlassian MCP, if connected, can confirm the site |
| `email` | The user's Atlassian login |
| `token` | An API token from id.atlassian.com. Write `token = ""` and have the user fill it in themselves, e.g. `! $EDITOR ~/.config/workshop/config.toml`. `JIRA_TOKEN` in the shell overrides it, but the tick timer's systemd unit doesn't see shell variables, so a scheduled tick needs it in the file. The file is `chmod 600` for that reason |

## Tier 2: how it shows to your team

These change what other people see. Settle `[jira.moves]` and `[review] claim` with the user; the rest go in the proposal with their defaults unless something the user said points elsewhere.

- **`[jira.moves]`**: the Jira status for each move: `working`, `blocked`, `review`, `approved`, `merged`, plus `preprod` and `dropped`. A move left out never changes the ticket. Offer to read the project's real statuses through the Atlassian MCP (a recent ticket's transitions) and map them. Read them with `expand=transitions.fields`: where the transition into a move's status requires `resolution`, offer the table form, such as `dropped = { status = "Done", resolution = "Dropped" }`, with a resolution from the project's list, or Jira refuses the move. GitHub uses `ai-<move>` labels instead, nothing to set.
- **`[jira] route`**: only if the board allows moving to the next column only. The columns in order.
- **`[jira] unblocked`**: statuses where a blocking ticket no longer holds work back, though Jira doesn't count them as Done, such as `Ready for Release`. Only if the user's board has them.
- **`[prs] search`**: narrows the PRs tab, in GitHub search terms. Suggest `org:<org>` from the repos' `origin` remotes.
- **`[review] claim`**: how a review shows as yours: `none` (default, for teams that assign reviewers), `github-reviewer`, `github-assignee`, `chat-reaction` (needs `[chat]`). **`mode`**: `silent` (default, nobody sees anything until you post) or `claimed`. Only worth raising when `claim` isn't `none`. Say how it shows on the board: in silent mode `a` reacts only when you post, and `A` or `m` reacts at once.
- **`comment_prefix`**: default `[ai]`, starts everything workshop posts. Only change it if the team wants another marker. It can't be empty.

## Tier 3: enhancements

Offer as one multi-select question: "Anything else to set up? Each is off or at its default without it." Leave out what's already settled, such as chat when the user said reviews come through Slack. Then take each chosen one in turn, again as a proposal rather than key by key.

| Setting | Default without it | Worth it when |
|---|---|---|
| `[chat]` | No chat at all | Review asks arrive in Slack. Then also `[prs] intake = "chat"`, so GitHub requests alone don't reach Needs you |
| `max_active` | 2 runs at once | Machine or limits want more or fewer |
| `max_waiting` | Off | The pile on you grows faster than you clear it |
| `[throttle]` | Weekdays 8-18, pause new at 80%, steps at 90% of the 5h limit | Different hours or tolerances |
| `sweeps` per repo | None | Want scheduled looks at deps, docs, backlog, logs. On Jira needs `[jira] project` to create tickets and `moves.dropped` to close them |
| `[jira] project` | Can't create tickets | Sweeps, or `workshop prepare` from an idea |
| `environments` per repo | `main`/`master` prod, `dev`/`preprod` preprod | Other branch names |
| `commands.logs` | `logs` sweep proposes nothing | Using the logs sweep |
| `commands.ci` per repo | A draft gets checks only where the repo's CI runs on drafts | The repo's CI skips drafts. The command starts CI on `$BRANCH` after each push, such as `az pipelines run --id <n> --branch "$BRANCH"`. Without it, the `ci` step passes with a note that no checks ran |
| `[review] copilot` | Off: no Copilot review of a draft | The org has Copilot code review. Copilot reviews each draft, and its threads go back to the builder before you see the draft. Needs `ci` and `copilot` in the `ticket` pipeline's steps where the user keeps their own `ticket.toml` |
| `[backlog]` labels | `planned`, `designed` | Team already uses other names |
| `done_hours` | 24 | Finished work should linger longer or shorter |
| `[review] after_post`, `block_failing_ci`, `[chat.reactions]` | Off, off, `eyes` for the claim and no other reaction | Fine-tuning chat reviews |
| `pipeline` and friends per repo | `ticket`, `review`, `reply`, `fix-ci`, `prepare` | e.g. `gated` for an approval after planning. Custom pipelines: README, Your own pipelines |

**Chat specifics.** Tool names depend on the user's MCP server. List the real ones (`claude mcp list`, or the tools visible in this session) and don't copy the example's: a claude.ai Slack connector's are `mcp__claude_ai_Slack__*`. `read_tools` gets read tools only (search, read channel, read thread), because message text steers that call. `react_tool` if `claim = "chat-reaction"` or `after_post = "chat-reply"`, and `reply_tool` only for `chat-reply`. With `chat-reply`, also propose `[chat.reactions]` with `approved` and `comments`: only reactions named there go out after a review, and nothing warns when none are. Take the names from the emoji the team already puts on recent asks, read with a read tool. Channels may need IDs; `slack_search_channels` can find them. Suggest a lower `check_minutes` (default 30) where chat is the review intake.

## Write it

- New file: follow `config.example.toml`'s layout: top-level keys first (TOML puts a key under the table above it), then `[repos.*]` and `[jira]`, then the team-facing tables, then enhancements. A one-line comment on any non-obvious value saying why it was chosen. Keep the example's first line, which says never to commit it.
- Existing file: edit the text in place. Keep the user's comments, order and anything this run didn't touch. Don't regenerate it through a TOML library.
- Then: `chmod 600`, parse check with `python3 -c 'import sys, tomllib; tomllib.load(open(sys.argv[1], "rb"))' <file>`, and `workshop doctor --no-launch`. Fix what it reports, or explain what the user must do (a Jira token, `gh auth login`, a git identity). While the Jira token is still empty, doctor's source check fails with `no Jira token`. That's expected: tell the user to fill it in and run doctor again, which then signs in and names who the token belongs to.
- Offer the full `workshop doctor` last: it launches a real agent and spends a little.

## Finish

A short table: each setting, set to what, or left at default, or skipped and why. Then what's left for the user:

- `cd <path> && claude` once per new repo, to accept folder trust.
- Fill in the Jira token, if it's still empty.
- `workshop schedule 5m` when they want tickets to start unattended. Not before.
