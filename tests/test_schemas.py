"""Tests for Weave Pydantic schemas."""
import pytest

from weave.schemas.manifest import (
    Manifest,
    Phase,
    UnitStatus,
    UnitType,
    create_manifest,
)
from weave.schemas.config import (
    WeaveConfig,
    create_default_config,
)
from weave.schemas.activity import (
    ActivityRecord,
    ActivityStatus,
    ActivityType,
    HookResult,
)


# ---------------------------------------------------------------------------
# Manifest tests
# ---------------------------------------------------------------------------

def test_manifest_defaults():
    m = create_manifest("test")
    assert m.name == "test"
    assert m.type == UnitType.project
    assert m.status == UnitStatus.pending
    assert m.phase == Phase.sandbox
    assert m.parent is None
    assert m.children == []
    assert m.provider is None
    assert m.agent is None
    assert m.inputs == {}
    assert m.outputs == {}
    assert m.tags == []
    assert m.id  # non-empty uuid string


def test_manifest_custom():
    m = create_manifest(
        "my-workflow",
        unit_type=UnitType.workflow,
        provider="claude-code",
    )
    assert m.type == UnitType.workflow
    assert m.provider == "claude-code"
    assert m.phase == Phase.sandbox  # default unchanged


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

def test_config_defaults():
    cfg = create_default_config()
    assert cfg.version == "1"
    assert cfg.phase == "sandbox"
    assert cfg.default_provider == "claude-code"
    assert "claude-code" in cfg.providers
    assert cfg.providers["claude-code"].enabled is True
    assert cfg.hooks.pre_invoke == []
    assert cfg.sessions.compaction.keep_recent == 50
    assert cfg.logging.level == "info"
    assert cfg.logging.format == "jsonl"
    assert "claude-code" in cfg.context.translate_to


def test_config_custom_provider():
    cfg = create_default_config(default_provider="gemini")
    assert cfg.default_provider == "gemini"


# ---------------------------------------------------------------------------
# ActivityRecord tests
# ---------------------------------------------------------------------------

def test_activity_record_defaults():
    rec = ActivityRecord(session_id="sess-123")
    assert rec.session_id == "sess-123"
    assert rec.status == ActivityStatus.success
    assert rec.files_changed == []
    assert rec.hook_results == []
    assert rec.type == ActivityType.invoke
    assert rec.id  # non-empty uuid string


def test_hook_result():
    hr = HookResult(hook="lint", phase="pre_invoke", result="passed", message="All clear")
    data = hr.model_dump()
    assert data["hook"] == "lint"
    assert data["phase"] == "pre_invoke"
    assert data["result"] == "passed"
    assert data["message"] == "All clear"


# ---------------------------------------------------------------------------
# JSON roundtrip tests
# ---------------------------------------------------------------------------

def test_manifest_json_roundtrip():
    original = create_manifest("roundtrip-test", unit_type=UnitType.task, provider="ollama")
    json_str = original.model_dump_json()
    restored = Manifest.model_validate_json(json_str)
    assert restored.name == original.name
    assert restored.type == original.type
    assert restored.provider == original.provider
    assert restored.id == original.id


def test_config_json_roundtrip():
    original = create_default_config()
    json_str = original.model_dump_json()
    restored = WeaveConfig.model_validate_json(json_str)
    assert restored.default_provider == original.default_provider
    assert restored.version == original.version
    assert set(restored.providers.keys()) == set(original.providers.keys())
    assert restored.sessions.compaction.keep_recent == original.sessions.compaction.keep_recent
