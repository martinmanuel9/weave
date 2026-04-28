"""Feedback ledger schemas -- invocation outcomes and routing scores."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class HealingDetail(BaseModel):
    """Details of a healing attempt within a feedback record."""

    used: bool = False
    attempts: int = 0
    original_failure: str = ""
    fallbacks: list[dict[str, Any]] = Field(default_factory=list)


class FeedbackRecord(BaseModel):
    """One invocation outcome in the feedback ledger."""

    id: str = Field(default_factory=lambda: f"fb_{uuid.uuid4().hex[:12]}")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    session_id: str = ""
    intent: str
    intent_confidence: float = 0.0
    intent_source: str = ""
    provider: str
    routing_source: str = "static"
    skill_used: str = ""
    task_preview: str = ""
    outcome: str  # "success" | "failure" | "healed" | "timeout"
    duration_ms: int
    quality_gate: str = ""
    security_findings: list[str] = Field(default_factory=list)
    healing: HealingDetail = Field(default_factory=HealingDetail)
    context_injection: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0


class RoutingScores(BaseModel):
    """Aggregated intent -> provider -> score mapping."""

    scores: dict[str, dict[str, float]] = Field(default_factory=dict)
    last_updated: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
