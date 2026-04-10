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
        'echo \'{"exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
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
        'echo \'{"exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
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
        'echo \'{"exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
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
        'echo \'{"exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
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
        'echo \'{"exitCode": 0, "stdout": "no-op", "stderr": "", "structured": null}\'\n'
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


def test_execute_no_revert_in_sandbox_phase(temp_dir):
    """Sandbox phase flags findings but never reverts files.

    resolve_action downgrades 'deny' to 'warn' in sandbox, so the final
    action_taken is 'flagged' (not 'denied'). _revert is a no-op on
    anything other than action_taken=='denied', so files stay on disk.
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

    # Adapter writes .env (flagged but not denied in sandbox)
    adapter = temp_dir / ".harness" / "providers" / "claude-code.sh"
    adapter.write_text(
        '#!/bin/bash\n'
        'read INPUT\n'
        'echo "SECRET=test" > .env\n'
        'echo \'{"exitCode": 0, "stdout": "done", "stderr": "", "structured": null}\'\n'
    )
    adapter.chmod(0o755)

    result = execute(task="make env", working_dir=temp_dir, caller="test")

    # Sandbox downgrades deny to warn -> FLAGGED, not DENIED
    assert result.status == RuntimeStatus.FLAGGED

    # .env still exists (not reverted)
    assert (temp_dir / ".env").exists()
    assert (temp_dir / ".env").read_text() == "SECRET=test\n"

    # files_reverted is empty
    assert result.security_result.files_reverted == []
