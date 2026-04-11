# Design: Provider Contract Registry

**Date:** 2026-04-11
**Phase:** 3 (foundation)
**Status:** draft
**Supersedes / extends:** [2026-04-09 Phase 1 design](2026-04-09-weave-runtime-phase1-design.md) — line 306 ("Provider contract registry (Phase 3)")

## Problem

Weave's provider system today is an informal arrangement. `core/providers.py` hardcodes a list of 4 known CLIs as plain dicts. Adapters are shell scripts under `.harness/providers/*.sh` that must follow an *unwritten* contract: read a fixed JSON payload from stdin, emit a loose `{exitCode, stdout, stderr, structured}` shape on stdout, set exit codes by convention. Nothing enforces this, nothing versions it, and nothing validates the adapter response — `invoker.py` silently falls back to `structured=None` when JSON parsing fails.

Worse, capability claims (read-only vs workspace-write) live on the user's `config.json` rather than on the provider itself. A user can declare `ollama` as `workspace-write` even though the real adapter has no way to write files, and vice versa — nothing stops a user from *under-declaring* a writing provider as `read-only` and assuming safety that doesn't exist. The provider has no voice in its own capability.

This spec formalizes the runtime↔adapter boundary as a **provider contract registry**: each provider declares its own capability ceiling, wire protocol versions, runtime, and feature set via a sidecar manifest file. The runtime loads these into a registry at startup, validates config against contract ceilings, and enforces schema on adapter responses at invoke time.

This is the foundation for two downstream Phase 3 items (sandbox enforcement, transcript compaction) — both need a formal way to query "what can this provider do." This spec does not attempt either of those. It is scoped narrowly to the contract and registry itself.

## Goals

1. **Capability honesty** — providers declare their own capability ceiling; config can only restrict below the ceiling, never elevate.
2. **Protocol versioning** — the runtime↔adapter wire protocol has a named version (`weave.request.v1`, `weave.response.v1`). Adapters declare which version they speak. The runtime sends the shape the adapter asked for.
3. **Output schema validation** — adapter responses are validated against the declared response schema. Malformed output surfaces as an invoker-level error rather than silently dropping structured data.
4. **Registry as a lookup layer** — replace the hardcoded `KNOWN_PROVIDERS` list with a typed registry that merges in-tree built-ins with user-added `.harness/providers/*.contract.json` manifests.

## Non-goals

- Dynamic `describe` mode on adapters. All contracts are static manifest files.
- Third-party providers via Python entrypoints. Providers are shell/binary adapters discovered via manifest files.
- Sandbox enforcement beyond declaring `capability_ceiling`. Actual filesystem/process isolation is a separate Phase 3 spec.
- Transcript compaction. A separate Phase 3 spec will consume `declared_features` from this registry.
- `weave providers list` CLI command. Separable; can land any time after.
- Contract v2. This spec only defines v1.
- Automated config file migration tool. The loader renames legacy `capability` → `capability_override` on read with a deprecation warning; no scripted file rewrite.
- `environment_requirements`, `resource_requirements`, `supported_platforms`, `author`, `license`, `pricing`. YAGNI.

## Architecture

### Layered model

```
+-------------------------------------+
|  Built-in contracts (in-tree)       |   src/weave/providers/builtin/*.contract.json
|  claude-code, codex, gemini,        |
|  ollama, vllm                       |
+---------------+---------------------+
                | loaded at registry.load() time (fail-fast)
                v
+-------------------------------------+
|  User contracts (.harness)          |   .harness/providers/*.contract.json
|  Required if the matching adapter   |   (fail per-provider, not per-weave)
|  file exists                        |
+---------------+---------------------+
                | merged; user wins on name collision
                v
+-------------------------------------+
|  ProviderRegistry                   |   src/weave/core/registry.py
|  {name -> ProviderContract}         |   single lookup surface
+---------------+---------------------+
                | consulted during
                v
+-------------------------------------+
|  Config load (WeaveConfig)          |   rejects capability_override > ceiling
|  Runtime prepare()                  |   attaches contract to PreparedContext
|  Invoker                            |   validates AdapterResponseV1
+-------------------------------------+
```

### Data flow for a single invocation

