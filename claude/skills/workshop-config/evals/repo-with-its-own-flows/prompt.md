---
tags: [skill-creator]
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

set up workshop for my repo ledger (repos/ledger). the repo has its own agent setup, so check how that fits

(Test harness, not part of the request: no person is here to answer. Wherever you would ask a question, append it to `transcript.md` in the working directory as `Q<n>: <question>` with the options you offered, answer it yourself from the facts below as the user would, and append `A<n>: <answer>`. Facts the user did not give are unknown: pick what you would recommend. The config file is `./config/config.toml` and every `workshop` command must run with `WORKSHOP_CONFIG=$PWD/config/config.toml`. The repos are under `./repos/`, and `WORKSHOP_REPOS` is `$PWD/config/repos`. Never touch `~/.config/workshop`. Bash can't write files in this sandbox, so write and edit them with the Write and Edit tools; a refused `chmod` or `workshop doctor` is expected.)

Facts the user knows:
- Tickets are GitHub issues on that repo. The label "ai" is fine. All their repos are in the GitHub org "acme".
- They trust the repo and its tests.
- They want to keep git-spice for stacks, as the repo says.
- Reviews should go workshop's way, not the repo's pr-review skill. The ticket-to-pr plugin must not run under workshop.
- No chat, sweeps or other extras.
