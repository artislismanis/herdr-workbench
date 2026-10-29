#!/usr/bin/env bats
# worktrunk pre-switch hook (worktrunk/hooks/sync-default-branch) against real
# git repos: a bare "origin", the primary clone, and a second clone that
# pushes new commits.

load helpers

HOOK="$REPO_ROOT/worktrunk/hooks/sync-default-branch"

setup() {
  ORIGIN="$BATS_TEST_TMPDIR/origin.git"
  PRIMARY="$BATS_TEST_TMPDIR/primary"
  OTHER="$BATS_TEST_TMPDIR/other"
  git init -q --bare -b main "$ORIGIN"
  git clone -q "$ORIGIN" "$PRIMARY" 2>/dev/null
  git -C "$PRIMARY" symbolic-ref HEAD refs/heads/main
  echo one >"$PRIMARY/file"
  git -C "$PRIMARY" add file
  git -C "$PRIMARY" commit -q -m one
  git -C "$PRIMARY" push -q -u origin main
  git clone -q "$ORIGIN" "$OTHER"
  echo two >"$OTHER/file"
  git -C "$OTHER" commit -q -am two
  git -C "$OTHER" push -q origin main
  UPSTREAM="$(git -C "$OTHER" rev-parse HEAD)"
}

sync() { run --separate-stderr bash "$HOOK" "$PRIMARY" main "$@"; }
main_at() { [ "$(git -C "$PRIMARY" rev-parse refs/heads/main)" = "$1" ]; }

@test "fast-forwards main checked out in the primary worktree" {
  sync origin
  [ "$status" -eq 0 ]
  main_at "$UPSTREAM"
  [ "$(cat "$PRIMARY/file")" = two ]
}

@test "fast-forwards main when no worktree has it checked out" {
  git -C "$PRIMARY" switch -q -c feature
  sync origin
  [ "$status" -eq 0 ]
  main_at "$UPSTREAM"
}

@test "leaves main alone when another worktree has it checked out" {
  git -C "$PRIMARY" switch -q -c feature
  git -C "$PRIMARY" worktree add -q "$BATS_TEST_TMPDIR/main-wt" main
  before="$(git -C "$PRIMARY" rev-parse main)"
  sync origin
  [ "$status" -eq 0 ]
  main_at "$before"
  echo "$stderr" | grep -q "checked out in another worktree"
}

@test "leaves a diverged main alone and exits 0" {
  echo local >"$PRIMARY/other-file"
  git -C "$PRIMARY" add other-file
  git -C "$PRIMARY" commit -q -m local
  before="$(git -C "$PRIMARY" rev-parse main)"
  sync origin
  [ "$status" -eq 0 ]
  main_at "$before"
  echo "$stderr" | grep -q diverged
}

@test "local changes in the way warn and exit 0" {
  echo dirty >"$PRIMARY/file"
  before="$(git -C "$PRIMARY" rev-parse main)"
  sync origin
  [ "$status" -eq 0 ]
  main_at "$before"
  echo "$stderr" | grep -q "couldn't fast-forward"
}

@test "an unreachable remote warns and exits 0" {
  git -C "$PRIMARY" remote set-url origin "$BATS_TEST_TMPDIR/missing.git"
  sync origin
  [ "$status" -eq 0 ]
  echo "$stderr" | grep -q "fetch from origin failed"
}

@test "a repo without a remote is left alone" {
  before="$(git -C "$PRIMARY" rev-parse main)"
  sync ""
  [ "$status" -eq 0 ]
  main_at "$before"
}