1. `runtime.prepare(task, provider_name)` calls `get_registry().load(project_root)` (idempotent) then `registry.get(provider_name)` → returns a `ProviderContract`.
2. Effective capability is resolved by `resolve_risk_class(contract_ceiling, config_override, requested)` in `core/policy.py`, which walks the three inputs using `risk_class_level()` ordinal comparisons. The contract ceiling enters this computation for the first time in this spec.
3. `PreparedContext` gains a `provider_contract: ProviderContract` field.
4. `runtime.execute(ctx)` passes `ctx.provider_contract` to `invoke_provider()`.
5. `invoke_provider`:
   a. reads `contract.adapter_runtime` to build the subprocess argv (`bash`, `python`, `node`, or direct binary),
   b. looks up `PROTOCOL_VERSIONS[contract.protocol.request_schema]` and instantiates the request model,
   c. sends `.model_dump_json()` to the adapter on stdin,
   d. captures stdout, parses as JSON, validates against `PROTOCOL_VERSIONS[contract.protocol.response_schema]`,
   e. success → populates `InvokeResult` from the validated model; failure → `InvokeResult(exit_code=1, stderr="adapter response violates <schema>: <details>", structured=None)`.

### Precedence rules

- **User manifest overrides built-in with the same name.** A warning is logged naming both sources.
- **Built-in manifest load failure** raises `ProviderRegistryError` and exits 1. Built-in breakage is a weave bug, not a user bug.
- **User manifest load failure** is logged and the provider is marked unavailable. Other providers still load.
- **Adapter file exists without a sidecar manifest** is a failure for *that* provider (unavailable with a clear message), not for weave startup. This is the exact case the spec exists to eliminate.

## Schemas

### `ProviderContract`

Lives in `src/weave/schemas/provider_contract.py`.

```python
from enum import Enum
from typing import Literal
from pydantic import BaseModel, Field
from weave.schemas.policy import RiskClass


class ProviderFeature(str, Enum):
    STREAMING = "streaming"
    STRUCTURED_OUTPUT = "structured-output"
    TOOL_USE = "tool-use"
    THINKING = "thinking"
    MULTIMODAL_INPUT = "multimodal-input"
    FILE_EDIT = "file-edit"
    SHELL_EXEC = "shell-exec"


class AdapterRuntime(str, Enum):
    BASH = "bash"
    PYTHON = "python"
    NODE = "node"
    BINARY = "binary"


class ProviderProtocol(BaseModel):
    request_schema: str
    response_schema: str


class ProviderContract(BaseModel):
    contract_version: Literal["1"] = "1"
    name: str
    display_name: str
    adapter: str
    adapter_runtime: AdapterRuntime
    capability_ceiling: RiskClass
    protocol: ProviderProtocol
    declared_features: list[ProviderFeature] = Field(default_factory=list)
    health_check: str | None = None
    source: Literal["builtin", "user"] = "builtin"
```

**Schema-level invariants** (enforced by pydantic validators):
- `protocol.request_schema` and `protocol.response_schema` are keys in the `PROTOCOL_VERSIONS` registry (validator imports `PROTOCOL_VERSIONS` at validation time — no circular import since `schemas/protocol.py` does not import `provider_contract`).
- `declared_features` members are all in the `ProviderFeature` enum (pydantic does this for free).
- `capability_ceiling` is a valid `RiskClass` (pydantic does this for free).
- `adapter_runtime` is a valid enum member (pydantic does this for free).

**Loader-level invariants** (enforced in `ProviderRegistry.load`, not in the schema — the schema alone cannot see the filename or filesystem):
- `name` equals the filename stem (`claude-code.contract.json` → `"claude-code"`).
- `adapter` resolves to an existing file, relative to the manifest's directory.
- `source` field on parsed contracts is ignored if present in the JSON; the loader injects `"builtin"` or `"user"` based on where the file came from.

### Wire protocol v1

Lives in `src/weave/schemas/protocol.py` (new file).

```python
from typing import Literal
from pydantic import BaseModel


class AdapterRequestV1(BaseModel):
    protocol: Literal["weave.request.v1"] = "weave.request.v1"
    session_id: str
    task: str
    workingDir: str
    context: str = ""
    timeout: int = 300


class AdapterResponseV1(BaseModel):
    protocol: Literal["weave.response.v1"] = "weave.response.v1"
    exitCode: int
    stdout: str
    stderr: str
    structured: dict | None = None


PROTOCOL_VERSIONS: dict[str, type[BaseModel]] = {
    "weave.request.v1": AdapterRequestV1,
    "weave.response.v1": AdapterResponseV1,
}
```

**camelCase rationale.** `workingDir` and `exitCode` use camelCase rather than snake_case to match the existing adapter shell scripts under `.harness/providers/*.sh`, which `jq` these fields out of stdin/stdout. Changing to snake_case would force a simultaneous rewrite of every adapter script for zero functional gain. The Python side tolerates this via pydantic's field names.

