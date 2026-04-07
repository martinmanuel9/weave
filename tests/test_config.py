"""Tests for weave config resolution."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from weave.core.config import _deep_merge, resolve_config


def test_deep_merge_nested():
    base = {"a": {"x": 1, "y": 2}, "b": 3}
    override = {"a": {"y": 99, "z": 0}, "c": 4}
    result = _deep_merge(base, override)
    assert result == {"a": {"x": 1, "y": 99, "z": 0}, "b": 3, "c": 4}


def test_deep_merge_non_dict_override():
    base = {"a": {"nested": 1}}
    override = {"a": "string"}
    result = _deep_merge(base, override)
    assert result["a"] == "string"


def test_resolve_defaults(tmp_path):
    """No config files → defaults should be returned."""
    config = resolve_config(project_dir=tmp_path, user_home=tmp_path)
    assert config.default_provider == "claude-code"
    assert config.version == "1"
    assert "claude-code" in config.providers


def test_resolve_project_override(tmp_path):
    """Project-level .harness/config.json overrides default_provider."""
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "config.json").write_text(
        json.dumps({"default_provider": "gemini"})
    )

    config = resolve_config(project_dir=tmp_path, user_home=tmp_path)
    assert config.default_provider == "gemini"


def test_resolve_local_overrides_project(tmp_path):
    """Local config overrides project config."""
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "config.json").write_text(json.dumps({"default_provider": "codex"}))
    (harness / "config.local.json").write_text(
        json.dumps({"default_provider": "ollama"})
    )

    config = resolve_config(project_dir=tmp_path, user_home=tmp_path)
    assert config.default_provider == "ollama"


def test_resolve_user_layer(tmp_path):
    """User-level config is applied before project config."""
    user_home = tmp_path / "home"
    user_harness = user_home / ".harness"
    user_harness.mkdir(parents=True)
    (user_harness / "config.json").write_text(
        json.dumps({"logging": {"level": "debug"}})
    )

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    config = resolve_config(project_dir=project_dir, user_home=user_home)
    assert config.logging.level == "debug"
