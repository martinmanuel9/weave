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
