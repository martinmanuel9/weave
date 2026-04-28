# Hermes-Weave Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate Hermes Agent as a Weave provider with a self-healing feedback loop so that Itzel dispatches web research, image generation, browser automation, and multi-step tasks to Hermes through the governed Weave pipeline.

**Architecture:** Three layers across two repos. Layer 1 (Weave + Itzel): Hermes provider adapter + intent mappings. Layer 2 (Weave): Skill registry in `.harness/skills/` with provider-agnostic routing recipes. Layer 3 (Weave + Itzel): Feedback ledger, routing score optimizer, and self-healing engine wired into Weave's post-invoke hooks. No changes to hermes-agent repo.

**Tech Stack:** Python 3.10+, Pydantic 2.x, Click (Weave CLI), PyYAML (Itzel config), JSONL (feedback ledger), JSON (skill registry, routing scores)

---

## File Map

### Weave repo (`~/repos/weave`)

| File | Action | Responsibility |
|------|--------|----------------|
| `src/weave/schemas/skill.py` | Create | SkillDefinition, SkillStrategy, SkillMetrics, ProviderScore Pydantic models |
| `src/weave/schemas/feedback.py` | Create | FeedbackRecord, HealingDetail, RoutingScores Pydantic models |
| `src/weave/core/skills.py` | Create | Skill registry CRUD: load, save, update metrics, get best provider |
| `src/weave/core/feedback.py` | Create | Feedback ledger: append record, compute scores, load/save routing scores |
| `src/weave/core/healing.py` | Create | Self-healing engine: attempt_healing with fallback retry |
| `src/weave/core/runtime.py` | Modify | Wire feedback_hook and healing into post-invoke stage |
| `src/weave/cli.py` | Modify | Add `weave skill` command group (list, show, create, import, promote) |
| `.harness/providers/hermes.contract.json` | Create | Hermes capability contract |
| `.harness/providers/hermes.sh` | Create | Bash wrapper invoking hermes_adapter.py |
| `.harness/providers/hermes_adapter.py` | Create | Python adapter importing AIAgent from hermes-agent |
| `tests/test_skill_schema.py` | Create | Tests for skill + feedback Pydantic models |
| `tests/test_skills.py` | Create | Tests for skill registry CRUD |
| `tests/test_feedback.py` | Create | Tests for feedback ledger + score computation |
| `tests/test_healing.py` | Create | Tests for self-healing engine |
| `tests/test_hermes_adapter.py` | Create | Tests for Hermes adapter request/response |

### Itzel repo (`~/repos/itzel`)

| File | Action | Responsibility |
|------|--------|----------------|
| `intent_engine.py` | Modify | Add Hermes intents to INTENT_TO_SKILL, fast-path keywords, routing score overlay |
| `orchestrator.py` | Modify | Route all dispatch through Weave, remove direct elif branches |
| `weave_dispatch.py` | Modify | Add hermes to TOOL_TO_PROVIDER mapping |
| `itzel.yaml` | Modify | Register hermes as tool |
| `tests/test_intent_hermes.py` | Create | Tests for new Hermes intent classification |

---

## Task 1: Skill and Feedback Pydantic Schemas

**Files:**
- Create: `~/repos/weave/src/weave/schemas/skill.py`
- Create: `~/repos/weave/src/weave/schemas/feedback.py`
- Create: `~/repos/weave/tests/test_skill_schema.py`

- [ ] **Step 1: Write failing tests for SkillDefinition model**

```python
# tests/test_skill_schema.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/repos/weave && python -m pytest tests/test_skill_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'weave.schemas.skill'`

- [ ] **Step 3: Implement skill schema**

```python
# src/weave/schemas/skill.py
"""Skill registry schemas — provider-agnostic routing recipes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class ProviderScore(BaseModel):
    """Per-provider performance metrics for a skill."""

    invocations: int = 0
    successes: int = 0
    avg_ms: int = 0
    score: float = 0.5


class SkillMetrics(BaseModel):
    """Aggregated metrics across all providers for a skill."""

    invocations: int = 0
    successes: int = 0
    failures: int = 0
    avg_duration_ms: int = 0
    by_provider: dict[str, ProviderScore] = Field(default_factory=dict)


class HealingLogEntry(BaseModel):
    """Record of a single self-healing event."""

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    trigger: str
    action: str
    outcome: str
    duration_ms: int = 0


class SkillStrategy(BaseModel):
    """Routing and recovery strategy for a skill."""

    primary_provider: str
    fallback_providers: list[str] = Field(default_factory=list)
    context_injection: str = ""
    timeout_ms: int = 30000
    max_retries: int = 2


class SkillDefinition(BaseModel):
    """A Weave-native skill — a routing recipe for an intent."""

    name: str
    version: str = "1"
    description: str = ""
    intents: list[str] = Field(default_factory=list)
    strategy: SkillStrategy
    metrics: SkillMetrics = Field(default_factory=SkillMetrics)
    healing_log: list[HealingLogEntry] = Field(default_factory=list)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
```

