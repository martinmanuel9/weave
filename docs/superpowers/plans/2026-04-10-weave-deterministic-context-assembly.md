# MAR-142 Implementation Plan — Deterministic Context Assembly

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ad-hoc `_load_context()` helper with a dedicated `ContextAssembly` type and `assemble_context()` function that produces byte-stable, canonically-ordered context with cache-key hashes.

**Architecture:** Two new files following the existing Phase 1/Phase 2 pattern: `src/weave/schemas/context.py` for the `ContextAssembly` Pydantic model and `src/weave/core/context.py` for the `assemble_context()` function. `runtime.py` imports both, renames `PreparedContext.context_text: str` → `context: ContextAssembly`, and extracts `ctx.context.full` at the single invoker call site. The invoker contract is unchanged — it still receives a plain string.

**Tech Stack:** Python 3.10+, Pydantic 2.x, stdlib `hashlib.sha256`, pytest. No new dependencies.

**Reference spec:** `docs/superpowers/specs/2026-04-10-weave-deterministic-context-assembly-design.md`

**Linear:** [MAR-142](https://linear.app/martymanny/issue/MAR-142)

---

## File Structure

### New files

| File | Responsibility |
|------|----------------|
| `src/weave/schemas/context.py` | `ContextAssembly` Pydantic model with `stable_prefix`, `volatile_task`, `full`, `stable_hash`, `full_hash`, `source_files` |
| `src/weave/core/context.py` | `assemble_context(working_dir)` function, canonical ordering constants, line ending normalization, hash computation |
| `tests/test_context.py` | 6 unit tests covering ordering, byte stability, line endings, empty/missing cases, hidden file filtering |

### Modified files

| File | Change |
|------|--------|
| `src/weave/core/runtime.py` | Delete `_load_context()`; rename `PreparedContext.context_text: str` → `context: ContextAssembly`; update `prepare()` to call `assemble_context()`; update `execute()` to pass `ctx.context.full` to invoker; add imports |
| `tests/test_runtime.py` | Add 2 integration tests for prepare populating the assembly and execute passing the string to the invoker |

### No other files touched

`src/weave/core/invoker.py` — unchanged. Invoker still receives `context: str`.
`.harness/providers/*.sh` adapter scripts — unchanged. Adapter JSON contract preserved.
`src/weave/schemas/activity.py` — unchanged. Phase 2.3 does not log context hashes in activity records.

---

## Task 1: Create `ContextAssembly` schema

**Files:**
- Create: `src/weave/schemas/context.py`
- Test: `tests/test_schemas.py` (add one test at the end)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_schemas.py`:

```python
def test_context_assembly_defaults():
    from weave.schemas.context import ContextAssembly
    ca = ContextAssembly(
        stable_prefix="hello",
        full="hello",
        stable_hash="abc",
        full_hash="abc",
    )
    assert ca.stable_prefix == "hello"
    assert ca.volatile_task == ""  # default
    assert ca.full == "hello"
    assert ca.source_files == []  # default factory
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && pytest tests/test_schemas.py::test_context_assembly_defaults -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'weave.schemas.context'`

- [ ] **Step 3: Create `src/weave/schemas/context.py`**

```python
"""Context assembly schema — deterministic project context with cache-key hashes."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ContextAssembly(BaseModel):
    """Deterministic assembly of project context.

    Separates stable prefix (project context, conventions, spec) from
    volatile per-turn content. Produces byte-stable output for identical
    inputs across runs — enables prompt cache stability and reliable
    session binding (Phase 2.2 / MAR-141).

    In Phase 2.3, volatile_task is always empty, so full == stable_prefix
    and full_hash == stable_hash. Phase 3 can populate volatile_task
    without schema changes.
    """
    stable_prefix: str
    volatile_task: str = ""
    full: str
    stable_hash: str
    full_hash: str
    source_files: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && pytest tests/test_schemas.py::test_context_assembly_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142
git add src/weave/schemas/context.py tests/test_schemas.py
git commit -m "$(cat <<'EOF'
feat(schemas): add ContextAssembly model

Introduces ContextAssembly as the data type for deterministic context
assembly. Fields: stable_prefix (markdown concatenation), volatile_task
(Phase 3 extension point, empty in Phase 2.3), full (combined payload),
stable_hash and full_hash (sha256 cache keys), source_files (diagnostic
ordering record).

Linear: MAR-142

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Implement `assemble_context` with canonical ordering

**Files:**
- Create: `src/weave/core/context.py`
- Test: `tests/test_context.py` (new file)

- [ ] **Step 1: Write the first failing test — canonical ordering**

Create `tests/test_context.py`:

```python
"""Tests for deterministic context assembly."""
from pathlib import Path


def _make_context_dir(root: Path, files: dict[str, str]) -> Path:
    """Helper: create .harness/context/ with the given files."""
    context_dir = root / ".harness" / "context"
    context_dir.mkdir(parents=True)
    for name, content in files.items():
        (context_dir / name).write_text(content)
    return context_dir


def test_assemble_context_canonical_ordering(temp_dir):
    """Canonical files come first in defined order; rest alphabetical."""
    from weave.core.context import assemble_context
    _make_context_dir(temp_dir, {
        "brief.md": "brief",
        "spec.md": "spec",
        "conventions.md": "conv",
        "extra.md": "extra",
        "another.md": "another",
    })
    ca = assemble_context(temp_dir)
    assert ca.source_files == [
        "conventions.md",
        "brief.md",
        "spec.md",
        "another.md",
        "extra.md",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && pytest tests/test_context.py::test_assemble_context_canonical_ordering -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'weave.core.context'`

- [ ] **Step 3: Create `src/weave/core/context.py`**

```python
"""Deterministic context assembly from .harness/context/ markdown files."""
from __future__ import annotations

import hashlib
from pathlib import Path

from weave.schemas.context import ContextAssembly


# Canonical order for well-known files — these come first when present.
# Other markdown files follow in alphabetical order.
_CANONICAL_ORDER = ["conventions.md", "brief.md", "spec.md"]

_SEPARATOR = "\n---\n"


def assemble_context(working_dir: Path) -> ContextAssembly:
    """Assemble a deterministic ContextAssembly from .harness/context/*.md.

    Ordering rules:
      1. Files in _CANONICAL_ORDER appear first, in that exact order
      2. Remaining *.md files follow in alphabetical order
      3. Canonical files are removed from the 'rest' partition before
         alphabetical ordering — no file is ever concatenated twice
      4. Hidden files (starting with '.') are excluded
      5. Missing canonical files are silently skipped

    Content rules:
      1. Each file's content is read as UTF-8
      2. Line endings are normalized: \\r\\n -> \\n, then \\r -> \\n
      3. Normalized contents are joined with '\\n---\\n' (no trailing whitespace)

    Phase 2.3: volatile_task is always empty, so full == stable_prefix
    and full_hash == stable_hash. Phase 3 can populate volatile_task.
    """
    context_dir = working_dir / ".harness" / "context"
    if not context_dir.exists():
        return _empty_assembly()

    # Discover all non-hidden .md files
    all_files = sorted(
        f for f in context_dir.glob("*.md")
        if not f.name.startswith(".")
    )

    # Partition into canonical (in defined order) and rest (alphabetical)
    canonical_files: list[Path] = []
    for name in _CANONICAL_ORDER:
        candidate = context_dir / name
        if candidate in all_files:
            canonical_files.append(candidate)

    rest = [f for f in all_files if f not in canonical_files]
    ordered = canonical_files + rest

    if not ordered:
        return _empty_assembly()

    # Read and normalize each file
    parts: list[str] = []
    source_files: list[str] = []
    for f in ordered:
        content = f.read_text(encoding="utf-8")
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        parts.append(normalized)
        source_files.append(f.name)

    stable_prefix = _SEPARATOR.join(parts)
    volatile_task = ""
    full = stable_prefix  # Phase 2.3: no volatile content

    stable_hash = hashlib.sha256(stable_prefix.encode("utf-8")).hexdigest()
    full_hash = hashlib.sha256(full.encode("utf-8")).hexdigest()

    return ContextAssembly(
        stable_prefix=stable_prefix,
        volatile_task=volatile_task,
        full=full,
        stable_hash=stable_hash,
        full_hash=full_hash,
        source_files=source_files,
    )


def _empty_assembly() -> ContextAssembly:
    """Return an empty but well-formed ContextAssembly."""
    empty_hash = hashlib.sha256(b"").hexdigest()
    return ContextAssembly(
        stable_prefix="",
        volatile_task="",
        full="",
        stable_hash=empty_hash,
        full_hash=empty_hash,
        source_files=[],
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && pytest tests/test_context.py::test_assemble_context_canonical_ordering -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142
git add src/weave/core/context.py tests/test_context.py
git commit -m "$(cat <<'EOF'
feat(context): add assemble_context with canonical ordering

Implements deterministic assembly from .harness/context/*.md files.
Canonical ordering: conventions.md, brief.md, spec.md first in that
order, then remaining files alphabetically. Line endings normalized
to LF. sha256 hashes computed for stable_prefix and full payload.
Returns well-formed empty assembly when context dir is missing or
contains no non-hidden markdown files.

Linear: MAR-142

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Test — byte stability across runs

**Files:**
- Test: `tests/test_context.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_context.py`:

```python
def test_assemble_context_byte_stable_across_runs(temp_dir):
    """Same inputs produce byte-identical outputs across repeated calls."""
    from weave.core.context import assemble_context
    _make_context_dir(temp_dir, {
        "conventions.md": "stable conventions",
        "brief.md": "stable brief",
        "spec.md": "stable spec",
    })

    first = assemble_context(temp_dir)
    second = assemble_context(temp_dir)

    assert first.stable_prefix == second.stable_prefix
    assert first.full == second.full
    assert first.stable_hash == second.stable_hash
    assert first.full_hash == second.full_hash
    assert first.source_files == second.source_files
```

- [ ] **Step 2: Run the test**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && pytest tests/test_context.py::test_assemble_context_byte_stable_across_runs -v`
Expected: PASS (Task 2's implementation is deterministic by construction — sorted file iteration, stable sha256, no timestamps)

- [ ] **Step 3: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142
git add tests/test_context.py
git commit -m "$(cat <<'EOF'
test(context): verify byte stability across repeated calls

Proves assemble_context has no hidden nondeterminism from file
iteration order, timestamps, or any other source. Identical
inputs produce identical stable_prefix, full, stable_hash,
full_hash, and source_files across calls.

Linear: MAR-142

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Test — line ending normalization

**Files:**
- Test: `tests/test_context.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_context.py`:

```python
def test_assemble_context_normalizes_line_endings(temp_dir):
    """CRLF and LF produce identical stable_hash when content is semantically equal."""
    import tempfile
    import shutil
    from weave.core.context import assemble_context

    # Two separate temp directories with semantically-identical content
    # but different line endings
    lf_dir = temp_dir / "lf"
    crlf_dir = temp_dir / "crlf"
    lf_dir.mkdir()
    crlf_dir.mkdir()

    lf_context = lf_dir / ".harness" / "context"
    crlf_context = crlf_dir / ".harness" / "context"
    lf_context.mkdir(parents=True)
    crlf_context.mkdir(parents=True)

    # Same semantic content, different raw bytes
    lf_content = "line one\nline two\nline three\n"
    crlf_content = "line one\r\nline two\r\nline three\r\n"

    # Write as bytes to bypass any platform auto-translation
    (lf_context / "spec.md").write_bytes(lf_content.encode("utf-8"))
    (crlf_context / "spec.md").write_bytes(crlf_content.encode("utf-8"))

    lf_result = assemble_context(lf_dir)
    crlf_result = assemble_context(crlf_dir)

    assert lf_result.stable_hash == crlf_result.stable_hash
    assert lf_result.stable_prefix == crlf_result.stable_prefix
```

- [ ] **Step 2: Run the test**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && pytest tests/test_context.py::test_assemble_context_normalizes_line_endings -v`
Expected: PASS (the normalization `content.replace("\r\n", "\n").replace("\r", "\n")` converts CRLF to LF before hashing)

- [ ] **Step 3: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142
git add tests/test_context.py
git commit -m "$(cat <<'EOF'
test(context): verify line ending normalization produces stable hashes

Proves CRLF and LF source files with identical semantic content
produce byte-identical stable_prefix and matching sha256 hashes.
Protects cross-platform byte stability — same repo checked out
on Windows (CRLF) and Linux (LF) produces the same cache key.

Linear: MAR-142

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Tests — empty directory, missing directory, hidden files

**Files:**
- Test: `tests/test_context.py`

- [ ] **Step 1: Write the three tests**

Append to `tests/test_context.py`:

```python
import hashlib as _hashlib


def test_assemble_context_empty_directory(temp_dir):
    """Empty .harness/context/ returns empty but well-formed assembly."""
    from weave.core.context import assemble_context
    context_dir = temp_dir / ".harness" / "context"
    context_dir.mkdir(parents=True)

    ca = assemble_context(temp_dir)

    empty_sha = _hashlib.sha256(b"").hexdigest()
    assert ca.stable_prefix == ""
    assert ca.volatile_task == ""
    assert ca.full == ""
    assert ca.stable_hash == empty_sha
    assert ca.full_hash == empty_sha
    assert ca.source_files == []


def test_assemble_context_missing_context_dir(temp_dir):
    """Missing .harness/context/ directory returns empty assembly gracefully."""
    from weave.core.context import assemble_context
    # temp_dir exists, but .harness/context/ does not
    ca = assemble_context(temp_dir)

    empty_sha = _hashlib.sha256(b"").hexdigest()
    assert ca.stable_prefix == ""
    assert ca.source_files == []
    assert ca.stable_hash == empty_sha


def test_assemble_context_skips_hidden_files(temp_dir):
    """Hidden files (starting with '.') are excluded from assembly."""
    from weave.core.context import assemble_context
    _make_context_dir(temp_dir, {
        "spec.md": "visible",
        ".hashes.json": "hidden metadata",
        ".draft.md": "hidden draft",
    })

    ca = assemble_context(temp_dir)
    assert ca.source_files == ["spec.md"]
    assert "hidden" not in ca.stable_prefix
    assert "draft" not in ca.stable_prefix
```

- [ ] **Step 2: Run the tests**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && pytest tests/test_context.py -v -k "empty_directory or missing_context_dir or skips_hidden"`
Expected: PASS (all three — Task 2's implementation handles all these cases)

- [ ] **Step 3: Run the full `test_context.py` suite**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && pytest tests/test_context.py -v`
Expected: 6 tests pass

- [ ] **Step 4: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142
git add tests/test_context.py
git commit -m "$(cat <<'EOF'
test(context): cover empty dir, missing dir, and hidden file cases

Proves assemble_context returns well-formed empty assemblies for
both an empty .harness/context/ directory and a missing directory
entirely. Hidden files (starting with '.') are excluded — .hashes.json
and .draft.md do not contribute to stable_prefix or source_files.

Linear: MAR-142

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Wire `assemble_context` into `runtime.py`

**Files:**
- Modify: `src/weave/core/runtime.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the failing integration test**

Append to `tests/test_runtime.py`:

```python
def test_prepare_populates_context_assembly(temp_dir):
    """prepare() stores a ContextAssembly on PreparedContext.context."""
    from weave.core.runtime import prepare
    from weave.schemas.context import ContextAssembly
    _init_harness(temp_dir)

    ctx = prepare(task="x", working_dir=temp_dir, caller="test")

    assert isinstance(ctx.context, ContextAssembly)
    assert isinstance(ctx.context.full, str)
    assert isinstance(ctx.context.stable_hash, str)
    assert len(ctx.context.stable_hash) == 64  # sha256 hex length
    assert isinstance(ctx.context.source_files, list)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && pytest tests/test_runtime.py::test_prepare_populates_context_assembly -v`
Expected: FAIL — `PreparedContext` has `context_text: str`, not `context: ContextAssembly`. The assertion `isinstance(ctx.context, ContextAssembly)` fails because `ctx.context` attribute does not exist.

- [ ] **Step 3: Add imports to `src/weave/core/runtime.py`**

Find the import block near the top of `src/weave/core/runtime.py`. Locate:

```python
from weave.core.config import resolve_config
from weave.core.hooks import HookContext, run_hooks
from weave.core.invoker import InvokeResult, invoke_provider
from weave.core.policy import evaluate_policy
from weave.core.security import DEFAULT_RULES, check_write_deny, resolve_action, scan_files
from weave.core.session import append_activity, create_session
from weave.schemas.activity import ActivityRecord, ActivityStatus, ActivityType, HookResult
from weave.schemas.config import ProviderConfig, WeaveConfig
from weave.schemas.policy import (
    HookResultRef,
    PolicyResult,
    RiskClass,
    RuntimeStatus,
    SecurityFinding,
    SecurityResult,
)
```

Add two new imports in alphabetical order (after `weave.core.config`, and after `weave.schemas.config`):

```python
from weave.core.config import resolve_config
from weave.core.context import assemble_context
from weave.core.hooks import HookContext, run_hooks
from weave.core.invoker import InvokeResult, invoke_provider
from weave.core.policy import evaluate_policy
from weave.core.security import DEFAULT_RULES, check_write_deny, resolve_action, scan_files
from weave.core.session import append_activity, create_session
from weave.schemas.activity import ActivityRecord, ActivityStatus, ActivityType, HookResult
from weave.schemas.config import ProviderConfig, WeaveConfig
from weave.schemas.context import ContextAssembly
from weave.schemas.policy import (
    HookResultRef,
    PolicyResult,
    RiskClass,
    RuntimeStatus,
    SecurityFinding,
    SecurityResult,
)
```

- [ ] **Step 4: Rename `context_text` → `context` on `PreparedContext`**

Find the `PreparedContext` dataclass in `src/weave/core/runtime.py`:

```python
@dataclass
class PreparedContext:
    """Everything the pipeline needs after the prepare stage."""
    config: WeaveConfig
    active_provider: str
    provider_config: ProviderConfig
    adapter_script: Path
    context_text: str
    session_id: str
    working_dir: Path
    phase: str
    task: str
    caller: str | None
    requested_risk_class: RiskClass | None
    pre_invoke_untracked: set[str]
```

Replace the `context_text: str` line with:

```python
    context: ContextAssembly
```

So the full class becomes:

```python
@dataclass
class PreparedContext:
    """Everything the pipeline needs after the prepare stage."""
    config: WeaveConfig
    active_provider: str
    provider_config: ProviderConfig
    adapter_script: Path
    context: ContextAssembly
    session_id: str
    working_dir: Path
    phase: str
    task: str
    caller: str | None
    requested_risk_class: RiskClass | None
    pre_invoke_untracked: set[str]
```

- [ ] **Step 5: Delete `_load_context()` function**

Find this function definition in `src/weave/core/runtime.py`:

```python
def _load_context(working_dir: Path) -> str:
    """Concatenate markdown files from .harness/context/ in sorted order."""
    parts: list[str] = []
    context_dir = working_dir / ".harness" / "context"
    if context_dir.exists():
        for md in sorted(context_dir.glob("*.md")):
            if not md.name.startswith("."):
                parts.append(md.read_text())
    return "\n---\n".join(parts)
```

Delete the entire function (all lines from `def _load_context` through the `return` statement).

- [ ] **Step 6: Update `prepare()` to use `assemble_context`**

Find this block in `prepare()`:

```python
    adapter_script = working_dir / ".harness" / "providers" / f"{active_provider}.sh"
    context_text = _load_context(working_dir)
    session_id = create_session()
    pre_invoke_untracked = _snapshot_untracked(working_dir)

    return PreparedContext(
        config=config,
        active_provider=active_provider,
        provider_config=provider_config,
        adapter_script=adapter_script,
        context_text=context_text,
        session_id=session_id,
        working_dir=working_dir,
        phase=config.phase,
        task=task,
        caller=caller,
        requested_risk_class=requested_risk_class,
        pre_invoke_untracked=pre_invoke_untracked,
    )
```

Replace with:

```python
    adapter_script = working_dir / ".harness" / "providers" / f"{active_provider}.sh"
    context = assemble_context(working_dir)
    session_id = create_session()
    pre_invoke_untracked = _snapshot_untracked(working_dir)

    return PreparedContext(
        config=config,
        active_provider=active_provider,
        provider_config=provider_config,
        adapter_script=adapter_script,
        context=context,
        session_id=session_id,
        working_dir=working_dir,
        phase=config.phase,
        task=task,
        caller=caller,
        requested_risk_class=requested_risk_class,
        pre_invoke_untracked=pre_invoke_untracked,
    )
```

- [ ] **Step 7: Update `execute()` to pass `ctx.context.full` to invoker**

Find this block in `execute()`:

```python
    invoke_result = invoke_provider(
        adapter_script=ctx.adapter_script,
        task=ctx.task,
        working_dir=ctx.working_dir,
        context=ctx.context_text,
        timeout=timeout,
    )
```

Replace with:

```python
    invoke_result = invoke_provider(
        adapter_script=ctx.adapter_script,
        task=ctx.task,
        working_dir=ctx.working_dir,
        context=ctx.context.full,
        timeout=timeout,
    )
```

- [ ] **Step 8: Run the integration test**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && pytest tests/test_runtime.py::test_prepare_populates_context_assembly -v`
Expected: PASS

- [ ] **Step 9: Run the full runtime suite to catch regressions**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && pytest tests/test_runtime.py -v`
Expected: all existing runtime tests pass. No test accesses `ctx.context_text` directly — they all go through `prepare()` and `execute()` which are now updated.

- [ ] **Step 10: Run the full test suite to catch cross-file regressions**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && pytest tests/ -q 2>&1 | tail -5`
Expected: **110 tests pass** (102 baseline + 1 schema test from Task 1 + 6 unit tests from Tasks 2, 3, 4, 5 + 1 integration test from Task 6).

- [ ] **Step 11: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142
git add src/weave/core/runtime.py tests/test_runtime.py
git commit -m "$(cat <<'EOF'
feat(runtime): wire assemble_context into prepare() and execute()

Replaces the ad-hoc _load_context() helper with the new
assemble_context() function. PreparedContext.context_text: str
is renamed to context: ContextAssembly. execute() extracts
ctx.context.full at the invoker call site, so the adapter
contract is unchanged — invoker still receives context as a
plain string.

_load_context() is deleted.

Linear: MAR-142

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Integration test — invoker still receives a string

**Files:**
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_runtime.py`:

```python
def test_execute_still_passes_context_string_to_invoker(temp_dir):
    """Invoker contract preserved: it receives ctx.context.full as a string.

    Uses a stub adapter that writes its received context (from the JSON
    stdin payload) to a file, then verifies the file content matches
    ctx.context.full produced by prepare().
    """
    from weave.core.runtime import execute, prepare
    _init_harness(temp_dir)

    # Stub adapter: parse stdin JSON, write "context" field to a file
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'INPUT=$(cat)\n'
        'echo "$INPUT" | python3 -c "import sys, json; '
        'data = json.loads(sys.stdin.read()); '
        'open(\'received_context.txt\', \'w\').write(data[\'context\'])"\n'
        'echo \'{"exitCode": 0, "stdout": "captured", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    # Capture the expected context by calling prepare() directly
    expected_ctx = prepare(task="x", working_dir=temp_dir, caller="test")
    expected_context_str = expected_ctx.context.full

    # Run execute and verify the adapter captured the same string
    execute(task="x", working_dir=temp_dir, caller="test")

    captured = (temp_dir / "received_context.txt").read_text()
    assert captured == expected_context_str
    assert isinstance(captured, str)
```

- [ ] **Step 2: Run the test**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && pytest tests/test_runtime.py::test_execute_still_passes_context_string_to_invoker -v`
Expected: PASS

**If the test fails** because the stub adapter's python3 subprocess has issues writing the file, simplify by using a different capture strategy — write the context field to a file via jq if available, or use a bash-only approach like `grep -o '"context":"[^"]*"'`. The goal is to capture the `context` field from the JSON payload and verify it matches `ctx.context.full`. The exact mechanism is flexible.

- [ ] **Step 3: Run the full test suite**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && pytest tests/ -q 2>&1 | tail -5`
Expected: **111 tests pass** (102 baseline + 1 schema + 6 unit + 2 integration).

Running tally across tasks: 102 baseline → Task 1 +1 → Task 2 +1 → Task 3 +1 → Task 4 +1 → Task 5 +3 → Task 6 +1 → Task 7 +1 = **111 total**.

- [ ] **Step 4: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142
git add tests/test_runtime.py
git commit -m "$(cat <<'EOF'
test(runtime): verify invoker still receives context as a plain string

Proves the invoker contract is preserved: execute() extracts
ctx.context.full and passes it to invoke_provider as a string,
so adapter scripts continue to see a single 'context' JSON field
with the full payload.

Linear: MAR-142

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Final verification

**Files:** none — verification only

- [ ] **Step 1: Run the full test suite**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && pytest tests/ -v 2>&1 | tail -30`
Expected: 111 tests pass (102 baseline + 9 new — 1 schema + 6 unit + 2 integration).

- [ ] **Step 2: Verify no circular imports**

Run:
```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && PYTHONPATH=src python3 -c "
from weave.core.runtime import prepare, execute
from weave.core.context import assemble_context
from weave.schemas.context import ContextAssembly
from weave.core import execute as core_execute
print('imports: ok')
"
```
Expected: prints `imports: ok`

- [ ] **Step 3: Manual determinism check**

Run:
```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && PYTHONPATH=src python3 -c "
import tempfile
from pathlib import Path
from weave.core.context import assemble_context

with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    ctx_dir = tmp / '.harness' / 'context'
    ctx_dir.mkdir(parents=True)
    (ctx_dir / 'conventions.md').write_text('c')
    (ctx_dir / 'brief.md').write_text('b')
    (ctx_dir / 'spec.md').write_text('s')
    (ctx_dir / 'extra.md').write_text('e')

    a = assemble_context(tmp)
    b = assemble_context(tmp)

    assert a.stable_hash == b.stable_hash
    assert a.source_files == ['conventions.md', 'brief.md', 'spec.md', 'extra.md']
    print('determinism check: ok')
    print('source_files:', a.source_files)
    print('stable_hash:', a.stable_hash[:16] + '...')
"
```
Expected: prints `determinism check: ok`, source file ordering, truncated hash

- [ ] **Step 4: Verify the pipeline docstring is still accurate**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-142 && head -6 src/weave/core/runtime.py`
Expected: the pipeline line still shows 7 stages (`prepare -> policy_check -> invoke -> security_scan -> cleanup -> revert -> record`). MAR-142 does not add or remove stages — it only enriches `prepare()`'s output.

- [ ] **Step 5: No commit** — Task 8 is verification only.

---

## Self-Review Notes

**Spec coverage:**
- `ContextAssembly` schema with all six fields → Task 1
- `assemble_context()` function with canonical ordering → Task 2
- Canonical ordering enforcement (`conventions.md`, `brief.md`, `spec.md`, then alphabetical) → Task 2 test
- "No file concatenated twice" invariant → Task 2 implementation (rest = all_files - canonical)
- Byte-stable output across runs → Task 3 test
- Line ending normalization (`\r\n` → `\n`, then `\r` → `\n`) → Task 4 test
- Empty directory handling → Task 5 test
- Missing directory handling → Task 5 test
- Hidden file filtering → Task 5 test
- Read errors propagate (not silently skipped) → enforced by implementation in Task 2 (no try/except around `read_text`)
- `PreparedContext.context_text: str` → `PreparedContext.context: ContextAssembly` → Task 6
- `_load_context()` deleted → Task 6 Step 5
- `prepare()` calls `assemble_context()` → Task 6 Step 6
- `execute()` passes `ctx.context.full` to invoker → Task 6 Step 7
- Invoker signature unchanged → Task 7 test verifies the adapter receives a string
- Adapter scripts unchanged → no task touches them
- All 102 pre-existing tests continue to pass → Task 6 Step 10 and Task 8 Step 1

**Placeholder scan:** No TBDs, TODOs, or placeholder steps. Every code block is complete and copy-pastable. Task 7 has a single caveat about adapter stub flexibility, which is explicit and scoped.

**Type consistency:**
- `ContextAssembly.stable_prefix: str`, `volatile_task: str`, `full: str`, `stable_hash: str`, `full_hash: str`, `source_files: list[str]` — defined in Task 1, used consistently in Tasks 2-8
- `assemble_context(working_dir: Path) -> ContextAssembly` — defined in Task 2, used in Task 6
- `PreparedContext.context: ContextAssembly` — renamed in Task 6, accessed via `ctx.context.full` in Task 6 Step 7
- `_CANONICAL_ORDER = ["conventions.md", "brief.md", "spec.md"]` — referenced consistently across Task 2's implementation and Task 2's test's expected `source_files`

**Expected final test count:** 111 tests (102 baseline + 1 schema + 6 unit + 2 integration).

**Architectural note:** All tests follow the existing pattern — `test_context.py` is a pure-function test file (fastest tests, no harness setup beyond `temp_dir`), and the integration tests in `test_runtime.py` go through the full pipeline. This matches `test_policy.py` (pure) vs `test_runtime.py` (integration), which is the healthiest split for future maintenance.
