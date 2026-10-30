#!/usr/bin/env bats
# wt-remove-current with stub herdr and wt over a real git repo and worktree.

load helpers

setup() {
  export HOME="$BATS_TEST_TMPDIR/home"
  REPO="$BATS_TEST_TMPDIR/repo"
  LINKED="$BATS_TEST_TMPDIR/linked"
  mkdir -p "$HOME/.local/bin"
  git init -q -b main "$REPO"
  git -C "$REPO" commit -q --allow-empty -m init
  git -C "$REPO" worktree add -q -b feat "$LINKED"
  REPO="$(git -C "$REPO" rev-parse --show-toplevel)"
  LINKED="$(git -C "$LINKED" rev-parse --show-toplevel)"
  export REPO LINKED
  export LOG="$BATS_TEST_TMPDIR/log"
  export PANE_CWD="$LINKED/sub" WT_RC=0
  mkdir -p "$PANE_CWD"
  cat >"$HOME/.local/bin/herdr" <<'STUB'
#!/bin/bash
case "$1 $2" in
  "pane get") printf '{"result":{"pane":{"cwd":"%s"}}}\n' "$PANE_CWD" ;;
  "worktree list") cat <<JSON
{"result":{"source":{"repo_root":"$REPO"},"worktrees":[
{"branch":"main","is_linked_worktree":false,"open_workspace_id":"w1","path":"$REPO"},
{"branch":"feat","is_linked_worktree":true,"open_workspace_id":"w2","path":"$LINKED"}]}}
JSON
  ;;
  "workspace close") echo "close $3" >>"$LOG" ;;
esac
STUB
  cat >"$HOME/.local/bin/wt" <<'STUB'
#!/bin/bash
echo "wt $*" >>"$LOG"
exit "$WT_RC"
STUB
  chmod +x "$HOME/.local/bin/herdr" "$HOME/.local/bin/wt"
  export HERDR_ACTIVE_PANE_ID=w2:p1
}

remove() { run bash -c "printf '$1\n' | '$REPO_ROOT/bin/wt-remove-current'"; }

@test "from a subfolder: removes that worktree, then closes its workspace" {
  remove y
  [ "$status" -eq 0 ]
  [ "$(cat "$LOG")" = "wt remove --foreground -C $REPO $LINKED
close w2" ]
}

@test "enter means yes" {
  remove ''
  [ "$status" -eq 0 ]
  grep -q "^wt remove" "$LOG"
}

@test "n removes nothing" {
  remove n
  [ "$status" -eq 0 ]
  [ ! -e "$LOG" ]
}

@test "the main checkout is refused" {
  PANE_CWD="$REPO" remove y
  [ "$status" -eq 1 ]
  [[ "$output" == *"main checkout"* ]]
  [ ! -e "$LOG" ]
}

@test "outside a repo is refused" {
  PANE_CWD="$BATS_TEST_TMPDIR" remove y
  [ "$status" -eq 1 ]
  [[ "$output" == *"Not in a git repository"* ]]
}

@test "a failed wt remove keeps the workspace" {
  WT_RC=1 remove y
  [ "$status" -eq 1 ]
  ! grep -q "^close" "$LOG"
}
