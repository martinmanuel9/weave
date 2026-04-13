"""Weave runtime — governed execution pipeline.

Pipeline: prepare -> policy_check -> invoke -> security_scan -> cleanup -> revert -> record.
Single entrypoint for all agent invocations, whether from the CLI, itzel,
or GSD.
"""
from __future__ import annotations

import fnmatch as _fnmatch
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from weave.core.config import resolve_config
from weave.core.context import assemble_context
from weave.core.hooks import HookContext, run_hooks
from weave.core.invoker import InvokeResult, invoke_provider
from weave.core.policy import evaluate_policy
from weave.core.registry import get_registry
from weave.core.security import DEFAULT_RULES, check_write_deny, resolve_action, scan_files
from weave.core.session import append_activity, create_session
from weave.core.session_binding import compute_binding, read_binding, validate_session, write_binding
from weave.schemas.activity import ActivityRecord, ActivityStatus, ActivityType, HookResult
from weave.schemas.config import ProviderConfig, WeaveConfig
from weave.schemas.context import ContextAssembly
from weave.schemas.policy import (
    HookResultRef,
    PolicyResult,
    RiskClass,
    RuntimeStatus,
    SecurityFinding,
    SecurityResult,
)
from weave.schemas.provider_contract import ProviderContract


_logger = logging.getLogger(__name__)

_PRESERVED_ENV_KEYS = frozenset({
    "PYTHONPATH", "LANG", "LC_ALL", "TERM", "USER", "LOGNAME", "SHELL",
})


def _build_sandbox_env(
    config: WeaveConfig,
    provider_binary_dir: str | None = None,
) -> dict[str, str]:
    """Build a sanitized environment dict for sandbox-phase adapter invocations.

    Strips env vars matching config.sandbox.strip_env_patterns, restricts
    PATH to config.sandbox.safe_path_dirs + provider_binary_dir, and removes
    HOME if config.sandbox.restrict_home is True (caller sets HOME to tmpdir).
    """
    import os as _os

    env = dict(_os.environ)
    patterns = config.sandbox.strip_env_patterns

    keys_to_remove = []
    for key in env:
        if key in _PRESERVED_ENV_KEYS:
            continue
        if key == "PATH":
            continue  # handled separately
        if key == "HOME" and config.sandbox.restrict_home:
            keys_to_remove.append(key)
            continue
        for pattern in patterns:
            if _fnmatch.fnmatch(key, pattern):
                keys_to_remove.append(key)
                break

    for key in keys_to_remove:
        del env[key]

    # Rebuild PATH
    path_dirs = list(config.sandbox.safe_path_dirs)
    if provider_binary_dir:
        path_dirs.insert(0, provider_binary_dir)
    env["PATH"] = ":".join(path_dirs)

    return env


def ensure_harness(working_dir: Path, name: str | None = None) -> bool:
    """Ensure .harness/ exists in working_dir. Scaffolds if missing.

    Returns True if a new harness was created, False if one already existed.
    """
    from weave.core.scaffold import scaffold_project

    harness = working_dir / ".harness"
    if harness.exists():
        return False

    project_name = name or working_dir.name
    scaffold_project(
        working_dir,
        name=project_name,
        default_provider="claude-code",
        phase="sandbox",
    )
    return True


@dataclass
class PreparedContext:
    """Everything the pipeline needs after the prepare stage."""
    config: WeaveConfig
    active_provider: str
    provider_config: ProviderConfig
    provider_contract: ProviderContract
    adapter_script: Path
    context: ContextAssembly
    session_id: str
    working_dir: Path
    phase: str
    task: str
    caller: str | None
    requested_risk_class: RiskClass | None
    pre_invoke_untracked: set[str]
    metadata: dict = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class RuntimeResult:
    invoke_result: InvokeResult | None
    policy_result: PolicyResult
    security_result: SecurityResult | None
    session_id: str
    risk_class: RiskClass
    status: RuntimeStatus


