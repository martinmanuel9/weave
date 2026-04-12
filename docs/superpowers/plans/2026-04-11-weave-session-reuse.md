# Session Reuse + Binding Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate session binding validation so reused sessions detect config/context/provider drift and respond according to a configurable policy (warn/rebind/strict).

**Architecture:** `SessionBindingPolicy` enum on `SessionsConfig`. `prepare()` and `execute()` gain an optional `session_id` parameter. When provided, `prepare()` validates the stored binding against the current context and applies the policy.

**Tech Stack:** Python 3.12, pydantic v2, pytest.

**Spec reference:** [`docs/superpowers/specs/2026-04-11-weave-session-reuse-design.md`](../specs/2026-04-11-weave-session-reuse-design.md)

**Baseline test count:** 244.

**Target test count:** 249 (+5).

---

## Task 1: Add `SessionBindingPolicy` enum + config field

**Files:**
- Modify: `src/weave/schemas/config.py`
- Modify: `tests/test_session_binding.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_session_binding.py`:

```python
def test_session_binding_policy_config_defaults():
    from weave.schemas.config import SessionBindingPolicy, SessionsConfig
    cfg = SessionsConfig()
    assert cfg.binding_policy == SessionBindingPolicy.WARN
    assert cfg.binding_policy.value == "warn"
```

- [ ] **Step 2: Run to confirm failure**

Run: `PYTHONPATH=src pytest tests/test_session_binding.py -v -k "binding_policy" 2>&1 | tail -20`
Expected: `ImportError` for `SessionBindingPolicy`.

- [ ] **Step 3: Add the enum and config field**

In `src/weave/schemas/config.py`, add the enum before `CompactionConfig` (after the existing imports section, alongside other enums):

```python
class SessionBindingPolicy(str, Enum):
    WARN = "warn"
    REBIND = "rebind"
    STRICT = "strict"
```

Note: `Enum` may not be imported yet. Check — if `from enum import Enum` is missing, add it. Actually, the file uses pydantic `BaseModel` and `Field` but enums are in `weave.schemas.policy`. Since `SessionBindingPolicy` is a config-level enum, define it here with its own `Enum` import.

Add `from enum import Enum` at the top if not already present.

Update `SessionsConfig`:

```python
class SessionsConfig(BaseModel):
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    binding_policy: SessionBindingPolicy = SessionBindingPolicy.WARN
```

- [ ] **Step 4: Run the test**

Run: `PYTHONPATH=src pytest tests/test_session_binding.py -v -k "binding_policy" 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 5: Run full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -5`
Expected: 245 passed.

- [ ] **Step 6: Commit**

```bash
git add src/weave/schemas/config.py tests/test_session_binding.py
git commit -m "feat(config): add SessionBindingPolicy enum with warn/rebind/strict"
```

---

## Task 2: Wire session_id into prepare() + execute() with binding validation

**Files:**
- Modify: `src/weave/core/runtime.py`
- Modify: `tests/test_session_binding.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session_binding.py`:

```python
import json
import logging
import subprocess
from pathlib import Path

import pytest


def _make_project_with_binding(tmp_path, session_id="existing-sess", phase="mvp"):
    """Create a minimal weave project with git, config, and an existing binding."""
    from weave.core import registry as registry_module
    registry_module._REGISTRY_SINGLETON = None

    repo = tmp_path
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)

    harness = repo / ".harness"
    for sub in ["context", "hooks", "providers", "sessions", "integrations"]:
        (harness / sub).mkdir(parents=True, exist_ok=True)

    (harness / "manifest.json").write_text(json.dumps({
        "id": "t", "type": "project", "name": "t", "status": "active",
        "phase": phase, "parent": None, "children": [],
        "provider": "claude-code", "agent": None,
        "created": "2026-04-11T00:00:00Z", "updated": "2026-04-11T00:00:00Z",
        "inputs": {}, "outputs": {}, "tags": [],
    }))
    (harness / "config.json").write_text(json.dumps({
        "version": "1", "phase": phase, "default_provider": "claude-code",
        "providers": {"claude-code": {"command": "claude", "enabled": True}},
    }))
    (harness / "context" / "conventions.md").write_text("# Conventions\nBe nice.\n")

    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    # Create an initial binding by calling prepare() once
    from weave.core.runtime import prepare
    ctx = prepare(task="setup", working_dir=repo)
    original_session_id = ctx.session_id

    # Now write a binding for our target session_id using the current state
    from weave.core.session_binding import compute_binding, write_binding
    binding = compute_binding(ctx)
    # Overwrite session_id in the binding
    binding = binding.model_copy(update={"session_id": session_id})
    write_binding(binding, harness / "sessions")

    return repo, session_id


def test_prepare_with_session_id_validates_binding_warn(tmp_path, caplog):
    from weave.core.runtime import prepare
    from weave.core import registry as registry_module

    repo, session_id = _make_project_with_binding(tmp_path)

    # Change config to cause drift
    config_path = repo / ".harness" / "config.json"
    config_path.write_text(json.dumps({
        "version": "2",  # changed!
        "phase": "mvp", "default_provider": "claude-code",
        "providers": {"claude-code": {"command": "claude", "enabled": True}},
        "sessions": {"binding_policy": "warn"},
    }))

    registry_module._REGISTRY_SINGLETON = None
    with caplog.at_level(logging.WARNING):
        ctx = prepare(task="reuse test", working_dir=repo, session_id=session_id)

    assert ctx.session_id == session_id
    assert any("config_hash" in rec.message for rec in caplog.records)


def test_prepare_with_session_id_validates_binding_rebind(tmp_path, caplog):
    from weave.core.runtime import prepare
    from weave.core import registry as registry_module

    repo, session_id = _make_project_with_binding(tmp_path)

    config_path = repo / ".harness" / "config.json"
    config_path.write_text(json.dumps({
        "version": "2",
        "phase": "mvp", "default_provider": "claude-code",
        "providers": {"claude-code": {"command": "claude", "enabled": True}},
        "sessions": {"binding_policy": "rebind"},
    }))

    registry_module._REGISTRY_SINGLETON = None
    with caplog.at_level(logging.INFO):
        ctx = prepare(task="reuse test", working_dir=repo, session_id=session_id)

    assert ctx.session_id == session_id
    # Should be info level, not warning
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    binding_warnings = [r for r in warning_records if "config_hash" in r.message]
    assert len(binding_warnings) == 0


def test_prepare_with_session_id_validates_binding_strict(tmp_path):
    from weave.core.runtime import prepare
    from weave.core import registry as registry_module

    repo, session_id = _make_project_with_binding(tmp_path)

    config_path = repo / ".harness" / "config.json"
    config_path.write_text(json.dumps({
        "version": "2",
        "phase": "mvp", "default_provider": "claude-code",
        "providers": {"claude-code": {"command": "claude", "enabled": True}},
        "sessions": {"binding_policy": "strict"},
    }))

    registry_module._REGISTRY_SINGLETON = None
    with pytest.raises(ValueError, match="config_hash"):
        prepare(task="reuse test", working_dir=repo, session_id=session_id)


def test_prepare_with_session_id_missing_binding(tmp_path):
    from weave.core.runtime import prepare
    from weave.core import registry as registry_module
    registry_module._REGISTRY_SINGLETON = None

    repo, _ = _make_project_with_binding(tmp_path, session_id="dummy")

    # Use a session_id that has NO binding sidecar
    registry_module._REGISTRY_SINGLETON = None
    ctx = prepare(task="fresh session", working_dir=repo, session_id="brand-new-sess")
    assert ctx.session_id == "brand-new-sess"
    # Binding should have been written
    assert (repo / ".harness" / "sessions" / "brand-new-sess.binding.json").exists()
```

- [ ] **Step 2: Run to confirm failure**

Run: `PYTHONPATH=src pytest tests/test_session_binding.py -v -k "prepare_with_session" 2>&1 | tail -30`
Expected: `TypeError: prepare() got an unexpected keyword argument 'session_id'`

- [ ] **Step 3: Update `prepare()` in `runtime.py`**

Read `src/weave/core/runtime.py` to understand the current `prepare()` function. Then:

Add `session_id: str | None = None` parameter. Add `import logging` if not present. Add validation logic.

The updated `prepare()` signature:

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

Inside `prepare()`, find where `session_id = create_session()` is called. Replace that section with:

```python
    if session_id is None:
        session_id = create_session()
```

Then, after the `PreparedContext` is built and the volatile context is wired, find where `write_binding` is called. Replace the binding section with:

```python
    # Session binding: validate if reusing, write if new
    sessions_dir = working_dir / ".harness" / "sessions"
    existing_binding = read_binding(session_id, sessions_dir) if session_id != create_session.__name__ else None
```

Actually, that's awkward. Let me think more carefully. The current flow is:

```python
    context = assemble_context(working_dir)
    session_id = create_session()         # ← this is what we're changing
    # ... volatile context ...
    prepared = PreparedContext(...)
    binding = compute_binding(prepared)
    sessions_dir = working_dir / ".harness" / "sessions"
    write_binding(binding, sessions_dir)
```

The new flow should be:

```python
    context = assemble_context(working_dir)
    is_reuse = session_id is not None
    if not is_reuse:
        session_id = create_session()
    # ... volatile context ...
    prepared = PreparedContext(...)

    # Session binding
    sessions_dir = working_dir / ".harness" / "sessions"
    if is_reuse:
        _validate_and_rebind(prepared, sessions_dir, config.sessions.binding_policy)
    else:
        binding = compute_binding(prepared)
        write_binding(binding, sessions_dir)
```

Add the `_validate_and_rebind` helper function to `runtime.py`:

```python
_logger = logging.getLogger(__name__)


def _validate_and_rebind(
    ctx: PreparedContext,
    sessions_dir: Path,
    policy: str,
) -> None:
    """Validate an existing session binding and apply the binding policy.

    If the binding is missing, writes a fresh one (first invocation of
    an externally-created session).
    """
    from weave.schemas.config import SessionBindingPolicy

    existing = read_binding(ctx.session_id, sessions_dir)
    if existing is None:
        # No binding exists — write a fresh one
        binding = compute_binding(ctx)
        write_binding(binding, sessions_dir)
        return

    mismatches = validate_session(ctx.session_id, ctx, sessions_dir)
    if not mismatches:
        return  # binding still valid, no rewrite needed

    mismatch_str = ", ".join(mismatches)

    if policy == SessionBindingPolicy.STRICT:
        raise ValueError(
            f"Session {ctx.session_id} binding has drifted: {mismatch_str}. "
            f"Binding policy is 'strict' — refusing to proceed."
        )

    if policy == SessionBindingPolicy.WARN:
        _logger.warning(
            "Session %s binding drifted on: %s — rebinding (policy=warn)",
            ctx.session_id, mismatch_str,
        )
    else:
        # rebind
        _logger.info(
            "Session %s binding drifted on: %s — rebinding (policy=rebind)",
            ctx.session_id, mismatch_str,
        )

    # Rewrite binding with current state
    binding = compute_binding(ctx)
    write_binding(binding, sessions_dir)
```

Also add `validate_session` to the imports from `session_binding`:

```python
from weave.core.session_binding import compute_binding, read_binding, validate_session, write_binding
```

And add `import logging` near the top if not present.

- [ ] **Step 4: Update `execute()` to accept `session_id`**

Find `execute()` and add `session_id: str | None = None` parameter. Pass it to `prepare()`:

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
    """Run the full 7-stage pipeline and return a RuntimeResult."""
    ctx = prepare(
        task=task,
        working_dir=working_dir,
        provider=provider,
        caller=caller,
        requested_risk_class=requested_risk_class,
        session_id=session_id,
    )
```

- [ ] **Step 5: Run the session binding tests**

Run: `PYTHONPATH=src pytest tests/test_session_binding.py -v -k "prepare_with_session or binding_policy" 2>&1 | tail -30`
Expected: 5 passed (1 from Task 1 + 4 from Task 2).

- [ ] **Step 6: Run the full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -10`
Expected: 249 passed (244 + 5).

If any existing tests fail because `prepare()` signature changed, they should still work — the new parameter is optional with default `None`. But check.

- [ ] **Step 7: Commit**

```bash
git add src/weave/core/runtime.py tests/test_session_binding.py
git commit -m "feat(runtime): session reuse with binding validation and configurable policy"
```

---

## Task 3: Final verification

- [ ] **Step 1: Full test suite**

Run: `PYTHONPATH=src pytest tests/ -v 2>&1 | tail -30`
Expected: **249 passed**.

- [ ] **Step 2: Import check**

```bash
PYTHONPATH=src python3 -c "
from weave.schemas.config import SessionBindingPolicy, SessionsConfig
from weave.core.runtime import prepare, execute
print('SessionBindingPolicy values:', [p.value for p in SessionBindingPolicy])
print('imports: ok')
"
```

- [ ] **Step 3: Smoke test**

```bash
PYTHONPATH=src python3 -c "
from weave.schemas.config import SessionBindingPolicy
print('warn:', SessionBindingPolicy.WARN.value)
print('rebind:', SessionBindingPolicy.REBIND.value)
print('strict:', SessionBindingPolicy.STRICT.value)
print('smoke: ok')
"
```

- [ ] **Step 4: No commit** — verification only.

---

## Self-Review Notes

**Spec coverage:** SessionBindingPolicy enum (Task 1), prepare()/execute() session_id param (Task 2), warn/rebind/strict policy behavior (Task 2), missing binding handling (Task 2), regression test for None session_id (existing tests cover this).

**Type consistency:** `_validate_and_rebind(ctx, sessions_dir, policy)` uses `SessionBindingPolicy` enum values consistently. `validate_session` signature matches `session_binding.py`.

**No placeholders.** All code blocks complete.