**Request shape change from today.** The current invoker payload is `{task, workingDir, context, timeout}`. v1 adds `protocol` (literal discriminator) and `session_id`. Built-in adapter scripts that already `jq -r '.task'` and `jq -r '.workingDir'` continue to work unchanged — they ignore the extra fields. No adapter script edits are required in this spec.

**Response shape.** Identical in field names to today's loose convention. The change is that it is now a pydantic model with a required `protocol` discriminator. Adapters that already emit `{exitCode, stdout, stderr, structured}` via `jq -n` need one addition: the `protocol` field. All five built-in adapter scripts in this spec are updated to emit it.

## Registry API

Lives in `src/weave/core/registry.py` (new file).

```python
from pathlib import Path
from weave.schemas.provider_contract import ProviderContract


class ProviderRegistryError(Exception):
    """Raised when a built-in contract is malformed. Weave exits 1."""


class ProviderRegistry:
    def __init__(self) -> None:
        self._contracts: dict[str, ProviderContract] = {}
        self._manifest_dirs: dict[str, Path] = {}
        self._loaded_root: Path | None = None

    def load(self, project_root: Path) -> None:
        """Load built-ins (fail-fast) then user contracts (fail-per-provider).

        Idempotent: second call with the same project_root is a no-op.
        A call with a different project_root resets state and reloads.
        """

    def get(self, name: str) -> ProviderContract:
        """Return contract by name. Raises KeyError if unknown."""

    def resolve_adapter_path(self, name: str) -> Path:
        """Return the absolute path to the adapter file for a loaded contract.

        Raises KeyError if the contract is unknown.
        """

    def list(self) -> list[ProviderContract]:
        """Return all loaded contracts in deterministic order (sorted by name)."""

    def has(self, name: str) -> bool: ...


def get_registry() -> ProviderRegistry:
    """Module-level singleton. Call .load(project_root) on first use."""
```

### Loader algorithm

```
1. Scan <weave package>/providers/builtin/*.contract.json
   for each file:
     parse JSON
     pydantic validate -> ProviderContract
     assert name == filename stem
     assert adapter file exists relative to manifest dir
     inject source="builtin"
     any failure -> raise ProviderRegistryError  (exit 1)
   add to registry.

2. Scan <project_root>/.harness/providers/*.contract.json
   for each file:
     parse JSON
     pydantic validate
     assert name == filename stem
     assert adapter file exists relative to manifest dir
     inject source="user"
     any failure -> log error, skip provider
     if name already in registry:
       log warning ("user contract overrides built-in <name>")
       replace built-in entry

3. Scan <project_root>/.harness/providers/*.sh for adapters whose
   <stem>.contract.json is missing.
   for each orphan:
     log error ("adapter <stem>.sh has no contract manifest; provider unavailable")
```

The orphan scan in step 3 is diagnostic only — the provider is simply absent from the registry, which is enough to make it unavailable. The log message tells the user what to do.

## Config schema change

`ProviderConfig` in `schemas/config.py` is modified:

```python
class ProviderConfig(BaseModel):
    command: str
    enabled: bool = True
    capability_override: RiskClass | None = None
    # health_check removed — moves to contract
```

### Migration

The config loader applies a one-shot in-memory rename on read:

- If the parsed JSON dict has a `capability` key but no `capability_override` key, rename `capability` → `capability_override` and emit a `DeprecationWarning` naming the affected provider.
- If both keys are present, the new key wins and a warning is logged about the ignored legacy key.
- If `health_check` is present on a `ProviderConfig`, it is silently ignored. No warning (the contract now owns it; carrying a warning for every existing project would be noise).

No file is rewritten automatically. When the user next runs a weave command that persists config (e.g., `weave init`), the rewritten file omits legacy fields.

### Capability validation

After `WeaveConfig` is parsed and the registry is loaded, a new validation pass iterates `config.providers`:

- For each entry, call `registry.get(name)`. Unknown name → raise `ConfigError` with the list of known providers.
- If `entry.capability_override` is set, compare its `RiskClass` rank to `contract.capability_ceiling` rank. If the override rank exceeds the ceiling rank, raise `ConfigError` naming both values and both fields.

This validation is the *load-time* half of the "capability honesty" guarantee. The *runtime* half lives in `runtime.prepare()`.

## Runtime integration

### `runtime.prepare()` changes

