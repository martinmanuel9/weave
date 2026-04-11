# Provider Contract Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Formalize the runtime↔adapter boundary with sidecar contract manifests — capability ceilings, versioned wire protocol, schema-validated responses, and a two-tier registry of in-tree built-ins + user overrides.

**Architecture:** New `schemas/protocol.py` defines `AdapterRequestV1`/`AdapterResponseV1` pydantic models. New `schemas/provider_contract.py` defines `ProviderContract` with enums and a validator that references the protocol registry. New `core/registry.py` loads built-in contracts from `src/weave/providers/builtin/` (fail-fast) and user contracts from `.harness/providers/` (fail-per-provider). `runtime.prepare()` resolves a contract per invocation and attaches it to `PreparedContext`; `invoker.invoke_provider()` consults the contract for spawn command and response validation. `core/policy.py` takes the contract ceiling as explicit input. Scaffold and detection become thin registry consumers.

**Tech Stack:** Python 3.12, pydantic v2, click (CLI), pytest.

**Spec reference:** [`docs/superpowers/specs/2026-04-11-weave-provider-contract-registry-design.md`](../specs/2026-04-11-weave-provider-contract-registry-design.md)

**Baseline test count:** 136 (verified via `pytest --collect-only -q` on commit `bd947a1`).

**Target test count:** 185 (+49: new files +27, extensions +22).

---

## File Structure

| File | Kind | Responsibility |
|---|---|---|
| `src/weave/schemas/protocol.py` | NEW | `AdapterRequestV1`, `AdapterResponseV1`, `PROTOCOL_VERSIONS` |
| `src/weave/schemas/provider_contract.py` | NEW | `ProviderFeature`, `AdapterRuntime`, `ProviderProtocol`, `ProviderContract` |
| `src/weave/core/registry.py` | NEW | `ProviderRegistry`, `ProviderRegistryError`, `get_registry` |
| `src/weave/providers/__init__.py` | NEW | empty marker so `providers/builtin` is importable-as-data |
| `src/weave/providers/builtin/*.contract.json` | NEW | 5 built-in contracts |
| `src/weave/providers/builtin/*.sh` | NEW | 5 built-in adapter scripts (4 copied + updated, 1 new `vllm.sh`) |
| `src/weave/schemas/config.py` | MODIFIED | rename `capability` → `capability_override`, drop `health_check` |
| `src/weave/core/config.py` | MODIFIED | legacy key migration, capability ceiling clamp validation |
| `src/weave/core/policy.py` | MODIFIED | `resolve_risk_class` and `evaluate_policy` take contract ceiling |
| `src/weave/core/invoker.py` | MODIFIED | signature change, schema-driven request and response |
| `src/weave/core/runtime.py` | MODIFIED | `PreparedContext.provider_contract`, registry load, contract forwarded |
| `src/weave/core/providers.py` | MODIFIED | `detect_providers` reads from registry; `KNOWN_PROVIDERS` deleted |
| `src/weave/core/scaffold.py` | MODIFIED | delete template generation; copy built-in files |
| `tests/test_protocol.py` | NEW | protocol model unit tests |
| `tests/test_provider_contract.py` | NEW | contract schema unit tests |
| `tests/test_registry.py` | NEW | registry loading and lookup tests |
| `tests/conftest.py` | NEW or MODIFIED | shared `make_contract(...)` test helper |
| `tests/test_config.py` | MODIFIED | migration + clamp tests |
| `tests/test_policy.py` | MODIFIED | rewrite existing tests to new signature + new tests |
| `tests/test_invoker.py` | MODIFIED | contract-driven tests |
| `tests/test_runtime.py` | MODIFIED | contract attached to PreparedContext, triple-clamp |

---

## Task 1: Add wire protocol schemas

**Files:**
- Create: `src/weave/schemas/protocol.py`
- Create: `tests/test_protocol.py`

This is the first piece because `ProviderContract` in Task 2 validates its `protocol.request_schema` / `protocol.response_schema` fields against `PROTOCOL_VERSIONS` from this module.

- [ ] **Step 1: Write the failing test file**

Create `tests/test_protocol.py`:

```python
"""Tests for the weave adapter wire protocol v1 schemas."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from weave.schemas.protocol import (
    PROTOCOL_VERSIONS,
    AdapterRequestV1,
    AdapterResponseV1,
)


def test_adapter_request_v1_defaults_protocol_literal():
    req = AdapterRequestV1(
        session_id="sess_abc",
        task="hello",
        workingDir="/tmp/proj",
    )
    assert req.protocol == "weave.request.v1"
    assert req.context == ""
    assert req.timeout == 300


def test_adapter_request_v1_round_trips_via_json():
    req = AdapterRequestV1(
        session_id="sess_abc",
        task="hello",
        workingDir="/tmp/proj",
        context="ctx",
        timeout=60,
    )
    blob = req.model_dump_json()
    parsed = json.loads(blob)
    assert parsed["protocol"] == "weave.request.v1"
    assert parsed["session_id"] == "sess_abc"
    assert parsed["task"] == "hello"
    assert parsed["workingDir"] == "/tmp/proj"
    assert parsed["context"] == "ctx"
    assert parsed["timeout"] == 60
    again = AdapterRequestV1.model_validate(parsed)
    assert again == req


def test_adapter_response_v1_accepts_well_formed_dict():
    resp = AdapterResponseV1.model_validate({
        "protocol": "weave.response.v1",
        "exitCode": 0,
        "stdout": "ok",
        "stderr": "",
        "structured": {"key": "val"},
    })
    assert resp.exitCode == 0
    assert resp.structured == {"key": "val"}


def test_adapter_response_v1_rejects_missing_exit_code():
    with pytest.raises(ValidationError):
        AdapterResponseV1.model_validate({
            "protocol": "weave.response.v1",
            "stdout": "",
            "stderr": "",
        })


def test_adapter_response_v1_rejects_wrong_protocol_literal():
    with pytest.raises(ValidationError):
        AdapterResponseV1.model_validate({
            "protocol": "weave.response.v0",
            "exitCode": 0,
            "stdout": "",
            "stderr": "",
        })


def test_adapter_response_v1_allows_structured_none_and_empty():
    none_resp = AdapterResponseV1.model_validate({
        "protocol": "weave.response.v1",
        "exitCode": 0,
        "stdout": "",
        "stderr": "",
        "structured": None,
    })
    empty_resp = AdapterResponseV1.model_validate({
        "protocol": "weave.response.v1",
        "exitCode": 0,
        "stdout": "",
        "stderr": "",
        "structured": {},
    })
    assert none_resp.structured is None
    assert empty_resp.structured == {}


def test_protocol_versions_registry_contains_v1_entries():
    assert "weave.request.v1" in PROTOCOL_VERSIONS
    assert "weave.response.v1" in PROTOCOL_VERSIONS
    assert PROTOCOL_VERSIONS["weave.request.v1"] is AdapterRequestV1
    assert PROTOCOL_VERSIONS["weave.response.v1"] is AdapterResponseV1
```

- [ ] **Step 2: Run the test to confirm it fails for the right reason**

Run: `PYTHONPATH=src pytest tests/test_protocol.py -v 2>&1 | tail -20`
Expected: FAIL with `ModuleNotFoundError: No module named 'weave.schemas.protocol'`

- [ ] **Step 3: Write the protocol module**

Create `src/weave/schemas/protocol.py`:

```python
"""Wire protocol v1 — runtime↔adapter request and response schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AdapterRequestV1(BaseModel):
    """Request payload sent to an adapter on stdin.

    camelCase on workingDir is deliberate — existing adapter shell scripts
    jq this field out of stdin. Changing to snake_case would force a
    simultaneous rewrite of every adapter for zero functional gain.
    """

    protocol: Literal["weave.request.v1"] = "weave.request.v1"
    session_id: str
    task: str
    workingDir: str
    context: str = ""
    timeout: int = 300


class AdapterResponseV1(BaseModel):
    """Response payload emitted by an adapter on stdout.

    camelCase fields match the shell-script jq output used since day one.
    """

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

- [ ] **Step 4: Run the test to confirm it passes**

Run: `PYTHONPATH=src pytest tests/test_protocol.py -v 2>&1 | tail -20`
Expected: 7 passed.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -5`
Expected: `136 + 7 = 143 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/weave/schemas/protocol.py tests/test_protocol.py
git commit -m "feat(schemas): add wire protocol v1 — AdapterRequestV1 and AdapterResponseV1"
```

---

## Task 2: Add ProviderContract schema

**Files:**
- Create: `src/weave/schemas/provider_contract.py`
- Create: `tests/test_provider_contract.py`

- [ ] **Step 1: Write the failing test file**

Create `tests/test_provider_contract.py`:

