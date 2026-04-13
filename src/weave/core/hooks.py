"""Hook chain execution — supports external bash scripts and Python callables."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Callable

from weave.schemas.activity import HookResult


@dataclass
class HookContext:
    provider: str
    task: str
    working_dir: str
    phase: str  # "pre-invoke", "post-invoke", or "post-scan"

    # Enriched fields (REQ-1) — None/[] when not yet available
    risk_class: str | None = None
    session_id: str | None = None
    provider_contract: str | None = None
    files_changed: list[str] = field(default_factory=list)
    exit_code: int | None = None
    security_findings: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "task": self.task,
            "working_dir": self.working_dir,
            "phase": self.phase,
            "risk_class": self.risk_class,
            "session_id": self.session_id,
            "provider_contract": self.provider_contract,
            "files_changed": self.files_changed,
            "exit_code": self.exit_code,
            "security_findings": self.security_findings,
        }


@dataclass
class HookChainResult:
    allowed: bool
    results: list[HookResult] = field(default_factory=list)


def _execute_script_hook(hook_path: str, context: HookContext) -> HookResult:
    """Execute a bash script hook, passing JSON context via stdin."""
    try:
        proc = subprocess.run(
            [hook_path],
            input=json.dumps(context.to_dict()),
            capture_output=True,
            text=True,
            timeout=30,
        )
        allowed = proc.returncode == 0
        message = proc.stderr.strip() if not allowed and proc.stderr.strip() else None
        return HookResult(
            hook=hook_path,
            phase=context.phase,
            result="allow" if allowed else "deny",
            message=message,
        )
    except subprocess.TimeoutExpired:
        return HookResult(
            hook=hook_path,
            phase=context.phase,
            result="deny",
            message="Hook timed out after 30 seconds",
        )
    except Exception as exc:
        return HookResult(
            hook=hook_path,
            phase=context.phase,
            result="deny",
            message=str(exc),
        )


def run_hooks(
    hook_paths: list[str],
    context: HookContext,
    callables: list[Callable] | None = None,
) -> HookChainResult:
    """Run script hooks then Python callables sequentially, fail-fast on deny."""
    results: list[HookResult] = []

    # Script hooks first
    for hook_path in hook_paths:
        result = _execute_script_hook(hook_path, context)
        results.append(result)
        if result.result == "deny":
            return HookChainResult(allowed=False, results=results)

    # Python callables next
    for fn in (callables or []):
        try:
            allowed = bool(fn(context))
        except Exception as exc:
            result = HookResult(
                hook=getattr(fn, "__name__", repr(fn)),
                phase=context.phase,
                result="deny",
                message=str(exc),
            )
            results.append(result)
            return HookChainResult(allowed=False, results=results)

        result = HookResult(
            hook=getattr(fn, "__name__", repr(fn)),
            phase=context.phase,
            result="allow" if allowed else "deny",
            message=None,
        )
        results.append(result)
        if not allowed:
            return HookChainResult(allowed=False, results=results)

    return HookChainResult(allowed=True, results=results)
