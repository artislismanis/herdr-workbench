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
| `workshop start <repo> <key> [--override] [--restart]` | Starts one ticket now. `--override` skips the last-editor check. `--restart` redoes a ticket that already has a run, in the same worktree and workspace |
| `workshop status` | Every run, plus what's skipped or held |
| `workshop needs-you` | Only what's waiting on you, and what to run |
| `workshop explain <key>` | One run in detail |
| `workshop resume <key>` | Tries a parked run's stopped step again |
| `workshop review <key>` | Opens a finished run in hunk, with the reviewer's notes on the diff. A run does this itself when its PR is ready |
| `workshop finish` | Ends a review from its workspace, on `prefix+shift+u`: see Board |
| `workshop approve <key>`, `workshop reject <key> <note>` | Answers a gate |
| `workshop answer <key> <text>` | Answers an agent's question |
| `workshop retry <key> [note]` | Runs a stopped step again, with guidance if given |
| `workshop pass <key> [note]` | Accepts a stopped agent or command step and moves on |
| `workshop drop <key> [reason]` | Ends a stopped run. The worktree and branch stay |
| `workshop comment <key> <text>` | Posts your comment on the ticket and acts on it now |
| `workshop list` | Ready tickets |
| `workshop schedule [<every>\|off]` | Shows, sets or stops the tick timer |
| `workshop stats` | Tokens, cost, first-pass rate and stops across all runs |
| `workshop refresh` | Reads ticket comments on stopped runs, and saves the backlog and PR snapshot, without starting anything new |
| `workshop board` | The board. `prefix+shift+b` in herdr opens it in its own workspace |
| `workshop doctor [--no-launch]` | Finds what would stop a run |

A `waiting` run stopped as planned, at a gate or with a question. A `parked` run stopped because something went wrong. Neither counts towards `max_active`.

## Safety

- A ticket runs only if you were the last person to change it, because ticket text steers the agent. Use `--override`, or add the `<label>-override` label yourself, to run one anyway.
- Gates are answered only from this command line, so nothing in a ticket can approve one.
- A blocked run takes guidance only from a comment you wrote, and nobody else edited. Other people's comments reach the agent as background, never as instructions. Everything workshop posts starts with `comment_prefix`, `[ai]` by default, so it can't count as your comment.
- Agents never commit or push. Workshop does, and it only opens draft PRs.

## Reviewing a PR

`a` on a PR in the Review tab, or `workshop pr <url>`, reviews it with AI. The repo needs a `[repos]` table with its path, found by its origin. Workshop checks out the PR's own branch and runs the repo's `qa` command, then a fresh read-only agent writes a report and a draft review, and hunk opens with those notes against where the PR branched. A failing `qa` becomes a finding. Shape the review in hunk, then `prefix+shift+u` posts it, with the choice of comment, approve, request changes or draft.

When a run's PR merges or closes, its worktree and workspace go too, and `wt` deletes the branch if it merged. A worktree with uncommitted changes, or commits that aren't on the PR, stays, and the event log says why.

That key is the only way anything reaches GitHub: a review run posts nothing, moves nothing, and never commits or pushes, whatever its pipeline says. PRs from forks are refused. The repo's checks run the PR's code on your machine, as a repo's own branch would, so leave `qa` out of the repo's commands if you don't want that. `REVIEW.md` in a repo guides the reviewer. `review_pipeline` in a repo's table picks another pipeline, `review` by default.

Reviewing the same PR again, once it has new commits, follows the PR's head and looks only at what changed since the last review: are the flagged issues fixed, and do the changes add new bugs. It doesn't go back over code nobody flagged, unless the new code raises a major concern. A force-push makes it a full review. A step's `again` names the prompt for those rounds. People's threads load into hunk beside the agent's notes, and aren't posted again.

## Pipelines

Pipelines are TOML files in `pipelines/` or `~/.config/workshop/pipelines/`. Set one per repo with `pipeline = "<name>"`.

| Pipeline | Steps |
|---|---|
| `ticket` | `tasks`, `implement`, `qa`, `ai-review` by a fresh agent, `open-pr` |
| `gated` | The same, with your approval after `tasks` |
| `review` | `qa`, then `ai-review` of a PR by a fresh agent, into hunk |

`ticket_stage` names the stage the ticket itself stands for. It has no steps yet, so it leads the Workflow panel with the ticket, and it counts in the strip. Any step can set `stage`, and the Workflow panel groups by it. It's a free name, so use your own, and leave it out for a flat list. The shipped pipelines use Build, Test and Deploy, from [the AI-native SDLC playbook](https://claude.com/resources/articles/the-ai-native-sdlc-playbook).

