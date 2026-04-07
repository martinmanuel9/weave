"""Spawn adapter scripts, parse JSON output, track git diffs."""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


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


def invoke_provider(
    adapter_script: str | Path,
    task: str,
    working_dir: Path,
    context: str = "",
    timeout: int = 300,
) -> InvokeResult:
    """Invoke an adapter script with a JSON task payload and return structured results."""
    adapter_path = Path(adapter_script)

    if not adapter_path.exists():
        return InvokeResult(
            exit_code=1,
            stdout="",
            stderr=f"Adapter script not found: {adapter_path}",
            structured=None,
            duration=0.0,
            files_changed=[],
        )

    payload = json.dumps(
        {
            "task": task,
            "workingDir": str(working_dir),
            "context": context,
            "timeout": timeout,
        }
    )

    start = time.monotonic()
    try:
        proc = subprocess.run(
            ["bash", str(adapter_path)],
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

    # Try to parse JSON output
    structured: dict | None = None
    try:
        structured = json.loads(stdout)
    except json.JSONDecodeError:
        pass  # structured stays None; raw stdout is preserved

    files_changed = _get_git_changed_files(working_dir)

    return InvokeResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        structured=structured,
        duration=duration_ms,
        files_changed=files_changed,
    )
