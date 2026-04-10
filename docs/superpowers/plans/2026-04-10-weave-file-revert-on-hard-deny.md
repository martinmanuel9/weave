# MAR-139 Implementation Plan — File Revert on Hard-Deny

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the security scan hard-denies an invocation in mvp/enterprise phase, roll back all files the invocation changed using git state, populating the previously-always-empty `SecurityResult.files_reverted`.

**Architecture:** Extends the runtime pipeline from 6 stages to 7 by inserting a new `_revert` stage between `_cleanup` and `_record`. `prepare()` captures a snapshot of pre-existing untracked files to protect pre-existing user work. `_revert` classifies each denied file via `git cat-file -e HEAD:<file>` — tracked files get `git checkout HEAD --`, new untracked files get `rm`, pre-existing untracked files are skipped. Best-effort, non-fatal: individual file failures are logged and the pipeline continues.

**Tech Stack:** Python 3.10+, stdlib `subprocess` for git commands, pytest with git-repo setup in `temp_dir`. No new dependencies.

**Reference spec:** `docs/superpowers/specs/2026-04-10-weave-file-revert-on-hard-deny-design.md`

**Linear:** [MAR-139](https://linear.app/martymanny/issue/MAR-139)

---

## File Structure

### Modified files

| File | Change |
|------|--------|
| `src/weave/core/runtime.py` | Add `pre_invoke_untracked: set[str]` field to `PreparedContext`; populate in `prepare()` via `git ls-files --others --exclude-standard`; add new `_revert()` stage function; wire into `execute()` between `_cleanup` and `_record`; update pipeline docstring at top of file to reflect 7 stages |
| `tests/test_runtime.py` | Add 5 integration tests covering: untracked-file revert, tracked-file revert, all-files-changed invariant, pre-existing-untracked preservation, sandbox no-op |

### No new files, no other modifications

`src/weave/schemas/policy.py` is untouched — `SecurityResult.files_reverted` already exists from Phase 1.
`src/weave/core/security.py` is untouched — the scan stays pure.
`src/weave/core/invoker.py` is untouched — the invoker stays thin.

---

## Task 1: Add `pre_invoke_untracked` snapshot to `PreparedContext`

**Files:**
- Modify: `src/weave/core/runtime.py` (`PreparedContext` dataclass and `prepare()` function)
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runtime.py` (at the end of the file):

```python
def test_prepare_captures_pre_invoke_untracked(temp_dir):
    """prepare() snapshots untracked files via git ls-files --others."""
    from weave.core.runtime import prepare
    import subprocess

    _init_harness(temp_dir)

    # Initialize git and commit the harness
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    # Create an untracked file BEFORE prepare runs
    (temp_dir / "user_work.txt").write_text("pre-existing work")

    ctx = prepare(task="x", working_dir=temp_dir, caller="test")

    assert isinstance(ctx.pre_invoke_untracked, set)
    assert "user_work.txt" in ctx.pre_invoke_untracked


def test_prepare_pre_invoke_untracked_empty_for_non_git_dir(temp_dir):
    """prepare() gracefully returns empty set when working_dir is not a git repo."""
    from weave.core.runtime import prepare
    _init_harness(temp_dir)

    # No git init — this is not a git repo
    ctx = prepare(task="x", working_dir=temp_dir, caller="test")

    assert ctx.pre_invoke_untracked == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139 && pytest tests/test_runtime.py::test_prepare_captures_pre_invoke_untracked -v`
Expected: FAIL with `AttributeError: 'PreparedContext' object has no attribute 'pre_invoke_untracked'`

- [ ] **Step 3: Add the field to `PreparedContext` and the snapshot helper**

In `src/weave/core/runtime.py`:

**First, add `import subprocess` at the top of the file.** The current import block looks like:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from weave.core.config import resolve_config
```

Change it to:

```python
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from weave.core.config import resolve_config
```

**Add a helper function** near the other private helpers (just above `_load_context`):

```python
def _snapshot_untracked(working_dir: Path) -> set[str]:
    """Return the set of untracked files in working_dir via git.

    Returns an empty set if the directory is not a git repo or git fails.
    Used by prepare() to capture state before invoke runs, so that _revert
    can distinguish pre-existing untracked files (preserve) from files
    created by the invocation (delete).
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return set()
        return {line for line in result.stdout.splitlines() if line}
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return set()
```

**Add the field to `PreparedContext`:**

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

**Update `prepare()` to populate the field.** Replace the `return PreparedContext(...)` block with:

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

- [ ] **Step 4: Run both new tests**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139 && pytest tests/test_runtime.py::test_prepare_captures_pre_invoke_untracked tests/test_runtime.py::test_prepare_pre_invoke_untracked_empty_for_non_git_dir -v`
Expected: PASS (both tests)

- [ ] **Step 5: Run the full runtime suite to confirm no regressions**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139 && pytest tests/test_runtime.py -v`
Expected: all runtime tests pass (existing tests construct `PreparedContext` only through `prepare()`, which always populates the new field — no test needs updating)

- [ ] **Step 6: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139
git add src/weave/core/runtime.py tests/test_runtime.py
git commit -m "$(cat <<'EOF'
feat(runtime): capture pre_invoke_untracked snapshot in prepare()

Adds a new set[str] field to PreparedContext populated via
git ls-files --others --exclude-standard. Used by the upcoming
_revert stage to distinguish pre-existing untracked user work
(preserve) from files created by the invocation (delete).

Returns empty set gracefully when working_dir is not a git repo.

Linear: MAR-139

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Implement `_revert` stage and wire into `execute()`

**Files:**
- Modify: `src/weave/core/runtime.py` (add `_revert` function, update `execute()` control flow, update module docstring)
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the failing integration test**

Append to `tests/test_runtime.py`:

```python
def test_execute_reverts_untracked_file_on_hard_deny(temp_dir):
    """mvp phase: adapter writes .env (untracked, denied) -> file is rm'd, files_reverted populated."""
    from weave.core.runtime import execute
    import json as _json
    import subprocess
    _init_harness(temp_dir)

    # Switch to mvp phase so deny is hard-deny (not downgrade to flagged)
    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["phase"] = "mvp"
    config_path.write_text(_json.dumps(config))

    # Adapter that writes .env (matches default deny list)
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo "SECRET=leaked" > .env\n'
        'echo \'{"exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    # Init git and commit the harness so .env is the only new untracked file
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    result = execute(task="make env", working_dir=temp_dir, caller="test")

    assert result.status == RuntimeStatus.DENIED
    assert not (temp_dir / ".env").exists()  # reverted
    assert result.security_result is not None
    assert ".env" in result.security_result.files_reverted
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139 && pytest tests/test_runtime.py::test_execute_reverts_untracked_file_on_hard_deny -v`
Expected: FAIL — `.env` still exists on disk because `_revert` does not yet exist. The assertion `not (temp_dir / ".env").exists()` fails.

- [ ] **Step 3: Update the module docstring**

Find the module docstring at the top of `src/weave/core/runtime.py`:

```python
"""Weave runtime — governed execution pipeline.

Pipeline: prepare -> policy_check -> invoke -> security_scan -> cleanup -> record.
Single entrypoint for all agent invocations, whether from the CLI, itzel,
or GSD.
"""
```

Replace with:

```python
"""Weave runtime — governed execution pipeline.

Pipeline: prepare -> policy_check -> invoke -> security_scan -> cleanup -> revert -> record.
Single entrypoint for all agent invocations, whether from the CLI, itzel,
or GSD.
"""
```

- [ ] **Step 4: Add the `_revert` function**

Insert this function in `src/weave/core/runtime.py` BETWEEN `_cleanup()` and `_record()`:

```python
def _revert(
    ctx: PreparedContext,
    invoke_result: InvokeResult | None,
    security_result: SecurityResult | None,
) -> None:
    """Stage 6: if security denied, revert all files_changed from the invocation.

    Per-file classification:
      - path escapes working_dir -> skip (never mutate outside working_dir)
      - tracked at HEAD -> git checkout HEAD -- <file>
      - not tracked AND in ctx.pre_invoke_untracked -> skip (pre-existing user work)
      - not tracked AND NOT in snapshot -> rm <file> (created by invocation)

    Best-effort: individual file failures are logged and skipped. Populates
    security_result.files_reverted in place with the list of successfully
    reverted relative paths.

    No-op when:
      - invoke_result is None (invoke never ran or failed)
      - security_result is None (scan was skipped due to non-zero exit)
      - security_result.action_taken != "denied"
    """
    if invoke_result is None or security_result is None:
        return
    if security_result.action_taken != "denied":
        return

    working_dir = ctx.working_dir
    working_dir_resolved = working_dir.resolve()
    reverted: list[str] = []

    for rel in invoke_result.files_changed:
        # Skip path-escape attempts
        try:
            abs_path = (working_dir / rel).resolve()
            abs_path.relative_to(working_dir_resolved)
        except ValueError:
            continue

        # Classify: tracked at HEAD?
        try:
            tracked = subprocess.run(
                ["git", "cat-file", "-e", f"HEAD:{rel}"],
                cwd=working_dir,
                capture_output=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            # git unavailable or timed out — cannot revert this file
            continue

        if tracked.returncode == 0:
            # Tracked at HEAD: restore content
            try:
                subprocess.run(
                    ["git", "checkout", "HEAD", "--", rel],
                    cwd=working_dir,
                    capture_output=True,
                    timeout=10,
                    check=True,
                )
                reverted.append(rel)
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
                continue
        else:
            # Not tracked at HEAD
            if rel in ctx.pre_invoke_untracked:
                # Pre-existing untracked user work — preserve
                continue
            # New file created by this invocation — delete
            try:
                abs_path.unlink()
                reverted.append(rel)
            except (FileNotFoundError, PermissionError, OSError):
                continue

    security_result.files_reverted = reverted
```

- [ ] **Step 5: Wire `_revert` into `execute()`**

Find the `execute()` function in `src/weave/core/runtime.py`. Locate this block:

```python
    post_hook_results = _cleanup(ctx, invoke_result)

    _record(
        ctx,
        invoke_result,
        policy,
        security_result,
        pre_hook_results,
        post_hook_results,
        status,
    )
```

Replace with:

```python
    post_hook_results = _cleanup(ctx, invoke_result)

    _revert(ctx, invoke_result, security_result)

    _record(
        ctx,
        invoke_result,
        policy,
        security_result,
        pre_hook_results,
        post_hook_results,
        status,
    )
```

- [ ] **Step 6: Run the integration test**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139 && pytest tests/test_runtime.py::test_execute_reverts_untracked_file_on_hard_deny -v`
Expected: PASS

- [ ] **Step 7: Run the full runtime suite**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139 && pytest tests/test_runtime.py -v`
Expected: all tests pass. No regressions — the existing `test_execute_denies_write_deny_in_mvp` test does not check filesystem state, so it still passes even though `credentials.json` is now reverted.

- [ ] **Step 8: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139
git add src/weave/core/runtime.py tests/test_runtime.py
git commit -m "$(cat <<'EOF'
feat(runtime): add _revert stage for hard-deny file rollback

Extends the runtime pipeline from 6 to 7 stages. When the security
scan denies in mvp/enterprise phase, _revert processes each file in
invoke_result.files_changed:

- path escapes working_dir: skip
- tracked at HEAD: git checkout HEAD -- <file>
- not tracked AND in pre_invoke_untracked snapshot: skip (user work)
- not tracked AND NOT in snapshot: rm (created by invocation)

Best-effort and non-fatal: individual file failures are caught and
skipped; the pipeline continues. Populates SecurityResult.files_reverted
in place with successfully reverted paths.

No-op in sandbox phase (action_taken resolves to 'flagged', not 'denied').

Linear: MAR-139

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Integration test — revert tracked file

**Files:**
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_runtime.py`:

```python
def test_execute_reverts_tracked_file_on_hard_deny(temp_dir):
    """mvp phase: adapter overwrites tracked config.json -> content restored from HEAD."""
    from weave.core.runtime import execute
    import json as _json
    import subprocess
    _init_harness(temp_dir)

    # Switch to mvp phase
    harness_config_path = temp_dir / ".harness" / "config.json"
    harness_config = _json.loads(harness_config_path.read_text())
    harness_config["phase"] = "mvp"
    harness_config_path.write_text(_json.dumps(harness_config))

    # Create and commit a tracked config.json at the root (not .harness/config.json)
    root_config = temp_dir / "config.json"
    root_config.write_text('{"version": "original"}')

    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    # Adapter overwrites config.json with tampered content
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo \'{"version": "tampered"}\' > config.json\n'
        'echo \'{"exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    result = execute(task="tamper config", working_dir=temp_dir, caller="test")

    assert result.status == RuntimeStatus.DENIED
    assert root_config.read_text() == '{"version": "original"}'  # restored from HEAD
    assert "config.json" in result.security_result.files_reverted
```

- [ ] **Step 2: Run the test**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139 && pytest tests/test_runtime.py::test_execute_reverts_tracked_file_on_hard_deny -v`
Expected: PASS (Task 2's `_revert` implementation already handles tracked files via the `git checkout HEAD --` branch)

- [ ] **Step 3: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139
git add tests/test_runtime.py
git commit -m "$(cat <<'EOF'
test(runtime): verify _revert restores tracked files from HEAD

Proves the git checkout HEAD -- branch of _revert: when a tracked
file is modified by a denied adapter, its content is restored from
HEAD rather than deleted.

Linear: MAR-139

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Integration test — revert ALL files_changed, not just flagged

**Files:**
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_runtime.py`:

```python
def test_execute_reverts_all_files_changed_not_just_flagged(temp_dir):
    """Denied invocations roll back the entire work, not just flagged files.

    Adapter writes helper.py (harmless) AND .env (denied). On hard-deny in
    mvp phase, BOTH must be reverted — even though only .env triggered the
    denial. This encodes the invariant that the unit of judgment is the
    invocation, not the individual file.
    """
    from weave.core.runtime import execute
    import json as _json
    import subprocess
    _init_harness(temp_dir)

    # mvp phase
    harness_config_path = temp_dir / ".harness" / "config.json"
    harness_config = _json.loads(harness_config_path.read_text())
    harness_config["phase"] = "mvp"
    harness_config_path.write_text(_json.dumps(harness_config))

    # Init git with clean baseline
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    # Adapter writes TWO untracked files: one flagged, one clean
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo "def add(a, b): return a + b" > helper.py\n'
        'echo "SECRET=leaked" > .env\n'
        'echo \'{"exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    result = execute(task="mixed write", working_dir=temp_dir, caller="test")

    assert result.status == RuntimeStatus.DENIED
    assert not (temp_dir / ".env").exists()  # flagged, reverted
    assert not (temp_dir / "helper.py").exists()  # NOT flagged, but STILL reverted
    assert ".env" in result.security_result.files_reverted
    assert "helper.py" in result.security_result.files_reverted
```

- [ ] **Step 2: Run the test**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139 && pytest tests/test_runtime.py::test_execute_reverts_all_files_changed_not_just_flagged -v`
Expected: PASS (Task 2's implementation reverts all `invoke_result.files_changed`, not just `security_result.findings`)

- [ ] **Step 3: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139
git add tests/test_runtime.py
git commit -m "$(cat <<'EOF'
test(runtime): verify _revert rolls back all files_changed, not just flagged

Encodes the core invariant: when security denies an invocation, the
entire invocation's work is rejected — not just the subset of files
that tripped the scan. Prevents split attacks where a malicious
adapter writes one flagged payload plus one unflagged loader.

Linear: MAR-139

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Integration test — preserve pre-existing untracked files

**Files:**
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_runtime.py`:

```python
def test_execute_preserves_pre_existing_untracked_on_revert(temp_dir):
    """Pre-existing untracked files that trigger denial must be preserved.

    The pre_invoke_untracked snapshot protects user work: if an operator
    has an untracked credentials.json sitting in their working tree, and
    an invocation happens while that file is present, the revert must
    not delete it — even though it triggers a denial (because it's in
    files_changed from the git ls-files query and matches the deny list).
    """
    from weave.core.runtime import execute
    import json as _json
    import subprocess
    _init_harness(temp_dir)

    # mvp phase
    harness_config_path = temp_dir / ".harness" / "config.json"
    harness_config = _json.loads(harness_config_path.read_text())
    harness_config["phase"] = "mvp"
    harness_config_path.write_text(_json.dumps(harness_config))

    # Init git + commit the harness
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    # Pre-existing untracked file that matches the deny list
    user_file = temp_dir / "credentials.json"
    user_file.write_text('{"key": "my-personal-work"}')

    # Adapter is a no-op (writes nothing)
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo \'{"exitCode": 0, "stdout": "no-op", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    result = execute(task="noop", working_dir=temp_dir, caller="test")

    # The pre-existing credentials.json is picked up by git ls-files --others
    # and triggers the deny list -> status is DENIED
    assert result.status == RuntimeStatus.DENIED

    # But the file is in pre_invoke_untracked, so _revert skips it
    assert user_file.exists()
    assert user_file.read_text() == '{"key": "my-personal-work"}'
    assert "credentials.json" not in result.security_result.files_reverted
```

- [ ] **Step 2: Run the test**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139 && pytest tests/test_runtime.py::test_execute_preserves_pre_existing_untracked_on_revert -v`
Expected: PASS (the `pre_invoke_untracked` check in `_revert` skips the file)

- [ ] **Step 3: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139
git add tests/test_runtime.py
git commit -m "$(cat <<'EOF'
test(runtime): verify _revert preserves pre-existing untracked files

Security invariant: the pre_invoke_untracked snapshot protects user
work. Even when a pre-existing untracked file triggers a denial (via
the deny list + git ls-files --others query), _revert skips it
because it was not created by the invocation.

Linear: MAR-139

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Integration test — no revert in sandbox phase

**Files:**
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_runtime.py`:

```python
def test_execute_no_revert_in_sandbox_phase(temp_dir):
    """Sandbox phase flags findings but never reverts files.

    resolve_action downgrades 'deny' to 'warn' in sandbox, so the final
    action_taken is 'flagged' (not 'denied'). _revert is a no-op on
    anything other than action_taken=='denied', so files stay on disk.
    """
    from weave.core.runtime import execute
    import subprocess
    _init_harness(temp_dir)  # default phase is sandbox

    # Init git
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    # Adapter writes .env (flagged but not denied in sandbox)
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo "SECRET=test" > .env\n'
        'echo \'{"exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    result = execute(task="make env", working_dir=temp_dir, caller="test")

    # Sandbox downgrades deny to warn -> FLAGGED, not DENIED
    assert result.status == RuntimeStatus.FLAGGED

    # .env still exists (not reverted)
    assert (temp_dir / ".env").exists()
    assert (temp_dir / ".env").read_text() == "SECRET=test\n"

    # files_reverted is empty
    assert result.security_result.files_reverted == []
```

- [ ] **Step 2: Run the test**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139 && pytest tests/test_runtime.py::test_execute_no_revert_in_sandbox_phase -v`
Expected: PASS (Task 2's `_revert` returns early when `action_taken != "denied"`)

- [ ] **Step 3: Commit**

```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139
git add tests/test_runtime.py
git commit -m "$(cat <<'EOF'
test(runtime): verify _revert is no-op in sandbox phase

Proves the phase gate: sandbox downgrades deny to warn via
resolve_action, so action_taken resolves to 'flagged'. _revert
only fires on 'denied', so files stay on disk and files_reverted
is empty.

Linear: MAR-139

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Final verification

**Files:** none — verification only

- [ ] **Step 1: Run the full test suite**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139 && pytest tests/ -v 2>&1 | tail -30`
Expected: 102 tests pass (95 after MAR-140 + 7 new from this plan — 2 from Task 1, then 1 each from Tasks 2, 3, 4, 5, 6).

Let me recount: 95 → Task 1 adds 2 (`test_prepare_captures_pre_invoke_untracked` and `test_prepare_pre_invoke_untracked_empty_for_non_git_dir`) = 97 → Task 2 adds 1 = 98 → Task 3 adds 1 = 99 → Task 4 adds 1 = 100 → Task 5 adds 1 = 101 → Task 6 adds 1 = 102. Final expected: **102 tests passing**.

- [ ] **Step 2: Verify the pipeline docstring was updated**

Run: `cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139 && head -6 src/weave/core/runtime.py`
Expected output contains:
```
Pipeline: prepare -> policy_check -> invoke -> security_scan -> cleanup -> revert -> record.
```

- [ ] **Step 3: Manual sanity check of the revert behavior**

Run:
```bash
cd /home/martymanny/.config/superpowers/worktrees/weave/mar-139 && PYTHONPATH=src python3 -c "
import subprocess, tempfile, json
from pathlib import Path
from weave.core.runtime import execute
from weave.schemas.policy import RuntimeStatus

with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    harness = tmp / '.harness'
    harness.mkdir()
    for sub in ['context', 'hooks', 'providers', 'sessions', 'integrations']:
        (harness / sub).mkdir()
    (harness / 'manifest.json').write_text(json.dumps({
        'id': 'test', 'type': 'project', 'name': 'test',
        'status': 'active', 'phase': 'mvp'
    }))
    (harness / 'config.json').write_text(json.dumps({
        'version': '1', 'phase': 'mvp', 'default_provider': 'claude-code',
        'providers': {'claude-code': {
            'command': '.harness/providers/claude-code.sh',
            'enabled': True, 'capability': 'workspace-write'
        }}
    }))
    adapter = harness / 'providers' / 'claude-code.sh'
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo \"SECRET=leaked\" > .env\n'
        'echo \\'{\"exitCode\": 0, \"stdout\": \"done\", \"stderr\": \"\", \"structured\": null}\\''
        '\n'
    )
    adapter.chmod(0o755)
    subprocess.run(['git', 'init', '-q'], cwd=tmp, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@test'], cwd=tmp, check=True)
    subprocess.run(['git', 'config', 'user.name', 'test'], cwd=tmp, check=True)
    subprocess.run(['git', 'add', '.'], cwd=tmp, check=True)
    subprocess.run(['git', 'commit', '-q', '-m', 'init'], cwd=tmp, check=True)
    result = execute(task='test', working_dir=tmp, caller='manual')
    assert result.status == RuntimeStatus.DENIED
    assert not (tmp / '.env').exists()
    assert '.env' in result.security_result.files_reverted
    print('manual revert check: ok')
"
```
Expected: prints `manual revert check: ok`

**Note:** The shell heredoc quoting in Step 3 is tricky. If the manual check fails due to quoting, skip it — the 6 integration tests in Tasks 1-6 already cover the behavior comprehensively. Step 3 is a nice-to-have verification, not required.

- [ ] **Step 4: No commit** — Task 7 is verification only.

---

## Self-Review Notes

**Spec coverage:**
- Pipeline extension (6 → 7 stages with `_revert`) → Task 2 (docstring update + function + wiring)
- `PreparedContext.pre_invoke_untracked` field → Task 1
- `prepare()` populates the snapshot → Task 1
- Per-file classification (tracked / untracked+snapshot / untracked+new) → Task 2 `_revert` body
- Path-escape files are skipped → Task 2 `_revert` body (the `try/except ValueError` block)
- Revert only on `action_taken == "denied"` → Task 2 early return; Task 6 test
- `security_result is None` guard → Task 2 early return (covered by existing `_record` pattern)
- `invoke_result is None` guard → Task 2 early return
- Best-effort, non-fatal error handling → Task 2 per-file try/except
- Empty `files_reverted` on non-git dir → Task 1 snapshot helper returns empty set; Task 2's revert logic no-ops because `git cat-file` fails gracefully
- No snapshot needed when git is unavailable → Task 1 snapshot helper's error handling
- Revert all files_changed (not just flagged) → Task 4 test
- Preserve pre-existing untracked → Task 5 test
- No revert in sandbox → Task 6 test
- Tracked file restoration → Task 3 test
- Untracked file deletion → Task 2 test
- All 95 existing tests continue to pass → Task 1 Step 5, Task 2 Step 7, Task 7 Step 1

**Placeholder scan:** No TBDs, TODOs, or placeholder steps. Every code block is complete and copy-pastable. One "nice to have" caveat is explicit in Task 7 Step 3 about shell quoting.

**Type consistency:**
- `pre_invoke_untracked: set[str]` is used consistently across `PreparedContext` (Task 1) and `_revert` (Task 2, checks `rel in ctx.pre_invoke_untracked`)
- `_revert` signature `(ctx, invoke_result, security_result) -> None` matches the call site in `execute()` (Task 2 Step 5)
- `security_result.files_reverted = reverted` assigns a `list[str]`, matching the existing Pydantic field type
- All test imports match the module structure (`from weave.core.runtime import execute`, etc.)

**Final expected test count:** 102 tests (95 MAR-140 baseline + 7 new).

**Architectural note for implementers:** `subprocess` is imported at the module level (Task 1 Step 3) and reused by both `_snapshot_untracked` and `_revert`. This matches the import style of other files in the project (e.g., `invoker.py` imports subprocess at the top). `subprocess` is stdlib so no new dependencies are introduced.
