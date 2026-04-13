# Integration Extension Points — Specification

## Overview

Add generic, Itzel-unaware extension points to Weave's runtime pipeline so external
systems can receive richer hook context, attach metadata to activity records, and gate
execution at a post-scan stage. These are the "sockets" that Itzel (Phase B) will plug
into — but they must work for any integrator.

## Requirements

- [ ] REQ-1 (P1): **Enrich HookContext** — Add `risk_class`, `files_changed`, `exit_code`,
      `security_findings`, `session_id`, and `provider_contract` fields to `HookContext`.
      Pre-invoke hooks receive all fields except `files_changed`, `exit_code`, and
      `security_findings` (not yet available). Post-invoke hooks receive all fields.

- [ ] REQ-2 (P1): **Caller metadata passthrough** — Add a `metadata: dict[str, Any]`
      parameter to `runtime.execute()` that flows through to `ActivityRecord.metadata`
      (field already exists on schema). External callers (e.g., Itzel routing) can attach
      CJE scores, intent labels, or routing decisions at invoke time.

- [ ] REQ-3 (P1): **Post-scan hook stage** — Add a `post_scan` hook list to `HooksConfig`
      and a corresponding pipeline stage between `_security_scan` and `_cleanup` in
      `execute()`. Post-scan hooks receive the full enriched `HookContext` including
      security findings and files_changed. A deny from a post-scan hook sets
      `RuntimeStatus.DENIED` and triggers `_revert`.

- [ ] REQ-4 (P2): **Activity event callbacks** — Add an `on_activity: list[Callable]`
      field to `WeaveConfig` (not serialized to JSON — runtime-only). After `_record()`
      appends the `ActivityRecord`, call each callback with the record. Failures in
      callbacks are logged but do not fail the pipeline.

## Acceptance Criteria

- [ ] AC-1: A post-invoke hook script receives JSON on stdin that includes `risk_class`,
      `files_changed`, `exit_code`, `session_id`, and `security_findings` keys.

- [ ] AC-2: A pre-invoke hook script receives JSON on stdin that includes `risk_class`,
      `session_id`, and `provider_contract` keys, but NOT `files_changed` or `exit_code`.

- [ ] AC-3: `runtime.execute(task=..., metadata={"cje_score": 0.87})` produces an
      `ActivityRecord` where `record.metadata["cje_score"] == 0.87`.

- [ ] AC-4: A `.harness/config.json` with `hooks.post_scan: ["./hooks/quality-gate.sh"]`
      causes the script to run after security scanning completes. If the script exits
      non-zero, `RuntimeResult.status` is `DENIED` and changed files are reverted.

- [ ] AC-5: Post-scan hooks do NOT run when the provider invocation itself failed
      (non-zero exit code) or timed out — only on successful invocation.

- [ ] AC-6: A callback registered via `config.on_activity` receives the full
      `ActivityRecord` after recording. If the callback raises, the pipeline still
      returns successfully and the error is logged.

- [ ] AC-7: Existing configs with no `post_scan` key or `metadata` parameter continue
      to work identically (backwards compatibility).

- [ ] AC-8: `HookContext.to_dict()` includes all new fields. Fields not yet available
      (e.g., `files_changed` in pre-invoke) serialize as `None` or `[]`.

## Out of Scope

- Itzel-side hook implementations (Phase B, itzel repo)
- Async callback execution (pipeline is synchronous)
- Remote webhooks or HTTP-based callbacks
- Plugin discovery or autoloading
- New CLI commands or flags

## Constraints

- Weave must never import Itzel — dependency flows one direction only
- Backwards-compatible: existing configs without new fields must work unchanged
- Follow existing conventions: Pydantic for schemas, dataclasses for internal types
- `on_activity` callbacks are runtime-only (not serialized to config.json)
- Post-scan hooks use the same `run_hooks()` mechanism as pre/post-invoke hooks
