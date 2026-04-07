"""Tests for weave.core.hooks — script and Python callable hook chains."""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import pytest

from weave.core.hooks import HookChainResult, HookContext, run_hooks


@pytest.fixture
def ctx() -> HookContext:
    return HookContext(
        provider="claude",
        task="write a test",
        working_dir="/tmp",
        phase="pre-invoke",
    )


def _make_script(tmp_path: Path, exit_code: int, stderr_msg: str = "") -> str:
    """Write a tiny bash script that exits with the given code."""
    script = tmp_path / "hook.sh"
    lines = ["#!/usr/bin/env bash"]
    if stderr_msg:
        lines.append(f'echo "{stderr_msg}" >&2')
    lines.append(f"exit {exit_code}")
    script.write_text("\n".join(lines) + "\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(script)


# ---------------------------------------------------------------------------
# Script hook tests
# ---------------------------------------------------------------------------


def test_no_hooks_allows(ctx: HookContext) -> None:
    result = run_hooks([], ctx)
    assert isinstance(result, HookChainResult)
    assert result.allowed is True
    assert result.results == []


def test_allow_hook(tmp_path: Path, ctx: HookContext) -> None:
    script = _make_script(tmp_path, exit_code=0)
    result = run_hooks([script], ctx)
    assert result.allowed is True
    assert len(result.results) == 1
    assert result.results[0].result == "allow"


def test_deny_hook(tmp_path: Path, ctx: HookContext) -> None:
    script = _make_script(tmp_path, exit_code=1, stderr_msg="denied by policy")
    result = run_hooks([script], ctx)
    assert result.allowed is False
    assert len(result.results) == 1
    assert result.results[0].result == "deny"
    assert result.results[0].message is not None
    assert "denied by policy" in result.results[0].message


# ---------------------------------------------------------------------------
# Python callable tests
# ---------------------------------------------------------------------------


def test_python_callable_allow(ctx: HookContext) -> None:
    result = run_hooks([], ctx, callables=[lambda c: True])
    assert result.allowed is True
    assert len(result.results) == 1
    assert result.results[0].result == "allow"


def test_python_callable_deny(ctx: HookContext) -> None:
    def blocker(c: HookContext) -> bool:
        return False

    result = run_hooks([], ctx, callables=[blocker])
    assert result.allowed is False
    assert result.results[0].result == "deny"


# ---------------------------------------------------------------------------
# Mixed test
# ---------------------------------------------------------------------------


def test_mixed_hooks(tmp_path: Path, ctx: HookContext) -> None:
    """Script allows, then callable denies — overall result is denied."""
    script = _make_script(tmp_path, exit_code=0)

    def denying_callable(c: HookContext) -> bool:
        return False

    result = run_hooks([script], ctx, callables=[denying_callable])
    assert result.allowed is False
    # First result from script (allow), second from callable (deny)
    assert len(result.results) == 2
    assert result.results[0].result == "allow"
    assert result.results[1].result == "deny"