- [ ] **Step 4: Write failing tests for FeedbackRecord model**

Append to `tests/test_skill_schema.py`:

```python
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
```

- [ ] **Step 5: Implement feedback schema**

```python
# src/weave/schemas/feedback.py
"""Feedback ledger schemas — invocation outcomes and routing scores."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class HealingDetail(BaseModel):
    """Details of a healing attempt within a feedback record."""

    used: bool = False
    attempts: int = 0
    original_failure: str = ""
    fallbacks: list[dict[str, Any]] = Field(default_factory=list)


class FeedbackRecord(BaseModel):
    """One invocation outcome in the feedback ledger."""

    id: str = Field(default_factory=lambda: f"fb_{uuid.uuid4().hex[:12]}")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    session_id: str = ""
    intent: str
    intent_confidence: float = 0.0
    intent_source: str = ""
    provider: str
    routing_source: str = "static"
    skill_used: str = ""
    task_preview: str = ""
    outcome: str  # "success" | "failure" | "healed" | "timeout"
    duration_ms: int
    quality_gate: str = ""
    security_findings: list[str] = Field(default_factory=list)
    healing: HealingDetail = Field(default_factory=HealingDetail)
    context_injection: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0


class RoutingScores(BaseModel):
    """Aggregated intent -> provider -> score mapping."""

    scores: dict[str, dict[str, float]] = Field(default_factory=dict)
    last_updated: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
```

- [ ] **Step 6: Run all schema tests to verify they pass**

Run: `cd ~/repos/weave && python -m pytest tests/test_skill_schema.py -v`
Expected: All 7 tests PASS

- [ ] **Step 7: Commit**

```bash
cd ~/repos/weave
git add src/weave/schemas/skill.py src/weave/schemas/feedback.py tests/test_skill_schema.py
git commit -m "feat(schemas): add skill registry and feedback ledger Pydantic models"
```

---

## Task 2: Skill Registry CRUD

**Files:**
- Create: `~/repos/weave/src/weave/core/skills.py`
- Create: `~/repos/weave/tests/test_skills.py`

- [ ] **Step 1: Write failing tests for skill registry**

```python
# tests/test_skills.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/repos/weave && python -m pytest tests/test_skills.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'weave.core.skills'`

- [ ] **Step 3: Implement skill registry**

```python
# src/weave/core/skills.py
"""Skill registry — CRUD for .harness/skills/ routing recipes."""

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/repos/weave && python -m pytest tests/test_skills.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ~/repos/weave
git add src/weave/core/skills.py tests/test_skills.py
git commit -m "feat(core): add skill registry CRUD with metrics tracking"
```

---

## Task 3: Feedback Ledger and Score Computation

**Files:**
- Create: `~/repos/weave/src/weave/core/feedback.py`
- Create: `~/repos/weave/tests/test_feedback.py`

- [ ] **Step 1: Write failing tests for feedback ledger**

```python
# tests/test_feedback.py
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
    assert score == 0.5  # < 3 records, neutral


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
    assert score2 == 0.5  # no data for this combo


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/repos/weave && python -m pytest tests/test_feedback.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'weave.core.feedback'`

- [ ] **Step 3: Implement feedback ledger**

```python
# src/weave/core/feedback.py
"""Feedback ledger — invocation outcomes, score computation, routing scores."""

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/repos/weave && python -m pytest tests/test_feedback.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ~/repos/weave
git add src/weave/core/feedback.py tests/test_feedback.py
git commit -m "feat(core): add feedback ledger with score computation"
```

---

## Task 4: Self-Healing Engine

**Files:**
- Create: `~/repos/weave/src/weave/core/healing.py`
- Create: `~/repos/weave/tests/test_healing.py`

- [ ] **Step 1: Write failing tests for healing engine**

```python
# tests/test_healing.py
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
from weave.schemas.activity import ActivityStatus


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
        exit_code=0,
        stdout="Fallback succeeded",
        stderr="",
        structured=None,
        duration=11200,
        files_changed=[],
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/repos/weave && python -m pytest tests/test_healing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'weave.core.healing'`

- [ ] **Step 3: Implement healing engine**