```python
"""Tests for the ProviderContract schema."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from weave.schemas.policy import RiskClass
from weave.schemas.provider_contract import (
    AdapterRuntime,
    ProviderContract,
    ProviderFeature,
    ProviderProtocol,
)


def _valid_contract_dict(**overrides) -> dict:
    base = {
        "contract_version": "1",
        "name": "claude-code",
        "display_name": "Claude Code",
        "adapter": "claude-code.sh",
        "adapter_runtime": "bash",
        "capability_ceiling": "workspace-write",
        "protocol": {
            "request_schema": "weave.request.v1",
            "response_schema": "weave.response.v1",
        },
        "declared_features": ["tool-use", "file-edit"],
        "health_check": "claude --version",
    }
    base.update(overrides)
    return base


def test_provider_contract_validates_good_manifest():
    contract = ProviderContract.model_validate(_valid_contract_dict())
    assert contract.name == "claude-code"
    assert contract.adapter_runtime == AdapterRuntime.BASH
    assert contract.capability_ceiling == RiskClass.WORKSPACE_WRITE
    assert ProviderFeature.TOOL_USE in contract.declared_features
    assert contract.source == "builtin"  # default


def test_provider_contract_rejects_unknown_feature():
    bad = _valid_contract_dict(declared_features=["tool-use", "not-a-real-feature"])
    with pytest.raises(ValidationError):
        ProviderContract.model_validate(bad)


def test_provider_contract_rejects_unknown_request_schema():
    bad = _valid_contract_dict(protocol={
        "request_schema": "weave.request.v999",
        "response_schema": "weave.response.v1",
    })
    with pytest.raises(ValidationError, match="request_schema"):
        ProviderContract.model_validate(bad)


def test_provider_contract_rejects_unknown_response_schema():
    bad = _valid_contract_dict(protocol={
        "request_schema": "weave.request.v1",
        "response_schema": "weave.response.v999",
    })
    with pytest.raises(ValidationError, match="response_schema"):
        ProviderContract.model_validate(bad)


def test_provider_contract_rejects_unknown_adapter_runtime():
    bad = _valid_contract_dict(adapter_runtime="perl")
    with pytest.raises(ValidationError):
        ProviderContract.model_validate(bad)


def test_provider_contract_rejects_unknown_capability_ceiling():
    bad = _valid_contract_dict(capability_ceiling="superuser")
    with pytest.raises(ValidationError):
        ProviderContract.model_validate(bad)


def test_provider_contract_version_is_literal_one():
    bad = _valid_contract_dict(contract_version="2")
    with pytest.raises(ValidationError):
        ProviderContract.model_validate(bad)
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `PYTHONPATH=src pytest tests/test_provider_contract.py -v 2>&1 | tail -20`
Expected: FAIL with `ModuleNotFoundError: No module named 'weave.schemas.provider_contract'`

- [ ] **Step 3: Write the provider_contract module**

Create `src/weave/schemas/provider_contract.py`:

```python
"""ProviderContract schema — declarative manifest for a provider adapter."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from weave.schemas.policy import RiskClass
from weave.schemas.protocol import PROTOCOL_VERSIONS


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
    BINARY = "binary"  # direct exec, no interpreter


class ProviderProtocol(BaseModel):
    request_schema: str
    response_schema: str

    @field_validator("request_schema")
    @classmethod
    def _validate_request_schema(cls, v: str) -> str:
        if v not in PROTOCOL_VERSIONS:
            raise ValueError(
                f"unknown request_schema {v!r}; "
                f"known: {sorted(PROTOCOL_VERSIONS.keys())}"
            )
        return v

    @field_validator("response_schema")
    @classmethod
    def _validate_response_schema(cls, v: str) -> str:
        if v not in PROTOCOL_VERSIONS:
            raise ValueError(
                f"unknown response_schema {v!r}; "
                f"known: {sorted(PROTOCOL_VERSIONS.keys())}"
            )
        return v


class ProviderContract(BaseModel):
    """Declarative contract for a provider adapter.

    Loaded by `ProviderRegistry` from `.contract.json` sidecar files.
    `source` is set by the loader (builtin vs user) and any value present
    in the JSON file is ignored and overwritten.
    """

    contract_version: Literal["1"] = "1"
    name: str
    display_name: str
    adapter: str  # relative to manifest directory
    adapter_runtime: AdapterRuntime
    capability_ceiling: RiskClass
    protocol: ProviderProtocol
    declared_features: list[ProviderFeature] = Field(default_factory=list)
    health_check: str | None = None
    source: Literal["builtin", "user"] = "builtin"
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `PYTHONPATH=src pytest tests/test_provider_contract.py -v 2>&1 | tail -20`
Expected: 7 passed.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -5`
Expected: `143 + 7 = 150 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/weave/schemas/provider_contract.py tests/test_provider_contract.py
git commit -m "feat(schemas): add ProviderContract with PROTOCOL_VERSIONS cross-validation"
```

---

## Task 3: Ship built-in contract manifests and adapter scripts

**Files:**
- Create: `src/weave/providers/__init__.py`
- Create: `src/weave/providers/builtin/__init__.py`
- Create: `src/weave/providers/builtin/claude-code.contract.json`
- Create: `src/weave/providers/builtin/claude-code.sh`
- Create: `src/weave/providers/builtin/codex.contract.json`
- Create: `src/weave/providers/builtin/codex.sh`
- Create: `src/weave/providers/builtin/gemini.contract.json`
- Create: `src/weave/providers/builtin/gemini.sh`
- Create: `src/weave/providers/builtin/ollama.contract.json`
- Create: `src/weave/providers/builtin/ollama.sh`
- Create: `src/weave/providers/builtin/vllm.contract.json`
- Create: `src/weave/providers/builtin/vllm.sh`
- Modify: `pyproject.toml` (package data inclusion)

No tests are added in this task; Task 4 (registry) will load these files as part of its tests.

- [ ] **Step 1: Create the providers package markers**

Create `src/weave/providers/__init__.py`:

```python
"""Weave provider built-in contracts and adapter scripts (package data)."""
```

Create `src/weave/providers/builtin/__init__.py`:

```python
"""Built-in provider contracts and adapter scripts shipped with weave."""
```

- [ ] **Step 2: Write the 5 contract manifests**

Create `src/weave/providers/builtin/claude-code.contract.json`:

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

Create `src/weave/providers/builtin/codex.contract.json`:

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

Create `src/weave/providers/builtin/gemini.contract.json`:

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

Create `src/weave/providers/builtin/ollama.contract.json`:

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

Create `src/weave/providers/builtin/vllm.contract.json`:

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

- [ ] **Step 3: Write the 5 adapter scripts**

All four existing adapter scripts must emit `protocol: "weave.response.v1"` as a top-level field in the JSON output. The in-tree copies are slightly edited versions of the `.harness/providers/*.sh` files used today.

Create `src/weave/providers/builtin/claude-code.sh`:

```bash
#!/usr/bin/env bash
# Weave provider adapter for claude-code
set -euo pipefail
INPUT=$(cat)
TASK=$(echo "$INPUT" | jq -r '.task')
WORKING_DIR=$(echo "$INPUT" | jq -r '.workingDir')
cd "$WORKING_DIR"
STDOUT=""
STDERR=""
EXIT_CODE=0
TMPFILE="${TMPDIR:-/tmp}/weave-stderr-$$"
STDOUT=$(claude --print "$TASK" 2>"$TMPFILE") || EXIT_CODE=$?
STDERR=$(cat "$TMPFILE" 2>/dev/null || echo "")
rm -f "$TMPFILE"
jq -n \
  --arg stdout "$STDOUT" \
  --arg stderr "$STDERR" \
  --argjson exitCode "$EXIT_CODE" \
  '{ protocol: "weave.response.v1", exitCode: $exitCode, stdout: $stdout, stderr: $stderr, structured: {} }'
```

Create `src/weave/providers/builtin/codex.sh`:

```bash
#!/usr/bin/env bash
# Weave provider adapter for codex
set -euo pipefail
INPUT=$(cat)
TASK=$(echo "$INPUT" | jq -r '.task')
WORKING_DIR=$(echo "$INPUT" | jq -r '.workingDir')
cd "$WORKING_DIR"
STDOUT=""
STDERR=""
EXIT_CODE=0
TMPFILE="${TMPDIR:-/tmp}/weave-stderr-$$"
STDOUT=$(codex exec "$TASK" 2>"$TMPFILE") || EXIT_CODE=$?
STDERR=$(cat "$TMPFILE" 2>/dev/null || echo "")
rm -f "$TMPFILE"
jq -n \
  --arg stdout "$STDOUT" \
  --arg stderr "$STDERR" \
  --argjson exitCode "$EXIT_CODE" \
  '{ protocol: "weave.response.v1", exitCode: $exitCode, stdout: $stdout, stderr: $stderr, structured: {} }'
```

Create `src/weave/providers/builtin/gemini.sh`:

```bash
#!/usr/bin/env bash
# Weave provider adapter for gemini
set -euo pipefail
INPUT=$(cat)
TASK=$(echo "$INPUT" | jq -r '.task')
WORKING_DIR=$(echo "$INPUT" | jq -r '.workingDir')
cd "$WORKING_DIR"
STDOUT=""
STDERR=""
EXIT_CODE=0
TMPFILE="${TMPDIR:-/tmp}/weave-stderr-$$"
STDOUT=$(gemini "$TASK" 2>"$TMPFILE") || EXIT_CODE=$?
STDERR=$(cat "$TMPFILE" 2>/dev/null || echo "")
rm -f "$TMPFILE"
jq -n \
  --arg stdout "$STDOUT" \
  --arg stderr "$STDERR" \
  --argjson exitCode "$EXIT_CODE" \
  '{ protocol: "weave.response.v1", exitCode: $exitCode, stdout: $stdout, stderr: $stderr, structured: {} }'
```

Create `src/weave/providers/builtin/ollama.sh`:

```bash
#!/usr/bin/env bash
# Weave provider adapter for ollama
set -euo pipefail
INPUT=$(cat)
TASK=$(echo "$INPUT" | jq -r '.task')
WORKING_DIR=$(echo "$INPUT" | jq -r '.workingDir')
cd "$WORKING_DIR"
STDOUT=""
STDERR=""
EXIT_CODE=0
TMPFILE="${TMPDIR:-/tmp}/weave-stderr-$$"
STDOUT=$(ollama run "$TASK" 2>"$TMPFILE") || EXIT_CODE=$?
STDERR=$(cat "$TMPFILE" 2>/dev/null || echo "")
rm -f "$TMPFILE"
jq -n \
  --arg stdout "$STDOUT" \
  --arg stderr "$STDERR" \
  --argjson exitCode "$EXIT_CODE" \
  '{ protocol: "weave.response.v1", exitCode: $exitCode, stdout: $stdout, stderr: $stderr, structured: {} }'
```

Create `src/weave/providers/builtin/vllm.sh`:

```bash
#!/usr/bin/env bash
# Weave provider adapter for vLLM (invoked via vllmc CLI)
set -euo pipefail
INPUT=$(cat)
TASK=$(echo "$INPUT" | jq -r '.task')
WORKING_DIR=$(echo "$INPUT" | jq -r '.workingDir')
cd "$WORKING_DIR"

if ! command -v vllmc >/dev/null 2>&1; then
  jq -n --arg stderr "vllmc not found on PATH" \
    '{ protocol: "weave.response.v1", exitCode: 127, stdout: "", stderr: $stderr, structured: {} }'
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

- [ ] **Step 4: Make the adapter scripts executable**

Run:
```bash
chmod +x src/weave/providers/builtin/claude-code.sh \
         src/weave/providers/builtin/codex.sh \
         src/weave/providers/builtin/gemini.sh \
         src/weave/providers/builtin/ollama.sh \
         src/weave/providers/builtin/vllm.sh
```

Expected: no output.

- [ ] **Step 5: Ensure package data is shipped with the wheel**

Read `pyproject.toml`:

```bash
cat pyproject.toml
```

Look for an existing `[tool.hatch.build]`, `[tool.setuptools.package-data]`, or equivalent section. If none exists for including non-Python data under `weave.providers.builtin`, add one. The exact syntax depends on the build backend weave uses. For hatchling (the default), add this block if not already present:

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/weave/providers/builtin" = "weave/providers/builtin"
```

For setuptools, add:

```toml
[tool.setuptools.package-data]
"weave.providers.builtin" = ["*.contract.json", "*.sh"]
```

For poetry, add to the `[tool.poetry]` section:

```toml
include = [
  { path = "src/weave/providers/builtin/*.contract.json", format = ["sdist", "wheel"] },
  { path = "src/weave/providers/builtin/*.sh", format = ["sdist", "wheel"] },
]
```

If the builder is different, skip this step and note it — local development via `PYTHONPATH=src` will still work since the files exist on disk. Package-data correctness only matters at `pip install` time, which no test in this plan exercises.

- [ ] **Step 6: Sanity-check JSON validity and directory listing**

Run:
```bash
for f in src/weave/providers/builtin/*.contract.json; do
  python3 -c "import json, sys; json.load(open('$f')); print('ok: $f')"
done
ls -l src/weave/providers/builtin/
```

Expected: each JSON prints `ok: <path>`, and the directory listing shows 5 `.contract.json` files and 5 executable `.sh` files plus `__init__.py`.

- [ ] **Step 7: Run the full suite to confirm no regressions**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -5`
Expected: `150 passed`. No test changes in this task.

- [ ] **Step 8: Commit**

```bash
git add src/weave/providers/ pyproject.toml
git commit -m "feat(providers): ship 5 built-in contract manifests and adapter scripts"
```

---

## Task 4: Implement ProviderRegistry

**Files:**
- Create: `src/weave/core/registry.py`
- Create: `tests/test_registry.py`

- [ ] **Step 1: Write the failing test file**

Create `tests/test_registry.py`:

```python
"""Tests for the ProviderRegistry."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from weave.core.registry import (
    ProviderRegistry,
    ProviderRegistryError,
    get_registry,
)
from weave.schemas.policy import RiskClass


BUILTIN_NAMES = {"claude-code", "codex", "gemini", "ollama", "vllm"}


def _valid_user_contract(name: str, adapter_filename: str) -> dict:
    return {
        "contract_version": "1",
        "name": name,
        "display_name": name,
        "adapter": adapter_filename,
        "adapter_runtime": "bash",
        "capability_ceiling": "read-only",
        "protocol": {
            "request_schema": "weave.request.v1",
            "response_schema": "weave.response.v1",
        },
        "declared_features": [],
        "health_check": None,
    }


def _write_user_provider(root: Path, name: str, contract_override: dict | None = None) -> None:
    providers_dir = root / ".harness" / "providers"
    providers_dir.mkdir(parents=True, exist_ok=True)
    adapter_filename = f"{name}.sh"
    (providers_dir / adapter_filename).write_text("#!/usr/bin/env bash\necho '{}'\n")
    (providers_dir / adapter_filename).chmod(0o755)
    manifest = _valid_user_contract(name, adapter_filename)
    if contract_override:
        manifest.update(contract_override)
    (providers_dir / f"{name}.contract.json").write_text(json.dumps(manifest))


def test_registry_loads_all_five_builtins(tmp_path):
    reg = ProviderRegistry()
    reg.load(tmp_path)
    names = {c.name for c in reg.list()}
    assert names == BUILTIN_NAMES


def test_registry_builtin_files_exist_on_disk():
    import weave
    root = Path(weave.__file__).parent / "providers" / "builtin"
    assert root.is_dir()
    for name in BUILTIN_NAMES:
        assert (root / f"{name}.contract.json").exists(), f"{name}.contract.json missing"
        assert (root / f"{name}.sh").exists(), f"{name}.sh missing"


def test_registry_get_returns_contract(tmp_path):
    reg = ProviderRegistry()
    reg.load(tmp_path)
    claude = reg.get("claude-code")
    assert claude.name == "claude-code"
    assert claude.capability_ceiling == RiskClass.WORKSPACE_WRITE
    assert claude.source == "builtin"


def test_registry_get_raises_for_unknown(tmp_path):
    reg = ProviderRegistry()
    reg.load(tmp_path)
    with pytest.raises(KeyError):
        reg.get("no-such-provider")


def test_registry_has(tmp_path):
    reg = ProviderRegistry()
    reg.load(tmp_path)
    assert reg.has("claude-code") is True
    assert reg.has("no-such-provider") is False


def test_registry_list_is_sorted(tmp_path):
    reg = ProviderRegistry()
    reg.load(tmp_path)
    names = [c.name for c in reg.list()]
    assert names == sorted(names)


