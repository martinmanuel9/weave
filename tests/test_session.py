"""Tests for weave.core.session — JSONL session logging."""
from __future__ import annotations

from pathlib import Path

import pytest

from weave.core.session import append_activity, create_session, read_session_activities
from weave.schemas.activity import ActivityRecord, ActivityStatus, ActivityType


def test_create_session() -> None:
    session_id = create_session()
    assert isinstance(session_id, str)
    # UUID4 string is 36 chars: 8-4-4-4-12 + 4 dashes
    assert len(session_id) == 36
    assert session_id.count("-") == 4


def test_append_and_read(tmp_path: Path) -> None:
    session_id = create_session()
    record = ActivityRecord(
        session_id=session_id,
        provider="claude",
        task="write unit tests",
        working_dir=str(tmp_path),
        type=ActivityType.invoke,
        status=ActivityStatus.success,
    )
    append_activity(tmp_path, session_id, record)
    records = read_session_activities(tmp_path, session_id)
    assert len(records) == 1
    assert records[0].provider == "claude"
    assert records[0].task == "write unit tests"
    assert records[0].session_id == session_id


def test_multiple_records(tmp_path: Path) -> None:
    session_id = create_session()
    tasks = ["task one", "task two", "task three"]
    for t in tasks:
        record = ActivityRecord(
            session_id=session_id,
            provider="gemini",
            task=t,
            working_dir=str(tmp_path),
        )
        append_activity(tmp_path, session_id, record)

    records = read_session_activities(tmp_path, session_id)
    assert len(records) == 3
    for i, record in enumerate(records):
        assert record.task == tasks[i]
        assert record.provider == "gemini"