1. Call `registry = get_registry()` and `registry.load(project_root)` before resolving the provider. `load()` is idempotent.
2. Resolve the contract: `contract = registry.get(provider_name)`. `KeyError` → raise `RuntimeError("unknown provider: <name>. Known: [<list>]")`.
3. Compute effective capability via the updated `resolve_risk_class()` in `core/policy.py` (see **Policy integration** below). The function now takes the contract ceiling as an explicit input rather than reading a ceiling off `ProviderConfig`.
4. Attach the contract to `PreparedContext` as a new field `provider_contract: ProviderContract`.

### `runtime.execute()` changes

Reads `ctx.provider_contract` from the prepared context and passes it to the invoker.

## Policy integration

`core/policy.py` currently reads the capability ceiling off `ProviderConfig.capability`. After this spec, the ceiling lives on the contract, so `policy.py` must be updated to take the contract as an explicit input.

### `resolve_risk_class()` new signature

```python
def resolve_risk_class(
    contract_ceiling: RiskClass,
    config_override: RiskClass | None,
    requested: RiskClass | None,
) -> RiskClass:
    """Resolve effective risk class by walking three inputs in order:
    contract ceiling -> config override -> caller requested.

    Each step may only restrict (lower the ordinal level), never elevate.
    Raises ValueError if `requested` exceeds the already-clamped ceiling.
    """
    effective = contract_ceiling
    if config_override is not None:
        if risk_class_level(config_override) > risk_class_level(effective):
            # Should never happen — config load validates this earlier.
            # Defense in depth: clamp silently rather than raise, since
            # the raise is reserved for explicit caller overreach.
            pass
        else:
            effective = config_override
    if requested is not None:
        if risk_class_level(requested) > risk_class_level(effective):
            raise ValueError(
                f"Requested risk class {requested.value} exceeds effective "
                f"ceiling {effective.value}"
            )
        effective = requested
    return effective
```

### `evaluate_policy()` new signature

```python
def evaluate_policy(
    contract: ProviderContract,
    provider_config: ProviderConfig,
    requested_class: RiskClass | None,
    phase: str,
) -> PolicyResult: ...
```

Internally it calls `resolve_risk_class(contract.capability_ceiling, provider_config.capability_override, requested_class)`. `PolicyResult.provider_ceiling` is populated from `contract.capability_ceiling` rather than `provider_config.capability`.

### Callers of policy functions

Every call site in `runtime.py` (and tests) that previously passed `provider_config` alone now passes `(contract, provider_config)`. The migration is mechanical: grep for `evaluate_policy(` and `resolve_risk_class(`, update each call.

## Invoker changes

`src/weave/core/invoker.py` — new signature:

```python
def invoke_provider(
    contract: ProviderContract,
    task: str,
    session_id: str,
    working_dir: Path,
    context: str = "",
    timeout: int = 300,
) -> InvokeResult: ...
```

**Behavior:**

1. **Resolve adapter path.** The registry exposes `registry.resolve_adapter_path(name) -> Path`. Internally the registry keeps a parallel `_manifest_dirs: dict[str, Path]` map populated at load time, and `resolve_adapter_path` returns `self._manifest_dirs[name] / self._contracts[name].adapter`. `ProviderContract` itself stays a clean pydantic model with no filesystem-aware fields.
2. **Build spawn argv** based on `contract.adapter_runtime`:
   - `bash` → `["bash", str(adapter_path)]`
   - `python` → `["python3", str(adapter_path)]`
   - `node` → `["node", str(adapter_path)]`
   - `binary` → `[str(adapter_path)]` (direct exec; adapter must be executable)
3. **Build request.** `request_cls = PROTOCOL_VERSIONS[contract.protocol.request_schema]`. Instantiate with `session_id`, `task`, `workingDir=str(working_dir)`, `context`, `timeout`. Serialize with `.model_dump_json()`.
4. **Run subprocess.** Same `subprocess.run` pattern as today. Timeout handling unchanged.
5. **Parse and validate response.**
   - `json.loads(stdout)` → `parsed`. Parse failure → return `InvokeResult(exit_code=1, stderr=f"adapter response is not valid JSON: {err}", structured=None, ...)`.
   - `response_cls = PROTOCOL_VERSIONS[contract.protocol.response_schema]`. `response_cls.model_validate(parsed)` → `validated`. Pydantic validation failure → return `InvokeResult(exit_code=1, stderr=f"adapter response violates {contract.protocol.response_schema}: {err}", structured=None, ...)`.
   - Success → populate `InvokeResult` from `validated.exitCode`, `validated.stdout`, `validated.stderr`, `validated.structured`.
