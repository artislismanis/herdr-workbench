---
tags: [skill-creator]
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

can you add web-portal (repos/web-portal) to my workshop config? same as orders-api basically

(Test harness, not part of the request: no person is here to answer. Wherever you would ask a question, append it to `transcript.md` in the working directory as `Q<n>: <question>` with the options you offered, answer it yourself from the facts below as the user would, and append `A<n>: <answer>`. Facts the user did not give are unknown: pick what you would recommend. The config file is `./config/config.toml` and every `workshop` command must run with `WORKSHOP_CONFIG=$PWD/config/config.toml`. The repos are under `./repos/`. Never touch `~/.config/workshop`. Bash can't write files in this sandbox, so write and edit them with the Write and Edit tools; a refused `chmod` or `workshop doctor` is expected.)

Facts the user knows:
- web-portal tickets are GitHub issues, label "ai".
- They trust the repo.
- Nothing else should change.
