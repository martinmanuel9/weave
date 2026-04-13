"""Tests for the weave runtime pipeline."""
import json
from pathlib import Path

import pytest

import weave.core.registry as registry_module
from weave.schemas.policy import RiskClass, RuntimeStatus


def _reset_registry():
    """Reset the registry singleton so each test gets a fresh load."""
    registry_module._REGISTRY_SINGLETON = None


def _init_harness(root: Path):
    """Create a minimal .harness/ directory in root."""
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
        'echo \'{"protocol": "weave.response.v1", "exitCode": 0, "stdout": "ok", "stderr": "", "structured": null}\'\n'
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


def test_execute_denies_write_deny_in_sandbox(temp_dir):
    """Sandbox phase enforces deny (no longer downgrades to warn)."""
    from weave.core.runtime import execute
    _init_harness(temp_dir)

    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo "SECRET=leaked" > .env\n'
        'echo \'{"protocol": "weave.response.v1", "exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
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
    assert result.status == RuntimeStatus.DENIED
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
        'echo \'{"protocol": "weave.response.v1", "exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
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
    # Exclude .harness from git tracking so harness files don't appear as
    # untracked and trigger the write-deny-list check on config.json.
    (temp_dir / ".gitignore").write_text(".harness/\n")
    (temp_dir / "seed.txt").write_text("x")
    subprocess.run(["git", "add", ".gitignore", "seed.txt"], cwd=temp_dir, check=True)
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