6. **files_changed** detection via git diff is unchanged (`_get_git_changed_files`).

`InvokeResult` dataclass shape is unchanged; no field additions or renames.

## Scaffold and detection updates

### `core/providers.py`

`KNOWN_PROVIDERS` is deleted. `detect_providers()` becomes:

```python
def detect_providers(project_root: Path | None = None) -> list[ProviderInfo]:
    registry = get_registry()
    registry.load(project_root or Path.cwd())
    out: list[ProviderInfo] = []
    for contract in registry.list():
        installed = (
            check_provider_health(contract.health_check)
            if contract.health_check
            else False
        )
        out.append(
            ProviderInfo(
                name=contract.name,
                command=contract.name,  # see note below on ProviderInfo.command
                installed=installed,
                health_check=contract.health_check or "",
                adapter_script=contract.adapter,
            )
        )
    return out
```

**Note on `ProviderInfo.command`.** This field exists today solely so `scaffold.py` can inject the command name into a bash template. Since scaffold now copies real adapter files instead of generating them from a template, the field no longer has a real consumer in the code. It is kept on the dataclass (set to `contract.name`) as a placeholder for CLI listing output, but flagged as deprecated in a comment. A follow-up cleanup can remove it once nothing reads it.

### `core/scaffold.py`

The template-generation path (`_ADAPTER_TEMPLATE`, `_CLI_FLAGS`, `_build_adapter_script`) is **deleted**. Scaffolding a new project, for each contract in the registry where the corresponding binary is installed:

1. Copy the in-tree adapter script from `src/weave/providers/builtin/<name>.sh` to `<project>/.harness/providers/<name>.sh` (preserving executable mode).
2. Copy the in-tree contract manifest from `src/weave/providers/builtin/<name>.contract.json` to `<project>/.harness/providers/<name>.contract.json`.

The user can then edit either file to customize. This gives users a working starting point rather than making them author a manifest from scratch. The copied manifest contains no `source` field; the registry loader will inject `source="user"` when it reads the copy back.

File copy uses `shutil.copy2` to preserve permissions. The copy is unconditional (no merging with existing files) — if the user has pre-existing content in `.harness/providers/`, scaffold must not overwrite it. Scaffold checks existence first and skips any file that already exists, logging a warning.

## Built-in contracts

Five manifests ship in `src/weave/providers/builtin/`, alongside their adapter scripts.

### `claude-code.contract.json`

```json
{
  "contract_version": "1",
  "name": "claude-code",
  "display_name": "Claude Code",
  "adapter": "claude-code.sh",
  "adapter_runtime": "bash",
  "capability_ceiling": "workspace-write",
  "protocol": {
    "request_schema": "weave.request.v1",
    "response_schema": "weave.response.v1"
  },
  "declared_features": ["tool-use", "file-edit", "shell-exec", "streaming"],
  "health_check": "claude --version"
}
```

### `codex.contract.json`

```json
{
  "contract_version": "1",
  "name": "codex",
  "display_name": "Codex CLI",
  "adapter": "codex.sh",
  "adapter_runtime": "bash",
  "capability_ceiling": "workspace-write",
  "protocol": {
    "request_schema": "weave.request.v1",
    "response_schema": "weave.response.v1"
  },
  "declared_features": ["tool-use", "file-edit", "shell-exec"],
  "health_check": "codex --version"
}
```

### `gemini.contract.json`

```json
{
  "contract_version": "1",
  "name": "gemini",
  "display_name": "Gemini CLI",
  "adapter": "gemini.sh",
  "adapter_runtime": "bash",
  "capability_ceiling": "workspace-write",
  "protocol": {
    "request_schema": "weave.request.v1",
    "response_schema": "weave.response.v1"
  },
  "declared_features": ["tool-use", "file-edit", "shell-exec"],
  "health_check": "gemini --version"
}
```

### `ollama.contract.json`

```json
{
  "contract_version": "1",
  "name": "ollama",
  "display_name": "Ollama",
  "adapter": "ollama.sh",
  "adapter_runtime": "bash",
  "capability_ceiling": "read-only",
  "protocol": {
    "request_schema": "weave.request.v1",
    "response_schema": "weave.response.v1"
  },
  "declared_features": ["structured-output"],
  "health_check": "ollama --version"
}
```

### `vllm.contract.json` (new)

```json
{
  "contract_version": "1",
  "name": "vllm",
  "display_name": "vLLM (via vllmc)",
  "adapter": "vllm.sh",
  "adapter_runtime": "bash",
  "capability_ceiling": "read-only",
  "protocol": {
    "request_schema": "weave.request.v1",
    "response_schema": "weave.response.v1"
  },
  "declared_features": ["structured-output"],
  "health_check": "vllmc server status"
}
```

