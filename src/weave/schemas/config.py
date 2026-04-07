"""Weave configuration schema."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    command: str
    enabled: bool = True
    health_check: str | None = None


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


class WeaveConfig(BaseModel):
    version: str = "1"
    phase: str = "sandbox"
    default_provider: str = "claude-code"
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)


def create_default_config(default_provider: str = "claude-code") -> WeaveConfig:
    """Create a WeaveConfig with sensible defaults."""
    return WeaveConfig(
        default_provider=default_provider,
        providers={
            "claude-code": ProviderConfig(
                command="claude",
                enabled=True,
                health_check="claude --version",
            ),
            "codex": ProviderConfig(
                command="codex",
                enabled=False,
            ),
            "gemini": ProviderConfig(
                command="gemini",
                enabled=False,
            ),
            "ollama": ProviderConfig(
                command="ollama",
                enabled=False,
            ),
        },
    )