def test_execute_respects_write_allow_overrides_in_mvp(temp_dir):
    """Full pipeline: mvp phase + allow override = SUCCESS (not DENIED).

    Without the allow override, writing config.json in mvp phase would
    hard-deny (proven by test_execute_denies_write_deny_in_mvp). With
    the override, it should succeed.
    """
    from weave.core.runtime import execute
    import json as _json
    _init_harness(temp_dir)

    # Switch to mvp phase AND add config.json to write_allow_overrides
    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["phase"] = "mvp"
    config["security"] = {"write_allow_overrides": ["config.json"]}
    config_path.write_text(_json.dumps(config))

    # Adapter that writes config.json (would normally be denied in mvp)
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo "{}" > config.json\n'
        'echo \'{"protocol": "weave.response.v1", "exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    (temp_dir / "seed.txt").write_text("x")
    # Commit .harness and seed so they don't appear as untracked files_changed
    subprocess.run(["git", "add", ".harness", "seed.txt"], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    result = execute(task="write config", working_dir=temp_dir, caller="test")
    assert result.status == RuntimeStatus.SUCCESS
    assert result.security_result is not None
    assert result.security_result.action_taken == "clean"
    # No findings because the file was exempted
    assert not any(
        f.rule_id == "write-deny-list"
        for f in result.security_result.findings
    )


def test_prepare_captures_pre_invoke_untracked(temp_dir):
    """prepare() snapshots untracked files via git ls-files --others."""
    from weave.core.runtime import prepare
    import subprocess

    _init_harness(temp_dir)

    # Initialize git and commit the harness
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    # Create an untracked file BEFORE prepare runs
    (temp_dir / "user_work.txt").write_text("pre-existing work")

    ctx = prepare(task="x", working_dir=temp_dir, caller="test")

    assert isinstance(ctx.pre_invoke_untracked, set)
    assert "user_work.txt" in ctx.pre_invoke_untracked


def test_prepare_pre_invoke_untracked_empty_for_non_git_dir(temp_dir):
    """prepare() gracefully returns empty set when working_dir is not a git repo."""
    from weave.core.runtime import prepare
    _init_harness(temp_dir)

    # No git init — this is not a git repo
    ctx = prepare(task="x", working_dir=temp_dir, caller="test")

    assert ctx.pre_invoke_untracked == set()


def test_execute_reverts_untracked_file_on_hard_deny(temp_dir):
    """mvp phase: adapter writes .env (untracked, denied) -> file is rm'd, files_reverted populated."""
    from weave.core.runtime import execute
    import json as _json
    import subprocess
    _init_harness(temp_dir)

    # Switch to mvp phase so deny is hard-deny (not downgrade to flagged)
    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["phase"] = "mvp"
    config_path.write_text(_json.dumps(config))

    # Adapter that writes .env (matches default deny list)
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo "SECRET=leaked" > .env\n'
        'echo \'{"protocol": "weave.response.v1", "exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    # Init git and commit the harness so .env is the only new untracked file
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    result = execute(task="make env", working_dir=temp_dir, caller="test")

    assert result.status == RuntimeStatus.DENIED
    assert not (temp_dir / ".env").exists()  # reverted
    assert result.security_result is not None
    assert ".env" in result.security_result.files_reverted


def test_execute_reverts_tracked_file_on_hard_deny(temp_dir):
    """mvp phase: adapter overwrites tracked config.json -> content restored from HEAD."""
    from weave.core.runtime import execute
    import json as _json
    import subprocess
    _init_harness(temp_dir)

    # Switch to mvp phase
    harness_config_path = temp_dir / ".harness" / "config.json"
    harness_config = _json.loads(harness_config_path.read_text())
    harness_config["phase"] = "mvp"
    harness_config_path.write_text(_json.dumps(harness_config))

    # Create and commit a tracked config.json at the root (not .harness/config.json)
    root_config = temp_dir / "config.json"
    root_config.write_text('{"version": "original"}')

    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    # Adapter overwrites config.json with tampered content
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo \'{"version": "tampered"}\' > config.json\n'
        'echo \'{"protocol": "weave.response.v1", "exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    result = execute(task="tamper config", working_dir=temp_dir, caller="test")

    assert result.status == RuntimeStatus.DENIED
    assert root_config.read_text() == '{"version": "original"}'  # restored from HEAD
    assert "config.json" in result.security_result.files_reverted


def test_execute_reverts_all_files_changed_not_just_flagged(temp_dir):
    """Denied invocations roll back the entire work, not just flagged files.

    Adapter writes helper.py (harmless) AND .env (denied). On hard-deny in
    mvp phase, BOTH must be reverted — even though only .env triggered the
    denial. This encodes the invariant that the unit of judgment is the
    invocation, not the individual file.
    """
    from weave.core.runtime import execute
    import json as _json
    import subprocess
    _init_harness(temp_dir)

    # mvp phase
    harness_config_path = temp_dir / ".harness" / "config.json"
    harness_config = _json.loads(harness_config_path.read_text())
    harness_config["phase"] = "mvp"
    harness_config_path.write_text(_json.dumps(harness_config))

    # Init git with clean baseline
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    # Adapter writes TWO untracked files: one flagged, one clean
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo "def add(a, b): return a + b" > helper.py\n'
        'echo "SECRET=leaked" > .env\n'
        'echo \'{"protocol": "weave.response.v1", "exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    result = execute(task="mixed write", working_dir=temp_dir, caller="test")

    assert result.status == RuntimeStatus.DENIED
    assert not (temp_dir / ".env").exists()  # flagged, reverted
    assert not (temp_dir / "helper.py").exists()  # NOT flagged, but STILL reverted
    assert ".env" in result.security_result.files_reverted
    assert "helper.py" in result.security_result.files_reverted


def test_execute_preserves_pre_existing_untracked_on_revert(temp_dir):
    """Pre-existing untracked files that trigger denial must be preserved.

    The pre_invoke_untracked snapshot protects user work: if an operator
    has an untracked credentials.json sitting in their working tree, and
    an invocation happens while that file is present, the revert must
    not delete it — even though it triggers a denial (because it's in
    files_changed from the git ls-files query and matches the deny list).
    """
    from weave.core.runtime import execute
    import json as _json
    import subprocess
    _init_harness(temp_dir)

    # mvp phase
    harness_config_path = temp_dir / ".harness" / "config.json"
    harness_config = _json.loads(harness_config_path.read_text())
    harness_config["phase"] = "mvp"
    harness_config_path.write_text(_json.dumps(harness_config))

    # Init git + commit the harness
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    # Pre-existing untracked file that matches the deny list
    user_file = temp_dir / "credentials.json"
    user_file.write_text('{"key": "my-personal-work"}')

    # Adapter is a no-op (writes nothing)
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo \'{"protocol": "weave.response.v1", "exitCode": 0, "stdout": "no-op", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    result = execute(task="noop", working_dir=temp_dir, caller="test")

    # The pre-existing credentials.json is picked up by git ls-files --others
    # and triggers the deny list -> status is DENIED
    assert result.status == RuntimeStatus.DENIED

    # But the file is in pre_invoke_untracked, so _revert skips it
    assert user_file.exists()
    assert user_file.read_text() == '{"key": "my-personal-work"}'
    assert "credentials.json" not in result.security_result.files_reverted


def test_execute_reverts_in_sandbox_phase(temp_dir):
    """Sandbox phase enforces deny and reverts files (Phase 3 change).

    resolve_action no longer downgrades 'deny' to 'warn' in sandbox, so the
    final action_taken is 'denied'. _revert removes the offending file.
    """
    from weave.core.runtime import execute
    import subprocess
    _init_harness(temp_dir)  # default phase is sandbox

    # Init git
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    # Adapter writes .env (denied in sandbox)
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo "SECRET=test" > .env\n'
        'echo \'{"protocol": "weave.response.v1", "exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    result = execute(task="make env", working_dir=temp_dir, caller="test")

    # Sandbox enforces deny -> DENIED
    assert result.status == RuntimeStatus.DENIED

    # .env is reverted (removed)
    assert not (temp_dir / ".env").exists()

    # files_reverted includes .env
    assert ".env" in result.security_result.files_reverted


def test_prepare_populates_context_assembly(temp_dir):
    """prepare() stores a ContextAssembly on PreparedContext.context."""
    from weave.core.runtime import prepare
    from weave.schemas.context import ContextAssembly
    _init_harness(temp_dir)

    ctx = prepare(task="x", working_dir=temp_dir, caller="test")

    assert isinstance(ctx.context, ContextAssembly)
    assert isinstance(ctx.context.full, str)
    assert isinstance(ctx.context.stable_hash, str)
    assert len(ctx.context.stable_hash) == 64  # sha256 hex length
    assert isinstance(ctx.context.source_files, list)


def test_execute_still_passes_context_string_to_invoker(temp_dir):
    """Invoker contract preserved: it receives ctx.context.full as a string.

    Uses a stub adapter that writes its received context (from the JSON
    stdin payload) to a file, then verifies the file content matches
    ctx.context.full produced by prepare().
    """
    from weave.core.runtime import execute, prepare
    _init_harness(temp_dir)

    # Stub adapter: parse stdin JSON, write "context" field to a file
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'INPUT=$(cat)\n'
        'echo "$INPUT" | python3 -c "import sys, json; '
        'data = json.loads(sys.stdin.read()); '
        'open(\'received_context.txt\', \'w\').write(data[\'context\'])"\n'
        'echo \'{"protocol": "weave.response.v1", "exitCode": 0, "stdout": "captured", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    # Capture the expected context by calling prepare() directly
    expected_ctx = prepare(task="x", working_dir=temp_dir, caller="test")
    expected_context_str = expected_ctx.context.full

    # Run execute and verify the adapter captured the same string
    execute(task="x", working_dir=temp_dir, caller="test")

    captured = (temp_dir / "received_context.txt").read_text()
    assert captured == expected_context_str
    assert isinstance(captured, str)


def test_prepare_writes_session_binding_sidecar(temp_dir):
    """prepare() writes a .binding.json sidecar next to the session."""
    import json as _json
    from weave.core.runtime import prepare
    _init_harness(temp_dir)

    ctx = prepare(task="x", working_dir=temp_dir, caller="test")

    sidecar = temp_dir / ".harness" / "sessions" / f"{ctx.session_id}.binding.json"
    assert sidecar.exists()

    data = _json.loads(sidecar.read_text())
    assert data["session_id"] == ctx.session_id
    assert "created_at" in data
    assert data["provider_name"] == ctx.active_provider
    assert len(data["adapter_script_hash"]) == 64
    assert len(data["context_stable_hash"]) == 64
    assert len(data["config_hash"]) == 64


def test_validate_session_raises_for_missing_binding(temp_dir):
    """validate_session raises FileNotFoundError when no sidecar exists."""
    import pytest
    from weave.core.runtime import prepare
    from weave.core.session_binding import validate_session
    _init_harness(temp_dir)

    ctx = prepare(task="x", working_dir=temp_dir, caller="test")

    # Delete the binding sidecar that prepare() just wrote
    sidecar = temp_dir / ".harness" / "sessions" / f"{ctx.session_id}.binding.json"
    sidecar.unlink()

    sessions_dir = temp_dir / ".harness" / "sessions"
    with pytest.raises(FileNotFoundError, match="No binding sidecar"):
        validate_session(ctx.session_id, ctx, sessions_dir)


def test_session_start_writes_marker_and_binding(temp_dir, monkeypatch):
    """weave session-start writes both binding sidecar and start marker, prints session_id."""
    import subprocess
    from click.testing import CliRunner
    from weave.cli import main

    _init_harness(temp_dir)

    # Initialize git so the marker captures git state
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    monkeypatch.chdir(temp_dir)
    runner = CliRunner()
    result = runner.invoke(main, ["session-start", "--task", "test plan"])
    assert result.exit_code == 0

    session_id = result.stdout.strip().splitlines()[0]
    assert len(session_id) >= 20  # UUID-ish

    # Both sidecars exist
    binding = temp_dir / ".harness" / "sessions" / f"{session_id}.binding.json"
    marker = temp_dir / ".harness" / "sessions" / f"{session_id}.start_marker.json"
    assert binding.exists()
    assert marker.exists()


def test_session_end_completes_clean_session(temp_dir, monkeypatch):
    """session-start + session-end on a clean working tree → SUCCESS, empty findings."""
    import subprocess
    from click.testing import CliRunner
    from weave.cli import main
    from weave.core.session import read_session_activities

    _init_harness(temp_dir)

    # Add .gitignore so .harness/ sidecars don't show up as untracked
    (temp_dir / ".gitignore").write_text(".harness/\n")

    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    monkeypatch.chdir(temp_dir)
    runner = CliRunner()
    start_result = runner.invoke(main, ["session-start", "--task", "clean test"])
    assert start_result.exit_code == 0
    session_id = start_result.stdout.strip().splitlines()[0]

    # Do nothing — clean working tree
    end_result = runner.invoke(main, ["session-end", "--session-id", session_id])
    assert end_result.exit_code == 0

    # JSONL contains a final ActivityRecord with success status
    sessions_dir = temp_dir / ".harness" / "sessions"
    records = read_session_activities(sessions_dir, session_id)
    assert len(records) >= 1
    final = records[-1]
    assert final.runtime_status == "success"
    assert final.security_findings == []


def test_session_end_detects_committed_denied_file_in_mvp(temp_dir, monkeypatch):
    """In mvp phase, a committed .env between session-start and session-end is denied.

    NOTE: The file is NOT reverted in the session-wrap flow because HEAD has
    advanced during the wrapped execution. MAR-139's _revert uses
    `git checkout HEAD -- <file>` for tracked files, which would restore the
    committed (leaked) content. This is a known gap — the revert semantics
    were designed for the weave invoke flow where HEAD is the pre-invocation
    state. A future task can extend _revert to handle the session-wrap case.

    For now, this test verifies the denial is detected and recorded even
    though physical revert is incomplete.
    """
    import json as _json
    import subprocess
    from click.testing import CliRunner
    from weave.cli import main
    from weave.core.session import read_session_activities

    _init_harness(temp_dir)

    # Switch to mvp phase
    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["phase"] = "mvp"
    config_path.write_text(_json.dumps(config))

    # Add .gitignore BEFORE init so .harness/ sidecars are ignored
    (temp_dir / ".gitignore").write_text(".harness/\n")

    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=temp_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    monkeypatch.chdir(temp_dir)
    runner = CliRunner()
    start_result = runner.invoke(main, ["session-start", "--task", "denied test"])
    assert start_result.exit_code == 0
    session_id = start_result.stdout.strip().splitlines()[0]

    # Simulate the wrapped subagent: commit a .env file (matches default deny list)
    (temp_dir / ".env").write_text("SECRET=leaked")
    subprocess.run(["git", "add", ".env"], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "leak secret"], cwd=temp_dir, check=True)

    end_result = runner.invoke(main, ["session-end", "--session-id", session_id])
    assert end_result.exit_code == 2  # DENIED

    # NOTE: The file is still present due to the MAR-139 gap documented above.
    # Physical revert is incomplete when HEAD advances during wrapped execution.

    # JSONL records the denial
    sessions_dir = temp_dir / ".harness" / "sessions"
    records = read_session_activities(sessions_dir, session_id)
    final = records[-1]
    assert final.runtime_status == "denied"
    assert any(
        f.get("rule_id") == "write-deny-list"
        for f in final.security_findings
    )


