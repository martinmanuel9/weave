# MAR-143 Design: GSD → Weave Bridge via Session Wrapping

- **Date:** 2026-04-10
- **Status:** Approved
- **Linear:** [MAR-143](https://linear.app/martymanny/issue/MAR-143)
- **Milestone:** Phase 2 — Runtime Discipline
- **Scope:** Wrap GSD `execute-plan` runs as single weave sessions via two new CLI commands (`session-start` and `session-end`). Captures the plan as a unit — cumulative file diff, security scan, policy result — without intercepting individual subagent task calls.

## Context

The original Linear issue assumed GSD's `execute-plan` shells out to `claude --print` via subprocess, and that we could intercept those calls by routing them through `weave invoke`. **That assumption is wrong.** Reading `~/.claude/get-shit-done/workflows/execute-plan.md`:

```
2. Use Task tool with subagent_type="general-purpose":
   Prompt: "Execute plan at .planning/phases/{phase}-{plan}-PLAN.md..."
3. After Task tool returns with agent_id:
   ...
4. Wait for subagent to complete
```

GSD's `execute-plan` is a Claude Code skill markdown file. When invoked, Claude reads the markdown and follows its instructions to spawn a subagent via the **Task tool** — an in-process Claude Code construct, not a subprocess. There is no `claude --print`. There is no shell command for weave to wrap. Weave's `runtime.execute()` is built around subprocess invocation of an adapter script and cannot intercept Task tool calls.

This reality forces a scope reframing. We have three honest options:

- **Per-task interception** — impossible. The Task tool is not a subprocess.
- **Defer MAR-143 entirely** — close it as "scope mismatch with reality" and ship Phase 2 at 4/5.
- **Wrap-the-plan** — instead of intercepting individual tasks, treat the entire plan as a single weave session bracketed by `session-start` and `session-end` calls. Capture the cumulative file diff, run the security scan against the diff, record the plan-level governance outcome.

MAR-143 takes the third option. The cost is per-task granularity loss (weave doesn't see individual subagent tool calls). The win is honest plan-level governance: every GSD plan run produces a real weave session with security scanning over its cumulative changes.

## Architecture

### Pipeline at the bridge

```
GSD execute-plan workflow (existing logic shown alongside new steps):

  1. Read STATE.md, identify plan to execute
  2. Initialize agent tracking (existing)
  3. weave session-start --task "execute plan 03-01"   ← NEW
        → captures pre-state (HEAD SHA, untracked snapshot)
        → writes binding sidecar (MAR-141) and start marker
        → prints session ID to stdout
        → caller stores it for later
  4. Use Task tool to spawn subagent (existing)
  5. Subagent executes plan: makes commits, modifies files
  6. weave session-end --session-id <id>               ← NEW
        → reads marker, computes files_changed via git diff
        → runs security scan over the cumulative diff
        → in mvp/enterprise: revert if denied
        → writes final ActivityRecord
  7. Report completion (existing, modified to surface weave outcome)
```

The subagent's individual tool calls are completely opaque to weave. What weave sees:

- **The plan as a unit** — one session, one final ActivityRecord
- **The cumulative file diff** — committed work + uncommitted modifications + new untracked files, computed by `git diff <start_sha>...HEAD`
- **The security scan result** — the same `_security_scan()` from MAR-140 running against the synthetic `files_changed` list
- **The policy result** — re-evaluated at end-time using the current config
- **The session binding sidecar** — written at start-time, captures the four MAR-141 hashes for drift detection

Per-task governance is explicitly out of scope. Plan-level governance is the deliverable.

### Why a marker file

The bridge has to survive across two separate process invocations. `weave session-start` runs in one shell, prints a session ID, and exits. Arbitrary work happens (the GSD subagent execution). Then `weave session-end` runs in a potentially different shell. There is no shared in-process state.

The marker file `.harness/sessions/<session_id>.start_marker.json` persists exactly the data that session-end needs from session-start: the start-time HEAD SHA (so we can `git diff` against it), the pre-existing untracked file list (so we can subtract them from "new untracked"), the task description (echoed into the final ActivityRecord), and a `git_available` flag (for graceful non-git fallback).

The marker is parallel to the binding sidecar from MAR-141, lives in the same directory, has the same naming pattern, and is also Pydantic-validated.

## `SessionMarker` Schema

New file `src/weave/schemas/session_marker.py`:

```python
"""Session marker schema — start-time state for wrapped session-end calls."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SessionMarker(BaseModel):
    """Persisted start-time state for a wrapped session.

    Written by `weave session-start` and read by `weave session-end` to
    compute the cumulative files_changed for security scanning. Lives at
    `.harness/sessions/<session_id>.start_marker.json` next to the binding
    sidecar.

    The marker captures everything `session-end` needs to compute the diff
    without requiring the start and end commands to run in the same process.
    """
    session_id: str
    start_time: datetime
    git_available: bool
    start_head_sha: str | None
    pre_invoke_untracked: list[str] = Field(default_factory=list)
    task: str
    working_dir: str
```

**Field semantics:**

- `session_id` — matches the binding and JSONL filenames exactly. The marker filename is `<session_id>.start_marker.json`.
- `start_time` — timezone-aware UTC datetime captured at session-start.
- `git_available` — explicit flag, NOT inferred from `start_head_sha is None`. Makes "no git" sessions easy to grep for during audits.
- `start_head_sha` — git SHA of HEAD at start time, or `null` when git is unavailable. If git is available but HEAD doesn't exist (no commits yet), this is the empty tree object SHA `4b825dc642cb6eb9a060e54bf8d69288fbee4904`, which lets `git diff` work against an empty baseline.
- `pre_invoke_untracked` — list of relative paths that were untracked at start time. Used to subtract pre-existing files from the "new untracked" computation at end. Empty when git is unavailable.
- `task` — the operator-supplied task description (e.g., "execute plan 03-01"). Echoed into the final ActivityRecord at session-end.
- `working_dir` — absolute path as a string. Stored for diagnostics; the actual session-end run validates that it's running in the same working dir.

## `core/session_marker.py` Module

New file with three functions, all pure-ish (filesystem I/O but no global state):

### `write_marker(session_id, task, working_dir, sessions_dir) -> SessionMarker`

Captures start-time state and persists a `SessionMarker` to disk.

```python
def write_marker(
    session_id: str,
    task: str,
    working_dir: Path,
    sessions_dir: Path,
) -> SessionMarker:
    """Capture start-time state and persist a SessionMarker.

    Returns the marker (also written to disk). Detects git availability,
    captures HEAD SHA, captures untracked file list. Falls back to
    git_available=False when git rev-parse fails.
    """
```

Implementation:
1. Run `git rev-parse --is-inside-work-tree` to detect git availability. Capture the result.
2. If git is available, run `git rev-parse HEAD` to capture the start SHA. If HEAD doesn't exist (no commits yet), use the empty tree object SHA `4b825dc642cb6eb9a060e54bf8d69288fbee4904` as a baseline that `git diff` can work against.
3. If git is available, run `git ls-files --others --exclude-standard` to capture the untracked snapshot.
4. Construct a `SessionMarker` with all fields populated.
5. Write to `sessions_dir / f"{session_id}.start_marker.json"` via `marker.model_dump_json(indent=2)`.
6. Return the marker.

All git commands have a 10-second timeout and are wrapped in try/except for graceful fallback. Any subprocess error transitions to the `git_available=False` path.

### `read_marker(session_id, sessions_dir) -> SessionMarker | None`

Loads a marker from disk. Returns `None` if the file doesn't exist. Raises on malformed JSON or Pydantic validation errors — a broken marker is an operator-facing error, not silently ignorable.

```python
def read_marker(session_id: str, sessions_dir: Path) -> SessionMarker | None:
    sidecar_path = sessions_dir / f"{session_id}.start_marker.json"
    if not sidecar_path.exists():
        return None
    return SessionMarker.model_validate_json(sidecar_path.read_text())
```

### `compute_files_changed(marker, working_dir) -> list[str]`

Computes the cumulative `files_changed` list since the marker was written.

```python
def compute_files_changed(
    marker: SessionMarker,
    working_dir: Path,
) -> list[str]:
    """Compute the cumulative files_changed list since the marker was written.

    For git-available sessions: combines `git diff <start_sha>...HEAD`
    (committed work since start), `git diff HEAD` (uncommitted modifications),
    and current untracked files minus the pre_invoke_untracked snapshot
    (new untracked).

    For non-git sessions: returns []. Logged as a degraded-enforcement signal.
    """
```

For git-available sessions, three queries merged into a sorted set:

1. **Committed work between start and HEAD:** `git diff --name-only <start_head_sha> HEAD`. Catches all the files modified in commits made during the wrapped execution.
2. **Uncommitted modifications:** `git diff --name-only HEAD`. Catches files modified but not yet committed.
3. **New untracked files:** `git ls-files --others --exclude-standard` minus `marker.pre_invoke_untracked`. Catches new files created but not staged.

The three sources are unioned into a `set[str]`, sorted, and returned as `list[str]` for stable test assertions.

For non-git sessions, returns an empty list immediately.

All three subprocess calls have 30-second timeouts. Failures are non-fatal — a failed query contributes nothing to the result, the function continues with what it has. This matches the best-effort posture of MAR-139's `_revert`.

## CLI Commands

### `weave session-start`

```python
@main.command("session-start")
@click.option("--task", "-t", required=True, help="Task description for the wrapped session")
@click.option("--provider", "-p", default=None, help="Override default provider")
@click.option("--risk-class", default=None,
              type=click.Choice(["read-only", "workspace-write", "external-network", "destructive"]),
              help="Request a specific risk class")
def session_start_cmd(task, provider, risk_class):
    """Start a wrapped session for external execution (e.g., GSD plan)."""
```

Behavior:

1. Calls `runtime.prepare(task=task, working_dir=cwd, provider=provider, caller="external", requested_risk_class=requested)`.
2. `prepare()` already writes the binding sidecar (MAR-141) and assembles context (MAR-142).
3. Calls `session_marker.write_marker(prepared.session_id, task, cwd, sessions_dir)` to capture git state.
4. Writes a `system` ActivityRecord to the JSONL marking session start (so the session has at least one record before session-end appends the final one).
5. Prints the session_id to stdout (so the caller can capture it via shell command substitution).
6. Exit code 0 on success, 2 on policy denial, 1 on configuration errors.

The `caller="external"` value is new — distinct from `cli`, `itzel`, `gsd`. We use `external` because the actual caller (GSD or itzel daemon or future consumer) is opaque to weave at this layer. The CLI command is just "an external process is wrapping work in a weave session." Future iterations could accept `--caller` as an explicit option.

If `prepare()` raises (no `.harness/`, missing provider, etc.), the command exits with a clear error message and non-zero exit code. No marker is written. No session is created. Operators must initialize the harness before wrapping a plan.

### `weave session-end`

```python
@main.command("session-end")
@click.option("--session-id", required=True, help="Session ID returned by session-start")
def session_end_cmd(session_id):
    """Finalize a wrapped session: scan changed files, run security policy, record outcome."""
```

Behavior:

1. Loads the marker via `session_marker.read_marker(session_id, sessions_dir)`. If missing, errors out — operators must call `session-start` first.
2. Reconstructs a `PreparedContext`-like object directly from the marker + current config + freshly-assembled context (NOT via a second `prepare()` call, to avoid creating an orphan binding sidecar). The reconstructed context uses the marker's `session_id` and `pre_invoke_untracked`.
3. Computes `files_changed` via `session_marker.compute_files_changed(marker, working_dir)`.
4. Constructs a synthetic `InvokeResult(exit_code=0, stdout="", stderr="", structured=None, duration=0.0, files_changed=files_changed)`.
5. Calls `_security_scan(ctx, fake_invoke_result)` — the same function MAR-140/MAR-139 use.
6. Determines status from `security_result.action_taken`:
   - `clean` → `RuntimeStatus.SUCCESS`
   - `flagged` → `RuntimeStatus.FLAGGED`
   - `denied` → `RuntimeStatus.DENIED`
7. Re-evaluates policy at end-time via `evaluate_policy(provider_config, requested_class=None, phase=ctx.phase)`. The session binding from session-start captured the start-time config hash; future audits can detect drift by comparing the binding's `config_hash` against the recorded `policy_result`.
8. Calls `_revert(ctx, fake_invoke_result, security_result)` — runs only on hard-deny in mvp/enterprise (MAR-139). Uses the marker's `pre_invoke_untracked` to protect pre-existing user work.
9. Writes a final ActivityRecord via `_record(...)` with the complete governance picture.
10. Prints status to stdout, exits with the same exit code mapping as `weave invoke`:
    - DENIED → 2
    - FLAGGED → 0 (sandbox warning, not a failure)
    - SUCCESS → 0
    - Any unexpected error → 1

### Why reconstruct rather than re-`prepare()`

`_security_scan`, `_revert`, and `_record` all expect a `PreparedContext`. We don't have a real one at session-end (the original `prepare()` happened in a different process). Two options:

1. **Re-call `prepare()`** — this would write a SECOND binding sidecar with a NEW session ID. We'd then have to override `session_id` to match the marker. The orphan binding pollutes `.harness/sessions/`.
2. **Construct directly from marker + config + context_assembly** — manually build a `PreparedContext` instance using `resolve_config()`, `assemble_context()`, the marker's `session_id`, and the marker's `pre_invoke_untracked`.

Option 2 is honest: we're not creating a new session, we're finalizing an existing one. The reconstructed `PreparedContext` has a real `provider_config` (from current config), real `adapter_script` path (from current config), real `context` (freshly assembled), and the marker's `session_id` and `pre_invoke_untracked`. Fields that aren't relevant at session-end (like `task` for the final ActivityRecord, which we pull from the marker) are stitched in directly.

## GSD Skill Markdown Changes

The skill file at `~/.claude/get-shit-done/workflows/execute-plan.md` is the only file outside the weave repo that needs editing. Two surgical bash insertions at well-defined boundaries.

### Insertion 1: After `init_agent_tracking`, before subagent dispatch

New step `weave_session_start`:

```markdown
<step name="weave_session_start" priority="before_subagent">

Wrap the plan execution in a weave session for governance:

\`\`\`bash
WEAVE_SESSION_ID=$(weave session-start --task "execute plan {phase}-{plan}")
echo "$WEAVE_SESSION_ID" > .planning/current-weave-session.txt
\`\`\`

**On policy denial (non-zero exit):** the wrapped session was rejected by
weave's policy engine. Do NOT proceed with subagent execution. Report the
denial to the user and abort the plan.

**On graceful failure (weave not installed, .harness/ missing):**
weave-bridge guidance — log the absence in agent-history.json with
`weave_status: "unavailable"`, proceed without governance wrapping. The
plan still executes, but security scan and policy checks are skipped.

</step>
```

### Insertion 2: After subagent completion + SUMMARY/commit, before final report

New step `weave_session_end`:

```markdown
<step name="weave_session_end" priority="after_subagent">

Finalize the wrapped weave session:

\`\`\`bash
WEAVE_SESSION_ID=$(cat .planning/current-weave-session.txt 2>/dev/null)
if [ -n "$WEAVE_SESSION_ID" ]; then
    weave session-end --session-id "$WEAVE_SESSION_ID"
    rm .planning/current-weave-session.txt
fi
\`\`\`

**On security denial (exit 2):** the cumulative file changes from this plan
were rejected by weave's security scanner. In mvp/enterprise phase, the
denied files have been reverted. Report the denial in the SUMMARY and
flag the plan as failed-by-security. Do NOT mark the plan complete.

**On security flag (exit 0 with flagged status):** in sandbox phase, the
security scanner found issues but downgraded them to warnings. Files are
preserved. Note the warnings in the SUMMARY for operator review.

</step>
```

### What does NOT change in execute-plan.md

- The Task tool subagent dispatch logic — completely untouched
- The agent-history.json tracking flow — additive only (`weave_session_id` field is new, optional)
- The SUMMARY/commit logic — untouched
- Pattern A/B/C routing logic (autonomous / segmented / decision-dependent) — untouched
- The decimal phase handling — untouched

The two new bash blocks are surgical insertions. They depend on `weave` being on PATH and `.harness/` existing in the working directory; both are graceful no-ops when those preconditions aren't met.

## Backwards Compatibility

- All 121 existing tests continue to pass unchanged
- `weave invoke` is unchanged
- `prepare()` is unchanged (binding sidecar writing from MAR-141 still happens)
- `_security_scan`, `_revert`, `_record` are unchanged (reused by session-end via synthetic InvokeResult)
- `_load_context()` was deleted in MAR-142 and remains deleted
- The new CLI commands are additive — `init`, `invoke`, `translate`, `validate`, `status`, `sync` all keep working
- GSD continues to work even when weave is uninstalled (the new bash steps are graceful no-ops in the markdown)
- The marker file is a new artifact in `.harness/sessions/` next to existing JSONL and binding files. Operators using `ls .harness/sessions/` will see one extra file per wrapped session.

## Tests

### Unit tests (`tests/test_session_marker.py` — new file)

1. **`test_write_marker_captures_git_state`** — git init in temp_dir, commit a seed file, create an untracked file, call `write_marker`. Verify `git_available=True`, `start_head_sha` is a 40-char hex SHA, `pre_invoke_untracked` contains the untracked file, and the marker file exists at the expected path.

2. **`test_write_marker_handles_non_git_directory`** — call `write_marker` in a non-git temp_dir. Verify `git_available=False`, `start_head_sha is None`, `pre_invoke_untracked == []`, and the marker file is still written.

3. **`test_read_marker_returns_none_for_missing_file`** — call `read_marker` for a session_id with no marker. Verify it returns `None`.

4. **`test_read_marker_round_trips_all_fields`** — write a marker, read it back, verify all fields match (including `start_time` timezone-awareness).

5. **`test_compute_files_changed_includes_committed_work`** — git init, commit seed, write_marker, then make a new commit modifying a tracked file and adding a new tracked file. Call `compute_files_changed`. Verify the result includes both committed files.

6. **`test_compute_files_changed_includes_uncommitted_modifications`** — write_marker, modify a tracked file without committing. Result includes the modified file.

7. **`test_compute_files_changed_includes_new_untracked_files`** — write_marker, create a new untracked file. Result includes it.

8. **`test_compute_files_changed_excludes_pre_existing_untracked`** — create an untracked file BEFORE write_marker (so it's captured in `pre_invoke_untracked`), do nothing else. Result is empty.

9. **`test_compute_files_changed_returns_empty_for_non_git`** — non-git temp_dir, write_marker, modify some files. Result is empty list.

### CLI integration tests (`tests/test_runtime.py` — additions)

10. **`test_session_start_writes_marker_and_binding`** — using Click's `CliRunner`, run `weave session-start --task "test"` in an initialized harness with a git repo. Verify both the binding sidecar AND the start marker exist on disk. Verify the session_id printed to stdout matches the filenames.

11. **`test_session_end_completes_clean_session`** — run `session-start`, capture session_id, do nothing, run `session-end`. Verify exit code 0, the JSONL contains a final ActivityRecord with `runtime_status: "success"`, and `security_findings == []`.

12. **`test_session_end_detects_committed_denied_file_in_mvp`** — switch harness to mvp phase, run `session-start`, make a commit adding `.env` (default deny list), run `session-end`. Verify exit code 2 (DENIED), the file is reverted (MAR-139 behavior), JSONL ActivityRecord has `runtime_status: "denied"` with a `write-deny-list` finding.

13. **`test_session_end_raises_for_missing_marker`** — run `session-end --session-id nonexistent-uuid` with no prior `session-start`. Expected: non-zero exit, error message about missing marker.

14. **`test_session_end_handles_non_git_directory_gracefully`** — `session-start` in a non-git temp_dir, then `session-end`. Verify exit code 0 (no enforcement happened), `files_changed == []` in the recorded ActivityRecord, `runtime_status: "success"`.

### Regression verification

All 121 existing tests must continue to pass unchanged. The risk surface:
- Existing `weave invoke` integration tests construct `PreparedContext` via `prepare()`. Adding new CLI commands doesn't affect them.
- MAR-141 binding sidecar creation in `prepare()` is reused by `session-start`. The marker file is a NEW file alongside the binding, not a replacement.
- MAR-139 `_revert` is reused by `session-end` via the synthetic `InvokeResult` path. The same git diff machinery applies.

### Expected final test count

- Baseline: 121
- New unit tests (test_session_marker.py): 9
- New integration tests (test_runtime.py): 5
- **Total: 135 passing**

## File Map

| File | Action | Role |
|------|--------|------|
| `src/weave/schemas/session_marker.py` | NEW | `SessionMarker` Pydantic model (7 fields) |
| `src/weave/core/session_marker.py` | NEW | `write_marker`, `read_marker`, `compute_files_changed` |
| `src/weave/cli.py` | MODIFY | Add `session_start_cmd` and `session_end_cmd` (thin wrappers) |
| `tests/test_session_marker.py` | NEW | 9 unit tests |
| `tests/test_runtime.py` | MODIFY | 5 integration tests for the CLI commands |
| `~/.claude/get-shit-done/workflows/execute-plan.md` | MODIFY (out of weave repo) | Two new bash steps wrapping the subagent dispatch |

## Out of Scope

- **Per-task interception** — impossible. The Claude Code Task tool is in-process and not a subprocess weave can wrap. Per-task governance would require either Claude Code itself to expose a tool-call hook API, or for GSD to externalize subagent execution through subprocess calls. Neither is in MAR-143's scope.
- **Wrapping `execute-phase`, `verify-work`, or other GSD workflows** beyond `execute-plan`. Same wrapping pattern can be extended to other workflows in a future task if it proves valuable. Start with the highest-traffic execution path.
- **Pre-task vs per-task hooks** in the wrapped flow. Hooks run as part of `prepare()` at session-start; there's no equivalent per-task hook chain because there are no per-task invocations from weave's perspective.
- **Session marker cleanup/lifecycle** — markers are left in place after `session-end` completes. Phase 3 session lifecycle work can introduce cleanup if needed.
- **A `--caller` CLI option** — hardcoded as `"external"` for now. Future work can accept it.
- **Modifying the binding sidecar to cross-reference the start marker path** — additive future improvement.
- **Session resume via marker** — we have the data (start state persisted), but no current consumer needs it. Defer.
- **Catching subagent failures in real-time** — `session-end` runs after the subagent completes regardless of outcome. If the subagent failed (didn't make commits, crashed), `compute_files_changed` returns whatever was actually changed, and the session is recorded as such. We don't try to detect "subagent crashed" — that's a GSD concern, surfaced through GSD's existing agent-history.json.

## Acceptance Criteria

- `src/weave/schemas/session_marker.py` exists and defines `SessionMarker` with all 7 fields
- `src/weave/core/session_marker.py` exists and exports `write_marker`, `read_marker`, `compute_files_changed`
- `write_marker` captures git state correctly: `git_available=True` with valid SHA when in a git repo, `git_available=False` with `start_head_sha=None` otherwise
- `write_marker` captures untracked files via `git ls-files --others --exclude-standard`
- `read_marker` returns `None` for missing files and raises for malformed files
- `compute_files_changed` for git-available sessions includes committed work, uncommitted modifications, and new untracked files (subtracting pre-existing untracked)
- `compute_files_changed` for non-git sessions returns `[]`
- `weave session-start` writes both the binding sidecar (via `prepare()`) AND the start marker, prints session_id to stdout
- `weave session-end` reads the marker, computes files_changed, runs security scan + revert + record
- `session-end` exit codes match `weave invoke`: DENIED=2, FAILED=1, SUCCESS/FLAGGED=0
- `session-end` raises a clear error for missing markers
- `session-end` handles non-git directories gracefully (no enforcement, session still recorded)
- `caller="external"` is set on the recorded ActivityRecord
- All 9 unit tests in `test_session_marker.py` pass
- All 5 new integration tests in `test_runtime.py` pass
- All 121 pre-existing tests pass unchanged
- Expected final test count: 135 passing
- The GSD `execute-plan.md` modifications are documented in the spec but applied in a separate step (since they live outside the weave repo)