### `vllm.sh` adapter script (new)

```bash
#!/usr/bin/env bash
# Weave provider adapter for vLLM (via vllmc CLI)
set -euo pipefail

INPUT=$(cat)
TASK=$(echo "$INPUT" | jq -r '.task')
WORKING_DIR=$(echo "$INPUT" | jq -r '.workingDir')
cd "$WORKING_DIR"

if ! command -v vllmc >/dev/null 2>&1; then
  jq -n --arg stderr "vllmc not found on PATH" \
    '{ protocol: "weave.response.v1", exitCode: 127, stdout: "", stderr: $stderr, structured: null }'
  exit 0
fi

STDOUT=""
STDERR=""
EXIT_CODE=0
TMPFILE="${TMPDIR:-/tmp}/weave-vllm-stderr-$$"
STDOUT=$(vllmc --json chat --no-stream "$TASK" 2>"$TMPFILE") || EXIT_CODE=$?
STDERR=$(cat "$TMPFILE" 2>/dev/null || echo "")
rm -f "$TMPFILE"

jq -n \
  --arg stdout "$STDOUT" \
  --arg stderr "$STDERR" \
  --argjson exitCode "$EXIT_CODE" \
  '{ protocol: "weave.response.v1", exitCode: $exitCode, stdout: $stdout, stderr: $stderr, structured: {} }'
```

Note the adapter emits a `structured: {}` rather than `null` so the response passes validation as a dict; weave consumers can still check for emptiness.

### Existing adapter updates

The four existing scripts (`claude-code.sh`, `codex.sh`, `gemini.sh`, `ollama.sh`) each need one change: add `protocol: "weave.response.v1"` to their `jq -n` output. This is a one-line diff per file.

## Error handling matrix

| Condition | Where detected | Behavior |
|---|---|---|
| Built-in manifest malformed JSON | `registry.load()` step 1 | `ProviderRegistryError`, weave exits 1 |
| Built-in manifest fails pydantic | `registry.load()` step 1 | `ProviderRegistryError`, weave exits 1 |
| Built-in adapter file missing | `registry.load()` step 1 (loader invariant) | `ProviderRegistryError`, weave exits 1 |
| Built-in manifest absent from in-tree dir | `tests/test_registry.py` | Test asserts all 5 exist |
| User manifest malformed JSON | `registry.load()` step 2 | Log error, provider absent, others load |
| User manifest fails pydantic | `registry.load()` step 2 | Log error, provider absent, others load |
| User adapter exists, manifest missing | `registry.load()` step 3 | Log error, provider absent, others load |
| User manifest overrides built-in by name | `registry.load()` step 2 | Warning logged, user contract wins |
| Unknown `request_schema` / `response_schema` | `ProviderContract` validator | Contract rejected at load |
| Unknown feature enum | `ProviderContract` validator | Contract rejected at load |
| `capability_override` > `capability_ceiling` | Config post-load validation | `ConfigError`, exit 1, names both fields |
| Caller requests unknown provider | `runtime.prepare()` | `RuntimeError` with known provider list |
| Adapter subprocess stdout not valid JSON | `invoke_provider()` | `InvokeResult(exit_code=1, stderr="adapter response is not valid JSON: …", structured=None)` |
| Adapter stdout parses but fails schema | `invoke_provider()` | `InvokeResult(exit_code=1, stderr="adapter response violates weave.response.v1: …", structured=None)` |
| Adapter subprocess times out | `invoke_provider()` | Unchanged from today |
| Adapter subprocess crashes | `invoke_provider()` | Unchanged from today |
| Legacy `capability` key in config | Config loader | Rename to `capability_override` on read, `DeprecationWarning` |
| Legacy `health_check` key in `ProviderConfig` | Config loader | Silently ignored |
| Legacy `policy.resolve_risk_class(provider_config, requested)` call site | Migration grep | Plan's policy task updates all call sites to the new three-arg signature |

## Test plan

### New test files

**`tests/test_provider_contract.py`**
- `ProviderContract` validates a known-good manifest dict.
- Rejects manifest with unknown `ProviderFeature` member.
- Rejects manifest with `request_schema` not in `PROTOCOL_VERSIONS`.
- Rejects manifest with `response_schema` not in `PROTOCOL_VERSIONS`.
- Rejects manifest with unknown `AdapterRuntime` value.
- Rejects manifest with unknown `RiskClass` for `capability_ceiling`.
- `contract_version` must be `"1"` (literal enforces this).

