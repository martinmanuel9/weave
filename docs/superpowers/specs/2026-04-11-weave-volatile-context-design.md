# Design: Volatile Context Population

**Date:** 2026-04-11
**Phase:** 4 (item 4.1)
**Status:** draft
**Extends:** [2026-04-10 Deterministic Context Assembly](2026-04-10-weave-deterministic-context-assembly-design.md) — the `volatile_task` extension point (line 51)

## Problem

Every weave invocation sends the adapter a `context` string assembled from `.harness/context/*.md` files — the project's conventions, brief, and spec. This is the *stable prefix*: deterministic, same on every run, good for prompt-cache stability.

But the adapter has no idea what just happened. It doesn't know what files changed since the last commit, what the recent git history looks like, or what previous invocations in this session did. Without this runtime context, the adapter operates blind — it can't build on previous work, avoid repeating mistakes, or understand the current project state.

The `ContextAssembly` schema has a `volatile_task` field that was left empty since Phase 2 as a "Phase 3 extension point." This spec populates it with per-invocation context from three data sources: git state, git history, and session activity.

## Goals

1. **Populate `volatile_task`** with runtime context assembled from local data sources (git, session history).
2. **Keep volatile separate from stable** — the stable prefix and its hash remain unchanged and cache-key-stable. Volatile content gets its own section after a `---` separator.
3. **Make it configurable** — a `VolatileContextConfig` with per-source toggles, per-source limits, and a global backstop. Users can disable volatile context entirely or tune which sources contribute.
4. **Best-effort, never blocking** — failure to gather any volatile source (git not installed, not a repo, session file corrupt) silently omits that source. An invocation should never fail because volatile context assembly failed.
5. **Keep modules focused** — new `core/volatile.py` owns all volatile context logic. `context.py` stays deterministic. `runtime.py` gets a 2-line change.

## Non-goals

- Volatile content from external sources (APIs, databases, web searches).
- LLM-driven summarization of volatile content (future enhancement, builds on this).
- Per-provider volatile content customization.
- Caching volatile content across invocations.
- Modifying the invoker's request payload schema — volatile goes into the same `context` string field.
- Task string echo into volatile context — the task is already in the request payload's `task` field.

## Architecture

### Assembly flow

```
prepare()
    │
    ├─ assemble_context(working_dir)                 ← existing, unchanged
    │     → ContextAssembly(stable_prefix, volatile_task="", full=stable_prefix)
    │
    ├─ build_volatile_context(working_dir, config,   ← NEW (core/volatile.py)
    │     session_id)
    │     │
    │     ├─ _git_diff_section(working_dir, max_files)
    │     ├─ _git_log_section(working_dir, max_entries)
    │     ├─ _activity_section(sessions_dir, session_id, max_records)
    │     │
    │     └─ join non-empty sections, truncate to max_total_chars
    │         → str (the volatile text)
    │
    ├─ assembly.with_volatile(volatile_text)          ← NEW method
    │     → ContextAssembly with volatile_task set,
    │       full = stable_prefix + separator + volatile_task,
    │       full_hash recomputed
    │
    └─ PreparedContext(..., context=assembly)
```

### What stays the same

- `assemble_context()` — untouched, still reads `.harness/context/*.md`
- `ContextAssembly` schema — same fields, `volatile_task` goes from always-empty to sometimes-populated
- `invoker.invoke_provider()` — still receives `context=ctx.context.full` as an opaque string
- Session binding — `compute_binding()` uses `context.stable_hash`, not `full_hash`, so volatile content doesn't affect binding stability

### What changes

- `prepare()` — 2 new lines after `assemble_context()` call
- `ContextAssembly` gains `with_volatile()` method
- New `VolatileContextConfig` on `WeaveConfig`
- New `core/volatile.py` module

## Schema changes

### `VolatileContextConfig` (new, in `schemas/config.py`)

```python
class VolatileContextConfig(BaseModel):
    enabled: bool = True
    git_diff_enabled: bool = True
    git_diff_max_files: int = 30
    git_log_enabled: bool = True
    git_log_max_entries: int = 10
    activity_enabled: bool = True
    activity_max_records: int = 5
    max_total_chars: int = 8000
```

### `WeaveConfig` extension

```python
class WeaveConfig(BaseModel):
    # ... existing fields ...
    volatile_context: VolatileContextConfig = Field(default_factory=VolatileContextConfig)
```

No migration needed — new field with defaults.

### `ContextAssembly.with_volatile()` method

