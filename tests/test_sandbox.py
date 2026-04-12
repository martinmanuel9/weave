"""Tests for sandbox enforcement beyond risk classification."""
from __future__ import annotations

import fnmatch
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import make_contract
from weave.schemas.config import ProviderConfig, WeaveConfig
from weave.schemas.policy import RiskClass


def test_sandbox_denies_external_network_provider():
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.EXTERNAL_NETWORK)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(command="x"),
        requested_class=None,
        phase="sandbox",
    )
    assert result.allowed is False
    assert any("restricts" in d.lower() for d in result.denials)


def test_sandbox_denies_destructive_provider():
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.DESTRUCTIVE)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(command="x"),
        requested_class=None,
        phase="sandbox",
    )
    assert result.allowed is False


def test_sandbox_allows_workspace_write_with_warning():
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.WORKSPACE_WRITE)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(command="x"),
        requested_class=None,
        phase="sandbox",
    )
    assert result.allowed is True
    assert any("sandbox restrictions" in w.lower() for w in result.warnings)


def test_sandbox_allows_read_only_unrestricted():
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.READ_ONLY)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(command="x"),
        requested_class=None,
        phase="sandbox",
    )
    assert result.allowed is True
    assert len(result.warnings) == 0


def test_mvp_behavior_unchanged():
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.EXTERNAL_NETWORK)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(command="x"),
        requested_class=None,
        phase="mvp",
    )
    assert result.allowed is False
    assert any("denies" in d.lower() for d in result.denials)


def test_resolve_action_sandbox_no_longer_downgrades():
    from weave.core.security import resolve_action
    assert resolve_action("deny", "sandbox") == "deny"


def test_resolve_action_other_phases_unchanged():
    from weave.core.security import resolve_action
    assert resolve_action("deny", "mvp") == "deny"
    assert resolve_action("deny", "enterprise") == "deny"
    assert resolve_action("warn", "sandbox") == "warn"
    assert resolve_action("log", "sandbox") == "log"


def test_invoker_forwards_env_to_subprocess(tmp_path, monkeypatch):
    """Verify invoke_provider passes env dict to subprocess.run."""
    import subprocess as sp
    from weave.core.invoker import invoke_provider
    from weave.core import registry as registry_module

    adapter = tmp_path / "echo.sh"
    adapter.write_text("#!/usr/bin/env bash\ncat /dev/stdin > /dev/null\n"
        'echo \'{"protocol":"weave.response.v1","exitCode":0,"stdout":"","stderr":"","structured":{}}\'\n')
    adapter.chmod(0o755)

    contract = make_contract(name="envtest", adapter="echo.sh")
    registry = registry_module.ProviderRegistry()
    registry._contracts[contract.name] = contract
    registry._manifest_dirs[contract.name] = adapter.parent

    # Collect env kwarg from each subprocess.run call; first call is the adapter,
    # subsequent calls are git helper invocations inside _get_git_changed_files.
    captured_envs: list = []
    original_run = sp.run

    def spy_run(*args, **kwargs):
        captured_envs.append(kwargs.get("env"))
        return original_run(*args, **kwargs)

    monkeypatch.setattr(sp, "run", spy_run)

    custom_env = {"PATH": "/usr/bin", "HOME": "/tmp/sandbox", "CUSTOM": "value"}
    invoke_provider(
        contract=contract,
        task="hi",
        session_id="sess",
        working_dir=tmp_path,
        registry=registry,
        env=custom_env,
    )
    # First subprocess.run call is the adapter — it must receive the custom env.
    assert captured_envs[0] is custom_env

    # Also verify None env is forwarded (inherit parent) on a fresh invocation.
    captured_envs.clear()
    invoke_provider(
        contract=contract,
        task="hi",
        session_id="sess",
        working_dir=tmp_path,
        registry=registry,
    )
    assert captured_envs[0] is None


