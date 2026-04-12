# Design: Quality Gates + Type Checking

**Date:** 2026-04-11
**Phase:** 4 (items 4.6 + 4.7)
**Status:** draft

## 4.6 Quality Gates

### Problem
The hook system supports post-invoke scripts but none ship with weave. Users who want quality gates (run tests, run linter after each invocation) must write their own hook scripts from scratch.

### Changes
1. Ship 2 built-in gate scripts in `src/weave/hooks/builtin/`: `run-tests.sh` (pytest) and `run-lint.sh` (ruff).
2. `scaffold_project()` gains `with_quality_gates: bool = False` — when True, copies gate scripts to `.harness/hooks/` and wires `config.hooks.post_invoke`.
3. `weave init` gains `--with-quality-gates` flag.

## 4.7 Type Checking

### Problem
249+ tests, 4k+ lines of typed Python, no type checker. Adding pyright catches type errors at dev time.

### Changes
1. Add `pyright` to dev dependencies in `pyproject.toml`.
2. Create `pyrightconfig.json` with appropriate settings for the `src/` layout.
3. Verify pyright passes on the codebase (manual, not a test).

## Files

| Path | Change |
|---|---|
| `src/weave/hooks/__init__.py` | NEW — package marker |
| `src/weave/hooks/builtin/__init__.py` | NEW — package marker |
| `src/weave/hooks/builtin/run-tests.sh` | NEW — pytest gate |
| `src/weave/hooks/builtin/run-lint.sh` | NEW — ruff gate |
| `src/weave/core/scaffold.py` | MODIFIED — `with_quality_gates` param |
| `src/weave/cli.py` | MODIFIED — `--with-quality-gates` flag on init |
| `pyproject.toml` | MODIFIED — add pyright to dev deps |
| `pyrightconfig.json` | NEW — pyright config |
| `tests/test_scaffold.py` or `tests/test_hooks.py` | +2 tests |
