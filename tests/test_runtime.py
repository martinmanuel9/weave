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


def test_execute_flags_write_deny_in_sandbox(temp_dir):
    """Sandbox phase downgrades deny to warn, so status is FLAGGED."""
    from weave.core.runtime import execute
    _init_harness(temp_dir)

    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo "SECRET=leaked" > .env\n'
        'echo \'{"exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    (temp_dir / "seed.txt").write_text("x")
    subprocess.run(["git", "add", "seed.txt"], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    result = execute(task="make env", working_dir=temp_dir, caller="test")
    assert result.status == RuntimeStatus.FLAGGED
    assert result.security_result is not None
    assert any(
        f.rule_id == "write-deny-list"
        for f in result.security_result.findings
    )


def test_execute_denies_write_deny_in_mvp(temp_dir):
    """MVP phase preserves deny, so status is DENIED."""
    from weave.core.runtime import execute
    import json as _json
    _init_harness(temp_dir)

    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["phase"] = "mvp"
    config_path.write_text(_json.dumps(config))

    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo "fake" > credentials.json\n'
        'echo \'{"exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    (temp_dir / "seed.txt").write_text("x")
    subprocess.run(["git", "add", "seed.txt"], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    result = execute(task="make creds", working_dir=temp_dir, caller="test")
    assert result.status == RuntimeStatus.DENIED
    assert result.security_result.action_taken == "denied"


def test_execute_denies_when_requested_class_exceeds_ceiling(temp_dir):
    from weave.core.runtime import execute
    _init_harness(temp_dir)
    result = execute(
        task="x",
        working_dir=temp_dir,
        caller="test",
        requested_risk_class=RiskClass.DESTRUCTIVE,
    )
    assert result.status == RuntimeStatus.DENIED
    assert result.policy_result.allowed is False
    assert result.invoke_result is None
    assert any("ceiling" in d.lower() for d in result.policy_result.denials)


def test_cli_invoke_routes_through_runtime(temp_dir, monkeypatch):
    from click.testing import CliRunner
    from weave.cli import main
    import subprocess

    _init_harness(temp_dir)
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    (temp_dir / "seed.txt").write_text("x")
    subprocess.run(["git", "add", "seed.txt"], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    monkeypatch.chdir(temp_dir)
    runner = CliRunner()
    result = runner.invoke(main, ["invoke", "say hi"])
    assert result.exit_code == 0
    sessions = list((temp_dir / ".harness" / "sessions").glob("*.jsonl"))
    assert len(sessions) == 1
    content = sessions[0].read_text()
    assert '"runtime_status"' in content
    assert '"caller":"cli"' in content or '"caller": "cli"' in content


def test_ensure_harness_creates_when_missing(temp_dir):
    from weave.core.runtime import ensure_harness
    assert not (temp_dir / ".harness").exists()
    ensure_harness(temp_dir, name="test-proj")
    assert (temp_dir / ".harness").exists()
    assert (temp_dir / ".harness" / "config.json").exists()
    assert (temp_dir / ".harness" / "manifest.json").exists()


def test_ensure_harness_noop_when_exists(temp_dir):
    from weave.core.runtime import ensure_harness
    _init_harness(temp_dir)
    original = (temp_dir / ".harness" / "manifest.json").read_text()
    ensure_harness(temp_dir, name="different-name")
    assert (temp_dir / ".harness" / "manifest.json").read_text() == original


def test_public_api_importable():
    from weave.core import execute, ensure_harness, RuntimeResult
    assert callable(execute)
    assert callable(ensure_harness)
    assert RuntimeResult is not None