def test_session_end_raises_for_missing_marker(temp_dir, monkeypatch):
    """session-end errors clearly when no marker exists for the given session_id."""
    from click.testing import CliRunner
    from weave.cli import main

    _init_harness(temp_dir)

    monkeypatch.chdir(temp_dir)
    runner = CliRunner()
    result = runner.invoke(main, ["session-end", "--session-id", "nonexistent-uuid"])
    assert result.exit_code != 0
    assert "No start marker" in result.stderr or "No start marker" in result.output


def test_session_end_handles_non_git_directory_gracefully(temp_dir, monkeypatch):
    """session-end in a non-git directory: no enforcement, session still recorded."""
    from click.testing import CliRunner
    from weave.cli import main
    from weave.core.session import read_session_activities

    _init_harness(temp_dir)
    # NO git init — non-git directory

    monkeypatch.chdir(temp_dir)
    runner = CliRunner()
    start_result = runner.invoke(main, ["session-start", "--task", "non-git test"])
    assert start_result.exit_code == 0
    session_id = start_result.stdout.strip().splitlines()[0]

    # Modify some files (won't be tracked because non-git)
    (temp_dir / "anything.txt").write_text("anything")

    end_result = runner.invoke(main, ["session-end", "--session-id", session_id])
    assert end_result.exit_code == 0  # SUCCESS, no enforcement

    # JSONL recorded the session as success with empty files_changed
    sessions_dir = temp_dir / ".harness" / "sessions"
    records = read_session_activities(sessions_dir, session_id)
    final = records[-1]
    assert final.runtime_status == "success"
    assert final.files_changed == []


