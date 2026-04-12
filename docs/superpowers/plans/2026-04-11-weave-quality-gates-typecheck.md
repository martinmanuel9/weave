# Quality Gates + Type Checking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship built-in quality gate hooks (pytest + ruff) and add pyright type checking config.

**Architecture:** Built-in hook scripts in `src/weave/hooks/builtin/`, scaffold copies them when `--with-quality-gates` flag is set. Pyright config as a standalone `pyrightconfig.json`.

**Tech Stack:** Python 3.12, click, pytest, pyright.

**Baseline test count:** 252.

**Target test count:** 254 (+2).

---

## Task 1: Ship built-in quality gate hooks + scaffold integration

**Files:**
- Create: `src/weave/hooks/__init__.py`
- Create: `src/weave/hooks/builtin/__init__.py`
- Create: `src/weave/hooks/builtin/run-tests.sh`
- Create: `src/weave/hooks/builtin/run-lint.sh`
- Modify: `src/weave/core/scaffold.py`
- Modify: `src/weave/cli.py`
- Modify: `tests/test_scaffold.py` (or create if missing)

- [ ] **Step 1: Write the failing tests**

Check if `tests/test_scaffold.py` exists. If not, create it. Append:

```python
"""Tests for quality gate scaffolding."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_scaffold_with_quality_gates_copies_hooks(tmp_path):
    from weave.core.scaffold import scaffold_project

    scaffold_project(tmp_path, name="test-proj", with_quality_gates=True)

    hooks_dir = tmp_path / ".harness" / "hooks"
    assert (hooks_dir / "run-tests.sh").exists()
    assert (hooks_dir / "run-lint.sh").exists()
    # Verify executable
    import stat
    assert (hooks_dir / "run-tests.sh").stat().st_mode & stat.S_IXUSR
    assert (hooks_dir / "run-lint.sh").stat().st_mode & stat.S_IXUSR

    # Verify config wires the hooks
    config = json.loads((tmp_path / ".harness" / "config.json").read_text())
    assert len(config["hooks"]["post_invoke"]) == 2
    assert any("run-tests" in h for h in config["hooks"]["post_invoke"])
    assert any("run-lint" in h for h in config["hooks"]["post_invoke"])


def test_scaffold_without_quality_gates_no_hooks(tmp_path):
    from weave.core.scaffold import scaffold_project

    scaffold_project(tmp_path, name="test-proj", with_quality_gates=False)

    hooks_dir = tmp_path / ".harness" / "hooks"
    assert not (hooks_dir / "run-tests.sh").exists()
    assert not (hooks_dir / "run-lint.sh").exists()

    config = json.loads((tmp_path / ".harness" / "config.json").read_text())
    assert config["hooks"]["post_invoke"] == []
```

- [ ] **Step 2: Run tests to confirm failure**

Run: `PYTHONPATH=src pytest tests/test_scaffold.py -v 2>&1 | tail -20`
Expected: `TypeError: scaffold_project() got an unexpected keyword argument 'with_quality_gates'`

- [ ] **Step 3: Create hook package markers**

Create `src/weave/hooks/__init__.py`:
```python
"""Weave hook scripts (package data)."""
```

Create `src/weave/hooks/builtin/__init__.py`:
```python
"""Built-in quality gate hooks shipped with weave."""
```

- [ ] **Step 4: Create the gate scripts**

Create `src/weave/hooks/builtin/run-tests.sh`:
```bash
#!/usr/bin/env bash
# Weave quality gate: run pytest
# Exit 0 = allow (tests pass), non-zero = deny (tests fail)
set -euo pipefail

INPUT=$(cat)  # consume stdin (hook protocol)
WORKING_DIR=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['working_dir'])" 2>/dev/null || echo ".")

cd "$WORKING_DIR"

if command -v pytest >/dev/null 2>&1; then
    pytest tests/ -x -q 2>&1 || exit 1
else
    echo "pytest not found, skipping test gate" >&2
    exit 0
fi
```

Create `src/weave/hooks/builtin/run-lint.sh`:
```bash
#!/usr/bin/env bash
# Weave quality gate: run ruff linter
# Exit 0 = allow (lint passes or ruff not installed), non-zero = deny
set -euo pipefail

INPUT=$(cat)  # consume stdin (hook protocol)
WORKING_DIR=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['working_dir'])" 2>/dev/null || echo ".")

cd "$WORKING_DIR"

if command -v ruff >/dev/null 2>&1; then
    ruff check . 2>&1 || exit 1
else
    echo "ruff not found, skipping lint gate" >&2
    exit 0
fi
```

Make both executable:
```bash
chmod +x src/weave/hooks/builtin/run-tests.sh src/weave/hooks/builtin/run-lint.sh
```

- [ ] **Step 5: Update `scaffold_project` in `scaffold.py`**

Add `with_quality_gates: bool = False` parameter to `scaffold_project()`.

