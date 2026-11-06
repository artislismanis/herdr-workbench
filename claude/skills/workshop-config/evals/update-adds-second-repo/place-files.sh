#!/usr/bin/env bash
set -eu
# Builds the repos and config this case starts from, in the current directory.
repo() { # name default-branch then files as name=content
  local name=$1 branch=$2; shift 2
  git init -q --bare -b "$branch" "remotes/$name.git"
  git init -q -b "$branch" "repos/$name"
  for f in "$@"; do printf "%s\n" "${f#*=}" > "repos/$name/${f%%=*}"; done
  git -C "repos/$name" -c user.name=t -c user.email=t@x add -A
  git -C "repos/$name" -c user.name=t -c user.email=t@x commit -qm init
  git -C "repos/$name" remote add origin "$PWD/remotes/$name.git"
  git -C "repos/$name" push -q origin "$branch"
  git -C "repos/$name" remote set-head origin "$branch"
}
mkdir -p config
repo orders-api master "uv.lock=# lock"
repo web-portal main "package.json={\"scripts\": {\"test\": \"vitest run\"}}"
cat > config/config.toml <<EOF
# Never commit this file.

max_active = 3   # this box has 32GB, two was too few

[repos.orders-api]
path = "$PWD/repos/orders-api"
source = "github"
label = "ai"
base = "master"

[repos.orders-api.commands]
qa = "uv run pytest -x"   # -x: stop at the first failure, the suite is slow
EOF
chmod 600 config/config.toml
