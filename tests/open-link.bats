#!/usr/bin/env bats
# open-link with a stub wl-copy.

setup() {
  load helpers
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
