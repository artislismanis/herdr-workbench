---
tags: [skill-creator]
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

first time setting up workshop. i've got one repo, orders-api, its in repos/orders-api. can you sort out the config for me

(Test harness, not part of the request: no person is here to answer. Wherever you would ask a question, append it to `transcript.md` in the working directory as `Q<n>: <question>` with the options you offered, answer it yourself from the facts below as the user would, and append `A<n>: <answer>`. Facts the user did not give are unknown: pick what you would recommend. The config file is `./config/config.toml` and every `workshop` command must run with `WORKSHOP_CONFIG=$PWD/config/config.toml`. The repos are under `./repos/`. Never touch `~/.config/workshop`.)

Facts the user knows:
- Tickets are GitHub issues on that repo. The label "ai" is fine.
- They trust the repo and its tests.
- The team assigns reviewers through CODEOWNERS. All their repos are in the GitHub org "acme".
- They do not want chat, sweeps or any other extras for now.
