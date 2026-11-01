# Workflow

How work is meant to move through workshop, from an idea to production, and what you do at each point. The [workshop README](../plugins/workshop/README.md) is the reference for commands and settings.

The board is where you work. Open it with `prefix+shift+b`. Agents do the work, and you make the decisions. Nothing goes out in your name without your go.

## The path of a ticket

| Stage | What happens | Your part |
|---|---|---|
| Capture | An idea becomes a ticket, by hand or with `p` on the Backlog tab. Sweeps can propose tickets too | Type the idea |
| Prepare | Two fresh agents grill you, on the requirements and then the design | Answer, then accept each one. It's written to the ticket |
| Queue | You add the delivery label, which puts the ticket in the queue | Read the ticket, then add the label |
| Build | An agent plans the tasks and implements them | Nothing, unless it stops |
| Test | The repo's checks run, then a fresh agent reviews the work | Nothing, unless it stops |
| Self-review | A draft PR opens in hunk, with the reviewer's notes on the diff | `g` to go there. Comment, send to the agent, then finish with `prefix+shift+u` |
| Team review | The PR is marked ready for reviewers | Ask for review, and answer comments with `a` on the Review tab |
| Deploy | A merge to `dev` or `preprod` shows as `preprod`. A merge to `main` or `master` shows as `in prod` and closes the ticket | Do any listed release steps, then accept |
| Maintain | Scheduled sweeps look at dependencies, docs, the backlog and logs | Accept or skip what they propose |

Reviewing someone else's PR is shorter: `a` on the Review tab runs the repo's checks and a fresh AI review into hunk. You shape the notes and post from there with `prefix+shift+u`.

## When something needs you

A run that needs you shows one of three states.

| State | Means | What you do |
|---|---|---|
| `decide` | It stopped as planned, at a gate or with a question | Read what's shown and decide |
| `fix` | Something went wrong: a failure, a time limit, or an agent that's gone | Read why, then choose |
| `your turn` | An agent in Prepare is waiting for your answer | `g`, then answer it there |

The board shows the reason, the report and a line saying what each key does, so you shouldn't need to open the workspace to decide.

## The three keys

Anything stopped for you has the same three keys on the bottom row. They mean the same thing everywhere.

| Key | Means | Use it when |
|---|---|---|
| `a` accept | This is good enough, move on | You agree with what's shown, or you've settled the problem yourself |
| `x` redo | Not yet, do this step again | You want a change. Your note goes to the agent |
| `d` drop | Stop working on this | The work isn't worth carrying on with |

What each does depends on where the run stopped:

| Where it stopped | `a` accept | `x` redo | `d` drop |
|---|---|---|---|
| `fix`: a step went wrong | Treats the step as passed and moves on, even past a failed reviewer. The result is still a draft PR for a person | Runs the step again. A note is optional and steers it | Ends the run |
| `decide`: an agent's question | Moves on without answering | Your note is the answer, and the agent carries on | Ends the run |
| `decide`: a gate on tasks | Starts building | Sends it back. A note is required, saying what to change | Ends the run |
| `decide`: a Prepare gate | Writes that section to the ticket and adds `planned` or `designed` | Sends it back to a fresh agent, up to 3 times. A note is required | Ends the run, and nothing further is written |
| `decide`: a sweep's proposals | Does everything you haven't skipped | Skips the numbers you give, or `all`. Skipped ones aren't proposed again | Ends the run, and nothing is done |
| `decide`: release steps | Says you've done them, and the run lands | Use it if a step went wrong: the run moves to `fix` with your note, and only `d` ends it | Ends the run, and the ticket stays open |

After a delivery is dropped, the ticket stays open with a comment and a `dropped` state, so you can close it or start over with `workshop start <repo> <key> --restart`. Its worktree and branch stay too, for you to keep or remove. Prepare and sweeps hold no work, so dropping one removes its checkout, and the ticket is left as it was.

In delivery, everything you do leaves a comment on the ticket, so the ticket is the full history. Prepare and sweeps touch a ticket only on your accept.

## From outside the board

On a `fix` or an agent's question, a plain comment of your own on the ticket counts as `x`, and closing the ticket counts as `d`. They're read at the next tick, or on `r`. The same actions exist as commands and ticket comments:

| Key | Terminal | On the ticket |
|---|---|---|
| `a` | `workshop pass <key>`, or `workshop approve <key>` at a gate | `/ai pass` |
| `x` | `workshop retry <key> [note]`, or `workshop reject <key> <note>` at a gate | Any comment, or `/ai retry` |
| `d` | `workshop drop <key> [reason]` | `/ai drop`, or close the ticket |

Gates are the exception: they're answered only from the board or the terminal, never from the ticket.
