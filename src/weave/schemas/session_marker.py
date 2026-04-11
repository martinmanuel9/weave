"""Session marker schema — start-time state for wrapped session-end calls."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SessionMarker(BaseModel):
    """Persisted start-time state for a wrapped session.

    Written by `weave session-start` and read by `weave session-end` to
    compute the cumulative files_changed for security scanning. Lives at
    `.harness/sessions/<session_id>.start_marker.json` next to the binding
    sidecar.

    The marker captures everything `session-end` needs to compute the diff
    without requiring the start and end commands to run in the same process.
    """
    session_id: str
    start_time: datetime
    git_available: bool
    start_head_sha: str | None
    pre_invoke_untracked: list[str] = Field(default_factory=list)
    task: str
    working_dir: str
