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


def _activity_section(
    sessions_dir: Path,
    session_id: str,
    max_records: int,
) -> str:
    """Recent session activity as a markdown section.

    Returns empty string if no session file or no records.
    Skips compaction_summary records. Most recent first.
    """
    log_file = sessions_dir / f"{session_id}.jsonl"
    if not log_file.exists():
        return ""

    records: list[ActivityRecord] = []
    try:
        for line in log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = ActivityRecord.model_validate_json(line)
                if record.task == "compaction_summary" and record.type == ActivityType.system:
                    continue
                records.append(record)
            except Exception:
                continue
    except (OSError, UnicodeDecodeError):
        return ""

    if not records:
        return ""

    tail = records[-max_records:]
    tail.reverse()

    lines = ["## Session Activity", "", "### Previous invocations"]
    for r in tail:
        ts = r.timestamp.strftime("%H:%M:%S") if r.timestamp else "??:??:??"
        task_snippet = (r.task or "")[:60]
        provider = r.provider or "unknown"
        status = r.status.value if r.status else "unknown"
        files_count = len(r.files_changed)
        duration_s = f"{(r.duration or 0) / 1000:.1f}s"
        lines.append(
            f'- [{ts}] provider={provider} task="{task_snippet}" '
            f"status={status} files={files_count} duration={duration_s}"
        )

    return "\n".join(lines)


def build_volatile_context(
    working_dir: Path,
    config,  # VolatileContextConfig
    session_id: str | None = None,
) -> str:
    """Assemble volatile context from enabled sources.

    Returns empty string if disabled or all sources are empty.
    """
    if not config.enabled:
        return ""

    sections: list[str] = []

    if config.git_diff_enabled:
        git_diff = _git_diff_section(working_dir, config.git_diff_max_files)
        if git_diff:
            sections.append(git_diff)

    if config.git_log_enabled:
        git_log = _git_log_section(working_dir, config.git_log_max_entries)
        if git_log:
            sections.append(git_log)

    if config.activity_enabled and session_id:
        sessions_dir = working_dir / ".harness" / "sessions"
        activity = _activity_section(
            sessions_dir, session_id, config.activity_max_records,
        )
        if activity:
            sections.append(activity)

    if not sections:
        return ""

    result = "\n\n".join(sections)

    if len(result) > config.max_total_chars:
        result = result[:config.max_total_chars] + "\n(volatile context truncated)"

    return result
