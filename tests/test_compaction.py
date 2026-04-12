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


def _write_records(sessions_dir: Path, session_id: str, records: list[ActivityRecord]) -> Path:
    """Helper: write a list of ActivityRecords as JSONL lines."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    log_file = sessions_dir / f"{session_id}.jsonl"
    with log_file.open("w") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")
    return log_file


def _read_lines(log_file: Path) -> list[str]:
    return [line for line in log_file.read_text().splitlines() if line.strip()]


def test_compact_noop_below_threshold(tmp_path):
    from weave.core.compaction import _maybe_compact_session

    sessions_dir = tmp_path / "sessions"
    records = [_make_record(session_id="s1") for _ in range(10)]
    log_file = _write_records(sessions_dir, "s1", records)
    original_content = log_file.read_text()

    _maybe_compact_session(sessions_dir, "s1", keep_recent=50)
    assert log_file.read_text() == original_content


def test_compact_rewrites_at_threshold(tmp_path):
    from weave.core.compaction import _maybe_compact_session

    sessions_dir = tmp_path / "sessions"
    records = [_make_record(session_id="s1", duration=float(i)) for i in range(60)]
    _write_records(sessions_dir, "s1", records)

    _maybe_compact_session(sessions_dir, "s1", keep_recent=50)
    lines = _read_lines(sessions_dir / "s1.jsonl")
    assert len(lines) == 51  # 1 summary + 50 recent
    first = json.loads(lines[0])
    assert first["task"] == "compaction_summary"
    assert first["metadata"]["compacted_count"] == 10


def test_compact_atomic_rewrite(tmp_path):
    from weave.core.compaction import _maybe_compact_session

    sessions_dir = tmp_path / "sessions"
    records = [_make_record(session_id="s1") for _ in range(60)]
    _write_records(sessions_dir, "s1", records)

    _maybe_compact_session(sessions_dir, "s1", keep_recent=50)
    assert not (sessions_dir / "s1.jsonl.tmp").exists()


def test_compact_skips_corrupt_lines(tmp_path):
    from weave.core.compaction import _maybe_compact_session

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    log_file = sessions_dir / "s1.jsonl"
    good_records = [_make_record(session_id="s1") for _ in range(5)]
    with log_file.open("w") as f:
        for r in good_records:
            f.write(r.model_dump_json() + "\n")
        f.write("this is not valid json\n")
        for _ in range(5):
            f.write(_make_record(session_id="s1").model_dump_json() + "\n")

    _maybe_compact_session(sessions_dir, "s1", keep_recent=5)
    lines = _read_lines(log_file)
    assert len(lines) == 6  # 1 summary + 5 recent
    summary = json.loads(lines[0])
    assert summary["metadata"]["compacted_count"] == 5 + 1  # 5 good + 1 corrupt counted


def test_compact_noop_for_empty_file(tmp_path):
    from weave.core.compaction import _maybe_compact_session

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "s1.jsonl").write_text("")

    _maybe_compact_session(sessions_dir, "s1", keep_recent=50)
    assert (sessions_dir / "s1.jsonl").read_text() == ""


def test_append_activity_with_compact_threshold(tmp_path):
    from weave.core.session import append_activity

    sessions_dir = tmp_path / "sessions"
    for i in range(6):
        append_activity(
            sessions_dir, "s1",
            _make_record(session_id="s1", duration=float(i)),
            compact_threshold=5,
        )
    lines = _read_lines(sessions_dir / "s1.jsonl")
    assert len(lines) == 6  # 1 summary + 5 recent
    first = json.loads(lines[0])
    assert first["task"] == "compaction_summary"
    assert first["metadata"]["compacted_count"] == 1


import os
import time


def _create_session_files(
    sessions_dir: Path,
    session_id: str,
    records: list[ActivityRecord] | None = None,
    with_binding: bool = True,
    with_marker: bool = True,
    mtime_offset: float = 0.0,
) -> None:
    """Create a full set of session files (JSONL + optional sidecars)."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    recs = records or [_make_record(session_id=session_id)]
    log_file = sessions_dir / f"{session_id}.jsonl"
    with log_file.open("w") as f:
        for r in recs:
            f.write(r.model_dump_json() + "\n")
    if with_binding:
        (sessions_dir / f"{session_id}.binding.json").write_text('{"binding": true}')
    if with_marker:
        (sessions_dir / f"{session_id}.start_marker.json").write_text('{"marker": true}')
    if mtime_offset != 0.0:
        now = time.time()
        target = now + mtime_offset
        os.utime(log_file, (target, target))


def test_lifecycle_noop_when_under_retention(tmp_path):
    from weave.core.compaction import compact_sessions

    sessions_dir = tmp_path / "sessions"
    for i in range(3):
        _create_session_files(sessions_dir, f"sess-{i}")
    result = compact_sessions(sessions_dir, sessions_to_keep=50)
    assert result.kept == 3
    assert result.removed == 0


