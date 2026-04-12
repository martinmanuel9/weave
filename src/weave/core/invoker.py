"""Spawn adapter scripts, parse JSON output, track git diffs."""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from weave.schemas.protocol import PROTOCOL_VERSIONS


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

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            deduped.append(f)
    return deduped


def _build_argv(runtime: str, adapter_path: Path) -> list[str]:
    """Return the command argv for the given adapter runtime."""
    runtime_lower = runtime.lower()
    if runtime_lower == "bash":
        return ["bash", str(adapter_path)]
    elif runtime_lower == "python":
        return ["python3", str(adapter_path)]
    elif runtime_lower == "node":
        return ["node", str(adapter_path)]
    elif runtime_lower == "binary":
        return [str(adapter_path)]
    else:
        raise ValueError(f"unknown adapter runtime: {runtime!r}")


def invoke_provider(
    contract,  # ProviderContract — not type-annotated to avoid circular import
    task: str,
    session_id: str,
    working_dir: Path,
    context: str = "",
    timeout: int = 300,
    registry=None,  # ProviderRegistry | None
) -> InvokeResult:
    """Invoke a provider adapter described by *contract* and return structured results.

    The request is built from the contract's declared protocol request schema,
    and the response is validated against the contract's declared response schema.
    """
    # Resolve adapter path: prefer registry lookup, fall back to contract.adapter
    if registry is not None and registry.has(contract.name):
        adapter_path = registry.resolve_adapter_path(contract.name)
    else:
        # No registry, or contract not in registry (synthesized contract).
        # Use contract.adapter directly — it may be an absolute path from
        # the transitional synthesis in runtime.execute().
        adapter_path = Path(contract.adapter)

    if not adapter_path.exists():
        return InvokeResult(
            exit_code=1,
            stdout="",
            stderr=f"Adapter script not found: {adapter_path}",
            structured=None,
            duration=0.0,
            files_changed=[],
        )

    # Build request payload from the contract's declared request schema
    request_cls = PROTOCOL_VERSIONS[contract.protocol.request_schema]
    request_obj = request_cls(
        session_id=session_id,
        task=task,
        workingDir=str(working_dir),
        context=context,
        timeout=timeout,
    )
    payload = request_obj.model_dump_json()

    # Determine argv from adapter runtime
    argv = _build_argv(contract.adapter_runtime.value, adapter_path)

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
        exit_code = proc.returncode
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

    # Parse stdout as JSON, then validate against the response schema
    response_cls = PROTOCOL_VERSIONS[contract.protocol.response_schema]
    structured: dict | None = None

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        # Non-JSON output is an error under the contract protocol
        return InvokeResult(
            exit_code=1,
            stdout=stdout,
            stderr=f"adapter stdout is not valid JSON: {exc}",
            structured=None,
            duration=duration_ms,
            files_changed=_get_git_changed_files(working_dir),
        )

    try:
        validated = response_cls.model_validate(parsed)
        structured = validated.model_dump()
    except ValidationError as exc:
        return InvokeResult(
            exit_code=1,
            stdout=stdout,
            stderr=(
                f"adapter response does not conform to "
                f"{contract.protocol.response_schema}: {exc}"
            ),
            structured=None,
            duration=duration_ms,
            files_changed=_get_git_changed_files(working_dir),
        )

    files_changed = _get_git_changed_files(working_dir)

    return InvokeResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        structured=structured,
        duration=duration_ms,
        files_changed=files_changed,
    )
