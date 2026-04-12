#!/usr/bin/env bash
# Weave quality gate: run ruff linter
# Exit 0 = allow (lint passes or ruff not installed), non-zero = deny
set -euo pipefail

INPUT=$(cat)
WORKING_DIR=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['working_dir'])" 2>/dev/null || echo ".")

cd "$WORKING_DIR"

if command -v ruff >/dev/null 2>&1; then
    ruff check . 2>&1 || exit 1
else
    echo "ruff not found, skipping lint gate" >&2
    exit 0
fi
