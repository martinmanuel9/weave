#!/usr/bin/env bash
# Weave provider adapter for ollama
set -euo pipefail
INPUT=$(cat)
TASK=$(echo "$INPUT" | jq -r '.task')
WORKING_DIR=$(echo "$INPUT" | jq -r '.workingDir')
cd "$WORKING_DIR"
STDOUT=""
STDERR=""
EXIT_CODE=0
TMPFILE="${TMPDIR:-/tmp}/weave-stderr-$$"
STDOUT=$(ollama run "$TASK" 2>"$TMPFILE") || EXIT_CODE=$?
STDERR=$(cat "$TMPFILE" 2>/dev/null || echo "")
rm -f "$TMPFILE"
jq -n \
  --arg stdout "$STDOUT" \
  --arg stderr "$STDERR" \
  --argjson exitCode "$EXIT_CODE" \
  '{ protocol: "weave.response.v1", exitCode: $exitCode, stdout: $stdout, stderr: $stderr, structured: {} }'