# ---------------------------------------------------------------------------
# Task 8 — new tests for contract integration
# ---------------------------------------------------------------------------


def test_prepare_attaches_provider_contract(temp_dir):
    """prepare() resolves and attaches a ProviderContract to PreparedContext."""
    from weave.core.runtime import prepare
    from weave.schemas.provider_contract import ProviderContract

    _reset_registry()
    _init_harness(temp_dir)

    ctx = prepare(task="x", working_dir=temp_dir, caller="test")

    assert isinstance(ctx.provider_contract, ProviderContract)
    assert ctx.provider_contract.name == "claude-code"
    assert ctx.provider_contract.capability_ceiling == RiskClass.WORKSPACE_WRITE


def test_prepare_raises_runtime_error_for_unknown_provider(temp_dir):
    """prepare() raises RuntimeError when config references an unknown provider."""
    from weave.core.runtime import prepare

    _reset_registry()
    harness = _init_harness(temp_dir)

    # Add an unknown provider to the config
    config_path = harness / "config.json"
    config_path.write_text(json.dumps({
        "version": "1",
        "phase": "sandbox",
        "default_provider": "claude-code",
        "providers": {
            "claude-code": {
                "command": "claude",
                "enabled": True,
                "capability_override": "workspace-write",
            },
            "mystery-ai": {
                "command": "mystery",
                "enabled": True,
            },
        },
    }))

    with pytest.raises(RuntimeError, match="unknown provider.*mystery-ai"):
        prepare(task="x", working_dir=temp_dir, provider="mystery-ai", caller="test")


