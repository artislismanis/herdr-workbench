# shellcheck shell=bash
# Helpers sourced by install.sh. Every write goes through install_file,
# ensure_symlink, ensure_top_block or ensure_bottom_block, so --dry-run and
# --check cover all of them.

if [ -t 1 ]; then
  C_DIM=$'\033[2m' C_GRN=$'\033[32m' C_YLW=$'\033[33m' C_RED=$'\033[31m' C_RST=$'\033[0m'
else
  C_DIM='' C_GRN='' C_YLW='' C_RED='' C_RST=''
fi

log()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$C_GRN" "$C_RST" "$*"; }
skip() { printf '%s· %s%s\n' "$C_DIM" "$*" "$C_RST"; }
warn() { printf '%s! %s%s\n' "$C_YLW" "$*" "$C_RST" >&2; }
die()  { printf '%s✗ %s%s\n' "$C_RED" "$*" "$C_RST" >&2; exit 1; }

# MODE is install, dry-run or check. DRIFT counts files that differ from the
# repo; --check exits non-zero when it is above zero.
MODE=install
DRIFT=0

# Shared by both writers: report, or replace dst with the prepared tmp file.
_apply() {
  local tmp="$1" dst="$2" mode="$3"
  if [ -f "$dst" ] && cmp -s "$tmp" "$dst" && [ "$(stat -c %a "$dst")" = "$mode" ]; then
    rm -f "$tmp"
    skip "$dst up to date"
    return 0
  fi
  case "$MODE" in
    check)   rm -f "$tmp"; DRIFT=$((DRIFT + 1)); warn "$dst differs from the repo" ;;
    dry-run) rm -f "$tmp"; log "${C_DIM}  would write $dst${C_RST}" ;;
    install)
      mkdir -p "$(dirname "$dst")"
      chmod "$mode" "$tmp"
      mv -f "$tmp" "$dst"
      ok "wrote $dst"
      ;;
  esac
}

# install_file <src> <dst> <mode>: dst is ours; refresh it whenever it drifts.
install_file() {
  local src="$1" dst="$2" mode="$3" tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/herdr-workbench.XXXXXX")"
  cp "$src" "$tmp"
  _apply "$tmp" "$dst" "$mode"
}

# ensure_symlink <target> <dst>: dst is ours; repoint it whenever it drifts.
ensure_symlink() {
  local target="$1" dst="$2"
  if [ "$(readlink "$dst")" = "$target" ]; then
    skip "$dst up to date"
    return 0
  fi
  case "$MODE" in
    check)   DRIFT=$((DRIFT + 1)); warn "$dst doesn't link to the repo" ;;
    dry-run) log "${C_DIM}  would link $dst${C_RST}" ;;
    install)
      mkdir -p "$(dirname "$dst")"
      ln -sfn "$target" "$dst"
      ok "linked $dst"
      ;;
  esac
}

# ensure_top_block <marker> <src> <dst>: dst is shared with its owner. Keep our
# block, delimited by marker lines, as the first thing in the file and leave
# everything below it alone. First in the file because a TOML key after a
# [table] header belongs to that table.
ensure_top_block() {
  local marker="$1" src="$2" dst="$3" tmp mode=644
  local begin="# >>> $marker >>>" end="# <<< $marker <<<"
  tmp="$(mktemp "${TMPDIR:-/tmp}/herdr-workbench.XXXXXX")"
  {
    printf '%s\n' "$begin"
    cat "$src"
    printf '%s\n' "$end"
    if [ -f "$dst" ]; then
      awk -v b="$begin" -v e="$end" '$0 == b { skip = 1; next } $0 == e { skip = 0; next } !skip' "$dst"
    fi
  } >"$tmp"
  [ -f "$dst" ] && mode="$(stat -c %a "$dst")"
  _apply "$tmp" "$dst" "$mode"
}

# ensure_bottom_block <marker> <src> <dst> [validate]: like ensure_top_block,
# but our block goes last, for files whose settings are all [tables]: a table
# at the end can't capture settings of yours. validate, if given, is called
# with the prepared file and must succeed before anything is written.
ensure_bottom_block() {
  local marker="$1" src="$2" dst="$3" validate="${4:-}" tmp mode=644
  local begin="# >>> $marker >>>" end="# <<< $marker <<<"
  tmp="$(mktemp "${TMPDIR:-/tmp}/herdr-workbench.XXXXXX")"
  {
    if [ -f "$dst" ]; then
      awk -v b="$begin" -v e="$end" '$0 == b { skip = 1; next } $0 == e { skip = 0; next } !skip' "$dst"
    fi
    printf '%s\n' "$begin"
    cat "$src"
    printf '%s\n' "$end"
  } >"$tmp"
  if [ -n "$validate" ] && ! "$validate" "$tmp"; then
    rm -f "$tmp"
    die "$dst would be invalid with our block; fix the settings above it (a table can't appear twice)."
  fi
  [ -f "$dst" ] && mode="$(stat -c %a "$dst")"
  _apply "$tmp" "$dst" "$mode"
}
