# Design: Content-Trust Allowlisting

**Date:** 2026-04-11
**Phase:** 4 (item 4.5)
**Status:** draft

## Problem

The security scanner (`scan_files`) checks every changed file against 6 regex-based rules. There's no way to exempt specific files from content scanning. Legitimate code that intentionally contains flagged patterns (test files, the security module itself, build scripts) triggers false positives. The existing `supply_chain_rules` override changes the *action* per rule but doesn't skip scanning. The existing `write_allow_overrides` exempts paths from the *write-deny list*, not the scanner.

## Changes

1. **`scanner_allowlist: list[str]`** on `SecurityConfig` — fnmatch patterns for files to skip entirely during content scanning.
2. **`scan_files()` gains `allowlist` parameter** — files matching any pattern are skipped before reading content.
3. **`_security_scan()` passes the allowlist** from config.

## Files

| Path | Change |
|---|---|
| `src/weave/schemas/config.py` | Add `scanner_allowlist` field to `SecurityConfig` |
| `src/weave/core/security.py` | `scan_files()` gains `allowlist` param, skip matching files |
| `src/weave/core/runtime.py` | Pass `allowlist` to `scan_files()` in `_security_scan` |
| `tests/test_security.py` | +3 tests |

## Non-goals

- Per-rule allowlisting (use `supply_chain_rules` action overrides for that)
- Content-hash-based trust (too complex for v1)
- Automatic detection of "this is a test file" (use explicit patterns)
