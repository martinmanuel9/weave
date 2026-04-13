# Integration Extension Points — Workflow

**Plan:** [`docs/superpowers/plans/2026-04-12-weave-integration-extension-points.md`](../../docs/superpowers/plans/2026-04-12-weave-integration-extension-points.md)

**Spec:** [`.harness/context/spec.md`](../../.harness/context/spec.md)

## Task Execution Order

1. **Task 1** — Enrich HookContext (REQ-1) — `hooks.py`, `runtime.py`
2. **Task 2** — Caller Metadata Passthrough (REQ-2) — `runtime.py` [parallel with Task 1]
3. **Task 3** — Post-Scan Hook Stage (REQ-3) — `config.py`, `runtime.py` [depends on Task 1]
4. **Task 4** — Activity Event Callbacks (REQ-4) — `config.py`, `runtime.py` [independent]
5. **Task 5** — Full Integration Test — `test_runtime.py` [depends on all]

## Parallel Execution

- Tasks 1 + 2 can run in parallel (no shared changes)
- Task 3 depends on Task 1 (uses enriched HookContext)
- Task 4 is independent
- Task 5 is last (integration test)