```python
# src/weave/core/healing.py
"""Self-healing engine — fallback retry on provider failure."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from weave.schemas.skill import SkillDefinition, HealingLogEntry

logger = logging.getLogger(__name__)


@dataclass
class HealingResult:
    healed: bool
    fallback_provider: str | None = None
    invoke_result: Any = None
    healing_log_entry: HealingLogEntry | None = None
    attempts: int = 0
    fallback_details: list[dict] = field(default_factory=list)


def attempt_healing(
    failure_reason: str,
    skill: SkillDefinition,
    task: str,
    working_dir: Path,
    session_id: str,
) -> HealingResult:
    """
    Try fallback providers from skill.strategy.fallback_providers.
    Returns HealingResult indicating whether recovery succeeded.
    """
    fallbacks = skill.strategy.fallback_providers
    if not fallbacks:
        logger.info("No fallback providers for skill %s", skill.name)
        return HealingResult(healed=False)

    fallback_details = []
    for provider in fallbacks:
        logger.info(
            "Healing %s: trying fallback provider %s", skill.name, provider
        )
        try:
            invoke_result = _invoke_fallback(
                provider=provider,
                task=task,
                working_dir=working_dir,
                session_id=session_id,
                timeout=skill.strategy.timeout_ms // 1000,
            )
        except Exception as exc:
            logger.warning("Fallback %s raised: %s", provider, exc)
            fallback_details.append({
                "provider": provider,
                "outcome": "error",
                "error": str(exc),
            })
            continue

        if invoke_result.exit_code == 0 and invoke_result.stdout.strip():
            log_entry = HealingLogEntry(
                trigger=failure_reason,
                action=f"fallback to {provider}",
                outcome="success",
                duration_ms=int(invoke_result.duration),
            )
            fallback_details.append({
                "provider": provider,
                "outcome": "success",
                "duration_ms": int(invoke_result.duration),
            })
            return HealingResult(
                healed=True,
                fallback_provider=provider,
                invoke_result=invoke_result,
                healing_log_entry=log_entry,
                attempts=len(fallback_details),
                fallback_details=fallback_details,
            )

        fallback_details.append({
            "provider": provider,
            "outcome": "failure",
            "exit_code": invoke_result.exit_code,
        })

    logger.warning("All fallbacks exhausted for skill %s", skill.name)
    return HealingResult(
        healed=False,
        attempts=len(fallback_details),
        fallback_details=fallback_details,
    )


def _invoke_fallback(
    provider: str,
    task: str,
    working_dir: Path,
    session_id: str,
    timeout: int = 300,
) -> Any:
    """Invoke a fallback provider through Weave's runtime.

    Imported lazily to avoid circular imports with runtime.py.
    """
    from weave.core.invoker import invoke_provider
    from weave.core.registry import get_registry

    registry = get_registry()
    contract = registry.get(provider)
    return invoke_provider(
        contract=contract,
        task=task,
        session_id=session_id,
        working_dir=working_dir,
        timeout=timeout,
        registry=registry,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/repos/weave && python -m pytest tests/test_healing.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ~/repos/weave
git add src/weave/core/healing.py tests/test_healing.py
git commit -m "feat(core): add self-healing engine with fallback provider retry"
```

---

## Task 5: Hermes Provider Adapter

**Files:**
- Create: `~/repos/weave/.harness/providers/hermes.contract.json`
- Create: `~/repos/weave/.harness/providers/hermes.sh`
- Create: `~/repos/weave/.harness/providers/hermes_adapter.py`
- Create: `~/repos/weave/tests/test_hermes_adapter.py`

- [ ] **Step 1: Write failing test for adapter request/response**

```python
# tests/test_hermes_adapter.py
import json
import pytest
from pathlib import Path


def test_hermes_contract_valid():
    contract_path = (
        Path(__file__).resolve().parents[1]
        / ".harness"
        / "providers"
        / "hermes.contract.json"
    )
    assert contract_path.exists(), f"Missing {contract_path}"
    contract = json.loads(contract_path.read_text())

    assert contract["name"] == "hermes"
    assert contract["adapter_runtime"] == "bash"
    assert contract["adapter"] == "hermes.sh"
    assert contract["capability_ceiling"] == "external-network"
    assert "tool-use" in contract["declared_features"]


def test_hermes_adapter_script_exists():
    adapter_path = (
        Path(__file__).resolve().parents[1]
        / ".harness"
        / "providers"
        / "hermes.sh"
    )
    assert adapter_path.exists()
    import stat
    assert adapter_path.stat().st_mode & stat.S_IEXEC


def test_hermes_adapter_python_exists():
    adapter_path = (
        Path(__file__).resolve().parents[1]
        / ".harness"
        / "providers"
        / "hermes_adapter.py"
    )
    assert adapter_path.exists()
    content = adapter_path.read_text()
    assert "AIAgent" in content
    assert "skip_memory=True" in content
    assert "quiet_mode=True" in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/repos/weave && python -m pytest tests/test_hermes_adapter.py -v`
Expected: FAIL with `AssertionError: Missing .harness/providers/hermes.contract.json`

