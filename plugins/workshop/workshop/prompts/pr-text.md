Write the title and description of the pull request for this change, the way this repo asks for them.

<!-- workshop -->

Do not edit any file in the repo. Write only the two files named below.

Read these first:

- The ticket, in `{run}/brief.md`. Where the ticket was split, its summary names the one PR this ticket builds. Title the PR for that part, not the whole ticket.
- What the builder wrote, in `{run}/implement.md`, and the review, in `{run}/ai-review.md`. `{run}/qa.md`, if it exists, holds the repo's checks.
- The repo's PR template: the first of `.github/pull_request_template.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `pull_request_template.md` and `docs/pull_request_template.md` that exists.
- What the repo's `CLAUDE.md` says about PR titles, descriptions and commits.

The prose style: use your own first. That is a skill named `prose-style` from your user skills or a plugin, such as `de-prose:prose-style`. Load it. Only where you have none, follow the repo's `.claude/skills/prose-style`. Where neither exists, write short, plain sentences.

Write `{run}/pr.md`:

- The first line is the title, with no `#`. It starts with the ticket key, then the format the repo asks for. With no rule in the repo, write the key, a space and the title.
- Then a blank line, then the description. Keep every `##` heading of the template, in its order, and fill each one as its comment asks. Drop the template's comments. Keep its checklists.
- Keep any `## Release steps` and `## After merge` sections from `{run}/implement.md` word for word. Workshop reads them from the PR.
- Use only facts from the files above. Where the template asks for something they don't hold, say so under its heading.

Then write `{out}`: one line on what you drew on, then a last line that is exactly `VERDICT: READY`.
