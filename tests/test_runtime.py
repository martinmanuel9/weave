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
