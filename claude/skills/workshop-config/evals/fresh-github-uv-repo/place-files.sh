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
repo orders-api master "uv.lock=# lock" "pyproject.toml=[project]
name = \"orders-api\""