- [ ] **Step 3: Create hermes.contract.json**

```json
{
  "contract_version": "1",
  "name": "hermes",
  "display_name": "Hermes Agent",
  "adapter": "hermes.sh",
  "adapter_runtime": "bash",
  "capability_ceiling": "external-network",
  "protocol": {
    "request_schema": "weave.request.v1",
    "response_schema": "weave.response.v1"
  },
  "declared_features": [
    "tool-use",
    "file-edit",
    "shell-exec"
  ],
  "health_check": "hermes --version"
}
```

Write to: `~/repos/weave/.harness/providers/hermes.contract.json`

- [ ] **Step 4: Create hermes.sh adapter wrapper**

```bash
#!/usr/bin/env bash
# Hermes Agent adapter for Weave runtime.
# Receives weave.request.v1 on stdin, returns weave.response.v1 on stdout.
set -euo pipefail
exec python3 "$(dirname "$0")/hermes_adapter.py"
```

Write to: `~/repos/weave/.harness/providers/hermes.sh`
Then: `chmod +x ~/repos/weave/.harness/providers/hermes.sh`

- [ ] **Step 5: Create hermes_adapter.py**

```python
#!/usr/bin/env python3
"""Hermes Agent adapter — translates Weave protocol to AIAgent invocation."""

import json
import os
import sys


def main() -> None:
    request = json.load(sys.stdin)

    task = request.get("task", "")
    context = request.get("context", "")
    timeout = request.get("timeout", 300)

    full_prompt = f"{context}\n\n{task}" if context else task

    # Import AIAgent from hermes-agent repo
    hermes_path = os.environ.get(
        "HERMES_AGENT_PATH",
        os.path.expanduser("~/repos/hermes-agent"),
    )
    sys.path.insert(0, hermes_path)

    try:
        from run_agent import AIAgent

        agent = AIAgent(
            base_url=os.environ.get(
                "OPENROUTER_API_URL", "https://openrouter.ai/api/v1"
            ),
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            model=os.environ.get("HERMES_MODEL", "anthropic/claude-sonnet-4"),
            max_iterations=30,
            quiet_mode=True,
            skip_memory=True,
        )

        result = agent.run_conversation(user_message=full_prompt)

        response = {
            "protocol": "weave.response.v1",
            "exitCode": 0,
            "stdout": result.get("response", ""),
            "stderr": "",
            "structured": {
                "usage": result.get("usage", {}),
                "tool_calls": [
                    t.get("name", "") for t in result.get("tool_calls", [])
                ],
            },
        }
    except Exception as exc:
        response = {
            "protocol": "weave.response.v1",
            "exitCode": 1,
            "stdout": "",
            "stderr": str(exc),
            "structured": None,
        }

    json.dump(response, sys.stdout)


if __name__ == "__main__":
    main()
```

