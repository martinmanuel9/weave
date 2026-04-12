"""Volatile context assembly — per-invocation context from git state and session activity."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from weave.schemas.activity import ActivityRecord, ActivityType

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 5


def _git_diff_section(working_dir: Path, max_files: int) -> str:
    """Git diff + untracked files as a markdown section.

    Returns empty string if no changes, not a git repo, or git fails.
    """
    entries: list[tuple[str, str]] = []

    try:
        result = subprocess.run(
            ["git", "diff", "--name-status", "HEAD"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode == 0:
            status_map = {"M": "modified", "A": "new", "D": "deleted", "R": "renamed"}
            for line in result.stdout.splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    status_code = parts[0][0] if parts[0] else "?"
                    filename = parts[1]
                    label = status_map.get(status_code, "changed")
                    entries.append((filename, label))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""

    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip()
                if line:
                    entries.append((line, "new"))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    if not entries:
        return ""

    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for filename, label in entries:
        if filename not in seen:
            seen.add(filename)
            unique.append((filename, label))

    lines = ["## Recent Git State", "", "### Changed files (since last commit)"]
    for filename, label in unique[:max_files]:
        lines.append(f"- {filename} ({label})")

    overflow = len(unique) - max_files
    if overflow > 0:
        lines.append(f"- (and {overflow} more...)")

    return "\n".join(lines)


def _git_log_section(working_dir: Path, max_entries: int) -> str:
    """Recent git log as a markdown section.

    Returns empty string if no commits, not a git repo, or git fails.
    """
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{max_entries}"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""

    commits = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not commits:
        return ""

    lines = ["### Recent commits"]
    for commit in commits:
        lines.append(f"- {commit}")

    return "\n".join(lines)
