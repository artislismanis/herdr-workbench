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
