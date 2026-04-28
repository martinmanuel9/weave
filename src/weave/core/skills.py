"""Skill registry -- CRUD for .harness/skills/ routing recipes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from weave.schemas.skill import SkillDefinition
from weave.schemas.feedback import FeedbackRecord


def _skills_dir(project_root: Path) -> Path:
    return project_root / ".harness" / "skills"


def _registry_path(project_root: Path) -> Path:
    return _skills_dir(project_root) / "registry.json"


def _skill_path(name: str, project_root: Path) -> Path:
    return _skills_dir(project_root) / f"{name}.skill.json"


def load_registry(project_root: Path) -> dict:
    path = _registry_path(project_root)
    if not path.exists():
        return {"version": 1, "skills": {}, "last_updated": ""}
    return json.loads(path.read_text())


def _save_registry(registry: dict, project_root: Path) -> None:
    registry["last_updated"] = datetime.now(timezone.utc).isoformat()
    _registry_path(project_root).write_text(
        json.dumps(registry, indent=2, default=str)
    )


def load_skill(name: str, project_root: Path) -> SkillDefinition:
    path = _skill_path(name, project_root)
    if not path.exists():
        raise FileNotFoundError(f"Skill not found: {name}")
    return SkillDefinition.model_validate_json(path.read_text())


def save_skill(skill: SkillDefinition, project_root: Path) -> None:
    skill.updated_at = datetime.now(timezone.utc)
    path = _skill_path(skill.name, project_root)
    path.write_text(skill.model_dump_json(indent=2, exclude_none=True))

    registry = load_registry(project_root)
    registry["skills"][skill.name] = {
        "file": f"{skill.name}.skill.json",
        "confidence_score": _skill_confidence(skill),
        "provider": skill.strategy.primary_provider,
        "intents": skill.intents,
    }
    _save_registry(registry, project_root)


def list_skills(project_root: Path) -> list[SkillDefinition]:
    registry = load_registry(project_root)
    skills = []
    for name in sorted(registry["skills"]):
        try:
            skills.append(load_skill(name, project_root))
        except FileNotFoundError:
            continue
    return skills


def update_skill_metrics(
    name: str, record: FeedbackRecord, project_root: Path
) -> None:
    skill = load_skill(name, project_root)

    skill.metrics.invocations += 1
    if record.outcome in ("success", "healed"):
        skill.metrics.successes += 1
    else:
        skill.metrics.failures += 1

    # Update running average duration for successes
    if record.outcome == "success" and skill.metrics.successes > 0:
        prev_avg = skill.metrics.avg_duration_ms
        n = skill.metrics.successes
        skill.metrics.avg_duration_ms = int(
            prev_avg + (record.duration_ms - prev_avg) / n
        )

    # Update per-provider score
    ps = skill.metrics.by_provider.get(record.provider)
    if ps is None:
        from weave.schemas.skill import ProviderScore
        ps = ProviderScore()
    ps.invocations += 1
    if record.outcome in ("success", "healed"):
        ps.successes += 1
    if ps.invocations > 0:
        ps.avg_ms = int(
            ps.avg_ms + (record.duration_ms - ps.avg_ms) / ps.invocations
        )
        ps.score = round(ps.successes / ps.invocations, 3) if ps.invocations >= 3 else 0.5
    skill.metrics.by_provider[record.provider] = ps

    save_skill(skill, project_root)


def get_best_provider(intent: str, project_root: Path) -> str | None:
    registry = load_registry(project_root)
    best_provider = None
    best_score = -1.0
    for _name, entry in registry["skills"].items():
        if intent in entry.get("intents", []):
            score = entry.get("confidence_score", 0.0)
            if score > best_score:
                best_score = score
                best_provider = entry.get("provider")
    return best_provider


def _skill_confidence(skill: SkillDefinition) -> float:
    if skill.metrics.invocations < 3:
        return 0.5
    return round(skill.metrics.successes / skill.metrics.invocations, 3)
