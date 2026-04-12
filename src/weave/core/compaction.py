"""Transcript compaction — within-session rolling compaction and cross-session lifecycle."""
from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from weave.schemas.activity import ActivityRecord, ActivityStatus, ActivityType

logger = logging.getLogger(__name__)


def _build_compaction_summary(records: list[ActivityRecord]) -> ActivityRecord:
    """Build a single summary ActivityRecord from a list of records.

    Detects prior compaction_summary records (task == "compaction_summary")
    and merges their metadata into the running totals so that repeated
    compactions accumulate stats correctly.
    """
    total_count = 0
    total_duration = 0.0
    status_counts: Counter[str] = Counter()
    providers: set[str] = set()
    all_files: list[str] = []
    earliest: datetime | None = None
    latest: datetime | None = None

    summary_file_count = 0

    for record in records:
        if record.task == "compaction_summary" and record.type == ActivityType.system:
            meta = record.metadata
            total_count += meta.get("compacted_count", 0)
            total_duration += meta.get("total_duration_ms", 0.0)
            for status, count in meta.get("status_counts", {}).items():
                status_counts[status] += count
            providers.update(meta.get("providers_used", []))
            all_files.extend(meta.get("unique_files_changed", []))
            summary_file_count += meta.get("total_files_changed", 0)
            e = meta.get("earliest_timestamp")
            if e:
                e_dt = datetime.fromisoformat(e)
                if earliest is None or e_dt < earliest:
                    earliest = e_dt
            l = meta.get("latest_timestamp")
            if l:
                l_dt = datetime.fromisoformat(l)
                if latest is None or l_dt > latest:
                    latest = l_dt
        else:
            total_count += 1
            total_duration += record.duration or 0.0
            status_counts[record.status.value] += 1
            if record.provider:
                providers.add(record.provider)
            all_files.extend(record.files_changed)
            ts = record.timestamp
            if earliest is None or ts < earliest:
                earliest = ts
            if latest is None or ts > latest:
                latest = ts

    unique_files = sorted(set(all_files))[:50]

    real_record_file_count = sum(
        len(r.files_changed) for r in records
        if not (r.task == "compaction_summary" and r.type == ActivityType.system)
    )
    total_files = real_record_file_count + summary_file_count

    return ActivityRecord(
        session_id=records[0].session_id if records else "unknown",
        type=ActivityType.system,
        status=ActivityStatus.success,
        task="compaction_summary",
        metadata={
            "compacted_count": total_count,
            "earliest_timestamp": earliest.isoformat() if earliest else None,
            "latest_timestamp": latest.isoformat() if latest else None,
            "total_duration_ms": total_duration,
            "providers_used": sorted(providers),
            "status_counts": dict(status_counts),
            "total_files_changed": total_files,
            "unique_files_changed": unique_files,
        },
    )


def _maybe_compact_session(
    sessions_dir: Path,
    session_id: str,
    keep_recent: int,
) -> None:
    """Compact a session's JSONL if it exceeds keep_recent lines.

    Replaces all lines before the most recent `keep_recent` with a single
    compaction_summary record. Uses atomic .tmp → rename for crash safety.
    """
    if keep_recent <= 0:
        return

    log_file = sessions_dir / f"{session_id}.jsonl"
    if not log_file.exists():
        return

    lines = [line for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) <= keep_recent:
        return

    old_lines = lines[:-keep_recent]
    recent_lines = lines[-keep_recent:]

    old_records: list[ActivityRecord] = []
    corrupt_count = 0
    for line in old_lines:
        try:
            old_records.append(ActivityRecord.model_validate_json(line))
        except Exception:
            corrupt_count += 1
            logger.warning("skipping corrupt line during compaction of session %s", session_id)

    summary = _build_compaction_summary(old_records)
    summary.metadata["compacted_count"] += corrupt_count

    tmp = log_file.with_suffix(".jsonl.tmp")
    content = summary.model_dump_json() + "\n" + "\n".join(recent_lines) + "\n"
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(log_file)
