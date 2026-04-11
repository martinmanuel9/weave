"""Tests for the ProviderContract schema."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from weave.schemas.policy import RiskClass
from weave.schemas.provider_contract import (
    AdapterRuntime,
    ProviderContract,
    ProviderFeature,
    ProviderProtocol,
)


def _valid_contract_dict(**overrides) -> dict:
    base = {
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
        "declared_features": ["tool-use", "file-edit"],
        "health_check": "claude --version",
    }
    base.update(overrides)
    return base


def test_provider_contract_validates_good_manifest():
    contract = ProviderContract.model_validate(_valid_contract_dict())
    assert contract.name == "claude-code"
    assert contract.adapter_runtime == AdapterRuntime.BASH
    assert contract.capability_ceiling == RiskClass.WORKSPACE_WRITE
    assert ProviderFeature.TOOL_USE in contract.declared_features
    assert contract.source == "builtin"  # default


def test_provider_contract_rejects_unknown_feature():
    bad = _valid_contract_dict(declared_features=["tool-use", "not-a-real-feature"])
    with pytest.raises(ValidationError):
        ProviderContract.model_validate(bad)


def test_provider_contract_rejects_unknown_request_schema():
    bad = _valid_contract_dict(protocol={
        "request_schema": "weave.request.v999",
        "response_schema": "weave.response.v1",
    })
    with pytest.raises(ValidationError, match="request_schema"):
        ProviderContract.model_validate(bad)


def test_provider_contract_rejects_unknown_response_schema():
    bad = _valid_contract_dict(protocol={
        "request_schema": "weave.request.v1",
        "response_schema": "weave.response.v999",
    })
    with pytest.raises(ValidationError, match="response_schema"):
        ProviderContract.model_validate(bad)


def test_provider_contract_rejects_unknown_adapter_runtime():
    bad = _valid_contract_dict(adapter_runtime="perl")
    with pytest.raises(ValidationError):
        ProviderContract.model_validate(bad)


def test_provider_contract_rejects_unknown_capability_ceiling():
    bad = _valid_contract_dict(capability_ceiling="superuser")
    with pytest.raises(ValidationError):
        ProviderContract.model_validate(bad)


def test_provider_contract_version_is_literal_one():
    bad = _valid_contract_dict(contract_version="2")
    with pytest.raises(ValidationError):
        ProviderContract.model_validate(bad)
