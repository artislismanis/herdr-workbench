#!/usr/bin/env bats
# pane-at-end with a stub herdr: the agent at left, a pane already open at right.

load helpers

setup() {
  export STUB="$BATS_TEST_TMPDIR/stub"
  mkdir -p "$STUB/bin"
  cat >"$STUB/bin/herdr" <<'HERDR'
#!/bin/sh
echo "$*" >>"$STUB/herdr.log"
[ "$1 $2" = "pane layout" ] && cat "$STUB/layout.json"
exit 0
HERDR
  chmod +x "$STUB/bin/herdr"
  export PATH="$STUB/bin:$PATH"
}

layout() {
  jq -n --arg focused "$1" '{result: {layout: {tab_id: "w1:t1", focused_pane_id: $focused, panes: [
    {pane_id: "w1:p1", rect: {x: 0, y: 0, width: 45, height: 50}},
    {pane_id: "w1:p9", rect: {x: 45, y: 0, width: 44, height: 50}},
    {pane_id: "w1:p2", rect: {x: 89, y: 0, width: 88, height: 50}}]}}}' >"$STUB/layout.json"
}

@test "--last names the pane at the right end of the row" {
  layout w1:p1
  run "$REPO_ROOT/bin/pane-at-end" --last w1:p1
  [ "$status" -eq 0 ]
  [ "$output" = w1:p2 ]
}

@test "a new pane moves out and back in after the last one, keeping focus" {
  layout w1:p9
  run "$REPO_ROOT/bin/pane-at-end" w1:p9
  [ "$status" -eq 0 ]
  grep -qx 'pane move w1:p9 --new-tab --no-focus' "$STUB/herdr.log"
  grep -qx 'pane move w1:p9 --tab w1:t1 --split right --target-pane w1:p2 --focus' "$STUB/herdr.log"
}

@test "a pane already at the end stays put" {
  layout w1:p1
  run "$REPO_ROOT/bin/pane-at-end" w1:p2
  [ "$status" -eq 0 ]
  ! grep -q 'pane move' "$STUB/herdr.log"
}
