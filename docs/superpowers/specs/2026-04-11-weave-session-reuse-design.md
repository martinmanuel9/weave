# Design: Session Reuse + Binding Validation

**Date:** 2026-04-11
**Phase:** 4 (item 4.4)
**Status:** draft
**Extends:** [2026-04-10 Session Binding Hashes](2026-04-10-weave-session-binding-hashes-design.md) (MAR-141)

## Problem

Session binding sidecars have been written on every invocation since Phase 2 (MAR-141) but never read back. `validate_session()` exists as a pure comparison function but nothing calls it. When a session is reused (same session_id across multiple invocations), the runtime has no way to detect that the config, context, or provider has changed since the session was created. This means a session can silently drift from its original conditions.

## Goals

1. **Activate binding validation** — when `prepare()` receives an existing session_id, validate the stored binding against the current context.
2. **Configurable response to drift** — `warn` (log and continue), `rebind` (silently update), or `strict` (refuse to proceed). Default: `warn`.
3. **Enable session reuse through the runtime** — `prepare()` and `execute()` accept an optional `session_id` parameter so callers can reuse sessions without creating them externally.

## Non-goals

- Automatic session resume (detecting the "last" session and reusing it).
- Session locking (preventing concurrent use of the same session_id).
- Migrating existing session bindings when config changes.

## Schema changes

### `SessionBindingPolicy` enum (in `schemas/config.py`)

```python
class SessionBindingPolicy(str, Enum):
    WARN = "warn"
    REBIND = "rebind"
    STRICT = "strict"
```

### `SessionsConfig` extension

```python
class SessionsConfig(BaseModel):
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    binding_policy: SessionBindingPolicy = SessionBindingPolicy.WARN
```

No migration needed — new field with default.

## Runtime changes

### `prepare()` new signature

```python
def prepare(
    task: str,
    working_dir: Path,
    provider: str | None = None,
    caller: str | None = None,
    requested_risk_class: RiskClass | None = None,
    session_id: str | None = None,
) -> PreparedContext:
```

When `session_id` is None: current behavior — create a new session via `create_session()`, write a fresh binding.

When `session_id` is provided:
1. Use the provided session_id (don't call `create_session()`).
2. Call `read_binding(session_id, sessions_dir)`.
3. If binding exists: call `validate_session(session_id, ctx, sessions_dir)` to get mismatches.
   - If mismatches and policy is `warn`: log a warning per mismatch, write a new binding (overwrite).
   - If mismatches and policy is `rebind`: log at info level, write a new binding (overwrite).
   - If mismatches and policy is `strict`: raise `ValueError` listing all mismatches.
   - If no mismatches: proceed without writing (binding is still valid).
4. If binding doesn't exist (FileNotFoundError from read_binding, or returns None): write a fresh binding. This handles the first invocation of an externally-created session (e.g., GSD bridge's `session-start`).

### `execute()` new signature

```python
def execute(
    task: str,
    working_dir: Path,
    provider: str | None = None,
    caller: str | None = None,
    requested_risk_class: RiskClass | None = None,
    timeout: int = 300,
    session_id: str | None = None,
) -> RuntimeResult:
```

Passes `session_id` through to `prepare()`.

## Error handling

| Condition | Behavior |
|---|---|
| `session_id=None` | Create new session, write binding (unchanged) |
| `session_id` provided, no binding file | Write fresh binding, proceed |
| `session_id` provided, binding matches | Proceed, no rewrite |
| `session_id` provided, binding drifted, policy=warn | Log warning per mismatch, rewrite binding, proceed |
| `session_id` provided, binding drifted, policy=rebind | Log info, rewrite binding, proceed |
| `session_id` provided, binding drifted, policy=strict | Raise ValueError listing mismatches |
| Binding file corrupt (malformed JSON) | Treat as missing — write fresh binding |

## Test plan

5 new tests in `tests/test_session_binding.py`:

1. `test_prepare_with_session_id_validates_binding_warn` — provide session_id with a stale binding (config changed), policy=warn → warning logged, invocation proceeds, new binding written.
2. `test_prepare_with_session_id_validates_binding_rebind` — same setup, policy=rebind → info logged, no warning, new binding written.
3. `test_prepare_with_session_id_validates_binding_strict` — same setup, policy=strict → ValueError raised.
4. `test_prepare_with_session_id_missing_binding` — session_id provided but no sidecar file → fresh binding written, no error.
5. `test_prepare_without_session_id_unchanged` — session_id=None → new session created (regression check).

**Target: 244 + 5 = 249 tests.**

## Files changed

| Path | Change |
|---|---|
| `src/weave/schemas/config.py` | Add `SessionBindingPolicy` enum, field on `SessionsConfig` |
| `src/weave/core/runtime.py` | `prepare()` and `execute()` gain `session_id` param, validation logic |
| `tests/test_session_binding.py` | +5 tests |
