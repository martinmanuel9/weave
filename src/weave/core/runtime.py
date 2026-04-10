"""Weave runtime — governed execution pipeline.

Pipeline: prepare -> policy_check -> invoke -> security_scan -> cleanup -> record.
Single entrypoint for all agent invocations, whether from the CLI, itzel,
or GSD.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from weave.core.config import resolve_config
from weave.core.invoker import InvokeResult
from weave.core.session import create_session
from weave.schemas.config import ProviderConfig, WeaveConfig
from weave.schemas.policy import (
    PolicyResult,
    RiskClass,
    RuntimeStatus,
    SecurityResult,
)


@dataclass
class PreparedContext:
    """Everything the pipeline needs after the prepare stage."""
    config: WeaveConfig
    active_provider: str
    provider_config: ProviderConfig
    adapter_script: Path
    context_text: str
    session_id: str
    working_dir: Path
    phase: str
    task: str
    caller: str | None
    requested_risk_class: RiskClass | None


@dataclass
class RuntimeResult:
    invoke_result: InvokeResult | None
    policy_result: PolicyResult
    security_result: SecurityResult | None
    session_id: str
    risk_class: RiskClass
    status: RuntimeStatus


def _load_context(working_dir: Path) -> str:
    """Concatenate markdown files from .harness/context/ in sorted order."""
    parts: list[str] = []
    context_dir = working_dir / ".harness" / "context"
    if context_dir.exists():
        for md in sorted(context_dir.glob("*.md")):
            if not md.name.startswith("."):
                parts.append(md.read_text())
    return "\n---\n".join(parts)


def prepare(
    task: str,
    working_dir: Path,
    provider: str | None = None,
    caller: str | None = None,
    requested_risk_class: RiskClass | None = None,
) -> PreparedContext:
    """Stage 1: load config, resolve provider, assemble context, create session."""
    config = resolve_config(working_dir)
    active_provider = provider or config.default_provider

    provider_config = config.providers.get(active_provider)
    if provider_config is None:
        raise ValueError(f"Provider '{active_provider}' not configured")

    adapter_script = working_dir / ".harness" / "providers" / f"{active_provider}.sh"
    context_text = _load_context(working_dir)
    session_id = create_session()

    return PreparedContext(
        config=config,
        active_provider=active_provider,
        provider_config=provider_config,
        adapter_script=adapter_script,
        context_text=context_text,
        session_id=session_id,
        working_dir=working_dir,
        phase=config.phase,
        task=task,
        caller=caller,
        requested_risk_class=requested_risk_class,
    )
