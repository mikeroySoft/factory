#!/bin/bash
# Git caching wrapper for #903 ablation git-cache arm.
# Caches read-only git command results by args+CWD hash.
# Placed in a temp bin dir prepended to PATH during the git-cache arm only.
set -euo pipefail

REAL_GIT=/usr/bin/git
CACHE_DIR="${GIT_CACHE_DIR:?GIT_CACHE_DIR must be set}"

HASH=$(printf '%s\0' "$PWD" "$@" | shasum -a 256 | cut -d' ' -f1)
CACHE_OUT="$CACHE_DIR/$HASH.stdout"
CACHE_ERR="$CACHE_DIR/$HASH.stderr"
CACHE_RC="$CACHE_DIR/$HASH.rc"

if [ -f "$CACHE_RC" ]; then
    cat "$CACHE_OUT" 2>/dev/null
    cat "$CACHE_ERR" >&2 2>/dev/null
    exit "$(cat "$CACHE_RC")"
fi

"$REAL_GIT" "$@" > "$CACHE_OUT" 2> "$CACHE_ERR"
RC=$?
printf '%d' "$RC" > "$CACHE_RC"
cat "$CACHE_OUT"
cat "$CACHE_ERR" >&2
exit "$RC"
