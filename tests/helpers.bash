# Shared by every bats file. Each test gets its own HOME under
# BATS_TEST_TMPDIR, so nothing touches the real one.

bats_require_minimum_version 1.5.0

REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
export REPO_ROOT

export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com
export GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
export GIT_CONFIG_NOSYSTEM=1

# Tools installed under the real HOME (herdr in ~/.local/bin, mise shims) would
# stand in for the stubs and break the missing-tool cases. CI has none of them.
PATH="$(printf '%s' "$PATH" | tr ':' '\n' | grep -v "^$HOME/" | paste -sd:)"
export PATH

# The rollback recipe stops the real tick timer otherwise: the user's systemd is outside HOME.
stub_systemctl() {
  mkdir -p "$BATS_TEST_TMPDIR/stubs"
  printf '#!/bin/sh\n' >"$BATS_TEST_TMPDIR/stubs/systemctl"
  chmod +x "$BATS_TEST_TMPDIR/stubs/systemctl"
  export PATH="$BATS_TEST_TMPDIR/stubs:$PATH"
}
