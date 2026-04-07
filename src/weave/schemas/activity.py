"""Weave activity record schema — audit log for agent invocations."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ActivityType(str, Enum):
    invoke = "invoke"
    hook = "hook"
    system = "system"
    user = "user"


class ActivityStatus(str, Enum):
    success = "success"
    failure = "failure"
    timeout = "timeout"
    denied = "denied"


class HookResult(BaseModel):
    hook: str
    phase: str
    result: str
    message: str | None = None


class ActivityRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    type: ActivityType = ActivityType.invoke
    provider: str | None = None
    task: str | None = None
    working_dir: str | None = None
    duration: float | None = None
    exit_code: int | None = None
    files_changed: list[str] = Field(default_factory=list)
    status: ActivityStatus = ActivityStatus.success
    hook_results: list[HookResult] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
