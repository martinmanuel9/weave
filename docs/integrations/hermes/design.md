# Hermes-Weave Integration: Self-Healing Orchestration Pipeline

**Date:** 2026-04-27
**Author:** Martin Lopez + Claude
**Status:** Approved
**Repos:** weave, itzel, hermes-agent (read-only)

---

## Problem

Itzel is a poly-agent CLI orchestrator that routes tasks to Claude Code, Gemini CLI, Local LLM, and other tools via intent classification. Hermes Agent is a self-improving agent with 40+ built-in tools (web search, browser automation, image generation, voice, cron scheduling). Today these systems are completely disconnected.

The goal is to integrate Hermes as a dispatch target within the `Itzel -> Intent Engine -> Weave` pipeline, add a shared skill registry at the Weave level, and build a feedback loop with self-healing so the system learns and recovers from failures over time.

## Non-Goals

- No changes to Hermes Agent's codebase (it stays upstream, untouched)
- No AutoGen/debate patterns (deferred to MAR-153, MAR-154, MAR-155)
- No Hermes-internal skill sharing (skills are Weave-native, not synced from Hermes)
- No new daemons or background processes

## Architecture

```
User
  |
  v
Itzel (orchestrator.py)
  |
  v
Intent Engine (intent_engine.py)
  |  reads .harness/feedback/routing-scores.json
  |  for dynamic provider selection
  |
  v
Weave (governed dispatch)
  |
  +-- Pre-invoke: assemble context + inject skill.strategy.context_injection
  |
  +-- Invoke: dispatch to provider adapter
  |     +-- Claude Code (.harness/providers/claude-code.sh)
  |     +-- Gemini CLI (.harness/providers/gemini.sh)
  |     +-- Hermes Agent (.harness/providers/hermes.sh) <-- NEW
  |     +-- Local LLM (.harness/providers/local-llm.sh)
  |
  +-- Post-invoke:
  |     +-- Security scan (existing)
  |     +-- Quality gate (existing)
  |     +-- Score outcome -> feedback.jsonl (NEW)
  |     +-- Update skill metrics -> .harness/skills/ (NEW)
  |     +-- Update routing scores -> routing-scores.json (NEW)
  |     +-- On failure -> Self-Healing Engine (NEW)
  |           +-- Retry with fallback provider
  |           +-- Log healing action
  |
  +-- Promote proven skills -> Open Brain (cross-project)
```

All dispatch goes through Weave. The direct `dispatch()` elif branches in `orchestrator.py` become legacy.

---

## Layer 1: Hermes as a Weave Provider

### 1.1 Provider Contract

File: `.harness/providers/hermes.contract.json`

```json
{
  "name": "hermes",
  "display_name": "Hermes Agent",
  "description": "Self-improving agent with 40+ tools: web search, browser automation, image generation, voice, cron scheduling",
  "adapter": { "runtime": "bash", "script": "hermes.sh" },
  "protocol": "weave.request.v1",
  "capabilities": {
    "ceiling": "external-network",
    "features": ["tool-use", "file-edit", "shell-exec", "web-access", "image-gen", "skill-creation"]
  }
}
```

### 1.2 Adapter Script

File: `.harness/providers/hermes.sh` + `.harness/providers/hermes_adapter.py`

The adapter imports `AIAgent` from Hermes directly (Python path insertion to `~/repos/hermes-agent`). This gives structured responses — token usage, tool calls list, full response text — rather than raw stdout.

```python
# hermes_adapter.py (simplified)
from run_agent import AIAgent

agent = AIAgent(
    base_url=os.environ.get("OPENROUTER_API_URL", "https://openrouter.ai/api/v1"),
    api_key=os.environ["OPENROUTER_API_KEY"],
    model=os.environ.get("HERMES_MODEL", "anthropic/claude-sonnet-4"),
    max_iterations=30,
    quiet_mode=True,
    skip_memory=True,  # Weave manages context, not Hermes
)

result = agent.run_conversation(user_message=full_prompt)

# Return structured response for Weave to log
print(json.dumps({
    "stdout": result["response"],
    "exit_code": 0,
    "provider": "hermes",
    "usage": result.get("usage", {}),
    "tool_calls": [t.get("name", "") for t in result.get("tool_calls", [])],
}))
```

Key decisions:
- `skip_memory=True`: Hermes is stateless per invocation. Weave + Open Brain own memory.
- `quiet_mode=True`: No spinner/banner output, clean structured response.
- `max_iterations=30`: Cap tool-calling depth to prevent runaway loops.

