# MAR-139 Design: File Revert on Hard-Deny

- **Date:** 2026-04-10
- **Status:** Approved
- **Linear:** [MAR-139](https://linear.app/martymanny/issue/MAR-139)
- **Milestone:** Phase 2 — Runtime Discipline
- **Scope:** When the security scan hard-denies an invocation (mvp/enterprise phase), roll back all files the invocation changed by consulting git state. Populates the previously-always-empty `SecurityResult.files_reverted`.

## Context

Phase 1 shipped `SecurityResult.files_reverted` as a schema field but the runtime always sets it to `[]`. Today when `_security_scan` returns `action_taken="denied"`, the runtime records the denial and flags the session as `RuntimeStatus.DENIED`, but the offending files remain on disk. An operator running in mvp phase sees the denial in the logs, but still has to manually clean up whatever the adapter wrote.

MAR-139 closes that gap. On hard-deny, the runtime rolls back everything the denied invocation wrote, using git as the baseline.

## Architecture

### Pipeline extension

The 6-stage runtime pipeline becomes 7 stages:

```
prepare -> policy_check -> invoke -> security_scan -> cleanup -> revert -> record
```

- **`_security_scan` stays pure.** It reads files and returns a `SecurityResult` decision. No filesystem mutations.
- **`_cleanup` stays unchanged.** Runs post-invoke hooks. Does not touch files.
- **`_revert` is new.** Inspects the `SecurityResult`. If `action_taken == "denied"`, reverts all files in `invoke_result.files_changed`. Populates `SecurityResult.files_reverted` in place.
- **`_record` is unchanged.** Reads the now-populated `SecurityResult` and writes the activity record.

### Control flow in `execute()`

```python
# after invoke returns...
if invoke_result.exit_code == 0:
    security_result = _security_scan(ctx, invoke_result)
    post_hook_results = _cleanup(ctx, invoke_result)
    _revert(ctx, invoke_result, security_result)  # mutates security_result.files_reverted
    # determine final status (SUCCESS / FLAGGED / DENIED) based on security_result.action_taken
```

`_revert` runs only when:
1. `security_result is not None` (i.e. invoke succeeded and scan ran), AND
2. `security_result.action_taken == "denied"`.

If invoke failed with a non-zero exit code or timed out, `security_result` is `None` and `_revert` is never called — there is nothing to judge. In sandbox phase, findings are downgraded to `"flagged"` (via `resolve_action`) and no revert happens. In mvp/enterprise with a denied finding, revert runs and the final status is `RuntimeStatus.DENIED`.

## Snapshot Strategy

### Pre-invoke untracked snapshot

`PreparedContext` gains one new field:

```python
@dataclass
class PreparedContext:
    # ... existing fields ...
    pre_invoke_untracked: set[str]   # snapshot of untracked files before invoke
```

`prepare()` populates this by running `git ls-files --others --exclude-standard` inside `working_dir` before the invoke stage starts. If the directory is not a git repo (or git is unavailable), the set is empty.

### Why a snapshot is required

The invoker's `files_changed` list includes both tracked-and-modified files AND untracked files (via `git ls-files --others`). That means a file that was untracked BEFORE the invocation will appear in `files_changed` just by being picked up by the `--others` query. If the revert logic blindly deletes untracked files, it would delete pre-existing user work.

The snapshot lets `_revert` distinguish:
- **Untracked and pre-existing** (in snapshot) → skip, do not touch
- **Untracked and new** (not in snapshot) → `rm` because the invocation created it

## Revert Logic

### Per-file classification

`_revert(ctx, invoke_result, security_result)` processes each file in `invoke_result.files_changed` with the following decision tree:

```
for rel in invoke_result.files_changed:
    if path escapes working_dir:
        skip (never mutate outside working_dir)
        continue

    if git cat-file -e HEAD:<rel>:     # tracked at HEAD
        git checkout HEAD -- <rel>     # restore content
        files_reverted.append(rel)
    else:                               # not tracked at HEAD
        if rel in ctx.pre_invoke_untracked:
            skip (pre-existing user work)
        else:
            rm <rel>                    # created by this invocation
            files_reverted.append(rel)
```

### Scope: all files_changed, not just flagged

When `action_taken == "denied"`, the revert applies to **every** file in `invoke_result.files_changed`, not just the subset flagged by `SecurityResult.findings`. Rationale:

1. The unit of judgment is the invocation, not the file. If any finding denies the run, the whole invocation is suspect.
2. A malicious adapter can stage attacks across files — write a flagged payload plus an unflagged loader. Partial revert leaves the loader in place for future exploitation.
3. The `DENIED` status already communicates "this run is rejected"; operators will expect that rejection to extend to all side effects.
4. In mvp/enterprise, losing good work from a denied run is recoverable (re-run); letting a malicious staging sneak through is not.

### Gated on phase

`_revert` is a no-op unless `security_result.action_taken == "denied"`. In sandbox phase, `resolve_action` downgrades deny to warn, so `action_taken` resolves to `"flagged"` and no revert happens. This preserves sandbox's experiment-friendly semantics.

## Error Handling

Revert is **best-effort, non-fatal**. Individual file failures are logged and skipped; the pipeline continues. Specifically:

1. **Path escapes `working_dir`** — skipped entirely. We never mutate anything outside the working directory. (This case is already flagged by `check_write_deny`, so the file is in `files_changed` with a deny finding, but `_revert` refuses to act on it.)
2. **Working directory is not a git repo** — `_revert` is a no-op, logged as a warning. `files_reverted = []`. Status remains `DENIED`. Operators running mvp/enterprise without git are taking on that risk.
3. **`git checkout HEAD -- <file>` fails** (e.g., permission denied, sparse-checkout exclusion) — log, skip that file, continue with the next. The file is NOT added to `files_reverted`.
4. **`rm <file>` fails** — log, skip, continue. Not added to `files_reverted`.
5. **`git cat-file` fails to execute** — treat the file as "not tracked at HEAD" (conservative), apply the untracked-file branch.

**What revert never does:**
- Does not mutate the git index (`git reset`, `git stash`, index editing)
- Does not touch files outside `invoke_result.files_changed`
- Does not run in sandbox phase (never on `"flagged"` status)
- Does not raise exceptions into the pipeline — all errors are caught and logged

### `SecurityResult.files_reverted` semantics

The field is a list of relative path strings that were successfully reverted. An empty list means one of:

- No denial occurred (the expected common case)
- Denial occurred but the directory is not a git repo
- Denial occurred but all per-file revert attempts failed
- Denial occurred on a file that was pre-existing untracked (skipped by design)

The combination `action_taken == "denied"` with `files_reverted == []` signals to operators that a denial occurred but the cleanup may be incomplete — investigate manually.

## Backwards Compatibility

- `PreparedContext.pre_invoke_untracked` is a new field with a default of an empty set when git is unavailable. All existing call sites (tests, runtime entrypoint, itzel dispatch) that construct `PreparedContext` go through `prepare()`, which populates the field. No construction sites need updates.
- `SecurityResult.files_reverted` already exists from Phase 1. The field's type and default (`list[str]` with `default_factory=list`) are unchanged. Only the runtime behavior that populates it changes.
- All 95 existing tests (91 Phase 1 + 4 MAR-140) must pass unchanged.
- `test_execute_denies_write_deny_in_mvp` currently asserts `status == DENIED` but does not inspect filesystem state or `files_reverted`. After MAR-139 ships, it still passes — the status is still `DENIED`, and the asserted fields are unchanged. Strengthening that test with a revert assertion is optional, not required.

## Tests

Five new integration tests in `tests/test_runtime.py`. No unit tests in `test_security.py` are needed — the security scan logic is unchanged.

### 1. `test_execute_reverts_untracked_file_on_hard_deny`

- Phase: `mvp`
- Setup: git init, commit seed file
- Adapter writes `credentials.json` (matches default deny list, untracked)
- Expected: `result.status == RuntimeStatus.DENIED`, `credentials.json` no longer exists on disk, `result.security_result.files_reverted == ["credentials.json"]`

### 2. `test_execute_reverts_tracked_file_on_hard_deny`

- Phase: `mvp`
- Setup: commit `config.json` with content `'{"version": "original"}'`, add nothing to allow overrides (so the default deny list still catches it)
- Adapter overwrites `config.json` with `'{"version": "tampered"}'`
- Expected: `result.status == RuntimeStatus.DENIED`, `config.json` content restored to `'{"version": "original"}'`, `files_reverted == ["config.json"]`

### 3. `test_execute_reverts_all_files_changed_not_just_flagged`

- Phase: `mvp`
- Setup: git init + seed commit
- Adapter writes two files:
  - `helper.py` — harmless content (not flagged by scanner, not in deny list)
  - `.env` — flagged by deny list
- Expected: `result.status == RuntimeStatus.DENIED`, BOTH files no longer exist, `files_reverted` contains both paths
- Proves: denied invocations roll back the entire work, not just the flagged subset (Q2 invariant)

### 4. `test_execute_preserves_pre_existing_untracked_on_revert`

- Phase: `mvp`
- Setup: git init + seed commit. Create untracked file `credentials.json` BEFORE calling `execute()` (this file is in `pre_invoke_untracked` snapshot AND matches the default deny list)
- Adapter is a no-op (writes nothing)
- Expected: `result.status == RuntimeStatus.DENIED` (because `credentials.json` is still in `files_changed` from the git ls-files query and triggers the deny list), `credentials.json` still exists on disk (protected by snapshot), `files_reverted == []`
- Proves: the `pre_invoke_untracked` snapshot correctly protects pre-existing user work from being deleted even when it triggers a denial (Q3 invariant)

### 5. `test_execute_no_revert_in_sandbox_phase`

- Phase: `sandbox`
- Setup: git init + seed commit
- Adapter writes `.env`
- Expected: `result.status == RuntimeStatus.FLAGGED` (not DENIED, because sandbox downgrades deny to warn), `.env` still exists on disk, `files_reverted == []`
- Proves: revert only fires when `action_taken` resolves to `"denied"`, not `"warn"`/`"flagged"`

## File Map

| File | Action | Role |
|------|--------|------|
| `src/weave/core/runtime.py` | MODIFY | Add `pre_invoke_untracked` to `PreparedContext`, populate in `prepare()`, add `_revert()` stage, wire into `execute()` |
| `tests/test_runtime.py` | MODIFY | Add 5 integration tests |

No other files are touched. `src/weave/schemas/policy.py` already has `SecurityResult.files_reverted`. `src/weave/core/security.py` is untouched (scan remains pure). `src/weave/core/invoker.py` is untouched (invoker stays thin).

## Out of Scope

- Restoring staged-but-uncommitted files to their staged state (revert is HEAD-relative only)
- Mutating `.git/index` in any way
- Operator-facing `disable_revert` config option (deferred to Phase 3 if operators want manual inspection mode)
- Restoring file permissions or symlink metadata beyond what `git checkout` provides natively
- Revert for files modified by pre/post hooks (hooks should not modify files in the first place; this is existing convention)
- Unit tests for `_revert` as a pure function (it is inherently a filesystem-side-effect stage, best tested via the pipeline integration tests)

## Acceptance Criteria

- `_revert` stage exists in `src/weave/core/runtime.py` between `_cleanup` and `_record`
- `PreparedContext.pre_invoke_untracked` is populated in `prepare()` via `git ls-files --others --exclude-standard`
- On `RuntimeStatus.DENIED` with `action_taken == "denied"`, all files in `invoke_result.files_changed` are processed through the per-file classification tree
- Tracked files are restored via `git checkout HEAD -- <file>`
- Untracked files new to this invocation are removed via `rm`
- Untracked files in `pre_invoke_untracked` are preserved (not deleted)
- Path-escape files are skipped
- `SecurityResult.files_reverted` is populated with the successfully-reverted paths
- Revert errors are logged and non-fatal — the run still returns a `RuntimeResult`
- No-op in sandbox phase
- All 5 new tests pass
- All 95 Phase 1 + MAR-140 tests continue to pass unchanged
