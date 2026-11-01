# Workflow

How work is meant to move through workshop, from an idea to production and back to the next idea, and what you do at each point. There are three phases: dream it up on the Backlog, build it on Delivery and Review, and operate it on Operate, where deploys and sweeps turn up the next tickets. The [workshop README](../plugins/workshop/README.md) is the reference for commands and settings.

The board is where you work. Open it with `prefix+shift+b`. Agents do the work, and you make the decisions. Nothing goes out in your name without your go.

## The path of a ticket

| Stage | What happens | Your part |
|---|---|---|
| Capture | An idea becomes a ticket, by hand or with `p` on the Backlog tab. Sweeps can propose tickets too | Type the idea |
| Prepare | Two separate talks with fresh agents: requirements, which makes the ticket `planned`, then design, which makes it `designed`. `p` with the ticket's number starts whichever is next, and on a designed or queued ticket that's the queue | Answer in the agent's pane. When it plays back a summary, your yes puts it on the ticket, with nothing to approve on the board |
| Queue | The design talk ends by asking if it's ready and where it goes among what's queued. Agents take tickets in that order as slots free up | Say so in the talk, or say no and read it first. `p` with its number queues it later, moves it, or takes it back out to `designed`. Adding the delivery label by hand works too |
| Build | An agent plans the tasks and implements them | Nothing, unless it stops |
| Test | The repo's checks run, then a fresh agent reviews the work | Nothing, unless it stops |
| Self-review | A draft PR opens in hunk, with the reviewer's notes on the diff | `g` to go there. Comment, send to the agent, then finish with `prefix+shift+u` |
| Team review | The PR is marked ready for reviewers | Ask for review, and answer comments with `a` on the Review tab |
| Deploy | A merge to `dev` or `preprod` shows as `preprod`. A merge to `main` or `master` shows as `in prod` and closes the ticket | Do any listed release steps, then answer |
| Maintain | Scheduled sweeps look at dependencies, docs, the backlog and logs | Approve or skip what they propose |

Delivery owns a ticket until your self-review lets it go with `prefix+shift+u`. From then the PR is managed on the Review tab, and Delivery keeps one line saying `in review`. A review ends in a merge, and the ticket moves to the Operate tab.

## Reviewing a PR

The Review tab works like Delivery: Needs you, With agents, With people, and Done lately.

1. `a` on someone else's PR runs the repo's checks and a fresh AI review. The row shows `reviewing` with its stages, under With agents.
2. When it's done, the row moves to Needs you as `your turn`, with the AI's recommendation. Its detail says what to do next, then gives a four line briefing to read before the diff: the intent, what the code delivers against it, the challenges, and the recommendation.
3. `g` opens hunk with the AI's notes on the diff, and the reviewer beside it. Ask it anything. It changes nothing and posts nothing.
4. Keep, change or drop the notes, then post with `prefix+shift+u`. What you post is the review, and it's all on GitHub, so the workspace and checkout go whatever you chose, and you're back on the board. The row shows `posted` until the PR moves on.

New commits offer `a` again, in a fresh checkout, for a review of what's changed.

`a` on your own PR with comments has an agent draft the fixes and replies. Nothing goes out yet: the row moves to Needs you, and its detail shows each reply. `g` opens the fix in hunk beside the agent, so you can tell it what to change. `prefix+shift+u` then pushes the fix and sends the replies, and a reply about a fix names its commit. That also re-requests review from whoever asked for changes, on GitHub, and the workspace and checkout go, as with a posted review. If you push fixes some other way, the row says `re-review`, because GitHub keeps showing changes requested until it's reviewed again, and `a` asks for another look. A reply to an inline comment joins that thread. Comments in a review's summary have no thread on GitHub, so those replies are general comments that quote what they answer.

Posting asks what the review should be. These are GitHub's own review types, and your inline comments and PR comment go with whichever you choose:

| Choice | On GitHub |
|---|---|
| Comment | Your comments, with no verdict |
| Approve | Your comments, plus your approval |
| Request changes | Your comments, plus a verdict on the whole PR. GitHub doesn't tie it to any one comment. If the repo requires reviews, it can't merge until you approve or the review is dismissed. It needs a PR comment saying what you want |
| Draft | A pending review only you can see, to finish on GitHub. The PR itself doesn't change |
| Cancel | Nothing leaves. The notes stay in hunk |

One key picks, with no enter. Enter on its own cancels. Approve and Request changes aren't offered on your own PR, because GitHub refuses them from the author.

## When something needs you

A run that needs you shows one of three states.

| State | Means | What you do |
|---|---|---|
| `decide` | It stopped as planned, at a gate or with a question | Read what's shown and decide |
| `fix` | Something went wrong: a failure, a time limit, or an agent that's gone | Read why, then choose |
| `your turn` | An agent in Prepare is waiting for your answer | `g`, then answer it there |

The board shows the reason and the report, so you shouldn't need to open the workspace to decide.

## Answering

Press `a` on anything stopped for you. A card opens at the bottom with a short brief and the question, the options, and room to write your own. Up and down choose, or just start typing, and enter confirms. If that run has another question, it follows straight away. When it's done, the board tells you and moves to the next thing that needs you, for `a` to pick up. `d` drops the run.

The brief is five lines the agent writes for you: what, why, background, bottom line and the ask. You should be able to decide from it alone. The full report stays in the panel above if you want it. Several decisions get a card each, with a line on how they fit together, and your answers go back as one.

An agent's question has no options, only your answer. A sweep asks about its proposals one at a time, and nothing happens until you've answered them all. Your own words are guidance: the agent acts on them and everything goes ahead, and you're only asked again if it has a real question. A skipped proposal isn't proposed again.

To treat a failed step as passed without running it again, use `workshop pass <key>`. It's a rare override, so it isn't on the board.

After a delivery is dropped, the ticket stays open with a comment and a `dropped` state, so you can close it or start over with `workshop start <repo> <key> --restart`. Its worktree and branch stay too, for you to keep or remove. Prepare and sweeps hold no work, so dropping one removes its checkout, and the ticket is left as it was.

In delivery, everything you do leaves a comment on the ticket, so the ticket is the full history. Prepare and sweeps touch a ticket only on your go.

## What a sweep looks at

Nothing is searched for. A sweep only sees what its `[repos.<name>]` table in the config points at, and a repo has no sweeps until you list them with `sweeps = { docs = "monthly" }`.

| Sweep | Looks at | Set by |
|---|---|---|
| `deps` | That repo's dependabot alerts and PRs, and its lock files | The repo's `path` |
| `docs` | That repo's docs against its code, and its last 30 days of commits | The repo's `path` |
| `backlog` | That repo's open tickets. On GitHub, its issues. On Jira, tickets with the repo's label that aren't done | The repo's `source` and `label` |
| `logs` | Whatever the repo's `logs` command prints, such as a Databricks or `az` query. Without one it proposes nothing | `[repos.<name>.commands]` |

Every sweep also gets the repo's open tickets, so it doesn't propose what's already there. Tickets in the delivery queue or being worked on are left out, and can't be touched.

## From outside the board

On a `fix` or an agent's question, a plain comment of your own on the ticket counts as an answer, and closing the ticket counts as `d`. They're read at the next tick, or on `r`. The same actions exist as commands and ticket comments:

| Key | Terminal | On the ticket |
|---|---|---|
| Enter on `a` | `workshop retry <key>`, or `workshop approve <key>` at a gate | `/ai retry` |
| Words on `a` | `workshop retry <key> <note>`, or `workshop reject <key> <note>` at a gate | Any comment |
| Not on the board | `workshop pass <key>` | `/ai pass` |
| `d` | `workshop drop <key> [reason]` | `/ai drop`, or close the ticket |

Gates are the exception: they're answered only from the board or the terminal, never from the ticket.