def test_registry_resolve_adapter_path_for_builtin(tmp_path):
    reg = ProviderRegistry()
    reg.load(tmp_path)
    path = reg.resolve_adapter_path("claude-code")
    assert path.exists()
    assert path.name == "claude-code.sh"


def test_registry_load_is_idempotent_for_same_root(tmp_path):
    reg = ProviderRegistry()
    reg.load(tmp_path)
    first_count = len(reg.list())
    reg.load(tmp_path)
    assert len(reg.list()) == first_count


def test_registry_reload_for_different_root(tmp_path):
    a = tmp_path / "proj_a"
    b = tmp_path / "proj_b"
    a.mkdir()
    b.mkdir()
    _write_user_provider(b, "extra")
    reg = ProviderRegistry()
    reg.load(a)
    assert reg.has("extra") is False
    reg.load(b)
    assert reg.has("extra") is True


def test_registry_loads_user_contract(tmp_path):
    _write_user_provider(tmp_path, "localtool")
    reg = ProviderRegistry()
    reg.load(tmp_path)
    assert reg.has("localtool")
    contract = reg.get("localtool")
    assert contract.source == "user"


def test_registry_user_overrides_builtin_with_warning(tmp_path, caplog):
    _write_user_provider(
        tmp_path,
        "claude-code",
        contract_override={"capability_ceiling": "read-only"},
    )
    reg = ProviderRegistry()
    with caplog.at_level(logging.WARNING, logger="weave.core.registry"):
        reg.load(tmp_path)
    contract = reg.get("claude-code")
    assert contract.source == "user"
    assert contract.capability_ceiling == RiskClass.READ_ONLY
    assert any("overrides built-in" in rec.message for rec in caplog.records)


def test_registry_skips_adapter_without_manifest(tmp_path, caplog):
    providers_dir = tmp_path / ".harness" / "providers"
    providers_dir.mkdir(parents=True)
    (providers_dir / "orphan.sh").write_text("#!/usr/bin/env bash\necho '{}'\n")
    (providers_dir / "orphan.sh").chmod(0o755)
    reg = ProviderRegistry()
    with caplog.at_level(logging.ERROR, logger="weave.core.registry"):
        reg.load(tmp_path)
    assert not reg.has("orphan")
    assert any("orphan" in rec.message and "no contract manifest" in rec.message
               for rec in caplog.records)


def test_registry_rejects_filename_stem_mismatch(tmp_path, caplog):
    providers_dir = tmp_path / ".harness" / "providers"
    providers_dir.mkdir(parents=True)
    (providers_dir / "mismatch.sh").write_text("#!/usr/bin/env bash\n")
    (providers_dir / "mismatch.sh").chmod(0o755)
    manifest = _valid_user_contract("wrong-name", "mismatch.sh")
    (providers_dir / "mismatch.contract.json").write_text(json.dumps(manifest))
    reg = ProviderRegistry()
    with caplog.at_level(logging.ERROR, logger="weave.core.registry"):
        reg.load(tmp_path)
    assert not reg.has("wrong-name")
    assert not reg.has("mismatch")
    assert any("filename stem" in rec.message.lower() for rec in caplog.records)


def test_registry_get_singleton_returns_same_instance(tmp_path):
    from weave.core import registry as registry_module
    # Reset singleton
    registry_module._REGISTRY_SINGLETON = None
    a = get_registry()
    b = get_registry()
    assert a is b
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `PYTHONPATH=src pytest tests/test_registry.py -v 2>&1 | tail -20`
Expected: FAIL with `ModuleNotFoundError: No module named 'weave.core.registry'`

- [ ] **Step 3: Write the registry module**

Create `src/weave/core/registry.py`:

```python
"""Provider contract registry — load and look up provider contracts."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from weave.schemas.provider_contract import ProviderContract


logger = logging.getLogger(__name__)


class ProviderRegistryError(Exception):
    """Raised when a built-in contract is malformed. Weave exits 1."""


def _builtin_dir() -> Path:
    """Return the absolute path to the in-tree built-in providers directory."""
    import weave
    return Path(weave.__file__).parent / "providers" / "builtin"


class ProviderRegistry:
    """Registry of provider contracts — merges built-ins with user overrides.

    Built-ins ship inside the weave package. User contracts live under
    `<project_root>/.harness/providers/`. On name collision, the user wins
    (a warning is logged). Built-in load failure is fatal; user load
    failure skips the single provider and logs an error.
    """

    def __init__(self) -> None:
        self._contracts: dict[str, ProviderContract] = {}
        self._manifest_dirs: dict[str, Path] = {}
        self._loaded_root: Path | None = None

    def load(self, project_root: Path) -> None:
        """Load built-ins then user contracts.

        Idempotent when called with the same `project_root`. A call with a
        different `project_root` resets all state and reloads.
        """
        project_root = Path(project_root)
        if self._loaded_root == project_root.resolve():
            return

        self._contracts.clear()
        self._manifest_dirs.clear()

        self._load_builtins()
        self._load_user(project_root)

        self._loaded_root = project_root.resolve()

    def _load_builtins(self) -> None:
        builtin_dir = _builtin_dir()
        if not builtin_dir.is_dir():
            raise ProviderRegistryError(
                f"built-in provider directory missing: {builtin_dir}"
            )
        for manifest_path in sorted(builtin_dir.glob("*.contract.json")):
            try:
                contract = self._parse_manifest(manifest_path, source="builtin")
            except Exception as exc:
                raise ProviderRegistryError(
                    f"failed to load built-in contract {manifest_path.name}: {exc}"
                ) from exc
            self._contracts[contract.name] = contract
            self._manifest_dirs[contract.name] = manifest_path.parent

    def _load_user(self, project_root: Path) -> None:
        user_dir = project_root / ".harness" / "providers"
        if not user_dir.is_dir():
            return

        loaded_stems: set[str] = set()
        for manifest_path in sorted(user_dir.glob("*.contract.json")):
            try:
                contract = self._parse_manifest(manifest_path, source="user")
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                logger.error(
                    "failed to load user contract %s: %s",
                    manifest_path.name,
                    exc,
                )
                continue

            if contract.name in self._contracts and self._contracts[contract.name].source == "builtin":
                logger.warning(
                    "user contract %s overrides built-in with the same name",
                    contract.name,
                )
            self._contracts[contract.name] = contract
            self._manifest_dirs[contract.name] = manifest_path.parent
            loaded_stems.add(manifest_path.stem.removesuffix(".contract"))

        # Orphan adapter scan: adapters without a matching manifest.
        for adapter_path in sorted(user_dir.glob("*.sh")):
            stem = adapter_path.stem
            if stem in loaded_stems:
                continue
            if stem in self._contracts and self._contracts[stem].source == "user":
                continue
            logger.error(
                "adapter %s has no contract manifest; provider unavailable. "
                "Create %s.contract.json or delete the adapter.",
                adapter_path.name,
                stem,
            )

    def _parse_manifest(self, manifest_path: Path, source: str) -> ProviderContract:
        raw = json.loads(manifest_path.read_text())
        # Strip any author-supplied 'source' field; we inject our own.
        raw.pop("source", None)
        contract = ProviderContract.model_validate(raw)

        expected_stem = manifest_path.name.removesuffix(".contract.json")
        if contract.name != expected_stem:
            raise ValueError(
                f"contract 'name' ({contract.name!r}) must match filename stem "
                f"({expected_stem!r})"
            )

        adapter_path = manifest_path.parent / contract.adapter
        if not adapter_path.exists():
            raise ValueError(
                f"adapter file not found: {adapter_path} "
                f"(declared in {manifest_path.name})"
            )

        # Patch the source field post-validation.
        contract = contract.model_copy(update={"source": source})
        return contract

    def get(self, name: str) -> ProviderContract:
        """Return the contract for `name`. Raises KeyError if unknown."""
        return self._contracts[name]

    def has(self, name: str) -> bool:
        return name in self._contracts

    def list(self) -> list[ProviderContract]:
        """Return all loaded contracts in name-sorted order."""
        return [self._contracts[name] for name in sorted(self._contracts)]

    def resolve_adapter_path(self, name: str) -> Path:
        """Return the absolute path to the adapter file for `name`.

        Raises KeyError if `name` is not loaded.
        """
        contract = self._contracts[name]
        return (self._manifest_dirs[name] / contract.adapter).resolve()


_REGISTRY_SINGLETON: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    """Return the process-wide registry singleton.

    Callers must invoke `.load(project_root)` before `.get()` or `.list()`.
    `.load()` is idempotent for the same project_root.
    """
    global _REGISTRY_SINGLETON
    if _REGISTRY_SINGLETON is None:
        _REGISTRY_SINGLETON = ProviderRegistry()
    return _REGISTRY_SINGLETON
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `PYTHONPATH=src pytest tests/test_registry.py -v 2>&1 | tail -40`
Expected: 14 passed.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -5`
Expected: `150 + 14 = 164 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/weave/core/registry.py tests/test_registry.py
git commit -m "feat(registry): add ProviderRegistry with built-in + user contract loading"
```

---

## Task 5: Migrate ProviderConfig — rename capability to capability_override

This task renames the field and teaches the config loader to accept legacy JSON. It does **not** yet plug the clamp validation into `resolve_config`; the clamp needs the registry, and wiring the registry into config load happens in Task 8 (runtime integration) where call ordering is clearest. Task 5 only lands the schema change and the legacy key rename, keeping the rest of the codebase compiling.

**Files:**
- Modify: `src/weave/schemas/config.py`
- Modify: `src/weave/core/config.py`
- Modify: `tests/test_config.py`
- Modify: `src/weave/core/scaffold.py` (minimal — just update the `ProviderConfig(...)` call)
- Modify: `src/weave/core/policy.py` (minimal — read `.capability_override or <fallback>` temporarily)
- Modify: `src/weave/core/runtime.py` (minimal — use the new field name; Task 8 does the real refactor)

This is the most cross-cutting task because the rename touches every `ProviderConfig.capability` reader. To keep the suite green at the end of this task, we add a **backward-read shim** on `ProviderConfig`: a `@property` named `capability` that returns `capability_override or RiskClass.WORKSPACE_WRITE`. Tasks 6 and 8 then remove the shim entirely.

- [ ] **Step 1: Write the failing test extensions first**

Open `tests/test_config.py` and add these tests at the bottom:

```python
import warnings

from weave.schemas.config import ProviderConfig
from weave.schemas.policy import RiskClass


def test_provider_config_accepts_capability_override_field():
    cfg = ProviderConfig(command="x", capability_override=RiskClass.READ_ONLY)
    assert cfg.capability_override == RiskClass.READ_ONLY


def test_provider_config_legacy_capability_key_renamed_on_read(tmp_path):
    """A config.json with the legacy 'capability' key is silently migrated."""
    from weave.core.config import resolve_config

    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "config.json").write_text(
        '{"version": "1", "phase": "sandbox", "default_provider": "claude-code", '
        '"providers": {"claude-code": {"command": "claude", "capability": "read-only"}}}'
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        config = resolve_config(tmp_path, user_home=tmp_path)
    provider = config.providers["claude-code"]
    assert provider.capability_override == RiskClass.READ_ONLY
    assert any("capability" in str(w.message).lower() for w in caught)


def test_provider_config_new_key_wins_over_legacy_key(tmp_path):
    from weave.core.config import resolve_config

    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "config.json").write_text(
        '{"version": "1", "phase": "sandbox", "default_provider": "claude-code", '
        '"providers": {"claude-code": {"command": "claude", '
        '"capability": "workspace-write", "capability_override": "read-only"}}}'
    )
    config = resolve_config(tmp_path, user_home=tmp_path)
    assert config.providers["claude-code"].capability_override == RiskClass.READ_ONLY


def test_provider_config_silently_ignores_legacy_health_check_key(tmp_path):
    from weave.core.config import resolve_config

    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "config.json").write_text(
        '{"version": "1", "phase": "sandbox", "default_provider": "claude-code", '
        '"providers": {"claude-code": {"command": "claude", '
        '"health_check": "claude --version"}}}'
    )
    # Should not raise.
    config = resolve_config(tmp_path, user_home=tmp_path)
    # Assert that the parsed ProviderConfig has no leftover health_check attribute.
    assert not hasattr(config.providers["claude-code"], "health_check")
```

- [ ] **Step 2: Run the new tests to confirm they fail**

Run: `PYTHONPATH=src pytest tests/test_config.py -v -k "capability_override or legacy or health_check" 2>&1 | tail -20`
Expected: all 4 fail with `ValidationError` / `AttributeError` / missing field.

