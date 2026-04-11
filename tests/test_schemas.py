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


# ---------------------------------------------------------------------------
# Policy schema tests
# ---------------------------------------------------------------------------

def test_risk_class_ordering():
    from weave.schemas.policy import RiskClass, risk_class_level
    assert risk_class_level(RiskClass.READ_ONLY) == 0
    assert risk_class_level(RiskClass.WORKSPACE_WRITE) == 1
    assert risk_class_level(RiskClass.EXTERNAL_NETWORK) == 2
    assert risk_class_level(RiskClass.DESTRUCTIVE) == 3


def test_policy_result_defaults():
    from weave.schemas.policy import PolicyResult, RiskClass
    r = PolicyResult(
        allowed=True,
        effective_risk_class=RiskClass.READ_ONLY,
        provider_ceiling=RiskClass.WORKSPACE_WRITE,
    )
    assert r.allowed is True
    assert r.warnings == []
    assert r.denials == []
    assert r.hook_results == []


def test_security_finding_fields():
    from weave.schemas.policy import SecurityFinding
    f = SecurityFinding(
        rule_id="pth-injection",
        file="evil.pth",
        match="suspicious content",
        severity="critical",
        action_taken="deny",
    )
    assert f.rule_id == "pth-injection"
    assert f.action_taken == "deny"


def test_runtime_status_values():
    from weave.schemas.policy import RuntimeStatus
    assert RuntimeStatus.SUCCESS == "success"
    assert RuntimeStatus.DENIED == "denied"
    assert RuntimeStatus.FLAGGED == "flagged"
    assert RuntimeStatus.FAILED == "failed"
    assert RuntimeStatus.TIMEOUT == "timeout"


# ---------------------------------------------------------------------------
# Governance field tests (Task 2)
# ---------------------------------------------------------------------------

def test_activity_record_governance_fields():
    from weave.schemas.activity import ActivityRecord
    r = ActivityRecord(
        session_id="s1",
        risk_class="workspace-write",
        policy_result={"allowed": True},
        security_findings=[{"rule_id": "pth-injection", "file": "x.pth"}],
        approval_status="approved",
        caller="itzel",
        runtime_status="success",
    )
    assert r.risk_class == "workspace-write"
    assert r.caller == "itzel"
    assert r.runtime_status == "success"
    assert len(r.security_findings) == 1


def test_activity_status_flagged():
    from weave.schemas.activity import ActivityStatus
    assert ActivityStatus.flagged == "flagged"


# ---------------------------------------------------------------------------
# ContextAssembly schema tests (MAR-142)
# ---------------------------------------------------------------------------

def test_context_assembly_defaults():
    from weave.schemas.context import ContextAssembly
    ca = ContextAssembly(
        stable_prefix="hello",
        full="hello",
        stable_hash="abc",
        full_hash="abc",
    )
    assert ca.stable_prefix == "hello"
    assert ca.volatile_task == ""  # default
    assert ca.full == "hello"
    assert ca.source_files == []  # default factory


def test_session_binding_fields():
    from datetime import datetime, timezone
    from weave.schemas.session_binding import SessionBinding

    sb = SessionBinding(
        session_id="test-id",
        created_at=datetime.now(timezone.utc),
        provider_name="claude-code",
        adapter_script_hash="a" * 64,
        context_stable_hash="b" * 64,
        config_hash="c" * 64,
    )
    assert sb.session_id == "test-id"
    assert sb.provider_name == "claude-code"
    assert len(sb.adapter_script_hash) == 64
    assert len(sb.context_stable_hash) == 64
    assert len(sb.config_hash) == 64


def test_session_marker_fields():
    from datetime import datetime, timezone
    from weave.schemas.session_marker import SessionMarker

    sm = SessionMarker(
        session_id="test-id",
        start_time=datetime.now(timezone.utc),
        git_available=True,
        start_head_sha="a" * 40,
        pre_invoke_untracked=["existing.txt"],
        task="execute plan 03-01",
        working_dir="/tmp/test",
    )
    assert sm.session_id == "test-id"
    assert sm.git_available is True
    assert sm.start_head_sha == "a" * 40
    assert sm.pre_invoke_untracked == ["existing.txt"]
    assert sm.task == "execute plan 03-01"
