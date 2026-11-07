#!/usr/bin/env bash
# Move each herdr plugin in pins to its default branch's tip:
#   <NAME>_PLUGIN=<owner/repo>
#   <NAME>_PLUGIN_COMMIT=<sha>   (the line right after)
# Prints one line per change with the compare link, which is the diff to
# review before merging. Needs gh (GH_TOKEN).
set -euo pipefail

file="${1:-pins}"
plugin_re='^([A-Z_]+)_PLUGIN=([^ ]+)$'

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

name="" repo=""
while IFS= read -r line; do
  if [ -n "$name" ]; then
    [[ "$line" == "${name}_PLUGIN_COMMIT="* ]] || { echo "bump-plugins: expected ${name}_PLUGIN_COMMIT= after ${name}_PLUGIN" >&2; exit 1; }
    cur="${line#*=}"
    new="$(gh api "repos/$repo/commits/HEAD" --jq .sha)"
    if [ "$new" != "$cur" ]; then
      line="${name}_PLUGIN_COMMIT=$new"
      echo "$repo: ${cur:0:12} -> ${new:0:12} https://github.com/$repo/compare/$cur...$new"
    fi
    name=""
  elif [[ "$line" =~ $plugin_re ]]; then
    name="${BASH_REMATCH[1]}" repo="${BASH_REMATCH[2]}"
  fi
  printf '%s\n' "$line" >>"$tmp"
done <"$file"

cat "$tmp" >"$file"
