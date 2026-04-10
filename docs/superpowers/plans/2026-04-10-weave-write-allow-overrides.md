# MAR-140 Implementation Plan — `write_allow_overrides` Enforcement

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate the dormant `SecurityConfig.write_allow_overrides` field so operators can surgically exempt specific paths from the write deny list while keeping the supply chain scanner active.

**Architecture:** Extend `check_write_deny` in `src/weave/core/security.py` with an optional `allow_patterns` parameter. Runtime's `_security_scan` passes `ctx.config.security.write_allow_overrides` through. Allow matching uses relative-path-only `fnmatch` (no symlink resolution, no basename fallback) — stricter than deny matching by design, so attackers cannot alias around allow entries via symlinks. Backwards compatible: default `None` preserves all Phase 1 behavior.

**Tech Stack:** Python 3.10+, stdlib only (`fnmatch`), pytest. No new dependencies.

**Reference spec:** `docs/superpowers/specs/2026-04-10-weave-write-allow-overrides-design.md`

**Linear:** [MAR-140](https://linear.app/martymanny/issue/MAR-140)

---

## File Structure

### Modified files

| File | Change |
|------|--------|
| `src/weave/core/security.py` | Add `allow_patterns: list[str] | None = None` parameter to `check_write_deny`. Apply allow filter after the three deny-detection stages. |
| `src/weave/core/runtime.py` | `_security_scan` passes `ctx.config.security.write_allow_overrides` as `allow_patterns` to `check_write_deny`. |
| `src/weave/schemas/config.py` | Remove `# Phase 2: not yet enforced` trailing comment on `write_allow_overrides` line. |
| `tests/test_security.py` | Add 3 unit tests covering allow override, surgical scope, and symlink non-leakage. |
| `tests/test_runtime.py` | Add 1 integration test for mvp phase + allow override. |

### No new files

This is a surgical extension of existing modules. Every change is additive except the one-line comment removal.

---

## Task 1: Extend `check_write_deny` with `allow_patterns` parameter

**Files:**
- Modify: `src/weave/core/security.py:10-38`
- Test: `tests/test_security.py`

- [ ] **Step 1: Write the failing test — basic allow override**

Append to the end of `tests/test_security.py` (after the existing write-deny tests, before the scanner tests):

```python
def test_check_write_deny_honors_allow_override(temp_dir):
    """Allow pattern exempts a file that matches a deny pattern."""
    from weave.core.security import check_write_deny
    denied = check_write_deny(
        files_changed=[".env"],
        working_dir=temp_dir,
        patterns=[".env"],
        allow_patterns=[".env"],
    )
    assert denied == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/martymanny/repos/weave && pytest tests/test_security.py::test_check_write_deny_honors_allow_override -v`
Expected: FAIL with `TypeError: check_write_deny() got an unexpected keyword argument 'allow_patterns'`

- [ ] **Step 3: Extend `check_write_deny` signature and logic**

Replace the current `check_write_deny` function (lines 10-38) with:

```python
def check_write_deny(
    files_changed: list[str],
    working_dir: Path,
    patterns: list[str],
    allow_patterns: list[str] | None = None,
) -> list[str]:
    """Return the subset of files_changed that match any deny pattern
    and do not match any allow pattern.

    Deny matching is symlink-aware: it resolves real paths before pattern
    matching, so a symlink pointing at a denied target is itself denied.
    Allow matching uses only the relative path as written — stricter by
    design, so that attackers cannot alias around allow entries via
    symlinks. Passing None or [] for allow_patterns preserves Phase 1
    behavior (no exemptions).
    """
    allow = allow_patterns or []
    denied: list[str] = []
    for rel in files_changed:
        abs_path = (working_dir / rel).resolve()
        matched_deny = False
        if _any_match(rel, patterns):
            matched_deny = True
        else:
            try:
                rel_resolved = abs_path.relative_to(working_dir.resolve())
                if _any_match(str(rel_resolved), patterns):
                    matched_deny = True
            except ValueError:
                # abs_path escapes working_dir; suspicious
                matched_deny = True
            if not matched_deny:
                basename = os.path.basename(rel)
                if _any_match(basename, patterns):
                    matched_deny = True

        if not matched_deny:
            continue

        # Allow override: exempt if the relative path as written matches
        # any allow pattern. No symlink resolution, no basename fallback.
        if allow and _any_match(rel, allow):
            continue

        denied.append(rel)
    return denied
```

- [ ] **Step 4: Run the new test**

Run: `cd /home/martymanny/repos/weave && pytest tests/test_security.py::test_check_write_deny_honors_allow_override -v`
Expected: PASS

- [ ] **Step 5: Run all existing security tests to confirm no regressions**

Run: `cd /home/martymanny/repos/weave && pytest tests/test_security.py -v`
Expected: PASS (all tests — 10 pre-existing + 1 new)

- [ ] **Step 6: Commit**

```bash
cd /home/martymanny/repos/weave
git add src/weave/core/security.py tests/test_security.py
git commit -m "$(cat <<'EOF'
feat(security): add allow_patterns parameter to check_write_deny

Extends check_write_deny with an optional allow_patterns argument.
A file matching a deny pattern is exempted if its relative path (as
written, no symlink resolution) also matches any allow pattern.
Backwards compatible: None default preserves Phase 1 behavior.

Linear: MAR-140

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Test — allow does not leak to other denied files

**Files:**
- Test: `tests/test_security.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_security.py` (right after `test_check_write_deny_honors_allow_override`):

```python
def test_check_write_deny_allow_does_not_leak_to_other_files(temp_dir):
    """Allow is surgical — it exempts only matching files, not others."""
    from weave.core.security import check_write_deny
    denied = check_write_deny(
        files_changed=[".env", "cert.pem"],
        working_dir=temp_dir,
        patterns=[".env", "*.pem"],
        allow_patterns=[".env"],
    )
    assert denied == ["cert.pem"]
```

- [ ] **Step 2: Run the test**

Run: `cd /home/martymanny/repos/weave && pytest tests/test_security.py::test_check_write_deny_allow_does_not_leak_to_other_files -v`
Expected: PASS (Task 1's implementation already handles this — the test is coverage, not new logic)

- [ ] **Step 3: Commit**

```bash
cd /home/martymanny/repos/weave
git add tests/test_security.py
git commit -m "$(cat <<'EOF'
test(security): verify allow override is surgical not blanket

Proves an allow entry for .env does not exempt cert.pem when
*.pem is also in the deny list.

Linear: MAR-140

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Test — allow does not match symlink target (security-critical)

**Files:**
- Test: `tests/test_security.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_security.py` (right after `test_check_write_deny_allow_does_not_leak_to_other_files`):

```python
def test_check_write_deny_allow_does_not_match_symlink_target(temp_dir):
    """Security invariant: allow patterns must NOT match via symlink resolution.

    An attacker who adds an allow entry for '.env' must not be able to
    then create a symlink named 'innocuous.txt' pointing at '.env' and
    have the symlink's writes pass the allow check.
    """
    import os
    from weave.core.security import check_write_deny

    real_env = temp_dir / ".env"
    real_env.write_text("SECRET=x")
    link = temp_dir / "innocuous.txt"
    os.symlink(real_env, link)

    denied = check_write_deny(
        files_changed=["innocuous.txt"],
        working_dir=temp_dir,
        patterns=[".env"],
        allow_patterns=[".env"],
    )
    assert denied == ["innocuous.txt"]
```

- [ ] **Step 2: Run the test**

Run: `cd /home/martymanny/repos/weave && pytest tests/test_security.py::test_check_write_deny_allow_does_not_match_symlink_target -v`
Expected: PASS

**Why this passes:** The deny list's symlink-aware stage 2 (resolved-path match) catches `innocuous.txt` → `.env`, marking it as a deny candidate. The allow check then runs `_any_match("innocuous.txt", [".env"])` — relative path only, no resolution. `"innocuous.txt"` does not match `.env`, so the denial stands. This test encodes the stricter-allow security invariant from the spec.

- [ ] **Step 3: Commit**

```bash
cd /home/martymanny/repos/weave
git add tests/test_security.py
git commit -m "$(cat <<'EOF'
test(security): verify allow does not inherit symlink resolution

Security-critical: ensures that an allow entry for a denied path
cannot be bypassed by creating a symlink pointing at the target.
Allow matching is relative-path-only by design.

Linear: MAR-140

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire `write_allow_overrides` through the runtime

**Files:**
- Modify: `src/weave/core/runtime.py:164-170`

- [ ] **Step 1: Read the current `_security_scan` function**

Run: `cd /home/martymanny/repos/weave && sed -n '160,195p' src/weave/core/runtime.py`

You will see a block that computes `deny_patterns` and calls `check_write_deny` with three arguments. Note the exact indentation.

- [ ] **Step 2: Update the `check_write_deny` call**

Find this block in `src/weave/core/runtime.py`:

```python
    deny_patterns = (
        ctx.config.security.write_deny_list + ctx.config.security.write_deny_extras
    )
    denied_writes = check_write_deny(files, ctx.working_dir, deny_patterns)
```

Replace it with:

```python
    deny_patterns = (
        ctx.config.security.write_deny_list + ctx.config.security.write_deny_extras
    )
    denied_writes = check_write_deny(
        files,
        ctx.working_dir,
        deny_patterns,
        allow_patterns=ctx.config.security.write_allow_overrides,
    )
```

- [ ] **Step 3: Run the full runtime test suite to confirm no regressions**

Run: `cd /home/martymanny/repos/weave && pytest tests/test_runtime.py -v`
Expected: PASS (all runtime tests — Phase 1 behavior unchanged since the default `write_allow_overrides` is `[]`)

- [ ] **Step 4: Commit**

```bash
cd /home/martymanny/repos/weave
git add src/weave/core/runtime.py
git commit -m "$(cat <<'EOF'
feat(runtime): pass write_allow_overrides to check_write_deny

Wires SecurityConfig.write_allow_overrides through the _security_scan
stage so operators can surgically exempt paths from the write deny
list via .harness/config.json.

Linear: MAR-140

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Integration test — allow override end-to-end in mvp phase

**Files:**
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runtime.py` (at the end of the file):

```python
def test_execute_respects_write_allow_overrides_in_mvp(temp_dir):
    """Full pipeline: mvp phase + allow override = SUCCESS (not DENIED).

    Without the allow override, writing config.json in mvp phase would
    hard-deny (proven by test_execute_denies_write_deny_in_mvp). With
    the override, it should succeed.
    """
    from weave.core.runtime import execute
    import json as _json
    _init_harness(temp_dir)

    # Switch to mvp phase AND add config.json to write_allow_overrides
    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["phase"] = "mvp"
    config["security"] = {"write_allow_overrides": ["config.json"]}
    config_path.write_text(_json.dumps(config))

    # Adapter that writes config.json (would normally be denied in mvp)
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo "{}" > config.json\n'
        'echo \'{"exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    (temp_dir / "seed.txt").write_text("x")
    subprocess.run(["git", "add", "seed.txt"], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    result = execute(task="write config", working_dir=temp_dir, caller="test")
    assert result.status == RuntimeStatus.SUCCESS
    assert result.security_result is not None
    assert result.security_result.action_taken == "clean"
    # No findings because the file was exempted
    assert not any(
        f.rule_id == "write-deny-list"
        for f in result.security_result.findings
    )
```

- [ ] **Step 2: Run the test**

Run: `cd /home/martymanny/repos/weave && pytest tests/test_runtime.py::test_execute_respects_write_allow_overrides_in_mvp -v`
Expected: PASS

**If it fails:** The most likely cause is the security config deserialization. Pydantic should accept `{"write_allow_overrides": ["config.json"]}` as partial input and populate the other `SecurityConfig` defaults. If you see a validation error, print `resolve_config(temp_dir).security` to diagnose.

- [ ] **Step 3: Commit**

```bash
cd /home/martymanny/repos/weave
git add tests/test_runtime.py
git commit -m "$(cat <<'EOF'
test(runtime): verify write_allow_overrides end-to-end in mvp phase

Integration test proving the full pipeline honors allow overrides
in the strictest enforcement phase. Writing config.json in mvp
would normally deny, but the override makes it succeed with
SecurityResult.action_taken == 'clean'.

Linear: MAR-140

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Remove the dormant-field annotation

**Files:**
- Modify: `src/weave/schemas/config.py:57`

- [ ] **Step 1: Read the current line**

Run: `cd /home/martymanny/repos/weave && sed -n '57p' src/weave/schemas/config.py`
Expected output:
```
    write_allow_overrides: list[str] = Field(default_factory=list)  # Phase 2: not yet enforced
```

- [ ] **Step 2: Remove the trailing comment**

Edit `src/weave/schemas/config.py`, find this exact line:

```python
    write_allow_overrides: list[str] = Field(default_factory=list)  # Phase 2: not yet enforced
```

Replace with:

```python
    write_allow_overrides: list[str] = Field(default_factory=list)
```

- [ ] **Step 3: Run the full suite to confirm nothing regressed**

Run: `cd /home/martymanny/repos/weave && pytest tests/ -q`
Expected: all tests pass (95 total — 91 Phase 1 + 4 new from this plan)

- [ ] **Step 4: Commit**

```bash
cd /home/martymanny/repos/weave
git add src/weave/schemas/config.py
git commit -m "$(cat <<'EOF'
chore(schemas): remove 'not yet enforced' annotation from write_allow_overrides

The field is now fully wired through check_write_deny and the runtime
security scan stage. MAR-140 complete.

Linear: MAR-140

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Final verification

**Files:** none — verification only

- [ ] **Step 1: Run the full test suite**

Run: `cd /home/martymanny/repos/weave && pytest tests/ -v`
Expected: 95 tests pass (91 Phase 1 + 4 new tests in this plan — one each from Tasks 1, 2, 3, and 5). Task 4 had no new tests (it only wired through the runtime; regression via existing tests). Task 6 had no new tests (comment removal only).

- [ ] **Step 2: Manual backwards-compat check**

Run:
```bash
cd /home/martymanny/repos/weave && python3 -c "
from weave.schemas.config import WeaveConfig
# Legacy config without write_allow_overrides still works
legacy = {
    'version': '1',
    'phase': 'sandbox',
    'default_provider': 'claude-code',
    'providers': {'claude-code': {'command': 'claude'}}
}
c = WeaveConfig.model_validate(legacy)
assert c.security.write_allow_overrides == []
print('legacy config: ok')

# New config with explicit overrides
new = {
    'version': '1',
    'phase': 'mvp',
    'default_provider': 'claude-code',
    'providers': {'claude-code': {'command': 'claude'}},
    'security': {'write_allow_overrides': ['config.json', 'examples/**']}
}
c = WeaveConfig.model_validate(new)
assert c.security.write_allow_overrides == ['config.json', 'examples/**']
print('new config: ok')
"
```
Expected: prints `legacy config: ok` and `new config: ok`

- [ ] **Step 3: Manual unit check of the new allow behavior**

Run:
```bash
cd /home/martymanny/repos/weave && python3 -c "
from pathlib import Path
import tempfile
from weave.core.security import check_write_deny

with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    # deny .env, allow .env → empty
    assert check_write_deny(['.env'], tmp, ['.env'], ['.env']) == []
    # deny .env, no allow → denied
    assert check_write_deny(['.env'], tmp, ['.env']) == ['.env']
    # deny .env, allow different pattern → still denied
    assert check_write_deny(['.env'], tmp, ['.env'], ['other.txt']) == ['.env']
    # None allow behaves like []
    assert check_write_deny(['.env'], tmp, ['.env'], None) == ['.env']
    print('allow behavior: ok')
"
```
Expected: prints `allow behavior: ok`

- [ ] **Step 4: No commit** — Task 7 is verification only.

---

## Self-Review Notes

**Spec coverage:**
- Exemption semantics (deny ∩ ¬allow) → Task 1 logic + Task 1 test
- Deny list only (scanner unaffected) → not explicitly tested, but the scope is enforced by only touching `check_write_deny`; the supply chain scanner has no allow parameter. A test for "scanner still flags an allowed path" is not strictly required because it's a negative assertion over unrelated code, and the scanner's behavior is already covered by existing Phase 1 tests. No task needed.
- Stricter allow matching (no symlink resolution) → Task 3 security-critical test
- `None` vs `[]` equivalence → Task 7 Step 3 manual check + Task 1 docstring explicitly calls it out
- Runtime wiring → Task 4
- Schema comment removal → Task 6
- Backwards compat with all Phase 1 tests → Task 4 Step 3 and Task 7 Step 1 regression verification

**Placeholder scan:** No TBDs, TODOs, or placeholder steps. Every code block is complete and self-contained.

**Type consistency:** All uses of `allow_patterns` are `list[str] | None` with `None` default. The `allow = allow_patterns or []` normalization is done once at the top of the function; all downstream checks use `allow`. The runtime call uses `allow_patterns=...` as a keyword argument consistently. `SecurityConfig.write_allow_overrides` is `list[str]` (never `None`) since it has a `default_factory=list`, so the runtime passes a list, never `None` — but the function accepts both.

**Expected final test count:** 95 tests (91 Phase 1 baseline + 4 new: 3 in `test_security.py` from Tasks 1/2/3, 1 in `test_runtime.py` from Task 5).
