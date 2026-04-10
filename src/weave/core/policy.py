"""Policy engine — risk class resolution and phase-dependent enforcement."""
from __future__ import annotations

from weave.schemas.config import ProviderConfig
from weave.schemas.policy import (
    PolicyResult,
    RiskClass,
    risk_class_level,
)


PHASE_ENFORCEMENT = {
    "sandbox": "warn",
    "mvp": "deny",
    "enterprise": "deny",
}


def resolve_risk_class(
    provider: ProviderConfig,
    requested: RiskClass | None,
) -> RiskClass:
    """Resolve effective risk class: provider ceiling ∩ caller override (lower only).

    Raises ValueError if caller requests a class above the provider's ceiling.
    """
    ceiling = provider.capability
    if requested is None:
        return ceiling
    if risk_class_level(requested) > risk_class_level(ceiling):
        raise ValueError(
            f"Requested risk class {requested.value} exceeds provider ceiling "
            f"{ceiling.value}"
        )
    return requested


def evaluate_policy(
    provider: ProviderConfig,
    requested_class: RiskClass | None,
    phase: str,
) -> PolicyResult:
    """Evaluate whether an invocation is allowed under the current phase.

    Returns PolicyResult with allowed flag, effective risk class, warnings/denials.
    Pre-invoke hooks are run separately by the runtime (not here) so this stays
    a pure policy decision.
    """
    warnings: list[str] = []
    denials: list[str] = []

    try:
        effective = resolve_risk_class(provider, requested_class)
    except ValueError as exc:
        return PolicyResult(
            allowed=False,
            effective_risk_class=provider.capability,
            provider_ceiling=provider.capability,
            requested_class=requested_class,
            warnings=warnings,
            denials=[str(exc)],
        )

    enforcement = PHASE_ENFORCEMENT.get(phase, "warn")
    is_high_risk = risk_class_level(effective) >= risk_class_level(
        RiskClass.EXTERNAL_NETWORK
    )

    if enforcement == "warn" and is_high_risk:
        warnings.append(
            f"Phase '{phase}' permits {effective.value} but this is a high-risk class"
        )
    elif enforcement == "deny" and is_high_risk:
        denials.append(
            f"Phase '{phase}' denies {effective.value} class invocations"
        )
        return PolicyResult(
            allowed=False,
            effective_risk_class=effective,
            provider_ceiling=provider.capability,
            requested_class=requested_class,
            warnings=warnings,
            denials=denials,
        )

    return PolicyResult(
        allowed=True,
        effective_risk_class=effective,
        provider_ceiling=provider.capability,
        requested_class=requested_class,
        warnings=warnings,
        denials=denials,
    )
