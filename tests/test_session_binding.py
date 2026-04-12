"""Tests for session binding computation, I/O, and validation."""
import json
from pathlib import Path

import weave.core.registry as registry_module


def _reset_registry():
    registry_module._REGISTRY_SINGLETON = None


def _init_harness(root: Path):
    """Create a minimal .harness/ directory for prepare() to consume."""
    _reset_registry()
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
                "capability_override": "workspace-write",
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
    # Write contract sidecar so the registry picks up this user adapter
    (harness / "providers" / "claude-code.contract.json").write_text(json.dumps({
        "contract_version": "1",
        "name": "claude-code",
        "display_name": "Claude Code",
        "adapter": "claude-code.sh",
        "adapter_runtime": "bash",
        "capability_ceiling": "workspace-write",
        "protocol": {
            "request_schema": "weave.request.v1",
            "response_schema": "weave.response.v1",
        },
    }))
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
            "claude-code": ProviderConfig(command="claude", capability_override=RiskClass.WORKSPACE_WRITE),
            "codex": ProviderConfig(command="codex", capability_override=RiskClass.WORKSPACE_WRITE),
            "gemini": ProviderConfig(command="gemini", capability_override=RiskClass.WORKSPACE_WRITE),
        },
    )
    config_b = WeaveConfig(
        version="1",
        phase="sandbox",
        default_provider="claude-code",
        providers={
            "gemini": ProviderConfig(command="gemini", capability_override=RiskClass.WORKSPACE_WRITE),
            "codex": ProviderConfig(command="codex", capability_override=RiskClass.WORKSPACE_WRITE),
            "claude-code": ProviderConfig(command="claude", capability_override=RiskClass.WORKSPACE_WRITE),
        },
    )

    assert _hash_config(config_a) == _hash_config(config_b)


def test_write_and_read_binding_round_trip(temp_dir):
    """write_binding + read_binding is lossless for all fields."""
    from datetime import datetime, timezone
    from weave.core.session_binding import read_binding, write_binding
    from weave.schemas.session_binding import SessionBinding

    sessions_dir = temp_dir / ".harness" / "sessions"
    original = SessionBinding(
        session_id="test-session-123",
        created_at=datetime(2026, 4, 10, 12, 34, 56, tzinfo=timezone.utc),
        provider_name="claude-code",
        adapter_script_hash="a" * 64,
        context_stable_hash="b" * 64,
        config_hash="c" * 64,
    )

    written_path = write_binding(original, sessions_dir)
    assert written_path.exists()
    assert written_path.name == "test-session-123.binding.json"

    loaded = read_binding("test-session-123", sessions_dir)
    assert loaded is not None
    assert loaded.session_id == original.session_id
    assert loaded.created_at == original.created_at
    assert loaded.provider_name == original.provider_name
    assert loaded.adapter_script_hash == original.adapter_script_hash
    assert loaded.context_stable_hash == original.context_stable_hash
    assert loaded.config_hash == original.config_hash


def test_read_binding_returns_none_for_missing_file(temp_dir):
    """read_binding returns None when the sidecar file does not exist."""
    from weave.core.session_binding import read_binding

    sessions_dir = temp_dir / ".harness" / "sessions"
    sessions_dir.mkdir(parents=True)

    result = read_binding("nonexistent", sessions_dir)
    assert result is None


def test_validate_session_returns_empty_for_matching_binding(temp_dir):
    """Identical inputs produce zero mismatches — session is reusable."""
    from weave.core.runtime import prepare
    from weave.core.session_binding import compute_binding, validate_session, write_binding

    _init_harness(temp_dir)

    # Create session 1, write its binding
    ctx1 = prepare(task="x", working_dir=temp_dir, caller="test")
    binding = compute_binding(ctx1)
    sessions_dir = temp_dir / ".harness" / "sessions"
    write_binding(binding, sessions_dir)

    # Prepare a new context on the SAME working_dir (nothing changed on disk).
    # Note: ctx2 will have a different session_id (prepare always creates a
    # fresh UUID), but the four compatibility fields should match.
    ctx2 = prepare(task="x", working_dir=temp_dir, caller="test")

    # Validate the OLD session_id against the NEW ctx
    mismatches = validate_session(ctx1.session_id, ctx2, sessions_dir)
    assert mismatches == []


def test_validate_session_detects_config_hash_mismatch(temp_dir):
    """Changing .harness/config.json flips the config_hash — detected as mismatch."""
    from weave.core.runtime import prepare
    from weave.core.session_binding import compute_binding, validate_session, write_binding

    _init_harness(temp_dir)

    # Prepare context, write binding
    ctx1 = prepare(task="x", working_dir=temp_dir, caller="test")
    binding = compute_binding(ctx1)
    sessions_dir = temp_dir / ".harness" / "sessions"
    write_binding(binding, sessions_dir)

    # Modify the config (phase sandbox -> mvp)
    config_path = temp_dir / ".harness" / "config.json"
    config = json.loads(config_path.read_text())
    config["phase"] = "mvp"
    config_path.write_text(json.dumps(config))

    # Prepare a new context against the modified config
    ctx2 = prepare(task="x", working_dir=temp_dir, caller="test")

    mismatches = validate_session(ctx1.session_id, ctx2, sessions_dir)
    assert mismatches == ["config_hash"]


