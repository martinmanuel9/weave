# Design: Transcript Compaction

**Date:** 2026-04-11
**Phase:** 3 (transcript compaction)
**Status:** draft
**Supersedes / extends:** [2026-04-09 Phase 1 design](2026-04-09-weave-runtime-phase1-design.md) — line 307 ("Transcript compaction (Phase 3)")

## Problem

Weave's session system writes data prolifically. Each `execute()` call appends an `ActivityRecord` to `{session_id}.jsonl`, and each session also produces a `.binding.json` and `.start_marker.json` sidecar. Over many invocations (e.g., a GSD-driven project with hundreds of tasks), `.harness/sessions/` accumulates thousands of files and unbounded-length JSONL files. Nothing prunes, archives, or summarizes this data.

Two independent growth problems exist:

1. **Within-session:** a single long-running session's JSONL grows without bound. If any consumer (context injection, `weave status`, debugging tools) reads the full file, it can blow the context window or take seconds to parse.

2. **Cross-session:** old sessions (completed, no longer active) linger forever. Their sidecars consume inodes and clutter the directory, making file listing slow.

This spec addresses both problems with two independent subsystems that share a `CompactionConfig` but differ in trigger, risk profile, and data flow.

## Goals

1. **Bounded JSONL files** — within-session rolling compaction keeps each JSONL at most `records_per_session` real records plus one summary record. Older records are summarized, not deleted silently — the summary preserves aggregate stats.

2. **Bounded session count** — cross-session lifecycle management (via `weave compact` CLI) summarizes old sessions into a one-line-per-session ledger, then deletes their raw files. The ledger provides a permanent audit trail.

3. **No data loss without user action** — within-session compaction is automatic but preserves a summary. Cross-session deletion is explicit (CLI command with `--dry-run` support).

4. **Crash-safe writes** — JSONL rewrites use atomic `.tmp` → `rename`.

## Non-goals

- Compacting the session history ledger (`session_history.jsonl`). It grows ~200 bytes per session; 10,000 sessions = ~2MB.
- Automatic lifecycle management (cron, hooks). The user runs `weave compact` when they choose.
- Streaming compaction (concurrent readers/writers on the same JSONL). Single-writer assumption holds.
- `weave status` integration (reading the ledger for display). Separable; can land any time after.
- Compression (gzip) of any files.
- Archive directory. The `archive_dir` config field is removed — summarize-then-delete replaces move-to-archive.

## Architecture

### Two subsystems

```
Subsystem A: Within-Session Rolling Compaction (eager, on write)
═══════════════════════════════════════════════════════════════════

  runtime._record()
       │
       ▼
  session.append_activity(compact_threshold=N)
       │
       ▼
  compaction._maybe_compact_session()
       │
       ▼
  count lines in JSONL
  > threshold?
       │ yes
       ▼
  split: [old records] [recent N records]
  _build_compaction_summary(old_records)
  atomic rewrite: [summary] + [recent N]


Subsystem B: Cross-Session Lifecycle (explicit, via CLI)
═══════════════════════════════════════════════════════════════════

  `weave compact` CLI command
       │
       ▼
  list all *.jsonl in sessions_dir (exclude session_history.jsonl)
  sort by mtime descending
  keep newest `sessions_to_keep`
       │
       ▼
  for each old session:
    _build_ledger_entry(session_id, jsonl_path)
    _append_ledger(ledger_path, entry)
    _delete_session_files(sessions_dir, session_id)
       │
       ▼
  return CompactResult(kept, removed, errors)
```

### Separation rationale

