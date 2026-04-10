"""Weave policy and security schemas."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RiskClass(str, Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    EXTERNAL_NETWORK = "external-network"
    DESTRUCTIVE = "destructive"


_RISK_LEVELS = {
    RiskClass.READ_ONLY: 0,
    RiskClass.WORKSPACE_WRITE: 1,
    RiskClass.EXTERNAL_NETWORK: 2,
    RiskClass.DESTRUCTIVE: 3,
}


def risk_class_level(rc: RiskClass) -> int:
    """Return ordinal level for a risk class (lower = safer)."""
    return _RISK_LEVELS[rc]


class RuntimeStatus(str, Enum):
    SUCCESS = "success"
    DENIED = "denied"
    FLAGGED = "flagged"
    FAILED = "failed"
    TIMEOUT = "timeout"


class HookResultRef(BaseModel):
    """Minimal hook result for embedding in PolicyResult."""
    hook: str
    phase: str
    result: str
    message: str | None = None


class PolicyResult(BaseModel):
    allowed: bool
    effective_risk_class: RiskClass
    provider_ceiling: RiskClass
    requested_class: RiskClass | None = None
    warnings: list[str] = Field(default_factory=list)
    denials: list[str] = Field(default_factory=list)
    hook_results: list[HookResultRef] = Field(default_factory=list)


class SecurityFinding(BaseModel):
    rule_id: str
    file: str
    match: str
    severity: str  # critical | high | medium
    action_taken: str  # deny | warn | log


class SecurityResult(BaseModel):
    findings: list[SecurityFinding] = Field(default_factory=list)
    action_taken: str = "clean"  # clean | flagged | denied
    files_reverted: list[str] = Field(default_factory=list)


class SecurityRule(BaseModel):
    id: str
    description: str
    pattern: str  # regex
    file_glob: str = "**/*"
    severity: str  # critical | high | medium
    default_action: str  # deny | warn | log


class RuleOverride(BaseModel):
    action: str  # deny | warn | log
