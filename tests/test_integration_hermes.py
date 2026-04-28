"""End-to-end test: skill lookup -> feedback -> score update -> healing."""

import json
import pytest
from pathlib import Path

from weave.core.skills import load_skill, save_skill, get_best_provider, update_skill_metrics
from weave.core.feedback import append_feedback, load_feedback, compute_all_scores, save_routing_scores, load_routing_scores
from weave.core.healing import attempt_healing, HealingResult
from weave.schemas.skill import SkillDefinition, SkillStrategy, ProviderScore
from weave.schemas.feedback import FeedbackRecord, HealingDetail


@pytest.fixture
def harness(tmp_path: Path) -> Path:
    skills_dir = tmp_path / ".harness" / "skills"
    skills_dir.mkdir(parents=True)
    feedback_dir = tmp_path / ".harness" / "feedback"
    feedback_dir.mkdir(parents=True)
    (skills_dir / "registry.json").write_text(json.dumps({
        "version": 1, "skills": {}, "last_updated": ""
    }))
    return tmp_path


def test_full_feedback_loop(harness: Path):
    """Simulate 5 successful invocations and verify routing scores update."""
    skill = SkillDefinition(
        name="web-research",
        description="Test",
        intents=["web_research"],
        strategy=SkillStrategy(
            primary_provider="hermes",
            fallback_providers=["claude-code"],
        ),
    )
    save_skill(skill, harness)

    # Simulate 5 successful invocations
    for i in range(5):
        record = FeedbackRecord(
            intent="web_research",
            provider="hermes",
            outcome="success",
            duration_ms=8000 + i * 100,
            skill_used="web-research",
        )
        append_feedback(record, harness)
        update_skill_metrics("web-research", record, harness)

    # Verify skill metrics
    updated = load_skill("web-research", harness)
    assert updated.metrics.invocations == 5
    assert updated.metrics.successes == 5
    assert updated.metrics.by_provider["hermes"].invocations == 5

    # Compute and save routing scores
    records = load_feedback(harness)
    scores = compute_all_scores(records)
    save_routing_scores(scores, harness)

    # Verify routing scores
    loaded_scores = load_routing_scores(harness)
    assert loaded_scores.scores["web_research"]["hermes"] > 0.8

    # Verify best provider lookup
    best = get_best_provider("web_research", harness)
    assert best == "hermes"


def test_healing_updates_feedback(harness: Path):
    """Simulate a failure + healing and verify feedback records correctly."""
    skill = SkillDefinition(
        name="web-research",
        description="Test",
        intents=["web_research"],
        strategy=SkillStrategy(
            primary_provider="hermes",
            fallback_providers=["claude-code"],
        ),
    )
    save_skill(skill, harness)

    # Record a healed outcome
    record = FeedbackRecord(
        intent="web_research",
        provider="hermes",
        outcome="healed",
        duration_ms=19400,
        skill_used="web-research",
        healing=HealingDetail(
            used=True,
            attempts=1,
            original_failure="timeout",
            fallbacks=[{"provider": "claude-code", "outcome": "success", "duration_ms": 11200}],
        ),
    )
    append_feedback(record, harness)
    update_skill_metrics("web-research", record, harness)

    # Healed counts as success for skill
    updated = load_skill("web-research", harness)
    assert updated.metrics.successes == 1
    assert updated.metrics.failures == 0

    # But provider score reflects the healing attempt
    records = load_feedback(harness)
    assert records[0].healing.used is True


def test_promotion_threshold(harness: Path):
    """Verify skill meets promotion threshold after enough successes."""
    skill = SkillDefinition(
        name="web-research",
        description="Test",
        intents=["web_research"],
        strategy=SkillStrategy(primary_provider="hermes"),
    )
    save_skill(skill, harness)

    for _ in range(6):
        record = FeedbackRecord(
            intent="web_research", provider="hermes",
            outcome="success", duration_ms=8000, skill_used="web-research",
        )
        update_skill_metrics("web-research", record, harness)

    updated = load_skill("web-research", harness)
    confidence = updated.metrics.successes / updated.metrics.invocations
    assert confidence >= 0.85
    assert updated.metrics.invocations >= 5
