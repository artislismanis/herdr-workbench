#!/usr/bin/env bats
# install.sh against a throwaway HOME.

load helpers

setup() {
  stub_systemctl
  export HOME="$BATS_TEST_TMPDIR/home"
  mkdir -p "$HOME"
  CONFIG="$HOME/.config/worktrunk/config.toml"
}

install() { run --separate-stderr "$REPO_ROOT/install.sh" "$@"; }

tree_hash() {
  (cd "$HOME" && find . -type f -printf '%p %m\n' -exec sha256sum {} \; | sort | sha256sum)
}

toml() {
  python3 -c 'import sys, tomllib, json; print(json.dumps(tomllib.load(open(sys.argv[1], "rb"))))' "$CONFIG"
}

@test "fresh install writes executable hooks and the config block" {
  install
  [ "$status" -eq 0 ]
  [ -x "$HOME/.config/worktrunk/hooks/worktree-setup" ]
  [ -x "$HOME/.config/worktrunk/hooks/sync-default-branch" ]
  [ "$(head -1 "$CONFIG")" = "# >>> herdr-workbench:worktrunk >>>" ]
  cmp "$REPO_ROOT/claude/rules/workbench.md" "$HOME/.claude/rules/herdr-workbench/workbench.md"
}

@test "the Claude rule follows CLAUDE_CONFIG_DIR" {
  CLAUDE_CONFIG_DIR="$BATS_TEST_TMPDIR/claude" install
  [ "$status" -eq 0 ]
  [ -f "$BATS_TEST_TMPDIR/claude/rules/herdr-workbench/workbench.md" ]
  [ ! -e "$HOME/.claude" ]
}

@test "re-run is a no-op" {
  install
  before="$(tree_hash)"
  install
  [ "$status" -eq 0 ]
  [ "$(tree_hash)" = "$before" ]
  ! echo "$output" | grep -q wrote
}

@test "--dry-run on a fresh HOME writes nothing" {
  install --dry-run
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "would write $CONFIG"
  [ -z "$(find "$HOME" -mindepth 1 -print -quit)" ]
}

@test "--check passes after install and fails on drift" {
  install
  install --check
  [ "$status" -eq 0 ]
  echo '# local edit' >>"$HOME/.config/worktrunk/hooks/worktree-setup"
  install --check
  [ "$status" -ne 0 ]
  echo "$stderr" | grep -q "worktree-setup differs"
}

@test "--check fails before anything is installed" {
  install --check
  [ "$status" -ne 0 ]
}

@test "settings below the block survive a refresh of the block" {
  install
  printf '\n[projects."github.com/acme/app"]\nworktree-path = "/tmp/elsewhere"\n' >>"$CONFIG"
  sed -i 's|^worktree-path = .*|worktree-path = "stale"|' "$CONFIG"
  install --check
  [ "$status" -ne 0 ]
  install
  [ "$status" -eq 0 ]
  grep -qF '[projects."github.com/acme/app"]' "$CONFIG"
  ! grep -q stale "$CONFIG"
  [ "$(grep -c '^# >>> herdr-workbench:worktrunk >>>$' "$CONFIG")" -eq 1 ]
}

@test "an existing config without the block gets it on top" {
  mkdir -p "$(dirname "$CONFIG")"
  printf '[merge]\nsquash = false\n' >"$CONFIG"
  install
  [ "$(head -1 "$CONFIG")" = "# >>> herdr-workbench:worktrunk >>>" ]
  json="$(toml)"
  echo "$json" | python3 -c 'import sys, json; c = json.load(sys.stdin); assert c["merge"] == {"squash": False}, c'
}

@test "the block is valid TOML and our keys stay top-level under a user table" {
  install
  printf '\n[projects."github.com/acme/app"]\nmerge.squash = false\n' >>"$CONFIG"
  toml | python3 -c '
import sys, json
c = json.load(sys.stdin)
assert c["worktree-path"] == "~/.herdr/worktrees/{{ repo }}/{{ branch | sanitize }}", c
assert "sync-default-branch" in c["pre-switch"]
assert "worktree-setup" in c["pre-start"]
assert "rm" in c["aliases"]
assert c["step"]["copy-ignored"]["exclude"] == [".venv/", "node_modules/"]
assert set(c["projects"]["github.com/acme/app"]) == {"merge"}, c
'
}

@test "--rollback prints a recipe and changes nothing" {
  install --rollback
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "rm -f \"$HOME/.config/worktrunk/hooks/worktree-setup\""
  [ -z "$(find "$HOME" -mindepth 1 -print -quit)" ]
}

@test "the rollback recipe removes the block and keeps the rest" {
  install
  printf '\n[merge]\nsquash = false\n' >>"$CONFIG"
  "$REPO_ROOT/install.sh" --rollback | bash
  [ ! -e "$HOME/.config/worktrunk/hooks" ]
  ! grep -q herdr-workbench "$CONFIG"
  grep -qx 'squash = false' "$CONFIG"
}
