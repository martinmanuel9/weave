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
