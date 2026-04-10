"""Tests for the weave runtime pipeline."""
import json
from pathlib import Path

import pytest

from weave.schemas.policy import RiskClass, RuntimeStatus


def _init_harness(root: Path):
    """Create a minimal .harness/ directory in root."""
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


def test_prepare_loads_config(temp_dir):
    from weave.core.runtime import prepare
    _init_harness(temp_dir)
    ctx = prepare(
        task="do a thing",
        working_dir=temp_dir,
        provider=None,
        caller="test",
    )
    assert ctx.config.default_provider == "claude-code"
    assert ctx.active_provider == "claude-code"
    assert ctx.session_id is not None
    assert ctx.phase == "sandbox"


def test_prepare_honors_provider_override(temp_dir):
    from weave.core.runtime import prepare
    _init_harness(temp_dir)
    ctx = prepare(
        task="x",
        working_dir=temp_dir,
        provider="claude-code",
        caller="test",
    )
    assert ctx.active_provider == "claude-code"


def test_prepare_raises_when_provider_not_configured(temp_dir):
    from weave.core.runtime import prepare
    _init_harness(temp_dir)
    with pytest.raises(ValueError, match="not configured"):
        prepare(task="x", working_dir=temp_dir, provider="ghost", caller="test")


def test_execute_happy_path(temp_dir):
    from weave.core.runtime import execute
    _init_harness(temp_dir)
    result = execute(
        task="say hi",
        working_dir=temp_dir,
        caller="test",
    )
    assert result.status == RuntimeStatus.SUCCESS
    assert result.policy_result.allowed is True
    assert result.invoke_result is not None
    assert result.invoke_result.exit_code == 0
    assert result.risk_class == RiskClass.WORKSPACE_WRITE


def test_execute_logs_activity(temp_dir):
    from weave.core.runtime import execute
    from weave.core.session import read_session_activities
    _init_harness(temp_dir)
    result = execute(task="x", working_dir=temp_dir, caller="test")
    sessions_dir = temp_dir / ".harness" / "sessions"
    records = read_session_activities(sessions_dir, result.session_id)
    assert len(records) == 1
    assert records[0].caller == "test"
    assert records[0].runtime_status == "success"
    assert records[0].risk_class == "workspace-write"