def test_config_load_rejects_capability_override_above_ceiling(temp_dir):
    """resolve_config raises ValueError when capability_override > contract ceiling."""
    from weave.core.config import resolve_config

    _reset_registry()
    harness = temp_dir / ".harness"
    harness.mkdir()
    for sub in ["context", "hooks", "providers", "sessions", "integrations"]:
        (harness / sub).mkdir()
    (harness / "manifest.json").write_text(json.dumps({
        "id": "test-id", "type": "project", "name": "test",
        "status": "active", "phase": "sandbox",
    }))
    # ollama has ceiling=read-only; set capability_override=destructive
    (harness / "config.json").write_text(json.dumps({
        "version": "1",
        "phase": "sandbox",
        "default_provider": "claude-code",
        "providers": {
            "claude-code": {"command": "claude", "enabled": True},
            "ollama": {
                "command": "ollama",
                "enabled": True,
                "capability_override": "destructive",
            },
        },
    }))

    with pytest.raises(ValueError, match="capability_override.*exceeds contract ceiling"):
        resolve_config(temp_dir, user_home=temp_dir)


def test_prepare_effective_capability_clamps_to_contract_ceiling(temp_dir):
    """Policy effective risk class is clamped to the contract ceiling.

    ollama has ceiling=read-only. Without any override, the effective
    capability should be read-only (not the default workspace-write).
    """
    from weave.core.runtime import prepare
    from weave.core.policy import evaluate_policy

    _reset_registry()
    _init_harness(temp_dir)

    # Add ollama to config and switch to it as default
    config_path = temp_dir / ".harness" / "config.json"
    config_path.write_text(json.dumps({
        "version": "1",
        "phase": "sandbox",
        "default_provider": "ollama",
        "providers": {
            "claude-code": {
                "command": "claude",
                "enabled": True,
                "capability_override": "workspace-write",
            },
            "ollama": {
                "command": "ollama",
                "enabled": True,
            },
        },
    }))

    ctx = prepare(task="x", working_dir=temp_dir, provider="ollama", caller="test")

    # The contract ceiling is read-only
    assert ctx.provider_contract.capability_ceiling == RiskClass.READ_ONLY

    # Policy evaluation clamps effective to read-only
    policy = evaluate_policy(
        contract=ctx.provider_contract,
        provider_config=ctx.provider_config,
        requested_class=None,
        phase=ctx.phase,
    )
    assert policy.effective_risk_class == RiskClass.READ_ONLY


