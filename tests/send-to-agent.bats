#!/usr/bin/env bats
# send-to-agent with a stub herdr: which plugin action runs depends on the focused pane.

load helpers

setup() {
  export HOME="$BATS_TEST_TMPDIR/home"
  export LOG="$BATS_TEST_TMPDIR/log"
  mkdir -p "$HOME/.local/bin"
  cat >"$HOME/.local/bin/herdr" <<'STUB'
#!/bin/bash
case "$1 $2" in
  "pane process-info") printf '{"result":{"process_info":{"foreground_processes":[{"name":"%s"}]}}}\n' "$FOREGROUND" ;;
  "plugin action") echo "$4 $6 as $HERDR_PANE_ID" >>"$LOG" ;;
esac
STUB
  chmod +x "$HOME/.local/bin/herdr"
  export PATH="$HOME/.local/bin:$PATH" HERDR_ACTIVE_PANE_ID=w2:p3
  unset HERDR_PANE_ID HERDR_BIN_PATH
}

@test "in hunk it sends the review comments, as the hunk pane" {
  FOREGROUND=hunk run "$REPO_ROOT/bin/send-to-agent"
  [ "$status" -eq 0 ]
  [ "$(cat "$LOG")" = "send-review jhochenbaum.hunkdiff as w2:p3" ]
}

@test "anywhere else it sends the saved annotations, as that pane" {
  FOREGROUND=claude run "$REPO_ROOT/bin/send-to-agent"
  [ "$status" -eq 0 ]
  [ "$(cat "$LOG")" = "send-archive annotate as w2:p3" ]
}