```python
def with_volatile(self, volatile_text: str) -> "ContextAssembly":
    """Return a new ContextAssembly with volatile_task populated.

    Recomputes `full` and `full_hash`. `stable_prefix` and `stable_hash`
    are preserved unchanged.
    """
    if not volatile_text:
        return self

    full = self.stable_prefix + "\n---\n" + volatile_text
    full_hash = hashlib.sha256(full.encode("utf-8")).hexdigest()

    return ContextAssembly(
        stable_prefix=self.stable_prefix,
        volatile_task=volatile_text,
        full=full,
        stable_hash=self.stable_hash,
        full_hash=full_hash,
        source_files=self.source_files,
    )
```

The `\n---\n` separator matches the existing `_SEPARATOR` used between stable context files.

## Volatile sources

### New module: `src/weave/core/volatile.py`

Three source functions + one orchestrator. All source functions return empty string on any error — volatile context is best-effort.

### Source 1: `_git_diff_section(working_dir, max_files) -> str`

Runs `git diff --name-status HEAD` for tracked changes and `git ls-files --others --exclude-standard` for untracked files. Combines results.

Output format:

```
## Recent Git State

### Changed files (since last commit)
- src/weave/core/runtime.py (modified)
- tests/test_sandbox.py (new)
- old_file.py (deleted)
```

Implementation details:
- Maps git status codes to human labels: `M` → `modified`, `A` → `new`, `D` → `deleted`, `R` → `renamed`
- Untracked files from `ls-files` are labeled `new`
- Caps at `max_files` entries; adds `(and N more...)` if truncated
- Returns empty string if no changes
- Subprocess calls use `timeout=5`, `capture_output=True`, `text=True`
- Any `subprocess` failure (FileNotFoundError, TimeoutExpired, non-zero exit) → return empty string

### Source 2: `_git_log_section(working_dir, max_entries) -> str`

Runs `git log --oneline -N`.

Output format:

```
### Recent commits
- fe2765a feat(sandbox): environment restriction and expanded write-deny
- afe0ff9 feat(invoker): add env parameter for subprocess environment control
```

Implementation details:
- Each line from git log becomes a bullet point
- Returns empty string if no commits or not a git repo
- Same subprocess error handling as Source 1

### Source 3: `_activity_section(sessions_dir, session_id, max_records) -> str`

Reads the current session's JSONL, parses `ActivityRecord` objects, takes the last `max_records` non-summary records.

Output format:

```
## Session Activity

### Previous invocations
- [10:30:05] provider=claude-code task="implement auth middleware" status=success files=3 duration=45.2s
- [10:28:12] provider=claude-code task="write auth tests" status=success files=2 duration=22.1s
```

