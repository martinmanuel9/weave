"""Tests for session binding computation, I/O, and validation."""
import json
from pathlib import Path


def _init_harness(root: Path):
    """Create a minimal .harness/ directory for prepare() to consume."""
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


def test_compute_binding_produces_all_fields(temp_dir):
    """compute_binding returns a SessionBinding with all six fields populated."""
    from weave.core.runtime import prepare
    from weave.core.session_binding import compute_binding
    from weave.schemas.session_binding import SessionBinding

    _init_harness(temp_dir)
    ctx = prepare(task="x", working_dir=temp_dir, caller="test")

    binding = compute_binding(ctx)

    assert isinstance(binding, SessionBinding)
    assert binding.session_id == ctx.session_id
    assert binding.provider_name == ctx.active_provider
    assert len(binding.adapter_script_hash) == 64
    assert len(binding.context_stable_hash) == 64
    assert len(binding.config_hash) == 64
    # created_at is timezone-aware
    assert binding.created_at.tzinfo is not None
