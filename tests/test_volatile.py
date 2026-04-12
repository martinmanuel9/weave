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
