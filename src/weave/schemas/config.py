"""Weave configuration schema."""
from __future__ import annotations

from pydantic import BaseModel, Field

from weave.schemas.policy import RiskClass, RuleOverride


class ProviderConfig(BaseModel):
    command: str
    enabled: bool = True
    capability_override: RiskClass | None = None


class HooksConfig(BaseModel):
    pre_invoke: list[str] = Field(default_factory=list)
    post_invoke: list[str] = Field(default_factory=list)
    pre_commit: list[str] = Field(default_factory=list)


class CompactionConfig(BaseModel):
    records_per_session: int = 50
    sessions_to_keep: int = 50


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
    write_allow_overrides: list[str] = Field(default_factory=list)


class SandboxConfig(BaseModel):
    strip_env_patterns: list[str] = Field(default_factory=lambda: [
        "AWS_*", "AZURE_*", "GCP_*", "GOOGLE_*",
        "GITHUB_TOKEN", "GITLAB_TOKEN", "NPM_TOKEN",
        "PYPI_TOKEN", "SSH_AUTH_SOCK", "GPG_*",
    ])
    safe_path_dirs: list[str] = Field(default_factory=lambda: [
        "/usr/bin", "/bin", "/usr/local/bin",
    ])
    extra_write_deny: list[str] = Field(default_factory=lambda: [
        ".git/hooks/*",
        "Makefile",
        "Dockerfile",
        "docker-compose*",
        "*.sh",
        ".github/workflows/*",
        "package.json",
        "pyproject.toml",
        "Cargo.toml",
    ])
    restrict_home: bool = True


class VolatileContextConfig(BaseModel):
    enabled: bool = True
    git_diff_enabled: bool = True
    git_diff_max_files: int = 30
    git_log_enabled: bool = True
    git_log_max_entries: int = 10
    activity_enabled: bool = True
    activity_max_records: int = 5
    max_total_chars: int = 8000


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
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    volatile_context: VolatileContextConfig = Field(default_factory=VolatileContextConfig)


def create_default_config(default_provider: str = "claude-code") -> WeaveConfig:
    """Create a WeaveConfig with sensible defaults."""
    return WeaveConfig(
        default_provider=default_provider,
        providers={
            "claude-code": ProviderConfig(
                command="claude",
                enabled=True,
                capability_override=None,
            ),
            "codex": ProviderConfig(
                command="codex",
                enabled=False,
                capability_override=None,
            ),
            "gemini": ProviderConfig(
                command="gemini",
                enabled=False,
                capability_override=None,
            ),
            "ollama": ProviderConfig(
                command="ollama",
                enabled=False,
                capability_override=None,
            ),
        },
    )
