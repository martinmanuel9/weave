#!/usr/bin/env bash
# Weave provider adapter for opencode (sst/opencode)
set -euo pipefail
INPUT=$(cat)
TASK=$(echo "$INPUT" | jq -r '.task')
WORKING_DIR=$(echo "$INPUT" | jq -r '.workingDir')
cd "$WORKING_DIR"

if ! command -v opencode >/dev/null 2>&1; then
  jq -n --arg stderr "opencode not found on PATH" \
    '{ protocol: "weave.response.v1", exitCode: 127, stdout: "", stderr: $stderr, structured: {} }'
  exit 0
fi

STDOUT=""
STDERR=""
EXIT_CODE=0
TMPFILE="${TMPDIR:-/tmp}/weave-opencode-stderr-$$"
STDOUT=$(opencode run "$TASK" 2>"$TMPFILE") || EXIT_CODE=$?
STDERR=$(cat "$TMPFILE" 2>/dev/null || echo "")
rm -f "$TMPFILE"

jq -n \
  --arg stdout "$STDOUT" \
  --arg stderr "$STDERR" \
  --argjson exitCode "$EXIT_CODE" \
  '{ protocol: "weave.response.v1", exitCode: $exitCode, stdout: $stdout, stderr: $stderr, structured: {} }'
