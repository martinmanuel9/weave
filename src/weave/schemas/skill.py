"""Skill registry schemas -- provider-agnostic routing recipes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class ProviderScore(BaseModel):
    """Per-provider performance metrics for a skill."""

    invocations: int = 0
    successes: int = 0
    avg_ms: int = 0
    score: float = 0.5


class SkillMetrics(BaseModel):
    """Aggregated metrics across all providers for a skill."""

    invocations: int = 0
    successes: int = 0
    failures: int = 0
    avg_duration_ms: int = 0
    by_provider: dict[str, ProviderScore] = Field(default_factory=dict)


class HealingLogEntry(BaseModel):
    """Record of a single self-healing event."""

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    trigger: str
    action: str
    outcome: str
    duration_ms: int = 0


class SkillStrategy(BaseModel):
    """Routing and recovery strategy for a skill."""

    primary_provider: str
    fallback_providers: list[str] = Field(default_factory=list)
    context_injection: str = ""
    timeout_ms: int = 30000
    max_retries: int = 2


class SkillDefinition(BaseModel):
    """A Weave-native skill -- a routing recipe for an intent."""

    name: str
    version: str = "1"
    description: str = ""
    intents: list[str] = Field(default_factory=list)
    strategy: SkillStrategy
    metrics: SkillMetrics = Field(default_factory=SkillMetrics)
    healing_log: list[HealingLogEntry] = Field(default_factory=list)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
