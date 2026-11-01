# Workflow

How work is meant to move through workshop, from an idea to production, and what you do at each point. The [workshop README](../plugins/workshop/README.md) is the reference for commands and settings.

The board is where you work. Open it with `prefix+shift+b`. Agents do the work, and you make the decisions. Nothing goes out in your name without your go.

## The path of a ticket

| Stage | What happens | Your part |
|---|---|---|
| Capture | An idea becomes a ticket, by hand or with `p` on the Backlog tab. Sweeps can propose tickets too | Type the idea |
| Prepare | Two fresh agents grill you, on the requirements and then the design | Answer, then approve each one. It's written to the ticket |
| Queue | You add the delivery label, which puts the ticket in the queue | Read the ticket, then add the label |
| Build | An agent plans the tasks and implements them | Nothing, unless it stops |
| Test | The repo's checks run, then a fresh agent reviews the work | Nothing, unless it stops |
| Self-review | A draft PR opens in hunk, with the reviewer's notes on the diff | `g` to go there. Comment, send to the agent, then finish with `prefix+shift+u` |
| Team review | The PR is marked ready for reviewers | Ask for review, and answer comments with `a` on the Review tab |
| Deploy | A merge to `dev` or `preprod` shows as `preprod`. A merge to `main` or `master` shows as `in prod` and closes the ticket | Do any listed release steps, then answer |
| Maintain | Scheduled sweeps look at dependencies, docs, the backlog and logs | Approve or skip what they propose |

Reviewing someone else's PR is shorter: `a` on the Review tab runs the repo's checks and a fresh AI review into hunk. You shape the notes and post from there with `prefix+shift+u`.

## When something needs you

A run that needs you shows one of three states.

| State | Means | What you do |
|---|---|---|
| `decide` | It stopped as planned, at a gate or with a question | Read what's shown and decide |
| `fix` | Something went wrong: a failure, a time limit, or an agent that's gone | Read why, then choose |
| `your turn` | An agent in Prepare is waiting for your answer | `g`, then answer it there |

The board shows the reason and the report, so you shouldn't need to open the workspace to decide.

## Answering

Anything stopped for you has two keys on the bottom row.

- **`a` answer:** a box opens and says what happens here. Press enter to carry on, or type what you want instead.
- **`d` drop:** stop working on this.

Carrying on means the obvious next thing: start building, write it to the ticket, do what a sweep listed, or run a failed step again. Whatever you type goes to the agent as what to change. There are two small differences: an agent's question needs a typed answer, and a sweep's box takes the numbers to skip, or `all`, then you press `a` again for the rest.

To treat a failed step as passed without running it again, use `workshop pass <key>`. It's a rare override, so it isn't on the board.

After a delivery is dropped, the ticket stays open with a comment and a `dropped` state, so you can close it or start over with `workshop start <repo> <key> --restart`. Its worktree and branch stay too, for you to keep or remove. Prepare and sweeps hold no work, so dropping one removes its checkout, and the ticket is left as it was.

In delivery, everything you do leaves a comment on the ticket, so the ticket is the full history. Prepare and sweeps touch a ticket only on your go.

## From outside the board

On a `fix` or an agent's question, a plain comment of your own on the ticket counts as an answer, and closing the ticket counts as `d`. They're read at the next tick, or on `r`. The same actions exist as commands and ticket comments:

| Key | Terminal | On the ticket |
|---|---|---|
| Enter on `a` | `workshop retry <key>`, or `workshop approve <key>` at a gate | `/ai retry` |
| Words on `a` | `workshop retry <key> <note>`, or `workshop reject <key> <note>` at a gate | Any comment |
| Not on the board | `workshop pass <key>` | `/ai pass` |
| `d` | `workshop drop <key> [reason]` | `/ai drop`, or close the ticket |

Gates are the exception: they're answered only from the board or the terminal, never from the ticket.