Write to: `~/repos/weave/.harness/providers/hermes_adapter.py`

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd ~/repos/weave && python -m pytest tests/test_hermes_adapter.py -v`
Expected: All 3 tests PASS

- [ ] **Step 7: Commit**

```bash
cd ~/repos/weave
git add .harness/providers/hermes.contract.json .harness/providers/hermes.sh .harness/providers/hermes_adapter.py tests/test_hermes_adapter.py
git commit -m "feat(providers): add Hermes Agent adapter and contract"
```

---

## Task 6: Wire Feedback and Healing into Weave Runtime

**Files:**
- Modify: `~/repos/weave/src/weave/core/runtime.py`

This task wires the feedback hook and healing engine into the existing post-invoke stage of the runtime pipeline.

- [ ] **Step 1: Add feedback_hook import and function to runtime.py**

At the top of `runtime.py`, add imports:

```python
from weave.core.feedback import append_feedback, compute_all_scores, save_routing_scores, load_feedback
from weave.core.skills import load_skill, update_skill_metrics
from weave.core.healing import attempt_healing
from weave.schemas.feedback import FeedbackRecord, HealingDetail
```

- [ ] **Step 2: Add _feedback_and_healing function to runtime.py**

Add this function before `execute()`:

```python
def _feedback_and_healing(
    ctx: PreparedContext,
    invoke_result: InvokeResult | None,
    status: RuntimeStatus,
) -> InvokeResult | None:
    """Post-invoke: record feedback, attempt healing on failure, update routing scores."""
    if invoke_result is None:
        return None

    working_dir = ctx.working_dir

    # Determine outcome
    if status == RuntimeStatus.SUCCESS:
        outcome = "success"
    elif status == RuntimeStatus.TIMEOUT:
        outcome = "timeout"
    else:
        outcome = "failure"

    # Build feedback record
    intent = ctx.metadata.get("intent", "") if ctx.metadata else ""
    skill_name = ctx.metadata.get("skill_used", "") if ctx.metadata else ""

    record = FeedbackRecord(
        session_id=ctx.session_id,
        intent=intent,
        intent_confidence=ctx.metadata.get("intent_confidence", 0.0) if ctx.metadata else 0.0,
        intent_source=ctx.metadata.get("intent_source", "") if ctx.metadata else "",
        provider=ctx.active_provider,
        routing_source=ctx.metadata.get("routing_source", "static") if ctx.metadata else "static",
        skill_used=skill_name,
        task_preview=ctx.task[:100],
        outcome=outcome,
        duration_ms=int(invoke_result.duration),
        context_injection=bool(ctx.metadata.get("context_injection")) if ctx.metadata else False,
    )

    # Attempt healing on failure
    if outcome in ("failure", "timeout") and skill_name:
        try:
            skill = load_skill(skill_name, working_dir)
            healing_result = attempt_healing(
                failure_reason=f"{outcome}: exit_code={invoke_result.exit_code}",
                skill=skill,
                task=ctx.task,
                working_dir=working_dir,
                session_id=ctx.session_id,
            )
            if healing_result.healed:
                record.outcome = "healed"
                record.healing = HealingDetail(
                    used=True,
                    attempts=healing_result.attempts,
                    original_failure=f"{outcome}: exit_code={invoke_result.exit_code}",
                    fallbacks=healing_result.fallback_details,
                )
                record.duration_ms += int(healing_result.invoke_result.duration)

                # Update skill healing log
                if healing_result.healing_log_entry:
                    skill.healing_log.append(healing_result.healing_log_entry)
                    from weave.core.skills import save_skill
                    save_skill(skill, working_dir)

                invoke_result = healing_result.invoke_result
        except FileNotFoundError:
            pass  # No skill definition, skip healing

    # Append feedback record
    append_feedback(record, working_dir)

    # Update skill metrics
    if skill_name:
        try:
            update_skill_metrics(skill_name, record, working_dir)
        except FileNotFoundError:
            pass

    # Recompute routing scores
    try:
        all_records = load_feedback(working_dir)
        scores = compute_all_scores(all_records)
        save_routing_scores(scores, working_dir)
    except Exception:
        pass  # Non-fatal — don't break the pipeline

    return invoke_result
```

- [ ] **Step 3: Wire _feedback_and_healing into execute()**

In the `execute()` function, after the existing `_cleanup()` call (Stage 5) and before `_record()` (Stage 7), add:

```python
    # Stage 5b: Feedback and healing
    invoke_result = _feedback_and_healing(ctx, invoke_result, status) or invoke_result
```

The exact insertion point is after `post_hook_results = _cleanup(...)` and before `activity = _record(...)`.

- [ ] **Step 4: Run existing Weave tests to verify no regressions**

Run: `cd ~/repos/weave && python -m pytest tests/ -v --timeout=60`
Expected: All existing tests PASS (new feedback/healing code only activates when metadata contains intent/skill_used)

- [ ] **Step 5: Commit**

```bash
cd ~/repos/weave
git add src/weave/core/runtime.py
git commit -m "feat(runtime): wire feedback ledger and self-healing into post-invoke stage"
```

---

## Task 7: Weave CLI — Skill Commands

**Files:**
- Modify: `~/repos/weave/src/weave/cli.py`

- [ ] **Step 1: Add skill command group to cli.py**

Add after the existing command groups (e.g., after `providers_group`):

```python
@main.group("skill")
def skill_group():
    """Manage the skill registry."""


@skill_group.command("list")
def skill_list_cmd():
    """List all registered skills with metrics."""
    from weave.core.skills import list_skills

    cwd = Path.cwd()
    skills = list_skills(cwd)
    if not skills:
        click.echo("No skills registered. Use 'weave skill create' to add one.")
        return

    for s in skills:
        score = round(s.metrics.successes / s.metrics.invocations, 2) if s.metrics.invocations > 0 else 0.0
        click.echo(
            f"  {s.name:24s}  provider={s.strategy.primary_provider:12s}  "
            f"invocations={s.metrics.invocations:4d}  score={score:.2f}"
        )


@skill_group.command("show")
@click.argument("name")
def skill_show_cmd(name: str):
    """Show details for a specific skill."""
    from weave.core.skills import load_skill

    try:
        skill = load_skill(name, Path.cwd())
    except FileNotFoundError:
        click.echo(f"Skill not found: {name}")
        raise SystemExit(1)

    click.echo(skill.model_dump_json(indent=2))