**`tests/test_protocol.py`**
- `AdapterRequestV1` round-trips via `model_dump_json` → `model_validate_json`.
- `AdapterResponseV1` accepts a well-formed response dict.
- `AdapterResponseV1` rejects missing `exitCode`.
- `AdapterResponseV1` rejects wrong `protocol` literal value.
- `AdapterResponseV1` accepts `structured=None`.
- `AdapterResponseV1` accepts `structured={}`.
- `PROTOCOL_VERSIONS` dict contains exactly `weave.request.v1` and `weave.response.v1` keys.

**`tests/test_registry.py`**
- Loads all 5 built-in contracts without error.
- All 5 built-in manifests exist on disk (directory listing test).
- Each built-in manifest's `adapter` field points to an existing file.
- Rejects a built-in with invalid JSON (monkeypatched in-tree dir) → `ProviderRegistryError`.
- Rejects a built-in that fails pydantic → `ProviderRegistryError`.
- Loads user contracts from a tmp `.harness/providers/`.
- User contract with same name as built-in wins and emits a warning (assert log).
- User adapter `foo.sh` without `foo.contract.json` → provider absent, log message asserted.
- User manifest with filename-stem mismatch (`bar.contract.json` declares `name: "baz"`) → rejected, other providers still load.
- `registry.get()` raises `KeyError` for unknown name.
- `registry.list()` returns contracts sorted by name (deterministic).
- `registry.load()` is idempotent — second call with the same root is a no-op.
- `registry.has()` returns correct bool for known and unknown names.

### Extensions to existing test files

**`tests/test_config.py`**
- Legacy `capability` key on `ProviderConfig` is renamed to `capability_override` on read, with `DeprecationWarning`.
- Both `capability` and `capability_override` present → new wins, warning logged.
- Legacy `health_check` key on `ProviderConfig` is silently ignored.
- `capability_override` = `read-only`, `capability_ceiling` = `workspace-write` → passes (restriction allowed).
- `capability_override` = `workspace-write`, `capability_ceiling` = `read-only` → `ConfigError` naming both fields.
- `capability_override` unset → effective capability is contract ceiling.

**`tests/test_invoker.py`**
- `invoke_provider` with a `bash` contract spawns `["bash", adapter_path]`.
- `invoke_provider` with a `python` contract spawns `["python3", adapter_path]` (mock `subprocess.run`, assert argv).
- `invoke_provider` with a `binary` contract spawns `[adapter_path]` directly.
- Valid adapter response populates `InvokeResult.structured` correctly.
- Malformed JSON response → `exit_code=1`, `stderr` contains `"not valid JSON"`, `structured=None`.
- JSON that fails schema validation → `exit_code=1`, `stderr` contains `"violates weave.response.v1"`, `structured=None`.
- Request payload sent to adapter includes `protocol: "weave.request.v1"` and `session_id`.

**`tests/test_runtime.py`**
- `prepare()` loads the registry and attaches `provider_contract` to `PreparedContext`.
- Effective capability is clamped to contract ceiling even when config omits `capability_override`.
- Effective capability is clamped to `capability_override` when it is below the ceiling.
- `execute()` forwards `ctx.provider_contract` to `invoke_provider()` (verify via mock).
- Unknown provider name raises `RuntimeError` with a helpful message listing known providers.

**`tests/test_policy.py`**
- Existing tests that called `resolve_risk_class(provider_config, requested)` are updated to the new `resolve_risk_class(contract_ceiling, config_override, requested)` signature. This is migration, not new coverage, and does not change the test count.
- New: `resolve_risk_class` returns contract ceiling when both override and requested are `None`.
- New: `resolve_risk_class` returns config override when it is below ceiling and no request.
- New: `resolve_risk_class` raises `ValueError` when `requested` exceeds effective ceiling.
- New: `evaluate_policy` populates `PolicyResult.provider_ceiling` from the contract, not from config.

### Running tally

- Current baseline: **136 tests** (verified via `pytest --collect-only -q`).
- New files: `test_provider_contract.py` ~7, `test_protocol.py` ~7, `test_registry.py` ~13 → **+27**.
- Extensions: `test_config.py` +6, `test_invoker.py` +7, `test_runtime.py` +5, `test_policy.py` +4 → **+22**.
- **Target after this spec's plan: 136 + 27 + 22 = 185 tests.**

The plan document will reconcile this figure precisely per task; deviations from 185 get explained there.

