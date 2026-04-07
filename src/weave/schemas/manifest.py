"""Weave manifest schema — unit of work definition."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class UnitType(str, Enum):
    project = "project"
    workflow = "workflow"
    task = "task"


class UnitStatus(str, Enum):
    pending = "pending"
    active = "active"
    paused = "paused"
    completed = "completed"
    failed = "failed"


class Phase(str, Enum):
    sandbox = "sandbox"
    mvp = "mvp"
    enterprise = "enterprise"


class Manifest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: UnitType = UnitType.project
    name: str
    status: UnitStatus = UnitStatus.pending
    phase: Phase = Phase.sandbox
    parent: str | None = None
    children: list[str] = Field(default_factory=list)
    provider: str | None = None
    agent: str | None = None
    created: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


def create_manifest(
    name: str,
    unit_type: UnitType = UnitType.project,
    phase: Phase = Phase.sandbox,
    provider: str | None = None,
) -> Manifest:
    """Create a new Manifest with sensible defaults."""
    return Manifest(
        name=name,
        type=unit_type,
        phase=phase,
        provider=provider,
    )
