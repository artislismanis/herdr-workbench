#!/usr/bin/env bats
# win-bridge's WSL side, with a real server on a spare port and Python as the Windows listener.

setup() {
  load helpers
  export HOME="$BATS_TEST_TMPDIR/home" WIN_BRIDGE_PORT=$((47200 + RANDOM % 500))
  mkdir -p "$HOME/.config/win-bridge"
  echo sekrit >"$HOME/.config/win-bridge/token"
  echo "'github.com'" >"$HOME/.config/win-bridge/hosts"
  "$REPO_ROOT/bin/win-bridge" serve &
  SERVER=$!
  for _ in $(seq 50); do get /nope 2>/dev/null && break; sleep 0.1; done
}

teardown() {
  kill "$SERVER" 2>/dev/null || true
}

# Prints the status, then the body.
get() {
  python3 - "$1" "${2:-sekrit}" <<'PY'
import sys, urllib.request, urllib.error
req = urllib.request.Request(f"http://127.0.0.1:{__import__('os').environ['WIN_BRIDGE_PORT']}{sys.argv[1]}", headers={"X-Token": sys.argv[2]})
try:
    with urllib.request.urlopen(req, timeout=40) as r:
        print(r.status); print(r.read().decode())
except urllib.error.HTTPError as e:
    print(e.code)
PY
}

listen() {
  mkdir -p "$HOME/.local/state/win-bridge"
  touch "$HOME/.local/state/win-bridge/seen"
}

@test "with nobody listening a link isn't taken, so open-link can copy it instead" {
  run "$REPO_ROOT/bin/win-bridge" open https://github.com/a/b/pull/1
  [ "$status" -eq 1 ]
  run "$REPO_ROOT/bin/win-bridge" status
  [ "$status" -eq 1 ]
}

@test "a listening Windows gets the link once" {
  listen
  run "$REPO_ROOT/bin/win-bridge" open https://github.com/a/b/pull/1
  [ "$status" -eq 0 ]
  run get /next
  [ "${lines[0]}" = 200 ]
  [[ "${lines[1]}" == *'"kind": "open"'*'"url": "https://github.com/a/b/pull/1"'* ]]
  [ -z "$(ls "$HOME/.local/state/win-bridge/queue")" ]
}

@test "only https links are taken" {
  listen
  run "$REPO_ROOT/bin/win-bridge" open file:///etc/passwd
  [ "$status" -eq 2 ]
}

@test "the wrong token gets nothing" {
  listen
  "$REPO_ROOT/bin/win-bridge" open https://github.com/a
  run get /next wrong
  [ "${lines[0]}" = 403 ]
  [ -n "$(ls "$HOME/.local/state/win-bridge/queue")" ]
}

@test "a stale link isn't opened" {
  listen
  mkdir -p "$HOME/.local/state/win-bridge/queue"
  echo '{"kind": "open", "url": "https://github.com/a", "at": 1}' >"$HOME/.local/state/win-bridge/queue/1.json"
  "$REPO_ROOT/bin/win-bridge" toast "Workshop: x ready" "see https://github.com/a/b/pull/2 now"
  run get /next
  [[ "${lines[1]}" == *'"kind": "toast"'*'"url": "https://github.com/a/b/pull/2"'* ]]
}

@test "the listener comes filled in, and only with the token" {
  run get /listener.ps1
  [ "${lines[0]}" = 200 ]
  [[ "$output" == *"\$Port = $WIN_BRIDGE_PORT"* ]]
  [[ "$output" == *"\$Token = 'sekrit'"* ]]
  [[ "$output" == *"\$Hosts = @('github.com')"* ]]
  run get /listener.ps1 wrong
  [ "${lines[0]}" = 403 ]
}

# A herdr that lists two workspaces and records what it's asked to focus.
stub_herdr() {
  mkdir -p "$HOME/.local/bin"
  cat >"$HOME/.local/bin/herdr" <<'SH'
#!/usr/bin/env bash
case "$1 $2" in
  "workspace list") echo '{"result": {"workspaces": [{"workspace_id": "w1", "label": "workshop"}, {"workspace_id": "w2", "label": "pr-7"}, {"workspace_id": "w3", "label": "50"}, {"workspace_id": "w4", "label": "50"}]}}' ;;
  "workspace focus") echo "$3" >"$HOME/focused" ;;
esac
SH
  chmod +x "$HOME/.local/bin/herdr"
}

focus_after() {
  listen
  "$REPO_ROOT/bin/win-bridge" toast "$@"
  run get /next
  id=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("focus", ""))' "${lines[1]}")
  [[ "${lines[1]}" != *'"where"'* ]]
  get "/focus?id=$id" >/dev/null
}

@test "a click on herdr's own toast goes to the workspace it names" {
  stub_herdr
  focus_after "claude finished" "pr-7 · 10"
  [ "$(cat "$HOME/focused")" = w2 ]
}

@test "a click on workshop's toast goes to the run's workspace" {
  stub_herdr
  focus_after "Workshop: pr-7 needs you" "Your turn on review"
  [ "$(cat "$HOME/focused")" = w2 ]
}

@test "a workspace two runs share sends the click to the board instead" {
  stub_herdr
  focus_after "Workshop: 50 ready" "It's open in hunk"
  [ "$(cat "$HOME/focused")" = w1 ]
}

@test "an id the bridge didn't hand out focuses nothing" {
  stub_herdr
  run get "/focus?id=made-up"
  [ "${lines[0]}" = 204 ]
  [ ! -e "$HOME/focused" ]
}
