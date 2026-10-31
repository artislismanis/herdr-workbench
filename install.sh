#!/usr/bin/env bash
# Installs the herdr workbench config into $HOME. Safe to re-run: files we own
# are refreshed when they drift, shared files only have our marked block
# rewritten.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$REPO_ROOT/lib/common.sh"
# shellcheck source=pins
source "$REPO_ROOT/pins"

WT_DIR="$HOME/.config/worktrunk"
WT_HOOKS=(worktree-setup sync-default-branch)
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
RULES_DIR="$CLAUDE_DIR/rules/herdr-workbench"
HERDR_CONFIG="${HERDR_CONFIG_PATH:-$HOME/.config/herdr/config.toml}"
HERDR_SKILL="$CLAUDE_DIR/skills/herdr/SKILL.md"
HERDR_BIN="$HOME/.config/herdr/bin"
HERDR_SCRIPTS=(wt-remove-current hunk-review-open hunk-pr-notes hunk-pr-push)
HERDR_PLUGINS="$HOME/.config/herdr/plugins"
HUNKDIFF_CONFIG="$HERDR_PLUGINS/config/jhochenbaum.hunkdiff/config.toml"
HUNK_CONFIG="$HOME/.config/hunk/config.toml"
HUNK_SKILL="$CLAUDE_DIR/skills/hunk-review/SKILL.md"
WORKSHOP_CLI="$HOME/.local/bin/workshop"
SYSTEMD_USER="$HOME/.config/systemd/user"
WORKSHOP_UNITS=(workshop-tick.service workshop-tick.timer)

usage() {
  cat <<USAGE
Usage: ./install.sh [--dry-run | --check | --rollback]

  (none)      Install or refresh everything.
  --dry-run   Print what would change; change nothing.
  --check     Exit non-zero if anything installed differs from this repo.
  --rollback  Print a shell recipe that undoes the install; run nothing.
USAGE
}

rollback() {
  cat <<RECIPE
# herdr-workbench rollback. Review, then paste.
rm -f$(printf ' "%s"' "${WT_HOOKS[@]/#/$WT_DIR/hooks/}")
rmdir --ignore-fail-on-non-empty "$WT_DIR/hooks" 2>/dev/null || true
# Strips only our block; your own worktrunk settings below it stay.
sed -i '/^# >>> herdr-workbench:worktrunk >>>$/,/^# <<< herdr-workbench:worktrunk <<<$/d' "$WT_DIR/config.toml"
rm -rf "$RULES_DIR" "$(dirname "$HERDR_SKILL")"
sed -i '/^# >>> herdr-workbench:herdr >>>$/,/^# <<< herdr-workbench:herdr <<<$/d' "$HERDR_CONFIG"
herdr integration uninstall claude 2>/dev/null || true
rm -f$(printf ' "%s"' "${HERDR_SCRIPTS[@]/#/$HERDR_BIN/}")
rmdir --ignore-fail-on-non-empty "$HERDR_BIN" 2>/dev/null || true
herdr plugin uninstall worktrunk 2>/dev/null || true
herdr plugin uninstall jhochenbaum.hunkdiff 2>/dev/null || true
herdr plugin uninstall annotate 2>/dev/null || true
herdr plugin uninstall workshop 2>/dev/null || true
rm -f "$WORKSHOP_CLI"
systemctl --user disable --now workshop-tick.timer 2>/dev/null || true
rm -f$(printf ' "%s"' "${WORKSHOP_UNITS[@]/#/$SYSTEMD_USER/}")
rm -f "$HUNKDIFF_CONFIG"
rm -rf "$(dirname "$HUNK_SKILL")"
# Strips only our block; your own hunk settings below it stay.
sed -i '/^# >>> herdr-workbench:hunk >>>$/,/^# <<< herdr-workbench:hunk <<<$/d' "$HUNK_CONFIG"
RECIPE
}

case "${1:-}" in
  "") ;;
  --dry-run) MODE=dry-run ;;
  --check) MODE=check ;;
  --rollback) rollback; exit 0 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { usage >&2; exit 2; }
[ "$(id -u)" != 0 ] || die "Run as your own user, not root: everything installs under \$HOME."

for hook in "${WT_HOOKS[@]}"; do
  install_file "$REPO_ROOT/worktrunk/hooks/$hook" "$WT_DIR/hooks/$hook" 755
done
ensure_top_block herdr-workbench:worktrunk "$REPO_ROOT/worktrunk/config.toml" "$WT_DIR/config.toml"
install_file "$REPO_ROOT/claude/rules/workbench.md" "$RULES_DIR/workbench.md" 644
ensure_top_block herdr-workbench:hunk "$REPO_ROOT/hunk/config.toml" "$HUNK_CONFIG"

if command -v hunk >/dev/null 2>&1; then
  # The skill bundled with the binary, so it always matches the pinned hunk.
  install_file "$(hunk skill path hunk-review)" "$HUNK_SKILL" 644
else
  warn "hunk not installed; skipping its skill."
fi

