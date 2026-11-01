#!/usr/bin/env bats
# The hunk GitHub scripts, with stub gh, hunk and herdr. The review shows lines
# 1-5 on the new side and 1-4 on the old side of f.txt.

load helpers

setup() {
  export STUB="$BATS_TEST_TMPDIR/stub"
  mkdir -p "$STUB/bin"
  REPO="$BATS_TEST_TMPDIR/repo"
  git init -q -b main "$REPO"
  echo a >"$REPO/f.txt"
  git -C "$REPO" add f.txt
  git -C "$REPO" commit -qm one
  SHA="$(git -C "$REPO" rev-parse HEAD)"

  cat >"$STUB/bin/gh" <<'GH'
#!/bin/sh
case "$*" in
  "pr view --json number --jq .number")
    [ -f "$STUB/gh-down" ] && { echo "error connecting to api.github.com" >&2; exit 1; }
    [ -f "$STUB/no-pr" ] && { echo 'no pull requests found for branch "x"' >&2; exit 1; }
    echo 7 ;;
  "pr view --json"*) [ -f "$STUB/no-pr" ] && exit 1; cat "$STUB/pr.json" ;;
  "api user --jq .login") echo me ;;
  *pulls/7/reviews\ --input*) cat >"$STUB/posted.json"; echo https://example.test/review ;;
  *pulls/7/reviews) cat "$STUB/reviews.json" ;;
  *pulls/7/comments) cat "$STUB/comments.json" ;;
  *) echo "unexpected gh: $*" >&2; exit 1 ;;
esac
GH
  cat >"$STUB/bin/hunk" <<'HUNK'
#!/bin/sh
case "$2 $3" in
  "review --repo") cat "$STUB/review.json" ;;
  "comment list") cat "$STUB/notes.json" ;;
  "comment rm") echo "$6" >>"$STUB/removed" ;;
  "comment apply") cat >"$STUB/applied.json" ;;
esac
HUNK
  # Like the real one, invoke takes the action id before its options.
  cat >"$STUB/bin/herdr" <<'HERDR'
#!/bin/sh
echo "$*" >>"$STUB/herdr.log"
case "$1 $2 $3" in
  "pane get "*) printf '{"result": {"pane": {"cwd": "%s"}}}' "$PWD" ;;
  "plugin action invoke") [ "$4" = review ] || { echo "unknown option" >&2; exit 2; } ;;
esac
HERDR
  chmod +x "$STUB/bin/"*
  export PATH="$STUB/bin:$PATH"
  unset HERDR_ACTIVE_PANE_ID HERDR_PANE_ID

  jq -n --arg sha "$SHA" '{number: 7, url: "https://example.test/7", headRefOid: $sha, author: {login: "them"}, autoMergeRequest: null}' >"$STUB/pr.json"
  echo '[[]]' >"$STUB/reviews.json"
  echo '[[]]' >"$STUB/comments.json"
  echo '{"comments": []}' >"$STUB/notes.json"
  cat >"$STUB/review.json" <<'JSON'
{"review": {"files": [{"path": "f.txt", "hunks": [{"oldRange": [1, 4], "newRange": [1, 5]}]}]}}
JSON
}

notes() { printf '%s' "$1" >"$STUB/notes.json"; }
push() { run --separate-stderr "$REPO_ROOT/bin/hunk-pr-push" "$@" "$REPO" </dev/null; }
posted() { jq -r "$1" "$STUB/posted.json"; }

AGENT_NOTE='{"source": "agent", "author": "claude", "filePath": "f.txt", "newRange": [2, 2], "body": "fix this"}'

@test "push: an inline note posts on its line, right side" {
  notes "{\"comments\": [$AGENT_NOTE]}"
  push --as comment
  [ "$status" -eq 0 ]
  [ "$(posted .event)" = COMMENT ]
  [ "$(posted .commit_id)" = "$SHA" ]
  [ "$(posted '.comments[0].line')" = 2 ]
  [ "$(posted '.comments[0].side')" = RIGHT ]
  [ "$(posted '.comments[0] | has("start_line")')" = false ]
}

@test "push: an old-side note posts on the left, a range as a multi-line comment" {
  notes '{"comments": [{"source": "agent", "filePath": "f.txt", "oldRange": [2, 3], "body": "gone"}]}'
  push --as comment
  [ "$(posted '.comments[0].side')" = LEFT ]
  [ "$(posted '.comments[0].start_line')" = 2 ]
  [ "$(posted '.comments[0].line')" = 3 ]
  [ "$(posted '.comments[0].start_side')" = LEFT ]
}

@test "push: a note outside the diff is left out with a warning" {
  notes "{\"comments\": [$AGENT_NOTE, {\"source\": \"agent\", \"filePath\": \"f.txt\", \"newRange\": [9, 9], \"body\": \"far\"}]}"
  push --as comment
  [ "$status" -eq 0 ]
  [ "$(posted '.comments | length')" = 1 ]
  echo "$stderr" | grep -q 'outside the diff'
}

