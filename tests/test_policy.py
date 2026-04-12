"""Tests for the weave policy engine."""
from __future__ import annotations

import pytest

from tests.conftest import make_contract
from weave.schemas.config import ProviderConfig
from weave.schemas.policy import RiskClass


def test_resolve_risk_class_returns_contract_ceiling_when_no_override_no_request():
    from weave.core.policy import resolve_risk_class
    result = resolve_risk_class(
        contract_ceiling=RiskClass.WORKSPACE_WRITE,
        config_override=None,
        requested=None,
    )
    assert result == RiskClass.WORKSPACE_WRITE


def test_resolve_risk_class_returns_config_override_when_below_ceiling():
    from weave.core.policy import resolve_risk_class
    result = resolve_risk_class(
        contract_ceiling=RiskClass.EXTERNAL_NETWORK,
        config_override=RiskClass.READ_ONLY,
        requested=None,
    )
    assert result == RiskClass.READ_ONLY


def test_resolve_risk_class_config_override_above_ceiling_is_clamped_silently():
    """Config validation catches this earlier; policy clamps defensively."""
    from weave.core.policy import resolve_risk_class
    result = resolve_risk_class(
        contract_ceiling=RiskClass.READ_ONLY,
        config_override=RiskClass.DESTRUCTIVE,
        requested=None,
    )
    assert result == RiskClass.READ_ONLY


def test_resolve_risk_class_allows_caller_to_request_lower():
    from weave.core.policy import resolve_risk_class
    result = resolve_risk_class(
        contract_ceiling=RiskClass.EXTERNAL_NETWORK,
        config_override=None,
        requested=RiskClass.READ_ONLY,
    )
    assert result == RiskClass.READ_ONLY


def test_resolve_risk_class_rejects_request_above_effective_ceiling():
    from weave.core.policy import resolve_risk_class
    with pytest.raises(ValueError, match="exceeds effective ceiling"):
        resolve_risk_class(
            contract_ceiling=RiskClass.READ_ONLY,
            config_override=None,
            requested=RiskClass.DESTRUCTIVE,
        )


def test_evaluate_policy_mvp_phase_allows_within_safe_ceiling():
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.WORKSPACE_WRITE)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(command="x"),
        requested_class=RiskClass.WORKSPACE_WRITE,
        phase="mvp",
    )
    assert result.allowed is True
    assert result.effective_risk_class == RiskClass.WORKSPACE_WRITE
    assert result.provider_ceiling == RiskClass.WORKSPACE_WRITE


def test_evaluate_policy_mvp_phase_allows_safe_class():
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.WORKSPACE_WRITE)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(command="x"),
        requested_class=None,
        phase="mvp",
    )
    assert result.allowed is True
    assert result.effective_risk_class == RiskClass.WORKSPACE_WRITE


def test_evaluate_policy_rejects_request_above_ceiling():
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.READ_ONLY)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(command="x"),
        requested_class=RiskClass.DESTRUCTIVE,
        phase="mvp",
    )
    assert result.allowed is False
    assert any("ceiling" in d.lower() for d in result.denials)


def test_evaluate_policy_mvp_denies_external_network():
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


def test_evaluate_policy_enterprise_denies_destructive():
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.DESTRUCTIVE)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(command="x"),
        requested_class=None,
        phase="enterprise",
    )
    assert result.allowed is False


def test_evaluate_policy_sandbox_restricts_high_risk():
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


def test_evaluate_policy_provider_ceiling_from_contract_not_config():
    """PolicyResult.provider_ceiling must reflect the contract, not config."""
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.READ_ONLY)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(
            command="x",
            capability_override=RiskClass.READ_ONLY,
        ),
        requested_class=None,
        phase="sandbox",
    )
    assert result.provider_ceiling == RiskClass.READ_ONLY


def test_evaluate_policy_config_override_narrows_below_ceiling():
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.WORKSPACE_WRITE)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(
            command="x",
            capability_override=RiskClass.READ_ONLY,
        ),
        requested_class=None,
        phase="mvp",
    )
    assert result.allowed is True
    assert result.effective_risk_class == RiskClass.READ_ONLY
