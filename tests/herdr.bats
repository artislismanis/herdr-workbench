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
    # More agents follow claude, as in the real output; the pause makes a
    # reader that quits early (grep -q) cost the stub a SIGPIPE every time.
    if [ -f "$HOME/.claude-integration" ]; then echo "claude: current (v11) (x)"; else echo "claude: not installed (x)"; fi
    sleep 0.1; echo "codex: not installed (x)" ;;
  "integration install") touch "$HOME/.claude-integration" ;;
  "integration uninstall") rm -f "$HOME/.claude-integration" ;;
  "plugin list") cat "$HOME/.plugin" 2>/dev/null || echo "No plugins installed." ;;
  "plugin install")
    echo "- enabled [github:$3@$5]" >>"$HOME/.plugin"
    echo "$3 $5" >>"$HOME/.plugin-installs"
    case "$3" in */herdr-annotate)
      [ -f "$HOME/.tui-download-fails" ] || { d="$HOME/.config/herdr/plugins/github-installations/annotate-1/2-0/checkout/bin"; mkdir -p "$d"; touch "$d/plannotator-tui.exe"; }
    esac ;;
  "plugin uninstall") rm -f "$HOME/.plugin" ;;
  "plugin link")
    echo "- workshop (Workshop) enabled [local:$(cd "$3" && pwd -P)]" >>"$HOME/.plugin"
    echo "$3" >>"$HOME/.plugin-links" ;;
esac
STUB
  cat >"$BATS_TEST_TMPDIR/bin/hunk" <<'STUB'
#!/bin/sh
[ "$1 $2 $3" = "skill path hunk-review" ] && echo "$HOME/hunk-skill.md"
STUB
  echo 'stub hunk skill' >"$HOME/hunk-skill.md"
  chmod +x "$BATS_TEST_TMPDIR/bin/herdr" "$BATS_TEST_TMPDIR/bin/hunk"
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

@test "--check is clean after an install" {
  install
  install --check
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "integration up to date"
}

@test "the worktrunk plugin installs at the pin, once" {
  install
  [ "$status" -eq 0 ]
  source "$REPO_ROOT/pins"
  grep -qx "$WORKTRUNK_PLUGIN $WORKTRUNK_PLUGIN_COMMIT" "$HOME/.plugin-installs"
  [ -x "$HOME/.config/herdr/bin/wt-remove-current" ]
  installs="$(wc -l <"$HOME/.plugin-installs")"
  install
  [ "$(wc -l <"$HOME/.plugin-installs")" -eq "$installs" ]
}

@test "the review plugins install at their pins" {
  install
  [ "$status" -eq 0 ]
  source "$REPO_ROOT/pins"
  grep -qx "$HUNKDIFF_PLUGIN $HUNKDIFF_PLUGIN_COMMIT" "$HOME/.plugin-installs"
  grep -qx "$ANNOTATE_PLUGIN $ANNOTATE_PLUGIN_COMMIT" "$HOME/.plugin-installs"
}

@test "hunkdiff points at our hunk by full path" {
  install
  grep -qxF "bin = \"$HOME/.local/bin/hunk\"" "$HOME/.config/herdr/plugins/config/jhochenbaum.hunkdiff/config.toml"
}

@test "the hunk skill comes from the binary, hunk settings stay yours" {
  mkdir -p "$HOME/.config/hunk"
  echo 'theme = "mine"' >"$HOME/.config/hunk/config.toml"
  install
  grep -qx 'stub hunk skill' "$HOME/.claude/skills/hunk-review/SKILL.md"
  grep -qx 'agent_notes = true' "$HOME/.config/hunk/config.toml"
  grep -qx 'theme = "mine"' "$HOME/.config/hunk/config.toml"
}

@test "a failed plannotator-tui download fails --check" {
  touch "$HOME/.tui-download-fails"
  install
  [ "$status" -eq 0 ]
  echo "$stderr" | grep -q "plannotator-tui is missing"
  install --check
  [ "$status" -ne 0 ]
}

@test "--check fails when the plugin is at another commit" {
  install
  sed -i 's/@[0-9a-f]*\]/@0000]/' "$HOME/.plugin"
  install --check
  [ "$status" -ne 0 ]
  echo "$stderr" | grep -q "worktrunk plugin isn't at"
}

