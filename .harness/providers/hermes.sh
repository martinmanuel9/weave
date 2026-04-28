#!/usr/bin/env bash
# Hermes Agent adapter for Weave runtime.
# Receives weave.request.v1 on stdin, returns weave.response.v1 on stdout.
set -euo pipefail
exec python3 "$(dirname "$0")/hermes_adapter.py"
