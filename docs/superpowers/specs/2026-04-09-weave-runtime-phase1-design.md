# Phase 1 Design: Make Weave the Runtime

- **Date:** 2026-04-09
- **Status:** Approved
- **Scope:** Transform weave from an adapter runner into a governed runtime with security enforcement

## Context

Three independent audits (Claude/Hermes, Gemini/Claw, Codex/OpenClaw) converge on the same finding: weave is an adapter runner, not a governed runtime. The avnet project (113 commits, 33 plans, 12 phases, ~8 hours) proved this — weave was used for `init` and `translate`, but actual agent invocations bypassed the harness entirely. No hooks were enforced, no policy gates existed, no security scanning occurred.

Phase 1 closes this gap by making `runtime.execute()` the single entrypoint for all agent invocations, regardless of caller (CLI, itzel, GSD).

## Architecture

### Runtime Pipeline

Every agent invocation flows through a 6-stage pipeline in `runtime.py`:

```
runtime.execute(task, provider, working_dir, risk_class=None, caller=None)
    |
    +-- 1. prepare()
    |   +-- Load config (3-layer resolution)
    |   +-- Auto-scaffold if .harness/ missing
    |   +-- Load/create session
    |   +-- Assemble context (stable prefix + volatile task)
    |   +-- Resolve provider (default or specified)
    |
    +-- 2. policy_check()
    |   +-- Resolve effective risk class:
    |   |   provider ceiling ^ caller override (lower only)
    |   +-- Evaluate phase-dependent enforcement (sandbox=warn, mvp/enterprise=deny)
    |   +-- Run pre-invoke hooks
    |   +-- Return PolicyResult
    |
    +-- 3. invoke()  [only if policy allows]
    |   +-- Call invoker.invoke_provider()
    |   +-- Capture InvokeResult
    |
    +-- 4. security_scan()  [post-invoke]
    |   +-- Supply chain scanner on files_changed
    |   +-- Write deny list check on files_changed
    |   +-- Per-rule action resolution (phase + rule config)
    |   +-- Return SecurityResult
    |
    +-- 5. cleanup()
    |   +-- Run post-invoke hooks
    |   +-- Revert files if hard-denied by security scan
    |   +-- Log cleanup actions
    |
    +-- 6. record()
        +-- Write enriched ActivityRecord to session JSONL
        +-- Return RuntimeResult
```

### Result Types

```python
@dataclass
class RuntimeResult:
    invoke_result: InvokeResult | None  # None if policy denied
    policy_result: PolicyResult
    security_result: SecurityResult | None  # None if not invoked
    session_id: str
    risk_class: RiskClass
    status: RuntimeStatus  # success | denied | flagged | failed | timeout
```

## Policy Engine

### Risk Classes

Four ordered execution risk classes. Each level includes the capabilities of lower levels.

```python
class RiskClass(str, Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    EXTERNAL_NETWORK = "external-network"
    DESTRUCTIVE = "destructive"
```

### Provider Capability Declarations

Each provider declares its capability ceiling in `.harness/config.json`:

```json
{
  "providers": {
    "claude-code": {
      "command": ".harness/providers/claude-code.sh",
      "enabled": true,
      "capability": "workspace-write"
    },
    "ollama": {
      "command": ".harness/providers/ollama.sh",
      "enabled": true,
      "capability": "read-only"
    }
  }
}
```

Default capability if omitted: `workspace-write` (backwards compatible).

### Phase-Dependent Enforcement

