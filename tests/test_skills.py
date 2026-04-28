import json
import pytest
from pathlib import Path

from weave.core.skills import (
    load_skill,
    save_skill,
    list_skills,
    update_skill_metrics,
    get_best_provider,
    load_registry,
)
from weave.schemas.skill import SkillDefinition, SkillStrategy, ProviderScore
from weave.schemas.feedback import FeedbackRecord


@pytest.fixture
def harness(tmp_path: Path) -> Path:
    skills_dir = tmp_path / ".harness" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "registry.json").write_text(json.dumps({
        "version": 1, "skills": {}, "last_updated": "2026-04-27T00:00:00Z"
    }))
    return tmp_path


def _make_skill(name: str = "web-research", provider: str = "hermes") -> SkillDefinition:
    return SkillDefinition(
        name=name,
        description=f"Test skill {name}",
        intents=["web_research"],
        strategy=SkillStrategy(
            primary_provider=provider,
            fallback_providers=["claude-code"],
        ),
    )


def test_save_and_load_skill(harness: Path):
    skill = _make_skill()
    save_skill(skill, harness)
    loaded = load_skill("web-research", harness)
    assert loaded.name == "web-research"
    assert loaded.strategy.primary_provider == "hermes"


def test_save_updates_registry(harness: Path):
    skill = _make_skill()
    save_skill(skill, harness)
    registry = load_registry(harness)
    assert "web-research" in registry["skills"]
    assert registry["skills"]["web-research"]["provider"] == "hermes"


def test_list_skills_empty(harness: Path):
    result = list_skills(harness)
    assert result == []


def test_list_skills_returns_saved(harness: Path):
    save_skill(_make_skill("web-research"), harness)
    save_skill(_make_skill("image-gen"), harness)
    result = list_skills(harness)
    assert len(result) == 2
    names = {s.name for s in result}
    assert names == {"web-research", "image-gen"}


def test_update_skill_metrics_success(harness: Path):
    save_skill(_make_skill(), harness)
    record = FeedbackRecord(
        intent="web_research",
        provider="hermes",
        outcome="success",
        duration_ms=8200,
    )
    update_skill_metrics("web-research", record, harness)
    loaded = load_skill("web-research", harness)
    assert loaded.metrics.invocations == 1
    assert loaded.metrics.successes == 1
    assert loaded.metrics.by_provider["hermes"].invocations == 1


def test_update_skill_metrics_failure(harness: Path):
    save_skill(_make_skill(), harness)
    record = FeedbackRecord(
        intent="web_research",
        provider="hermes",
        outcome="failure",
        duration_ms=30000,
    )
    update_skill_metrics("web-research", record, harness)
    loaded = load_skill("web-research", harness)
    assert loaded.metrics.invocations == 1
    assert loaded.metrics.failures == 1


def test_get_best_provider(harness: Path):
    skill = _make_skill()
    skill.metrics.by_provider = {
        "hermes": ProviderScore(invocations=20, successes=19, score=0.94),
        "claude-code": ProviderScore(invocations=4, successes=3, score=0.72),
    }
    save_skill(skill, harness)
    best = get_best_provider("web_research", harness)
    assert best == "hermes"


def test_get_best_provider_no_match(harness: Path):
    result = get_best_provider("unknown_intent", harness)
    assert result is None


def test_load_skill_not_found(harness: Path):
    with pytest.raises(FileNotFoundError):
        load_skill("nonexistent", harness)