### 1.3 New Intent Mappings

File: `itzel/intent_engine.py`

New intents routed to Hermes:

| Intent | Routes to | Why |
|--------|-----------|-----|
| `web_research` | hermes | Exa + Firecrawl + Parallel web search, multi-source synthesis |
| `browser_automation` | hermes | Browserbase integration, stealth mode, CAPTCHA solving |
| `image_generation` | hermes | FAL.ai integration |
| `voice_transcription` | hermes | Whisper (local/Groq/OpenAI), Edge TTS |
| `scheduled_automation` | hermes | Built-in cron scheduler with platform delivery |
| `multi_step_task` | hermes | Tasks requiring 2+ tool categories chained together |

New fast-path keywords:

```python
FAST_PATH.extend([
    ("web_research", ["research", "search the web", "find online", "look up"]),
    ("browser_automation", ["screenshot", "browse to", "open the website", "scrape"]),
    ("image_generation", ["generate an image", "create a picture", "draw", "make an image"]),
    ("voice_transcription", ["transcribe", "voice memo", "audio file"]),
    ("scheduled_automation", ["schedule", "every morning", "run daily", "cron"]),
    ("multi_step_task", ["research and", "find and then", "search, analyze, and"]),
])
```

Existing routing stays unchanged: `code_generation` -> Claude Code, `long_doc_analysis` -> Gemini, etc.

### 1.4 Orchestrator Consolidation

File: `itzel/orchestrator.py`

All dispatch goes through `dispatch_via_weave()`. The direct elif branches (`elif tool == "claude_code": subprocess.run(["claude", ...])`) are removed. `dispatch()` becomes a thin wrapper around Weave.

### 1.5 Config Registration

File: `itzel/itzel.yaml`

```yaml
tools:
  hermes:
    enabled: true
    binary: hermes
    description: "Multi-tool agent: web research, browser automation, image gen, voice, cron"
```

---

## Layer 2: Weave-Native Skill Registry

### 2.1 Directory Structure

```
.harness/
  skills/
    registry.json              # Index of all skills + metrics
    web-research.skill.json    # Individual skill definitions
    image-gen.skill.json
    ...
```

### 2.2 Skill Schema

File: `src/weave/schemas/skill.py` (Pydantic model)

```json
{
  "name": "web-research",
  "version": "1.3",
  "description": "Multi-source web research with cross-reference synthesis",
  "intents": ["web_research", "multi_step_task"],
  "strategy": {
    "primary_provider": "hermes",
    "fallback_providers": ["claude-code", "gemini"],
    "context_injection": "When performing web research, use at least 3 sources and cross-reference claims.",
    "timeout_ms": 30000,
    "max_retries": 2
  },
  "metrics": {
    "invocations": 24,
    "successes": 22,
    "failures": 2,
    "avg_duration_ms": 8500,
    "by_provider": {
      "hermes": { "invocations": 20, "successes": 19, "avg_ms": 8200, "score": 0.94 },
      "claude-code": { "invocations": 4, "successes": 3, "avg_ms": 12000, "score": 0.72 }
    }
  },
  "healing_log": [
    {
      "timestamp": "2026-04-27T14:00:00Z",
      "trigger": "hermes timeout after 30s",
      "action": "fallback to claude-code",
      "outcome": "success",
      "duration_ms": 11200
    }
  ],
  "created_at": "2026-04-20T10:00:00Z",
  "updated_at": "2026-04-27T14:15:00Z"
}
```

Skills are **routing recipes**, not provider artifacts. They describe what works for a given intent, which provider handles it best, what context to inject, and how to recover on failure.

### 2.3 Registry Index

File: `.harness/skills/registry.json`

```json
{
  "version": 1,
  "skills": {
    "web-research": {
      "file": "web-research.skill.json",
      "confidence_score": 0.94,
      "provider": "hermes",
      "intents": ["web_research", "multi_step_task"]
    },
    "image-gen": {
      "file": "image-gen.skill.json",
      "confidence_score": 0.99,
      "provider": "hermes",
      "intents": ["image_generation"]
    }
  },
  "last_updated": "2026-04-27T14:15:00Z"
}
```

### 2.4 Skills Module

File: `src/weave/core/skills.py`