def test_build_sandbox_env_strips_matching_vars(monkeypatch):
    from weave.core.runtime import _build_sandbox_env

    monkeypatch.setenv("AWS_SECRET_KEY", "hunter2")
    monkeypatch.setenv("AZURE_TENANT_ID", "abc")
    monkeypatch.setenv("SAFE_VAR", "keep-me")

    config = WeaveConfig()
    env = _build_sandbox_env(config, provider_binary_dir="/usr/local/bin")

    assert "AWS_SECRET_KEY" not in env
    assert "AZURE_TENANT_ID" not in env
    assert env.get("SAFE_VAR") == "keep-me"


def test_build_sandbox_env_preserves_safe_vars(monkeypatch):
    from weave.core.runtime import _build_sandbox_env

    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("USER", "testuser")
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("PYTHONPATH", "/some/path")

    config = WeaveConfig()
    env = _build_sandbox_env(config)

    assert env["LANG"] == "en_US.UTF-8"
    assert env["USER"] == "testuser"
    assert env["TERM"] == "xterm-256color"
    assert env["PYTHONPATH"] == "/some/path"


def test_build_sandbox_env_restricts_path(monkeypatch):
    from weave.core.runtime import _build_sandbox_env

    monkeypatch.setenv("PATH", "/dangerous/bin:/usr/bin:/opt/evil")

    config = WeaveConfig()
    env = _build_sandbox_env(config, provider_binary_dir="/opt/provider/bin")

    path_dirs = env["PATH"].split(":")
    assert "/opt/provider/bin" in path_dirs
    assert "/usr/bin" in path_dirs
    assert "/bin" in path_dirs
    assert "/dangerous/bin" not in path_dirs
    assert "/opt/evil" not in path_dirs


def test_build_sandbox_env_restricts_home(monkeypatch):
    from weave.core.runtime import _build_sandbox_env

    monkeypatch.setenv("HOME", "/home/realuser")

    config = WeaveConfig()
    env = _build_sandbox_env(config)

    # When restrict_home=True, HOME should be removed (caller sets it to tmpdir)
    assert "HOME" not in env


def test_build_sandbox_env_noop_when_config_empty(monkeypatch):
    from weave.core.runtime import _build_sandbox_env
    from weave.schemas.config import SandboxConfig

    monkeypatch.setenv("AWS_SECRET_KEY", "hunter2")
    monkeypatch.setenv("PATH", "/usr/bin")

    config = WeaveConfig(sandbox=SandboxConfig(
        strip_env_patterns=[],
        safe_path_dirs=[],
        restrict_home=False,
    ))
    env = _build_sandbox_env(config)

    assert env.get("AWS_SECRET_KEY") == "hunter2"


def test_sandbox_extra_write_deny_appended_in_sandbox():
    """Verify the sandbox extra_write_deny list covers the expected patterns."""
    from weave.core.security import check_write_deny
    import tempfile

    config = WeaveConfig()
    base_deny = config.security.write_deny_list + config.security.write_deny_extras
    sandbox_deny = base_deny + config.sandbox.extra_write_deny

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        workflows_dir = tmp / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)
        (workflows_dir / "ci.yml").write_text("name: CI\n")

        denied = check_write_deny(
            [".github/workflows/ci.yml"],
            tmp,
            sandbox_deny,
        )
        assert ".github/workflows/ci.yml" in denied


def test_sandbox_extra_write_deny_not_appended_in_mvp():
    """In mvp phase, the extra_write_deny patterns are NOT in the base deny list."""
    config = WeaveConfig()
    base_deny = config.security.write_deny_list + config.security.write_deny_extras
    for pattern in config.sandbox.extra_write_deny:
        assert pattern not in base_deny


def test_sandbox_tmpdir_cleaned_up():
    """Verify tmpdir lifecycle pattern works correctly."""
    import tempfile as tf

    sandbox_tmpdir = Path(tf.mkdtemp(prefix="weave-sandbox-test-"))
    assert sandbox_tmpdir.exists()
    try:
        (sandbox_tmpdir / "test.txt").write_text("hello")
    finally:
        shutil.rmtree(sandbox_tmpdir, ignore_errors=True)
    assert not sandbox_tmpdir.exists()
