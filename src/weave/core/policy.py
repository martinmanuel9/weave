"""Policy engine — risk class resolution and phase-dependent enforcement."""
from __future__ import annotations

from weave.schemas.config import ProviderConfig
from weave.schemas.policy import (
    PolicyResult,
    RiskClass,
    risk_class_level,
)
from weave.schemas.provider_contract import ProviderContract


PHASE_ENFORCEMENT = {
    "sandbox": "restrict",
    "mvp": "deny",
    "enterprise": "deny",
}


def resolve_risk_class(
    contract_ceiling: RiskClass,
    config_override: RiskClass | None,
    requested: RiskClass | None,
) -> RiskClass:
    """Resolve effective risk class by walking three inputs in order.

    contract ceiling -> config override -> caller requested

    Each step may only restrict (lower the ordinal level), never elevate.
    `config_override` above the ceiling is clamped silently (config load
    validates this earlier; the clamp is defense in depth).
    `requested` above the already-clamped ceiling raises ValueError.
    """
    effective = contract_ceiling

    if config_override is not None and risk_class_level(config_override) <= risk_class_level(effective):
        effective = config_override

    if requested is not None:
        if risk_class_level(requested) > risk_class_level(effective):
            raise ValueError(
                f"Requested risk class {requested.value} exceeds effective "
                f"ceiling {effective.value}"
            )
        effective = requested

    return effective


def evaluate_policy(
    contract: ProviderContract,
    provider_config: ProviderConfig,
    requested_class: RiskClass | None,
    phase: str,
) -> PolicyResult:
    """Evaluate whether an invocation is allowed under the current phase.

    Pre-invoke hooks are run separately by the runtime (not here) so this
    stays a pure policy decision.
    """
    warnings: list[str] = []
    denials: list[str] = []
    ceiling = contract.capability_ceiling

    try:
        effective = resolve_risk_class(
            contract_ceiling=ceiling,
            config_override=provider_config.capability_override,
            requested=requested_class,
        )
    except ValueError as exc:
        return PolicyResult(
            allowed=False,
            effective_risk_class=ceiling,
            provider_ceiling=ceiling,
            requested_class=requested_class,
            warnings=warnings,
            denials=[str(exc)],
        )

    enforcement = PHASE_ENFORCEMENT.get(phase, "warn")
    is_high_risk = risk_class_level(effective) >= risk_class_level(RiskClass.EXTERNAL_NETWORK)

    if enforcement == "restrict":
        if is_high_risk:
            denials.append(
                f"Phase '{phase}' restricts {effective.value} class invocations"
            )
            return PolicyResult(
                allowed=False,
                effective_risk_class=effective,
                provider_ceiling=ceiling,
                requested_class=requested_class,
                warnings=warnings,
                denials=denials,
            )
        elif risk_class_level(effective) >= risk_class_level(RiskClass.WORKSPACE_WRITE):
            warnings.append(
                f"Phase '{phase}' applies sandbox restrictions to {effective.value}"
            )
    elif enforcement == "warn" and is_high_risk:
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
            provider_ceiling=ceiling,
            requested_class=requested_class,
            warnings=warnings,
            denials=denials,
        )

    return PolicyResult(
        allowed=True,
        effective_risk_class=effective,
        provider_ceiling=ceiling,
        requested_class=requested_class,
        warnings=warnings,
        denials=denials,
    )