def test_session_binding_policy_config_defaults():
    from weave.schemas.config import SessionBindingPolicy, SessionsConfig
    cfg = SessionsConfig()
    assert cfg.binding_policy == SessionBindingPolicy.WARN
    assert cfg.binding_policy.value == "warn"


import logging
import subprocess

import pytest


def _make_project_with_binding(tmp_path, session_id="existing-sess", phase="mvp"):
    """Create a minimal weave project with git, config, and an existing binding."""
    from weave.core import registry as registry_module
    registry_module._REGISTRY_SINGLETON = None

    repo = tmp_path
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)

    harness = repo / ".harness"
    for sub in ["context", "hooks", "providers", "sessions", "integrations"]:
        (harness / sub).mkdir(parents=True, exist_ok=True)

    (harness / "manifest.json").write_text(json.dumps({
        "id": "t", "type": "project", "name": "t", "status": "active",
        "phase": phase, "parent": None, "children": [],
        "provider": "claude-code", "agent": None,
        "created": "2026-04-11T00:00:00Z", "updated": "2026-04-11T00:00:00Z",
        "inputs": {}, "outputs": {}, "tags": [],
    }))
    (harness / "config.json").write_text(json.dumps({
        "version": "1", "phase": phase, "default_provider": "claude-code",
        "providers": {"claude-code": {"command": "claude", "enabled": True}},
    }))
    (harness / "context" / "conventions.md").write_text("# Conventions\nBe nice.\n")

    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    # Create binding for target session_id using current state
    from weave.core.runtime import prepare
    ctx = prepare(task="setup", working_dir=repo)

    from weave.core.session_binding import compute_binding, write_binding
    binding = compute_binding(ctx)
    binding = binding.model_copy(update={"session_id": session_id})
    write_binding(binding, harness / "sessions")

    return repo, session_id


def test_prepare_with_session_id_validates_binding_warn(tmp_path, caplog):
    from weave.core.runtime import prepare
    from weave.core import registry as registry_module

    repo, session_id = _make_project_with_binding(tmp_path)

    # Change config to cause drift
    (repo / ".harness" / "config.json").write_text(json.dumps({
        "version": "2",
        "phase": "mvp", "default_provider": "claude-code",
        "providers": {"claude-code": {"command": "claude", "enabled": True}},
        "sessions": {"binding_policy": "warn"},
    }))

    registry_module._REGISTRY_SINGLETON = None
    with caplog.at_level(logging.WARNING):
        ctx = prepare(task="reuse test", working_dir=repo, session_id=session_id)

    assert ctx.session_id == session_id
    assert any("config_hash" in rec.message for rec in caplog.records)


def test_prepare_with_session_id_validates_binding_strict(tmp_path):
    from weave.core.runtime import prepare
    from weave.core import registry as registry_module

    repo, session_id = _make_project_with_binding(tmp_path)

    (repo / ".harness" / "config.json").write_text(json.dumps({
        "version": "2",
        "phase": "mvp", "default_provider": "claude-code",
        "providers": {"claude-code": {"command": "claude", "enabled": True}},
        "sessions": {"binding_policy": "strict"},
    }))

    registry_module._REGISTRY_SINGLETON = None
    with pytest.raises(ValueError, match="config_hash"):
        prepare(task="reuse test", working_dir=repo, session_id=session_id)


def test_prepare_with_session_id_missing_binding(tmp_path):
    from weave.core.runtime import prepare
    from weave.core import registry as registry_module
    registry_module._REGISTRY_SINGLETON = None

    repo, _ = _make_project_with_binding(tmp_path, session_id="dummy")

    registry_module._REGISTRY_SINGLETON = None
    ctx = prepare(task="fresh", working_dir=repo, session_id="brand-new-sess")
    assert ctx.session_id == "brand-new-sess"
    assert (repo / ".harness" / "sessions" / "brand-new-sess.binding.json").exists()


def test_prepare_with_session_id_rebind(tmp_path, caplog):
    from weave.core.runtime import prepare
    from weave.core import registry as registry_module

    repo, session_id = _make_project_with_binding(tmp_path)

    (repo / ".harness" / "config.json").write_text(json.dumps({
        "version": "2",
        "phase": "mvp", "default_provider": "claude-code",
        "providers": {"claude-code": {"command": "claude", "enabled": True}},
        "sessions": {"binding_policy": "rebind"},
    }))

    registry_module._REGISTRY_SINGLETON = None
    with caplog.at_level(logging.INFO):
        ctx = prepare(task="reuse test", working_dir=repo, session_id=session_id)

    assert ctx.session_id == session_id
    # Should be info, not warning
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING and "config_hash" in r.message]
    assert len(warning_records) == 0
