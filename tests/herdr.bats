#!/usr/bin/env bats
# herdr's part of install.sh, with a stub herdr on PATH. The stub's config
# check fails on a duplicate [update] table, as the real one does.

load helpers

setup() {
  export HOME="$BATS_TEST_TMPDIR/home"
  mkdir -p "$HOME" "$BATS_TEST_TMPDIR/bin"
  HCONFIG="$HOME/.config/herdr/config.toml"
  cat >"$BATS_TEST_TMPDIR/bin/herdr" <<'STUB'
#!/bin/sh
case "$1 ${2:-}" in
  "--skill ") printf -- '---\nname: herdr\n---\nstub skill\n' ;;
  "config check")
    [ "$(grep -c '^\[update\]' "$HERDR_CONFIG_PATH" 2>/dev/null)" -le 1 ] || { echo "duplicate key update" >&2; exit 1; } ;;
  "integration status")
    if [ -f "$HOME/.claude-integration" ]; then echo "claude: current (v11) (x)"; else echo "claude: not installed (x)"; fi ;;
  "integration install") touch "$HOME/.claude-integration" ;;
  "integration uninstall") rm -f "$HOME/.claude-integration" ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/herdr"
  export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
}

install() { run --separate-stderr "$REPO_ROOT/install.sh" "$@"; }

@test "fresh install writes the config block, the skill and the integration" {
  install
  [ "$status" -eq 0 ]
  [ "$(tail -1 "$HCONFIG")" = "# <<< herdr-workbench:herdr <<<" ]
  grep -qx 'version_check = false' "$HCONFIG"
  grep -qx 'stub skill' "$HOME/.claude/skills/herdr/SKILL.md"
  [ -f "$HOME/.claude-integration" ]
}

@test "your settings stay above the block and survive a refresh" {
  mkdir -p "$(dirname "$HCONFIG")"
  printf '[keys]\nprefix = "ctrl+a"\n' >"$HCONFIG"
  install
  [ "$status" -eq 0 ]
  [ "$(head -1 "$HCONFIG")" = "[keys]" ]
  install
  [ "$(grep -c '^# >>> herdr-workbench:herdr >>>$' "$HCONFIG")" -eq 1 ]
  ! echo "$output" | grep -q wrote
}

@test "a config that would be invalid is refused and left untouched" {
  mkdir -p "$(dirname "$HCONFIG")"
  printf '[update]\nchannel = "stable"\n' >"$HCONFIG"
  before="$(cat "$HCONFIG")"
  install
  [ "$status" -ne 0 ]
  echo "$stderr" | grep -q "would be invalid"
  [ "$(cat "$HCONFIG")" = "$before" ]
}

@test "--check fails when the integration is missing" {
  install
  rm "$HOME/.claude-integration"
  install --check
  [ "$status" -ne 0 ]
  echo "$stderr" | grep -q "Claude integration"
}

@test "--dry-run runs no integration install" {
  install --dry-run
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "would run herdr integration install claude"
  [ ! -e "$HOME/.claude-integration" ]
}

@test "without herdr the rest still installs" {
  PATH="$(echo "$PATH" | sed "s|$BATS_TEST_TMPDIR/bin:||")" install
  [ "$status" -eq 0 ]
  echo "$stderr" | grep -q "herdr not installed"
  [ -f "$HOME/.claude/rules/herdr-workbench/workbench.md" ]
  [ ! -e "$HCONFIG" ]
}

@test "the rollback recipe removes the block, the skill and the integration" {
  mkdir -p "$(dirname "$HCONFIG")"
  printf '[keys]\nprefix = "ctrl+a"\n' >"$HCONFIG"
  install
  "$REPO_ROOT/install.sh" --rollback | bash
  ! grep -q herdr-workbench "$HCONFIG"
  grep -qx 'prefix = "ctrl+a"' "$HCONFIG"
  [ ! -e "$HOME/.claude/skills/herdr" ]
  [ ! -e "$HOME/.claude-integration" ]
}