@test "workshop is linked once and runs from ~/.local/bin" {
  install
  [ "$status" -eq 0 ]
  grep -qF "[local:$REPO_ROOT/plugins/workshop]" "$HOME/.plugin"
  "$HOME/.local/bin/workshop" --help 2>&1 | grep -q '^Usage: workshop'
  install
  [ "$(wc -l <"$HOME/.plugin-links")" -eq 1 ]
  ! echo "$output" | grep -q "✓ linked"
}

@test "workshop without a config says so, and advance stays quiet" {
  install
  run --separate-stderr "$HOME/.local/bin/workshop" doctor
  [ "$status" -eq 1 ]
  echo "$stderr" | grep -q "no config at"
  run "$HOME/.local/bin/workshop" advance
  [ "$status" -eq 0 ]
}

@test "--check fails when workshop isn't linked to this repo, and install repoints it" {
  install
  sed -i "s|\[local:.*|[local:/old/checkout]|" "$HOME/.plugin"
  install --check
  [ "$status" -ne 0 ]
  echo "$stderr" | grep -q "workshop plugin isn't linked"
  install
  grep -qF "[local:$REPO_ROOT/plugins/workshop]" "$HOME/.plugin"
}

@test "--check fails when the workshop command is missing or points elsewhere, and install repoints it" {
  install
  rm "$HOME/.local/bin/workshop"
  install --check
  [ "$status" -ne 0 ]
  echo "$stderr" | grep -q "workshop doesn't link to the repo"
  ln -s /old/checkout/workshop "$HOME/.local/bin/workshop"
  install --check
  [ "$status" -ne 0 ]
  install
  [ "$(readlink "$HOME/.local/bin/workshop")" = "$REPO_ROOT/plugins/workshop/bin/workshop" ]
}

@test "the tick timer is installed and left off" {
  cat >"$BATS_TEST_TMPDIR/bin/systemctl" <<'STUB'
#!/bin/sh
echo "$*" >>"$HOME/.systemctl-calls"
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/systemctl"
  install
  [ "$status" -eq 0 ]
  [ -f "$HOME/.config/systemd/user/workshop-tick.service" ]
  [ -f "$HOME/.config/systemd/user/workshop-tick.timer" ]
  [ ! -e "$HOME/.systemctl-calls" ]
  "$REPO_ROOT/install.sh" --rollback | bash
  grep -qx -- '--user disable --now workshop-tick.timer' "$HOME/.systemctl-calls"
  [ ! -e "$HOME/.config/systemd/user/workshop-tick.timer" ]
  [ ! -e "$HOME/.config/systemd/user/workshop-tick.timer.d" ]
}

@test "--dry-run runs no integration install" {
  install --dry-run
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "would run herdr integration install claude"
  [ ! -e "$HOME/.claude-integration" ]
  echo "$output" | grep -q "would install devashish2203/herdr-worktrunk"
  [ ! -e "$HOME/.plugin" ]
  echo "$output" | grep -q "would link the workshop plugin"
  echo "$output" | grep -q "would link $HOME/.local/bin/workshop"
  [ ! -L "$HOME/.local/bin/workshop" ]
}

@test "without herdr the rest still installs" {
  PATH="$(echo "$PATH" | sed "s|$BATS_TEST_TMPDIR/bin:||")" install
  [ "$status" -eq 0 ]
  echo "$stderr" | grep -q "herdr not installed"
  [ -f "$HOME/.claude/rules/herdr-workbench/workbench.md" ]
  [ ! -e "$HCONFIG" ]
}

@test "the rollback recipe removes the block, the skill, the integration, the script and the plugin" {
  mkdir -p "$(dirname "$HCONFIG")"
  printf '[keys]\nprefix = "ctrl+a"\n' >"$HCONFIG"
  install
  "$REPO_ROOT/install.sh" --rollback | bash
  ! grep -q herdr-workbench "$HCONFIG"
  grep -qx 'prefix = "ctrl+a"' "$HCONFIG"
  [ ! -e "$HOME/.claude/skills/herdr" ]
  [ ! -e "$HOME/.claude-integration" ]
  [ ! -e "$HOME/.config/herdr/bin" ]
  [ ! -e "$HOME/.plugin" ]
  [ ! -L "$HOME/.local/bin/workshop" ]
  [ ! -e "$HOME/.claude/skills/hunk-review" ]
  [ ! -e "$HOME/.config/herdr/plugins/config/jhochenbaum.hunkdiff/config.toml" ]
  ! grep -q herdr-workbench "$HOME/.config/hunk/config.toml"
}