@skill_group.command("create")
@click.argument("name")
@click.option("--provider", required=True, help="Primary provider name")
@click.option("--intent", multiple=True, required=True, help="Intent(s) this skill handles")
@click.option("--fallback", multiple=True, help="Fallback provider(s)")
@click.option("--context", default="", help="Context injection text")
def skill_create_cmd(
    name: str,
    provider: str,
    intent: tuple[str, ...],
    fallback: tuple[str, ...],
    context: str,
):
    """Create a new skill definition."""
    from weave.core.skills import save_skill
    from weave.schemas.skill import SkillDefinition, SkillStrategy

    skill = SkillDefinition(
        name=name,
        description=f"Skill for {', '.join(intent)}",
        intents=list(intent),
        strategy=SkillStrategy(
            primary_provider=provider,
            fallback_providers=list(fallback),
            context_injection=context,
        ),
    )
    save_skill(skill, Path.cwd())
    click.echo(f"Created skill: {name}")


@skill_group.command("promote")
@click.argument("name")
def skill_promote_cmd(name: str):
    """Promote a proven skill to Open Brain for cross-project sharing."""
    import json as _json
    from weave.core.skills import load_skill
    from weave.integrations.open_brain import capture_thought

    try:
        skill = load_skill(name, Path.cwd())
    except FileNotFoundError:
        click.echo(f"Skill not found: {name}")
        raise SystemExit(1)

    confidence = round(skill.metrics.successes / skill.metrics.invocations, 2) if skill.metrics.invocations > 0 else 0.0
    if confidence < 0.85 or skill.metrics.invocations < 5:
        click.echo(
            f"Skill {name} not ready for promotion "
            f"(score={confidence:.2f}, invocations={skill.metrics.invocations}). "
            f"Needs score >= 0.85 and >= 5 invocations."
        )
        raise SystemExit(1)

    # Load Open Brain config
    config_path = Path.cwd() / ".harness" / "config.json"
    if not config_path.exists():
        click.echo("No .harness/config.json found.")
        raise SystemExit(1)

    config = _json.loads(config_path.read_text())
    integrations = config.get("integrations", {}).get("open_brain", {})
    ob_url = integrations.get("url", "")
    ob_key = integrations.get("key", "")

    if not ob_url or not ob_key:
        click.echo("Open Brain not configured in .harness/config.json")
        raise SystemExit(1)

    content = f"skill:{name}\n\n{skill.model_dump_json(indent=2)}"
    success = capture_thought(ob_url, ob_key, content)
    if success:
        click.echo(f"Promoted skill {name} to Open Brain")
    else:
        click.echo("Failed to promote to Open Brain")
        raise SystemExit(1)
```

- [ ] **Step 2: Run Weave CLI to verify commands register**

Run: `cd ~/repos/weave && python -m weave.cli skill --help`
Expected: Shows `list`, `show`, `create`, `promote` subcommands

- [ ] **Step 3: Commit**

```bash
cd ~/repos/weave
git add src/weave/cli.py
git commit -m "feat(cli): add weave skill list/show/create/promote commands"
```

---

## Task 8: Itzel Intent Engine — Hermes Intents

**Files:**
- Modify: `~/repos/itzel/intent_engine.py`
- Create: `~/repos/itzel/tests/test_intent_hermes.py`

- [ ] **Step 1: Write failing tests for new Hermes intents**

```python
# tests/test_intent_hermes.py
import pytest
from intent_engine import INTENT_TO_SKILL, FAST_PATH, classify


def test_hermes_intents_registered():
    assert INTENT_TO_SKILL["web_research"] == "hermes"
    assert INTENT_TO_SKILL["browser_automation"] == "hermes"
    assert INTENT_TO_SKILL["scheduled_automation"] == "hermes"
    assert INTENT_TO_SKILL["multi_step_task"] == "hermes"
    assert INTENT_TO_SKILL["voice_transcription"] == "hermes"


def test_existing_intents_unchanged():
    assert INTENT_TO_SKILL["code_generation"] == "claude_code"
    assert INTENT_TO_SKILL["long_doc_analysis"] == "gemini"
    assert INTENT_TO_SKILL["embeddings"] == "local_llm"


def test_fast_path_web_research():
    keywords = [kw for intent, kws in FAST_PATH if intent == "web_research" for kw in kws]
    assert "research" in keywords
    assert "search the web" in keywords


def test_fast_path_image_generation_extended():
    keywords = [kw for intent, kws in FAST_PATH if intent == "image_generation" for kw in kws]
    # Existing + new keywords
    assert "generate an image" in keywords