Functions:
- `load_skill(name: str) -> SkillDefinition` - load from `.harness/skills/`
- `save_skill(skill: SkillDefinition) -> None` - write to `.harness/skills/` + update registry
- `update_skill_metrics(name: str, outcome: FeedbackRecord) -> None` - update metrics after invocation
- `list_skills() -> list[SkillSummary]` - list all registered skills
- `get_best_provider(intent: str) -> str | None` - query registry for highest-scoring provider for an intent

### 2.5 CLI Commands

```bash
weave skill list                    # List all registered skills with metrics
weave skill show web-research       # Show skill details
weave skill create web-research     # Create a new skill interactively
weave skill import web-research     # Import proven skill from Open Brain
weave skill promote web-research    # Manually promote skill to Open Brain
```

### 2.6 Open Brain Promotion

When a skill crosses the promotion threshold (confidence_score >= 0.85, invocations >= 5), a post-invoke hook promotes it to Open Brain via `capture_thought()`:

```python
capture_thought(
    title=f"skill:{skill_name}",
    content=json.dumps(skill_definition),
    tags=["weave-skill", "proven", *skill.intents]
)
```

Other projects import with `weave skill import <name>`, which searches Open Brain and copies to local `.harness/skills/`.

---

## Layer 3: Feedback Loop with Self-Healing

### 3.1 Feedback Ledger

File: `.harness/feedback/feedback.jsonl`

One JSON line per invocation:

```json
{
  "id": "fb_20260427_143000_a1b2c3",
  "timestamp": "2026-04-27T14:30:00Z",
  "session_id": "weave-session-uuid",
  "intent": "web_research",
  "intent_confidence": 0.91,
  "intent_source": "vllm",
  "provider": "hermes",
  "routing_source": "learned",
  "skill_used": "web-research",
  "task_preview": "Research recent advances in...",
  "outcome": "success",
  "duration_ms": 8200,
  "quality_gate": "passed",
  "security_findings": [],
  "healing": {
    "used": false,
    "attempts": 0,
    "fallbacks": []
  },
  "context_injection": true,
  "prompt_tokens": 1200,
  "completion_tokens": 3400
}
```

### 3.2 Feedback Schema

File: `src/weave/schemas/feedback.py` (Pydantic model)

- `FeedbackRecord` - single invocation outcome
- `HealingRecord` - healing attempt details (provider, outcome, duration)
- `RoutingScores` - aggregated intent -> provider scores

### 3.3 Routing Scores

File: `.harness/feedback/routing-scores.json`

```json
{
  "web_research": {
    "hermes": 0.94,
    "claude-code": 0.72,
    "gemini": 0.61
  },
  "code_generation": {
    "claude-code": 0.97,
    "hermes": 0.81
  },
  "image_generation": {
    "hermes": 0.99
  }
}
```

### 3.4 Score Computation

File: `src/weave/core/feedback.py`

```python
def compute_score(intent: str, provider: str, records: list[FeedbackRecord]) -> float:
    relevant = [r for r in records if r.intent == intent and r.provider == provider]
    if len(relevant) < 3:
        return 0.5  # insufficient data, neutral score

    # 70% success rate (reliability), 30% speed
    recency_weighted_rate = weighted_success_rate(relevant, decay=0.9)
    median_ms = median([r.duration_ms for r in relevant if r.outcome == "success"])
    duration_factor = 1.0 / (1.0 + median_ms / 30000)

    return round(recency_weighted_rate * 0.7 + duration_factor * 0.3, 3)
```

- 3-invocation minimum prevents cold-start noise
- "healed" outcomes count as success for the skill but penalize the primary provider
- Recency weighting (exponential decay 0.9) so recent performance matters more
- Scores recomputed after every invocation

### 3.5 Intent Engine Routing Override

File: `itzel/intent_engine.py`

```python
def classify(prompt: str) -> IntentResult:
    intent = classify_intent(prompt)           # existing classification

    # Dynamic override from learned routing scores
    scores = load_routing_scores()
    if intent.intent in scores:
        best_provider = max(scores[intent.intent], key=scores[intent.intent].get)
        static_provider = INTENT_TO_SKILL.get(intent.intent)

        if best_provider != static_provider and scores[intent.intent][best_provider] > 0.8:
            intent.skill = best_provider
            intent.routing_source = "learned"

    return intent
```

Dynamic override only activates when the learned score exceeds 0.8, preventing premature switching on limited data.

### 3.6 Self-Healing Engine