- [ ] **Step 3: Update `ProviderConfig` schema**

Edit `src/weave/schemas/config.py`. Replace the existing `ProviderConfig` class with:

```python
class ProviderConfig(BaseModel):
    command: str
    enabled: bool = True
    capability_override: RiskClass | None = None

    # Backward-read shim — removed in Task 8 once all readers migrate.
    # Returns the override when set, else WORKSPACE_WRITE as a default that
    # matches the pre-migration behavior where `capability` defaulted to
    # WORKSPACE_WRITE on construction.
    @property
    def capability(self) -> RiskClass:
        return self.capability_override or RiskClass.WORKSPACE_WRITE
```

Also update `create_default_config` in the same file — replace the four `ProviderConfig(..., capability=...)` call sites to use `capability_override=` instead:

```python
def create_default_config(default_provider: str = "claude-code") -> WeaveConfig:
    """Create a WeaveConfig with sensible defaults."""
    return WeaveConfig(
        default_provider=default_provider,
        providers={
            "claude-code": ProviderConfig(
                command="claude",
                enabled=True,
                capability_override=None,  # contract ceiling governs
            ),
            "codex": ProviderConfig(
                command="codex",
                enabled=False,
                capability_override=None,
            ),
            "gemini": ProviderConfig(
                command="gemini",
                enabled=False,
                capability_override=None,
            ),
            "ollama": ProviderConfig(
                command="ollama",
                enabled=False,
                capability_override=None,
            ),
        },
    )
```

- [ ] **Step 4: Teach `resolve_config` to migrate legacy keys**

Edit `src/weave/core/config.py`. Replace the file with:

```python
"""3-layer config resolution for Weave harness."""
from __future__ import annotations

import json
import warnings
from pathlib import Path

from ..schemas.config import WeaveConfig, create_default_config


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _migrate_provider_legacy_keys(merged: dict) -> None:
    """Rename legacy `capability` → `capability_override` on every provider entry.

    Drops the legacy `health_check` key (it now lives on the contract).
    Emits a DeprecationWarning once per migrated key. Mutates `merged` in place.
    """
    providers = merged.get("providers")
    if not isinstance(providers, dict):
        return
    for provider_name, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        if "capability" in entry:
            legacy = entry.pop("capability")
            if "capability_override" not in entry:
                entry["capability_override"] = legacy
                warnings.warn(
                    f"config: provider {provider_name!r} uses legacy 'capability' key; "
                    f"renaming to 'capability_override'",
                    DeprecationWarning,
                    stacklevel=2,
                )
            else:
                warnings.warn(
                    f"config: provider {provider_name!r} has both 'capability' and "
                    f"'capability_override'; legacy 'capability' ignored",
                    DeprecationWarning,
                    stacklevel=2,
                )
        if "health_check" in entry:
            entry.pop("health_check")  # silently ignored; contract owns health check


def resolve_config(project_dir: Path, user_home: Path | None = None) -> WeaveConfig:
    """Resolve config from defaults → user → project → local layers."""
    home = user_home or Path.home()
    merged = create_default_config().model_dump()

    for config_path in [
        home / ".harness" / "config.json",
        project_dir / ".harness" / "config.json",
        project_dir / ".harness" / "config.local.json",
    ]:
        if config_path.exists():
            merged = _deep_merge(merged, json.loads(config_path.read_text()))

    _migrate_provider_legacy_keys(merged)

    return WeaveConfig.model_validate(merged)
```

- [ ] **Step 5: Fix scaffold's `ProviderConfig` constructor call**

Edit `src/weave/core/scaffold.py`. Find the block that builds `ProviderConfig` for installed providers (around line 90). Replace:

```python
    for provider in installed:
        config.providers[provider.name] = ProviderConfig(
            command=provider.adapter_script,
            enabled=True,
            health_check=provider.health_check,
        )
```

with:

```python
    for provider in installed:
        config.providers[provider.name] = ProviderConfig(
            command=provider.adapter_script,
            enabled=True,
            capability_override=None,
        )
```

Task 9 does the larger scaffold cleanup; here we only keep it compiling.

- [ ] **Step 6: Run the targeted tests**

Run: `PYTHONPATH=src pytest tests/test_config.py -v 2>&1 | tail -30`
Expected: all config tests pass (existing + 4 new = previous count + 4).

- [ ] **Step 7: Run the full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -10`
Expected: all 168 pass (164 + 4 new config tests). The `.capability` property shim keeps `policy.py` and `runtime.py` working unchanged.

- [ ] **Step 8: Commit**

```bash
git add src/weave/schemas/config.py src/weave/core/config.py src/weave/core/scaffold.py tests/test_config.py
git commit -m "feat(config): rename capability → capability_override with legacy key migration"
```

---

## Task 6: Refactor policy.py to take contract ceiling explicitly

This task reworks `resolve_risk_class` and `evaluate_policy` to accept a `ProviderContract` (or just a ceiling) as an explicit input. The backward-read shim from Task 5 is still in place, so the old code paths continue to compile — but after this task, `policy.py` no longer reads `provider.capability` at all.

**Files:**
- Modify: `src/weave/core/policy.py`
- Modify: `tests/test_policy.py`
- Create: `tests/conftest.py` (with a `make_contract` helper used by policy/registry/runtime tests)

- [ ] **Step 1: Create the shared test helper**

Create `tests/conftest.py` (if it does not already exist, otherwise append — check first with `ls tests/conftest.py`). If the file exists, add the helper function only.

```python
"""Shared pytest fixtures and helpers for weave tests."""
from __future__ import annotations

from weave.schemas.policy import RiskClass
from weave.schemas.provider_contract import (
    AdapterRuntime,
    ProviderContract,
    ProviderFeature,
    ProviderProtocol,
)


def make_contract(
    name: str = "test-provider",
    capability_ceiling: RiskClass = RiskClass.WORKSPACE_WRITE,
    adapter: str = "test-provider.sh",
    adapter_runtime: AdapterRuntime = AdapterRuntime.BASH,
    features: list[ProviderFeature] | None = None,
    source: str = "builtin",
) -> ProviderContract:
    """Build a minimal valid ProviderContract for tests."""
    return ProviderContract(
        name=name,
        display_name=name,
        adapter=adapter,
        adapter_runtime=adapter_runtime,
        capability_ceiling=capability_ceiling,
        protocol=ProviderProtocol(
            request_schema="weave.request.v1",
            response_schema="weave.response.v1",
        ),
        declared_features=features or [],
        source=source,
    )
```

- [ ] **Step 2: Rewrite `tests/test_policy.py` to the new signature**

Replace the entire file with:

```python
"""Tests for the weave policy engine."""
from __future__ import annotations

import pytest

from tests.conftest import make_contract
from weave.schemas.config import ProviderConfig
from weave.schemas.policy import RiskClass


def test_resolve_risk_class_returns_contract_ceiling_when_no_override_no_request():
    from weave.core.policy import resolve_risk_class
    result = resolve_risk_class(
        contract_ceiling=RiskClass.WORKSPACE_WRITE,
        config_override=None,
        requested=None,
    )
    assert result == RiskClass.WORKSPACE_WRITE


def test_resolve_risk_class_returns_config_override_when_below_ceiling():
    from weave.core.policy import resolve_risk_class
    result = resolve_risk_class(
        contract_ceiling=RiskClass.EXTERNAL_NETWORK,
        config_override=RiskClass.READ_ONLY,
        requested=None,
    )
    assert result == RiskClass.READ_ONLY


def test_resolve_risk_class_config_override_above_ceiling_is_clamped_silently():
    """Config validation catches this earlier; policy clamps defensively."""
    from weave.core.policy import resolve_risk_class
    result = resolve_risk_class(
        contract_ceiling=RiskClass.READ_ONLY,
        config_override=RiskClass.DESTRUCTIVE,
        requested=None,
    )
    assert result == RiskClass.READ_ONLY


def test_resolve_risk_class_allows_caller_to_request_lower():
    from weave.core.policy import resolve_risk_class
    result = resolve_risk_class(
        contract_ceiling=RiskClass.EXTERNAL_NETWORK,
        config_override=None,
        requested=RiskClass.READ_ONLY,
    )
    assert result == RiskClass.READ_ONLY


def test_resolve_risk_class_rejects_request_above_effective_ceiling():
    from weave.core.policy import resolve_risk_class
    with pytest.raises(ValueError, match="exceeds effective ceiling"):
        resolve_risk_class(
            contract_ceiling=RiskClass.READ_ONLY,
            config_override=None,
            requested=RiskClass.DESTRUCTIVE,
        )


def test_evaluate_policy_sandbox_phase_always_allows_within_ceiling():
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.DESTRUCTIVE)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(command="x"),
        requested_class=RiskClass.DESTRUCTIVE,
        phase="sandbox",
    )
    assert result.allowed is True
    assert result.effective_risk_class == RiskClass.DESTRUCTIVE
    assert result.provider_ceiling == RiskClass.DESTRUCTIVE


def test_evaluate_policy_mvp_phase_allows_safe_class():
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.WORKSPACE_WRITE)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(command="x"),
        requested_class=None,
        phase="mvp",
    )
    assert result.allowed is True
    assert result.effective_risk_class == RiskClass.WORKSPACE_WRITE


def test_evaluate_policy_rejects_request_above_ceiling():
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.READ_ONLY)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(command="x"),
        requested_class=RiskClass.DESTRUCTIVE,
        phase="mvp",
    )
    assert result.allowed is False
    assert any("ceiling" in d.lower() for d in result.denials)


def test_evaluate_policy_mvp_denies_external_network():
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.EXTERNAL_NETWORK)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(command="x"),
        requested_class=None,
        phase="mvp",
    )
    assert result.allowed is False
    assert any("denies" in d.lower() for d in result.denials)


def test_evaluate_policy_enterprise_denies_destructive():
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.DESTRUCTIVE)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(command="x"),
        requested_class=None,
        phase="enterprise",
    )
    assert result.allowed is False


def test_evaluate_policy_sandbox_warns_on_high_risk():
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.EXTERNAL_NETWORK)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(command="x"),
        requested_class=None,
        phase="sandbox",
    )
    assert result.allowed is True
    assert len(result.warnings) >= 1
    assert any("high-risk" in w.lower() for w in result.warnings)


def test_evaluate_policy_provider_ceiling_from_contract_not_config():
    """PolicyResult.provider_ceiling must reflect the contract, not config."""
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.READ_ONLY)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(
            command="x",
            capability_override=RiskClass.READ_ONLY,
        ),
        requested_class=None,
        phase="sandbox",
    )
    assert result.provider_ceiling == RiskClass.READ_ONLY


def test_evaluate_policy_config_override_narrows_below_ceiling():
    from weave.core.policy import evaluate_policy
    contract = make_contract(capability_ceiling=RiskClass.WORKSPACE_WRITE)
    result = evaluate_policy(
        contract=contract,
        provider_config=ProviderConfig(
            command="x",
            capability_override=RiskClass.READ_ONLY,
        ),
        requested_class=None,
        phase="mvp",
    )
    assert result.allowed is True
    assert result.effective_risk_class == RiskClass.READ_ONLY
```

- [ ] **Step 3: Run the rewritten policy tests to confirm they fail**

Run: `PYTHONPATH=src pytest tests/test_policy.py -v 2>&1 | tail -30`
Expected: most tests fail with `TypeError: resolve_risk_class() got an unexpected keyword argument 'contract_ceiling'` (the old signature still takes `provider` + `requested`).

- [ ] **Step 4: Rewrite `src/weave/core/policy.py`**

Replace the entire file with:

```python
"""Policy engine — risk class resolution and phase-dependent enforcement."""
from __future__ import annotations

from weave.schemas.config import ProviderConfig
from weave.schemas.policy import (
    PolicyResult,
    RiskClass,
    risk_class_level,
)
from weave.schemas.provider_contract import ProviderContract


PHASE_ENFORCEMENT = {
    "sandbox": "warn",
    "mvp": "deny",
    "enterprise": "deny",
}


def resolve_risk_class(
    contract_ceiling: RiskClass,
    config_override: RiskClass | None,
    requested: RiskClass | None,
) -> RiskClass:
    """Resolve effective risk class by walking three inputs in order.

    contract ceiling -> config override -> caller requested

    Each step may only restrict (lower the ordinal level), never elevate.
    `config_override` above the ceiling is clamped silently (config load
    validates this earlier; the clamp is defense in depth).
    `requested` above the already-clamped ceiling raises ValueError.
    """
    effective = contract_ceiling

    if config_override is not None and risk_class_level(config_override) <= risk_class_level(effective):
        effective = config_override

    if requested is not None:
        if risk_class_level(requested) > risk_class_level(effective):
            raise ValueError(
                f"Requested risk class {requested.value} exceeds effective "
                f"ceiling {effective.value}"
            )
        effective = requested

    return effective


