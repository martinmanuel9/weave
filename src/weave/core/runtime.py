"""Weave runtime — governed execution pipeline.

Pipeline: prepare -> policy_check -> invoke -> security_scan -> cleanup -> record.
Single entrypoint for all agent invocations, whether from the CLI, itzel,
or GSD.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from weave.core.config import resolve_config
from weave.core.hooks import HookContext, run_hooks
from weave.core.invoker import InvokeResult, invoke_provider
from weave.core.policy import evaluate_policy
from weave.core.security import DEFAULT_RULES, check_write_deny, resolve_action, scan_files
from weave.core.session import append_activity, create_session
from weave.schemas.activity import ActivityRecord, ActivityStatus, ActivityType, HookResult
from weave.schemas.config import ProviderConfig, WeaveConfig
from weave.schemas.policy import (
    HookResultRef,
    PolicyResult,
    RiskClass,
    RuntimeStatus,
    SecurityFinding,
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


def _policy_check(ctx: PreparedContext) -> tuple[PolicyResult, list[HookResult]]:
    """Stage 2: evaluate policy and run pre-invoke hooks."""
    policy = evaluate_policy(
        provider=ctx.provider_config,
        requested_class=ctx.requested_risk_class,
        phase=ctx.phase,
    )

    if not policy.allowed:
        return policy, []

    hook_ctx = HookContext(
        provider=ctx.active_provider,
        task=ctx.task,
        working_dir=str(ctx.working_dir),
        phase="pre-invoke",
    )
    chain = run_hooks(ctx.config.hooks.pre_invoke, hook_ctx)

    policy.hook_results = [
        HookResultRef(
            hook=r.hook,
            phase=r.phase,
            result=r.result,
            message=r.message,
        )
        for r in chain.results
    ]
    if not chain.allowed:
        policy.allowed = False
        policy.denials.append("Pre-invoke hook denied execution")

    return policy, chain.results


def _security_scan(
    ctx: PreparedContext,
    invoke_result: InvokeResult,
) -> SecurityResult:
    """Stage 4: supply chain scanner + write deny list over files_changed."""
    files = invoke_result.files_changed
    findings: list[SecurityFinding] = []

    deny_patterns = (
        ctx.config.security.write_deny_list + ctx.config.security.write_deny_extras
    )
    denied_writes = check_write_deny(files, ctx.working_dir, deny_patterns)
    for rel in denied_writes:
        action = resolve_action("deny", phase=ctx.phase)
        findings.append(
            SecurityFinding(
                rule_id="write-deny-list",
                file=rel,
                match=rel,
                severity="critical",
                action_taken=action,
            )
        )

    scan_findings = scan_files(files, ctx.working_dir, DEFAULT_RULES)
    for f in scan_findings:
        override = ctx.config.security.supply_chain_rules.get(f.rule_id)
        base_action = override.action if override else f.action_taken
        f.action_taken = resolve_action(base_action, phase=ctx.phase)
        findings.append(f)

    has_deny = any(f.action_taken == "deny" for f in findings)
    has_warn = any(f.action_taken in ("warn", "log") for f in findings)
    if has_deny:
        action_taken = "denied"
    elif has_warn:
        action_taken = "flagged"
    else:
        action_taken = "clean"

    return SecurityResult(
        findings=findings,
        action_taken=action_taken,
        files_reverted=[],  # revert deferred to Phase 2
    )


def _cleanup(
    ctx: PreparedContext,
    invoke_result: InvokeResult | None,
) -> list[HookResult]:
    """Stage 5: run post-invoke hooks."""
    if invoke_result is None:
        return []
    hook_ctx = HookContext(
        provider=ctx.active_provider,
        task=ctx.task,
        working_dir=str(ctx.working_dir),
        phase="post-invoke",
    )
    chain = run_hooks(ctx.config.hooks.post_invoke, hook_ctx)
    return chain.results


def _record(
    ctx: PreparedContext,
    invoke_result: InvokeResult | None,
    policy_result: PolicyResult,
    security_result: SecurityResult | None,
    pre_hook_results: list[HookResult],
    post_hook_results: list[HookResult],
    status: RuntimeStatus,
) -> None:
    """Stage 6: append an enriched ActivityRecord to session JSONL."""
    sessions_dir = ctx.working_dir / ".harness" / "sessions"

    activity_status_map = {
        RuntimeStatus.SUCCESS: ActivityStatus.success,
        RuntimeStatus.DENIED: ActivityStatus.denied,
        RuntimeStatus.FLAGGED: ActivityStatus.flagged,
        RuntimeStatus.FAILED: ActivityStatus.failure,
        RuntimeStatus.TIMEOUT: ActivityStatus.timeout,
    }

    approval_status_map = {
        RuntimeStatus.SUCCESS: "approved",
        RuntimeStatus.DENIED: "denied",
        RuntimeStatus.FLAGGED: "flagged",
        RuntimeStatus.FAILED: "approved",
        RuntimeStatus.TIMEOUT: "approved",
    }

    record = ActivityRecord(
        session_id=ctx.session_id,
        type=ActivityType.invoke,
        provider=ctx.active_provider,
        task=ctx.task,
        working_dir=str(ctx.working_dir),
        duration=invoke_result.duration if invoke_result else 0.0,
        exit_code=invoke_result.exit_code if invoke_result else None,
        files_changed=invoke_result.files_changed if invoke_result else [],
        status=activity_status_map[status],
        hook_results=pre_hook_results + post_hook_results,
        risk_class=policy_result.effective_risk_class.value,
        policy_result=policy_result.model_dump(mode="json"),
        security_findings=[
            f.model_dump(mode="json")
            for f in (security_result.findings if security_result else [])
        ],
        approval_status=approval_status_map[status],
        caller=ctx.caller,
        runtime_status=status.value,
    )
    append_activity(sessions_dir, ctx.session_id, record)


def execute(
    task: str,
    working_dir: Path,
    provider: str | None = None,
    caller: str | None = None,
    requested_risk_class: RiskClass | None = None,
    timeout: int = 300,
) -> RuntimeResult:
    """Run the full 6-stage pipeline and return a RuntimeResult."""
    ctx = prepare(
        task=task,
        working_dir=working_dir,
        provider=provider,
        caller=caller,
        requested_risk_class=requested_risk_class,
    )

    policy, pre_hook_results = _policy_check(ctx)

    if not policy.allowed:
        _record(ctx, None, policy, None, pre_hook_results, [], RuntimeStatus.DENIED)
        return RuntimeResult(
            invoke_result=None,
            policy_result=policy,
            security_result=None,
            session_id=ctx.session_id,
            risk_class=policy.effective_risk_class,
            status=RuntimeStatus.DENIED,
        )

    invoke_result = invoke_provider(
        adapter_script=ctx.adapter_script,
        task=ctx.task,
        working_dir=ctx.working_dir,
        context=ctx.context_text,
        timeout=timeout,
    )

    if invoke_result.exit_code == 124:
        status = RuntimeStatus.TIMEOUT
        security_result = None
    elif invoke_result.exit_code != 0:
        status = RuntimeStatus.FAILED
        security_result = None
    else:
        security_result = _security_scan(ctx, invoke_result)
        if security_result.action_taken == "denied":
            status = RuntimeStatus.DENIED
        elif security_result.action_taken == "flagged":
            status = RuntimeStatus.FLAGGED
        else:
            status = RuntimeStatus.SUCCESS

    post_hook_results = _cleanup(ctx, invoke_result)

    _record(
        ctx,
        invoke_result,
        policy,
        security_result,
        pre_hook_results,
        post_hook_results,
        status,
    )

    return RuntimeResult(
        invoke_result=invoke_result,
        policy_result=policy,
        security_result=security_result,
        session_id=ctx.session_id,
        risk_class=policy.effective_risk_class,
        status=status,
    )