A `command` step runs what the repo sets for that step id under `[repos.<name>.commands]`, with no agent and no tokens. Its output is the report, and a failed `qa` goes back to `implement`. A repo without the command skips the step, shown as `SKIPPED`, and `ai-review` runs the repo's checks itself. Commands run as you, with your access, so only set them on repos you trust.

Agent steps can set `model` and `minutes` (default 45). Steps that aren't `fresh` share one agent, so they must agree on the model.

## Terms

| Term | Means |
|---|---|
| Pipeline | The ordered steps a run follows, in a TOML file |
| Stage | A group of steps, such as Build, and nothing else |
| Step | One piece of work in a pipeline, by an agent, a person or workshop |
| Round | One attempt at a step. A failed review sends the work round again |
| Status | Where a run stands: `running`, `parked`, `waiting` or `in review`, then what became of it, such as `merged` or `dropped` |
| Column | Where a ticket sits on Backlog: New, Planned, Designed or Queued |
| Move | What workshop does to the ticket as a run goes on: `working`, `blocked`, `review`, `approved` or `merged` |
| Self-review | You reading the change in hunk, before anyone else sees it |
| Peer review | Other people reviewing the PR |
| QA | The `qa` step: the repo's own tests, run as a command |
| AI review | The `ai-review` step: a fresh agent checks the work before you see it |

## Limits

The board's 5h and 7d meters show whether you'd last until the reset at an even pace, not how much is used: 50% used with 15 minutes left is green. Amber is more than 10 points ahead of that pace, and red more than 25, or the limit reached. The cpu and ram meters below them come from `/proc`: ram counts what's available, which includes cache that can be reclaimed, and peaks last as long as the board stays open.

`[throttle]` paces work against Claude's limits, using `~/.local/state/claude-usage.json`. The status line saves it, and each tick and board refresh updates it from `claude -p /usage`, which costs no tokens and includes use outside herdr. See `config.example.toml`. Running steps always finish. `/usage` prints text for people, so if it can't be read the saved figures stay. Without the file, or with an old one, tickets run one at a time.

## Tickets

The ticket follows the run through `working`, `blocked`, `review`, `approved` and `merged`: `ai-<move>` labels on GitHub, and the statuses in `[jira.moves]` on Jira. A draft PR stays in `working`, because it's still in self-review, and `review` starts when you mark it ready. Each `tick` follows the PR. Where a board allows only the next column, `route` lists the columns to walk. A move that fails is logged, leaves the ticket where it got to, and never stops a run. Workshop comments when it starts, when it stops and when the draft PR is ready.

When a run stops on a question or a problem, the ticket moves to `blocked`. Comment on it with what's missing, and the next `tick` hands that to the agent and carries on. That needs the schedule on, or a manual `workshop tick`. A merged PR moves the ticket to `merged`, and on GitHub closes the issue.

## Board

`prefix+shift+b` opens the board in a `workshop` workspace at the top of the list, or goes back to it if it's open. Quitting closes that workspace. Opening it costs no tokens or API calls: it reads run files, the event log and the backlog snapshot, and shows how old the snapshot is.

Tab and Shift+Tab switch views. Tabs, rows and the buttons along the bottom work with the mouse as well as the keys, and the wheel scrolls. Clicking a row opens it. Dragging selects text inside the list or the panel and copies it on release, with `wl-copy`. Shift+drag is your terminal's own selection, across the whole window.

Delivery, where it opens, shows counts and usage meters, then runs by who they wait on, each with a strip of its stages (`●─◐─○`), and recent events below. Green is fine, amber waits on a person or had to go round again, and red has stopped. A pipeline without stages shows each step. Opening one shows its steps as a timeline, with rounds, verdicts, cost, the log and what to run next. Backlog shows a bar per column, then the columns as cards on a wide enough window. Review lists PRs, yours and others', by whose move it is: `●` in blue is yours, `○` in cyan someone else's, and yellow means someone else reviewed a PR you're asked to review. Enter shows its reviews, threads and comments from the last refresh, with open threads first and `blocking:` comments in red. `g` goes to a run's workspace, with hunk open on a draft PR, and `r` refreshes the backlog and PRs, which makes API calls but uses no tokens. On a window 130 wide or more, Delivery and Review show the selected item's detail in a panel beside the list, with its actions at the top. Clicking a row selects it, the wheel over the panel scrolls it, and so do Page Up and Page Down. Enter still opens the detail full width.

A run's detail is built around what to do next. A stopped run starts with Needs you, taken from the `## Needs you` section of the report that stopped it, with its actions underneath. The Workflow section comes next, as a tree under stage headings that carry the stage's colour, status and total, each with its verdict, rounds, cost and latest report, and the stopped step shows its findings in place. `▸` marks the report to read first. Older rounds sit behind `+2 older`, or press `e`: `ai-review.md` is always the latest, and `ai-review-1.md` is the first round, counting up. Enter or a click opens a report, and `esc` goes back.

