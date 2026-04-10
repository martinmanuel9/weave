"""Tests for the weave policy engine."""
import pytest

from weave.schemas.policy import RiskClass
from weave.schemas.config import ProviderConfig


def test_resolve_risk_class_uses_provider_ceiling_when_no_override():
    from weave.core.policy import resolve_risk_class
    provider = ProviderConfig(command="x", capability=RiskClass.WORKSPACE_WRITE)
    result = resolve_risk_class(provider, requested=None)
    assert result == RiskClass.WORKSPACE_WRITE


def test_resolve_risk_class_allows_caller_to_request_lower():
    from weave.core.policy import resolve_risk_class
    provider = ProviderConfig(command="x", capability=RiskClass.EXTERNAL_NETWORK)
    result = resolve_risk_class(provider, requested=RiskClass.READ_ONLY)
    assert result == RiskClass.READ_ONLY


def test_resolve_risk_class_rejects_request_above_ceiling():
    from weave.core.policy import resolve_risk_class
    provider = ProviderConfig(command="x", capability=RiskClass.READ_ONLY)
    with pytest.raises(ValueError, match="exceeds provider ceiling"):
        resolve_risk_class(provider, requested=RiskClass.DESTRUCTIVE)


def test_evaluate_policy_sandbox_phase_always_allows_within_ceiling():
    from weave.core.policy import evaluate_policy
    provider = ProviderConfig(command="x", capability=RiskClass.DESTRUCTIVE)
    result = evaluate_policy(
        provider=provider,
        requested_class=RiskClass.DESTRUCTIVE,
        phase="sandbox",
    )
    assert result.allowed is True
    assert result.effective_risk_class == RiskClass.DESTRUCTIVE


def test_evaluate_policy_mvp_phase_allows_safe_class():
    from weave.core.policy import evaluate_policy
    provider = ProviderConfig(command="x", capability=RiskClass.WORKSPACE_WRITE)
    result = evaluate_policy(
        provider=provider,
        requested_class=None,
        phase="mvp",
    )
    assert result.allowed is True
    assert result.effective_risk_class == RiskClass.WORKSPACE_WRITE


def test_evaluate_policy_rejects_request_above_ceiling():
    from weave.core.policy import evaluate_policy
    provider = ProviderConfig(command="x", capability=RiskClass.READ_ONLY)
    result = evaluate_policy(
        provider=provider,
        requested_class=RiskClass.DESTRUCTIVE,
        phase="mvp",
    )
    assert result.allowed is False
    assert any("ceiling" in d.lower() for d in result.denials)


def test_evaluate_policy_mvp_denies_external_network():
    from weave.core.policy import evaluate_policy
    provider = ProviderConfig(command="x", capability=RiskClass.EXTERNAL_NETWORK)
    result = evaluate_policy(
        provider=provider,
        requested_class=None,
        phase="mvp",
    )
    assert result.allowed is False
    assert any("denies" in d.lower() for d in result.denials)


def test_evaluate_policy_enterprise_denies_destructive():
    from weave.core.policy import evaluate_policy
    provider = ProviderConfig(command="x", capability=RiskClass.DESTRUCTIVE)
    result = evaluate_policy(
        provider=provider,
        requested_class=None,
        phase="enterprise",
    )
    assert result.allowed is False


def test_evaluate_policy_sandbox_warns_on_high_risk():
    from weave.core.policy import evaluate_policy
    provider = ProviderConfig(command="x", capability=RiskClass.EXTERNAL_NETWORK)
    result = evaluate_policy(
        provider=provider,
        requested_class=None,
        phase="sandbox",
    )
    assert result.allowed is True
    assert len(result.warnings) >= 1
    assert any("high-risk" in w.lower() for w in result.warnings)