def test_lifecycle_removes_oldest_sessions(tmp_path):
    from weave.core.compaction import compact_sessions

    sessions_dir = tmp_path / "sessions"
    for i in range(5):
        _create_session_files(sessions_dir, f"sess-{i}", mtime_offset=-500 + i * 100)

    result = compact_sessions(sessions_dir, sessions_to_keep=2)
    assert result.kept == 2
    assert result.removed == 3
    remaining = sorted(p.stem for p in sessions_dir.glob("*.jsonl") if p.name != "session_history.jsonl")
    assert len(remaining) == 2


def test_lifecycle_writes_ledger_entry(tmp_path):
    from weave.core.compaction import compact_sessions

    sessions_dir = tmp_path / "sessions"
    _create_session_files(
        sessions_dir, "old-sess",
        records=[
            _make_record(
                session_id="old-sess",
                provider="claude-code",
                task="build the thing",
                duration=1000.0,
                status=ActivityStatus.success,
                files_changed=["a.py"],
                timestamp=datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc),
            ),
        ],
        mtime_offset=-1000,
    )
    _create_session_files(sessions_dir, "new-sess", mtime_offset=0)

    compact_sessions(sessions_dir, sessions_to_keep=1)

    ledger = sessions_dir / "session_history.jsonl"
    assert ledger.exists()
    entries = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["session_id"] == "old-sess"
    assert entry["provider"] == "claude-code"
    assert entry["invocation_count"] == 1
    assert entry["total_duration_ms"] == 1000.0
    assert entry["final_status"] == "success"
    assert entry["files_changed_count"] == 1
    assert "build the thing" in entry["task_snippet"]


def test_lifecycle_ledger_entry_includes_compaction_summary(tmp_path):
    from weave.core.compaction import compact_sessions

    sessions_dir = tmp_path / "sessions"
    summary_record = _make_record(
        session_id="old-sess",
        record_type=ActivityType.system,
        task="compaction_summary",
        metadata={
            "compacted_count": 10,
            "total_duration_ms": 5000.0,
            "earliest_timestamp": "2026-04-10T08:00:00+00:00",
            "latest_timestamp": "2026-04-10T20:00:00+00:00",
            "providers_used": ["claude-code"],
            "status_counts": {"success": 10},
            "total_files_changed": 20,
            "unique_files_changed": [],
        },
    )
    tail_record = _make_record(
        session_id="old-sess", duration=100.0, files_changed=["x.py"],
    )
    _create_session_files(
        sessions_dir, "old-sess",
        records=[summary_record, tail_record],
        mtime_offset=-1000,
    )
    _create_session_files(sessions_dir, "new-sess", mtime_offset=0)

    compact_sessions(sessions_dir, sessions_to_keep=1)

    ledger = sessions_dir / "session_history.jsonl"
    entries = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    entry = entries[0]
    assert entry["invocation_count"] == 11
    assert entry["total_duration_ms"] == 5100.0


def test_lifecycle_deletes_all_sidecar_files(tmp_path):
    from weave.core.compaction import compact_sessions

    sessions_dir = tmp_path / "sessions"
    _create_session_files(sessions_dir, "old-sess", mtime_offset=-1000)
    _create_session_files(sessions_dir, "new-sess", mtime_offset=0)

    compact_sessions(sessions_dir, sessions_to_keep=1)
    assert not (sessions_dir / "old-sess.jsonl").exists()
    assert not (sessions_dir / "old-sess.binding.json").exists()
    assert not (sessions_dir / "old-sess.start_marker.json").exists()


def test_lifecycle_skips_missing_sidecars(tmp_path):
    from weave.core.compaction import compact_sessions

    sessions_dir = tmp_path / "sessions"
    _create_session_files(
        sessions_dir, "old-sess",
        with_binding=False, with_marker=False,
        mtime_offset=-1000,
    )
    _create_session_files(sessions_dir, "new-sess", mtime_offset=0)

    result = compact_sessions(sessions_dir, sessions_to_keep=1)
    assert result.removed == 1
    assert len(result.errors) == 0


def test_lifecycle_dry_run_deletes_nothing(tmp_path):
    from weave.core.compaction import compact_sessions

    sessions_dir = tmp_path / "sessions"
    _create_session_files(sessions_dir, "old-sess", mtime_offset=-1000)
    _create_session_files(sessions_dir, "new-sess", mtime_offset=0)

    result = compact_sessions(sessions_dir, sessions_to_keep=1, dry_run=True)
    assert result.removed == 1
    assert (sessions_dir / "old-sess.jsonl").exists()
    assert not (sessions_dir / "session_history.jsonl").exists()


def test_lifecycle_excludes_ledger_from_session_count(tmp_path):
    from weave.core.compaction import compact_sessions

    sessions_dir = tmp_path / "sessions"
    _create_session_files(sessions_dir, "sess-1", mtime_offset=-1000)
    _create_session_files(sessions_dir, "sess-2", mtime_offset=0)
    (sessions_dir / "session_history.jsonl").write_text('{"old": true}\n')

    result = compact_sessions(sessions_dir, sessions_to_keep=2)
    assert result.kept == 2
    assert result.removed == 0