Colours are the terminal's own, so the board follows its colour scheme. Use herdr's `terminal` theme to match. On a stopped run, the bottom row offers comment, retry, pass and drop, or approve, reject and drop at a gate. Each asks for its text, then runs the matching `workshop` command and leaves a comment on the ticket.

Each run works in a linked worktree, grouped under its repo's workspace in herdr, which opens if it isn't already. A finished run opens hunk beside its agent, with the reviewer's notes on the diff. A note that can't sit on a changed line waits on the first change as `PR-level:`, and goes in the PR-level comment if you post the review. The detail shows the reviewer's handover: what changed, and a recommendation to approve, or to approve after named checks. The ticket's Draft PR comment carries the same text. Comment in hunk and send those to the agent with `prefix+shift+s` if it needs changes.

`prefix+shift+u` finishes a review, and offers only what applies. On a delivered run that's pushing the agent's fixes, marking the PR ready, or posting the notes as a PR comment. GitHub won't let you approve your own PR, so ready is the end of it. Anywhere else it's a plain PR review, with approve and request changes. Nothing is pushed until you choose it there.

## Concluding a stopped run

A stopped run always has a way out, from the terminal or the ticket:

| Terminal | Your own ticket comment | Does |
|---|---|---|
| `workshop retry` | `/ai retry [note]`, or any plain comment | Runs the step again, with your note as guidance |
| `workshop pass` | `/ai pass [note]` | Takes the step as good enough, even over a reviewer, and moves on. Parked or waiting agent and command steps only |
| `workshop drop` | `/ai drop [reason]`, or close the ticket | Ends the run. It moves to Landed as `dropped` |

Terminal actions happen at once. `workshop comment <key> "text"` posts a real comment and acts on it straight away, and takes `/ai` commands too. Comments made on the ticket itself are read on the next `tick` or `workshop refresh`, because nothing tells workshop about them sooner. Everything leaves a comment, so the ticket holds the full history. Guidance from a person gives the step a fresh set of review rounds, and every step can read it. `/ai` follows `comment_prefix`. Only your own unedited comment counts, and gates still need `approve` or `reject` from the terminal.

## Backlog

Each `tick` and `workshop refresh` saves open tickets to `~/.local/state/workshop/board.json`, sorted by label: none is `new`, then `planned`, `designed`, and the delivery label is `queued`. `[backlog]` renames the first two. Claimed tickets leave the list. On Jira the repo's label sets the scope, so a ticket without it doesn't show. A source that fails keeps its last good list and records the error. Polling makes API calls but uses no tokens.

## PRs

The same poll saves your open PRs, PRs you're asked to review or have reviewed, and those merged in the last week, in one GitHub call. Each gets whose move it is. `[prs] search` narrows them, in GitHub search terms.

| Your move | Waiting on others |
|---|---|
| Yours: draft, checks failing, changes requested, fixed and not re-requested, comments to address, approved | Yours: awaiting review, re-review requested |
| Theirs: needs review, re-review | Theirs: draft, waiting for fixes, new commits, approved |

A push to someone's PR stays with them until they ask again, since it may be work in progress. Bots' reviews don't count.

## Run files

Briefs, reports and notes live in `~/.local/state/workshop/runs/<repo>--<key>/`, not in the repo, so they outlive the worktree. Each review round's report is kept as `<step>-<n>.md`. Agents reach the folder through `--add-dir`.

A step can set `output`, the file it writes, and `publish`: `pr` makes it the PR description and `ticket` posts it as a ticket comment.

## Cost

Each agent step records its turns, tokens, minutes and API-equivalent dollars, read from Claude Code's transcripts. `workshop explain` shows them, the ticket gets the summary with the draft PR, and `workshop stats` adds them up across runs. On a subscription the dollars aren't a bill, they're for comparing steps. Prices live in `workshop/cost.py`, and a model not listed there shows as unpriced.

## Running it on a schedule

Nothing runs by itself by default: without a schedule, a run moves only on agent events or a `tick`, so held runs stay held and new tickets wait. A systemd user timer is installed but off. Turning it on means ready tickets start unattended:

```bash
workshop schedule 5m    # on, ticks now and then every 5 minutes. Run it again with a new value to change the frequency
workshop schedule off
workshop schedule       # shows on or off, and how often
journalctl --user -u workshop-tick.service
```

The frequency takes a number and `s`, `m`, `min` or `h`. A tick costs no tokens, so shorter only means tickets start, and held runs carry on, sooner. It's kept in a systemd drop-in, so reinstalling doesn't reset it. `off` clears it.

It only runs while the distro is up.