# herdr's config check is strict (duplicate tables, unknown keys), so run it on
# the prepared file instead of parsing TOML here.
_herdr_config_ok() {
  local out
  out="$(HERDR_CONFIG_PATH="$1" herdr config check 2>&1)" || { printf '%s\n' "$out" >&2; return 1; }
}

# Reinstalling replaces the checkout, so only do it when the pin moved.
_ensure_plugin() {
  local id="$1" repo="$2" commit="$3"
  if grep -qF "[github:$repo@$commit]" <<<"$(herdr plugin list --plugin "$id")"; then
    skip "$id plugin at ${commit:0:12}"
    return 0
  fi
  case "$MODE" in
    check)   DRIFT=$((DRIFT + 1)); warn "$id plugin isn't at ${commit:0:12}" ;;
    dry-run) log "${C_DIM}  would install $repo at ${commit:0:12}${C_RST}" ;;
    install)
      herdr plugin install "$repo" --ref "$commit" -y >/dev/null
      ok "installed $repo at ${commit:0:12}"
      ;;
  esac
}

# Linked, so the plugin moves with this checkout. herdr stores the resolved path.
_ensure_workshop() {
  local dir
  dir="$(cd "$REPO_ROOT/plugins/workshop" && pwd -P)"
  if grep -qF "[local:$dir]" <<<"$(herdr plugin list --plugin workshop)"; then
    skip "workshop plugin linked"
  else
    case "$MODE" in
      check)   DRIFT=$((DRIFT + 1)); warn "workshop plugin isn't linked to this repo" ;;
      dry-run) log "${C_DIM}  would link the workshop plugin${C_RST}" ;;
      install) herdr plugin link "$dir" >/dev/null; ok "linked the workshop plugin" ;;
    esac
  fi
  ensure_symlink "$dir/bin/workshop" "$WORKSHOP_CLI"
  # Installed, never enabled: turning it on starts tickets unattended.
  for unit in "${WORKSHOP_UNITS[@]}"; do
    install_file "$dir/systemd/$unit" "$SYSTEMD_USER/$unit" 644
  done
}

if ! command -v herdr >/dev/null 2>&1; then
  warn "herdr not installed; skipping its config, skill, Claude integration, plugins and workshop."
else
  ensure_bottom_block herdr-workbench:herdr "$REPO_ROOT/herdr/config.toml" "$HERDR_CONFIG" _herdr_config_ok
  # The skill bundled with the binary, so it always matches the pinned herdr.
  skill="$(mktemp "${TMPDIR:-/tmp}/herdr-workbench.XXXXXX")"
  herdr --skill >"$skill"
  install_file "$skill" "$HERDR_SKILL" 644
  rm -f "$skill"
  # Lets herdr resume Claude sessions after a server restart.
  # Captured first: grep -q quits at the claude line, and herdr then dies of
  # SIGPIPE writing the rest, which pipefail reads as not current.
  status="$(herdr integration status)"
  if grep -q '^claude: current' <<<"$status"; then
    skip "herdr's Claude integration up to date"
  else
    case "$MODE" in
      check)   DRIFT=$((DRIFT + 1)); warn "herdr's Claude integration isn't installed or current" ;;
      dry-run) log "${C_DIM}  would run herdr integration install claude${C_RST}" ;;
      install) herdr integration install claude >/dev/null; ok "installed herdr's Claude integration" ;;
    esac
  fi
  for script in "${HERDR_SCRIPTS[@]}"; do
    install_file "$REPO_ROOT/bin/$script" "$HERDR_BIN/$script" 755
  done
  _ensure_plugin worktrunk "$WORKTRUNK_PLUGIN" "$WORKTRUNK_PLUGIN_COMMIT"
  _ensure_plugin jhochenbaum.hunkdiff "$HUNKDIFF_PLUGIN" "$HUNKDIFF_PLUGIN_COMMIT"
  _ensure_plugin annotate "$ANNOTATE_PLUGIN" "$ANNOTATE_PLUGIN_COMMIT"
  # The plugin's build only warns when this download fails, so a broken review
  # pane would otherwise look like a clean install.
  if [ "$MODE" != dry-run ] && ! compgen -G "$HERDR_PLUGINS/github-installations/annotate-*/*/checkout/bin/plannotator-tui.exe" >/dev/null; then
    DRIFT=$((DRIFT + 1))
    warn "plannotator-tui is missing; reinstall the annotate plugin: herdr plugin uninstall annotate, then re-run this."
  fi
  hunkdiff_config="$(mktemp "${TMPDIR:-/tmp}/herdr-workbench.XXXXXX")"
  sed "s|__HOME__|$HOME|" "$REPO_ROOT/herdr/hunkdiff.toml" >"$hunkdiff_config"
  install_file "$hunkdiff_config" "$HUNKDIFF_CONFIG" 644
  rm -f "$hunkdiff_config"
  _ensure_workshop
fi

if [ "$MODE" = check ] && [ "$DRIFT" -gt 0 ]; then
  die "$DRIFT file(s) differ from the repo; run ./install.sh to refresh."
fi