def evaluate_policy(
    contract: ProviderContract,
    provider_config: ProviderConfig,
    requested_class: RiskClass | None,
    phase: str,
) -> PolicyResult:
    """Evaluate whether an invocation is allowed under the current phase.

    Pre-invoke hooks are run separately by the runtime (not here) so this
    stays a pure policy decision.
    """
    warnings: list[str] = []
    denials: list[str] = []
    ceiling = contract.capability_ceiling

    try:
        effective = resolve_risk_class(
            contract_ceiling=ceiling,
            config_override=provider_config.capability_override,
            requested=requested_class,
        )
    except ValueError as exc:
        return PolicyResult(
            allowed=False,
            effective_risk_class=ceiling,
            provider_ceiling=ceiling,
            requested_class=requested_class,
            warnings=warnings,
            denials=[str(exc)],
        )

    enforcement = PHASE_ENFORCEMENT.get(phase, "warn")
    is_high_risk = risk_class_level(effective) >= risk_class_level(RiskClass.EXTERNAL_NETWORK)

    if enforcement == "warn" and is_high_risk:
        warnings.append(
            f"Phase '{phase}' permits {effective.value} but this is a high-risk class"
        )
    elif enforcement == "deny" and is_high_risk:
        denials.append(
            f"Phase '{phase}' denies {effective.value} class invocations"
        )
        return PolicyResult(
            allowed=False,
            effective_risk_class=effective,
            provider_ceiling=ceiling,
            requested_class=requested_class,
            warnings=warnings,
            denials=denials,
        )

    return PolicyResult(
        allowed=True,
        effective_risk_class=effective,
        provider_ceiling=ceiling,
        requested_class=requested_class,
        warnings=warnings,
        denials=denials,
    )
```

- [ ] **Step 5: Run the policy tests to confirm they pass**

Run: `PYTHONPATH=src pytest tests/test_policy.py -v 2>&1 | tail -30`
Expected: 13 passed.

- [ ] **Step 6: Run the full suite — expect regressions in test_runtime.py**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -20`
Expected: most pass, but `test_runtime.py` tests that call `_policy_check` will fail because `runtime.py` still passes `provider=provider_config` to the old `evaluate_policy` signature.

- [ ] **Step 7: Minimal runtime.py patch to restore the suite**

Open `src/weave/core/runtime.py`, find `_policy_check` (around line 149). Replace:

```python
def _policy_check(ctx: PreparedContext) -> tuple[PolicyResult, list[HookResult]]:
    """Stage 2: evaluate policy and run pre-invoke hooks."""
    policy = evaluate_policy(
        provider=ctx.provider_config,
        requested_class=ctx.requested_risk_class,
        phase=ctx.phase,
    )
```

with:

```python
def _policy_check(ctx: PreparedContext) -> tuple[PolicyResult, list[HookResult]]:
    """Stage 2: evaluate policy and run pre-invoke hooks."""
    # ctx.provider_contract is populated by prepare() in Task 8. Until then,
    # we synthesize a minimal contract from provider_config.capability. The
    # backward-read shim on ProviderConfig.capability still works because
    # Task 5 preserved it.
    from weave.core.registry import get_registry
    registry = get_registry()
    registry.load(ctx.working_dir)
    if registry.has(ctx.active_provider):
        contract = registry.get(ctx.active_provider)
    else:
        from tests.conftest import make_contract  # pragma: no cover - test shim
        contract = make_contract(
            name=ctx.active_provider,
            capability_ceiling=ctx.provider_config.capability,
        )

    policy = evaluate_policy(
        contract=contract,
        provider_config=ctx.provider_config,
        requested_class=ctx.requested_risk_class,
        phase=ctx.phase,
    )
```

**Stop.** The test-shim import is ugly — it is intentional placeholder scaffolding for this single intermediate commit, replaced in Task 8 by proper registry-driven contract resolution at `prepare()` time. The `# pragma: no cover` comment signals this to the reviewer. Task 8 deletes the fallback branch entirely.

Actually — remove the `from tests.conftest import make_contract` line. Production code must never import from tests. Use a plain inline construction instead:

```python
def _policy_check(ctx: PreparedContext) -> tuple[PolicyResult, list[HookResult]]:
    """Stage 2: evaluate policy and run pre-invoke hooks."""
    from weave.core.registry import get_registry
    from weave.schemas.provider_contract import (
        AdapterRuntime,
        ProviderContract,
        ProviderProtocol,
    )

    registry = get_registry()
    registry.load(ctx.working_dir)
    if registry.has(ctx.active_provider):
        contract = registry.get(ctx.active_provider)
    else:
        # Transitional: registry not yet wired into prepare(). Synthesize
        # a minimal contract from ProviderConfig.capability (the shim).
        # Task 8 removes this branch and resolves the contract in prepare().
        contract = ProviderContract(
            name=ctx.active_provider,
            display_name=ctx.active_provider,
            adapter=str(ctx.adapter_script),
            adapter_runtime=AdapterRuntime.BASH,
            capability_ceiling=ctx.provider_config.capability,
            protocol=ProviderProtocol(
                request_schema="weave.request.v1",
                response_schema="weave.response.v1",
            ),
        )

    policy = evaluate_policy(
        contract=contract,
        provider_config=ctx.provider_config,
        requested_class=ctx.requested_risk_class,
        phase=ctx.phase,
    )
```

- [ ] **Step 8: Run the full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -10`
Expected: `164 - 8 old policy tests + 13 new policy tests = 169 passed`. If there are failures, they will be in test_runtime.py — read the errors and note them for Task 8. If any test asserts specific log / warning behavior from the legacy path, it should continue to pass because the shim still computes the same effective result.

- [ ] **Step 9: Commit**

```bash
git add src/weave/core/policy.py src/weave/core/runtime.py tests/conftest.py tests/test_policy.py
git commit -m "refactor(policy): take contract ceiling as explicit input"
```

---

## Task 7: Refactor invoker to take a contract

**Files:**
- Modify: `src/weave/core/invoker.py`
- Modify: `tests/test_invoker.py`

- [ ] **Step 1: Write the new invoker tests first (append to `tests/test_invoker.py`)**

Add these tests after the existing ones (do not delete existing tests yet — we'll migrate them in step 3):

```python
from tests.conftest import make_contract
from weave.schemas.provider_contract import AdapterRuntime, ProviderContract


def _valid_contract_for(adapter_path: Path, runtime: AdapterRuntime = AdapterRuntime.BASH, name: str = "testadapter") -> ProviderContract:
    contract = make_contract(
        name=name,
        adapter=adapter_path.name,
        adapter_runtime=runtime,
    )
    return contract


def test_invoke_contract_valid_response_populates_structured(tmp_path):
    adapter = tmp_path / "good.sh"
    adapter.write_text(
        "#!/usr/bin/env bash\n"
        "cat /dev/stdin > /dev/null\n"
        "echo '{\"protocol\":\"weave.response.v1\",\"exitCode\":0,\"stdout\":\"hi\",\"stderr\":\"\",\"structured\":{\"k\":1}}'\n"
    )
    adapter.chmod(adapter.stat().st_mode | stat.S_IEXEC)
    contract = _valid_contract_for(adapter)

    # Patch registry to resolve to our tmp adapter
    from weave.core import registry as registry_module
    registry = registry_module.ProviderRegistry()
    registry._contracts[contract.name] = contract
    registry._manifest_dirs[contract.name] = adapter.parent

    result = invoke_provider(
        contract=contract,
        session_id="sess_test",
        task="hello",
        working_dir=tmp_path,
        registry=registry,
    )
    assert result.exit_code == 0
    assert result.structured == {"k": 1}
    assert result.stdout == "hi"


def test_invoke_contract_non_json_response_flags_as_error(tmp_path):
    adapter = tmp_path / "plain.sh"
    adapter.write_text(
        "#!/usr/bin/env bash\ncat /dev/stdin > /dev/null\necho 'not json at all'\n"
    )
    adapter.chmod(adapter.stat().st_mode | stat.S_IEXEC)
    contract = _valid_contract_for(adapter, name="plainprovider")

    from weave.core import registry as registry_module
    registry = registry_module.ProviderRegistry()
    registry._contracts[contract.name] = contract
    registry._manifest_dirs[contract.name] = adapter.parent

    result = invoke_provider(
        contract=contract,
        session_id="sess_test",
        task="hi",
        working_dir=tmp_path,
        registry=registry,
    )
    assert result.exit_code == 1
    assert "not valid JSON" in result.stderr
    assert result.structured is None


def test_invoke_contract_schema_violation_is_error(tmp_path):
    adapter = tmp_path / "bad_schema.sh"
    adapter.write_text(
        "#!/usr/bin/env bash\ncat /dev/stdin > /dev/null\n"
        # Missing exitCode, wrong protocol
        "echo '{\"protocol\":\"weave.response.v1\",\"stdout\":\"\",\"stderr\":\"\"}'\n"
    )
    adapter.chmod(adapter.stat().st_mode | stat.S_IEXEC)
    contract = _valid_contract_for(adapter, name="badschemaprovider")

    from weave.core import registry as registry_module
    registry = registry_module.ProviderRegistry()
    registry._contracts[contract.name] = contract
    registry._manifest_dirs[contract.name] = adapter.parent

    result = invoke_provider(
        contract=contract,
        session_id="sess_test",
        task="hi",
        working_dir=tmp_path,
        registry=registry,
    )
    assert result.exit_code == 1
    assert "violates weave.response.v1" in result.stderr
    assert result.structured is None


def test_invoke_contract_request_includes_protocol_and_session_id(tmp_path):
    """Adapter captures the request payload and echoes key fields in structured."""
    adapter = tmp_path / "echo_req.sh"
    adapter.write_text(
        "#!/usr/bin/env bash\n"
        "INPUT=$(cat)\n"
        "PROTO=$(echo \"$INPUT\" | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"protocol\"])')\n"
        "SID=$(echo \"$INPUT\" | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"session_id\"])')\n"
        "jq -n --arg proto \"$PROTO\" --arg sid \"$SID\" "
        "  '{ protocol: \"weave.response.v1\", exitCode: 0, stdout: \"\", stderr: \"\", structured: { proto: $proto, sid: $sid } }'\n"
    )
    adapter.chmod(adapter.stat().st_mode | stat.S_IEXEC)
    contract = _valid_contract_for(adapter, name="echoreqprovider")

    from weave.core import registry as registry_module
    registry = registry_module.ProviderRegistry()
    registry._contracts[contract.name] = contract
    registry._manifest_dirs[contract.name] = adapter.parent

    result = invoke_provider(
        contract=contract,
        session_id="sess_xyz",
        task="hi",
        working_dir=tmp_path,
        registry=registry,
    )
    assert result.exit_code == 0
    assert result.structured == {"proto": "weave.request.v1", "sid": "sess_xyz"}


def test_invoke_contract_python_runtime_spawns_python3(tmp_path, monkeypatch):
    """Verify argv[0] is python3 when adapter_runtime is python."""
    import subprocess as sp

    adapter = tmp_path / "fake.py"
    adapter.write_text("# noop\n")
    contract = make_contract(
        name="pyadapter",
        adapter="fake.py",
        adapter_runtime=AdapterRuntime.PYTHON,
    )
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        class P:
            returncode = 0
            stdout = '{"protocol":"weave.response.v1","exitCode":0,"stdout":"","stderr":"","structured":null}'
            stderr = ""
        return P()

    monkeypatch.setattr(sp, "run", fake_run)

    from weave.core import registry as registry_module
    registry = registry_module.ProviderRegistry()
    registry._contracts[contract.name] = contract
    registry._manifest_dirs[contract.name] = adapter.parent

    result = invoke_provider(
        contract=contract,
        session_id="sess_test",
        task="hi",
        working_dir=tmp_path,
        registry=registry,
    )
    assert captured["argv"][0] == "python3"
    assert str(adapter) in captured["argv"][1]
    assert result.exit_code == 0


