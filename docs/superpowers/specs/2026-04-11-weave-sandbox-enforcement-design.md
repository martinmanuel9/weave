# Design: Sandbox Enforcement Beyond Risk Classification

**Date:** 2026-04-11
**Phase:** 3 (sandbox enforcement)
**Status:** draft
**Supersedes / extends:** [2026-04-09 Phase 1 design](2026-04-09-weave-runtime-phase1-design.md) — line 308 ("Sandbox enforcement beyond risk classification (Phase 3)")

## Problem

Weave's sandbox phase is currently permissive. `PHASE_ENFORCEMENT["sandbox"] = "warn"` means every risk class is allowed with at most a warning. `resolve_action("deny", phase="sandbox")` downgrades security denials to warnings. The net effect: sandbox offers zero actual protection. An untrusted provider in sandbox can write to any file, read any credential, and trigger any security rule without being stopped.

This was deliberate for Phase 1 — the policy and security machinery needed to exist before it could be enforced. Phase 3 has now landed the provider contract registry (capability ceilings, protocol versioning) and transcript compaction. The foundation is complete. This spec closes the loop: sandbox becomes a real enforcement boundary.

## Goals

1. **Deny high-risk providers in sandbox.** Providers declaring `external-network` or `destructive` capability ceilings are hard-denied in sandbox phase. They must be promoted to mvp or enterprise phase before they can run.

2. **Restrict medium-risk providers in sandbox.** Providers declaring `workspace-write` are allowed but operate under expanded write-deny restrictions (CI pipelines, build configs, shell scripts, git hooks) and environment sanitization (credential stripping, PATH restriction, HOME isolation).

3. **Make sandbox security findings real.** The `deny → warn` downgrade in `resolve_action` for sandbox phase is removed. Security rules that specify `deny` actually deny in sandbox.

4. **Control the subprocess environment.** Adapters spawned in sandbox phase inherit a sanitized environment: no cloud credentials, no tokens, restricted PATH, isolated HOME directory. This reduces the attack surface for untrusted providers without requiring container isolation.

5. **Keep everything configurable.** A `SandboxConfig` section in `WeaveConfig` exposes the env-strip patterns, PATH allowlist, extra write-deny patterns, and HOME restriction flag. Users who need to loosen sandbox for their workflow can edit the config.

## Non-goals

- Container or namespace isolation (Docker, bubblewrap, seccomp). Requires runtime dependencies and/or root. Future enhancement.
- Network-level blocking (iptables, firewall rules). Requires root.
- Per-provider sandbox overrides. If you trust a provider enough to loosen sandbox, promote it out of sandbox phase.
- Sandbox restrictions for `session-end` CLI. It doesn't spawn adapters.
- `weave sandbox-check` preview CLI command. Separable; can land any time after.
- Automatic tmpdir for working directory (copying the project). Too expensive; write-deny + revert already protect the working dir.

## Architecture

### Two enforcement layers

```
Layer 1: Policy Gating (before invoke — fail fast)
═══════════════════════════════════════════════════

  evaluate_policy(contract, provider_config, requested_class, phase="sandbox")
       │
       ├─ effective >= external-network?  → DENIED (hard deny)
       ├─ effective == workspace-write?   → ALLOWED + warning + restrictions
       └─ effective == read-only?         → ALLOWED (unrestricted)


Layer 2: Environment Restriction (at invoke time)
═══════════════════════════════════════════════════

  _build_sandbox_env(config, provider_binary_dir)
       │
       ├─ Strip env vars matching SandboxConfig.strip_env_patterns
       ├─ Restrict PATH to SandboxConfig.safe_path_dirs + provider binary dir
       ├─ Replace HOME with tempdir (if restrict_home is True)
       │
       ▼
  invoke_provider(..., env=sandbox_env)
       │
       ▼
  _security_scan()
       │
       ├─ Expanded write-deny (base + sandbox.extra_write_deny)
       ├─ resolve_action no longer downgrades deny → warn in sandbox
       │
       ▼
  _revert() (if denied — unchanged from Phase 1)
```

### Pipeline integration

The existing 7-stage pipeline (`prepare → policy_check → invoke → security_scan → cleanup → revert → record`) gains two modifications:

1. Between `_policy_check` and `invoke_provider`: a new `_build_sandbox_env()` call constructs the sanitized environment dict (sandbox phase only).
2. `_security_scan` appends `config.sandbox.extra_write_deny` to the deny patterns list (sandbox phase only).

No new pipeline stages are added. The environment construction is a helper called from `execute()`, not a separate stage.

## Schema changes

### `SandboxConfig` (new, in `schemas/config.py`)

