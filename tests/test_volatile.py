"""Tests for volatile context population."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from weave.schemas.activity import ActivityRecord, ActivityStatus, ActivityType
from weave.schemas.config import VolatileContextConfig, WeaveConfig
from weave.schemas.context import ContextAssembly


def _make_assembly(stable: str = "# Conventions\nbe nice") -> ContextAssembly:
    stable_hash = hashlib.sha256(stable.encode()).hexdigest()
    return ContextAssembly(
        stable_prefix=stable,
        volatile_task="",
        full=stable,
        stable_hash=stable_hash,
        full_hash=stable_hash,
        source_files=["conventions.md"],
    )


def test_volatile_context_config_defaults():
    cfg = VolatileContextConfig()
    assert cfg.enabled is True
    assert cfg.git_diff_enabled is True
    assert cfg.git_diff_max_files == 30
    assert cfg.git_log_enabled is True
    assert cfg.git_log_max_entries == 10
    assert cfg.activity_enabled is True
    assert cfg.activity_max_records == 5
    assert cfg.max_total_chars == 8000


def test_weave_config_has_volatile_context_field():
    config = WeaveConfig()
    assert hasattr(config, "volatile_context")
    assert isinstance(config.volatile_context, VolatileContextConfig)


def test_with_volatile_populates_fields():
    assembly = _make_assembly()
    updated = assembly.with_volatile("## Git State\nsome changes")
    assert updated.volatile_task == "## Git State\nsome changes"
    assert updated.stable_prefix == assembly.stable_prefix
    assert updated.stable_hash == assembly.stable_hash
    assert "## Git State" in updated.full
    assert assembly.stable_prefix in updated.full
    assert "\n---\n" in updated.full


def test_with_volatile_empty_is_noop():
    assembly = _make_assembly()
    updated = assembly.with_volatile("")
    assert updated is assembly
    assert updated.full_hash == assembly.full_hash


def test_with_volatile_full_hash_differs_from_stable_hash():
    assembly = _make_assembly()
    updated = assembly.with_volatile("volatile content")
    assert updated.full_hash != updated.stable_hash
    assert updated.stable_hash == assembly.stable_hash


def _init_git_repo(tmp_path: Path) -> Path:
    """Initialize a git repo with one committed file."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "init.txt").write_text("initial\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_git_diff_section_shows_modified_and_new_files(tmp_path):
    from weave.core.volatile import _git_diff_section
    _init_git_repo(tmp_path)
    (tmp_path / "init.txt").write_text("changed\n")
    (tmp_path / "new_file.py").write_text("print('hi')\n")
    result = _git_diff_section(tmp_path, max_files=30)
    assert "## Recent Git State" in result
    assert "init.txt" in result
    assert "modified" in result
    assert "new_file.py" in result
    assert "new" in result


def test_git_diff_section_caps_at_max_files(tmp_path):
    from weave.core.volatile import _git_diff_section
    _init_git_repo(tmp_path)
    for i in range(40):
        (tmp_path / f"file_{i:03d}.txt").write_text(f"content {i}\n")
    result = _git_diff_section(tmp_path, max_files=5)
    assert result.count("- ") <= 6
    assert "and 35 more" in result


def test_git_diff_section_empty_when_no_changes(tmp_path):
    from weave.core.volatile import _git_diff_section
    _init_git_repo(tmp_path)
    result = _git_diff_section(tmp_path, max_files=30)
    assert result == ""


def test_git_diff_section_empty_when_not_git_repo(tmp_path):
    from weave.core.volatile import _git_diff_section
    result = _git_diff_section(tmp_path, max_files=30)
    assert result == ""


