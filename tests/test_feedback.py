import json
import pytest
from pathlib import Path

from weave.core.feedback import (
    append_feedback,
    load_feedback,
    compute_score,
    compute_all_scores,
    load_routing_scores,
    save_routing_scores,
)
from weave.schemas.feedback import FeedbackRecord, RoutingScores


@pytest.fixture
def harness(tmp_path: Path) -> Path:
    feedback_dir = tmp_path / ".harness" / "feedback"
    feedback_dir.mkdir(parents=True)
    return tmp_path


def _record(
    intent: str = "web_research",
    provider: str = "hermes",
    outcome: str = "success",
    duration_ms: int = 8200,
) -> FeedbackRecord:
    return FeedbackRecord(
        intent=intent,
        provider=provider,
        outcome=outcome,
        duration_ms=duration_ms,
    )


def test_append_and_load_feedback(harness: Path):
    append_feedback(_record(), harness)
    append_feedback(_record(outcome="failure", duration_ms=30000), harness)
    records = load_feedback(harness)
    assert len(records) == 2
    assert records[0].outcome == "success"
    assert records[1].outcome == "failure"


def test_compute_score_insufficient_data(harness: Path):
    records = [_record(), _record()]
    score = compute_score("web_research", "hermes", records)
    assert score == 0.5


def test_compute_score_all_success(harness: Path):
    records = [_record() for _ in range(5)]
    score = compute_score("web_research", "hermes", records)
    assert score > 0.8


def test_compute_score_mixed(harness: Path):
    records = [
        _record(outcome="success"),
        _record(outcome="success"),
        _record(outcome="failure", duration_ms=30000),
        _record(outcome="success"),
        _record(outcome="success"),
    ]
    score = compute_score("web_research", "hermes", records)
    assert 0.5 < score < 1.0


def test_compute_score_filters_by_intent_and_provider(harness: Path):
    records = [
        _record(intent="web_research", provider="hermes", outcome="success"),
        _record(intent="web_research", provider="hermes", outcome="success"),
        _record(intent="web_research", provider="hermes", outcome="success"),
        _record(intent="code_generation", provider="claude-code", outcome="success"),
    ]
    score = compute_score("web_research", "hermes", records)
    assert score > 0.8
    score2 = compute_score("web_research", "claude-code", records)
    assert score2 == 0.5


def test_compute_all_scores(harness: Path):
    records = [
        _record(intent="web_research", provider="hermes"),
        _record(intent="web_research", provider="hermes"),
        _record(intent="web_research", provider="hermes"),
        _record(intent="code_generation", provider="claude-code"),
        _record(intent="code_generation", provider="claude-code"),
        _record(intent="code_generation", provider="claude-code"),
    ]
    scores = compute_all_scores(records)
    assert "web_research" in scores.scores
    assert "hermes" in scores.scores["web_research"]
    assert "code_generation" in scores.scores


def test_routing_scores_roundtrip(harness: Path):
    scores = RoutingScores(scores={"web_research": {"hermes": 0.94}})
    save_routing_scores(scores, harness)
    loaded = load_routing_scores(harness)
    assert loaded.scores["web_research"]["hermes"] == 0.94
