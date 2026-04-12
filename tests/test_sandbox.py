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