def test_fast_path_scheduled_automation():
    keywords = [kw for intent, kws in FAST_PATH if intent == "scheduled_automation" for kw in kws]
    assert "schedule" in keywords
    assert "run daily" in keywords
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/repos/itzel && python -m pytest tests/test_intent_hermes.py -v`
Expected: FAIL with `AssertionError: assert 'image_gen' == 'hermes'` (image_generation currently maps to image_gen)

- [ ] **Step 3: Add Hermes intents to INTENT_TO_SKILL**

In `~/repos/itzel/intent_engine.py`, update the `INTENT_TO_SKILL` dict. Add these entries (some replace existing mappings, some are new):

```python
# After the existing entries in INTENT_TO_SKILL, add/replace:
    # Hermes — multi-tool agent
    "web_research": "hermes",
    "browser_automation": "hermes",
    "image_generation": "hermes",  # replaces "image_gen"
    "voice_transcription": "hermes",
    "scheduled_automation": "hermes",
    "multi_step_task": "hermes",
```

- [ ] **Step 4: Add fast-path keywords**

In `~/repos/itzel/intent_engine.py`, append to the `FAST_PATH` list:

```python
    ("web_research", ["research", "search the web", "find online", "look up"]),
    ("browser_automation", ["screenshot", "browse to", "open the website", "scrape"]),
    ("voice_transcription", ["transcribe", "voice memo", "audio file"]),
    ("scheduled_automation", ["schedule", "every morning", "run daily", "cron"]),
    ("multi_step_task", ["research and", "find and then", "search, analyze, and"]),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd ~/repos/itzel && python -m pytest tests/test_intent_hermes.py -v`
Expected: All 5 tests PASS

- [ ] **Step 6: Commit**

```bash
cd ~/repos/itzel
git add intent_engine.py tests/test_intent_hermes.py
git commit -m "feat(intent): add Hermes intents and fast-path keywords for web research, browser, image gen, voice, cron, multi-step"
```

---

## Task 9: Itzel Orchestrator — Route All Dispatch Through Weave

**Files:**
- Modify: `~/repos/itzel/orchestrator.py`
- Modify: `~/repos/itzel/weave_dispatch.py`
- Modify: `~/repos/itzel/itzel.yaml`

- [ ] **Step 1: Add hermes to TOOL_TO_PROVIDER in weave_dispatch.py**

In `~/repos/itzel/weave_dispatch.py`, update the `TOOL_TO_PROVIDER` dict:

```python
TOOL_TO_PROVIDER = {
    "claude_code": "claude-code",
    "gemini": "gemini",
    "hermes": "hermes",
}
```

- [ ] **Step 2: Update is_weave_tool to include hermes**

The `is_weave_tool()` function checks `TOOL_TO_PROVIDER`, so adding hermes to the dict automatically includes it. Verify by reading the function — if it just does `return tool in TOOL_TO_PROVIDER`, no further change needed.

- [ ] **Step 3: Update dispatch_via_weave to pass routing metadata**

In `~/repos/itzel/weave_dispatch.py`, ensure the `routing_metadata` parameter is passed through to `execute()`. The metadata dict should include `intent`, `skill_used`, `intent_confidence`, `intent_source`, and `routing_source` so the feedback hook in Weave can record them.

In `orchestrator.py`, update the call site to pass metadata:

```python
if is_weave_tool(tool):
    routing_metadata = {
        "intent": intent_result.intent,
        "intent_confidence": intent_result.confidence,
        "intent_source": intent_result.classification_method,
        "routing_source": getattr(intent_result, "routing_source", "static"),
        "skill_used": _find_skill_for_intent(intent_result.intent),
    }
    response = dispatch_via_weave(tool, prompt, routing_metadata=routing_metadata)
```

- [ ] **Step 4: Add _find_skill_for_intent helper to orchestrator.py**

```python
def _find_skill_for_intent(intent: str) -> str:
    """Map intent to skill name for feedback tracking."""
    _INTENT_TO_SKILL_NAME = {
        "web_research": "web-research",
        "browser_automation": "browser-automation",
        "image_generation": "image-gen",
        "voice_transcription": "voice-transcription",
        "scheduled_automation": "scheduled-automation",
        "multi_step_task": "multi-step-task",
    }
    return _INTENT_TO_SKILL_NAME.get(intent, "")
```

- [ ] **Step 5: Register hermes in itzel.yaml**

Add to the `tools:` section:

```yaml
  hermes:
    enabled: true
    binary: hermes
    description: "Multi-tool agent: web research, browser automation, image gen, voice, cron"
```

- [ ] **Step 6: Add routing score overlay to Intent Engine**

In `~/repos/itzel/intent_engine.py`, add routing score loading at the end of `classify()`, before the return:

```python
    # Dynamic routing override from learned scores
    try:
        import json as _json
        from pathlib import Path as _Path
        scores_path = _Path.home() / "repos" / "weave" / ".harness" / "feedback" / "routing-scores.json"
        if scores_path.exists():
            routing_data = _json.loads(scores_path.read_text())
            intent_scores = routing_data.get("scores", {}).get(result.intent, {})
            if intent_scores:
                best = max(intent_scores, key=intent_scores.get)
                best_score = intent_scores[best]
                # Only override if learned score is strong and differs from static
                if best_score > 0.8 and best != result.skill:
                    result.skill = best
                    result.classification_method = f"{result.classification_method}+learned"
    except Exception:
        pass  # Non-fatal — fall back to static routing