def test_execute_metadata_passthrough(temp_dir):
    """AC-3: metadata kwarg lands in ActivityRecord.metadata."""
    from weave.core.runtime import execute
    from weave.core.session import read_session_activities
    _init_harness(temp_dir)
    result = execute(
        task="with meta",
        working_dir=temp_dir,
        caller="test",
        metadata={"cje_score": 0.87, "intent": "code_generation"},
    )
    assert result.status == RuntimeStatus.SUCCESS
    sessions_dir = temp_dir / ".harness" / "sessions"
    records = read_session_activities(sessions_dir, result.session_id)
    assert records[0].metadata["cje_score"] == 0.87
    assert records[0].metadata["intent"] == "code_generation"


def test_execute_no_metadata_defaults_empty(temp_dir):
    """AC-7: No metadata parameter = empty dict (backwards compat)."""
    from weave.core.runtime import execute
    from weave.core.session import read_session_activities
    _init_harness(temp_dir)
    result = execute(task="no meta", working_dir=temp_dir, caller="test")
    sessions_dir = temp_dir / ".harness" / "sessions"
    records = read_session_activities(sessions_dir, result.session_id)
    assert records[0].metadata == {}


# ---------------------------------------------------------------------------
# Task 3 — Post-Scan Hook Stage (REQ-3)
# ---------------------------------------------------------------------------


def test_post_scan_hook_runs_on_success(temp_dir):
    """AC-4: post_scan hook runs after security scan on success."""
    import json as _json, stat
    from weave.core.runtime import execute
    _init_harness(temp_dir)

    marker = temp_dir / "post_scan_ran"
    hook = temp_dir / ".harness" / "hooks" / "gate.sh"
    hook.write_text(f'#!/bin/bash\ntouch {marker}\nexit 0\n')
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["hooks"] = {"post_scan": [str(hook)]}
    config_path.write_text(_json.dumps(config))

    result = execute(task="go", working_dir=temp_dir, caller="test")
    assert result.status == RuntimeStatus.SUCCESS
    assert marker.exists()