```python
class SandboxConfig(BaseModel):
    strip_env_patterns: list[str] = Field(default_factory=lambda: [
        "AWS_*", "AZURE_*", "GCP_*", "GOOGLE_*",
        "GITHUB_TOKEN", "GITLAB_TOKEN", "NPM_TOKEN",
        "PYPI_TOKEN", "SSH_AUTH_SOCK", "GPG_*",
    ])
    safe_path_dirs: list[str] = Field(default_factory=lambda: [
        "/usr/bin", "/bin", "/usr/local/bin",
    ])
    extra_write_deny: list[str] = Field(default_factory=lambda: [
        ".git/hooks/*",
        "Makefile",
        "Dockerfile",
        "docker-compose*",
        "*.sh",
        ".github/workflows/*",
        "package.json",
        "pyproject.toml",
        "Cargo.toml",
    ])
    restrict_home: bool = True
```

### `WeaveConfig` extension

```python
class WeaveConfig(BaseModel):
    # ... existing fields ...
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
```

No migration needed — new field with defaults. Existing config files without a `sandbox` key get the defaults automatically via pydantic.

## Policy changes

### Phase enforcement

`PHASE_ENFORCEMENT` in `policy.py`:

```python
PHASE_ENFORCEMENT = {
    "sandbox": "restrict",   # was "warn"
    "mvp": "deny",
    "enterprise": "deny",
}
```

### `evaluate_policy` new branch

The existing function gains a `"restrict"` branch:

```python
if enforcement == "restrict":
    if is_high_risk:
        # external-network, destructive → hard deny
        denials.append(
            f"Phase '{phase}' restricts {effective.value} class invocations"
        )
        return PolicyResult(
            allowed=False,
            effective_risk_class=effective,
            provider_ceiling=ceiling,
            requested_class=requested_class,
            warnings=warnings,
            denials=denials,
        )
    elif risk_class_level(effective) >= risk_class_level(RiskClass.WORKSPACE_WRITE):
        # workspace-write → allowed with sandbox restrictions
        warnings.append(
            f"Phase '{phase}' applies sandbox restrictions to {effective.value}"
        )
```

The existing `"warn"` and `"deny"` branches are unchanged.

### Sandbox restriction semantics by risk class

| Risk class | Sandbox (`restrict`) | MVP (`deny`) | Enterprise (`deny`) |
|---|---|---|---|
| `read-only` | Allowed, unrestricted | Allowed | Allowed |
| `workspace-write` | Allowed, env sanitized, extra write-deny, security findings enforced | Allowed | Allowed |
| `external-network` | **Denied** | Denied | Denied |
| `destructive` | **Denied** | Denied | Denied |

## Security changes

### `resolve_action` simplification

```python
def resolve_action(default_action: str, phase: str) -> str:
    """Phase-dependent action resolution.

    All phases now enforce actions as-is. The previous sandbox
    deny→warn downgrade was removed in Phase 3 sandbox enforcement.
    """
    return default_action
```

The function still exists for API stability (callers still call it) and in case future phases need phase-dependent overrides. But for now, it's an identity function.

### Write-deny expansion in `_security_scan`

In `runtime._security_scan()`, the deny patterns list construction changes:

```python
deny_patterns = (
    ctx.config.security.write_deny_list
    + ctx.config.security.write_deny_extras
)
if ctx.phase == "sandbox":
    deny_patterns = deny_patterns + ctx.config.sandbox.extra_write_deny
```

The `extra_write_deny` patterns are appended only during sandbox-phase invocations. In mvp/enterprise, the base deny list applies as before.

## Environment restriction

### `_build_sandbox_env` (new function in `runtime.py`)

```python
def _build_sandbox_env(
    config: WeaveConfig,
    provider_binary_dir: str | None = None,
) -> dict[str, str]:
```

**Algorithm:**

1. Start with `dict(os.environ)` (copy of the current process environment).
2. For each key in the copy, if it matches any pattern in `config.sandbox.strip_env_patterns` (using `fnmatch.fnmatch`), remove it.
3. Build PATH: join `config.sandbox.safe_path_dirs` with `:` separator. If `provider_binary_dir` is set, prepend it. This ensures the adapter's own binary is always reachable.
4. Preserve these keys regardless of strip patterns: `PYTHONPATH`, `LANG`, `LC_ALL`, `TERM`, `USER`, `LOGNAME`, `SHELL`. These are needed for subprocess functionality but are not security-sensitive.
5. Return the dict.

HOME and XDG variables are set by the caller (`execute()`) from the tempdir, not inside `_build_sandbox_env`. This keeps the function pure (no tempdir creation).

### Invoker changes

`invoke_provider()` gains one parameter:

```python
def invoke_provider(
    contract: ProviderContract,
    task: str,
    session_id: str,
    working_dir: Path,
    context: str = "",
    timeout: int = 300,
    registry=None,
    env: dict[str, str] | None = None,
) -> InvokeResult:
```

The only change in the body: `env=env` passed to `subprocess.run()`. When `None`, subprocess inherits the parent environment (current behavior).

### Runtime `execute()` changes

```python
def execute(...) -> RuntimeResult:
    ctx = prepare(...)
    policy, pre_hook_results = _policy_check(ctx)

    if not policy.allowed:
        ...  # existing denial path

    # Build sandbox environment if in sandbox phase
    sandbox_env = None
    sandbox_tmpdir = None
    if ctx.phase == "sandbox":
        import tempfile
        sandbox_tmpdir = Path(tempfile.mkdtemp(prefix="weave-sandbox-"))
        provider_bin_dir = str(ctx.adapter_script.parent)
        sandbox_env = _build_sandbox_env(ctx.config, provider_bin_dir)
        if ctx.config.sandbox.restrict_home:
            sandbox_env["HOME"] = str(sandbox_tmpdir)
            sandbox_env["XDG_CONFIG_HOME"] = str(sandbox_tmpdir / ".config")
            sandbox_env["XDG_DATA_HOME"] = str(sandbox_tmpdir / ".local" / "share")

    try:
        invoke_result = invoke_provider(
            contract=ctx.provider_contract,
            ...,
            env=sandbox_env,
        )
        # ... security scan, cleanup, revert, record (existing)
    finally:
        if sandbox_tmpdir and sandbox_tmpdir.exists():
            import shutil
            shutil.rmtree(sandbox_tmpdir, ignore_errors=True)
```

### Tempdir lifecycle

- Created at the start of `execute()` (after policy check, before invoke)
- Passed to the adapter subprocess as `HOME`
- Cleaned up in a `finally` block at the end of `execute()`
- `shutil.rmtree(ignore_errors=True)` — best effort cleanup; don't crash if cleanup fails
- Only created when `ctx.phase == "sandbox"` — other phases don't get a tempdir

## Error handling matrix

| Condition | Where | Behavior |
|---|---|---|
| `external-network` provider in sandbox | `evaluate_policy` | Hard deny, `PolicyResult.allowed=False` |
| `destructive` provider in sandbox | `evaluate_policy` | Hard deny, same |
| `workspace-write` provider in sandbox | `evaluate_policy` | Allowed with warning |
| `read-only` provider in sandbox | `evaluate_policy` | Allowed, no restrictions |
| Security finding `deny` in sandbox | `resolve_action` | Returns `"deny"` (no downgrade) → `_revert` |
| Sandbox `extra_write_deny` match | `_security_scan` | Deny finding → `_revert` rolls back |
| Same file match in mvp/enterprise | `_security_scan` | Not in extra list (sandbox-only) |
| Env var matches strip pattern | `_build_sandbox_env` | Removed silently |
| No env vars match | `_build_sandbox_env` | All preserved minus PATH/HOME |
| Tempdir creation fails | `execute()` | OSError propagates (disk full = hard error) |
| Tempdir cleanup fails | `execute() finally` | `ignore_errors=True` — best effort |
| Provider binary not on safe PATH | `_build_sandbox_env` | `provider_binary_dir` prepended to PATH |
| Empty `strip_env_patterns` | `_build_sandbox_env` | No vars stripped (user opted out) |
| Empty `safe_path_dirs` | `_build_sandbox_env` | PATH = only provider binary dir |
| Phase is not sandbox | `execute()` | `env=None`, invoker inherits parent env |

## Test plan

### New test file: `tests/test_sandbox.py`

**Policy enforcement (restrict mode):**
1. `test_sandbox_denies_external_network_provider` — contract ceiling `external-network`, sandbox → denied
2. `test_sandbox_denies_destructive_provider` — contract ceiling `destructive`, sandbox → denied
3. `test_sandbox_allows_workspace_write_with_warning` — ceiling `workspace-write`, sandbox → allowed + warning
4. `test_sandbox_allows_read_only_unrestricted` — ceiling `read-only`, sandbox → allowed, no warnings
5. `test_mvp_behavior_unchanged` — mvp still denies high-risk (regression check)

**Security action resolution:**
6. `test_resolve_action_sandbox_no_longer_downgrades` — `resolve_action("deny", "sandbox")` returns `"deny"`
7. `test_resolve_action_other_phases_unchanged` — mvp/enterprise returns action as-is