Subsystem A touches ONE file (the current session's JSONL) on every Nth write. Cheap, automatic, invisible. Subsystem B touches MANY files (all old sessions) on explicit command. Destructive, user-controlled, logged. They share `CompactionConfig` but are otherwise independent — different triggers, different risk profiles, different code paths.

### File layout after compaction

```
.harness/
└── sessions/
    ├── {active-session-1}.jsonl           # bounded by rolling compaction
    ├── {active-session-1}.binding.json
    ├── {active-session-1}.start_marker.json
    ├── {active-session-2}.jsonl
    ├── ...
    └── session_history.jsonl              # append-only ledger, one line per dead session
```

## Config changes

`CompactionConfig` in `schemas/config.py` — replace the current stub:

```python
class CompactionConfig(BaseModel):
    records_per_session: int = 50    # subsystem A: rolling compaction threshold per JSONL
    sessions_to_keep: int = 50       # subsystem B: lifecycle retention count
```

The old `keep_recent` and `archive_dir` fields are removed. A legacy-key migration in `core/config.py` maps `keep_recent` → `records_per_session` and silently drops `archive_dir`, matching the pattern established for `capability` → `capability_override` in Phase 3.

## Subsystem A: Within-session rolling compaction

### Trigger

Called from `session.append_activity()` when `compact_threshold` is not `None`. The `_record()` function in `runtime.py` passes `config.sessions.compaction.records_per_session` as the threshold.

### Updated `append_activity` signature

```python
def append_activity(
    sessions_dir: Path,
    session_id: str,
    record: ActivityRecord,
    compact_threshold: int | None = None,
) -> None:
```

When `compact_threshold` is set and positive, calls `_maybe_compact_session()` after writing. Callers that don't want compaction (tests, the ledger writer) omit the parameter.

### `_maybe_compact_session(sessions_dir, session_id, keep_recent)` algorithm

```
1. log_file = sessions_dir / f"{session_id}.jsonl"
2. Read all lines. If len(lines) <= keep_recent: return (no-op).
3. Split: old_lines = lines[:-keep_recent], recent_lines = lines[-keep_recent:]
4. Parse old_lines into ActivityRecord objects (skip corrupt lines with warning).
5. Call _build_compaction_summary(old_records) → summary ActivityRecord.
6. Atomic rewrite:
   a. tmp = log_file.with_suffix(".jsonl.tmp")
   b. Write: summary JSON line + "\n" + "\n".join(recent_lines) + "\n"
   c. tmp.rename(log_file)  # atomic on POSIX
```

### `_build_compaction_summary(records)` logic

Produces a single `ActivityRecord` with `type=ActivityType.system`, `task="compaction_summary"`:

1. Separate any existing `compaction_summary` records from real activity records.
2. From existing summaries: extract `compacted_count`, `total_duration_ms`, `status_counts`, `total_files_changed`, `unique_files_changed`, `earliest_timestamp`, `providers_used` from their `metadata` dicts.
3. From real records: count, sum durations, tally statuses, collect `files_changed`, collect providers, find min/max timestamps.
4. Merge: add counts, union sets, min earliest / max latest.
5. Cap `unique_files_changed` at 50 entries (sorted, truncated) to keep the summary bounded.
6. Return:

```python
ActivityRecord(
    type=ActivityType.system,
    status=ActivityStatus.success,
    task="compaction_summary",
    metadata={
        "compacted_count": <int>,
        "earliest_timestamp": <ISO string>,
        "latest_timestamp": <ISO string>,
        "total_duration_ms": <float>,
        "providers_used": [<str>, ...],
        "status_counts": {"success": N, "denied": N, ...},
        "total_files_changed": <int>,
        "unique_files_changed": [<str>, ...],  # capped at 50
    },
)
```

### Merge behavior for repeated compactions

When a JSONL is compacted and then grows past the threshold again, the oldest record will be the previous compaction summary. `_build_compaction_summary` detects `task == "compaction_summary"` records and folds their `metadata` into the new summary's running totals. This means:

- `compacted_count` accumulates across all compaction cycles
- Timestamps track the true earliest and latest across the session's full history
- Provider lists and file lists are unioned
- No fidelity is lost on aggregate stats through repeated compactions

### Atomicity and crash safety

The `.tmp` → `rename` pattern gives crash safety on POSIX:
- If the process dies before `rename`: `.tmp` is a partial file, original JSONL is intact. Next compaction will re-trigger normally. The stale `.tmp` is overwritten.
- If the process dies after `rename`: compaction is complete. The JSONL contains the summary + recent records.
- No locking is needed — single-writer assumption (one `execute()` at a time per session).

## Subsystem B: Cross-session lifecycle management

### Trigger

Explicit `weave compact` CLI command. Never runs automatically.

### `compact_sessions(sessions_dir, sessions_to_keep, dry_run=False)` algorithm

```
1. Glob *.jsonl in sessions_dir. Exclude "session_history.jsonl".
2. Sort by st_mtime descending (newest first).
3. If len(files) <= sessions_to_keep: return CompactResult(kept=len, removed=0).
4. keep = files[:sessions_to_keep], remove = files[sessions_to_keep:]
5. If dry_run: return CompactResult(kept=len(keep), removed=len(remove)) without acting.
6. For each file in remove:
   a. session_id = file.stem
   b. entry = _build_ledger_entry(session_id, file)
   c. _append_ledger(ledger_path, entry)  # must succeed before deletion
   d. _delete_session_files(sessions_dir, session_id)
   e. On error in (b-d): collect error message, continue to next session.
7. Return CompactResult(kept, removed, errors).
```

### `_build_ledger_entry(session_id, jsonl_path)` logic

Reads the session's JSONL, parses all records, and produces a dict:

```python
{
    "session_id": "abc-123",
    "provider": "claude-code",             # most-used provider across records
    "started": "2026-04-11T10:00:00Z",     # earliest timestamp
    "ended": "2026-04-11T10:30:00Z",       # latest timestamp
    "invocation_count": 12,                 # total records (including compacted count)
    "total_duration_ms": 45000.0,
    "final_status": "success",             # status of last record
    "files_changed_count": 8,
    "task_snippet": "implement auth mid..."  # first 100 chars of the first task field
}
```

If the JSONL contains a `compaction_summary` record, its `compacted_count` is folded into `invocation_count` and its `total_duration_ms` into the sum. This ensures the ledger reflects the session's full history, not just the post-compaction tail.

If the JSONL is corrupt (no parseable records), the entry is written with degraded data: `invocation_count: 0`, `final_status: "unknown"`, `task_snippet: ""`. The error is collected in `CompactResult.errors`.

### `_delete_session_files(sessions_dir, session_id)` logic

Deletes all files matching `{session_id}.*` in sessions_dir:
- `{session_id}.jsonl`
- `{session_id}.binding.json`
- `{session_id}.start_marker.json`

Best-effort: missing files are silently skipped. Each successful deletion is a separate `Path.unlink()` call. If any deletion fails (permissions), the error is logged and collected.

### `CompactResult` dataclass

```python
@dataclass
class CompactResult:
    kept: int
    removed: int
    errors: list[str] = field(default_factory=list)
```

### Session history ledger (`session_history.jsonl`)

- Lives at `.harness/sessions/session_history.jsonl`
- Append-only JSONL, one JSON line per compacted session
- Never compacted itself (bounded by growth rate: ~200 bytes per session)
- Excluded from session file discovery (the glob filter skips it by name)
- `_append_ledger` writes to the ledger using the same append pattern as `append_activity`, but without compaction

### CLI command

```python
@main.command("compact")
@click.option("--dry-run", is_flag=True, help="Show what would be removed without acting")
@click.pass_context
def compact_cmd(ctx, dry_run):
    """Compact old sessions: summarize to ledger and delete raw files."""
```

Reads `CompactionConfig` from the project config. Calls `compact_sessions()`. Prints a summary: `Compacted N sessions (M kept, E errors)` or `Dry run: would remove N sessions (M kept)`.

### Ordering guarantee

`_append_ledger` MUST succeed before `_delete_session_files` runs. If the ledger append fails (disk full, permissions), the raw files are preserved — no data loss. This is the critical ordering invariant.

## Error handling matrix

| Condition | Where | Behavior |
|---|---|---|
| JSONL has fewer lines than threshold | `_maybe_compact_session` | No-op, return |
| JSONL is empty or missing | `_maybe_compact_session` | No-op, return |
| Corrupt JSON line during within-session compaction | `_build_compaction_summary` | Skip line, log warning, count in `compacted_count` without contributing stats |
| `.tmp` file left from crashed compaction | `_maybe_compact_session` | Overwritten on next compaction (original JSONL is intact) |
| `compact_threshold` is 0 or negative | `_maybe_compact_session` | Treat as no-op (same as `None`) |
| Corrupt JSONL during ledger entry build | `_build_ledger_entry` | Write degraded entry, collect error in `CompactResult.errors` |
| Ledger file write fails | `_append_ledger` | Raise exception — do NOT delete session files |
| Session file deletion fails | `_delete_session_files` | Log, collect error, continue to next session |
| No `.harness/sessions/` directory | `compact_sessions` | Return `CompactResult(kept=0, removed=0)` |
| `session_history.jsonl` in glob results | `compact_sessions` | Excluded by name filter |
| Legacy config `keep_recent` | Config migration | Renamed to `records_per_session`, `DeprecationWarning` |
| Legacy config `archive_dir` | Config migration | Silently dropped |

## Test plan

### New test file: `tests/test_compaction.py`

**Within-session (Subsystem A):**

1. `test_compact_noop_below_threshold` — 10 records, threshold 50, file unchanged.
2. `test_compact_rewrites_at_threshold` — 60 records, threshold 50, file has 1 summary + 50 recent.
3. `test_compact_summary_has_correct_stats` — verify `compacted_count`, `total_duration_ms`, `status_counts`, `providers_used`, `earliest_timestamp`, `latest_timestamp`, `total_files_changed`, `unique_files_changed`.
4. `test_compact_merges_prior_summary` — compact once (60→51), add 10 more (61 lines), compact again → single summary with merged stats from both cycles.
5. `test_compact_atomic_rewrite` — verify `.tmp` file doesn't persist after successful compaction.
6. `test_compact_skips_corrupt_lines` — inject a bad JSON line, compaction succeeds with warning, corrupt line counted but stats excluded.
7. `test_compact_noop_for_empty_file` — empty JSONL, no crash.
8. `test_append_activity_with_compact_threshold` — call `append_activity` with `compact_threshold=5`, add 6 records, verify file is compacted.

**Cross-session (Subsystem B):**

9. `test_lifecycle_noop_when_under_retention` — 3 sessions, retention 50, nothing removed.
10. `test_lifecycle_removes_oldest_sessions` — 5 sessions with staggered mtimes, retention 2, verify 3 oldest removed, 2 newest kept.
11. `test_lifecycle_writes_ledger_entry` — verify `session_history.jsonl` gets one line per removed session with correct shape.
12. `test_lifecycle_ledger_entry_includes_compaction_summary` — session with a compaction summary record, verify ledger entry folds the summary stats.
13. `test_lifecycle_deletes_all_sidecar_files` — verify binding.json and start_marker.json are cleaned up alongside JSONL.
14. `test_lifecycle_skips_missing_sidecars` — JSONL exists but binding is missing, no crash.
15. `test_lifecycle_dry_run_deletes_nothing` — `dry_run=True`, verify files still exist, result shows what would be removed.
16. `test_lifecycle_excludes_ledger_from_session_count` — `session_history.jsonl` not counted as a session.
17. `test_lifecycle_handles_corrupt_session` — one session has corrupt JSONL, error collected, other sessions still processed.

**Config:**

18. `test_compaction_config_new_fields` — verify `records_per_session` and `sessions_to_keep` defaults.
19. `test_compaction_config_legacy_keep_recent_migrated` — old config with `keep_recent` mapped to `records_per_session`.

**CLI:**

20. `test_compact_cli_command_exists` — `weave compact --help` doesn't crash.
21. `test_compact_cli_runs_lifecycle` — invoke via CliRunner, verify result output.

### Extensions to existing test files

**`tests/test_runtime.py`:**

22. `test_record_passes_compact_threshold_to_append_activity` — verify `_record` passes `compact_threshold=config.sessions.compaction.records_per_session` to `append_activity` (mock or spy approach).

### Running tally

- Current baseline: **179 tests**.
- New file: `test_compaction.py` ~21 tests.
- Extension: `test_runtime.py` +1 test.
- **Target: 179 + 22 = 201 tests.**

The plan will reconcile this precisely per task.

## Files changed / added

| Path | Change |
|---|---|
| `src/weave/core/compaction.py` | **NEW** — `_maybe_compact_session`, `_build_compaction_summary`, `compact_sessions`, `_build_ledger_entry`, `_delete_session_files`, `_append_ledger`, `CompactResult` |
| `src/weave/core/session.py` | **MODIFIED** — `append_activity` gains optional `compact_threshold` parameter |
| `src/weave/core/runtime.py` | **MODIFIED** — `_record()` passes `compact_threshold` to `append_activity` |
| `src/weave/schemas/config.py` | **MODIFIED** — `CompactionConfig` fields renamed, `archive_dir` removed |
| `src/weave/core/config.py` | **MODIFIED** — legacy key migration for `keep_recent` → `records_per_session`, drop `archive_dir` |
| `src/weave/cli.py` | **MODIFIED** — new `compact` command |
| `tests/test_compaction.py` | **NEW** — ~21 tests |
| `tests/test_runtime.py` | **MODIFIED** — +1 test |

## Open questions (to resolve in the plan, not this spec)

- Whether `_maybe_compact_session` should be called inside `append_activity` or immediately after it in `_record`. The spec says inside `append_activity` for encapsulation, but the plan may find it cleaner to call from `_record` to avoid changing `append_activity`'s contract. Either works; the plan decides.
- Whether `compact_sessions` should accept the full config object or just the two integers. Passing the config is more future-proof; passing integers is simpler. Plan decides.

## Self-review notes

**Spec coverage:**
- Both subsystems (within-session rolling compaction, cross-session lifecycle) are fully designed with algorithms, data shapes, and error handling.
- Config change is specified with migration path.
- CLI command with `--dry-run` is specified.
- Ordering invariant (ledger before delete) is called out explicitly.
- All 22 tests enumerated with specific assertions.

**Placeholder scan:** No TBDs, TODOs, or vague references. Every algorithm step has concrete logic.

**Internal consistency:**
- `CompactionConfig.records_per_session` is used by Subsystem A's `_maybe_compact_session` and Subsystem B is controlled by `sessions_to_keep`. No cross-contamination.
- The summary shape in Subsystem A matches the fields that Subsystem B's `_build_ledger_entry` reads when folding compacted stats into the ledger.
- `ActivityType.system` and `task="compaction_summary"` are used consistently as the discriminator for summary records.

**Scope check:** Single implementation plan. One new module (`compaction.py`), one new CLI command, targeted edits to 4 existing files, one new test file. Not too large.

**Ambiguity check:** Two open questions flagged for the plan (call site of `_maybe_compact_session`, parameter style for `compact_sessions`). Everything else is explicit.
