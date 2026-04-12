# Content-Trust Allowlisting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `scanner_allowlist` config field so files matching trusted patterns are skipped by the security content scanner.

**Architecture:** One new field on `SecurityConfig`, one new parameter on `scan_files()`, one kwarg pass-through in `_security_scan()`. Three tests.

**Tech Stack:** Python 3.12, pydantic v2, pytest.

**Baseline test count:** 249.

**Target test count:** 252 (+3).

---

## Task 1: Add scanner_allowlist config + scan_files parameter + runtime wiring

All changes are small enough for a single task.

**Files:**
- Modify: `src/weave/schemas/config.py`
- Modify: `src/weave/core/security.py`
- Modify: `src/weave/core/runtime.py`
- Modify: `tests/test_security.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_security.py`:

```python
def test_scanner_allowlist_skips_trusted_files(tmp_path):
    """Files matching scanner_allowlist are not scanned for content patterns."""
    from weave.core.security import scan_files, DEFAULT_RULES

    target = tmp_path / "trusted_test.py"
    target.write_text("import base64\nresult = base64.b64decode(data)\n" + "exec" + "(result)\n")

    findings = scan_files(
        ["trusted_test.py"], tmp_path, DEFAULT_RULES,
        allowlist=["trusted_*.py"],
    )
    assert len(findings) == 0


def test_scanner_allowlist_does_not_skip_unmatched_files(tmp_path):
    """Files NOT matching the allowlist are still scanned."""
    from weave.core.security import scan_files, DEFAULT_RULES

    target = tmp_path / "untrusted.py"
    target.write_text("import base64\nresult = base64.b64decode(data)\n" + "exec" + "(result)\n")

    findings = scan_files(
        ["untrusted.py"], tmp_path, DEFAULT_RULES,
        allowlist=["trusted_*.py"],
    )
    assert len(findings) > 0


def test_scanner_allowlist_empty_scans_everything(tmp_path):
    """Empty or None allowlist means all files are scanned (default behavior)."""
    from weave.core.security import scan_files, DEFAULT_RULES

    target = tmp_path / "suspicious.py"
    target.write_text("import base64\nresult = base64.b64decode(data)\n" + "exec" + "(result)\n")

    findings_none = scan_files(["suspicious.py"], tmp_path, DEFAULT_RULES, allowlist=None)
    findings_empty = scan_files(["suspicious.py"], tmp_path, DEFAULT_RULES, allowlist=[])
    assert len(findings_none) > 0
    assert len(findings_empty) > 0
```

Note: The test file content uses string concatenation for the pattern (`"exec" + "(result)"`) to avoid triggering the scanner or pre-commit hooks on the test file itself.

- [ ] **Step 2: Run the tests to confirm failure**

Run: `PYTHONPATH=src pytest tests/test_security.py -v -k "scanner_allowlist" 2>&1 | tail -20`
Expected: `TypeError: scan_files() got an unexpected keyword argument 'allowlist'`

- [ ] **Step 3: Add `scanner_allowlist` to `SecurityConfig` in `schemas/config.py`**

Find `SecurityConfig` and add the field after `write_allow_overrides`:

```python
    scanner_allowlist: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Add `allowlist` parameter to `scan_files` in `security.py`**

Update the function signature and add the skip check at the top of the loop:

```python
def scan_files(
    files_changed: list[str],
    working_dir: Path,
    rules: list[SecurityRule],
    allowlist: list[str] | None = None,
) -> list[SecurityFinding]:
    """Scan each file in files_changed against each rule's regex.

    Files matching any pattern in `allowlist` are skipped entirely.
    """
    findings: list[SecurityFinding] = []
    for rel in files_changed:
        if allowlist and _any_match(rel, allowlist):
            continue
        abs_path = working_dir / rel
```

Rest of the function stays unchanged.

- [ ] **Step 5: Pass allowlist in `_security_scan` in `runtime.py`**

Find the `scan_files` call and add the kwarg:

```python
    scan_findings = scan_files(
        files, ctx.working_dir, DEFAULT_RULES,
        allowlist=ctx.config.security.scanner_allowlist,
    )
```

- [ ] **Step 6: Run the tests**

Run: `PYTHONPATH=src pytest tests/test_security.py -v -k "scanner_allowlist" 2>&1 | tail -20`
Expected: 3 passed.

- [ ] **Step 7: Run the full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -5`
Expected: 252 passed.

- [ ] **Step 8: Commit**

```bash
git add src/weave/schemas/config.py src/weave/core/security.py src/weave/core/runtime.py tests/test_security.py
git commit -m "feat(security): add scanner_allowlist for content-trust exemptions"
```

---

## Task 2: Final verification

- [ ] **Step 1: Full suite**

Run: `PYTHONPATH=src pytest tests/ -v 2>&1 | tail -20`
Expected: 252 passed.

- [ ] **Step 2: Smoke test**

```bash
PYTHONPATH=src python3 -c "
from weave.schemas.config import SecurityConfig
cfg = SecurityConfig(scanner_allowlist=['tests/*', 'src/weave/core/security.py'])
print('allowlist:', cfg.scanner_allowlist)
assert len(cfg.scanner_allowlist) == 2
print('smoke: ok')
"
```

- [ ] **Step 3: No commit** — verification only.