def _snapshot_untracked(working_dir: Path) -> set[str]:
    """Return the set of untracked files in working_dir via git.

    Returns an empty set if the directory is not a git repo or git fails.
    Used by prepare() to capture state before invoke runs, so that _revert
    can distinguish pre-existing untracked files (preserve) from files
    created by the invocation (delete).
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return set()
        return {line for line in result.stdout.splitlines() if line}
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return set()



def _validate_and_rebind(ctx, sessions_dir, policy):
    """Validate an existing session binding and apply the binding policy."""
    from weave.schemas.config import SessionBindingPolicy

    existing = read_binding(ctx.session_id, sessions_dir)
    if existing is None:
        binding = compute_binding(ctx)
        write_binding(binding, sessions_dir)
        return

    mismatches = validate_session(ctx.session_id, ctx, sessions_dir)
    if not mismatches:
        return

    mismatch_str = ", ".join(mismatches)

    if policy == SessionBindingPolicy.STRICT:
        raise ValueError(
            f"Session {ctx.session_id} binding has drifted: {mismatch_str}. "
            f"Binding policy is 'strict' — refusing to proceed."
        )
    elif policy == SessionBindingPolicy.WARN:
        _logger.warning(
            "Session %s binding drifted on: %s — rebinding (policy=warn)",
            ctx.session_id, mismatch_str,
        )
    else:
        _logger.info(
            "Session %s binding drifted on: %s — rebinding (policy=rebind)",
            ctx.session_id, mismatch_str,
        )

    binding = compute_binding(ctx)
    write_binding(binding, sessions_dir)


def prepare(
    task: str,
    working_dir: Path,
    provider: str | None = None,
    caller: str | None = None,
    requested_risk_class: RiskClass | None = None,
    session_id: str | None = None,
    metadata: dict | None = None,
) -> PreparedContext:
    """Stage 1: load config, resolve provider, assemble context, create session."""
    config = resolve_config(working_dir)
    active_provider = provider or config.default_provider

    provider_config = config.providers.get(active_provider)
    if provider_config is None:
        raise ValueError(f"Provider '{active_provider}' not configured")

    # Resolve contract from the registry
    registry = get_registry()
    registry.load(working_dir)
    if not registry.has(active_provider):
        known = ", ".join(sorted(c.name for c in registry.list()))
        raise RuntimeError(
            f"unknown provider: {active_provider!r}. Known providers: {known}"
        )
    contract = registry.get(active_provider)
    adapter_script = registry.resolve_adapter_path(active_provider)

    context = assemble_context(working_dir)
    is_reuse = session_id is not None
    if not is_reuse:
        session_id = create_session()
    from weave.core.volatile import build_volatile_context
    volatile_text = build_volatile_context(
        working_dir=working_dir,
        config=config.volatile_context,
        session_id=session_id,
    )
    context = context.with_volatile(volatile_text)
    pre_invoke_untracked = _snapshot_untracked(working_dir)

    prepared = PreparedContext(
        config=config,
        active_provider=active_provider,
        provider_config=provider_config,
        provider_contract=contract,
        adapter_script=adapter_script,
        context=context,
        session_id=session_id,
        working_dir=working_dir,
        phase=config.phase,
        task=task,
        caller=caller,
        requested_risk_class=requested_risk_class,
        pre_invoke_untracked=pre_invoke_untracked,
        metadata=metadata or {},
    )

    # Session binding: validate if reusing, write if new
    sessions_dir = working_dir / ".harness" / "sessions"
    if is_reuse:
        _validate_and_rebind(prepared, sessions_dir, config.sessions.binding_policy)
    else:
        binding = compute_binding(prepared)
        write_binding(binding, sessions_dir)

    return prepared


def _policy_check(ctx: PreparedContext) -> tuple[PolicyResult, list[HookResult]]:
    """Stage 2: evaluate policy and run pre-invoke hooks."""
    policy = evaluate_policy(
        contract=ctx.provider_contract,
        provider_config=ctx.provider_config,
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
        risk_class=policy.effective_risk_class.value,
        session_id=ctx.session_id,
        provider_contract=ctx.active_provider,
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
    if ctx.phase == "sandbox":
        deny_patterns = deny_patterns + ctx.config.sandbox.extra_write_deny
    denied_writes = check_write_deny(
        files,
        ctx.working_dir,
        deny_patterns,
        allow_patterns=ctx.config.security.write_allow_overrides,
    )
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

    scan_findings = scan_files(
        files, ctx.working_dir, DEFAULT_RULES,
        allowlist=ctx.config.security.scanner_allowlist,
    )
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
        files_reverted=[],  # populated by _revert stage if action_taken == "denied"
    )


def _post_scan_gate(
    ctx: PreparedContext,
    invoke_result: InvokeResult,
    security_result: SecurityResult,
    policy: PolicyResult,
) -> tuple[list[HookResult], bool]:
    """Stage 4b: run post-scan hooks. Returns (results, denied)."""
    if not ctx.config.hooks.post_scan:
        return [], False

    hook_ctx = HookContext(
        provider=ctx.active_provider,
        task=ctx.task,
        working_dir=str(ctx.working_dir),
        phase="post-scan",
        risk_class=policy.effective_risk_class.value,
        session_id=ctx.session_id,
        provider_contract=ctx.active_provider,
        files_changed=invoke_result.files_changed,
        exit_code=invoke_result.exit_code,
        security_findings=[
            f.model_dump(mode="json") for f in security_result.findings
        ],
    )
    chain = run_hooks(ctx.config.hooks.post_scan, hook_ctx)
    return chain.results, not chain.allowed


def _cleanup(
    ctx: PreparedContext,
    invoke_result: InvokeResult | None,
    security_result: SecurityResult | None = None,
) -> list[HookResult]:
    """Stage 5: run post-invoke hooks."""
    if invoke_result is None:
        return []
    hook_ctx = HookContext(
        provider=ctx.active_provider,
        task=ctx.task,
        working_dir=str(ctx.working_dir),
        phase="post-invoke",
        risk_class=ctx.requested_risk_class.value if ctx.requested_risk_class else None,
        session_id=ctx.session_id,
        provider_contract=ctx.active_provider,
        files_changed=invoke_result.files_changed,
        exit_code=invoke_result.exit_code,
        security_findings=[
            f.model_dump(mode="json")
            for f in (security_result.findings if security_result else [])
        ],
    )
    chain = run_hooks(ctx.config.hooks.post_invoke, hook_ctx)
    return chain.results


def _revert(
    ctx: PreparedContext,
    invoke_result: InvokeResult | None,
    security_result: SecurityResult | None,
) -> None:
    """Stage 6: if security denied, revert all files_changed from the invocation.

    Per-file classification:
      - path escapes working_dir -> skip (never mutate outside working_dir)
      - tracked at HEAD -> git checkout HEAD -- <file>
      - not tracked AND in ctx.pre_invoke_untracked -> skip (pre-existing user work)
      - not tracked AND NOT in snapshot -> rm <file> (created by invocation)

    Best-effort: individual file failures are logged and skipped. Populates
    security_result.files_reverted in place with the list of successfully
    reverted relative paths.

    No-op when:
      - invoke_result is None (invoke never ran or failed)
      - security_result is None (scan was skipped due to non-zero exit)
      - security_result.action_taken != "denied"
    """
    if invoke_result is None or security_result is None:
        return
    if security_result.action_taken != "denied":
        return

    working_dir = ctx.working_dir
    working_dir_resolved = working_dir.resolve()
    reverted: list[str] = []

    for rel in invoke_result.files_changed:
        # Skip path-escape attempts
        try:
            abs_path = (working_dir / rel).resolve()
            abs_path.relative_to(working_dir_resolved)
        except ValueError:
            continue

        # Classify: tracked at HEAD?
        try:
            tracked = subprocess.run(
                ["git", "cat-file", "-e", f"HEAD:{rel}"],
                cwd=working_dir,
                capture_output=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            # git unavailable or timed out — cannot revert this file
            continue

        if tracked.returncode == 0:
            # Tracked at HEAD: restore content
            try:
                subprocess.run(
                    ["git", "checkout", "HEAD", "--", rel],
                    cwd=working_dir,
                    capture_output=True,
                    timeout=10,
                    check=True,
                )
                reverted.append(rel)
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
                continue
        else:
            # Not tracked at HEAD
            if rel in ctx.pre_invoke_untracked:
                # Pre-existing untracked user work — preserve
                continue
            # New file created by this invocation — delete
            try:
                abs_path.unlink()
                reverted.append(rel)
            except (FileNotFoundError, PermissionError, OSError):
                continue

    security_result.files_reverted = reverted


def _record(
    ctx: PreparedContext,
    invoke_result: InvokeResult | None,
    policy_result: PolicyResult,
    security_result: SecurityResult | None,
    pre_hook_results: list[HookResult],
    post_hook_results: list[HookResult],
    status: RuntimeStatus,
    metadata: dict | None = None,
) -> None:
    """Stage 7: append an enriched ActivityRecord to session JSONL."""
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
        metadata=metadata or {},
    )
    compact_threshold = ctx.config.sessions.compaction.records_per_session
    append_activity(sessions_dir, ctx.session_id, record, compact_threshold=compact_threshold)


def execute(
    task: str,
    working_dir: Path,
    provider: str | None = None,
    caller: str | None = None,
    requested_risk_class: RiskClass | None = None,
    timeout: int = 300,
    session_id: str | None = None,
    metadata: dict | None = None,
) -> RuntimeResult:
    """Run the full 7-stage pipeline and return a RuntimeResult."""
    ctx = prepare(
        task=task,
        working_dir=working_dir,
        provider=provider,
        caller=caller,
        requested_risk_class=requested_risk_class,
        session_id=session_id,
        metadata=metadata,
    )

    policy, pre_hook_results = _policy_check(ctx)

    if not policy.allowed:
        _record(ctx, None, policy, None, pre_hook_results, [], RuntimeStatus.DENIED, metadata=ctx.metadata)
        return RuntimeResult(
            invoke_result=None,
            policy_result=policy,
            security_result=None,
            session_id=ctx.session_id,
            risk_class=policy.effective_risk_class,
            status=RuntimeStatus.DENIED,
        )

    # Build sandbox environment if in sandbox phase
    sandbox_env = None
    sandbox_tmpdir = None
    if ctx.phase == "sandbox":
        import tempfile
        sandbox_tmpdir = Path(tempfile.mkdtemp(prefix="weave-sandbox-"))
        provider_bin_dir = str(ctx.adapter_script.parent)
        sandbox_env = _build_sandbox_env(ctx.config, provider_bin_dir)
        if ctx.config.sandbox.restrict_home:
            sandbox_env["HOME"] = str(sandbox_tmpdir)
            sandbox_env["XDG_CONFIG_HOME"] = str(sandbox_tmpdir / ".config")
            sandbox_env["XDG_DATA_HOME"] = str(sandbox_tmpdir / ".local" / "share")

    try:
        invoke_result = invoke_provider(
            contract=ctx.provider_contract,
            task=ctx.task,
            session_id=ctx.session_id,
            working_dir=ctx.working_dir,
            context=ctx.context.full,
            timeout=timeout,
            registry=get_registry(),
            env=sandbox_env,
        )

        post_scan_results: list[HookResult] = []

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

            # Post-scan quality gate (REQ-3) — only on successful invocation
            if status in (RuntimeStatus.SUCCESS, RuntimeStatus.FLAGGED):
                post_scan_results, post_scan_denied = _post_scan_gate(
                    ctx, invoke_result, security_result, policy,
                )
                if post_scan_denied:
                    status = RuntimeStatus.DENIED
                    security_result.action_taken = "denied"

        post_hook_results = _cleanup(ctx, invoke_result, security_result)

        _revert(ctx, invoke_result, security_result)

        all_post_hooks = post_scan_results + post_hook_results
        _record(
            ctx,
            invoke_result,
            policy,
            security_result,
            pre_hook_results,
            all_post_hooks,
            status,
            metadata=ctx.metadata,
        )
    finally:
        if sandbox_tmpdir and sandbox_tmpdir.exists():
            import shutil
            shutil.rmtree(sandbox_tmpdir, ignore_errors=True)

    return RuntimeResult(
        invoke_result=invoke_result,
        policy_result=policy,
        security_result=security_result,
        session_id=ctx.session_id,
        risk_class=policy.effective_risk_class,
        status=status,
    )