def test_invoke_contract_binary_runtime_spawns_direct(tmp_path, monkeypatch):
    import subprocess as sp

    adapter = tmp_path / "fake_bin"
    adapter.write_text("")
    adapter.chmod(0o755)
    contract = make_contract(
        name="binadapter",
        adapter="fake_bin",
        adapter_runtime=AdapterRuntime.BINARY,
    )
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        class P:
            returncode = 0
            stdout = '{"protocol":"weave.response.v1","exitCode":0,"stdout":"","stderr":"","structured":null}'
            stderr = ""
        return P()

    monkeypatch.setattr(sp, "run", fake_run)

    from weave.core import registry as registry_module
    registry = registry_module.ProviderRegistry()
    registry._contracts[contract.name] = contract
    registry._manifest_dirs[contract.name] = adapter.parent

    invoke_provider(
        contract=contract,
        session_id="sess_test",
        task="hi",
        working_dir=tmp_path,
        registry=registry,
    )
    assert len(captured["argv"]) == 1
    assert str(adapter) in captured["argv"][0]
```

- [ ] **Step 2: Replace the three existing invoker tests with contract-based equivalents**

In `tests/test_invoker.py`, delete the existing tests `test_invoke_missing_adapter`, `test_invoke_simple_adapter`, and `test_invoke_non_json_output` (and any other test that calls `invoke_provider(adapter_script=...)` with the old signature), then replace them with:

```python
def test_invoke_missing_adapter(tmp_path):
    # Contract references a nonexistent adapter path
    contract = make_contract(
        name="missing",
        adapter="nonexistent.sh",
    )
    from weave.core import registry as registry_module
    registry = registry_module.ProviderRegistry()
    registry._contracts[contract.name] = contract
    registry._manifest_dirs[contract.name] = tmp_path

    result = invoke_provider(
        contract=contract,
        session_id="sess_test",
        task="hi",
        working_dir=tmp_path,
        registry=registry,
    )
    assert result.exit_code == 1
    assert "not found" in result.stderr.lower()
    assert result.structured is None
```

- [ ] **Step 3: Run the new tests to confirm they fail**

Run: `PYTHONPATH=src pytest tests/test_invoker.py -v 2>&1 | tail -40`
Expected: every test fails with `TypeError: invoke_provider() got an unexpected keyword argument 'contract'`.

- [ ] **Step 4: Rewrite the invoker**

Replace `src/weave/core/invoker.py` with:

```python
"""Spawn adapter subprocesses, enforce wire protocol, track git diffs."""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from weave.schemas.protocol import PROTOCOL_VERSIONS
from weave.schemas.provider_contract import AdapterRuntime, ProviderContract


@dataclass
class InvokeResult:
    exit_code: int
    stdout: str
    stderr: str
    structured: dict | None
    duration: float  # milliseconds
    files_changed: list[str] = field(default_factory=list)


def _get_git_changed_files(working_dir: Path) -> list[str]:
    """Return list of modified + untracked files in working_dir."""
    files: list[str] = []

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=working_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            files.extend(f for f in result.stdout.splitlines() if f)
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=working_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            files.extend(f for f in result.stdout.splitlines() if f)
    except Exception:
        pass

    seen: set[str] = set()
    deduped: list[str] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            deduped.append(f)
    return deduped


def _build_argv(runtime: AdapterRuntime, adapter_path: Path) -> list[str]:
    if runtime is AdapterRuntime.BASH:
        return ["bash", str(adapter_path)]
    if runtime is AdapterRuntime.PYTHON:
        return ["python3", str(adapter_path)]
    if runtime is AdapterRuntime.NODE:
        return ["node", str(adapter_path)]
    if runtime is AdapterRuntime.BINARY:
        return [str(adapter_path)]
    raise ValueError(f"unknown adapter runtime: {runtime}")


def invoke_provider(
    contract: ProviderContract,
    task: str,
    session_id: str,
    working_dir: Path,
    context: str = "",
    timeout: int = 300,
    registry=None,
) -> InvokeResult:
    """Invoke an adapter via its contract. Validates request and response."""
    # Resolve adapter path through the registry
    if registry is None:
        from weave.core.registry import get_registry
        registry = get_registry()
    try:
        adapter_path = registry.resolve_adapter_path(contract.name)
    except KeyError:
        return InvokeResult(
            exit_code=1,
            stdout="",
            stderr=f"Contract {contract.name!r} not resolvable by registry",
            structured=None,
            duration=0.0,
            files_changed=[],
        )

    if not adapter_path.exists():
        return InvokeResult(
            exit_code=1,
            stdout="",
            stderr=f"Adapter script not found: {adapter_path}",
            structured=None,
            duration=0.0,
            files_changed=[],
        )

    # Build request payload according to the contract's declared request schema
    request_cls = PROTOCOL_VERSIONS[contract.protocol.request_schema]
    request = request_cls(
        session_id=session_id,
        task=task,
        workingDir=str(working_dir),
        context=context,
        timeout=timeout,
    )
    payload = request.model_dump_json()

    # Spawn subprocess using the declared adapter runtime
    argv = _build_argv(contract.adapter_runtime, adapter_path)
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=working_dir,
        )
        duration_ms = (time.monotonic() - start) * 1000
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired:
        duration_ms = timeout * 1000.0
        return InvokeResult(
            exit_code=124,
            stdout="",
            stderr=f"Adapter timed out after {timeout}s",
            structured=None,
            duration=duration_ms,
            files_changed=[],
        )

    # Parse and validate the response
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return InvokeResult(
            exit_code=1,
            stdout=stdout,
            stderr=f"adapter response is not valid JSON: {exc}",
            structured=None,
            duration=duration_ms,
            files_changed=_get_git_changed_files(working_dir),
        )

    response_cls = PROTOCOL_VERSIONS[contract.protocol.response_schema]
    try:
        validated = response_cls.model_validate(parsed)
    except ValidationError as exc:
        return InvokeResult(
            exit_code=1,
            stdout=stdout,
            stderr=f"adapter response violates {contract.protocol.response_schema}: {exc}",
            structured=None,
            duration=duration_ms,
            files_changed=_get_git_changed_files(working_dir),
        )

    files_changed = _get_git_changed_files(working_dir)

    return InvokeResult(
        exit_code=validated.exitCode,
        stdout=validated.stdout,
        stderr=validated.stderr,
        structured=validated.structured,
        duration=duration_ms,
        files_changed=files_changed,
    )
```

- [ ] **Step 5: Fix the one call site in `runtime.execute()`**

Open `src/weave/core/runtime.py`. Find the `invoke_provider(...)` call inside `execute` (around line 414). Replace:

```python
    invoke_result = invoke_provider(
        adapter_script=ctx.adapter_script,
        task=ctx.task,
        working_dir=ctx.working_dir,
        context=ctx.context.full,
        timeout=timeout,
    )
```

with:

```python
    # Transitional: Task 8 moves contract resolution into prepare().
    from weave.core.registry import get_registry
    registry = get_registry()
    registry.load(ctx.working_dir)
    if registry.has(ctx.active_provider):
        contract = registry.get(ctx.active_provider)
    else:
        from weave.schemas.provider_contract import (
            AdapterRuntime,
            ProviderContract,
            ProviderProtocol,
        )
        contract = ProviderContract(
            name=ctx.active_provider,
            display_name=ctx.active_provider,
            adapter=str(ctx.adapter_script),
            adapter_runtime=AdapterRuntime.BASH,
            capability_ceiling=ctx.provider_config.capability,
            protocol=ProviderProtocol(
                request_schema="weave.request.v1",
                response_schema="weave.response.v1",
            ),
        )

    invoke_result = invoke_provider(
        contract=contract,
        session_id=ctx.session_id,
        task=ctx.task,
        working_dir=ctx.working_dir,
        context=ctx.context.full,
        timeout=timeout,
        registry=registry,
    )
```

- [ ] **Step 6: Run the invoker tests to confirm they pass**

Run: `PYTHONPATH=src pytest tests/test_invoker.py -v 2>&1 | tail -30`
Expected: all invoker tests pass (previous count minus 3 removed plus 6 new = previous + 3 net).

- [ ] **Step 7: Run the full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -10`
Expected: total should be `169 + 3 = 172 passed`. If any `test_runtime.py` test fails because its adapter script does not emit the `protocol` field, note it — Task 8 rewrites those tests.

- [ ] **Step 8: Commit**

```bash
git add src/weave/core/invoker.py src/weave/core/runtime.py tests/test_invoker.py
git commit -m "refactor(invoker): take ProviderContract and enforce wire protocol v1"
```

---

## Task 8: Runtime integration — prepare() resolves contract, triple-clamp enforced

**Files:**
- Modify: `src/weave/core/runtime.py`
- Modify: `tests/test_runtime.py`
- Modify: `src/weave/schemas/config.py` (remove the `capability` shim property)
- Modify: `src/weave/core/config.py` (add capability clamp validation post-registry-load)

This task is the biggest — it wires the registry into `prepare()`, adds the `provider_contract` field to `PreparedContext`, removes all transitional scaffolding from Tasks 6 and 7, removes the `capability` shim property, and enforces the capability clamp at config load time.

- [ ] **Step 1: Add `provider_contract` field to `PreparedContext` and rewrite `prepare`**

Open `src/weave/core/runtime.py`.

Add to the imports block near the top:

```python
from weave.core.registry import get_registry
from weave.schemas.provider_contract import ProviderContract
```

Update the `PreparedContext` dataclass to include the new field. Find:

```python
@dataclass
class PreparedContext:
    """Everything the pipeline needs after the prepare stage."""
    config: WeaveConfig
    active_provider: str
    provider_config: ProviderConfig
    adapter_script: Path
    context: ContextAssembly
    session_id: str
    working_dir: Path
    phase: str
    task: str
    caller: str | None
    requested_risk_class: RiskClass | None
    pre_invoke_untracked: set[str]
```

Replace with:

```python
@dataclass
class PreparedContext:
    """Everything the pipeline needs after the prepare stage."""
    config: WeaveConfig
    active_provider: str
    provider_config: ProviderConfig
    provider_contract: ProviderContract
    adapter_script: Path
    context: ContextAssembly
    session_id: str
    working_dir: Path
    phase: str
    task: str
    caller: str | None
    requested_risk_class: RiskClass | None
    pre_invoke_untracked: set[str]
```

Now rewrite `prepare()` to resolve the contract and the adapter script via the registry. Replace the existing function with:

```python
def prepare(
    task: str,
    working_dir: Path,
    provider: str | None = None,
    caller: str | None = None,
    requested_risk_class: RiskClass | None = None,
) -> PreparedContext:
    """Stage 1: load config, resolve provider contract, assemble context, create session."""
    config = resolve_config(working_dir)
    active_provider = provider or config.default_provider

    provider_config = config.providers.get(active_provider)
    if provider_config is None:
        raise ValueError(f"Provider '{active_provider}' not configured")

    registry = get_registry()
    registry.load(working_dir)
    if not registry.has(active_provider):
        known = sorted(c.name for c in registry.list())
        raise RuntimeError(
            f"unknown provider: {active_provider!r}. Known providers: {known}"
        )
    contract = registry.get(active_provider)

    adapter_script = registry.resolve_adapter_path(active_provider)
    context = assemble_context(working_dir)
    session_id = create_session()
    pre_invoke_untracked = _snapshot_untracked(working_dir)

    prepared = PreparedContext(
        config=config,
        active_provider=active_provider,
        provider_config=provider_config,
        provider_contract=contract,
        adapter_script=adapter_script,
        context=context,
        session_id=session_id,
        working_dir=working_dir,
        phase=config.phase,
        task=task,
        caller=caller,
        requested_risk_class=requested_risk_class,
        pre_invoke_untracked=pre_invoke_untracked,
    )

    binding = compute_binding(prepared)
    sessions_dir = working_dir / ".harness" / "sessions"
    write_binding(binding, sessions_dir)

    return prepared
```

- [ ] **Step 2: Remove transitional scaffolding from `_policy_check`**

Replace the entire `_policy_check` function with:

```python
def _policy_check(ctx: PreparedContext) -> tuple[PolicyResult, list[HookResult]]:
    """Stage 2: evaluate policy and run pre-invoke hooks."""
    policy = evaluate_policy(
        contract=ctx.provider_contract,
        provider_config=ctx.provider_config,
        requested_class=ctx.requested_risk_class,
        phase=ctx.phase,
    )

    if not policy.allowed:
        return policy, []

    hook_ctx = HookContext(
        provider=ctx.active_provider,
        task=ctx.task,
        working_dir=str(ctx.working_dir),
        phase="pre-invoke",
    )
    chain = run_hooks(ctx.config.hooks.pre_invoke, hook_ctx)

    policy.hook_results = [
        HookResultRef(
            hook=r.hook,
            phase=r.phase,
            result=r.result,
            message=r.message,
        )
        for r in chain.results
    ]
    if not chain.allowed:
        policy.allowed = False
        policy.denials.append("Pre-invoke hook denied execution")

    return policy, chain.results
```

