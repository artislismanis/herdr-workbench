# Workshop

Turns a ticket from GitHub, Jira or a Markdown intent into a self-reviewed draft PR. It runs as a herdr plugin, with one agent per ticket in its own worktree. Standard library Python, no daemon: each run is a state file, and `tick` or an agent event moves it on.

## Set up

Copy `config.example.toml` to `~/.config/workshop/config.toml`, `chmod 600` it, and add your repos. Then:

```bash
cd <repo> && claude     # accept folder trust once per clone; its worktrees inherit it
workshop doctor         # checks tools, logins and repos, and launches a real agent
```

## Commands

| Command | Does |
|---|---|
| `workshop tick` | Moves every run on, then starts one ready ticket if there's room |
| `workshop start <repo> <key> [--override]` | Starts one ticket now. `--override` skips the last-editor check |
| `workshop status` | Every run, plus what's skipped or held |
| `workshop needs-you` | Only what's waiting on you, and what to run |
| `workshop explain <key>` | One run in detail |
| `workshop resume <key>` | Tries a parked run's stopped step again |
| `workshop review <key>` | Opens a finished run in hunk, with the reviewer's notes on the diff |
| `workshop approve <key>`, `workshop reject <key> <note>` | Answers a gate |
| `workshop answer <key> <text>` | Answers an agent's question |
| `workshop list` | Ready tickets |
| `workshop schedule [<every>\|off]` | Shows, sets or stops the tick timer |
| `workshop stats` | Tokens, cost, first-pass rate and stops across all runs |
| `workshop doctor [--no-launch]` | Finds what would stop a run |

A `waiting` run stopped as planned, at a gate or with a question. A `parked` run stopped because something went wrong. Neither counts towards `max_active`.

## Safety

- A ticket runs only if you were the last person to change it, because ticket text steers the agent. Use `--override`, or add the `<label>-override` label yourself, to run one anyway.
- Gates are answered only from this command line, so nothing in a ticket can approve one.
- A blocked run takes guidance only from a comment you wrote, and nobody else edited. Other people's comments reach the agent as background, never as instructions. Everything workshop posts starts with `[workshop]`, so it can't count as your comment.
- Agents never commit or push. Workshop does, and it only opens draft PRs.

## Pipelines

Pipelines are TOML files in `pipelines/` or `~/.config/workshop/pipelines/`. Set one per repo with `pipeline = "<name>"`.

| Pipeline | Steps |
|---|---|
| `ticket` | Plan, build, verify by a fresh agent, draft PR |
| `gated` | The same, with your approval after the plan |

Agent steps can set `model` and `minutes` (default 45). Steps that aren't `fresh` share one agent, so they must agree on the model.

## Limits

`[throttle]` paces work against Claude's limits, using `~/.local/state/claude-usage.json`, which the status line saves. See `config.example.toml`. Running steps always finish. Without the file, or with an old one, tickets run one at a time.

## Tickets

The ticket follows the run through `working`, `blocked`, `review` and `done`: `ai-<stage>` labels on GitHub, and the statuses in `[jira.moves]` on Jira. A move that fails is logged and never stops a run. Workshop comments when it starts, when it stops and when the draft PR is ready.

When a run stops on a question or a problem, the ticket moves to `blocked`. Comment on it with what's missing, and the next `tick` hands that to the agent and carries on. That needs the schedule on, or a manual `workshop tick`. A merged PR moves the ticket to `done`, and on GitHub closes the issue.

## Run files

Briefs, reports and notes live in `~/.local/state/workshop/runs/<repo>--<key>/`, not in the repo, so they outlive the worktree. Each review round's report is kept as `<step>-<n>.md`. Agents reach the folder through `--add-dir`.

A step can set `output`, the file it writes, and `publish`: `pr` makes it the PR description and `ticket` posts it as a ticket comment.

## Cost

Each agent step records its turns, tokens, minutes and API-equivalent dollars, read from Claude Code's transcripts. `workshop explain` shows them, the ticket gets the summary with the draft PR, and `workshop stats` adds them up across runs. On a subscription the dollars aren't a bill, they're for comparing steps. Prices live in `workshop/cost.py`, and a model not listed there shows as unpriced.

## Running it on a schedule

Nothing runs by itself by default: without a schedule, a run moves only on agent events or a `tick`, so held runs stay held and new tickets wait. A systemd user timer is installed but off. Turning it on means ready tickets start unattended:

```bash
workshop schedule 5m    # on, every 5 minutes. Run it again with a new value to change the frequency
workshop schedule off
workshop schedule       # shows on or off, and how often
journalctl --user -u workshop-tick.service
```

The frequency takes a number and `s`, `m`, `min` or `h`. A tick costs no tokens, so shorter only means tickets start, and held runs carry on, sooner. It's kept in a systemd drop-in, so reinstalling doesn't reset it. `off` clears it.

It only runs while the distro is up.
