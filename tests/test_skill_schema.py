"""Tests for skill registry and feedback ledger Pydantic schemas."""
import pytest
from datetime import datetime, timezone
from weave.schemas.skill import (
    SkillDefinition,
    SkillStrategy,
    SkillMetrics,
    ProviderScore,
)


def test_skill_definition_minimal():
    skill = SkillDefinition(
        name="web-research",
        description="Multi-source web research",
        intents=["web_research"],
        strategy=SkillStrategy(primary_provider="hermes"),
    )
    assert skill.name == "web-research"
    assert skill.version == "1"
    assert skill.strategy.primary_provider == "hermes"
    assert skill.strategy.fallback_providers == []
    assert skill.strategy.timeout_ms == 30000
    assert skill.strategy.max_retries == 2
    assert skill.metrics.invocations == 0


def test_skill_definition_full():
    skill = SkillDefinition(
        name="web-research",
        description="Multi-source web research with synthesis",
        intents=["web_research", "multi_step_task"],
        strategy=SkillStrategy(
            primary_provider="hermes",
            fallback_providers=["claude-code", "gemini"],
            context_injection="Use at least 3 sources.",
            timeout_ms=30000,
            max_retries=2,
        ),
        metrics=SkillMetrics(
            invocations=24,
            successes=22,
            failures=2,
            avg_duration_ms=8500,
            by_provider={
                "hermes": ProviderScore(
                    invocations=20, successes=19, avg_ms=8200, score=0.94
                ),
            },
        ),
    )
    assert skill.metrics.by_provider["hermes"].score == 0.94
    assert len(skill.intents) == 2


def test_skill_definition_roundtrip_json():
    skill = SkillDefinition(
        name="image-gen",
        description="Image generation via FAL.ai",
        intents=["image_generation"],
        strategy=SkillStrategy(primary_provider="hermes"),
    )
    json_str = skill.model_dump_json(indent=2)
    loaded = SkillDefinition.model_validate_json(json_str)
    assert loaded.name == skill.name
    assert loaded.strategy.primary_provider == "hermes"


def test_provider_score_defaults():
    ps = ProviderScore()
    assert ps.invocations == 0
    assert ps.successes == 0
    assert ps.avg_ms == 0
    assert ps.score == 0.5


from weave.schemas.feedback import FeedbackRecord, HealingDetail, RoutingScores


def test_feedback_record_minimal():
    rec = FeedbackRecord(
        intent="web_research",
        provider="hermes",
        outcome="success",
        duration_ms=8200,
    )
    assert rec.outcome == "success"
    assert rec.healing.used is False
    assert rec.routing_source == "static"


def test_feedback_record_with_healing():
    rec = FeedbackRecord(
        intent="web_research",
        provider="hermes",
        outcome="healed",
        duration_ms=19400,
        healing=HealingDetail(
            used=True,
            attempts=1,
            original_failure="timeout after 30000ms",
            fallbacks=[{"provider": "claude-code", "outcome": "success", "duration_ms": 11200}],
        ),
    )
    assert rec.healing.used is True
    assert rec.healing.attempts == 1


def test_routing_scores_roundtrip():
    scores = RoutingScores(
        scores={
            "web_research": {"hermes": 0.94, "claude-code": 0.72},
            "code_generation": {"claude-code": 0.97},
        }
    )
    json_str = scores.model_dump_json(indent=2)
    loaded = RoutingScores.model_validate_json(json_str)
    assert loaded.scores["web_research"]["hermes"] == 0.94
