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


def test_compute_binding_uses_context_stable_hash(temp_dir):
    """compute_binding reuses the ContextAssembly.stable_hash, not a recomputed value."""
    from weave.core.runtime import prepare
    from weave.core.session_binding import compute_binding

    _init_harness(temp_dir)
    ctx = prepare(task="x", working_dir=temp_dir, caller="test")

    binding = compute_binding(ctx)

    # The binding's context_stable_hash must equal the one already
    # computed by assemble_context in MAR-142 — no recomputation.
    assert binding.context_stable_hash == ctx.context.stable_hash


def test_compute_binding_config_hash_is_canonical():
    """Config hash is byte-stable regardless of dict insertion order."""
    from weave.core.session_binding import _hash_config
    from weave.schemas.config import ProviderConfig, WeaveConfig
    from weave.schemas.policy import RiskClass

    # Two configs with semantically identical providers but
    # different insertion order
    config_a = WeaveConfig(
        version="1",
        phase="sandbox",
        default_provider="claude-code",
        providers={
            "claude-code": ProviderConfig(command="claude", capability=RiskClass.WORKSPACE_WRITE),
            "codex": ProviderConfig(command="codex", capability=RiskClass.WORKSPACE_WRITE),
            "gemini": ProviderConfig(command="gemini", capability=RiskClass.WORKSPACE_WRITE),
        },
    )
    config_b = WeaveConfig(
        version="1",
        phase="sandbox",
        default_provider="claude-code",
        providers={
            "gemini": ProviderConfig(command="gemini", capability=RiskClass.WORKSPACE_WRITE),
            "codex": ProviderConfig(command="codex", capability=RiskClass.WORKSPACE_WRITE),
            "claude-code": ProviderConfig(command="claude", capability=RiskClass.WORKSPACE_WRITE),
        },
    )

    assert _hash_config(config_a) == _hash_config(config_b)
