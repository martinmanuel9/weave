#!/usr/bin/env bash
# Weave quality gate: run pytest
# Exit 0 = allow (tests pass), non-zero = deny (tests fail)
set -euo pipefail

INPUT=$(cat)
WORKING_DIR=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['working_dir'])" 2>/dev/null || echo ".")

cd "$WORKING_DIR"

if command -v pytest >/dev/null 2>&1; then
    pytest tests/ -x -q 2>&1 || exit 1
else
    echo "pytest not found, skipping test gate" >&2
    exit 0
fi
