"""Feedback ledger -- invocation outcomes, score computation, routing scores."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from weave.schemas.feedback import FeedbackRecord, RoutingScores


def _feedback_dir(project_root: Path) -> Path:
    return project_root / ".harness" / "feedback"


def _ledger_path(project_root: Path) -> Path:
    return _feedback_dir(project_root) / "feedback.jsonl"


def _scores_path(project_root: Path) -> Path:
    return _feedback_dir(project_root) / "routing-scores.json"


def append_feedback(record: FeedbackRecord, project_root: Path) -> None:
    path = _ledger_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(record.model_dump_json() + "\n")


def load_feedback(project_root: Path) -> list[FeedbackRecord]:
    path = _ledger_path(project_root)
    if not path.exists():
        return []
    records = []
    for line in path.read_text().strip().splitlines():
        if line:
            records.append(FeedbackRecord.model_validate_json(line))
    return records


def compute_score(
    intent: str, provider: str, records: list[FeedbackRecord]
) -> float:
    relevant = [
        r for r in records if r.intent == intent and r.provider == provider
    ]
    if len(relevant) < 3:
        return 0.5

    successes = sum(
        1 for r in relevant if r.outcome in ("success", "healed")
    )
    success_rate = successes / len(relevant)

    success_durations = [
        r.duration_ms for r in relevant if r.outcome == "success"
    ]
    if success_durations:
        med_ms = median(success_durations)
        duration_factor = 1.0 / (1.0 + med_ms / 30000)
    else:
        duration_factor = 0.0

    return round(success_rate * 0.7 + duration_factor * 0.3, 3)


def compute_all_scores(records: list[FeedbackRecord]) -> RoutingScores:
    pairs: set[tuple[str, str]] = set()
    for r in records:
        pairs.add((r.intent, r.provider))

    scores: dict[str, dict[str, float]] = {}
    for intent, provider in pairs:
        score = compute_score(intent, provider, records)
        if intent not in scores:
            scores[intent] = {}
        scores[intent][provider] = score

    return RoutingScores(scores=scores)


def save_routing_scores(scores: RoutingScores, project_root: Path) -> None:
    path = _scores_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    scores.last_updated = datetime.now(timezone.utc)
    path.write_text(scores.model_dump_json(indent=2))


def load_routing_scores(project_root: Path) -> RoutingScores:
    path = _scores_path(project_root)
    if not path.exists():
        return RoutingScores()
    return RoutingScores.model_validate_json(path.read_text())