def test_git_log_section_shows_recent_commits(tmp_path):
    from weave.core.volatile import _git_log_section
    _init_git_repo(tmp_path)
    for i in range(3):
        (tmp_path / f"f{i}.txt").write_text(f"{i}\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"commit {i}"], cwd=tmp_path, check=True)
    result = _git_log_section(tmp_path, max_entries=10)
    assert "### Recent commits" in result
    assert "commit 2" in result
    assert "commit 1" in result
    assert "commit 0" in result


def test_git_log_section_caps_at_max_entries(tmp_path):
    from weave.core.volatile import _git_log_section
    _init_git_repo(tmp_path)
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text(f"{i}\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"commit {i}"], cwd=tmp_path, check=True)
    result = _git_log_section(tmp_path, max_entries=3)
    lines = [l for l in result.splitlines() if l.startswith("- ")]
    assert len(lines) == 3


def test_git_log_section_empty_when_no_commits(tmp_path):
    from weave.core.volatile import _git_log_section
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    result = _git_log_section(tmp_path, max_entries=10)
    assert result == ""


def _make_record(
    session_id: str = "test-session",
    provider: str = "claude-code",
    task: str = "do something",
    duration: float = 100.0,
    status: ActivityStatus = ActivityStatus.success,
    files_changed: list[str] | None = None,
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


def _write_session_records(sessions_dir: Path, session_id: str, records: list[ActivityRecord]) -> None:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    log_file = sessions_dir / f"{session_id}.jsonl"
    with log_file.open("w") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")


def test_activity_section_shows_recent_records(tmp_path):
    from weave.core.volatile import _activity_section
    sessions_dir = tmp_path / "sessions"
    records = [
        _make_record(session_id="s1", provider="claude-code", task="write tests",
                     duration=22100.0, status=ActivityStatus.success,
                     files_changed=["a.py", "b.py"],
                     timestamp=datetime(2026, 4, 11, 10, 28, 12, tzinfo=timezone.utc)),
        _make_record(session_id="s1", provider="claude-code", task="implement auth middleware",
                     duration=45200.0, status=ActivityStatus.success,
                     files_changed=["c.py", "d.py", "e.py"],
                     timestamp=datetime(2026, 4, 11, 10, 30, 5, tzinfo=timezone.utc)),
    ]
    _write_session_records(sessions_dir, "s1", records)
    result = _activity_section(sessions_dir, "s1", max_records=5)
    assert "## Session Activity" in result
    assert "implement auth middleware" in result
    assert "write tests" in result
    lines = [l for l in result.splitlines() if l.startswith("- [")]
    assert "implement auth" in lines[0]  # most recent first
    assert "write tests" in lines[1]


def test_activity_section_caps_at_max_records(tmp_path):
    from weave.core.volatile import _activity_section
    sessions_dir = tmp_path / "sessions"
    records = [_make_record(session_id="s1", task=f"task {i}") for i in range(10)]
    _write_session_records(sessions_dir, "s1", records)
    result = _activity_section(sessions_dir, "s1", max_records=3)
    lines = [l for l in result.splitlines() if l.startswith("- [")]
    assert len(lines) == 3


def test_activity_section_skips_compaction_summaries(tmp_path):
    from weave.core.volatile import _activity_section
    sessions_dir = tmp_path / "sessions"
    records = [
        _make_record(session_id="s1", record_type=ActivityType.system,
                     task="compaction_summary", metadata={"compacted_count": 10}),
        _make_record(session_id="s1", task="real task 1"),
        _make_record(session_id="s1", task="real task 2"),
    ]
    _write_session_records(sessions_dir, "s1", records)
    result = _activity_section(sessions_dir, "s1", max_records=5)
    assert "compaction_summary" not in result
    assert "real task 1" in result
    assert "real task 2" in result


def test_activity_section_empty_when_no_session(tmp_path):
    from weave.core.volatile import _activity_section
    sessions_dir = tmp_path / "sessions"
    result = _activity_section(sessions_dir, "nonexistent", max_records=5)
    assert result == ""


def test_build_volatile_context_combines_sources(tmp_path):
    from weave.core.volatile import build_volatile_context
    repo = _init_git_repo(tmp_path)
    (repo / "new.py").write_text("x = 1\n")
    sessions_dir = repo / ".harness" / "sessions"
    _write_session_records(sessions_dir, "s1", [
        _make_record(session_id="s1", task="previous task"),
    ])
    config = VolatileContextConfig()
    result = build_volatile_context(repo, config, session_id="s1")
    assert "## Recent Git State" in result
    assert "### Recent commits" in result
    assert "## Session Activity" in result


def test_build_volatile_context_disabled_returns_empty(tmp_path):
    from weave.core.volatile import build_volatile_context
    config = VolatileContextConfig(enabled=False)
    result = build_volatile_context(tmp_path, config, session_id="s1")
    assert result == ""


def test_build_volatile_context_omits_empty_sources(tmp_path):
    from weave.core.volatile import build_volatile_context
    sessions_dir = tmp_path / ".harness" / "sessions"
    _write_session_records(sessions_dir, "s1", [
        _make_record(session_id="s1", task="only activity"),
    ])
    config = VolatileContextConfig(git_diff_enabled=False, git_log_enabled=False)
    result = build_volatile_context(tmp_path, config, session_id="s1")
    assert "## Session Activity" in result
    assert "## Recent Git State" not in result
    assert "### Recent commits" not in result


def test_build_volatile_context_truncates_at_global_limit(tmp_path):
    from weave.core.volatile import build_volatile_context
    repo = _init_git_repo(tmp_path)
    for i in range(20):
        (repo / f"file_{i:03d}.py").write_text(f"x = {i}\n")
    config = VolatileContextConfig(max_total_chars=100)
    result = build_volatile_context(repo, config)
    assert len(result) <= 130
    assert "(volatile context truncated)" in result
