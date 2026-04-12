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


def test_provider_config_capability_override_default():
    from weave.schemas.config import ProviderConfig
    p = ProviderConfig(command="x")
    assert p.capability_override is None


def test_provider_config_capability_override_explicit():
    from weave.schemas.config import ProviderConfig
    from weave.schemas.policy import RiskClass
    p = ProviderConfig(command="x", capability_override="read-only")
    assert p.capability_override == RiskClass.READ_ONLY


def test_security_config_defaults():
    from weave.schemas.config import SecurityConfig
    s = SecurityConfig()
    assert ".env" in s.write_deny_list
    assert "*.pem" in s.write_deny_list
    assert s.supply_chain_rules == {}
    assert s.write_deny_extras == []


def test_weave_config_has_security():
    from weave.schemas.config import WeaveConfig
    c = WeaveConfig()
    assert c.security is not None
    assert ".env" in c.security.write_deny_list


def test_weave_config_backwards_compat():
    """Existing config JSON without security/capability still parses."""
    from weave.schemas.config import WeaveConfig
    legacy = {
        "version": "1",
        "phase": "sandbox",
        "default_provider": "claude-code",
        "providers": {"claude-code": {"command": "claude"}},
    }
    c = WeaveConfig.model_validate(legacy)
    assert c.providers["claude-code"].capability_override is None
    assert c.security is not None


import warnings

from weave.schemas.config import ProviderConfig
from weave.schemas.policy import RiskClass


def test_provider_config_accepts_capability_override_field():
    cfg = ProviderConfig(command="x", capability_override=RiskClass.READ_ONLY)
    assert cfg.capability_override == RiskClass.READ_ONLY


def test_provider_config_legacy_capability_key_renamed_on_read(tmp_path):
    """A config.json with the legacy 'capability' key is silently migrated."""
    from weave.core.config import resolve_config

    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "config.json").write_text(
        '{"version": "1", "phase": "sandbox", "default_provider": "claude-code", '
        '"providers": {"claude-code": {"command": "claude", "capability": "read-only"}}}'
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        config = resolve_config(tmp_path, user_home=tmp_path)
    provider = config.providers["claude-code"]
    assert provider.capability_override == RiskClass.READ_ONLY
    assert any("capability" in str(w.message).lower() for w in caught)


def test_provider_config_new_key_wins_over_legacy_key(tmp_path):
    from weave.core.config import resolve_config

    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "config.json").write_text(
        '{"version": "1", "phase": "sandbox", "default_provider": "claude-code", '
        '"providers": {"claude-code": {"command": "claude", '
        '"capability": "workspace-write", "capability_override": "read-only"}}}'
    )
    config = resolve_config(tmp_path, user_home=tmp_path)
    assert config.providers["claude-code"].capability_override == RiskClass.READ_ONLY


def test_provider_config_silently_ignores_legacy_health_check_key(tmp_path):
    from weave.core.config import resolve_config

    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "config.json").write_text(
        '{"version": "1", "phase": "sandbox", "default_provider": "claude-code", '
        '"providers": {"claude-code": {"command": "claude", '
        '"health_check": "claude --version"}}}'
    )
    config = resolve_config(tmp_path, user_home=tmp_path)
    assert not hasattr(config.providers["claude-code"], "health_check")


def test_compaction_config_new_fields():
    from weave.schemas.config import CompactionConfig
    cfg = CompactionConfig()
    assert cfg.records_per_session == 50
    assert cfg.sessions_to_keep == 50
    assert not hasattr(cfg, "keep_recent")
    assert not hasattr(cfg, "archive_dir")


def test_compaction_config_legacy_keep_recent_migrated(tmp_path):
    from weave.core.config import resolve_config

    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "config.json").write_text(
        '{"version": "1", "phase": "sandbox", "default_provider": "claude-code", '
        '"providers": {"claude-code": {"command": "claude"}}, '
        '"sessions": {"compaction": {"keep_recent": 25, "archive_dir": ".harness/old"}}}'
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        config = resolve_config(tmp_path, user_home=tmp_path)
    assert config.sessions.compaction.records_per_session == 25
    assert config.sessions.compaction.sessions_to_keep == 50  # default, not migrated
    assert any("keep_recent" in str(w.message).lower() for w in caught)