@test "push: your notes and GitHub's never post" {
  notes "{\"comments\": [$AGENT_NOTE,
    {\"source\": \"user\", \"filePath\": \"f.txt\", \"newRange\": [1, 1], \"body\": \"mine\"},
    {\"source\": \"agent\", \"author\": \"github:bob\", \"filePath\": \"f.txt\", \"newRange\": [3, 3], \"body\": \"bob\"}]}"
  push --as comment
  [ "$(posted '.comments | length')" = 1 ]
  [ "$(posted '.comments[0].body')" = "fix this" ]
}

@test "push: a pr-level note becomes the body, without its prefix" {
  notes '{"comments": [{"source": "agent", "author": "pr-level", "filePath": "f.txt", "newRange": [1, 1], "body": "PR-level: overall"}]}'
  push --as request-changes
  [ "$status" -eq 0 ]
  [ "$(posted .body)" = overall ]
  [ "$(posted .event)" = REQUEST_CHANGES ]
}

@test "push: draft sends no event" {
  notes "{\"comments\": [$AGENT_NOTE]}"
  push --as draft
  [ "$(posted 'has("event")')" = false ]
}

@test "push: your own PR refuses approve and request changes" {
  jq '.author.login = "me"' "$STUB/pr.json" >"$STUB/x" && mv "$STUB/x" "$STUB/pr.json"
  notes "{\"comments\": [$AGENT_NOTE]}"
  push --as approve
  [ "$status" -ne 0 ]
  echo "$stderr" | grep -q "refuses approve"
  [ ! -e "$STUB/posted.json" ]
}

@test "push: your own PR isn't offered approve at the prompt" {
  jq '.author.login = "me"' "$STUB/pr.json" >"$STUB/x" && mv "$STUB/x" "$STUB/pr.json"
  notes "{\"comments\": [$AGENT_NOTE]}"
  run bash -c "echo n | '$REPO_ROOT/bin/hunk-pr-push' '$REPO' 2>&1"
  ! echo "$output" | grep -q approve
}

@test "push: a pending review of yours is refused" {
  echo '[[{"state": "PENDING", "user": {"login": "me"}}]]' >"$STUB/reviews.json"
  notes "{\"comments\": [$AGENT_NOTE]}"
  push --as comment
  [ "$status" -ne 0 ]
  echo "$stderr" | grep -q "pending review"
  [ ! -e "$STUB/posted.json" ]
}

@test "push: uncommitted changes are refused" {
  echo b >>"$REPO/f.txt"
  notes "{\"comments\": [$AGENT_NOTE]}"
  push --as comment
  [ "$status" -ne 0 ]
  echo "$stderr" | grep -q "uncommitted"
}

@test "push: HEAD must be the PR's head" {
  jq '.headRefOid = "0000"' "$STUB/pr.json" >"$STUB/x" && mv "$STUB/x" "$STUB/pr.json"
  notes "{\"comments\": [$AGENT_NOTE]}"
  push --as comment
  [ "$status" -ne 0 ]
  echo "$stderr" | grep -q "pushed head"
}

@test "push: request changes needs a PR-level comment" {
  notes "{\"comments\": [$AGENT_NOTE]}"
  push --as request-changes
  [ "$status" -ne 0 ]
  echo "$stderr" | grep -q "needs a PR-level comment"
}

@test "push: requesting changes asks for the PR-level comment when the notes are all inline" {
  notes "{\"comments\": [$AGENT_NOTE]}"
  run bash -c "echo 'see the notes' | '$REPO_ROOT/bin/hunk-pr-push' --as request-changes '$REPO'"
  [ "$status" -eq 0 ]
  [ "$(posted .body)" = "see the notes" ]
  [ "$(posted .event)" = REQUEST_CHANGES ]
}

@test "push: --before runs ahead of a visible review, and a failure posts nothing" {
  jq '.author.login = "them"' "$STUB/pr.json" >"$STUB/x" && mv "$STUB/x" "$STUB/pr.json"
  notes "{\"comments\": [$AGENT_NOTE]}"
  push --as comment --before "test ! -e '$STUB/posted.json' && touch '$STUB/before'"
  [ "$status" -eq 0 ]
  [ -e "$STUB/before" ]
  rm "$STUB/posted.json"
  push --as comment --before false
  [ "$status" -ne 0 ]
  [ ! -e "$STUB/posted.json" ]
}

@test "push: --before waits for a review anyone can see" {
  jq '.author.login = "them"' "$STUB/pr.json" >"$STUB/x" && mv "$STUB/x" "$STUB/pr.json"
  notes "{\"comments\": [$AGENT_NOTE]}"
  push --as draft --before "touch '$STUB/before'"
  [ "$status" -eq 0 ]
  [ ! -e "$STUB/before" ]
}

@test "push: a clean review can be approved with no notes" {
  push --as approve
  [ "$status" -eq 0 ]
  [ "$(posted .event)" = APPROVE ]
  [ "$(posted '.comments | length')" = 0 ]
}

@test "push: approving with auto-merge on asks again, and no means no" {
  jq '.autoMergeRequest = {}' "$STUB/pr.json" >"$STUB/x" && mv "$STUB/x" "$STUB/pr.json"
  notes "{\"comments\": [$AGENT_NOTE]}"
  run bash -c "echo n | '$REPO_ROOT/bin/hunk-pr-push' --as approve '$REPO'"
  [ "$status" -eq 0 ]
  [ ! -e "$STUB/posted.json" ]
}

@test "push: nothing to post stops" {
  push --as comment
  [ "$status" -ne 0 ]
  echo "$stderr" | grep -q "nothing to post"
}

@test "notes: a thread lands as one note, replies included" {
  cat >"$STUB/comments.json" <<'JSON'
[[{"id": 1, "in_reply_to_id": null, "path": "f.txt", "line": 2, "side": "RIGHT", "user": {"login": "ann"}, "body": "why?", "html_url": "u1"}],
 [{"id": 2, "in_reply_to_id": 1, "path": "f.txt", "line": 2, "side": "RIGHT", "user": {"login": "bob"}, "body": "because", "html_url": "u2"}]]
JSON
  run "$REPO_ROOT/bin/hunk-pr-notes" "$REPO"
  [ "$status" -eq 0 ]
  [ "$(jq '.comments | length' "$STUB/applied.json")" = 1 ]
  [ "$(jq -r '.comments[0].author' "$STUB/applied.json")" = github:ann ]
  [ "$(jq '.comments[0].newLine' "$STUB/applied.json")" = 2 ]
  jq -r '.comments[0].summary' "$STUB/applied.json" | grep -q 'bob: because'
}

@test "notes: outdated and outside threads are reported, not added" {
  cat >"$STUB/comments.json" <<'JSON'
[[{"id": 1, "in_reply_to_id": null, "path": "f.txt", "line": null, "side": "RIGHT", "user": {"login": "ann"}, "body": "x", "html_url": "u1"},
  {"id": 2, "in_reply_to_id": null, "path": "f.txt", "line": 9, "side": "RIGHT", "user": {"login": "ann"}, "body": "y", "html_url": "u2"}]]
JSON
  run "$REPO_ROOT/bin/hunk-pr-notes" "$REPO"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q '0 thread(s); 1 outdated, 1 outside'
  [ ! -e "$STUB/applied.json" ]
}

@test "notes: re-running replaces earlier GitHub notes only" {
  notes '{"comments": [{"noteId": "gh1", "author": "github:ann"}, {"noteId": "ai1", "author": "claude"}, {"noteId": "you1"}]}'
  run "$REPO_ROOT/bin/hunk-pr-notes" "$REPO"
  [ "$(cat "$STUB/removed")" = gh1 ]
}

@test "notes: --quiet with no PR says nothing" {
  touch "$STUB/no-pr"
  run "$REPO_ROOT/bin/hunk-pr-notes" --quiet "$REPO"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "notes: a GitHub failure isn't mistaken for no PR" {
  touch "$STUB/gh-down"
  run "$REPO_ROOT/bin/hunk-pr-notes" --quiet "$REPO"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q "error connecting"
}

@test "open: a GitHub failure is reported, with the review still open" {
  touch "$STUB/gh-down"
  cd "$REPO"
  HERDR_ACTIVE_PANE_ID=p1 run "$REPO_ROOT/bin/hunk-review-open"
  [ "$status" -ne 0 ]
  grep -q 'plugin action invoke review' "$STUB/herdr.log"
  grep -q 'notification show' "$STUB/herdr.log"
  echo "$output" | grep -q "error connecting"
}

@test "open: a key acts on the focused pane, without moving focus" {
  touch "$STUB/no-pr"
  cd "$REPO"
  HERDR_ACTIVE_PANE_ID=p1 run "$REPO_ROOT/bin/hunk-review-open"
  [ "$status" -eq 0 ]
  grep -qx 'plugin action invoke review --plugin jhochenbaum.hunkdiff' "$STUB/herdr.log"
  ! grep -q 'agent focus' "$STUB/herdr.log"
}

@test "open: Claude focuses its own pane first" {
  touch "$STUB/no-pr"
  cd "$REPO"
  HERDR_PANE_ID=p2 run "$REPO_ROOT/bin/hunk-review-open"
  [ "$status" -eq 0 ]
  [ "$(head -1 "$STUB/herdr.log")" = "agent focus p2" ]
}
