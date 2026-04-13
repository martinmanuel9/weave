# Integration Extension Points Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add generic extension points to Weave's runtime pipeline so external systems (Itzel/CJE, or any integrator) can receive enriched hook context, attach metadata to activity records, gate execution at a post-scan stage, and subscribe to activity events.

**Architecture:** Four focused changes across existing modules. No new files except tests. `HookContext` gains optional fields that are `None` when not yet available (pre-invoke). `HooksConfig` gains `post_scan` list. `runtime.execute()` gains `metadata` parameter. `WeaveConfig` gains runtime-only `on_activity` callback list.

**Tech Stack:** Python 3.12, pydantic v2, dataclasses, pytest.

**Spec reference:** [`.harness/context/spec.md`](../../../.harness/context/spec.md)

**Baseline test count:** 254 (verified on commit `c0263ca`).

**Target test count:** 275 (+21 new tests).

---

## File Structure

| File | Kind | Responsibility |
|---|---|---|
| `src/weave/core/hooks.py` | MODIFIED | Enrich `HookContext` with risk_class, files_changed, exit_code, security_findings, session_id, provider_contract |
| `src/weave/schemas/config.py` | MODIFIED | Add `post_scan` to `HooksConfig`, `on_activity` to `WeaveConfig` |
| `src/weave/core/runtime.py` | MODIFIED | Wire `metadata` param, post-scan hook stage, activity callbacks |
| `tests/test_hooks.py` | MODIFIED | Tests for enriched HookContext serialization |
| `tests/test_runtime.py` | MODIFIED | Tests for metadata passthrough, post-scan hooks, activity callbacks |

---

## Task 1: Enrich HookContext (REQ-1)

Add optional fields to `HookContext` so hooks receive governance data. Pre-invoke hooks get partial context (risk_class, session_id, provider_contract). Post-invoke/post-scan hooks get everything.

**Files:**
- Modify: `src/weave/core/hooks.py`
- Modify: `tests/test_hooks.py`

- [ ] **Step 1: Write failing tests for enriched HookContext**

Add to `tests/test_hooks.py`:

```python
# ---------------------------------------------------------------------------
# Enriched HookContext tests
# ---------------------------------------------------------------------------


def test_hook_context_to_dict_includes_new_fields():
    """AC-8: to_dict() includes all new fields."""
    ctx = HookContext(
        provider="claude-code",
        task="do stuff",
        working_dir="/tmp",
        phase="post-invoke",
        risk_class="workspace-write",
        session_id="sess-123",
        provider_contract="claude-code",
        files_changed=["main.py", "utils.py"],
        exit_code=0,
        security_findings=[{"rule_id": "pth-injection", "file": "evil.pth"}],
    )
    d = ctx.to_dict()
    assert d["risk_class"] == "workspace-write"
    assert d["session_id"] == "sess-123"
    assert d["provider_contract"] == "claude-code"
    assert d["files_changed"] == ["main.py", "utils.py"]
    assert d["exit_code"] == 0
    assert len(d["security_findings"]) == 1


def test_hook_context_pre_invoke_nulls():
    """AC-2: Pre-invoke context has None/[] for unavailable fields."""
    ctx = HookContext(
        provider="claude-code",
        task="do stuff",
        working_dir="/tmp",
        phase="pre-invoke",
        risk_class="workspace-write",
        session_id="sess-456",
        provider_contract="claude-code",
    )
    d = ctx.to_dict()
    assert d["risk_class"] == "workspace-write"
    assert d["session_id"] == "sess-456"
    assert d["files_changed"] == []
    assert d["exit_code"] is None
    assert d["security_findings"] == []


def test_hook_context_backwards_compat():
    """AC-7: Old-style construction with no new fields still works."""
    ctx = HookContext(
        provider="claude-code",
        task="x",
        working_dir="/tmp",
        phase="pre-invoke",
    )
    d = ctx.to_dict()
    assert d["risk_class"] is None
    assert d["session_id"] is None
    assert d["files_changed"] == []
    assert d["exit_code"] is None
    assert d["security_findings"] == []


def test_script_hook_receives_enriched_context(tmp_path: Path):
    """AC-1: Script hook receives JSON with new fields on stdin."""
    import stat

    # Script that echoes stdin to a file so we can inspect it
    output_file = tmp_path / "received.json"
    script = tmp_path / "inspector.sh"
    script.write_text(
        f'#!/usr/bin/env bash\ncat > {output_file}\nexit 0\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    ctx = HookContext(
        provider="claude-code",
        task="inspect",
        working_dir="/tmp",
        phase="post-invoke",
        risk_class="workspace-write",
        session_id="sess-789",
        provider_contract="claude-code",
        files_changed=["app.py"],
        exit_code=0,
        security_findings=[],
    )
    run_hooks([str(script)], ctx)

    import json
    received = json.loads(output_file.read_text())
    assert received["risk_class"] == "workspace-write"
    assert received["session_id"] == "sess-789"
    assert received["files_changed"] == ["app.py"]
    assert received["exit_code"] == 0
    assert received["security_findings"] == []
```