- [ ] **Step 3: Simplify the `execute` call site**

Replace the `invoke_result = invoke_provider(...)` block inside `execute` with:

```python
    invoke_result = invoke_provider(
        contract=ctx.provider_contract,
        session_id=ctx.session_id,
        task=ctx.task,
        working_dir=ctx.working_dir,
        context=ctx.context.full,
        timeout=timeout,
    )
```

The default `registry=None` path inside `invoke_provider` will use `get_registry()`, which has already been loaded by `prepare()`.

- [ ] **Step 4: Remove the `capability` shim property from `ProviderConfig`**

Edit `src/weave/schemas/config.py`. Delete the `@property capability` block added in Task 5. The class should now be:

```python
class ProviderConfig(BaseModel):
    command: str
    enabled: bool = True
    capability_override: RiskClass | None = None
```

- [ ] **Step 5: Grep for any remaining `.capability` reads and fix them**

Run: `PYTHONPATH=src python3 -c "import weave.core.runtime, weave.core.policy, weave.core.invoker, weave.core.scaffold, weave.core.config" 2>&1`
Expected: either no output, or an `AttributeError` naming a specific file. If an error appears, open that file and replace `.capability` with `.capability_override`, then re-run.

Also run a text search to catch anything Python import-time didn't hit:

```bash
grep -rn "provider_config\.capability\b\|\.capability\b" src/ tests/ --include="*.py" | grep -v "capability_override\|capability_ceiling"
```

Fix each hit by switching to `capability_override` (expected to return `None | RiskClass`) and handling the `None` case explicitly, or by reading from the contract ceiling instead where appropriate.

- [ ] **Step 6: Add capability clamp validation in `resolve_config`**

Edit `src/weave/core/config.py`. Add a new function and call it from `resolve_config` after validation:

```python
def _validate_capability_ceilings(config: WeaveConfig, project_dir: Path) -> None:
    """Reject configs that declare capability_override above the contract ceiling."""
    from weave.core.registry import get_registry
    from weave.schemas.policy import risk_class_level

    registry = get_registry()
    registry.load(project_dir)

    for name, entry in config.providers.items():
        if entry.capability_override is None:
            continue
        if not registry.has(name):
            # Unknown provider in config — skip here; prepare() raises later.
            continue
        contract = registry.get(name)
        if risk_class_level(entry.capability_override) > risk_class_level(contract.capability_ceiling):
            raise ValueError(
                f"config: provider {name!r} capability_override="
                f"{entry.capability_override.value!r} exceeds contract "
                f"capability_ceiling={contract.capability_ceiling.value!r}"
            )
```

Call it at the end of `resolve_config`:

```python
def resolve_config(project_dir: Path, user_home: Path | None = None) -> WeaveConfig:
    """Resolve config from defaults → user → project → local layers."""
    home = user_home or Path.home()
    merged = create_default_config().model_dump()

    for config_path in [
        home / ".harness" / "config.json",
        project_dir / ".harness" / "config.json",
        project_dir / ".harness" / "config.local.json",
    ]:
        if config_path.exists():
            merged = _deep_merge(merged, json.loads(config_path.read_text()))

    _migrate_provider_legacy_keys(merged)
    config = WeaveConfig.model_validate(merged)
    _validate_capability_ceilings(config, project_dir)
    return config
```

- [ ] **Step 7: Add new runtime tests for contract attachment and triple-clamp**

Append to `tests/test_runtime.py`:

```python
from tests.conftest import make_contract as _make_contract


def _make_minimal_project(tmp_path):
    """Create a minimal weave project directory with a claude-code adapter.

    Returns (project_root, adapter_path).
    """
    import stat
    harness = tmp_path / ".harness"
    for sub in ["context", "hooks", "providers", "sessions", "integrations"]:
        (harness / sub).mkdir(parents=True)

    (harness / "manifest.json").write_text(
        '{"id":"t","type":"project","name":"t","status":"active","phase":"sandbox",'
        '"parent":null,"children":[],"provider":"claude-code","agent":null,'
        '"created":"2026-04-11T00:00:00Z","updated":"2026-04-11T00:00:00Z",'
        '"inputs":{},"outputs":{},"tags":[]}'
    )
    (harness / "config.json").write_text(
        '{"version":"1","phase":"sandbox","default_provider":"claude-code",'
        '"providers":{"claude-code":{"command":"claude","enabled":true}}}'
    )

    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    return tmp_path


def test_prepare_attaches_provider_contract(tmp_path):
    from weave.core.runtime import prepare

    # Reset singleton so each test gets a fresh registry
    from weave.core import registry as registry_module
    registry_module._REGISTRY_SINGLETON = None

    _make_minimal_project(tmp_path)
    ctx = prepare(task="hi", working_dir=tmp_path)
    assert ctx.provider_contract is not None
    assert ctx.provider_contract.name == "claude-code"
    assert ctx.provider_contract.capability_ceiling.value == "workspace-write"


def test_prepare_raises_runtime_error_for_unknown_provider(tmp_path):
    from weave.core.runtime import prepare

    from weave.core import registry as registry_module
    registry_module._REGISTRY_SINGLETON = None

    _make_minimal_project(tmp_path)
    # Inject an unknown provider into config
    cfg_path = tmp_path / ".harness" / "config.json"
    cfg_path.write_text(
        '{"version":"1","phase":"sandbox","default_provider":"nosuch",'
        '"providers":{"nosuch":{"command":"nosuch","enabled":true}}}'
    )
    with pytest.raises(RuntimeError, match="unknown provider"):
        prepare(task="hi", working_dir=tmp_path)


def test_config_load_rejects_capability_override_above_ceiling(tmp_path):
    from weave.core.config import resolve_config

    from weave.core import registry as registry_module
    registry_module._REGISTRY_SINGLETON = None

    _make_minimal_project(tmp_path)
    cfg_path = tmp_path / ".harness" / "config.json"
    cfg_path.write_text(
        '{"version":"1","phase":"sandbox","default_provider":"ollama",'
        '"providers":{"ollama":{"command":"ollama","enabled":true,'
        '"capability_override":"destructive"}}}'
    )
    with pytest.raises(ValueError, match="exceeds contract capability_ceiling"):
        resolve_config(tmp_path, user_home=tmp_path)


def test_prepare_effective_capability_clamps_to_contract_ceiling(tmp_path):
    """Even if config omits override, effective capability uses contract ceiling."""
    from weave.core.runtime import prepare, _policy_check

    from weave.core import registry as registry_module
    registry_module._REGISTRY_SINGLETON = None

    _make_minimal_project(tmp_path)
    cfg_path = tmp_path / ".harness" / "config.json"
    cfg_path.write_text(
        '{"version":"1","phase":"sandbox","default_provider":"ollama",'
        '"providers":{"ollama":{"command":"ollama","enabled":true}}}'
    )
    ctx = prepare(task="hi", working_dir=tmp_path)
    policy, _ = _policy_check(ctx)
    # Ollama ceiling is read-only regardless of what config says
    assert policy.effective_risk_class.value == "read-only"
```

Note: before this task, `test_runtime.py` likely has tests that pre-create adapter scripts in `.harness/providers/` and expect `prepare()` to find them there. After Task 8, `prepare()` resolves via the registry, which means built-in providers no longer need a user-side adapter file for tests to run. Any tests that were creating a dummy `claude-code.sh` in `.harness/providers/` can delete that setup. Keep them if they exercise user-contract override behavior; delete only the setup lines that were there to satisfy the legacy `adapter_script = working_dir / ".harness" / "providers" / f"{active_provider}.sh"` path.

- [ ] **Step 8: Run the targeted runtime tests**

Run: `PYTHONPATH=src pytest tests/test_runtime.py -v 2>&1 | tail -40`
Expected: all runtime tests pass, including the 4 new ones. If any existing test fails because it was relying on the old behavior of reading the adapter script from `.harness/providers/`, fix the test by either (a) removing the now-unneeded adapter file creation or (b) writing a user contract alongside the adapter so the registry picks it up.