Implementation details:
- Reads `{sessions_dir}/{session_id}.jsonl`
- Filters out `compaction_summary` records (they're metadata, not useful for the adapter)
- Takes the last `max_records` records after filtering
- Formats each: `[HH:MM:SS] provider=X task="Y" status=Z files=N duration=Xs`
- Task string truncated to 60 chars
- Duration formatted as seconds with 1 decimal place
- Most recent first in output
- Returns empty string if session file doesn't exist, is empty, or all lines are corrupt
- Corrupt JSON lines are silently skipped

### Orchestrator: `build_volatile_context(working_dir, config, session_id) -> str`

```python
def build_volatile_context(
    working_dir: Path,
    config: VolatileContextConfig,
    session_id: str | None = None,
) -> str:
    """Assemble volatile context from enabled sources.

    Returns empty string if disabled or all sources are empty.
    """
    if not config.enabled:
        return ""

    sections: list[str] = []

    if config.git_diff_enabled:
        git_diff = _git_diff_section(working_dir, config.git_diff_max_files)
        if git_diff:
            sections.append(git_diff)

    if config.git_log_enabled:
        git_log = _git_log_section(working_dir, config.git_log_max_entries)
        if git_log:
            sections.append(git_log)

    if config.activity_enabled and session_id:
        sessions_dir = working_dir / ".harness" / "sessions"
        activity = _activity_section(
            sessions_dir, session_id, config.activity_max_records,
        )
        if activity:
            sections.append(activity)

    if not sections:
        return ""

    result = "\n\n".join(sections)

    if len(result) > config.max_total_chars:
        result = result[:config.max_total_chars] + "\n(volatile context truncated)"

    return result
```

### `prepare()` changes

After the existing `context = assemble_context(working_dir)` line:

```python
    volatile_text = build_volatile_context(
        working_dir=working_dir,
        config=config.volatile_context,
        session_id=session_id,
    )
    context = context.with_volatile(volatile_text)
```

Two lines. `session_id` is already available at this point in `prepare()` (created by `create_session()` on the line above).

## Error handling

| Condition | Where | Behavior |
|---|---|---|
| Not a git repo | `_git_diff_section`, `_git_log_section` | Return empty string |
| `git` command not found | Same | Return empty string |
| `git` command times out (5s) | Same | Return empty string |
| No changed files | `_git_diff_section` | Return empty string (section omitted) |
| No commits | `_git_log_section` | Return empty string |
| Session JSONL missing | `_activity_section` | Return empty string |
| Session JSONL corrupt | `_activity_section` | Skip corrupt lines, format parseable ones |
| All sources return empty | `build_volatile_context` | Return empty string → `with_volatile("")` is no-op |
| `enabled = False` | `build_volatile_context` | Return empty string immediately |
| Total exceeds `max_total_chars` | `build_volatile_context` | Truncate with `(volatile context truncated)` suffix |
| `session_id` is None | `build_volatile_context` | Skip activity section |

Every error path returns empty string. Volatile context never blocks an invocation.

## Test plan

### New test file: `tests/test_volatile.py`

**Git diff source:**
1. `test_git_diff_section_shows_modified_and_new_files` — init repo, commit, modify + create, verify output
2. `test_git_diff_section_caps_at_max_files` — 40 files, max=5, verify truncation message
3. `test_git_diff_section_empty_when_no_changes` — clean repo, returns empty
4. `test_git_diff_section_empty_when_not_git_repo` — no git, returns empty

**Git log source:**
5. `test_git_log_section_shows_recent_commits` — 3 commits, all shown
6. `test_git_log_section_caps_at_max_entries` — 10 commits, max=3, only 3
7. `test_git_log_section_empty_when_no_commits` — empty repo, returns empty

**Activity source:**
8. `test_activity_section_shows_recent_records` — 3 records, all shown, most recent first
9. `test_activity_section_caps_at_max_records` — 10 records, max=3, only 3
10. `test_activity_section_skips_compaction_summaries` — 2 real + 1 summary, only real shown
11. `test_activity_section_empty_when_no_session` — no file, returns empty

**Orchestrator:**
12. `test_build_volatile_context_combines_sources` — all sources active, all headers present
13. `test_build_volatile_context_disabled_returns_empty` — `enabled=False`
14. `test_build_volatile_context_omits_empty_sources` — git disabled, only activity in output
15. `test_build_volatile_context_truncates_at_global_limit` — `max_total_chars=100`, verify truncation

**ContextAssembly.with_volatile:**
16. `test_with_volatile_populates_fields` — verify volatile_task, full, full_hash set
17. `test_with_volatile_empty_is_noop` — returns unchanged assembly
18. `test_with_volatile_full_hash_differs_from_stable_hash` — hashes diverge

**Integration:**
19. `test_prepare_populates_volatile_context` — full project with git history + session, verify ctx.context.volatile_task is non-empty

### Running tally

- Current baseline: **217 tests**
- New file: `tests/test_volatile.py` — 19 tests
- **Target: 217 + 19 = 236 tests**

## Files changed / added

| Path | Change |
|---|---|
| `src/weave/core/volatile.py` | **NEW** — `_git_diff_section`, `_git_log_section`, `_activity_section`, `build_volatile_context` |
| `src/weave/schemas/context.py` | **MODIFIED** — `with_volatile()` method on `ContextAssembly` |
| `src/weave/schemas/config.py` | **MODIFIED** — `VolatileContextConfig`, `volatile_context` field on `WeaveConfig` |
| `src/weave/core/runtime.py` | **MODIFIED** — 2-line change in `prepare()` |
| `tests/test_volatile.py` | **NEW** — 19 tests |

## Open questions (to resolve in the plan, not this spec)

- Whether `_git_diff_section` should combine the `## Recent Git State` header with `### Changed files` into one section, or keep them as separate sub-headers under a shared parent. The spec shows the two-level header approach; the plan may simplify to one level if the output is cleaner.
- Whether `_activity_section` should show the session_id in the header (`### Previous invocations (session abc-123)`) or omit it (the adapter probably doesn't care about the session ID). The plan decides based on output readability.

## Self-review notes

**Spec coverage:**
- All three sources (git diff, git log, session activity) designed with format, limits, and error handling
- Orchestrator combines sources with per-source and global limits
- `ContextAssembly.with_volatile()` method preserves stable hash, recomputes full hash
- Config schema with all 8 fields + master switch
- `prepare()` integration is a 2-line change
- 19 tests across all sources, orchestrator, schema method, and integration

**Placeholder scan:** No TBDs or TODOs.

**Internal consistency:**
- `VolatileContextConfig` field names (`git_diff_max_files`, `git_log_max_entries`, `activity_max_records`) match the parameters of their corresponding source functions
- `with_volatile()` uses `\n---\n` separator matching `_SEPARATOR` in `context.py`
- `build_volatile_context` returns empty string → `with_volatile("")` returns self → `full == stable_prefix` → same behavior as pre-Phase-4

**Scope check:** Single plan. One new module, one new schema, one new method, a 2-line runtime change. Focused and bounded.