File: `src/weave/core/healing.py`

```python
def attempt_healing(
    activity: ActivityRecord,
    skill: SkillDefinition,
    config: dict,
) -> HealingResult:
    """
    Called by post-invoke hook when outcome is FAILURE.
    Returns HealingResult with retry provider + modified context, or gives up.
    """
```

**Healing flow:**
1. Provider fails (non-zero exit, timeout, empty response)
2. Post-invoke hook scores outcome as FAILURE
3. Healing engine checks `skill.strategy`:
   - Has `fallback_providers`? -> retry with next provider
   - Has `max_retries` remaining? -> retry same provider with modified prompt
   - No retries left? -> log failure, return error to user
4. Log healing action to `skill.healing_log`
5. Update `skill.metrics.by_provider` scores

**What counts as failure:**
- Exit code != 0
- Timeout exceeded (`skill.strategy.timeout_ms`)
- Empty or malformed response
- Post-invoke quality gate failure (pytest fails, ruff fails)

**What does NOT trigger healing:**
- Security scan denial (intentional, not retriable)
- User cancellation

### 3.7 Runtime Integration

File: `src/weave/core/runtime.py` (modifications)

The existing 6-stage pipeline gets two additions in the post-invoke stage:

```
Stage 4 (existing): Security scan
Stage 5 (existing): Post-invoke hooks
  NEW: feedback_hook() -> score outcome, write feedback.jsonl, update skill metrics, update routing scores
  NEW: healing_hook() -> on failure, attempt_healing() -> retry with fallback if available
Stage 6 (existing): Record activity
```

Both hooks are built-in (not user-configured). They run after existing hooks so they don't interfere with quality gates.

---

## Files Changed

### Weave repo (`~/repos/weave`)

| File | Change |
|------|--------|
| `src/weave/core/healing.py` | New - self-healing engine |
| `src/weave/core/skills.py` | New - skill registry CRUD |
| `src/weave/core/feedback.py` | New - feedback ledger + score computation |
| `src/weave/schemas/skill.py` | New - SkillDefinition, SkillStrategy, SkillMetrics Pydantic models |
| `src/weave/schemas/feedback.py` | New - FeedbackRecord, HealingRecord, RoutingScores Pydantic models |
| `src/weave/core/runtime.py` | Modified - wire feedback + healing hooks into post-invoke stage |
| `src/weave/core/hooks.py` | Modified - register feedback_hook and healing_hook as built-in post-invoke hooks |
| `src/weave/cli.py` | Modified - add `weave skill list/show/create/import/promote` commands |
| `.harness/providers/hermes.sh` | New - Hermes adapter shell wrapper |
| `.harness/providers/hermes_adapter.py` | New - Python adapter importing AIAgent |
| `.harness/providers/hermes.contract.json` | New - Hermes capability contract |

### Itzel repo (`~/repos/itzel`)

| File | Change |
|------|--------|
| `intent_engine.py` | Modified - add Hermes intents, fast-path keywords, routing score overlay |
| `orchestrator.py` | Modified - all dispatch goes through Weave, remove direct elif branches |
| `itzel.yaml` | Modified - register hermes as tool |

### Hermes Agent repo (`~/repos/hermes-agent`)

No changes. Hermes stays upstream and untouched.

---

## Deferred Work (in Linear backlog)

| Issue | Description |
|-------|-------------|
| MAR-153 | Pre-invoke routing debate for ambiguous intent classification |
| MAR-154 | Post-invoke quality debate for high-stakes task validation |
| MAR-155 | AutoGen GroupChat for multi-provider collaborative tasks |

---

## Success Criteria

1. `hermes` appears as a registered Weave provider (`weave status` shows it)
2. Intent Engine routes `web_research`, `image_generation`, etc. to Hermes
3. Dispatching "research the top 5 competitors to Acme Corp" goes through: Itzel -> Intent Engine (web_research) -> Weave -> Hermes adapter -> structured response
4. `.harness/skills/` contains skill definitions with live metrics
5. `.harness/feedback/feedback.jsonl` records every invocation with outcome
6. `.harness/feedback/routing-scores.json` updates after each invocation
7. When Hermes times out, self-healing retries with fallback provider and logs the recovery
8. After 5+ successful invocations with score >= 0.85, skill auto-promotes to Open Brain
9. Intent Engine's routing overrides static mapping when learned scores are strong (> 0.8)
10. No changes to hermes-agent repo