Add a helper to find the built-in hooks dir:
```python
def _builtin_hooks_dir() -> Path:
    import weave
    return Path(weave.__file__).parent / "hooks" / "builtin"
```

At the end of `scaffold_project`, after the adapter script copying, add:
```python
    # Quality gates
    if with_quality_gates:
        hooks_dir = harness_dir / "hooks"
        builtin_hooks = _builtin_hooks_dir()
        for hook_name in ["run-tests.sh", "run-lint.sh"]:
            src_hook = builtin_hooks / hook_name
            dst_hook = hooks_dir / hook_name
            if src_hook.exists() and not dst_hook.exists():
                shutil.copy2(src_hook, dst_hook)
                dst_hook.chmod(
                    dst_hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                )
        config.hooks.post_invoke = [
            ".harness/hooks/run-tests.sh",
            ".harness/hooks/run-lint.sh",
        ]
```

Note: `shutil` and `stat` are already imported in `scaffold.py`.

IMPORTANT: The `config.hooks.post_invoke` assignment must happen BEFORE the config is written to `config.json`. Check the current order in `scaffold_project` — the config is written around line 80-99 (`(harness_dir / "config.json").write_text(...)`). The quality gates block must be placed BEFORE that write. Read the file carefully.

- [ ] **Step 6: Update `weave init` CLI command**

In `src/weave/cli.py`, find the `init_cmd` function and add a `--with-quality-gates` option:

```python
@main.command("init")
@click.option("--name", default=None, help="Project name (defaults to directory name)")
@click.option("--provider", default="claude-code", help="Default provider")
@click.option("--phase", default="sandbox", show_default=True,
              type=click.Choice(["sandbox", "mvp", "enterprise"]), help="Project phase")
@click.option("--with-quality-gates", is_flag=True, help="Install pytest + ruff post-invoke hooks")
def init_cmd(name, provider, phase, with_quality_gates):
```

Pass it through to `scaffold_project`:
```python
    scaffold_project(
        cwd,
        name=name,
        default_provider=provider,
        phase=phase,
        with_quality_gates=with_quality_gates,
    )
```

- [ ] **Step 7: Run the tests**

Run: `PYTHONPATH=src pytest tests/test_scaffold.py -v 2>&1 | tail -20`
Expected: 2 passed (or more if test_scaffold.py already had tests).

- [ ] **Step 8: Run full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -5`
Expected: 254 passed.

- [ ] **Step 9: Commit**

```bash
git add src/weave/hooks/ src/weave/core/scaffold.py src/weave/cli.py tests/test_scaffold.py
git commit -m "feat(scaffold): add quality gate hooks (pytest + ruff) with --with-quality-gates flag"
```

---

## Task 2: Add pyright type checking config

**Files:**
- Modify: `pyproject.toml`
- Create: `pyrightconfig.json`

- [ ] **Step 1: Create `pyrightconfig.json`**

```json
{
  "include": ["src"],
  "pythonVersion": "3.12",
  "pythonPlatform": "Linux",
  "typeCheckingMode": "basic",
  "reportMissingImports": true,
  "reportMissingTypeStubs": false,
  "reportUnusedImport": true,
  "reportUnusedVariable": true,
  "venvPath": ".",
  "venv": ".venv"
}
```

- [ ] **Step 2: Add pyright to dev dependencies in `pyproject.toml`**

Find:
```toml
[project.optional-dependencies]
dev = ["pytest>=7.0"]
```
Replace with:
```toml
[project.optional-dependencies]
dev = ["pytest>=7.0", "pyright>=1.1"]
```

- [ ] **Step 3: Run pyright to verify (best-effort)**

```bash
pip install pyright 2>/dev/null; PYTHONPATH=src pyright src/ 2>&1 | tail -20
```

If pyright reports errors, note them but don't fix them in this task — the goal is to add the config, not achieve zero errors. Type errors can be fixed incrementally.

- [ ] **Step 4: Run the full test suite to confirm no regressions**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -5`
Expected: 254 passed (no test changes in this task).

- [ ] **Step 5: Commit**

```bash
git add pyrightconfig.json pyproject.toml
git commit -m "chore: add pyright type checking config and dev dependency"
```

---

## Task 3: Final verification

- [ ] **Step 1: Full suite**

Run: `PYTHONPATH=src pytest tests/ -v 2>&1 | tail -20`
Expected: 254 passed.

- [ ] **Step 2: Verify hooks exist on disk**

```bash
ls -l src/weave/hooks/builtin/
```
Expected: `run-tests.sh` and `run-lint.sh` with executable bit.

- [ ] **Step 3: Verify init --with-quality-gates flag**

```bash
PYTHONPATH=src python3 -c "
from click.testing import CliRunner
from weave.cli import main
runner = CliRunner()
result = runner.invoke(main, ['init', '--help'])
print(result.output)
assert '--with-quality-gates' in result.output
print('flag exists: ok')
"
```

- [ ] **Step 4: No commit** — verification only.
