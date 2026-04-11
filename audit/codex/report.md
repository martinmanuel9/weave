# OpenClaw Harness Audit

- Date: 2026-04-08
- Origin agent: Codex
- Target repo: `weave`
- Compared systems: `openclaw`, current `weave`, adjacent `itzel` notes where relevant
- Evidence base:
  - OpenClaw runtime/docs/code inspection
  - Weave current Python implementation inspection
  - Itzel repo guidance inspection
  - No live Open Brain MCP context was available in this session, so prior captured thoughts were not directly queried

## Executive Summary

OpenClaw is useful as a harness reference not because it supports many models, but because it treats agent execution as a governed runtime with explicit contracts around context assembly, session identity, tool execution, sandboxing, and extension boundaries.

Current Weave is clean, understandable, and already has the right instincts:

- CLI-first provider model
- provider-specific context translation
- JSONL session logging
- layered config resolution
- Open Brain integration seam

But Weave is still mostly an adapter runner. The biggest gap is that it does not yet enforce stable runtime contracts around approvals, deterministic context assembly, session reuse/invalidation, transcript hygiene, or extension/plugin boundaries. Those are the OpenClaw patterns most worth inheriting.

## What Weave Already Does Well

These should be preserved:

1. Filesystem-first harness shape
   - `.harness/` is easy to inspect, portable, and composable.
   - This matches the right operating model for an internal development harness.

2. Thin provider adapters
   - `src/weave/core/invoker.py` keeps provider invocation simple.
   - This reduces lock-in and keeps the harness aligned with real CLI workflows.

3. Context translation with hand-edit detection
   - `src/weave/core/translate.py` already solves a real problem: one source context, multiple agent-specific projections.
   - The hash guard is a good start for preserving manual edits.

4. Layered config
   - `src/weave/core/config.py` supports defaults -> user -> project -> local.
   - That is the right direction for a harness that will operate across projects and operators.

5. Open Brain seam
   - `src/weave/integrations/open_brain.py` is already a clean memory boundary.
   - This is a better pattern than tightly coupling memory behavior into the core invoker.

## OpenClaw Patterns Worth Inheriting

### 1. Treat the harness as a runtime, not just a launcher

OpenClaw centers execution around a single agent runtime and session model rather than around ad hoc shell calls.

Benefits:

- one place to enforce policy
- one place to shape context
- one place to own retries, compaction, and lifecycle cleanup
- easier observability and debugging

What to inherit in Weave:

- add a runtime layer between CLI command entrypoints and provider adapters
- make that runtime own session state, policy checks, context assembly, execution metadata, and cleanup
- keep adapters thin; move governance into runtime code

OpenClaw references:

- `docs/concepts/agent.md`
- `src/agents/agent-command.ts`
- `src/agents/cli-runner.ts`

### 2. Deterministic context assembly is a first-class requirement

OpenClaw explicitly protects prompt-cache stability and deterministic request assembly. It sorts cache-sensitive inputs and avoids churning older prompt bytes unless necessary.

Benefits:

- lower token cost
- more predictable model behavior
- reproducible debugging
- fewer accidental regressions from ordering changes

What to inherit in Weave:

- define canonical ordering for:
  - translated context parts
  - provider/tool catalogs
  - hook-added context
  - session replay entries
  - git diff / changed-file summaries
- separate stable prefix context from volatile per-turn content
- test that semantically unchanged inputs produce byte-stable payloads

Current Weave risk:

- `translate.py` concatenates files in fixed order, which is good
- but provider invocation payloads, future hook output, and any retrieved context are not yet normalized as a deterministic contract

OpenClaw references:

- `docs/reference/prompt-caching.md`
- `docs/reference/transcript-hygiene.md`
- repo rule in `AGENTS.md` under prompt cache stability

### 3. Session identity and reuse need invalidation rules

OpenClaw does not just reuse sessions; it invalidates reuse when auth profile, auth epoch, system prompt hash, or MCP config hash changes.

Benefits:

- prevents stale or cross-contaminated sessions
- makes provider reuse safe
- reduces hard-to-explain behavior when runtime config changes

What to inherit in Weave:

- define a provider session binding record per harness session
- store compatibility hashes beside the provider session id
- invalidate reuse when any behavior-shaping input changes

Suggested initial invalidation fields for Weave:

- provider id
- adapter script hash
- translated context hash
- harness config hash
- tool catalog hash
- memory/retrieval config hash

OpenClaw references:

- `src/agents/cli-session.ts`
- `src/agents/cli-runner.ts`

### 4. Approval-aware execution should be explicit, not implicit

OpenClaw models execution approvals and denial outcomes directly. It does not assume every command should run just because a model requested it.

Benefits:

- safer internal rollout
- cleaner user trust model
- auditable side effects
- better future support for human-in-the-loop checkpoints

What to inherit in Weave:

- define execution classes: read-only, workspace-write, external-network, destructive
- require policy evaluation before adapter/provider execution and before nested shell actions
- record approval result as structured data, not just stderr text
- standardize user-facing denial messages

Current Weave risk:

- `invoke_provider()` is effectively unrestricted beyond timeout and cwd
- there is no approval or risk classification layer

OpenClaw references:

- `src/agents/exec-approval-result.ts`
- `src/agents/sandbox/config.ts`
- `docs/concepts/agent-workspace.md`

### 5. Sandboxing should be a contract, not a best-effort convention

OpenClaw distinguishes workspace cwd from actual sandbox isolation. It is explicit that cwd alone is not isolation.

Benefits:

- avoids false security assumptions
- supports phased hardening
- makes workspace access policy machine-enforceable

What to inherit in Weave:

- define sandbox modes explicitly:
  - `off`
  - `workspace-read`
  - `workspace-write`
  - `project-write + network`
  - `full-trust`
- make provider adapters declare the minimum sandbox capability they require
- split working directory from allowed filesystem scope in the schema

Current Weave risk:

- `working_dir` is treated as the operating boundary, but subprocesses can still escape it

OpenClaw references:

- `docs/concepts/agent-workspace.md`
- `src/agents/sandbox/config.ts`

### 6. Transcript hygiene and compaction matter early

OpenClaw has both transcript repair/hygiene and persistent compaction semantics. It keeps recent context intact, summarizes older history, and preserves tool-call/result pairing.

Benefits:

- long-running sessions remain usable
- session files stay recoverable
- provider-specific transcript breakage is contained
- summary state becomes part of the system of record

What to inherit in Weave:

- evolve session logs from append-only activity records into typed transcript/history records
- add a repair/read path that tolerates malformed lines and preserves backups
- support explicit compaction summaries with kept tail boundaries
- preserve action/result pairings during summarization

Current Weave gap:

- `src/weave/core/session.py` stores activity, but not enough structured transcript state to resume intelligent work safely

OpenClaw references:

- `docs/reference/session-management-compaction.md`
- `src/agents/compaction.ts`

### 7. Separate public extension contracts from internal implementation

One of OpenClaw's strongest architectural patterns is boundary discipline: plugin SDK contract, plugin registry contract, and laziness around runtime imports.

Benefits:

- internal harness can add providers/tools without core churn
- third-party or team-local extensions do not depend on internals
- startup/import costs stay under control
- future refactors are possible without breaking every integration

What to inherit in Weave:

- define a provider adapter contract formally instead of treating shell scripts as the only interface
- keep discovery/metadata on a light path and execution on a heavy path
- avoid letting every integration import core internals freely
- make manifest/config metadata usable without executing provider code

Suggested concrete split for Weave:

- `weave.schemas.contracts`: public schemas
- `weave.runtime`: internal orchestrator/runtime
- `weave.providers`: provider registry and light metadata
- `weave.providers.runtime`: execution path only
- `weave.integrations`: optional plugin/integration adapters

OpenClaw references:

- `src/plugin-sdk/AGENTS.md`
- `src/plugins/AGENTS.md`

### 8. Cleanup paths need to be first-class

OpenClaw explicitly cleans up failed spawns, bindings, runtime handles, and sessions.

