# MAR-141 Implementation Plan — Session Binding Hashes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write a `.binding.json` sidecar next to every session's JSONL log capturing provider, adapter, context, and config hashes. Add a `validate_session()` function that compares a stored binding against a current `PreparedContext` and returns mismatched field names. Lays the foundation for future session reuse without adding reuse logic yet.

**Architecture:** Two new files following the MAR-142 pattern: `src/weave/schemas/session_binding.py` for the `SessionBinding` Pydantic model and `src/weave/core/session_binding.py` for `compute_binding`, `write_binding`, `read_binding`, `validate_session`, and a private `_hash_config` helper. `runtime.py` calls `compute_binding()` + `write_binding()` at the end of `prepare()`. No changes to `execute()`, invoker, or adapter scripts.

**Tech Stack:** Python 3.10+, Pydantic 2.x, stdlib `hashlib.sha256` and `json.dumps` for canonical config hashing, pytest. No new dependencies.

**Reference spec:** `docs/superpowers/specs/2026-04-10-weave-session-binding-hashes-design.md`

**Linear:** [MAR-141](https://linear.app/martymanny/issue/MAR-141)

---

## File Structure

### New files

| File | Responsibility |
|------|----------------|
| `src/weave/schemas/session_binding.py` | `SessionBinding` Pydantic model with six fields |
| `src/weave/core/session_binding.py` | `compute_binding`, `write_binding`, `read_binding`, `validate_session`, `_hash_config` |
| `tests/test_session_binding.py` | 6 unit tests |

### Modified files

| File | Change |
|------|--------|
| `src/weave/core/runtime.py` | Two new imports; `prepare()` computes and writes the binding sidecar before returning |
| `tests/test_runtime.py` | Add 2 integration tests |

### No other files touched

`src/weave/core/invoker.py` — unchanged.
`src/weave/core/session.py` — unchanged (binding is a distinct concern from JSONL append).
Adapter scripts — unchanged.
`ActivityRecord` schema — unchanged.

---

## Task 1: Create `SessionBinding` schema

**Files:**
- Create: `src/weave/schemas/session_binding.py`
- Test: `tests/test_schemas.py` (add one test at the end)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_schemas.py`:

```python
def test_session_binding_fields():
    from datetime import datetime, timezone
    from weave.schemas.session_binding import SessionBinding

    sb = SessionBinding(
        session_id="test-id",
        created_at=datetime.now(timezone.utc),
        provider_name="claude-code",
        adapter_script_hash="a" * 64,
        context_stable_hash="b" * 64,
        config_hash="c" * 64,
    )
    assert sb.session_id == "test-id"
    assert sb.provider_name == "claude-code"
    assert len(sb.adapter_script_hash) == 64
    assert len(sb.context_stable_hash) == 64
    assert len(sb.config_hash) == 64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && pytest tests/test_schemas.py::test_session_binding_fields -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'weave.schemas.session_binding'`

- [ ] **Step 3: Create `src/weave/schemas/session_binding.py`**

```python
"""Session binding schema — compatibility fingerprint of a session's creation-time inputs."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SessionBinding(BaseModel):
    """Compatibility fingerprint of a session's creation-time inputs.

    Written as a .binding.json sidecar alongside the session JSONL.
    Captured once at prepare() time; never updated. Future callers
    that want to reuse a session can load this binding and compare
    its hashes against the current PreparedContext — any mismatch
    means the session has drifted from its original conditions and
    should not be reused.

    Phase 2.2 (MAR-141) produces bindings but does not yet gate
    anything on them. validate_session() exists as a pure comparison
    function for future consumers.
    """
    session_id: str
    created_at: datetime
    provider_name: str
    adapter_script_hash: str
    context_stable_hash: str
    config_hash: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && pytest tests/test_schemas.py::test_session_binding_fields -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141
git add src/weave/schemas/session_binding.py tests/test_schemas.py
git commit -m "$(cat <<'EOF'
feat(schemas): add SessionBinding model

Introduces SessionBinding as the compatibility fingerprint for
session reuse. Six fields: session_id, created_at, provider_name,
adapter_script_hash, context_stable_hash, config_hash. Provider
is a plain string equality check; the other three are sha256 hex
digests.

Linear: MAR-141

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Implement `compute_binding` + `_hash_config`

**Files:**
- Create: `src/weave/core/session_binding.py`
- Create: `tests/test_session_binding.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `tests/test_session_binding.py`:

```python
"""Tests for session binding computation, I/O, and validation."""
import json
from pathlib import Path


def _init_harness(root: Path):
    """Create a minimal .harness/ directory for prepare() to consume."""
    harness = root / ".harness"
    harness.mkdir()
    for sub in ["context", "hooks", "providers", "sessions", "integrations"]:
        (harness / sub).mkdir()
    (harness / "manifest.json").write_text(json.dumps({
        "id": "test-id",
        "type": "project",
        "name": "test",
        "status": "active",
        "phase": "sandbox",
    }))
    (harness / "config.json").write_text(json.dumps({
        "version": "1",
        "phase": "sandbox",
        "default_provider": "claude-code",
        "providers": {
            "claude-code": {
                "command": ".harness/providers/claude-code.sh",
                "enabled": True,
                "capability": "workspace-write",
            }
        },
    }))
    adapter = harness / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo \'{"exitCode": 0, "stdout": "ok", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)
    return harness


def test_compute_binding_produces_all_fields(temp_dir):
    """compute_binding returns a SessionBinding with all six fields populated."""
    from weave.core.runtime import prepare
    from weave.core.session_binding import compute_binding
    from weave.schemas.session_binding import SessionBinding

    _init_harness(temp_dir)
    ctx = prepare(task="x", working_dir=temp_dir, caller="test")

    binding = compute_binding(ctx)

    assert isinstance(binding, SessionBinding)
    assert binding.session_id == ctx.session_id
    assert binding.provider_name == ctx.active_provider
    assert len(binding.adapter_script_hash) == 64
    assert len(binding.context_stable_hash) == 64
    assert len(binding.config_hash) == 64
    # created_at is timezone-aware
    assert binding.created_at.tzinfo is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && pytest tests/test_session_binding.py::test_compute_binding_produces_all_fields -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'weave.core.session_binding'`

- [ ] **Step 3: Create `src/weave/core/session_binding.py`**

```python
"""Session binding — compute, write, read, and validate session compatibility fingerprints."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from weave.schemas.config import WeaveConfig
from weave.schemas.session_binding import SessionBinding


def _hash_config(config: WeaveConfig) -> str:
    """Compute a byte-stable sha256 of the config as canonicalized JSON.

    Uses json.dumps(sort_keys=True, separators=(",", ":")) to produce
    deterministic output regardless of dict iteration order or Pydantic
    serialization internals.
    """
    data = config.model_dump(mode="json")
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_binding(ctx) -> SessionBinding:
    """Build a SessionBinding from a PreparedContext.

    No filesystem writes. Not strictly pure because created_at reads
    wall-clock time, but created_at is excluded from validate_session
    comparisons so the four compatibility fields are deterministic for
    identical inputs.

    Adapter script hash uses sha256(b"") as a fallback when the adapter
    file is missing — the binding stays well-formed even when scaffolding
    is incomplete.
    """
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

**Note on the `ctx` parameter type:** the signature uses `ctx` without a type annotation to avoid a circular import. `PreparedContext` lives in `runtime.py`, and `runtime.py` will import from `session_binding.py` — adding `from weave.core.runtime import PreparedContext` here would create a cycle. The function accesses `ctx.session_id`, `ctx.active_provider`, `ctx.adapter_script`, `ctx.context.stable_hash`, and `ctx.config` via duck typing. This is the same pattern used by other `core/` modules that accept `PreparedContext` (e.g., `_security_scan` in runtime.py is defined where PreparedContext lives, but if it lived in another file the same approach would apply).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && pytest tests/test_session_binding.py::test_compute_binding_produces_all_fields -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141
git add src/weave/core/session_binding.py tests/test_session_binding.py
git commit -m "$(cat <<'EOF'
feat(session-binding): add compute_binding and _hash_config

Implements the core construction function for SessionBinding.
Reads adapter script bytes via sha256, reuses ContextAssembly.stable_hash
for context_stable_hash, computes canonical config hash via
json.dumps(sort_keys=True, separators=(",", ":")). Adapter script
fallback uses sha256(b"") when the file is missing so bindings are
always well-formed.

Linear: MAR-141

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Test — compute_binding uses context.stable_hash directly

**Files:**
- Test: `tests/test_session_binding.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_session_binding.py`:

```python
def test_compute_binding_uses_context_stable_hash(temp_dir):
    """compute_binding reuses the ContextAssembly.stable_hash, not a recomputed value."""
    from weave.core.runtime import prepare
    from weave.core.session_binding import compute_binding

    _init_harness(temp_dir)
    ctx = prepare(task="x", working_dir=temp_dir, caller="test")

    binding = compute_binding(ctx)

    # The binding's context_stable_hash must equal the one already
    # computed by assemble_context in MAR-142 — no recomputation.
    assert binding.context_stable_hash == ctx.context.stable_hash
```

- [ ] **Step 2: Run the test**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && pytest tests/test_session_binding.py::test_compute_binding_uses_context_stable_hash -v`
Expected: PASS (Task 2's implementation directly assigns `ctx.context.stable_hash`)

- [ ] **Step 3: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141
git add tests/test_session_binding.py
git commit -m "$(cat <<'EOF'
test(session-binding): verify context_stable_hash comes from ctx.context

Proves compute_binding reuses the MAR-142 ContextAssembly hash
rather than recomputing. This is the key integration with MAR-142
and ensures context hash semantics stay consistent across consumers.

Linear: MAR-141

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Test — config_hash is canonical across different dict orderings

**Files:**
- Test: `tests/test_session_binding.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_session_binding.py`:

```python
def test_compute_binding_config_hash_is_canonical():
    """Config hash is byte-stable regardless of dict insertion order."""
    from weave.core.session_binding import _hash_config
    from weave.schemas.config import ProviderConfig, WeaveConfig
    from weave.schemas.policy import RiskClass

    # Two configs with semantically identical providers but
    # different insertion order
    config_a = WeaveConfig(
        version="1",
        phase="sandbox",
        default_provider="claude-code",
        providers={
            "claude-code": ProviderConfig(command="claude", capability=RiskClass.WORKSPACE_WRITE),
            "codex": ProviderConfig(command="codex", capability=RiskClass.WORKSPACE_WRITE),
            "gemini": ProviderConfig(command="gemini", capability=RiskClass.WORKSPACE_WRITE),
        },
    )
    config_b = WeaveConfig(
        version="1",
        phase="sandbox",
        default_provider="claude-code",
        providers={
            "gemini": ProviderConfig(command="gemini", capability=RiskClass.WORKSPACE_WRITE),
            "codex": ProviderConfig(command="codex", capability=RiskClass.WORKSPACE_WRITE),
            "claude-code": ProviderConfig(command="claude", capability=RiskClass.WORKSPACE_WRITE),
        },
    )

    assert _hash_config(config_a) == _hash_config(config_b)
```

- [ ] **Step 2: Run the test**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && pytest tests/test_session_binding.py::test_compute_binding_config_hash_is_canonical -v`
Expected: PASS (the `json.dumps(sort_keys=True)` in `_hash_config` sorts keys alphabetically, so insertion order doesn't matter)

- [ ] **Step 3: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141
git add tests/test_session_binding.py
git commit -m "$(cat <<'EOF'
test(session-binding): verify config_hash is canonical across dict orderings

Proves _hash_config produces byte-stable output regardless of the
insertion order of dict fields inside WeaveConfig. Protects against
Pydantic version drift and dict iteration instability.

Linear: MAR-141

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Implement `write_binding` and `read_binding` + round-trip test

**Files:**
- Modify: `src/weave/core/session_binding.py`
- Test: `tests/test_session_binding.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_session_binding.py`:

```python
def test_write_and_read_binding_round_trip(temp_dir):
    """write_binding + read_binding is lossless for all fields."""
    from datetime import datetime, timezone
    from weave.core.session_binding import read_binding, write_binding
    from weave.schemas.session_binding import SessionBinding

    sessions_dir = temp_dir / ".harness" / "sessions"
    original = SessionBinding(
        session_id="test-session-123",
        created_at=datetime(2026, 4, 10, 12, 34, 56, tzinfo=timezone.utc),
        provider_name="claude-code",
        adapter_script_hash="a" * 64,
        context_stable_hash="b" * 64,
        config_hash="c" * 64,
    )

    written_path = write_binding(original, sessions_dir)
    assert written_path.exists()
    assert written_path.name == "test-session-123.binding.json"

    loaded = read_binding("test-session-123", sessions_dir)
    assert loaded is not None
    assert loaded.session_id == original.session_id
    assert loaded.created_at == original.created_at
    assert loaded.provider_name == original.provider_name
    assert loaded.adapter_script_hash == original.adapter_script_hash
    assert loaded.context_stable_hash == original.context_stable_hash
    assert loaded.config_hash == original.config_hash


def test_read_binding_returns_none_for_missing_file(temp_dir):
    """read_binding returns None when the sidecar file does not exist."""
    from weave.core.session_binding import read_binding

    sessions_dir = temp_dir / ".harness" / "sessions"
    sessions_dir.mkdir(parents=True)

    result = read_binding("nonexistent", sessions_dir)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && pytest tests/test_session_binding.py::test_write_and_read_binding_round_trip tests/test_session_binding.py::test_read_binding_returns_none_for_missing_file -v`
Expected: FAIL with `ImportError: cannot import name 'write_binding' from 'weave.core.session_binding'`

- [ ] **Step 3: Add `write_binding` and `read_binding` to `src/weave/core/session_binding.py`**

Append to `src/weave/core/session_binding.py` (after `compute_binding`):

```python
def write_binding(binding: SessionBinding, sessions_dir: Path) -> Path:
    """Serialize a SessionBinding to a .binding.json sidecar.

    Creates sessions_dir if it does not exist (matching append_activity's
    behavior). Returns the written path.
    """
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = sessions_dir / f"{binding.session_id}.binding.json"
    sidecar_path.write_text(binding.model_dump_json(indent=2))
    return sidecar_path


def read_binding(session_id: str, sessions_dir: Path) -> SessionBinding | None:
    """Load a SessionBinding from its .binding.json sidecar.

    Returns None if the file does not exist. Raises on malformed JSON
    or Pydantic validation errors — a broken binding is an operator-facing
    error, not silently ignorable.
    """
    sidecar_path = sessions_dir / f"{session_id}.binding.json"
    if not sidecar_path.exists():
        return None
    return SessionBinding.model_validate_json(sidecar_path.read_text())
```

- [ ] **Step 4: Run the tests**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && pytest tests/test_session_binding.py::test_write_and_read_binding_round_trip tests/test_session_binding.py::test_read_binding_returns_none_for_missing_file -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141
git add src/weave/core/session_binding.py tests/test_session_binding.py
git commit -m "$(cat <<'EOF'
feat(session-binding): add write_binding and read_binding

Sidecar file at {sessions_dir}/{session_id}.binding.json.
write_binding creates the directory if missing and returns the
path. read_binding returns None for missing files and raises on
malformed JSON. Round-trip is lossless for all six SessionBinding
fields.

Linear: MAR-141

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Implement `validate_session` + matching/mismatching tests

**Files:**
- Modify: `src/weave/core/session_binding.py`
- Test: `tests/test_session_binding.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session_binding.py`:

```python
def test_validate_session_returns_empty_for_matching_binding(temp_dir):
    """Identical inputs produce zero mismatches — session is reusable."""
    from weave.core.runtime import prepare
    from weave.core.session_binding import compute_binding, validate_session, write_binding

    _init_harness(temp_dir)

    # Create session 1, write its binding
    ctx1 = prepare(task="x", working_dir=temp_dir, caller="test")
    binding = compute_binding(ctx1)
    sessions_dir = temp_dir / ".harness" / "sessions"
    write_binding(binding, sessions_dir)

    # Prepare a new context on the SAME working_dir (nothing changed on disk).
    # Note: ctx2 will have a different session_id (prepare always creates a
    # fresh UUID), but the four compatibility fields should match.
    ctx2 = prepare(task="x", working_dir=temp_dir, caller="test")

    # Validate the OLD session_id against the NEW ctx
    mismatches = validate_session(ctx1.session_id, ctx2, sessions_dir)
    assert mismatches == []


def test_validate_session_detects_config_hash_mismatch(temp_dir):
    """Changing .harness/config.json flips the config_hash — detected as mismatch."""
    from weave.core.runtime import prepare
    from weave.core.session_binding import compute_binding, validate_session, write_binding

    _init_harness(temp_dir)

    # Prepare context, write binding
    ctx1 = prepare(task="x", working_dir=temp_dir, caller="test")
    binding = compute_binding(ctx1)
    sessions_dir = temp_dir / ".harness" / "sessions"
    write_binding(binding, sessions_dir)

    # Modify the config (phase sandbox -> mvp)
    config_path = temp_dir / ".harness" / "config.json"
    config = json.loads(config_path.read_text())
    config["phase"] = "mvp"
    config_path.write_text(json.dumps(config))

    # Prepare a new context against the modified config
    ctx2 = prepare(task="x", working_dir=temp_dir, caller="test")

    mismatches = validate_session(ctx1.session_id, ctx2, sessions_dir)
    assert mismatches == ["config_hash"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && pytest tests/test_session_binding.py::test_validate_session_returns_empty_for_matching_binding tests/test_session_binding.py::test_validate_session_detects_config_hash_mismatch -v`
Expected: FAIL with `ImportError: cannot import name 'validate_session'`

- [ ] **Step 3: Add `validate_session` to `src/weave/core/session_binding.py`**

Append to `src/weave/core/session_binding.py` (after `read_binding`):

```python
def validate_session(
    session_id: str,
    ctx,
    sessions_dir: Path,
) -> list[str]:
    """Return the list of binding field names that differ between the
    stored binding and the current PreparedContext.

    Empty list means the session is reusable against ctx. Non-empty
    means one or more invalidating inputs changed.

    Raises FileNotFoundError if the binding sidecar does not exist —
    a nonexistent binding is qualitatively different from a mismatched
    binding. Callers should treat them as distinct signals.

    The comparison checks exactly four fields: provider_name,
    adapter_script_hash, context_stable_hash, config_hash. session_id
    and created_at are identity fields, not compatibility fields.
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

- [ ] **Step 4: Run the tests**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && pytest tests/test_session_binding.py -v`
Expected: all session_binding tests pass (6 total so far: compute_binding_produces_all_fields, compute_binding_uses_context_stable_hash, compute_binding_config_hash_is_canonical, write_and_read_binding_round_trip, read_binding_returns_none_for_missing_file, validate_session_returns_empty_for_matching_binding, validate_session_detects_config_hash_mismatch = 7 passing)

Wait — let me recount. Tasks 2-6 add these tests:
- Task 2: `test_compute_binding_produces_all_fields` (1)
- Task 3: `test_compute_binding_uses_context_stable_hash` (1)
- Task 4: `test_compute_binding_config_hash_is_canonical` (1)
- Task 5: `test_write_and_read_binding_round_trip` + `test_read_binding_returns_none_for_missing_file` (2)
- Task 6: `test_validate_session_returns_empty_for_matching_binding` + `test_validate_session_detects_config_hash_mismatch` (2)

Total in `test_session_binding.py`: 7 tests. Expected after Task 6: 7 tests passing in that file.

- [ ] **Step 5: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141
git add src/weave/core/session_binding.py tests/test_session_binding.py
git commit -m "$(cat <<'EOF'
feat(session-binding): add validate_session compatibility check

Compares a stored binding against a current PreparedContext and
returns a list of mismatched field names. Empty list means the
session is reusable. Raises FileNotFoundError for missing bindings
(distinct signal from mismatched bindings).

Compares exactly four fields: provider_name, adapter_script_hash,
context_stable_hash, config_hash. Excludes session_id and created_at
which are identity fields, not compatibility fields.

Linear: MAR-141

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Wire into `prepare()` + integration tests

**Files:**
- Modify: `src/weave/core/runtime.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the failing integration tests**

Append to `tests/test_runtime.py`:

```python
def test_prepare_writes_session_binding_sidecar(temp_dir):
    """prepare() writes a .binding.json sidecar next to the session."""
    import json as _json
    from weave.core.runtime import prepare
    _init_harness(temp_dir)

    ctx = prepare(task="x", working_dir=temp_dir, caller="test")

    sidecar = temp_dir / ".harness" / "sessions" / f"{ctx.session_id}.binding.json"
    assert sidecar.exists()

    data = _json.loads(sidecar.read_text())
    assert data["session_id"] == ctx.session_id
    assert "created_at" in data
    assert data["provider_name"] == ctx.active_provider
    assert len(data["adapter_script_hash"]) == 64
    assert len(data["context_stable_hash"]) == 64
    assert len(data["config_hash"]) == 64


def test_validate_session_raises_for_missing_binding(temp_dir):
    """validate_session raises FileNotFoundError when no sidecar exists."""
    import pytest
    from weave.core.runtime import prepare
    from weave.core.session_binding import validate_session
    _init_harness(temp_dir)

    ctx = prepare(task="x", working_dir=temp_dir, caller="test")

    # Delete the binding sidecar that prepare() just wrote
    sidecar = temp_dir / ".harness" / "sessions" / f"{ctx.session_id}.binding.json"
    sidecar.unlink()

    sessions_dir = temp_dir / ".harness" / "sessions"
    with pytest.raises(FileNotFoundError, match="No binding sidecar"):
        validate_session(ctx.session_id, ctx, sessions_dir)
```

- [ ] **Step 2: Run the first test to verify it fails**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && pytest tests/test_runtime.py::test_prepare_writes_session_binding_sidecar -v`
Expected: FAIL — `prepare()` does not yet write a sidecar. The assertion `sidecar.exists()` fails.

- [ ] **Step 3: Add imports to `src/weave/core/runtime.py`**

Find the import block near the top. Locate:

```python
from weave.core.session import append_activity, create_session
```

Add the new import right after it (maintaining alphabetical order within the `weave.core.*` group):

```python
from weave.core.session import append_activity, create_session
from weave.core.session_binding import compute_binding, write_binding
```

- [ ] **Step 4: Update `prepare()` to write the binding sidecar**

Find the tail of the `prepare()` function in `src/weave/core/runtime.py`. It currently looks like:

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

Replace with:

```python
    adapter_script = working_dir / ".harness" / "providers" / f"{active_provider}.sh"
    context = assemble_context(working_dir)
    session_id = create_session()
    pre_invoke_untracked = _snapshot_untracked(working_dir)

    prepared = PreparedContext(
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

    # Write the session binding sidecar
    binding = compute_binding(prepared)
    sessions_dir = working_dir / ".harness" / "sessions"
    write_binding(binding, sessions_dir)

    return prepared
```

- [ ] **Step 5: Run both integration tests**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && pytest tests/test_runtime.py::test_prepare_writes_session_binding_sidecar tests/test_runtime.py::test_validate_session_raises_for_missing_binding -v`
Expected: PASS (both tests)

- [ ] **Step 6: Run the full runtime suite to catch regressions**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && pytest tests/test_runtime.py -v`
Expected: all runtime tests pass. MAR-139's file revert tests use git-initialized directories; the new `.binding.json` files do not match any deny pattern or scanner rule, so they should not trigger any security findings.

**Potential regression to watch for:** MAR-139's `test_execute_preserves_pre_existing_untracked_on_revert` creates an untracked file before `prepare()` and expects it to be picked up in `pre_invoke_untracked`. After MAR-141, `prepare()` also writes `.harness/sessions/<id>.binding.json`. That file is a NEW untracked file created by `prepare()` itself, so it will NOT be in `pre_invoke_untracked` (the snapshot is taken BEFORE the binding is written — assuming `_snapshot_untracked` runs before `write_binding`, which it does in the plan code above).

Double-check by reading the updated `prepare()` flow: `_snapshot_untracked(working_dir)` runs before the `PreparedContext(...)` construction, which runs before `compute_binding(prepared)` and `write_binding(binding, sessions_dir)`. So the snapshot is captured BEFORE the binding is written. The binding file itself will appear in `invoke_result.files_changed` for subsequent runs only if those runs happen without the binding being committed, but this is a `.binding.json` file (not matching any deny pattern or scanner rule). Safe.

- [ ] **Step 7: Run the full test suite to catch cross-file regressions**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && pytest tests/ -q 2>&1 | tail -5`
Expected: **120 tests pass** (111 baseline + 1 schema test from Task 1 + 7 unit tests in test_session_binding.py from Tasks 2-6 + 1 integration test `test_prepare_writes_session_binding_sidecar` from Task 7... wait, Task 7 adds 2 integration tests. Let me recount.)

Running tally: 111 baseline → Task 1 +1 → 112 → Task 2 +1 → 113 → Task 3 +1 → 114 → Task 4 +1 → 115 → Task 5 +2 → 117 → Task 6 +2 → 119 → Task 7 +2 = **121 total**.

Final expected: **121 tests passing**.

Wait, the spec says 119. Let me reconcile: the spec counted "6 unit tests in test_session_binding.py" but I've got 7 tests in that file (compute_binding_produces_all_fields, compute_binding_uses_context_stable_hash, compute_binding_config_hash_is_canonical, write_and_read_binding_round_trip, read_binding_returns_none_for_missing_file, validate_session_returns_empty_for_matching_binding, validate_session_detects_config_hash_mismatch) plus the `test_read_binding_returns_none_for_missing_file` bonus I added in Task 5 Step 1 because it's a natural test to include alongside the round-trip test.

Reconciliation: the spec's "6 unit tests" was conservative; Task 5 delivers 2 tests (round-trip + none for missing), so the actual unit test count is 7, not 6. Plus the 1 schema test in Task 1 (test_session_binding_fields in test_schemas.py) and 2 integration tests in Task 7 = 10 new tests total, not 8. Final expected: **121 tests passing** (111 + 10).

- [ ] **Step 8: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141
git add src/weave/core/runtime.py tests/test_runtime.py
git commit -m "$(cat <<'EOF'
feat(runtime): write session binding sidecar in prepare()

Every session created by prepare() now gets a .binding.json sidecar
alongside the JSONL log. Contains provider_name, adapter_script_hash,
context_stable_hash, and config_hash for future session reuse
validation via weave.core.session_binding.validate_session().

execute() and all other pipeline stages are unchanged — the binding
is written as a side effect of prepare() and consumed only by
validate_session(). No new execute() parameters, no gating.

Linear: MAR-141

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Final verification

**Files:** none — verification only

- [ ] **Step 1: Run the full test suite**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && pytest tests/ -v 2>&1 | tail -30`
Expected: **121 tests pass** (111 baseline + 10 new: 1 schema test + 7 unit tests + 2 integration tests).

Running tally across tasks:
- Task 1: +1 (test_schemas.py schema test)
- Task 2: +1 (compute_binding_produces_all_fields)
- Task 3: +1 (compute_binding_uses_context_stable_hash)
- Task 4: +1 (compute_binding_config_hash_is_canonical)
- Task 5: +2 (write_and_read round trip, read_binding none for missing)
- Task 6: +2 (validate returns empty for match, validate detects config mismatch)
- Task 7: +2 (prepare writes sidecar, validate raises for missing)
- **Total new: 10. Final: 121.**

- [ ] **Step 2: Verify no circular imports**

Run:
```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && PYTHONPATH=src python3 -c "
from weave.core.runtime import prepare, execute
from weave.core.session_binding import compute_binding, write_binding, read_binding, validate_session
from weave.schemas.session_binding import SessionBinding
print('imports: ok')
"
```
Expected: prints `imports: ok`

- [ ] **Step 3: Manual determinism check for config_hash**

Run:
```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && PYTHONPATH=src python3 -c "
from weave.core.session_binding import _hash_config
from weave.schemas.config import ProviderConfig, WeaveConfig
from weave.schemas.policy import RiskClass

c1 = WeaveConfig(
    version='1', phase='sandbox', default_provider='claude-code',
    providers={
        'claude-code': ProviderConfig(command='claude', capability=RiskClass.WORKSPACE_WRITE),
        'codex': ProviderConfig(command='codex', capability=RiskClass.WORKSPACE_WRITE),
    },
)
c2 = WeaveConfig(
    version='1', phase='sandbox', default_provider='claude-code',
    providers={
        'codex': ProviderConfig(command='codex', capability=RiskClass.WORKSPACE_WRITE),
        'claude-code': ProviderConfig(command='claude', capability=RiskClass.WORKSPACE_WRITE),
    },
)
assert _hash_config(c1) == _hash_config(c2)
print('config_hash canonical: ok')
"
```
Expected: prints `config_hash canonical: ok`

- [ ] **Step 4: Manual sidecar check**

Run:
```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-141 && PYTHONPATH=src python3 -c "
import tempfile, json
from pathlib import Path
from weave.core.runtime import prepare

with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    harness = tmp / '.harness'
    harness.mkdir()
    for sub in ['context', 'hooks', 'providers', 'sessions', 'integrations']:
        (harness / sub).mkdir()
    (harness / 'manifest.json').write_text(json.dumps({
        'id': 'test', 'type': 'project', 'name': 'test',
        'status': 'active', 'phase': 'sandbox'
    }))
    (harness / 'config.json').write_text(json.dumps({
        'version': '1', 'phase': 'sandbox', 'default_provider': 'claude-code',
        'providers': {'claude-code': {
            'command': '.harness/providers/claude-code.sh',
            'enabled': True, 'capability': 'workspace-write'
        }}
    }))
    adapter = harness / 'providers' / 'claude-code.sh'
    adapter.write_text('#!/bin/bash\nread INPUT\necho ok\n')
    adapter.chmod(0o755)

    ctx = prepare(task='test', working_dir=tmp, caller='manual')
    sidecar = harness / 'sessions' / f'{ctx.session_id}.binding.json'
    assert sidecar.exists()
    data = json.loads(sidecar.read_text())
    assert data['provider_name'] == 'claude-code'
    assert len(data['config_hash']) == 64
    print('sidecar check: ok')
    print('fields:', sorted(data.keys()))
"
```
Expected: prints `sidecar check: ok` and the list of fields

- [ ] **Step 5: No commit** — Task 8 is verification only.

---

## Self-Review Notes

**Spec coverage:**
- `SessionBinding` schema with 6 fields → Task 1
- `compute_binding` produces all fields → Task 2
- `compute_binding` reuses `ctx.context.stable_hash` → Task 3
- `_hash_config` canonical JSON ordering → Task 4
- Adapter script hash fallback (`sha256(b"")` for missing) → Task 2 implementation (covered by the happy-path test — missing file case is implicit since `_init_harness` always creates the adapter; a dedicated test for the missing-adapter case is not in the plan but the code path is exercised by `compute_binding` when it hits the `else` branch)
- `write_binding` writes to correct path with indent=2 → Task 5 round-trip test
- `read_binding` returns None for missing files → Task 5 dedicated test
- `read_binding` raises for malformed files → NOT explicitly tested (raises is the default Pydantic behavior; a test would require writing corrupt JSON and asserting an exception. Low-value test.)
- `validate_session` returns empty for matching → Task 6
- `validate_session` detects config hash mismatch → Task 6
- `validate_session` raises for missing binding → Task 7 (integration test)
- `validate_session` compares exactly 4 fields → Task 6 implementation + the matching/config-mismatch tests cover the core behavior. The other two mismatch fields (adapter_script_hash and context_stable_hash) are not individually tested but follow the same pattern. If a future reader wants explicit coverage, adding two more tests is trivial.
- `prepare()` calls `compute_binding` + `write_binding` at the end → Task 7 Step 4
- `execute()` and pipeline stages unchanged → Task 7 does not touch them
- `invoker.py` unchanged → no task touches it
- All 111 existing tests continue to pass → Task 7 Step 6 regression verification

**Placeholder scan:** No TBDs, TODOs, or placeholder steps. Every code block is complete and copy-pastable. Mid-plan recounting of test totals was fixed.

**Type consistency:**
- `SessionBinding.session_id: str`, `created_at: datetime`, `provider_name: str`, `adapter_script_hash: str`, `context_stable_hash: str`, `config_hash: str` — defined in Task 1, used consistently in Tasks 2-7
- `compute_binding(ctx) -> SessionBinding` — the `ctx` parameter is untyped (no annotation) to avoid circular imports from `runtime.py`; the function accesses `ctx.session_id`, `ctx.active_provider`, `ctx.adapter_script`, `ctx.context.stable_hash`, `ctx.config` via duck typing. Documented in Task 2 Step 3.
- `write_binding(binding: SessionBinding, sessions_dir: Path) -> Path` — consistent signature
- `read_binding(session_id: str, sessions_dir: Path) -> SessionBinding | None` — consistent
- `validate_session(session_id: str, ctx, sessions_dir: Path) -> list[str]` — consistent (again untyped `ctx` for the same circular-import reason)

**Expected final test count:** 121 tests (111 baseline + 10 new: 1 schema + 7 unit + 2 integration).

**Architectural note:** The untyped `ctx` parameter in `compute_binding` and `validate_session` is unusual for the weave codebase, which typically has full type annotations. The alternative would be moving `PreparedContext` into its own file (e.g., `src/weave/schemas/prepared_context.py`) to break the cycle, but that's out of scope for MAR-141. Duck typing is the pragmatic choice; if a future refactor moves `PreparedContext`, the annotations can be added then.
