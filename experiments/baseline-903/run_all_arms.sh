#!/bin/bash
# Helper reference for the ablation sequence (not executed as one script).
# Each arm is invoked individually as a bounded subprocess.
# This file documents the exact commands for reproducibility.

set -euo pipefail

REPO=/Users/mike/factories/factory/.factory/wt-903
PYTHON=/Users/mike/.local/share/uv/tools/factory/bin/python
SCRATCH=/tmp/factory-903-ablation-scratch.S0l4EY
HARNESS=experiments/baseline-903/ablation_run.py
OUTPUT_BASE=experiments/baseline-903/raw/ablation

COMMON_ENV=(
    HOME="$SCRATCH"
    XDG_CONFIG_HOME="$SCRATCH/.config"
    ANTHROPIC_API_KEY=
    ANTHROPIC_AUTH_TOKEN=
    CLAUDE_API_KEY=
    UV_CACHE_DIR=/Users/mike/.cache/uv
)

# 1. control-1
env "${COMMON_ENV[@]}" "$PYTHON" "$HARNESS" control "$OUTPUT_BASE/control-1"

# 2. ram (TMPDIR on RAM disk)
#    Before: RAMDISK_DEV=$(hdiutil attach -nomount ram://4194304)
#            diskutil erasevolume HFS+ RamDisk903 $RAMDISK_DEV
env "${COMMON_ENV[@]}" TMPDIR=/Volumes/RamDisk903 "$PYTHON" "$HARNESS" ram "$OUTPUT_BASE/ram"
#    After: hdiutil detach $RAMDISK_DEV

# 3. control-2
env "${COMMON_ENV[@]}" "$PYTHON" "$HARNESS" control "$OUTPUT_BASE/control-2"

# 4. git-cache (PATH wrapper)
#    Before: mkdir /tmp/factory-903-git-cache-bin
#            cp experiments/baseline-903/git-cache-wrapper.sh /tmp/factory-903-git-cache-bin/git
#            mkdir /tmp/factory-903-git-cache-data
env "${COMMON_ENV[@]}" \
    PATH="/tmp/factory-903-git-cache-bin:$PATH" \
    GIT_CACHE_DIR=/tmp/factory-903-git-cache-data \
    "$PYTHON" "$HARNESS" git-cache "$OUTPUT_BASE/git-cache"

# 5. control-3
env "${COMMON_ENV[@]}" "$PYTHON" "$HARNESS" control "$OUTPUT_BASE/control-3"

# 6. scratch-throttle (_users patched)
env "${COMMON_ENV[@]}" "$PYTHON" "$HARNESS" scratch-throttle "$OUTPUT_BASE/scratch-throttle"

# 7. control-4
env "${COMMON_ENV[@]}" "$PYTHON" "$HARNESS" control "$OUTPUT_BASE/control-4"
