#!/usr/bin/env bash
set -eu
# Builds the repos and config this case starts from, in the current directory.
repo() { # name default-branch then files as name=content
  local name=$1 branch=$2; shift 2
  git init -q --bare -b "$branch" "remotes/$name.git"
  git init -q -b "$branch" "repos/$name"
  for f in "$@"; do mkdir -p "$(dirname "repos/$name/${f%%=*}")"; printf "%s\n" "${f#*=}" > "repos/$name/${f%%=*}"; done
  git -C "repos/$name" -c user.name=t -c user.email=t@x add -A
  git -C "repos/$name" -c user.name=t -c user.email=t@x commit -qm init
  git -C "repos/$name" remote add origin "$PWD/remotes/$name.git"
  git -C "repos/$name" push -q origin "$branch"
  git -C "repos/$name" remote set-head origin "$branch"
}
mkdir -p config config/repos
repo ledger main "uv.lock=# lock" "pyproject.toml=[project]
name = \"ledger\"" "CLAUDE.md=# ledger

- Stack dependent branches with git-spice, through \`gs\`, never raw git.
- Review PRs with the pr-review skill, which posts a draft GitHub review." ".claude/skills/pr-review/SKILL.md=---
name: pr-review
description: Review a PR and post a draft GitHub review
---
Review the PR, then post a draft review with gh." ".claude/settings.json={\"enabledPlugins\": {\"ticket-to-pr@team-skills\": true}}"
