"""Self-healing engine -- fallback retry on provider failure."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from weave.schemas.skill import SkillDefinition, HealingLogEntry

logger = logging.getLogger(__name__)


@dataclass
class HealingResult:
    healed: bool
    fallback_provider: str | None = None
    invoke_result: Any = None
    healing_log_entry: HealingLogEntry | None = None
    attempts: int = 0
    fallback_details: list[dict] = field(default_factory=list)


def attempt_healing(
    failure_reason: str,
    skill: SkillDefinition,
    task: str,
    working_dir: Path,
    session_id: str,
) -> HealingResult:
    """
    Try fallback providers from skill.strategy.fallback_providers.
    Returns HealingResult indicating whether recovery succeeded.
    """
    fallbacks = skill.strategy.fallback_providers
    if not fallbacks:
        logger.info("No fallback providers for skill %s", skill.name)
        return HealingResult(healed=False)

    fallback_details = []
    for provider in fallbacks:
        logger.info(
            "Healing %s: trying fallback provider %s", skill.name, provider
        )
        try:
            invoke_result = _invoke_fallback(
                provider=provider,
                task=task,
                working_dir=working_dir,
                session_id=session_id,
                timeout=skill.strategy.timeout_ms // 1000,
            )
        except Exception as exc:
            logger.warning("Fallback %s raised: %s", provider, exc)
            fallback_details.append({
                "provider": provider,
                "outcome": "error",
                "error": str(exc),
            })
            continue

        if invoke_result.exit_code == 0 and invoke_result.stdout.strip():
            log_entry = HealingLogEntry(
                trigger=failure_reason,
                action=f"fallback to {provider}",
                outcome="success",
                duration_ms=int(invoke_result.duration),
            )
            fallback_details.append({
                "provider": provider,
                "outcome": "success",
                "duration_ms": int(invoke_result.duration),
            })
            return HealingResult(
                healed=True,
                fallback_provider=provider,
                invoke_result=invoke_result,
                healing_log_entry=log_entry,
                attempts=len(fallback_details),
                fallback_details=fallback_details,
            )

        fallback_details.append({
            "provider": provider,
            "outcome": "failure",
            "exit_code": invoke_result.exit_code,
        })

    logger.warning("All fallbacks exhausted for skill %s", skill.name)
    return HealingResult(
        healed=False,
        attempts=len(fallback_details),
        fallback_details=fallback_details,
    )


def _invoke_fallback(
    provider: str,
    task: str,
    working_dir: Path,
    session_id: str,
    timeout: int = 300,
) -> Any:
    """Invoke a fallback provider through Weave's runtime.
    Imported lazily to avoid circular imports with runtime.py.
    """
    from weave.core.invoker import invoke_provider
    from weave.core.registry import get_registry

    registry = get_registry()
    contract = registry.get(provider)
    return invoke_provider(
        contract=contract,
        task=task,
        session_id=session_id,
        working_dir=working_dir,
        timeout=timeout,
        registry=registry,
    )
