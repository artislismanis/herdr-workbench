#!/usr/bin/env bats
# .github/bump-plugins.sh against a stubbed gh.

load helpers

setup() {
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  cat >"$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/sh
case "$2" in
  repos/acme/moved/commits/HEAD) echo bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb ;;
  repos/acme/same/commits/HEAD) echo cccccccccccccccccccccccccccccccccccccccc ;;
  *) exit 1 ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"
  export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
  PINS="$BATS_TEST_TMPDIR/pins"
  cat >"$PINS" <<'PINS'
# a comment
MOVED_PLUGIN=acme/moved
MOVED_PLUGIN_COMMIT=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
SAME_PLUGIN=acme/same
SAME_PLUGIN_COMMIT=cccccccccccccccccccccccccccccccccccccccc
PINS
}

@test "moves only the plugin whose upstream moved, with its compare link" {
  run "$REPO_ROOT/.github/bump-plugins.sh" "$PINS"
  [ "$status" -eq 0 ]
  [ "$output" = "acme/moved: aaaaaaaaaaaa -> bbbbbbbbbbbb https://github.com/acme/moved/compare/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa...bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" ]
  grep -qx 'MOVED_PLUGIN_COMMIT=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' "$PINS"
  grep -qx 'SAME_PLUGIN_COMMIT=cccccccccccccccccccccccccccccccccccccccc' "$PINS"
  grep -qx '# a comment' "$PINS"
}

@test "a plugin without its commit on the next line stops it" {
  sed -i '/^MOVED_PLUGIN_COMMIT/d' "$PINS"
  run "$REPO_ROOT/.github/bump-plugins.sh" "$PINS"
  [ "$status" -ne 0 ]
  [[ "$output" == *"expected MOVED_PLUGIN_COMMIT="* ]]
}

@test "the real pins file parses" {
  cp "$REPO_ROOT/pins" "$PINS"
  cat >"$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/sh
echo 0000000000000000000000000000000000000000
STUB
  run "$REPO_ROOT/.github/bump-plugins.sh" "$PINS"
  [ "$status" -eq 0 ]
  [ "$(grep -c '_PLUGIN_COMMIT=0000000000000000000000000000000000000000$' "$PINS")" -eq "$(grep -c '_PLUGIN=' "$REPO_ROOT/pins")" ]
}
