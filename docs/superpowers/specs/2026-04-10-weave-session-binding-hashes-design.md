# MAR-141 Design: Session Binding Hashes

- **Date:** 2026-04-10
- **Status:** Approved
- **Linear:** [MAR-141](https://linear.app/martymanny/issue/MAR-141)
- **Milestone:** Phase 2 — Runtime Discipline
- **Scope:** Write a compatibility fingerprint (the "session binding") next to every session's JSONL log, and provide a `validate_session()` function that compares a stored binding against a current `PreparedContext`. Phase 2.2 delivers the foundation; actual session reuse is not implemented yet.

## Context

From the Codex/OpenClaw audit:

> OpenClaw does not just reuse sessions; it invalidates reuse when auth profile, auth epoch, system prompt hash, or MCP config hash changes.

Weave sessions today are one-shot: `create_session()` returns a new UUID4 every invocation, there is no lookup or reuse mechanism, and sessions are append-only JSONL files with no compatibility metadata. If a future caller (itzel daemon, retry logic, session resume) wants to reuse a session safely, it needs a way to detect that the session's creation-time inputs have drifted.

MAR-141 lays that foundation. Every session gets a `.binding.json` sidecar capturing the hashes of provider, adapter script, context, and config at creation time. A `validate_session()` function compares the stored binding against a current `PreparedContext` and returns the list of mismatched field names — empty list means reusable.

Importantly: **MAR-141 does not add session reuse logic to `execute()`.** No new parameters, no gating, no behavior changes for existing callers. The binding is written and the validator exists; what consumes them is deferred to a future task with a concrete use case.

## Architecture

### Module split

Following the MAR-142 pattern exactly. Schema types live in `src/weave/schemas/`, implementation logic lives in `src/weave/core/`, runtime integration is one new call in `prepare()`.

- `src/weave/schemas/session_binding.py` — `SessionBinding` Pydantic model
- `src/weave/core/session_binding.py` — `compute_binding`, `write_binding`, `read_binding`, `validate_session`, and private `_hash_config` helper

Naming note: the files are called `session_binding.py` rather than `binding.py` to avoid ambiguity with the existing `src/weave/core/session.py` (which handles JSONL append). `SessionBinding` concerns are genuinely adjacent to but distinct from activity-stream concerns, and mixing them would grow `session.py` significantly.

### `SessionBinding` schema

```python
class SessionBinding(BaseModel):
    """Compatibility fingerprint of a session's creation-time inputs."""
    session_id: str
    created_at: datetime
    provider_name: str
    adapter_script_hash: str
    context_stable_hash: str
    config_hash: str
```

**Field semantics:**

- `session_id` — matches the `.jsonl` basename exactly. The sidecar is named `<session_id>.binding.json`.
- `created_at` — timezone-aware UTC datetime captured at binding creation time. Pydantic serializes to ISO-8601 in JSON.
- `provider_name` — plain string equality (not a hash). Reusing a session bound to one provider against a different provider would be meaningless.
- `adapter_script_hash` — sha256 hex of the adapter script file's raw bytes. Catches adapter script edits.
- `context_stable_hash` — pulled directly from `ContextAssembly.stable_hash` (already computed by `assemble_context()` in MAR-142). No re-computation needed.
- `config_hash` — sha256 hex of the canonicalized `WeaveConfig` JSON (details below).

No `tool_catalog_hash` or `memory_config_hash`. Neither concept exists in Phase 2 — weave has no tool registry distinct from `WeaveConfig.providers` (already covered by `config_hash`), and Open Brain memory config lives in environment variables, not in the session. Phase 3 can add those fields if the concepts materialize.

## Binding Computation

### `compute_binding(ctx: PreparedContext) -> SessionBinding`

No filesystem writes. Produces a `SessionBinding` from an already-constructed `PreparedContext` by reading data already on the context object. Not strictly pure because `created_at` is populated from wall-clock time via `datetime.now(timezone.utc)` — but `created_at` is an identity field excluded from compatibility comparisons, so the four hash/name fields that `validate_session()` actually checks are deterministic for identical inputs.

```python
def compute_binding(ctx: PreparedContext) -> SessionBinding:
    """Build a SessionBinding from a PreparedContext (no side effects)."""
    # Adapter script hash — read raw bytes if the file exists,
    # else use sha256("") as a sentinel for "no adapter."
    if ctx.adapter_script.exists():
        adapter_bytes = ctx.adapter_script.read_bytes()
        adapter_script_hash = hashlib.sha256(adapter_bytes).hexdigest()
    else:
        adapter_script_hash = hashlib.sha256(b"").hexdigest()

    return SessionBinding(
        session_id=ctx.session_id,
        created_at=datetime.now(timezone.utc),
        provider_name=ctx.active_provider,
        adapter_script_hash=adapter_script_hash,
        context_stable_hash=ctx.context.stable_hash,
        config_hash=_hash_config(ctx.config),
    )
```

**Error handling for missing adapter:** using `sha256(b"")` keeps the binding well-formed even when scaffolding is incomplete. The binding reflects "no adapter present at creation time" rather than raising — which matches the existing runtime's behavior of happily creating a session even when the adapter script is missing (the invoke stage handles the failure, not `prepare()`).

### `_hash_config(config: WeaveConfig) -> str`

Private helper that produces the canonical hash of a `WeaveConfig`.

```python
def _hash_config(config: WeaveConfig) -> str:
    """Compute a byte-stable sha256 of the config as canonicalized JSON."""
    data = config.model_dump(mode="json")
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

**Why canonical JSON instead of `model_dump_json()` directly:**

- Pydantic v2's `model_dump_json()` respects field declaration order (not alphabetical), which is stable across runs in practice but not a documented guarantee.
- `WeaveConfig.providers` is `dict[str, ProviderConfig]`. Python dict iteration is insertion-ordered; in edge cases (future Pydantic versions, config merged from multiple sources), the same logical config could serialize with different key ordering.
- `json.dumps(sort_keys=True, separators=(",", ":"))` removes both risks. Keys always sort alphabetically, separators have no whitespace variance, and the output is deterministic.

**Pessimistic scope:** the full config is hashed, including fields that may not be behavior-shaping (e.g., `logging.level`). This is deliberate — we don't have enough operator feedback yet to know which fields should be curated. Pessimism is cheap: over-invalidation means a fresh session, not a broken run. Future work can move to a curated subset if operators report frustration.

## Sidecar I/O

### `write_binding(binding: SessionBinding, sessions_dir: Path) -> Path`

Serializes the binding to `{sessions_dir}/{binding.session_id}.binding.json` via `model_dump_json(indent=2)` for human readability. Creates `sessions_dir` if it doesn't exist (matching `append_activity`'s behavior). Returns the written path.

### `read_binding(session_id: str, sessions_dir: Path) -> SessionBinding | None`

Loads and parses the sidecar at `{sessions_dir}/{session_id}.binding.json`.

- File doesn't exist: returns `None`
- File exists but is malformed (invalid JSON or fails Pydantic validation): raises. A broken binding is an operator-facing error, not silently ignorable.

### `validate_session(session_id, ctx, sessions_dir) -> list[str]`

The consumer-facing comparison function. Loads the stored binding and compares the four compatibility fields against a `PreparedContext`. Returns a list of mismatched field names (empty list = reusable).

```python
def validate_session(
    session_id: str,
    ctx: PreparedContext,
    sessions_dir: Path,
) -> list[str]:
    """Return the list of binding field names that differ between the
    stored binding and the current PreparedContext.

    Empty list means the session is reusable against ctx. Non-empty
    means one or more invalidating inputs changed.

    Raises FileNotFoundError if the binding sidecar does not exist —
    a nonexistent binding is qualitatively different from a mismatched
    binding. Callers should treat them as distinct signals.
    """
    binding = read_binding(session_id, sessions_dir)
    if binding is None:
        raise FileNotFoundError(f"No binding sidecar for session {session_id}")

    current = compute_binding(ctx)
    mismatches: list[str] = []
    if binding.provider_name != current.provider_name:
        mismatches.append("provider_name")
    if binding.adapter_script_hash != current.adapter_script_hash:
        mismatches.append("adapter_script_hash")
    if binding.context_stable_hash != current.context_stable_hash:
        mismatches.append("context_stable_hash")
    if binding.config_hash != current.config_hash:
        mismatches.append("config_hash")
    return mismatches
```

**Design notes:**

- `session_id` and `created_at` are identity fields, not compatibility fields. They're intentionally excluded from the comparison — a binding compared against itself (same session_id, same created_at) is the "matches itself" case. What we're checking is whether the session's _inputs_ have drifted, not whether the binding is the same binding.
- The explicit field-by-field comparison is deliberate. We could use `model_dump()` + dict comparison, but spelling out the four fields makes the "what counts as compatibility" decision visible in the code, not hidden in a serialization layer. A future field addition is a one-line change, and the spelling-out makes it grep-discoverable.
- `FileNotFoundError` vs. a sentinel like `["binding_missing"]`: missing and mismatched are semantically different. A missing binding means the session was created before MAR-141 (or the sidecar was deleted) — the caller needs to know whether to create a new session vs. whether to retry with fresh state. Exceptions distinguish the cases cleanly.

## Runtime Integration

Exactly one call site changes: the end of `prepare()` in `src/weave/core/runtime.py`.

### Before

```python
    adapter_script = working_dir / ".harness" / "providers" / f"{active_provider}.sh"
    context = assemble_context(working_dir)
    session_id = create_session()
    pre_invoke_untracked = _snapshot_untracked(working_dir)

    return PreparedContext(
        config=config,
        # ... all fields ...
        pre_invoke_untracked=pre_invoke_untracked,
    )
```

### After

```python
    adapter_script = working_dir / ".harness" / "providers" / f"{active_provider}.sh"
    context = assemble_context(working_dir)
    session_id = create_session()
    pre_invoke_untracked = _snapshot_untracked(working_dir)

    prepared = PreparedContext(
        config=config,
        # ... all fields ...
        pre_invoke_untracked=pre_invoke_untracked,
    )

    # Write the session binding sidecar
    binding = compute_binding(prepared)
    sessions_dir = working_dir / ".harness" / "sessions"
    write_binding(binding, sessions_dir)

    return prepared
```

**Why the local variable:** `compute_binding(ctx)` needs the fully-constructed `PreparedContext` to read `ctx.adapter_script`, `ctx.context.stable_hash`, and `ctx.config`. Constructing the context, passing it to `compute_binding`, and then returning it is cleaner than inlining everything.

### New imports

```python
from weave.core.session_binding import compute_binding, write_binding
```

Added after `from weave.core.session import append_activity, create_session` to maintain alphabetical order within the `weave.core.*` import group.

### What does NOT change

- `PreparedContext` dataclass — unchanged. The binding is computed on demand from existing fields, not stored on the context.
- `_policy_check`, `_security_scan`, `_cleanup`, `_revert`, `_record`, `execute()` — all unchanged. The binding is written during `prepare()` and consumed only by future callers of `validate_session()`.
- `invoker.py`, adapter scripts, `ActivityRecord` schema — all untouched.
- No new `execute()` parameters. There is no `reuse_session_id` argument.

### Error handling in `prepare()`

If `write_binding` fails (disk full, permission denied), the exception propagates out of `prepare()` and kills the invocation. This is deliberate — a session that can't write its binding is a session that future reuse checks can't validate. Better to fail loudly at creation than to silently produce sessions with no binding metadata.

## Backwards Compatibility

- All 111 existing tests continue to pass unchanged
- Old sessions without bindings still work — nothing reads bindings except tests
- `PreparedContext` dataclass unchanged
- No adapter or invoker changes
- `validate_session()` raising `FileNotFoundError` is the correct behavior for legacy sessions — they don't have a binding, and that's a distinguishable signal for future consumers
- MAR-139's file revert tests use git-initialized working directories; the new `write_binding` call adds a new `.binding.json` file inside `.harness/sessions/`. This file does not match any write deny list pattern and does not trigger any supply chain scanner rule (all rules require code fragments, not filenames), so existing MAR-139 tests pass unchanged

## Tests

### Unit tests (`tests/test_session_binding.py` — new file)

1. **`test_compute_binding_produces_all_fields`**
   - Setup: build a `PreparedContext` via `prepare()` on an initialized harness
   - Expected: returned `SessionBinding` has all six fields populated, all hashes are 64-character hex strings, `provider_name == ctx.active_provider`
   - Proves: basic construction works end-to-end

2. **`test_compute_binding_uses_context_stable_hash`**
   - Setup: prepare a context with known content
   - Expected: `binding.context_stable_hash == ctx.context.stable_hash` exactly
   - Proves: we reuse MAR-142's hash rather than recomputing

3. **`test_compute_binding_config_hash_is_canonical`**
   - Setup: construct two `WeaveConfig` instances with semantically identical data but different internal ordering (e.g., providers added in reverse order via model construction)
   - Expected: both produce the same `config_hash`
   - Proves: canonical JSON ordering via `json.dumps(sort_keys=True)` eliminates ordering instability

4. **`test_write_and_read_binding_round_trip`**
   - Setup: build a `SessionBinding`, `write_binding()` to a temp dir, then `read_binding()` back
   - Expected: the loaded binding equals the original (all fields, including `created_at`)
   - Proves: JSON serialization is lossless

5. **`test_validate_session_returns_empty_for_matching_binding`**
   - Setup: prepare context, compute binding, write it; prepare a NEW context from the same working_dir (nothing has changed on disk)
   - Expected: `validate_session(session_id, new_ctx, sessions_dir) == []`
   - Proves: identical inputs produce zero mismatches

6. **`test_validate_session_detects_config_hash_mismatch`**
   - Setup: prepare context, write binding. Modify `.harness/config.json` (e.g., change phase from "sandbox" to "mvp"), call `prepare()` again to get a new `ctx`
   - Expected: `validate_session(old_session_id, new_ctx, sessions_dir) == ["config_hash"]`
   - Proves: config changes are detected — this is the most important test, validating the whole point of session binding

### Integration tests (`tests/test_runtime.py` — additions)

7. **`test_prepare_writes_session_binding_sidecar`**
   - Setup: init harness, call `prepare()`
   - Expected: file `.harness/sessions/<session_id>.binding.json` exists on disk, parses as valid JSON, contains all six `SessionBinding` fields
   - Proves: `prepare()` actually writes the sidecar to disk as a side effect

8. **`test_validate_session_raises_for_missing_binding`**
   - Setup: init harness, call `prepare()` to get a ctx. Delete the binding sidecar. Call `validate_session()`.
   - Expected: raises `FileNotFoundError`
   - Proves: missing bindings are distinguished from mismatched bindings via exception

### Regression verification

All 111 existing tests must pass unchanged. Expected final count: **119 passing** (111 baseline + 8 new).

## File Map

| File | Action | Role |
|------|--------|------|
| `src/weave/schemas/session_binding.py` | NEW | `SessionBinding` Pydantic model |
| `src/weave/core/session_binding.py` | NEW | `compute_binding`, `write_binding`, `read_binding`, `validate_session`, `_hash_config` |
| `src/weave/core/runtime.py` | MODIFY | Two new imports; `prepare()` computes and writes the binding sidecar before returning |
| `tests/test_session_binding.py` | NEW | 6 unit tests |
| `tests/test_runtime.py` | MODIFY | 2 integration tests |

## Out of Scope

- **Actual session reuse logic in `execute()`** — deferred until a consumer has a concrete use case. No new `execute()` parameters.
- **`tool_catalog_hash`** — weave has no tool catalog concept distinct from `WeaveConfig.providers`, which is already covered by `config_hash`
- **`memory_config_hash`** — Open Brain config lives in environment variables, not in a hashable session object
- **Binding cleanup/lifecycle** — deferred to future Phase 3 session management work
- **Binding schema versioning** — a single format version is fine for now; multi-version migration can wait for a real need
- **Hot-reload of binding** — bindings are written once at `prepare()` and never updated; a session whose inputs change mid-run is considered a new session, not a reusable one
- **Curated config subset hashing** — hashing only "behavior-shaping" fields instead of the full config. Deferred until operator feedback shows real invalidation pain.

## Acceptance Criteria

- `src/weave/schemas/session_binding.py` exists and defines `SessionBinding` with all six fields
- `src/weave/core/session_binding.py` exists and exports `compute_binding`, `write_binding`, `read_binding`, `validate_session`
- `compute_binding()` is a pure function (no filesystem writes) and produces byte-stable output for identical `PreparedContext` inputs
- `_hash_config()` uses `json.dumps(sort_keys=True, separators=(",", ":"))` for canonical ordering
- Adapter script hash uses `sha256(b"")` as a fallback when the adapter file is missing
- `write_binding()` writes to `.harness/sessions/<session_id>.binding.json` with `indent=2`
- `read_binding()` returns `None` for missing files and raises for malformed files
- `validate_session()` returns a list of mismatched field names and raises `FileNotFoundError` for missing bindings
- The comparison compares exactly the four fields `provider_name`, `adapter_script_hash`, `context_stable_hash`, `config_hash` — not `session_id` or `created_at`
- `prepare()` calls `compute_binding()` and `write_binding()` at the end, before returning
- `execute()` and all other pipeline stages are unchanged
- `invoker.py` and adapter scripts are unchanged
- All 6 unit tests in `test_session_binding.py` pass
- All 2 new integration tests in `test_runtime.py` pass
- All 111 pre-existing tests pass unchanged
- Expected final test count: 119 passing