def test_lifecycle_handles_corrupt_session(tmp_path):
    from weave.core.compaction import compact_sessions

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "corrupt.jsonl").write_text("not valid json at all\n")
    os.utime(sessions_dir / "corrupt.jsonl", (time.time() - 1000, time.time() - 1000))
    _create_session_files(sessions_dir, "good-sess", mtime_offset=0)

    result = compact_sessions(sessions_dir, sessions_to_keep=1)
    assert result.removed == 1
    assert len(result.errors) == 0
    ledger = sessions_dir / "session_history.jsonl"
    entries = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    assert len(entries) == 1
    assert entries[0]["session_id"] == "corrupt"
    assert entries[0]["invocation_count"] == 0
    assert entries[0]["final_status"] == "unknown"


def test_compact_cli_command_exists():
    from click.testing import CliRunner
    from weave.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["compact", "--help"])
    assert result.exit_code == 0
    assert "compact" in result.output.lower()


def test_compact_cli_runs_lifecycle(tmp_path):
    from click.testing import CliRunner
    from weave.cli import main

    sessions_dir = tmp_path / ".harness" / "sessions"
    _create_session_files(sessions_dir, "old-sess", mtime_offset=-1000)
    _create_session_files(sessions_dir, "new-sess", mtime_offset=0)
    harness = tmp_path / ".harness"
    (harness / "config.json").write_text(json.dumps({
        "version": "1", "phase": "sandbox", "default_provider": "claude-code",
        "providers": {"claude-code": {"command": "claude"}},
        "sessions": {"compaction": {"sessions_to_keep": 1}},
    }))

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        import os
        os.chdir(tmp_path)
        result = runner.invoke(main, ["compact"])
    assert result.exit_code == 0
    assert "1" in result.output


def test_read_session_history(tmp_path):
    from weave.core.compaction import read_session_history

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    ledger = sessions_dir / "session_history.jsonl"
    ledger.write_text(
        '{"session_id": "sess-old", "provider": "ollama", "started": "2026-04-09T08:00:00+00:00", "ended": "2026-04-09T09:00:00+00:00", "invocation_count": 3, "total_duration_ms": 12100.0, "final_status": "success", "files_changed_count": 5, "task_snippet": "analyze code"}\n'
        '{"session_id": "sess-new", "provider": "claude-code", "started": "2026-04-10T10:00:00+00:00", "ended": "2026-04-10T11:00:00+00:00", "invocation_count": 12, "total_duration_ms": 45000.0, "final_status": "success", "files_changed_count": 8, "task_snippet": "build the thing"}\n'
    )
    entries = read_session_history(sessions_dir)
    assert len(entries) == 2
    assert entries[0]["session_id"] == "sess-new"
    assert entries[1]["session_id"] == "sess-old"


def test_read_session_history_missing_ledger(tmp_path):
    from weave.core.compaction import read_session_history

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    entries = read_session_history(sessions_dir)
    assert entries == []


def test_status_cmd_shows_session_history(tmp_path):
    """weave status includes compacted session history from the ledger."""
    from click.testing import CliRunner
    from weave.cli import main

    harness = tmp_path / ".harness"
    for sub in ["context", "hooks", "providers", "sessions", "integrations"]:
        (harness / sub).mkdir(parents=True)

    (harness / "manifest.json").write_text(json.dumps({
        "id": "t", "type": "project", "name": "statustest", "status": "active",
        "phase": "sandbox", "parent": None, "children": [],
        "provider": "claude-code", "agent": None,
        "created": "2026-04-11T00:00:00Z", "updated": "2026-04-11T00:00:00Z",
        "inputs": {}, "outputs": {}, "tags": [],
    }))
    (harness / "config.json").write_text(json.dumps({
        "version": "1", "phase": "sandbox", "default_provider": "claude-code",
        "providers": {"claude-code": {"command": "claude", "enabled": True}},
    }))

    sessions_dir = harness / "sessions"
    (sessions_dir / "session_history.jsonl").write_text(
        '{"session_id": "sess-compacted", "provider": "claude-code", '
        '"started": "2026-04-10T10:00:00+00:00", "ended": "2026-04-10T11:00:00+00:00", '
        '"invocation_count": 5, "total_duration_ms": 30000.0, '
        '"final_status": "success", "files_changed_count": 3, '
        '"task_snippet": "build feature"}\n'
    )
    (sessions_dir / "active-sess.jsonl").write_text('{"dummy": true}\n')

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        import os
        os.chdir(tmp_path)
        result = runner.invoke(main, ["status"])

    assert result.exit_code == 0
    assert "1 active" in result.output
    assert "1 compacted" in result.output
    assert "Session history" in result.output
    assert "sess-compacted" in result.output
    assert "5 invocations" in result.output