**Environment restriction:**
8. `test_build_sandbox_env_strips_matching_vars` — set `AWS_SECRET_KEY`, verify absent in result
9. `test_build_sandbox_env_preserves_safe_vars` — `LANG`, `USER`, `TERM` preserved
10. `test_build_sandbox_env_restricts_path` — PATH contains only safe dirs + provider dir
11. `test_build_sandbox_env_restricts_home` — HOME points to tempdir
12. `test_build_sandbox_env_noop_when_config_empty` — empty strip patterns + restrict_home=False → mostly inherited

**Integration:**
13. `test_sandbox_extra_write_deny_appended_in_sandbox` — write `.github/workflows/ci.yml`, denied in sandbox
14. `test_sandbox_extra_write_deny_not_appended_in_mvp` — same file, mvp → not denied by sandbox patterns
15. `test_invoke_receives_sandbox_env` — mock invoke_provider, verify `env` kwarg is dict in sandbox
16. `test_invoke_receives_none_env_in_mvp` — mock invoke_provider, verify `env` is None in mvp
17. `test_sandbox_tmpdir_cleaned_up_after_invoke` — after execute() returns, tmpdir gone

### Extensions to existing test files

**`tests/test_policy.py`:**
18. Update `test_evaluate_policy_sandbox_warns_on_high_risk` → `test_evaluate_policy_sandbox_restricts_high_risk` — sandbox now denies high-risk, not just warns

**`tests/test_security.py`:**
19. Update test that expects `resolve_action("deny", "sandbox")` to return `"warn"` → now returns `"deny"`

### Running tally

- Current baseline: **201 tests**
- New file: `tests/test_sandbox.py` — 17 tests
- Updated tests in `test_policy.py` and `test_security.py` — 2 tests modified (net +0)
- **Target: 201 + 17 = 218 tests**

Plan reconciles the exact count per task.

## Files changed / added

| Path | Change |
|---|---|
| `src/weave/schemas/config.py` | **MODIFIED** — add `SandboxConfig`, add `sandbox` field to `WeaveConfig` |
| `src/weave/core/policy.py` | **MODIFIED** — `PHASE_ENFORCEMENT["sandbox"] = "restrict"`, new branch in `evaluate_policy` |
| `src/weave/core/security.py` | **MODIFIED** — `resolve_action` simplified (no sandbox downgrade) |
| `src/weave/core/runtime.py` | **MODIFIED** — `_build_sandbox_env()`, `execute()` passes `env=`, sandbox write-deny expansion in `_security_scan`, tmpdir lifecycle |
| `src/weave/core/invoker.py` | **MODIFIED** — `env` parameter on `invoke_provider`, forwarded to `subprocess.run` |
| `tests/test_sandbox.py` | **NEW** — 17 tests |
| `tests/test_policy.py` | **MODIFIED** — update sandbox test |
| `tests/test_security.py` | **MODIFIED** — update resolve_action test |

## Open questions (to resolve in the plan, not this spec)

- Whether `_build_sandbox_env` should be a standalone function in `runtime.py` or a separate module (`core/sandbox.py`). The function is small (~20 lines) — putting it in runtime is fine unless the plan finds it growing.
- Whether the preserved env vars (`PYTHONPATH`, `LANG`, etc.) should be configurable via `SandboxConfig` or hardcoded. Hardcoded for now — these are functional requirements, not security decisions.
- Whether tests that currently assert sandbox-warns behavior need to be updated or replaced. The plan will grep for these and decide per test.

## Self-review notes

**Spec coverage:**
- Policy gating for all 4 risk classes in sandbox → evaluate_policy restrict branch + error matrix
- Security deny→warn removal → resolve_action simplification
- Expanded write-deny list → _security_scan sandbox branch
- Environment sanitization → _build_sandbox_env + invoker env param + tmpdir lifecycle
- Config schema → SandboxConfig with all 4 fields + WeaveConfig extension
- 17 tests covering policy, security, env restriction, and integration

**Placeholder scan:** No TBDs, TODOs, or incomplete sections.

**Internal consistency:**
- `"restrict"` enforcement is used consistently: defined in `PHASE_ENFORCEMENT`, branched on in `evaluate_policy`, tested in `test_sandbox.py`
- `SandboxConfig.extra_write_deny` is read in `_security_scan`, populated with defaults in the schema, tested in integration tests
- `env` parameter flows from `_build_sandbox_env` → `execute()` → `invoke_provider()` → `subprocess.run(env=env)` consistently
- Tempdir created in `execute()`, set as HOME in `sandbox_env`, cleaned up in `finally`

**Scope check:** Single implementation plan. One new schema, targeted edits to 5 existing files, one new test file. Moderate complexity — the `_build_sandbox_env` function is the only new logic; everything else is wiring changes to existing machinery.

**Ambiguity check:** Three open questions flagged for the plan. Everything else is explicit.
