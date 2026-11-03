#!/usr/bin/env bats
# open-link and notify-send, with a stub wl-copy and win-bridge.

setup() {
  load helpers
  export HOME="$BATS_TEST_TMPDIR/home"
  STUB="$BATS_TEST_TMPDIR/stub"
  mkdir -p "$STUB"
  export PATH="$STUB:$PATH" COPIED="$BATS_TEST_TMPDIR/copied"
  printf '#!/usr/bin/env bash\ncat >"$COPIED"\n' >"$STUB/wl-copy"
  chmod +x "$STUB/wl-copy"
}

@test "a link lands on the clipboard and says so" {
  run "$REPO_ROOT/bin/open-link" https://example.com/a
  [ "$status" -eq 0 ]
  [ "$(cat "$COPIED")" = https://example.com/a ]
  [[ "$output" == *"paste it in your browser: https://example.com/a"* ]]
}

@test "no link is a usage error" {
  run "$REPO_ROOT/bin/open-link"
  [ "$status" -eq 2 ]
}

@test "a link goes to Windows when win-bridge takes it" {
  BIN="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$BIN"
  cp "$REPO_ROOT/bin/open-link" "$BIN/"
  printf '#!/usr/bin/env bash\necho "$@" >"$COPIED.bridge"\n' >"$BIN/win-bridge"
  chmod +x "$BIN/win-bridge"
  ln -s "$BIN/open-link" "$STUB/xdg-open"
  run "$STUB/xdg-open" https://example.com/a
  [ "$status" -eq 0 ]
  [ "$(cat "$COPIED.bridge")" = "open https://example.com/a" ]
  [ ! -e "$COPIED" ]
  [[ "$output" == *"Opened in your Windows browser: https://example.com/a"* ]]
}

@test "notify-send passes herdr's title and body to win-bridge, options and all" {
  BIN="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$BIN"
  cp "$REPO_ROOT/bin/notify-send" "$BIN/"
  printf '#!/usr/bin/env bash\nprintf "%%s|" "$@" >"$COPIED.bridge"\n' >"$BIN/win-bridge"
  chmod +x "$BIN/win-bridge"
  ln -s "$BIN/notify-send" "$STUB/notify-send"
  run "$STUB/notify-send" --app-name Herdr -u normal -- "Agent done" "pr-7 finished"
  [ "$status" -eq 0 ]
  [ "$(cat "$COPIED.bridge")" = "toast|Agent done|pr-7 finished|" ]
}

@test "notify-send without win-bridge listening still succeeds" {
  BIN="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$BIN"
  cp "$REPO_ROOT/bin/notify-send" "$BIN/"
  printf '#!/usr/bin/env bash\nexit 1\n' >"$BIN/win-bridge"
  chmod +x "$BIN/win-bridge"
  run "$BIN/notify-send" "Only a title"
  [ "$status" -eq 0 ]
}
