# MAR-140 Design: `write_allow_overrides` Enforcement

- **Date:** 2026-04-10
- **Status:** Approved
- **Linear:** [MAR-140](https://linear.app/martymanny/issue/MAR-140)
- **Milestone:** Phase 2 — Runtime Discipline
- **Scope:** Activate the dormant `SecurityConfig.write_allow_overrides` field so operators can surgically exempt specific paths from the write deny list.

## Context

Phase 1 declared `SecurityConfig.write_allow_overrides: list[str]` as a Pydantic field but no code reads it. The field is currently annotated `# Phase 2: not yet enforced` in `src/weave/schemas/config.py`.

Operators cannot exempt a legitimate write-target path (e.g., a project that intentionally writes to `config.json`) without removing the pattern from the main deny list entirely, which would disable the protection for every other file.

Phase 2.5 closes this Phase 1 loose end.

## Semantics

### Allow override meaning

A file matching a deny pattern is exempted if and only if its relative path also matches an allow pattern. Allow overrides are exemptions, not replacements — the deny list still runs first; allow only subtracts from the result.

Expressed as a set operation:

```
final_denied = (deny_matches) - (allow_matches)
```

This matches how `.gitignore` negation and most security allowlists work: the control runs first, and exemptions are surgical.

### Scope: write deny list only

Allow overrides apply exclusively to the write deny list (`check_write_deny`). The supply chain scanner (`scan_files` with `DEFAULT_RULES`) is unaffected — an exempted file path can still have its contents flagged.

Rationale: `write_allow_overrides` literally means "I'm okay with this file *path* being written", not "I trust this file's contents". The two concerns answer different questions:

- Write deny list: should this path be written?
- Supply chain scanner: does this content look malicious?

Mixing them would let an operator who wanted to allow writing `config.json` accidentally disable the scanner for it. A future "content trust" concept would be a separate field (`scanner_allowlist` or similar) in a later phase.

### Matching: relative path only, no symlink resolution

Allow patterns use simple `fnmatch` against the relative path as written. No symlink resolution, no basename fallback, no resolved-path matching.

This is deliberately stricter than deny matching. The deny list's symlink-awareness (three stages: relative path, resolved path, basename) exists to catch attackers who try to obfuscate what they're writing. If allow overrides inherited that same resolution, an attacker with write access to `.harness/config.json` could add `"write_allow_overrides": [".env"]` and then their malicious symlink-to-`.env` writes would pass both the literal check AND the resolved check.

Stricter allow matching keeps the "deny catches tricks, allow is surgical" invariant: the operator must name exactly what they want to allow, with no aliasing.

### Empty allow list

Backwards compatible. Every deny match still produces a denial — identical to Phase 1 behavior. The default `allow_patterns=None` in the extended signature preserves existing behavior for all callers.

## Architecture

### Function signature change

`src/weave/core/security.py` — extend `check_write_deny`:

```python
def check_write_deny(
    files_changed: list[str],
    working_dir: Path,
    patterns: list[str],
    allow_patterns: list[str] | None = None,
) -> list[str]:
    """Return the subset of files_changed that match any deny pattern
    and do not match any allow pattern.

    Deny matching uses three stages (relative path, resolved path,
    basename) to catch symlink aliasing. Allow matching uses only the
    relative path — stricter by design, to prevent attackers from
    aliasing around allow entries.
    """
```

### Control flow

For each file in `files_changed`:

1. Run the three existing deny-detection stages (stage 1: relative path; stage 2: resolved path after symlink resolution; stage 3: basename). If any stage matches, the file is a deny candidate.
2. If the file is a deny candidate AND `allow_patterns` is truthy (non-None and non-empty) AND the file's relative path (as written) matches any allow pattern via `fnmatch`, skip the denial.
3. Otherwise, add to the denied list.

The allow check is applied once per file, after all three deny stages — not per stage. This means a file that matches deny via symlink resolution cannot be exempted by an allow pattern matching the resolved path; only the relative path as written counts.

**`None` vs `[]` equivalence:** Both are treated as "no allow patterns" — behavior is identical to Phase 1. The signature uses `allow_patterns: list[str] | None = None` for caller convenience; internally, `None` is normalized to the empty list before matching.

### Runtime wiring

`src/weave/core/runtime.py` — `_security_scan` passes the allow list:

```python
denied_writes = check_write_deny(
    files,
    ctx.working_dir,
    deny_patterns,
    allow_patterns=ctx.config.security.write_allow_overrides,
)
```

`deny_patterns` is still composed as `write_deny_list + write_deny_extras`. Extras are automatically covered by the same allow override path.

### Schema cleanup

`src/weave/schemas/config.py` — remove the trailing `# Phase 2: not yet enforced` comment from the `write_allow_overrides` line. The field is no longer dormant.

## Backwards Compatibility

- `allow_patterns` defaults to `None`. All existing callers continue to work unchanged.
- When `None` or empty, `check_write_deny` behaves identically to Phase 1.
- All 91 Phase 1 tests must continue to pass without modification.
- Existing `.harness/config.json` files (which either omit `write_allow_overrides` or set it to `[]`) get Phase 1 behavior automatically.

## Tests

### Unit tests (`tests/test_security.py`)

1. **`test_check_write_deny_honors_allow_override`**
   - Deny: `[".env"]`
   - Allow: `[".env"]`
   - Files: `[".env"]`
   - Expected: denied list is empty.
   - Proves: allow overrides a direct deny match.

2. **`test_check_write_deny_allow_does_not_leak_to_other_files`**
   - Deny: `[".env", "*.pem"]`
   - Allow: `[".env"]`
   - Files: `[".env", "cert.pem"]`
   - Expected: denied list contains only `cert.pem`.
   - Proves: allow is surgical, not a blanket.

3. **`test_check_write_deny_allow_does_not_match_symlink_target`**
   - Setup: create symlink `innocuous.txt` pointing at `.env`.
   - Deny: `[".env"]`
   - Allow: `[".env"]`
   - Files: `["innocuous.txt"]`
   - Expected: denied list contains `innocuous.txt`.
   - Proves: allow does NOT inherit the symlink-resolution stage. Security-critical test encoding the stricter-allow invariant.

### Integration test (`tests/test_runtime.py`)

4. **`test_execute_respects_write_allow_overrides_in_mvp`**
   - Phase: `mvp` (the strictest enforcement)
   - `write_allow_overrides`: `["config.json"]`
   - Stub adapter writes `config.json`
   - Run `execute()`.
   - Expected: `result.status == RuntimeStatus.SUCCESS`, `result.security_result.action_taken == "clean"`.
   - Proves: the full pipeline honors the override end-to-end, including in the strictest phase.

### Regression verification

All 91 Phase 1 tests must pass unchanged, specifically:
- 4 existing `check_write_deny` tests in `test_security.py`
- `test_execute_flags_write_deny_in_sandbox`
- `test_execute_denies_write_deny_in_mvp`
- CLI integration tests that exercise the runtime

## File Map

| File | Action | Role |
|------|--------|------|
| `src/weave/core/security.py` | MODIFY | Add `allow_patterns` parameter to `check_write_deny` |
| `src/weave/core/runtime.py` | MODIFY | Pass `write_allow_overrides` to `check_write_deny` in `_security_scan` |
| `src/weave/schemas/config.py` | MODIFY | Remove `# Phase 2: not yet enforced` comment |
| `tests/test_security.py` | MODIFY | Add 3 unit tests |
| `tests/test_runtime.py` | MODIFY | Add 1 integration test |

## Out of Scope

- **Content-trust allowlisting** — a separate `scanner_allowlist` field in a future phase if needed.
- **CLI flag for one-off allow overrides** — config-only for now. Operators edit `.harness/config.json`.
- **Per-rule overrides in `supply_chain_rules`** — already supported separately via `RuleOverride`.
- **Hot-reload of allow list** — config is resolved once per invocation at `prepare()` time, as with all other config.

## Acceptance Criteria

- `check_write_deny(files, dir, deny_patterns, allow_patterns=[...])` skips files matching any allow pattern
- Runtime passes allow patterns from `SecurityConfig.write_allow_overrides`
- Operator can set `{"security": {"write_allow_overrides": ["config.json"]}}` in `.harness/config.json` and writes to `config.json` are no longer denied in mvp/enterprise phase
- Symlink-target aliasing does not bypass the allow-match stricture
- All 4 new tests pass
- All 91 Phase 1 tests continue to pass unchanged
- `SecurityConfig.write_allow_overrides` comment no longer says "not yet enforced"
