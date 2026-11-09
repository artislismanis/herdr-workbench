---
type: llm
focus: trace
weight: 1
---

config.toml stays chmod 600: it was edited in place (the scaffold set 600, and Edit keeps the mode) or `chmod 600` was run on it. The eval sandbox's Bash can't write, so a refused chmod still counts