Run: `PYTHONPATH=src python3 -m pytest tests/test_hooks.py -x` — expect 4 failures (new fields don't exist yet).

- [ ] **Step 2: Add fields to HookContext**

Modify `src/weave/core/hooks.py` — update `HookContext`:

```python
@dataclass
class HookContext:
    provider: str
    task: str
    working_dir: str
    phase: str  # "pre-invoke", "post-invoke", or "post-scan"

    # Enriched fields (REQ-1) — None/[] when not yet available
    risk_class: str | None = None
    session_id: str | None = None
    provider_contract: str | None = None
    files_changed: list[str] = field(default_factory=list)
    exit_code: int | None = None
    security_findings: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "task": self.task,
            "working_dir": self.working_dir,
            "phase": self.phase,
            "risk_class": self.risk_class,
            "session_id": self.session_id,
            "provider_contract": self.provider_contract,
            "files_changed": self.files_changed,
            "exit_code": self.exit_code,
            "security_findings": self.security_findings,
        }
```

Note: add `from dataclasses import dataclass, field` (add `field` to the import).

Run: `PYTHONPATH=src python3 -m pytest tests/test_hooks.py -x` — all should pass.

- [ ] **Step 3: Wire enriched context into runtime pipeline**

Modify `src/weave/core/runtime.py` — update `_policy_check` to build enriched pre-invoke HookContext:

```python
# In _policy_check, replace the HookContext construction:
    hook_ctx = HookContext(
        provider=ctx.active_provider,
        task=ctx.task,
        working_dir=str(ctx.working_dir),
        phase="pre-invoke",
        risk_class=policy.effective_risk_class.value,
        session_id=ctx.session_id,
        provider_contract=ctx.active_provider,
    )
```

Modify `_cleanup` to build enriched post-invoke HookContext:

```python
def _cleanup(
    ctx: PreparedContext,
    invoke_result: InvokeResult | None,
    security_result: SecurityResult | None = None,
) -> list[HookResult]:
    """Stage 5: run post-invoke hooks."""
    if invoke_result is None:
        return []
    hook_ctx = HookContext(
        provider=ctx.active_provider,
        task=ctx.task,
        working_dir=str(ctx.working_dir),
        phase="post-invoke",
        risk_class=ctx.requested_risk_class.value if ctx.requested_risk_class else None,
        session_id=ctx.session_id,
        provider_contract=ctx.active_provider,
        files_changed=invoke_result.files_changed,
        exit_code=invoke_result.exit_code,
        security_findings=[
            f.model_dump(mode="json")
            for f in (security_result.findings if security_result else [])
        ],
    )
    chain = run_hooks(ctx.config.hooks.post_invoke, hook_ctx)
    return chain.results
```

Also update the `_cleanup` call in `execute()` to pass `security_result`:

```python
        post_hook_results = _cleanup(ctx, invoke_result, security_result)
```

Run: `PYTHONPATH=src python3 -m pytest tests/test_hooks.py tests/test_runtime.py -x` — all should pass.

---

## Task 2: Caller Metadata Passthrough (REQ-2)

Add `metadata` parameter to `execute()` and `prepare()` that flows to `ActivityRecord.metadata`.

**Files:**
- Modify: `src/weave/core/runtime.py`
- Modify: `tests/test_runtime.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_runtime.py`:

```python
def test_execute_metadata_passthrough(temp_dir):
    """AC-3: metadata kwarg lands in ActivityRecord.metadata."""
    from weave.core.runtime import execute
    from weave.core.session import read_session_activities
    _init_harness(temp_dir)
    result = execute(
        task="with meta",
        working_dir=temp_dir,
        caller="test",
        metadata={"cje_score": 0.87, "intent": "code_generation"},
    )
    assert result.status == RuntimeStatus.SUCCESS
    sessions_dir = temp_dir / ".harness" / "sessions"
    records = read_session_activities(sessions_dir, result.session_id)
    assert records[0].metadata["cje_score"] == 0.87
    assert records[0].metadata["intent"] == "code_generation"


def test_execute_no_metadata_defaults_empty(temp_dir):
    """AC-7: No metadata parameter = empty dict (backwards compat)."""
    from weave.core.runtime import execute
    from weave.core.session import read_session_activities
    _init_harness(temp_dir)
    result = execute(task="no meta", working_dir=temp_dir, caller="test")
    sessions_dir = temp_dir / ".harness" / "sessions"
    records = read_session_activities(sessions_dir, result.session_id)
    assert records[0].metadata == {}
```

Run: `PYTHONPATH=src python3 -m pytest tests/test_runtime.py::test_execute_metadata_passthrough -x` — expect failure.

- [ ] **Step 2: Add metadata to execute(), prepare(), PreparedContext, and _record()**

In `src/weave/core/runtime.py`:

1. Add `metadata: dict` to `PreparedContext`:

```python
@dataclass
class PreparedContext:
    """Everything the pipeline needs after the prepare stage."""
    config: WeaveConfig
    active_provider: str
    provider_config: ProviderConfig
    provider_contract: ProviderContract
    adapter_script: Path
    context: ContextAssembly
    session_id: str
    working_dir: Path
    phase: str
    task: str
    caller: str | None
    requested_risk_class: RiskClass | None
    pre_invoke_untracked: set[str]
    metadata: dict = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
```

2. Add `metadata` param to `prepare()`:

```python
def prepare(
    task: str,
    working_dir: Path,
    provider: str | None = None,
    caller: str | None = None,
    requested_risk_class: RiskClass | None = None,
    session_id: str | None = None,
    metadata: dict | None = None,
) -> PreparedContext:
```

And pass it through to `PreparedContext(... metadata=metadata or {})`.

3. Add `metadata` param to `execute()`:

```python
def execute(
    task: str,
    working_dir: Path,
    provider: str | None = None,
    caller: str | None = None,
    requested_risk_class: RiskClass | None = None,
    timeout: int = 300,
    session_id: str | None = None,
    metadata: dict | None = None,
) -> RuntimeResult:
```

Pass `metadata=metadata` to `prepare(...)`.

4. In `_record()`, add `metadata` param and set on the `ActivityRecord`:

```python
def _record(
    ctx: PreparedContext,
    invoke_result: InvokeResult | None,
    policy_result: PolicyResult,
    security_result: SecurityResult | None,
    pre_hook_results: list[HookResult],
    post_hook_results: list[HookResult],
    status: RuntimeStatus,
    metadata: dict | None = None,
) -> ActivityRecord:
```

Set `metadata=metadata or {}` on the `ActivityRecord` constructor. Change return type from `None` to `ActivityRecord` (needed for Task 4).

5. Update all `_record(...)` call sites in `execute()` to pass `metadata=ctx.metadata`.

Run: `PYTHONPATH=src python3 -m pytest tests/test_runtime.py -x` — all should pass.

---

## Task 3: Post-Scan Hook Stage (REQ-3)

Add `post_scan` hook list and wire it between security scanning and cleanup.

**Files:**
- Modify: `src/weave/schemas/config.py`
- Modify: `src/weave/core/runtime.py`
- Modify: `tests/test_runtime.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_runtime.py`:

```python
def test_post_scan_hook_runs_on_success(temp_dir):
    """AC-4: post_scan hook runs after security scan on success."""
    import json as _json, stat
    from weave.core.runtime import execute
    _init_harness(temp_dir)

    # Create a post-scan hook that writes a marker file
    marker = temp_dir / "post_scan_ran"
    hook = temp_dir / ".harness" / "hooks" / "gate.sh"
    hook.write_text(f'#!/bin/bash\ntouch {marker}\nexit 0\n')
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["hooks"] = {"post_scan": [str(hook)]}
    config_path.write_text(_json.dumps(config))

    result = execute(task="go", working_dir=temp_dir, caller="test")
    assert result.status == RuntimeStatus.SUCCESS
    assert marker.exists()


def test_post_scan_hook_deny_triggers_revert(temp_dir):
    """AC-4: post_scan deny sets DENIED and reverts files."""
    import json as _json, stat, subprocess
    from weave.core.runtime import execute
    _init_harness(temp_dir)

    # Set up git so revert can work
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=temp_dir, check=True)
    (temp_dir / ".gitignore").write_text(".harness/\n")
    (temp_dir / "seed.txt").write_text("original")
    subprocess.run(["git", "add", ".gitignore", "seed.txt"], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    # Adapter modifies seed.txt
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo "modified" > seed.txt\n'
        'echo \'{"protocol": "weave.response.v1", "exitCode": 0, '
        '"stdout": "done", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    # Post-scan hook that denies
    hook = temp_dir / ".harness" / "hooks" / "deny-gate.sh"
    hook.write_text('#!/bin/bash\necho "quality too low" >&2\nexit 1\n')
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["hooks"] = {"post_scan": [str(hook)]}
    config_path.write_text(_json.dumps(config))

    result = execute(task="modify seed", working_dir=temp_dir, caller="test")
    assert result.status == RuntimeStatus.DENIED

    # seed.txt should be reverted
    assert (temp_dir / "seed.txt").read_text() == "original"


def test_post_scan_hook_skipped_on_invoke_failure(temp_dir):
    """AC-5: post_scan hooks do NOT run when invoke fails."""
    import json as _json, stat
    from weave.core.runtime import execute
    _init_harness(temp_dir)

    # Adapter that fails
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo \'{"protocol": "weave.response.v1", "exitCode": 1, '
        '"stdout": "", "stderr": "boom", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    # Post-scan hook that writes a marker
    marker = temp_dir / "should_not_exist"
    hook = temp_dir / ".harness" / "hooks" / "gate.sh"
    hook.write_text(f'#!/bin/bash\ntouch {marker}\nexit 0\n')
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["hooks"] = {"post_scan": [str(hook)]}
    config_path.write_text(_json.dumps(config))

    result = execute(task="fail", working_dir=temp_dir, caller="test")
    assert result.status == RuntimeStatus.FAILED
    assert not marker.exists()


def test_post_scan_hook_skipped_on_timeout(temp_dir):
    """AC-5: post_scan hooks do NOT run on timeout."""
    import json as _json, stat
    from weave.core.runtime import execute
    _init_harness(temp_dir)

    # Adapter that returns timeout exit code
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo \'{"protocol": "weave.response.v1", "exitCode": 124, '
        '"stdout": "", "stderr": "timed out", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    marker = temp_dir / "should_not_exist"
    hook = temp_dir / ".harness" / "hooks" / "gate.sh"
    hook.write_text(f'#!/bin/bash\ntouch {marker}\nexit 0\n')
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["hooks"] = {"post_scan": [str(hook)]}
    config_path.write_text(_json.dumps(config))

    result = execute(task="timeout", working_dir=temp_dir, caller="test")
    assert result.status == RuntimeStatus.TIMEOUT
    assert not marker.exists()


def test_post_scan_hook_receives_enriched_context(temp_dir):
    """AC-1 + AC-4: post_scan hook receives security findings and files_changed."""
    import json as _json, stat
    from weave.core.runtime import execute
    _init_harness(temp_dir)

    output_file = temp_dir / "hook_input.json"
    hook = temp_dir / ".harness" / "hooks" / "inspector.sh"
    hook.write_text(f'#!/bin/bash\ncat > {output_file}\nexit 0\n')
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["hooks"] = {"post_scan": [str(hook)]}
    config_path.write_text(_json.dumps(config))

    result = execute(task="inspect", working_dir=temp_dir, caller="test")
    assert result.status == RuntimeStatus.SUCCESS
    assert output_file.exists()

    received = _json.loads(output_file.read_text())
    assert "risk_class" in received
    assert "session_id" in received
    assert "files_changed" in received
    assert "exit_code" in received
    assert "security_findings" in received
    assert received["phase"] == "post-scan"
```

Run: `PYTHONPATH=src python3 -m pytest tests/test_runtime.py::test_post_scan_hook_runs_on_success -x` — expect failure.

- [ ] **Step 2: Add `post_scan` to HooksConfig**

In `src/weave/schemas/config.py`:

```python
class HooksConfig(BaseModel):
    pre_invoke: list[str] = Field(default_factory=list)
    post_invoke: list[str] = Field(default_factory=list)
    post_scan: list[str] = Field(default_factory=list)
    pre_commit: list[str] = Field(default_factory=list)
```

- [ ] **Step 3: Add `_post_scan_gate` and wire into execute()**

In `src/weave/core/runtime.py`, add a new function:

```python
def _post_scan_gate(
    ctx: PreparedContext,
    invoke_result: InvokeResult,
    security_result: SecurityResult,
    policy: PolicyResult,
) -> tuple[list[HookResult], bool]:
    """Stage 4b: run post-scan hooks. Returns (results, denied)."""
    if not ctx.config.hooks.post_scan:
        return [], False

    hook_ctx = HookContext(
        provider=ctx.active_provider,
        task=ctx.task,
        working_dir=str(ctx.working_dir),
        phase="post-scan",
        risk_class=policy.effective_risk_class.value,
        session_id=ctx.session_id,
        provider_contract=ctx.active_provider,
        files_changed=invoke_result.files_changed,
        exit_code=invoke_result.exit_code,
        security_findings=[
            f.model_dump(mode="json") for f in security_result.findings
        ],
    )
    chain = run_hooks(ctx.config.hooks.post_scan, hook_ctx)
    return chain.results, not chain.allowed
```

Then update `execute()` to insert the post-scan stage. In the success branch (where `security_result` is computed and status is not TIMEOUT/FAILED), after the security scan determines status, add:

```python
            # Post-scan quality gate (REQ-3)
            if status in (RuntimeStatus.SUCCESS, RuntimeStatus.FLAGGED):
                post_scan_results, post_scan_denied = _post_scan_gate(
                    ctx, invoke_result, security_result, policy,
                )
                if post_scan_denied:
                    status = RuntimeStatus.DENIED
                    # Force revert by marking security as denied
                    security_result.action_taken = "denied"
            else:
                post_scan_results = []
```

For TIMEOUT and FAILED branches, set `post_scan_results = []`.

Include `post_scan_results` in the `_record(...)` call — append them to `post_hook_results` or pass separately. Simplest: combine them with post_hook_results since they're all `HookResult`:

```python
        post_hook_results = _cleanup(ctx, invoke_result, security_result)

        _revert(ctx, invoke_result, security_result)

        all_post_hooks = post_scan_results + post_hook_results
        _record(
            ctx, invoke_result, policy, security_result,
            pre_hook_results, all_post_hooks, status,
            metadata=ctx.metadata,
        )
```

Run: `PYTHONPATH=src python3 -m pytest tests/test_runtime.py -x` — all should pass.

---

## Task 4: Activity Event Callbacks (REQ-4)

Add `on_activity` runtime-only callback list to `WeaveConfig`.

**Files:**
- Modify: `src/weave/schemas/config.py`
- Modify: `src/weave/core/runtime.py`
- Modify: `tests/test_runtime.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_runtime.py`:

```python
def test_on_activity_callback_receives_record(temp_dir):
    """AC-6: Callback receives the ActivityRecord after recording."""
    from weave.core.runtime import execute
    from weave.schemas.activity import ActivityRecord
    _init_harness(temp_dir)

    received = []
    def capture(record: ActivityRecord):
        received.append(record)

    # Patch the config after init to add the callback
    from weave.core.runtime import prepare
    from weave.core.config import resolve_config
    import json as _json

    result = execute(
        task="callback test",
        working_dir=temp_dir,
        caller="test",
        on_activity=[capture],
    )
    assert result.status == RuntimeStatus.SUCCESS
    assert len(received) == 1
    assert received[0].task == "callback test"
    assert received[0].session_id == result.session_id


def test_on_activity_callback_failure_does_not_crash(temp_dir):
    """AC-6: Callback exception is logged but pipeline succeeds."""
    from weave.core.runtime import execute
    _init_harness(temp_dir)

    def exploding_callback(record):
        raise RuntimeError("callback boom")

    result = execute(
        task="survives callback",
        working_dir=temp_dir,
        caller="test",
        on_activity=[exploding_callback],
    )
    assert result.status == RuntimeStatus.SUCCESS


def test_on_activity_not_serialized_to_config(temp_dir):
    """AC-7: on_activity is excluded from JSON serialization."""
    from weave.schemas.config import WeaveConfig
    config = WeaveConfig(on_activity=[lambda r: None])
    dumped = config.model_dump(mode="json")
    assert "on_activity" not in dumped


def test_execute_no_on_activity_backwards_compat(temp_dir):
    """AC-7: Omitting on_activity works identically to before."""
    from weave.core.runtime import execute
    _init_harness(temp_dir)
    result = execute(task="compat", working_dir=temp_dir, caller="test")
    assert result.status == RuntimeStatus.SUCCESS
```

Run: `PYTHONPATH=src python3 -m pytest tests/test_runtime.py::test_on_activity_callback_receives_record -x` — expect failure.

- [ ] **Step 2: Add on_activity to WeaveConfig (excluded from serialization)**

In `src/weave/schemas/config.py`:

```python
from typing import Any, Callable

class WeaveConfig(BaseModel):
    version: str = "1"
    phase: str = "sandbox"
    default_provider: str = "claude-code"
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    volatile_context: VolatileContextConfig = Field(default_factory=VolatileContextConfig)
    on_activity: list[Callable] = Field(default_factory=list, exclude=True)
```

The `exclude=True` ensures `model_dump(mode="json")` omits this field. Also need to add `model_config = ConfigDict(arbitrary_types_allowed=True)` if not already present, since `Callable` isn't a JSON-serializable type.

- [ ] **Step 3: Add on_activity param to execute() and wire callbacks**

In `src/weave/core/runtime.py`:

1. Add `on_activity` param to `execute()`:

```python
def execute(
    task: str,
    working_dir: Path,
    provider: str | None = None,
    caller: str | None = None,
    requested_risk_class: RiskClass | None = None,
    timeout: int = 300,
    session_id: str | None = None,
    metadata: dict | None = None,
    on_activity: list | None = None,
) -> RuntimeResult:
```

2. After `prepare()` returns, merge the callbacks into config:

```python
    if on_activity:
        ctx.config.on_activity = on_activity
```

3. Change `_record()` to return the `ActivityRecord` and fire callbacks:

```python
def _record(
    ctx: PreparedContext,
    invoke_result: InvokeResult | None,
    policy_result: PolicyResult,
    security_result: SecurityResult | None,
    pre_hook_results: list[HookResult],
    post_hook_results: list[HookResult],
    status: RuntimeStatus,
    metadata: dict | None = None,
) -> ActivityRecord:
    """Stage 7: append ActivityRecord, fire on_activity callbacks."""
    # ... existing record construction ...
    record = ActivityRecord(
        # ... existing fields ...
        metadata=metadata or {},
    )
    compact_threshold = ctx.config.sessions.compaction.records_per_session
    append_activity(sessions_dir, ctx.session_id, record, compact_threshold=compact_threshold)

    # Fire activity event callbacks (REQ-4)
    for cb in ctx.config.on_activity:
        try:
            cb(record)
        except Exception:
            _logger.exception("on_activity callback %r failed", cb)

    return record
```

Run: `PYTHONPATH=src python3 -m pytest tests/test_runtime.py -x` — all should pass.

---

## Task 5: Full Integration Test + Final Verification

End-to-end test combining all four features, plus full test suite run.

**Files:**
- Modify: `tests/test_runtime.py`

- [ ] **Step 1: Write integration test**

Add to `tests/test_runtime.py`:

```python
def test_full_integration_all_extension_points(temp_dir):
    """Integration: metadata + enriched hooks + post-scan + callback all together."""
    import json as _json, stat
    from weave.core.runtime import execute
    from weave.core.session import read_session_activities
    _init_harness(temp_dir)

    # Post-scan hook that inspects context
    context_file = temp_dir / "post_scan_context.json"
    hook = temp_dir / ".harness" / "hooks" / "inspector.sh"
    hook.write_text(f'#!/bin/bash\ncat > {context_file}\nexit 0\n')
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["hooks"] = {"post_scan": [str(hook)]}
    config_path.write_text(_json.dumps(config))

    # Activity callback
    callback_records = []
    def capture(record):
        callback_records.append(record)

    result = execute(
        task="full integration",
        working_dir=temp_dir,
        caller="itzel",
        metadata={"cje_score": 0.92, "routing_intent": "code_gen"},
        on_activity=[capture],
    )

    # Pipeline succeeded
    assert result.status == RuntimeStatus.SUCCESS

    # Metadata landed in ActivityRecord
    sessions_dir = temp_dir / ".harness" / "sessions"
    records = read_session_activities(sessions_dir, result.session_id)
    assert records[0].metadata["cje_score"] == 0.92

    # Post-scan hook received enriched context
    assert context_file.exists()
    received = _json.loads(context_file.read_text())
    assert received["phase"] == "post-scan"
    assert received["session_id"] == result.session_id
    assert "risk_class" in received

    # Callback fired
    assert len(callback_records) == 1
    assert callback_records[0].metadata["routing_intent"] == "code_gen"
```

- [ ] **Step 2: Run full test suite**

```bash
PYTHONPATH=src python3 -m pytest -x -q
```

Expect: 275 tests, all passing. Baseline was 254 → +21 new tests.

---

## Summary

| Task | REQ | Tests Added | Files Modified |
|---|---|---|---|
| Task 1: Enrich HookContext | REQ-1 | +4 | `hooks.py`, `runtime.py`, `test_hooks.py` |
| Task 2: Metadata Passthrough | REQ-2 | +2 | `runtime.py`, `test_runtime.py` |
| Task 3: Post-Scan Hook Stage | REQ-3 | +5 | `config.py`, `runtime.py`, `test_runtime.py` |
| Task 4: Activity Callbacks | REQ-4 | +4 | `config.py`, `runtime.py`, `test_runtime.py` |
| Task 5: Integration | — | +1 | `test_runtime.py` |
| **Total** | | **+21** | **4 source + 2 test files** |

**Dependency order:** Task 1 → Task 3 (post-scan needs enriched context), Task 2 is independent, Task 4 is independent, Task 5 depends on all.

**Parallel-safe pairs:** Task 1 + Task 2 can run in parallel. Task 3 depends on Task 1. Task 4 is independent. Task 5 is last.
