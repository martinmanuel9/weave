# Design: `weave status` with Session History Ledger

**Date:** 2026-04-11
**Phase:** 4 (item 4.2)
**Status:** draft

## Problem

`weave status` counts session JSONL files and shows recent activity, but has two gaps: (1) it includes `session_history.jsonl` in its file count (a bug — it's a ledger, not a session), and (2) it shows nothing about compacted sessions. After running `weave compact`, removed sessions vanish from status entirely even though their summaries exist in the ledger.

## Changes

1. **Fix session count** — exclude `session_history.jsonl` from the glob.
2. **Add `read_session_history()` helper** — reads and parses the ledger file, returns entries most-recent-first.
3. **Append "Session history" section** — show last 10 ledger entries below the existing activity output.
4. **Show split count** — `Sessions: N active, M compacted` instead of just `Sessions: N`.

## Output format

```
Project:  weave
Phase:    sandbox
Status:   active
Provider: claude-code
Enabled providers: claude-code, ollama
Sessions: 3 active, 15 compacted

Recent activity:
  [2026-04-11 10:30] claude-code — success — implement auth middleware

Session history (compacted):
  [2026-04-10] sess-abc12345 — claude-code — 12 invocations — 45.0s — success
  [2026-04-09] sess-def67890 — ollama — 3 invocations — 12.1s — success
```

If no ledger exists, the "Session history" section is omitted. If the ledger exists but is empty, the section is omitted.

## Files changed

| Path | Change |
|---|---|
| `src/weave/core/compaction.py` | Add `read_session_history(sessions_dir) -> list[dict]` |
| `src/weave/cli.py` | Update `status_cmd` — fix count, add ledger section |
| `tests/test_compaction.py` | +1 test for `read_session_history` |
| `tests/test_status.py` or inline | +2 tests for CLI output |

## Non-goals

- Filtering/searching session history
- `--history` flag or separate subcommand
- Paginating large ledgers (10-entry cap is sufficient)
