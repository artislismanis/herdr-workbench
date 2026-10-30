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
rm -f "$HERDR_BIN/wt-remove-current"
rmdir --ignore-fail-on-non-empty "$HERDR_BIN" 2>/dev/null || true
herdr plugin uninstall worktrunk 2>/dev/null || true
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

# herdr's config check is strict (duplicate tables, unknown keys), so run it on
# the prepared file instead of parsing TOML here.
_herdr_config_ok() {
  local out
  out="$(HERDR_CONFIG_PATH="$1" herdr config check 2>&1)" || { printf '%s\n' "$out" >&2; return 1; }
}

if ! command -v herdr >/dev/null 2>&1; then
  warn "herdr not installed; skipping its config, skill, Claude integration and worktrunk plugin."
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
  install_file "$REPO_ROOT/bin/wt-remove-current" "$HERDR_BIN/wt-remove-current" 755
  # Reinstalling replaces the checkout, so only do it when the pin moved.
  plugin="$(herdr plugin list --plugin worktrunk)"
  if grep -qF "[github:$WORKTRUNK_PLUGIN@$WORKTRUNK_PLUGIN_COMMIT]" <<<"$plugin"; then
    skip "worktrunk plugin at ${WORKTRUNK_PLUGIN_COMMIT:0:12}"
  else
    case "$MODE" in
      check)   DRIFT=$((DRIFT + 1)); warn "worktrunk plugin isn't at ${WORKTRUNK_PLUGIN_COMMIT:0:12}" ;;
      dry-run) log "${C_DIM}  would install $WORKTRUNK_PLUGIN at ${WORKTRUNK_PLUGIN_COMMIT:0:12}${C_RST}" ;;
      install)
        herdr plugin install "$WORKTRUNK_PLUGIN" --ref "$WORKTRUNK_PLUGIN_COMMIT" -y >/dev/null
        ok "installed $WORKTRUNK_PLUGIN at ${WORKTRUNK_PLUGIN_COMMIT:0:12}"
        ;;
    esac
  fi
fi

if [ "$MODE" = check ] && [ "$DRIFT" -gt 0 ]; then
  die "$DRIFT file(s) differ from the repo; run ./install.sh to refresh."
fi