| Phase | Enforcement |
|-------|-------------|
| sandbox | warn (log warnings, don't block) |
| mvp | deny (hard deny violations) |
| enterprise | deny (hard deny violations) |

### Policy Evaluation Flow

1. Determine effective risk class: start with provider ceiling, caller can request lower (never higher)
2. Evaluate against phase enforcement: sandbox warns, mvp/enterprise denies
3. Run pre-invoke hooks (existing hook system): any hook deny stops execution

```python
@dataclass
class PolicyResult:
    allowed: bool
    effective_risk_class: RiskClass
    provider_ceiling: RiskClass
    requested_class: RiskClass | None
    warnings: list[str]
    denials: list[str]
    hook_results: list[HookResult]
```

## Security Scanning

### Supply Chain Scanner

Post-invoke scanner that checks `files_changed` for suspicious patterns. Derived from Hermes Agent audit.

| Rule ID | Description | Severity | Default Action |
|---------|-------------|----------|----------------|
| `pth-injection` | Python `.pth` file additions (auto-execute on import) | critical | deny |
| `base64-exec` | Base64 encoding combined with dynamic code execution | critical | deny |
| `encoded-subprocess` | Subprocess invocation with encoded argument strings | critical | deny |
| `outbound-exfil` | New POST/PUT to external URLs in non-API code | high | warn |
| `unsafe-deserialize` | Unsafe deserialization APIs (pickle, unsafe YAML, marshal) | high | warn |
| `credential-harvest` | Reading `.ssh/`, `.aws/`, `.gnupg/` paths | critical | deny |

Each rule is a dataclass:

```python
@dataclass
class SecurityRule:
    id: str
    description: str
    pattern: str          # regex
    file_glob: str        # which files to scan (default "**/*")
    severity: str         # critical | high | medium
    default_action: str   # deny | warn | log
```

### Write Deny List

Pre-check on `files_changed` against protected paths. Symlink-aware (resolves real paths via `os.path.realpath` before matching).

Default deny list:

```python
WRITE_DENY_LIST = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "credentials.json",
    "config.json",
    ".harness/config.json",
    ".git/config",
]
```

### Phase + Per-Rule Resolution

1. Check rule's configured action (if set in `.harness/config.json` under `security.supply_chain_rules`)
2. Else use rule's default_action
3. Apply phase override: if phase is `sandbox` and action is `deny`, downgrade to `warn`
4. Return `SecurityFinding(rule_id, file, match, action_taken)`

```python
@dataclass
class SecurityResult:
    findings: list[SecurityFinding]
    action_taken: str      # clean | flagged | denied
    files_reverted: list[str]
```

### Security Config

```python
class SecurityConfig(BaseModel):
    supply_chain_rules: dict[str, RuleOverride] = {}
    write_deny_list: list[str] = [".env", ".env.*", "*.pem", "*.key", "id_rsa*", "credentials.json"]
    write_deny_extras: list[str] = []
    write_allow_overrides: list[str] = []
```

## Itzel Convergence

### Changes to `weave_dispatch.py`

Current flow (broken):
```
dispatch_via_weave() -> try weave -> if unavailable -> fall back to direct CLI
```

New flow (governed):
```
dispatch_via_weave() -> ensure_harness() -> runtime.execute()
```

### Auto-Scaffold

`ensure_harness(project_dir)`:
1. Check if `.harness/` exists in `project_dir`
2. If not, call `weave.core.scaffold.scaffold_project()` with auto-detected providers, phase `sandbox`, name from directory
3. Log a system activity record: `type=system, task="auto-scaffold"`
4. Proceed through runtime pipeline

No fallback path. If weave's Python package isn't importable, itzel surfaces the error.

### Import Strategy

Itzel imports weave as a Python package:

```python
from weave.core.runtime import execute as weave_execute
from weave.core.scaffold import scaffold_project
```

In-process — no subprocess overhead, shared session state, direct access to `RuntimeResult`.

### Removals

- The `try/except` fallback to direct dispatch
- Direct subprocess calls to `claude`, `codex`, `gemini`
- Any path that bypasses hooks/logging

### Preserved

- Itzel's routing intelligence (intent -> provider selection)
- Itzel's framework chaining (GSD -> weave -> next stage)
- The `dispatch_to_project()` helper (calls `runtime.execute()` internally)

## Schema Changes

### ActivityRecord (enriched)

New fields added to existing `ActivityRecord` in `schemas/activity.py`:

```python
risk_class: str | None           # "read-only", "workspace-write", etc.
policy_result: dict | None       # serialized PolicyResult
security_findings: list[dict]    # serialized SecurityFindings
approval_status: str | None      # "approved", "denied", "flagged", "warn"
caller: str | None               # "cli", "itzel", "gsd"
runtime_status: str | None       # "success", "denied", "flagged", "failed", "timeout"
```

### WeaveConfig (extended)

```python
class ProviderConfig(BaseModel):
    command: str
    enabled: bool = True
    health_check: str | None = None
    capability: RiskClass = RiskClass.WORKSPACE_WRITE  # new, backwards compatible

class WeaveConfig(BaseModel):
    # ... existing fields unchanged ...
    security: SecurityConfig = SecurityConfig()  # new, safe defaults
```

## Backwards Compatibility

- All new fields have defaults — existing `.harness/config.json` files work unchanged
- Existing `ActivityRecord` fields untouched — new fields are additive
- Providers without `capability` default to `workspace-write`
- Projects without `security` config get the default deny list and scanner rules

## File Map

| File | Action | Role |
|------|--------|------|
| `src/weave/core/runtime.py` | NEW | Pipeline orchestrator |
| `src/weave/core/policy.py` | NEW | Risk classes, phase enforcement |
| `src/weave/core/security.py` | NEW | Supply chain scanner, write deny list |
| `src/weave/schemas/policy.py` | NEW | Enums, dataclasses for policy/security |
| `src/weave/schemas/config.py` | MODIFY | Add SecurityConfig, capability to ProviderConfig |
| `src/weave/schemas/activity.py` | MODIFY | Add governance fields to ActivityRecord |
| `src/weave/cli.py` | MODIFY | Route `weave invoke` through runtime |
| `itzel/weave_dispatch.py` | MODIFY | Remove fallback, add auto-scaffold, import runtime |

## Out of Scope

- Session binding hashes (Phase 2)
- Deterministic context assembly (Phase 2)
- GSD -> weave bridge (Phase 2)
- Provider contract registry (Phase 3)
- Transcript compaction (Phase 3)
- Sandbox enforcement beyond risk classification (Phase 3)

## Tests

- **Runtime pipeline:** happy path, policy deny, security flag, security deny, timeout, cleanup
- **Policy engine:** risk class resolution, phase enforcement, caller override capped at ceiling
- **Security scanner:** each of the 6 rules, write deny list with symlinks, per-rule overrides, phase downgrade
- **Config:** backwards compatibility with existing configs, new fields with defaults
- **Auto-scaffold:** itzel trigger when `.harness/` missing
