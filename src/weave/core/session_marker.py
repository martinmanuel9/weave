"""Session marker — capture and read start-time state for wrapped sessions."""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from weave.schemas.session_marker import SessionMarker


# Empty tree object SHA — a baseline that git diff can work against when
# HEAD does not exist (no commits yet in the repo).
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _detect_git_state(working_dir: Path) -> tuple[bool, str | None, list[str]]:
    """Detect git availability, capture HEAD SHA, and snapshot untracked files.

    Returns (git_available, start_head_sha, pre_invoke_untracked).
    Falls back to (False, None, []) if any git command fails.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or result.stdout.strip() != "true":
            return False, None, []
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False, None, []

    # Capture HEAD SHA, falling back to the empty tree SHA if HEAD does not exist
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            start_head_sha = result.stdout.strip()
        else:
            start_head_sha = _EMPTY_TREE_SHA
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        start_head_sha = _EMPTY_TREE_SHA

    # Capture untracked file snapshot
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            pre_invoke_untracked = sorted(
                line for line in result.stdout.splitlines() if line
            )
        else:
            pre_invoke_untracked = []
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pre_invoke_untracked = []

    return True, start_head_sha, pre_invoke_untracked


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
    git_available, start_head_sha, pre_invoke_untracked = _detect_git_state(working_dir)

    marker = SessionMarker(
        session_id=session_id,
        start_time=datetime.now(timezone.utc),
        git_available=git_available,
        start_head_sha=start_head_sha,
        pre_invoke_untracked=pre_invoke_untracked,
        task=task,
        working_dir=str(working_dir.resolve()),
    )

    sessions_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = sessions_dir / f"{session_id}.start_marker.json"
    sidecar_path.write_text(marker.model_dump_json(indent=2))
    return marker


def read_marker(session_id: str, sessions_dir: Path) -> SessionMarker | None:
    """Load a SessionMarker from its .start_marker.json sidecar.

    Returns None if the file does not exist. Raises on malformed JSON
    or Pydantic validation errors — a broken marker is an operator-facing
    error, not silently ignorable.
    """
    sidecar_path = sessions_dir / f"{session_id}.start_marker.json"
    if not sidecar_path.exists():
        return None
    return SessionMarker.model_validate_json(sidecar_path.read_text())


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

    Best-effort: individual subprocess failures contribute nothing to the
    result; the function continues with what it has.
    """
    if not marker.git_available or marker.start_head_sha is None:
        return []

    files: set[str] = set()

    # 1. Committed work between start and HEAD
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", marker.start_head_sha, "HEAD"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            files.update(line for line in result.stdout.splitlines() if line)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # 2. Uncommitted modifications
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            files.update(line for line in result.stdout.splitlines() if line)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # 3. New untracked (current untracked - pre_invoke_untracked)
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            current_untracked = {
                line for line in result.stdout.splitlines() if line
            }
            pre_set = set(marker.pre_invoke_untracked)
            files.update(current_untracked - pre_set)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return sorted(files)