```

- [ ] **Step 7: Run Itzel's existing tests to check no regressions**

Run: `cd ~/repos/itzel && python -m pytest tests/ -v --timeout=60 2>/dev/null || echo "Check test output for failures"`
Expected: Existing tests pass. New hermes routing is additive.

- [ ] **Step 8: Commit**

```bash
cd ~/repos/itzel
git add orchestrator.py weave_dispatch.py itzel.yaml intent_engine.py
git commit -m "feat(dispatch): route hermes through Weave, pass routing metadata, add learned score overlay"
```

---

## Task 10: Seed Initial Skills

**Files:**
- Create: `~/repos/weave/.harness/skills/registry.json`
- Create: `~/repos/weave/.harness/skills/web-research.skill.json`
- Create: `~/repos/weave/.harness/skills/image-gen.skill.json`
- Create: `~/repos/weave/.harness/skills/browser-automation.skill.json`
- Create: `~/repos/weave/.harness/skills/scheduled-automation.skill.json`
- Create: `~/repos/weave/.harness/skills/multi-step-task.skill.json`
- Create: `~/repos/weave/.harness/skills/voice-transcription.skill.json`

- [ ] **Step 1: Create .harness/skills/ directory**

```bash
mkdir -p ~/repos/weave/.harness/skills
```

- [ ] **Step 2: Create seed skill definitions**

Use `weave skill create` CLI or write directly. Create each skill:

```bash
cd ~/repos/weave
python -m weave.cli skill create web-research --provider hermes --intent web_research --intent multi_step_task --fallback claude-code --fallback gemini --context "Use at least 3 sources and cross-reference claims before synthesizing."
python -m weave.cli skill create image-gen --provider hermes --intent image_generation --context "Generate high-quality images using FAL.ai."
python -m weave.cli skill create browser-automation --provider hermes --intent browser_automation --fallback claude-code --context "Use Browserbase for remote browser execution with stealth mode."
python -m weave.cli skill create scheduled-automation --provider hermes --intent scheduled_automation --context "Use Hermes cron scheduler for recurring tasks."
python -m weave.cli skill create multi-step-task --provider hermes --intent multi_step_task --fallback claude-code --fallback gemini --context "Chain multiple tools as needed. Break complex tasks into steps."
python -m weave.cli skill create voice-transcription --provider hermes --intent voice_transcription --context "Use Whisper for transcription. Support local, Groq, and OpenAI providers."
```

- [ ] **Step 3: Verify skills are registered**

Run: `cd ~/repos/weave && python -m weave.cli skill list`
Expected: Shows 6 skills with provider=hermes and invocations=0

- [ ] **Step 4: Commit**

```bash
cd ~/repos/weave
git add .harness/skills/
git commit -m "feat(skills): seed initial Hermes skill definitions for web research, image gen, browser, cron, multi-step, voice"
```

---

## Task 11: End-to-End Integration Test

**Files:**
- Create: `~/repos/weave/tests/test_integration_hermes.py`

- [ ] **Step 1: Write integration test that exercises the full pipeline**

```python
# tests/test_integration_hermes.py
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
```

- [ ] **Step 2: Run integration tests**

Run: `cd ~/repos/weave && python -m pytest tests/test_integration_hermes.py -v`
Expected: All 3 tests PASS

- [ ] **Step 3: Run full test suite**

Run: `cd ~/repos/weave && python -m pytest tests/ -v --timeout=60`
Expected: All tests PASS (existing + new)

- [ ] **Step 4: Commit**

```bash
cd ~/repos/weave
git add tests/test_integration_hermes.py
git commit -m "test: add end-to-end integration tests for feedback loop, healing, and promotion"
```

---

## Summary

| Task | Description | Repo | Files |
|------|-------------|------|-------|
| 1 | Skill + Feedback Pydantic schemas | Weave | 3 files |
| 2 | Skill registry CRUD | Weave | 2 files |
| 3 | Feedback ledger + score computation | Weave | 2 files |
| 4 | Self-healing engine | Weave | 2 files |
| 5 | Hermes provider adapter | Weave | 4 files |
| 6 | Wire into Weave runtime | Weave | 1 file |
| 7 | CLI skill commands | Weave | 1 file |
| 8 | Intent Engine Hermes intents | Itzel | 2 files |
| 9 | Orchestrator Weave consolidation | Itzel | 4 files |
| 10 | Seed initial skills | Weave | 7 files |
| 11 | Integration tests | Weave | 1 file |