def test_post_scan_hook_deny_triggers_revert(temp_dir):
    """AC-4: post_scan deny sets DENIED and reverts files."""
    import json as _json, stat, subprocess
    from weave.core.runtime import execute
    _init_harness(temp_dir)

    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=temp_dir, check=True)
    (temp_dir / ".gitignore").write_text(".harness/\n")
    (temp_dir / "seed.txt").write_text("original")
    subprocess.run(["git", "add", ".gitignore", "seed.txt"], cwd=temp_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=temp_dir, check=True)

    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo "modified" > seed.txt\n'
        'echo \'{"protocol": "weave.response.v1", "exitCode": 0, '
        '"stdout": "done", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    hook = temp_dir / ".harness" / "hooks" / "deny-gate.sh"
    hook.write_text('#!/bin/bash\necho "quality too low" >&2\nexit 1\n')
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["hooks"] = {"post_scan": [str(hook)]}
    config_path.write_text(_json.dumps(config))

    result = execute(task="modify seed", working_dir=temp_dir, caller="test")
    assert result.status == RuntimeStatus.DENIED
    assert (temp_dir / "seed.txt").read_text() == "original"


def test_post_scan_hook_skipped_on_invoke_failure(temp_dir):
    """AC-5: post_scan hooks do NOT run when invoke fails."""
    import json as _json, stat
    from weave.core.runtime import execute
    _init_harness(temp_dir)

    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo \'{"protocol": "weave.response.v1", "exitCode": 1, '
        '"stdout": "", "stderr": "boom", "structured": null}\'\n'
        'exit 1\n'
    )
    adapter.chmod(0o755)

    marker = temp_dir / "should_not_exist"
    hook = temp_dir / ".harness" / "hooks" / "gate.sh"
    hook.write_text(f'#!/bin/bash\ntouch {marker}\nexit 0\n')
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["hooks"] = {"post_scan": [str(hook)]}
    config_path.write_text(_json.dumps(config))

    result = execute(task="fail", working_dir=temp_dir, caller="test")
    assert result.status == RuntimeStatus.FAILED
    assert not marker.exists()


def test_post_scan_hook_skipped_on_timeout(temp_dir):
    """AC-5: post_scan hooks do NOT run on timeout."""
    import json as _json, stat
    from weave.core.runtime import execute
    _init_harness(temp_dir)

    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo \'{"protocol": "weave.response.v1", "exitCode": 124, '
        '"stdout": "", "stderr": "timed out", "structured": null}\'\n'
        'exit 124\n'
    )
    adapter.chmod(0o755)

    marker = temp_dir / "should_not_exist"
    hook = temp_dir / ".harness" / "hooks" / "gate.sh"
    hook.write_text(f'#!/bin/bash\ntouch {marker}\nexit 0\n')
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["hooks"] = {"post_scan": [str(hook)]}
    config_path.write_text(_json.dumps(config))

    result = execute(task="timeout", working_dir=temp_dir, caller="test")
    assert result.status == RuntimeStatus.TIMEOUT
    assert not marker.exists()


def test_post_scan_hook_receives_enriched_context(temp_dir):
    """AC-1 + AC-4: post_scan hook receives security findings and files_changed."""
    import json as _json, stat
    from weave.core.runtime import execute
    _init_harness(temp_dir)

    output_file = temp_dir / "hook_input.json"
    hook = temp_dir / ".harness" / "hooks" / "inspector.sh"
    hook.write_text(f'#!/bin/bash\ncat > {output_file}\nexit 0\n')
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    config_path = temp_dir / ".harness" / "config.json"
    config = _json.loads(config_path.read_text())
    config["hooks"] = {"post_scan": [str(hook)]}
    config_path.write_text(_json.dumps(config))

    result = execute(task="inspect", working_dir=temp_dir, caller="test")
    assert result.status == RuntimeStatus.SUCCESS
    assert output_file.exists()

    received = _json.loads(output_file.read_text())
    assert "risk_class" in received
    assert "session_id" in received
    assert "files_changed" in received
    assert "exit_code" in received
    assert "security_findings" in received
    assert received["phase"] == "post-scan"
