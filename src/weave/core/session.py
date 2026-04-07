"""JSONL session logging — Weave-compatible format."""
from __future__ import annotations

import uuid
from pathlib import Path

from weave.schemas.activity import ActivityRecord


def create_session() -> str:
    """Return a new UUID4 session ID string."""
    return str(uuid.uuid4())


def append_activity(
    sessions_dir: Path,
    session_id: str,
    record: ActivityRecord,
) -> None:
    """Append an ActivityRecord as a JSON line to {session_id}.jsonl."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    log_file = sessions_dir / f"{session_id}.jsonl"
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(record.model_dump_json() + "\n")


def read_session_activities(
    sessions_dir: Path,
    session_id: str,
) -> list[ActivityRecord]:
    """Read and parse all ActivityRecord lines from {session_id}.jsonl."""
    log_file = sessions_dir / f"{session_id}.jsonl"
    if not log_file.exists():
        return []
    records: list[ActivityRecord] = []
    for line in log_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(ActivityRecord.model_validate_json(line))
    return records