Benefits:

- fewer zombie sessions
- fewer dangling child processes
- less operator confusion after failures
- more reliable retries

What to inherit in Weave:

- add cleanup contracts for failed invocation, timed-out provider runs, interrupted sessions, and partial translation/generation steps
- track whether cleanup is best-effort or required
- log cleanup actions as first-class events

OpenClaw references:

- `src/acp/control-plane/spawn.ts`

## OpenClaw Tradeoffs and Cons

These are real costs, not reasons to reject the model.

1. Complexity cost
   - OpenClaw has many explicit seams because it supports broad runtime scenarios.
   - Weave should inherit the principles, not clone the entire surface area.

2. Documentation and contract maintenance overhead
   - Boundary discipline requires docs, schema checks, and invariant tests.
   - This is worth it, but only after defining a stable core contract.

3. Potential over-engineering risk
   - If Weave adopts compaction, sandboxing, plugin contracts, and approvals all at once, delivery speed will stall.
   - Stage the adoption.

4. Runtime indirection
   - A governed runtime means more wrapper code around direct CLI calls.
   - The trade is operational safety and predictability.

## Best Practices To Adopt For The Internal Harness

Recommended as concrete policy:

1. Define a canonical harness session record
   - include session id, provider binding, hashes of behavior-shaping inputs, timestamps, and cleanup status

2. Normalize all runtime inputs before execution
   - deterministic sort/order rules
   - stable hashes for context, tools, and config

3. Introduce policy gates before side effects
   - read vs write vs network vs destructive
   - approval or allowlist hooks per class

4. Separate context sources by stability
   - stable project context
   - volatile task context
   - retrieved memory
   - runtime-generated observations

5. Make transcripts resumable
   - use typed JSONL records with enough detail to reconstruct a run safely

6. Add explicit invalidation rules for provider session reuse
   - do not reuse sessions across material config/context changes

7. Keep adapters thin and declarative
   - runtime owns orchestration; adapters only translate invocation to provider-specific CLI behavior

8. Keep extension boundaries narrow
   - formal manifest/schema for providers and integrations
   - lazy-load runtime-heavy paths

9. Treat cleanup and failure states as normal states
   - timed out
   - denied
   - interrupted
   - cleanup pending
   - cleanup complete

10. Add invariant tests for the harness contract
   - deterministic context generation
   - session invalidation behavior
   - approval enforcement
   - transcript repair/compaction behavior

## Recommended Adoption Order For Weave

### Phase 1: High-value, low-complexity

- add a runtime session record with binding hashes
- define execution risk classes and approval schema
- make invocation payload assembly deterministic
- enrich JSONL records to capture transcript-style events and failure states

### Phase 2: Structural hardening

- introduce a runtime orchestrator between CLI and adapters
- add formal provider contract/registry boundaries
- add cleanup lifecycle handling
- separate stable context prefix from volatile task content

### Phase 3: Long-running harness support

- add persistent compaction and transcript hygiene
- add sandbox capability schema and enforcement
- add memory retrieval normalization and cache-aware assembly
- add extension/plugin loading discipline

## Suggested Immediate Backlog For Internal Development

1. Add `SessionBinding` schema to Weave with compatibility hashes.
2. Replace raw `invoke_provider()` contract with `prepare -> policy_check -> invoke -> cleanup -> record`.
3. Introduce typed event records in session JSONL instead of only activity records.
4. Add deterministic ordering tests for translated context, provider metadata, and execution payloads.
5. Add a provider manifest/registry layer so adapters are discoverable without executing them.
6. Add a simple approval policy engine before pursuing deeper sandboxing.
7. Keep Open Brain as an integration seam, not a core dependency.

## Bottom Line

The most important inheritance from OpenClaw is disciplined runtime governance:
deterministic context, explicit policy gates, resumable session state, safe session reuse, and narrow extension contracts.

The part not worth copying wholesale is OpenClaw's full breadth. Weave should adopt the control-plane ideas and preserve its current filesystem-first simplicity.
