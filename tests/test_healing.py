import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

from weave.core.healing import attempt_healing, HealingResult
from weave.schemas.skill import (
    SkillDefinition,
    SkillStrategy,
    HealingLogEntry,
)


@dataclass
class MockInvokeResult:
    exit_code: int
    stdout: str
    stderr: str
    structured: dict | None
    duration: float
    files_changed: list[str]


def _make_skill(
    fallbacks: list[str] | None = None, max_retries: int = 2
) -> SkillDefinition:
    return SkillDefinition(
        name="web-research",
        description="Test skill",
        intents=["web_research"],
        strategy=SkillStrategy(
            primary_provider="hermes",
            fallback_providers=fallbacks or ["claude-code"],
            timeout_ms=30000,
            max_retries=max_retries,
        ),
    )


def test_healing_with_fallback_success():
    skill = _make_skill(fallbacks=["claude-code"])
    mock_result = MockInvokeResult(
        exit_code=0, stdout="Fallback succeeded", stderr="",
        structured=None, duration=11200, files_changed=[],
    )
    with patch("weave.core.healing._invoke_fallback", return_value=mock_result):
        result = attempt_healing(
            failure_reason="timeout after 30000ms",
            skill=skill,
            task="research quantum computing",
            working_dir=Path("/tmp"),
            session_id="test-session",
        )
    assert result.healed is True
    assert result.fallback_provider == "claude-code"
    assert result.invoke_result.stdout == "Fallback succeeded"


def test_healing_no_fallback_providers():
    skill = _make_skill(fallbacks=[])
    result = attempt_healing(
        failure_reason="timeout",
        skill=skill,
        task="research something",
        working_dir=Path("/tmp"),
        session_id="test-session",
    )
    assert result.healed is False
    assert result.fallback_provider is None


def test_healing_all_fallbacks_fail():
    skill = _make_skill(fallbacks=["claude-code", "gemini"])
    mock_fail = MockInvokeResult(
        exit_code=1, stdout="", stderr="error", structured=None,
        duration=5000, files_changed=[],
    )
    with patch("weave.core.healing._invoke_fallback", return_value=mock_fail):
        result = attempt_healing(
            failure_reason="timeout",
            skill=skill,
            task="research something",
            working_dir=Path("/tmp"),
            session_id="test-session",
        )
    assert result.healed is False


def test_healing_result_has_log_entry():
    skill = _make_skill(fallbacks=["claude-code"])
    mock_result = MockInvokeResult(
        exit_code=0, stdout="OK", stderr="", structured=None,
        duration=11200, files_changed=[],
    )
    with patch("weave.core.healing._invoke_fallback", return_value=mock_result):
        result = attempt_healing(
            failure_reason="exit code 1",
            skill=skill,
            task="research something",
            working_dir=Path("/tmp"),
            session_id="test-session",
        )
    assert result.healing_log_entry is not None
    assert result.healing_log_entry.trigger == "exit code 1"
    assert result.healing_log_entry.action == "fallback to claude-code"
    assert result.healing_log_entry.outcome == "success"
