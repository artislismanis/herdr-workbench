#!/usr/bin/env bash
# Installs the herdr workbench config into $HOME. Safe to re-run: files we own
# are refreshed when they drift, shared files only have our marked block
# rewritten.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$REPO_ROOT/lib/common.sh"

WT_DIR="$HOME/.config/worktrunk"
WT_HOOKS=(worktree-setup sync-default-branch)

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

if [ "$MODE" = check ] && [ "$DRIFT" -gt 0 ]; then
  die "$DRIFT file(s) differ from the repo; run ./install.sh to refresh."
fi