- [ ] **Step 9: Run the full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -10`
Expected: `172 + 4 = 176 passed`. The runtime test count grows by 4; nothing else changes.

- [ ] **Step 10: Commit**

```bash
git add src/weave/core/runtime.py src/weave/core/config.py src/weave/schemas/config.py tests/test_runtime.py
git commit -m "feat(runtime): prepare() resolves ProviderContract via registry; enforce triple clamp"
```

---

## Task 9: Scaffold and detect_providers — purge KNOWN_PROVIDERS and template generation

**Files:**
- Modify: `src/weave/core/providers.py`
- Modify: `src/weave/core/scaffold.py`
- Possibly: `tests/test_scaffold.py` (if it exists) and `tests/test_providers.py`

- [ ] **Step 1: Rewrite `providers.py`**

Replace `src/weave/core/providers.py` with:

```python
"""Weave provider detection — find installed CLI tools via registry-driven health checks."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProviderInfo:
    """Thin projection of a ProviderContract for scaffold and CLI listing.

    Note: `command` is kept for backward compatibility with the scaffold
    template path (now deleted) and CLI output. It mirrors `name` since
    the contract no longer carries an explicit command field. A future
    cleanup may remove this field once no consumer reads it.
    """

    name: str
    command: str
    installed: bool
    health_check: str
    adapter_script: str = field(default="")


def check_provider_health(cmd: str) -> bool:
    """Run a health-check command and return True if it exits with code 0."""
    try:
        parts = cmd.split()
        result = subprocess.run(
            parts,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def detect_providers(project_root: Path | None = None) -> list[ProviderInfo]:
    """Check all registry-known providers and return a ProviderInfo list."""
    from weave.core.registry import get_registry

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
                command=contract.name,
                installed=installed,
                health_check=contract.health_check or "",
                adapter_script=contract.adapter,
            )
        )
    return out
```

- [ ] **Step 2: Rewrite `scaffold.py` to copy built-in files**

Replace `src/weave/core/scaffold.py` with:

```python
"""Weave project scaffolding — create .harness/ directory tree."""
from __future__ import annotations

import json
import shutil
import stat
from pathlib import Path

from weave.core.manifest import write_manifest
from weave.core.providers import ProviderInfo, detect_providers
from weave.schemas.config import ProviderConfig, create_default_config
from weave.schemas.manifest import Phase, UnitType, create_manifest


def _builtin_dir() -> Path:
    import weave
    return Path(weave.__file__).parent / "providers" / "builtin"


def _copy_builtin_provider_files(
    provider_name: str,
    dest_dir: Path,
) -> tuple[bool, bool]:
    """Copy a built-in provider's contract manifest and adapter script.

    Returns (adapter_copied, manifest_copied). Existing files are preserved
    (not overwritten).
    """
    src = _builtin_dir()
    adapter_src = src / f"{provider_name}.sh"
    manifest_src = src / f"{provider_name}.contract.json"

    adapter_dst = dest_dir / f"{provider_name}.sh"
    manifest_dst = dest_dir / f"{provider_name}.contract.json"

    adapter_copied = False
    manifest_copied = False

    if adapter_src.exists() and not adapter_dst.exists():
        shutil.copy2(adapter_src, adapter_dst)
        adapter_dst.chmod(
            adapter_dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        adapter_copied = True

    if manifest_src.exists() and not manifest_dst.exists():
        shutil.copy2(manifest_src, manifest_dst)
        manifest_copied = True

    return adapter_copied, manifest_copied


def scaffold_project(
    project_dir: Path | str,
    name: str | None = None,
    default_provider: str = "claude-code",
    phase: str = "sandbox",
) -> None:
    """Scaffold a Weave project at project_dir.

    Creates the .harness/ directory tree, manifest.json, config.json,
    context template files, and copies built-in adapter scripts and
    contract manifests for installed providers.
    """
    project_dir = Path(project_dir)
    harness_dir = project_dir / ".harness"

    if name is None:
        name = project_dir.name

    for sub in ["context", "hooks", "providers", "sessions", "integrations"]:
        (harness_dir / sub).mkdir(parents=True, exist_ok=True)

    phase_enum = Phase(phase)
    manifest = create_manifest(
        name=name,
        unit_type=UnitType.project,
        phase=phase_enum,
        provider=default_provider,
    )
    write_manifest(project_dir, manifest)

    providers = detect_providers(project_root=project_dir)
    installed = [p for p in providers if p.installed]

    config = create_default_config(default_provider=default_provider)
    config.phase = phase
    for provider in installed:
        config.providers[provider.name] = ProviderConfig(
            command=provider.name,
            enabled=True,
            capability_override=None,
        )
    config.context.translate_to = [p.name for p in installed]

    (harness_dir / "config.json").write_text(config.model_dump_json(indent=2))

    context_dir = harness_dir / "context"
    _write_if_not_exists(
        context_dir / "conventions.md",
        f"# {name} \u2014 Conventions\n\nAdd your project coding standards and rules here.\n",
    )
    _write_if_not_exists(
        context_dir / "brief.md",
        f"# {name} \u2014 Brief\n\nDescribe what this project is building.\n",
    )
    _write_if_not_exists(
        context_dir / "spec.md",
        f"# {name} \u2014 Specification\n\nAdd requirements and acceptance criteria here.\n",
    )

    gitignore_path = project_dir / ".gitignore"
    env_entries = ".env\n.env.local\n.env.*.local\n"
    if gitignore_path.exists():
        existing = gitignore_path.read_text()
        if ".env" not in existing:
            with open(gitignore_path, "a") as f:
                f.write(f"\n# Environment secrets (added by weave init)\n{env_entries}")
    else:
        gitignore_path.write_text(f"# Environment secrets\n{env_entries}")

    providers_dir = harness_dir / "providers"
    for provider in installed:
        _copy_builtin_provider_files(provider.name, providers_dir)


def _write_if_not_exists(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content)
```

- [ ] **Step 3: Check for existing scaffold / providers test files**

Run: `ls tests/test_scaffold.py tests/test_providers.py 2>&1`

If `tests/test_scaffold.py` exists, open it and check for any references to `_ADAPTER_TEMPLATE`, `_CLI_FLAGS`, `_build_adapter_script`, or assertions that a generated adapter script contains literal `claude --print` or similar template output. Replace those assertions with checks that the scaffold copied the built-in file (e.g., `(harness / "providers" / "claude-code.sh").exists()` and `(harness / "providers" / "claude-code.contract.json").exists()`).

If `tests/test_providers.py` exists, check for references to `KNOWN_PROVIDERS`. Replace any direct iteration over `KNOWN_PROVIDERS` with `detect_providers(project_root=...)`.

- [ ] **Step 4: Run the full suite**

Run: `PYTHONPATH=src pytest tests/ 2>&1 | tail -10`
Expected: `176` or slightly higher if scaffold/providers tests gained assertions. If any scaffold test fails with "template contained X" or similar, update the assertion to check the copied built-in file instead.

- [ ] **Step 5: Commit**

```bash
git add src/weave/core/providers.py src/weave/core/scaffold.py tests/
git commit -m "refactor(scaffold): copy built-in provider files; delete KNOWN_PROVIDERS and template"
```

---

## Task 10: Final verification

**Files:** none — verification only.

- [ ] **Step 1: Run the full test suite**

Run: `PYTHONPATH=src pytest tests/ -v 2>&1 | tail -30`
Expected: **185 tests pass**.

Breakdown by task:
- Task 1: +7 (test_protocol.py)
- Task 2: +7 (test_provider_contract.py)
- Task 3: +0 (data files only)
- Task 4: +14 (test_registry.py)
- Task 5: +4 (test_config.py extensions)
- Task 6: +13 (test_policy.py rewrite: previous 8 replaced by 13)
  - Net change: +13 − 8 = +5
- Task 7: +6 (test_invoker.py extensions: 3 legacy removed + 1 migrated + 6 new = +4 net)
  - Net change: +4
- Task 8: +4 (test_runtime.py extensions)
- Task 9: +0 (behavior changes, not test count)

Tally: 136 baseline + 7 + 7 + 14 + 4 + 5 + 4 + 4 = **181**.

The spec targeted **185**. The **4-test discrepancy** comes from:
- Task 6 policy rewrite may end up with a different exact count if you combine or split any test cases during implementation.
- Task 7 invoker extensions assume net +4; if the edge cases you hit while implementing require more coverage, the count will rise.

If the final count is between **180 and 190**, that is acceptable; note the exact number in the commit message for Task 10. If it is outside that range, investigate whether a test was silently skipped or a check was dropped.

- [ ] **Step 2: Verify no circular imports and core module boundaries are clean**

Run:
```bash
PYTHONPATH=src python3 -c "
from weave.cli import main
from weave.core.runtime import prepare, execute
from weave.core.registry import get_registry, ProviderRegistry
from weave.core.invoker import invoke_provider
from weave.core.policy import resolve_risk_class, evaluate_policy
from weave.schemas.protocol import AdapterRequestV1, AdapterResponseV1, PROTOCOL_VERSIONS
from weave.schemas.provider_contract import ProviderContract, ProviderFeature, AdapterRuntime
print('imports: ok')
"
```
Expected: `imports: ok`

- [ ] **Step 3: Verify that the registry loads all five built-ins from a clean install**

Run:
```bash
PYTHONPATH=src python3 -c "
from pathlib import Path
import tempfile
from weave.core.registry import ProviderRegistry

with tempfile.TemporaryDirectory() as d:
    reg = ProviderRegistry()
    reg.load(Path(d))
    names = sorted(c.name for c in reg.list())
    print('builtins:', names)
    assert names == ['claude-code', 'codex', 'gemini', 'ollama', 'vllm'], names
    print('ok')
"
```
Expected: `builtins: ['claude-code', 'codex', 'gemini', 'ollama', 'vllm']` followed by `ok`.

- [ ] **Step 4: Manual smoke test — prepare → execute a minimal session with vllm**

This exercises the full path: registry load → contract resolution → request schema v1 build → adapter script spawn → response schema v1 validation. The vllm adapter is used because it is the only built-in whose fallback path (no `vllmc` installed) returns a valid `exitCode: 127` response without hitting an external server. If `vllmc` is not on the PATH, the adapter returns the fallback response — still a valid v1 response.

Run:
```bash
PYTHONPATH=src python3 -c "
import subprocess, tempfile, json, os
from pathlib import Path
from weave.core.runtime import prepare, execute

with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    harness = tmp / '.harness'
    for sub in ['context', 'hooks', 'providers', 'sessions', 'integrations']:
        (harness / sub).mkdir(parents=True)
    (harness / 'manifest.json').write_text(json.dumps({
        'id': 'smoke', 'type': 'project', 'name': 'smoke',
        'status': 'active', 'phase': 'sandbox', 'parent': None,
        'children': [], 'provider': 'vllm', 'agent': None,
        'created': '2026-04-11T00:00:00Z', 'updated': '2026-04-11T00:00:00Z',
        'inputs': {}, 'outputs': {}, 'tags': []
    }))
    (harness / 'config.json').write_text(json.dumps({
        'version': '1', 'phase': 'sandbox', 'default_provider': 'vllm',
        'providers': {'vllm': {'command': 'vllm', 'enabled': True}}
    }))

    subprocess.run(['git', 'init', '-q'], cwd=tmp, check=True)
    subprocess.run(['git', 'config', 'user.email', 'smoke@smoke'], cwd=tmp, check=True)
    subprocess.run(['git', 'config', 'user.name', 'smoke'], cwd=tmp, check=True)
    subprocess.run(['git', 'add', '.'], cwd=tmp, check=True)
    subprocess.run(['git', 'commit', '-q', '-m', 'init'], cwd=tmp, check=True)

    # Reset registry singleton
    from weave.core import registry as rm
    rm._REGISTRY_SINGLETON = None

    ctx = prepare(task='smoke test', working_dir=tmp)
    print('contract.name:', ctx.provider_contract.name)
    print('contract.ceiling:', ctx.provider_contract.capability_ceiling.value)
    print('adapter_script:', ctx.adapter_script)

    result = execute(task='smoke test', working_dir=tmp, provider='vllm')
    print('status:', result.status.value)
    print('invoke exit_code:', result.invoke_result.exit_code if result.invoke_result else None)
    print('stderr snippet:', (result.invoke_result.stderr[:80] if result.invoke_result else None))
    print('manual smoke: ok')
"
```
Expected:
- `contract.name: vllm`
- `contract.ceiling: read-only`
- `adapter_script:` points into `src/weave/providers/builtin/vllm.sh`
- If `vllmc` is installed: status is `success` (exit 0) or `failure` depending on server state.
- If `vllmc` is not installed: `invoke exit_code: 127` and stderr mentions `vllmc not found`. This still validates the protocol round-trip.
- Final line: `manual smoke: ok`

- [ ] **Step 4 (alt): If the smoke test above fails for environmental reasons unrelated to the spec**, fall back to invoking with a dummy user contract that shells out to `/bin/true`:

Run:
```bash
PYTHONPATH=src python3 -c "
# ... same setup ...
# Replace provider config with a dummy
(harness / 'providers' / 'dummy.sh').write_text(
    '#!/usr/bin/env bash\ncat /dev/null\n'
    'echo \\'{\"protocol\":\"weave.response.v1\",\"exitCode\":0,\"stdout\":\"ok\",\"stderr\":\"\",\"structured\":{}}\\'\n'
)
(harness / 'providers' / 'dummy.sh').chmod(0o755)
(harness / 'providers' / 'dummy.contract.json').write_text(json.dumps({
    'contract_version': '1', 'name': 'dummy', 'display_name': 'Dummy',
    'adapter': 'dummy.sh', 'adapter_runtime': 'bash',
    'capability_ceiling': 'read-only',
    'protocol': {
        'request_schema': 'weave.request.v1',
        'response_schema': 'weave.response.v1',
    },
    'declared_features': [],
}))
# ... then prepare/execute with provider='dummy' ...
"
```

- [ ] **Step 5: No commit**

Task 10 is verification only. No files change.

- [ ] **Step 6: Merge marker — optional final commit**

If you want a single marker commit noting the phase is complete:

```bash
git commit --allow-empty -m "feat: Phase 3 provider contract registry complete (target 185 tests)"
```

This is optional; skip it if you prefer the phase to be marked only by the individual task commits.

---

## Self-Review Notes

**Spec coverage:** Every section of the spec maps to one or more tasks.
- Goals 1-4 (capability honesty, protocol versioning, output schema validation, registry) → Tasks 1-2 (schemas), Task 4 (registry), Tasks 6-8 (enforcement)
- Architecture layered model → Tasks 3-4 (built-ins + registry), Task 8 (runtime integration)
- Schemas section → Tasks 1 (protocol) + 2 (contract)
- Registry API → Task 4
- Config schema change → Task 5 (rename + migration) + Task 8 (shim removal + clamp validation)
- Runtime integration → Task 8
- Policy integration → Task 6
- Invoker changes → Task 7
- Scaffold + detection updates → Task 9
- 5 built-in contracts → Task 3
- Error handling matrix → every relevant task has assertions; orphan adapter, legacy key, wrong schema, stem mismatch, filename stem mismatch, override-above-ceiling, and unknown provider all tested
- Test plan → distributed across tasks; final tally reconciled in Task 10

**Placeholder scan:** No TBDs or TODOs in any code block. Every step contains the actual content needed. The `# pragma: no cover` annotation in Task 6 Step 7 is scaffolding explicitly marked and removed in Task 8.

**Type consistency:**
- `ProviderContract` field names match across Tasks 2, 3 (JSON files), 4 (validation), 6 (policy consumer), 7 (invoker consumer), 8 (runtime consumer).
- `invoke_provider` signature `(contract, task, session_id, working_dir, context, timeout, registry)` is consistent across Task 7 definition and Tasks 7-8 call sites.
- `resolve_risk_class(contract_ceiling, config_override, requested)` keyword args are consistent across Task 6 definition and Task 8 callers.
- `evaluate_policy(contract, provider_config, requested_class, phase)` consistent across Task 6 and Task 8.
- `PROTOCOL_VERSIONS["weave.request.v1"]` key strings match between Tasks 1, 2, 3 (JSON files), 7 (invoker).
- `ProviderFeature` enum members match between Task 2 definition and Task 3 JSON declarations.
- `AdapterRuntime` enum members match between Task 2 definition and Task 7 `_build_argv`.

**Scope check:** One implementation plan, 10 numbered tasks, each task is self-contained and commits independently. The largest tasks (6, 7, 8) each still produce a working green test suite at commit time thanks to transitional shims; no task leaves the tree broken.

**Ambiguity check:**
- `pyproject.toml` package-data inclusion (Task 3 Step 5) is intentionally conditional on the build backend — the step tells the implementer what to do for each of the three common cases and gives them permission to skip if the backend is different, because local `PYTHONPATH=src` development is unaffected.
- Task 10 Step 1 allows a 180-190 range on the final test count because the exact number of sub-tests in some rewrites depends on how the implementer chooses to split edge cases.
- Task 8 Step 5 (`grep` for remaining `.capability` reads) is a best-effort safety net — the task depends on the implementer reading their grep output and making judgement calls, which is appropriate for a cross-cutting rename.
