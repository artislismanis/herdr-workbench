#!/usr/bin/env bats
# wt-prune with stub herdr and wt over a real git repo and worktree.

load helpers

setup() {
  export HOME="$BATS_TEST_TMPDIR/home"
  REPO="$BATS_TEST_TMPDIR/repo"
  LINKED="$BATS_TEST_TMPDIR/linked"
  KEPT="$BATS_TEST_TMPDIR/kept"
  mkdir -p "$HOME/.local/bin"
  git init -q -b main "$REPO"
  git -C "$REPO" commit -q --allow-empty -m init
  git -C "$REPO" worktree add -q -b feat "$LINKED"
  git -C "$REPO" worktree add -q -b kept "$KEPT"
  export REPO="$(git -C "$REPO" rev-parse --show-toplevel)" LINKED KEPT
  export LOG="$BATS_TEST_TMPDIR/log" PLAN='[{"branch":"feat","reason":"same commit as","target":"main"}]'
  cat >"$HOME/.local/bin/herdr" <<'STUB'
#!/bin/bash
case "$1 $2" in
  "pane get") printf '{"result":{"pane":{"cwd":"%s"}}}\n' "$REPO" ;;
  "worktree list") cat <<JSON
{"result":{"source":{"repo_root":"$REPO"},"worktrees":[
{"branch":"main","is_linked_worktree":false,"open_workspace_id":"w1","path":"$REPO"},
{"branch":"feat","is_linked_worktree":true,"open_workspace_id":"w2","path":"$LINKED"},
{"branch":"kept","is_linked_worktree":true,"open_workspace_id":"w3","path":"$KEPT"}]}}
JSON
  ;;
  "workspace close") echo "close $3" >>"$LOG" ;;
esac
STUB
  cat >"$HOME/.local/bin/wt" <<'STUB'
#!/bin/bash
echo "wt $*" >>"$LOG"
[[ "$*" == *--dry-run* ]] && { printf '%s\n' "$PLAN"; exit 0; }
rm -rf "$LINKED"
STUB
  chmod +x "$HOME/.local/bin/herdr" "$HOME/.local/bin/wt"
  export HERDR_ACTIVE_PANE_ID=w1:p1
}

prune() { run bash -c "printf '$1\n' | '$REPO_ROOT/bin/wt-prune'"; }

@test "shows what's merged, removes it and closes only its workspace" {
  prune y
  [ "$status" -eq 0 ]
  [[ "$output" == *"feat: same commit as main"* ]]
  grep -q -- "--foreground --yes -C $REPO" "$LOG"
  grep -qx "close w2" "$LOG"
  ! grep -q "close w3" "$LOG"
}

@test "a no keeps everything" {
  prune n
  [ "$status" -eq 0 ]
  ! grep -q -- "--foreground" "$LOG"
  [ -d "$LINKED" ]
}

@test "nothing merged says so and removes nothing" {
  PLAN='[]' prune ''
  [[ "$output" == *"Nothing merged"* ]]
  ! grep -q -- "--foreground" "$LOG"
}
