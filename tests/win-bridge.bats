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
  run get "/focus?id=$id"
}

@test "a click on herdr's own toast goes to the workspace it names" {
  stub_herdr
  focus_after "claude finished" "pr-7 · 10"
  [ "$(cat "$HOME/focused")" = w2 ]
  [ "${lines[1]}" = "$(hostname): pr-7" ]
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
  [ "${lines[1]}" = "$(hostname): workshop" ]
}

@test "an id the bridge didn't hand out focuses nothing" {
  stub_herdr
  run get "/focus?id=made-up"
  [ "${lines[0]}" = 200 ]
  [ -z "${lines[1]:-}" ]
  [ ! -e "$HOME/focused" ]
}

@test "the click handler comes filled in, and only with the token" {
  run get /go.ps1
  [ "${lines[0]}" = 200 ]
  [[ "$output" == *"127.0.0.1:$WIN_BRIDGE_PORT/focus"* ]]
  [[ "$output" == *"'X-Token' = 'sekrit'"* ]]
  run get /go.ps1 wrong
  [ "${lines[0]}" = 403 ]
}

@test "herdr's own window title is what the click looks for" {
  stub_herdr
  mkdir -p "$HOME/.config/herdr"
  printf '[ui]\nwindow_title = "herdr {workspace}"\n' >"$HOME/.config/herdr/config.toml"
  focus_after "claude finished" "pr-7 · 10"
  [ "${lines[1]}" = "herdr pr-7" ]
}

@test "a tab named herdr, then the distro's name, are the next guesses at herdr's tab" {
  stub_herdr
  echo TestEnv >"$HOME/.config/win-bridge/distro"
  focus_after "claude finished" "pr-7 · 10"
  [ "${lines[1]}" = "$(hostname): pr-7" ]
  [ "${lines[2]}" = herdr ]
  [ "${lines[3]}" = TestEnv ]
}

@test "setup prints the listener task and, in a distro, the keep-alive task for it" {
  stub_systemctl
  run env WSL_DISTRO_NAME=TestEnv "$REPO_ROOT/bin/win-bridge" setup github.com
  [ "$status" -eq 0 ]
  [[ "$output" == *"Register-ScheduledTask -TaskName win-bridge"* ]]
  [[ "$output" == *"--headless wsl.exe -d TestEnv --exec sleep infinity"* ]]
  [[ "$output" == *"-TaskName WSL-KeepAlive-TestEnv"* ]]
  [[ "$output" != *"@"[A-Z]*"@"* ]]
  [ "$(grep -c -- '-User $env:USERNAME)' <<<"$output")" -eq 2 ]
  [ "$(cat "$HOME/.config/win-bridge/distro")" = TestEnv ]
  grep -qx "Environment=WIN_BRIDGE_PORT=$WIN_BRIDGE_PORT" "$HOME/.config/systemd/user/win-bridge.service"
}

@test "setup's steps are numbered, and say which run in PowerShell and which in WSL" {
  stub_systemctl
  run env WSL_DISTRO_NAME=TestEnv "$REPO_ROOT/bin/win-bridge" setup github.com
  [ "$status" -eq 0 ]
  [[ "$output" == *"1 of 3 · In Windows PowerShell: links and toasts"* ]]
  [[ "$output" == *"2 of 3 · In Windows PowerShell: keep TestEnv running"* ]]
  [[ "$output" == *"3 of 3 · In WSL: herdr's notifications"* ]]
}

@test "setup refuses a WIN_BRIDGE_PORT another distro's win-bridge holds" {
  stub_systemctl
  # All distros share one network: a second server there answers with its own token.
  other="$BATS_TEST_TMPDIR/other" port=$((WIN_BRIDGE_PORT + 1))
  mkdir -p "$other/.config/win-bridge"
  echo theirs >"$other/.config/win-bridge/token"
  HOME="$other" WIN_BRIDGE_PORT=$port "$REPO_ROOT/bin/win-bridge" serve &
  theirs=$!
  for _ in $(seq 50); do WIN_BRIDGE_PORT=$port get /nope theirs 2>/dev/null | grep -q 404 && break; sleep 0.1; done
  run env WIN_BRIDGE_PORT=$port "$REPO_ROOT/bin/win-bridge" setup github.com
  kill "$theirs" 2>/dev/null || true
  [ "$status" -eq 1 ]
  [[ "$output" == *"Port $port is taken by another distro's win-bridge"* ]]
  [[ "$output" != *"Register-ScheduledTask"* ]]
}

unit_port() { sed -n 's/^Environment=WIN_BRIDGE_PORT=//p' "$HOME/.config/systemd/user/win-bridge.service"; }

@test "each distro gets its own port, folder, task and click link, and keeps the port on a re-run" {
  stub_systemctl
  run env -u WIN_BRIDGE_PORT WSL_DISTRO_NAME=TestEnv "$REPO_ROOT/bin/win-bridge" setup github.com
  [ "$status" -eq 0 ]
  port="$(unit_port)"
  [ "$port" -ge 47200 ] && [ "$port" -lt 48000 ]
  [[ "$output" == *'$env:LOCALAPPDATA\win-bridge\TestEnv'* ]]
  [[ "$output" == *"-TaskName win-bridge-TestEnv"* ]]
  [[ "$output" == *"http://127.0.0.1:$port/listener.ps1"* ]]
  [[ "$output" == *"Classes\win-bridge-$port"* ]]
  run env -u WIN_BRIDGE_PORT WSL_DISTRO_NAME=TestEnv "$REPO_ROOT/bin/win-bridge" setup github.com
  [ "$(unit_port)" = "$port" ]
}

@test "a re-run moves off a port another distro has taken since" {
  stub_systemctl
  other="$BATS_TEST_TMPDIR/other" port=$((WIN_BRIDGE_PORT + 2))
  mkdir -p "$other/.config/win-bridge" "$HOME/.config/systemd/user"
  echo theirs >"$other/.config/win-bridge/token"
  HOME="$other" WIN_BRIDGE_PORT=$port "$REPO_ROOT/bin/win-bridge" serve &
  theirs=$!
  for _ in $(seq 50); do WIN_BRIDGE_PORT=$port get /nope theirs 2>/dev/null | grep -q 404 && break; sleep 0.1; done
  echo "Environment=WIN_BRIDGE_PORT=$port" >"$HOME/.config/systemd/user/win-bridge.service"
  run env -u WIN_BRIDGE_PORT WSL_DISTRO_NAME=TestEnv "$REPO_ROOT/bin/win-bridge" setup github.com
  kill "$theirs" 2>/dev/null || true
  [ "$status" -eq 0 ]
  [ "$(unit_port)" != "$port" ]
}

@test "the listener's click link is per port, so clicks go back to their own distro" {
  run get /listener.ps1
  [[ "$output" == *"Classes\win-bridge-\$Port"* ]]
  [[ "$output" == *'win-bridge-${Port}:go?id='* ]]
}
