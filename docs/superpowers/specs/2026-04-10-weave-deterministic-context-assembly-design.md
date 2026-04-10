# MAR-142 Design: Deterministic Context Assembly

- **Date:** 2026-04-10
- **Status:** Approved
- **Linear:** [MAR-142](https://linear.app/martymanny/issue/MAR-142)
- **Milestone:** Phase 2 — Runtime Discipline
- **Scope:** Replace the ad-hoc `_load_context()` helper with a dedicated `ContextAssembly` type and `assemble_context()` function that produces byte-stable, canonically-ordered context with cache-key hashes.

## Context

From the Codex/OpenClaw audit:

> OpenClaw explicitly protects prompt-cache stability and deterministic request assembly. It sorts cache-sensitive inputs and avoids churning older prompt bytes unless necessary.

Today `runtime._load_context()` concatenates sorted markdown files with `"\n---\n"` separators. That is a good start for deterministic ordering, but it lacks:

1. A formal separation between stable prefix (project foundation) and volatile per-turn content
2. Computable hashes for use as cache keys and session-binding identifiers
3. Line ending normalization to protect cross-platform byte stability
4. A dedicated type that downstream consumers (MAR-141 session binding) can import and hash

MAR-142 formalizes the concept with a schema type and a pure function, keeping `runtime.py` focused on orchestration.

## Architecture

### Module split

Two new files, following the existing Phase 1 / Phase 2 pattern where schema types live in `src/weave/schemas/` and implementation logic lives in `src/weave/core/`:

- `src/weave/schemas/context.py` — `ContextAssembly` Pydantic model
- `src/weave/core/context.py` — `assemble_context(working_dir)` function

`runtime.py` imports both, deletes its private `_load_context()` helper, and stores the full `ContextAssembly` on `PreparedContext`.

### `ContextAssembly` schema

```python
class ContextAssembly(BaseModel):
    """Deterministic assembly of project context."""
    stable_prefix: str        # concatenated .harness/context/*.md, canonical ordering
    volatile_task: str = ""   # per-turn supplemental context (Phase 3 extension point)
    full: str                 # stable_prefix + separator + volatile_task (if non-empty)
    stable_hash: str          # sha256 hex of stable_prefix (cache key)
    full_hash: str            # sha256 hex of full payload
    source_files: list[str] = Field(default_factory=list)  # canonical ordering used
```

**Field semantics:**

- `stable_prefix` — the concatenated content of `.harness/context/*.md` files in canonical order, line endings normalized. This is the portion meant to benefit from prompt cache reuse.
- `volatile_task` — per-turn supplemental context. In Phase 2.3 this is always `""`; Phase 3 will populate it with per-invocation content like retrieved memory or git diff summaries.
- `full` — the complete payload sent to the adapter. When `volatile_task == ""`, `full == stable_prefix` exactly (no trailing separator).
- `stable_hash` — sha256 hex digest of `stable_prefix`, used as a cache key and consumed by MAR-141's session binding hashes.
- `full_hash` — sha256 hex digest of `full`. In Phase 2.3 `full_hash == stable_hash` (because the contents are identical); the fields are kept distinct so Phase 3 can diverge them without schema changes.
- `source_files` — diagnostic list of the source filenames in the exact order they were concatenated. Lets tests assert canonical ordering without re-deriving it.

## Assembly Logic

### Canonical ordering

```python
_CANONICAL_ORDER = ["conventions.md", "brief.md", "spec.md"]
_SEPARATOR = "\n---\n"
```

Ordering rules applied by `assemble_context()`:

1. Files in `_CANONICAL_ORDER` appear first, in that exact order
2. Remaining `*.md` files follow in alphabetical order
3. Hidden files (starting with `.`) are excluded
4. Missing canonical files are silently skipped — an operator without a `brief.md` still gets a valid assembly

This ordering matches the existing `.harness/context/` convention while making the precedence explicit and machine-enforceable.

### Content normalization

1. Each file is read as UTF-8
2. Line endings normalized: `\r\n` → `\n`, then `\r` → `\n`
3. Normalized contents joined with `_SEPARATOR` (`"\n---\n"`)
4. No trailing newline or whitespace is added — `_SEPARATOR.join(parts)` produces exact output

**Rationale for line ending normalization:** the same markdown file checked out on Windows (CRLF) vs Linux (LF) produces different bytes, which would produce different hashes and defeat prompt cache stability. Normalization ensures byte-identical output regardless of source OS.

### Empty and missing cases

- `.harness/context/` exists but contains no non-hidden markdown files → return `_empty_assembly()`
- `.harness/context/` does not exist at all → return `_empty_assembly()`
- `_empty_assembly()` returns `ContextAssembly(stable_prefix="", volatile_task="", full="", stable_hash=sha256(""), full_hash=sha256(""), source_files=[])`

An empty assembly is well-formed (not `None`), consistent (hashes are the sha256 of the empty string), and safe to store on `PreparedContext`.

### Error handling

Read errors (`OSError`, `UnicodeDecodeError`) are NOT caught. They propagate out of `assemble_context()` and fail `prepare()`. A broken context file should surface as an error at invocation time rather than silently hashing to a different value. This is a deliberate failure mode — better loud than lossy.

## Runtime Integration

### `PreparedContext` field change

```python
@dataclass
class PreparedContext:
    """Everything the pipeline needs after the prepare stage."""
    config: WeaveConfig
    active_provider: str
    provider_config: ProviderConfig
    adapter_script: Path
    context: ContextAssembly          # was: context_text: str
    session_id: str
    working_dir: Path
    phase: str
    task: str
    caller: str | None
    requested_risk_class: RiskClass | None
    pre_invoke_untracked: set[str]
```

### `prepare()` change

Replace the line `context_text = _load_context(working_dir)` with `context = assemble_context(working_dir)`, and update the `PreparedContext(...)` construction to pass `context=context` instead of `context_text=context_text`. Delete the old `_load_context()` private function.

### `execute()` change

The single call site that reads `ctx.context_text` is in `execute()` where it passes context to the invoker. Change:

```python
invoke_result = invoke_provider(
    adapter_script=ctx.adapter_script,
    task=ctx.task,
    working_dir=ctx.working_dir,
    context=ctx.context.full,   # was: context=ctx.context_text
    timeout=timeout,
)
```

### What does NOT change

- `src/weave/core/invoker.py` — untouched. Invoker still receives `context: str` and embeds it in the adapter JSON payload as `{"context": "..."}`.
- Adapter scripts (`.harness/providers/*.sh`) — untouched. They still see a single `context` JSON field and read it as an opaque string.
- `ActivityRecord` — untouched. Phase 2.3 does not log context hashes in activity records. MAR-141 session binding will consume them directly via `PreparedContext.context`.
- Other runtime stages (`_policy_check`, `_security_scan`, `_cleanup`, `_revert`, `_record`) — untouched.
- `.harness/config.json` schema — untouched.

## Design Decisions and Rationale

### Why only markdown files in the stable prefix

The Codex/OpenClaw audit's point about deterministic assembly is primarily about prompt cache stability — keeping a byte-stable prefix long enough to benefit provider-side prompt caching. The markdown files in `.harness/context/` are the natural candidate because they (1) already live on disk, (2) change rarely, (3) have a clear purpose as project background.

Tool catalog and provider metadata are better handled by the upcoming MAR-141 session binding hashes, which are explicitly designed to invalidate sessions on config/provider/capability changes. Baking those into the stable prefix here would duplicate the invalidation surface — a config change would invalidate both the stable prefix hash and the session binding hash. One job per field.

### Why the invoker signature is unchanged

The invoker's job is "run the adapter and capture results," not "understand context structure." Adding `ContextAssembly` to the invoker's type surface would couple it to a concept it does not need. No adapter script today reads the `context` field as anything other than an opaque blob. Splitting it into `stable`/`volatile` at the JSON level is speculative — we have no current consumer.

Runtime extracts `ctx.context.full` at the call site. Backwards compatible, minimal blast radius.

### Why `volatile_task` is empty in Phase 2.3

There is no current producer of volatile content. Introducing a populated field for a use case that does not exist is premature. The schema field exists as a Phase 3 extension point so that future work (retrieved memory, git diff summaries, recent files changed) can populate it without breaking the contract.

Keeping `volatile_task == ""` in Phase 2.3 means `full == stable_prefix` exactly, and therefore `full_hash == stable_hash`. The fields are kept distinct anyway because equality is a Phase 2.3 artifact, not a schema guarantee.

### Why `source_files` is a field and not a method

`ContextAssembly` is a Pydantic model — it serializes for logging and storage. Including `source_files` as a data field means the canonical ordering is visible in activity records and session JSONL without requiring consumers to re-run `assemble_context()`. It is also testable via simple equality assertions.

## Backwards Compatibility

- `PreparedContext.context_text` is renamed to `PreparedContext.context`. The type changes from `str` to `ContextAssembly`. Any caller reading `ctx.context_text` must switch to `ctx.context.full`.
- Grep of the codebase confirms the only reader is `execute()` in `runtime.py`, which is updated in Section 3.
- Test fixtures that construct `PreparedContext` directly (none exist — all current tests go through `prepare()`) would need updates.
- No change to Phase 1's `invoke` adapter contract, so `.harness/providers/*.sh` scripts are untouched.
- All 102 existing tests (95 Phase 1 + MAR-140 + 7 MAR-139) must continue to pass.

## Tests

### Unit tests (`tests/test_context.py` — new file)

1. **`test_assemble_context_canonical_ordering`**
   - Setup: create `brief.md`, `spec.md`, `conventions.md`, `extra.md`, `another.md` in `.harness/context/`
   - Expected: `source_files == ["conventions.md", "brief.md", "spec.md", "another.md", "extra.md"]`
   - Proves: canonical files come first in the defined order, rest alphabetical

2. **`test_assemble_context_byte_stable_across_runs`**
   - Setup: create three markdown files with fixed content
   - Call `assemble_context()` twice on the same directory
   - Expected: both returns have identical `full_hash`, `stable_prefix`, `full`, and `source_files`
   - Proves: no nondeterminism from file iteration order, timestamps, or other sources

3. **`test_assemble_context_normalizes_line_endings`**
   - Setup: create two directories — one with a CRLF-endings file, one with LF-endings file containing identical semantic content
   - Expected: the two `stable_hash` values are equal despite different source bytes
   - Proves: line ending normalization produces byte-identical output regardless of source OS

4. **`test_assemble_context_empty_directory`**
   - Setup: create `.harness/context/` with no markdown files (or only hidden files like `.hashes.json`)
   - Expected: returns `ContextAssembly` with empty strings and `sha256("")` for both hashes
   - Proves: empty case is well-formed

5. **`test_assemble_context_missing_context_dir`**
   - Setup: `working_dir` exists but has no `.harness/context/` subdirectory
   - Expected: same as empty-directory case — empty `ContextAssembly`
   - Proves: missing directory is graceful, not an exception

6. **`test_assemble_context_skips_hidden_files`**
   - Setup: create `spec.md`, `.hashes.json`, `.draft.md`
   - Expected: `source_files == ["spec.md"]` only
   - Proves: hidden file filter works

### Integration tests (`tests/test_runtime.py` — additions)

7. **`test_prepare_populates_context_assembly`**
   - Setup: initialize harness
   - Expected: `ctx.context` is a `ContextAssembly` instance, `ctx.context.full` is a non-empty string, `ctx.context.stable_hash` is a 64-character hex string, `ctx.context.source_files` contains at least one of the canonical file names
   - Proves: `prepare()` threads `assemble_context()` through to `PreparedContext.context`

8. **`test_execute_still_passes_context_string_to_invoker`**
   - Setup: minimal harness with a stub adapter that echoes back the received `context` field from its stdin JSON payload
   - Expected: after `execute()`, the adapter's captured context string equals `ctx.context.full`
   - Proves: the invoker contract is preserved — it still receives a plain string, not a `ContextAssembly` object

### Regression verification

All 102 existing tests must pass unchanged. The critical regression risk is any test that directly accessed `ctx.context_text` — none exist in the current codebase.

## File Map

| File | Action | Role |
|------|--------|------|
| `src/weave/schemas/context.py` | NEW | `ContextAssembly` Pydantic model |
| `src/weave/core/context.py` | NEW | `assemble_context(working_dir)` function + canonical ordering constants |
| `src/weave/core/runtime.py` | MODIFY | Delete `_load_context`, rename `PreparedContext.context_text` → `context`, update `prepare()` + `execute()`, add imports |
| `tests/test_context.py` | NEW | 6 unit tests |
| `tests/test_runtime.py` | MODIFY | 2 integration tests |

## Out of Scope

- Volatile context population (Phase 3 — retrieved memory, git diffs, recent files changed)
- Tool catalog or provider metadata in the stable prefix (belongs to MAR-141)
- Hot-reload of context files during an invocation (resolved once per invocation, same as config)
- Activity record integration of context hashes (trivial additive change, not needed until MAR-141)
- Streaming or lazy context loading (context files are small — overkill)
- Context compression or truncation (Phase 3 transcript hygiene work, not this task)
- Exposing `ContextAssembly` to adapter scripts (no consumer — Phase 3+)

## Acceptance Criteria

- `src/weave/schemas/context.py` exists and defines `ContextAssembly` with all six fields
- `src/weave/core/context.py` exists and exports `assemble_context(working_dir: Path) -> ContextAssembly`
- `assemble_context` produces byte-identical output for identical inputs across runs
- Canonical file ordering is enforced: `conventions.md`, `brief.md`, `spec.md`, then alphabetical
- Line endings are normalized to `\n`
- Hidden files are excluded
- Empty and missing directories produce well-formed empty assemblies
- `PreparedContext.context_text: str` is replaced by `PreparedContext.context: ContextAssembly`
- `_load_context()` is deleted from `runtime.py`
- `prepare()` calls `assemble_context()` and stores the full object on the context field
- `execute()` passes `ctx.context.full` to `invoke_provider`
- `invoker.py` is unchanged
- Adapter scripts are unchanged
- 6 unit tests in `test_context.py` pass
- 2 new integration tests in `test_runtime.py` pass
- All 102 pre-existing tests pass unchanged
- Expected final test count: 110 passing
