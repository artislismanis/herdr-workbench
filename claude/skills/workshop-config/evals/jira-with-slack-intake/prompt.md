---
tags: [skill-creator]
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

setting up workshop at work. repo is billing-svc (repos/billing-svc), tickets live in Jira at acme.atlassian.net and review asks come in through Slack, not GitHub. help me get the config right

(Test harness, not part of the request: no person is here to answer. Wherever you would ask a question, append it to `transcript.md` in the working directory as `Q<n>: <question>` with the options you offered, answer it yourself from the facts below as the user would, and append `A<n>: <answer>`. Facts the user did not give are unknown: pick what you would recommend. The config file is `./config/config.toml` and every `workshop` command must run with `WORKSHOP_CONFIG=$PWD/config/config.toml`. The repos are under `./repos/`. Never touch `~/.config/workshop`. Bash can't write files in this sandbox, so write and edit them with the Write and Edit tools; a refused `chmod` or `workshop doctor` is expected.)

Facts the user knows:
- Their Atlassian email is sam@acme.example. They have a Jira API token and will paste it into the file themselves.
- Jira tickets for this repo carry the labels "ai" and "billing-svc". Skip tickets in "Backlog".
- Jira statuses: Backlog, To Do, In Progress, Blocked, In Review, Done. Any move is allowed.
- Review asks are posted in the Slack channel #eng-reviews. Slack is connected to Claude. They want an eyes reaction when they take a review.
- The eval has no Slack server, so this stands in for the session's tool list. The claude.ai Slack connector's tools are `mcp__claude_ai_Slack__slack_search_public`, `mcp__claude_ai_Slack__slack_search_channels`, `mcp__claude_ai_Slack__slack_read_channel`, `mcp__claude_ai_Slack__slack_read_thread`, `mcp__claude_ai_Slack__slack_add_reaction` and `mcp__claude_ai_Slack__slack_send_message`.
- The GitHub org is "acme". They trust the repo; tests run with npm test.