## Files changed / added

| Path | Change |
|---|---|
| `src/weave/schemas/provider_contract.py` | **NEW** — `ProviderContract`, `ProviderFeature`, `AdapterRuntime`, `ProviderProtocol` |
| `src/weave/schemas/protocol.py` | **NEW** — `AdapterRequestV1`, `AdapterResponseV1`, `PROTOCOL_VERSIONS` |
| `src/weave/core/registry.py` | **NEW** — `ProviderRegistry`, `ProviderRegistryError`, `get_registry()` |
| `src/weave/providers/builtin/claude-code.contract.json` | **NEW** |
| `src/weave/providers/builtin/codex.contract.json` | **NEW** |
| `src/weave/providers/builtin/gemini.contract.json` | **NEW** |
| `src/weave/providers/builtin/ollama.contract.json` | **NEW** |
| `src/weave/providers/builtin/vllm.contract.json` | **NEW** |
| `src/weave/providers/builtin/claude-code.sh` | **NEW** (copy of existing `.harness/providers/claude-code.sh` + `protocol` field) |
| `src/weave/providers/builtin/codex.sh` | **NEW** (same pattern) |
| `src/weave/providers/builtin/gemini.sh` | **NEW** (same pattern) |
| `src/weave/providers/builtin/ollama.sh` | **NEW** (same pattern) |
| `src/weave/providers/builtin/vllm.sh` | **NEW** — `vllmc` wrapper |
| `src/weave/schemas/config.py` | **MODIFIED** — rename `capability` → `capability_override`, remove `health_check` |
| `src/weave/core/config.py` | **MODIFIED** — legacy key migration on read, capability clamp validation |
| `src/weave/core/invoker.py` | **MODIFIED** — signature change, schema-driven request build, response validation |
| `src/weave/core/runtime.py` | **MODIFIED** — registry load, `PreparedContext` dataclass gains `provider_contract` field, contract forwarded to invoker, capability resolution now calls updated `policy.py` |
| `src/weave/core/policy.py` | **MODIFIED** — `resolve_risk_class()` and `evaluate_policy()` take contract ceiling as an explicit input; `PolicyResult.provider_ceiling` populated from contract |
| `src/weave/core/providers.py` | **MODIFIED** — `detect_providers()` driven by registry; delete `KNOWN_PROVIDERS` |
| `src/weave/core/scaffold.py` | **MODIFIED** — copy adapter script + contract manifest from built-ins into `.harness/providers/`; delete `_build_adapter_script`, `_ADAPTER_TEMPLATE`, `_CLI_FLAGS` (template generation is replaced by direct file copy) |
| `tests/test_provider_contract.py` | **NEW** |
| `tests/test_protocol.py` | **NEW** |
| `tests/test_registry.py` | **NEW** |
| `tests/test_config.py` | **MODIFIED** |
| `tests/test_invoker.py` | **MODIFIED** |
| `tests/test_runtime.py` | **MODIFIED** |

## Open questions (to resolve in the plan, not this spec)

- Exact argv for `python` runtime: `python3` vs `sys.executable`. The plan decides based on how adapter scripts are expected to run in test environments.
- Whether to add a `command` field to `ProviderContract` now or leave `detect_providers()` echoing `name` into the legacy `ProviderInfo.command`. Leaving it for now; revisit if any consumer starts needing the real binary name.

**Resolved during self-review (not open):**
- `registry.load()` idempotency and cross-project behavior: spec now states "idempotent when called with the same `project_root`; resets and reloads when called with a different root" (Registry API section).
- Session binding stability: `compute_binding()` hashes `config`, not `PreparedContext`, so adding `provider_contract` to the dataclass does not affect binding hashes. No change to `session_binding.py`.

## Self-review notes

Spec covers:
- All four goals from the brainstorm (capability honesty, protocol versioning, output schema validation, registry as lookup layer).
- The five architectural decisions from Q3-Q6 (field set, closed feature enum, two-tier registry with fail-fast, capability clamp chain, strict-with-migration schema validation).
- The revised provider set (5 built-ins with vllm via vllmc, opencode dropped).
- No TBDs or placeholder steps in any code block.
- No contradictions between sections (capability clamp is described in Architecture → Config → Runtime → Error matrix consistently).
- Scope is a single implementation plan: one new concept (contract), one new module (registry), one schema module (protocol), focused invoker/config/runtime edits, five data files. Not too large to execute in one plan.
- Ambiguity check: "registry singleton across tests" flagged as an open question for the plan; everything else is explicit.
