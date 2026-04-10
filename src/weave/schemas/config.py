"""Weave configuration schema."""
from __future__ import annotations

from pydantic import BaseModel, Field

from weave.schemas.policy import RiskClass, RuleOverride


class ProviderConfig(BaseModel):
    command: str
    enabled: bool = True
    health_check: str | None = None
    capability: RiskClass = RiskClass.WORKSPACE_WRITE


class HooksConfig(BaseModel):
    pre_invoke: list[str] = Field(default_factory=list)
    post_invoke: list[str] = Field(default_factory=list)
    pre_commit: list[str] = Field(default_factory=list)


class CompactionConfig(BaseModel):
    keep_recent: int = 50
    archive_dir: str = ".harness/archive"


class SessionsConfig(BaseModel):
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)


class LoggingConfig(BaseModel):
    level: str = "info"
    format: str = "jsonl"


class ContextConfig(BaseModel):
    translate_to: list[str] = Field(
        default_factory=lambda: ["claude-code", "codex", "gemini", "ollama"]
    )


def _default_write_deny_list() -> list[str]:
    return [
        ".env",
        ".env.*",
        "*.pem",
        "*.key",
        "id_rsa*",
        "credentials.json",
        "config.json",
        ".harness/config.json",
        ".git/config",
    ]


class SecurityConfig(BaseModel):
    supply_chain_rules: dict[str, RuleOverride] = Field(default_factory=dict)
    write_deny_list: list[str] = Field(default_factory=_default_write_deny_list)
    write_deny_extras: list[str] = Field(default_factory=list)
    write_allow_overrides: list[str] = Field(default_factory=list)  # Phase 2: not yet enforced


class WeaveConfig(BaseModel):
    version: str = "1"
    phase: str = "sandbox"
    default_provider: str = "claude-code"
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)


def create_default_config(default_provider: str = "claude-code") -> WeaveConfig:
    """Create a WeaveConfig with sensible defaults."""
    return WeaveConfig(
        default_provider=default_provider,
        providers={
            "claude-code": ProviderConfig(
                command="claude",
                enabled=True,
                health_check="claude --version",
                capability=RiskClass.WORKSPACE_WRITE,
            ),
            "codex": ProviderConfig(
                command="codex",
                enabled=False,
                capability=RiskClass.WORKSPACE_WRITE,
            ),
            "gemini": ProviderConfig(
                command="gemini",
                enabled=False,
                capability=RiskClass.WORKSPACE_WRITE,
            ),
            "ollama": ProviderConfig(
                command="ollama",
                enabled=False,
                capability=RiskClass.READ_ONLY,
            ),
        },
    )
