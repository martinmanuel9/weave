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
