#!/usr/bin/env bash
# bash -n and shellcheck over every tracked shell script (by .sh, .bash or
# shebang). Usage: ./lint.sh [file ...]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [ "$#" -gt 0 ]; then
  files=("$@")
else
  mapfile -t files < <(git ls-files)
fi

targets=()
for f in "${files[@]}"; do
  [ -f "$f" ] || continue
  case "$f" in
    *.sh|*.bash) targets+=("$f"); continue ;;
    *.*) continue ;;
  esac
  case "$(head -1 "$f")" in '#!/usr/bin/env bash'*|'#!/bin/bash'*|'#!/bin/sh'*) targets+=("$f") ;; esac
done

fail=0
for f in "${targets[@]}"; do
  bash -n "$f" || fail=1
  shellcheck -S warning -x "$f" || fail=1
done
[ "$fail" = 0 ] && echo "lint: ok"
exit "$fail"
