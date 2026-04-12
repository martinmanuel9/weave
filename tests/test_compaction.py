"""Tests for weave transcript compaction."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from weave.schemas.activity import ActivityRecord, ActivityStatus, ActivityType


def _make_record(
    provider: str = "claude-code",
    task: str = "do something",
    duration: float = 100.0,
    status: ActivityStatus = ActivityStatus.success,
    files_changed: list[str] | None = None,
    session_id: str = "test-session",
    timestamp: datetime | None = None,
    record_type: ActivityType = ActivityType.invoke,
    metadata: dict | None = None,
) -> ActivityRecord:
    return ActivityRecord(
        session_id=session_id,
        type=record_type,
        provider=provider,
        task=task,
        duration=duration,
        status=status,
        files_changed=files_changed or [],
        timestamp=timestamp or datetime.now(timezone.utc),
        metadata=metadata or {},
    )


def test_build_compaction_summary_basic_stats():
    from weave.core.compaction import _build_compaction_summary

    records = [
        _make_record(
            provider="claude-code",
            duration=100.0,
            status=ActivityStatus.success,
            files_changed=["a.py", "b.py"],
            timestamp=datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc),
        ),
        _make_record(
            provider="ollama",
            duration=200.0,
            status=ActivityStatus.denied,
            files_changed=["b.py", "c.py"],
            timestamp=datetime(2026, 4, 11, 11, 0, tzinfo=timezone.utc),
        ),
        _make_record(
            provider="claude-code",
            duration=50.0,
            status=ActivityStatus.success,
            files_changed=[],
            timestamp=datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc),
        ),
    ]
    summary = _build_compaction_summary(records)
    assert summary.type == ActivityType.system
    assert summary.task == "compaction_summary"
    meta = summary.metadata
    assert meta["compacted_count"] == 3
    assert meta["total_duration_ms"] == 350.0
    assert meta["status_counts"] == {"success": 2, "denied": 1}
    assert set(meta["providers_used"]) == {"claude-code", "ollama"}
    assert meta["earliest_timestamp"] == "2026-04-11T10:00:00+00:00"
    assert meta["latest_timestamp"] == "2026-04-11T12:00:00+00:00"
    assert meta["total_files_changed"] == 4
    assert set(meta["unique_files_changed"]) == {"a.py", "b.py", "c.py"}


def test_build_compaction_summary_merges_prior_summary():
    from weave.core.compaction import _build_compaction_summary

    prior_summary = _make_record(
        record_type=ActivityType.system,
        task="compaction_summary",
        metadata={
            "compacted_count": 10,
            "earliest_timestamp": "2026-04-10T08:00:00+00:00",
            "latest_timestamp": "2026-04-10T20:00:00+00:00",
            "total_duration_ms": 5000.0,
            "providers_used": ["gemini"],
            "status_counts": {"success": 8, "failed": 2},
            "total_files_changed": 30,
            "unique_files_changed": ["old.py"],
        },
    )
    new_record = _make_record(
        provider="claude-code",
        duration=100.0,
        status=ActivityStatus.success,
        files_changed=["new.py"],
        timestamp=datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc),
    )
    summary = _build_compaction_summary([prior_summary, new_record])
    meta = summary.metadata
    assert meta["compacted_count"] == 11
    assert meta["total_duration_ms"] == 5100.0
    assert meta["status_counts"] == {"success": 9, "failed": 2}
    assert set(meta["providers_used"]) == {"gemini", "claude-code"}
    assert meta["earliest_timestamp"] == "2026-04-10T08:00:00+00:00"
    assert meta["latest_timestamp"] == "2026-04-11T10:00:00+00:00"
    assert meta["total_files_changed"] == 31
    assert set(meta["unique_files_changed"]) == {"old.py", "new.py"}


def test_build_compaction_summary_empty_list():
    from weave.core.compaction import _build_compaction_summary

    summary = _build_compaction_summary([])
    meta = summary.metadata
    assert meta["compacted_count"] == 0
    assert meta["total_duration_ms"] == 0.0
