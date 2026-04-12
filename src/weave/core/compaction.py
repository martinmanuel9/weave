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


@dataclass
class CompactResult:
    """Result of a cross-session compaction run."""
    kept: int
    removed: int
    errors: list[str] = field(default_factory=list)


def _build_ledger_entry(session_id: str, jsonl_path: Path) -> dict:
    """Read a session JSONL and produce a one-line ledger summary dict."""
    records: list[ActivityRecord] = []
    try:
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(ActivityRecord.model_validate_json(line))
                except Exception:
                    pass
    except Exception:
        pass

    if not records:
        return {
            "session_id": session_id,
            "provider": None,
            "started": None,
            "ended": None,
            "invocation_count": 0,
            "total_duration_ms": 0.0,
            "final_status": "unknown",
            "files_changed_count": 0,
            "task_snippet": "",
        }

    invocation_count = 0
    total_duration = 0.0
    total_files = 0
    providers: Counter[str] = Counter()
    earliest: datetime | None = None
    latest: datetime | None = None
    first_task: str | None = None

    for r in records:
        if r.task == "compaction_summary" and r.type == ActivityType.system:
            meta = r.metadata
            invocation_count += meta.get("compacted_count", 0)
            total_duration += meta.get("total_duration_ms", 0.0)
            total_files += meta.get("total_files_changed", 0)
            for p in meta.get("providers_used", []):
                providers[p] += meta.get("compacted_count", 0)
            e = meta.get("earliest_timestamp")
            if e:
                e_dt = datetime.fromisoformat(e)
                if earliest is None or e_dt < earliest:
                    earliest = e_dt
            l_ts = meta.get("latest_timestamp")
            if l_ts:
                l_dt = datetime.fromisoformat(l_ts)
                if latest is None or l_dt > latest:
                    latest = l_dt
        else:
            invocation_count += 1
            total_duration += r.duration or 0.0
            total_files += len(r.files_changed)
            if r.provider:
                providers[r.provider] += 1
            if first_task is None and r.task:
                first_task = r.task
            ts = r.timestamp
            if earliest is None or ts < earliest:
                earliest = ts
            if latest is None or ts > latest:
                latest = ts

    primary_provider = providers.most_common(1)[0][0] if providers else None
    real_records = [
        r for r in records
        if not (r.task == "compaction_summary" and r.type == ActivityType.system)
    ]
    final_status = real_records[-1].status.value if real_records else "unknown"

    return {
        "session_id": session_id,
        "provider": primary_provider,
        "started": earliest.isoformat() if earliest else None,
        "ended": latest.isoformat() if latest else None,
        "invocation_count": invocation_count,
        "total_duration_ms": total_duration,
        "final_status": final_status,
        "files_changed_count": total_files,
        "task_snippet": (first_task or "")[:100],
    }


def _append_ledger(ledger_path: Path, entry: dict) -> None:
    """Append a ledger entry as a JSON line. Raises on failure."""
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _delete_session_files(sessions_dir: Path, session_id: str) -> list[str]:
    """Delete all files for a session. Returns list of errors (empty = success)."""
    errors: list[str] = []
    for suffix in [".jsonl", ".binding.json", ".start_marker.json"]:
        path = sessions_dir / f"{session_id}{suffix}"
        if path.exists():
            try:
                path.unlink()
            except OSError as exc:
                errors.append(f"failed to delete {path.name}: {exc}")
    return errors


def compact_sessions(
    sessions_dir: Path,
    sessions_to_keep: int,
    dry_run: bool = False,
) -> CompactResult:
    """Compact old sessions: summarize to ledger, then delete raw files.

    Keeps the `sessions_to_keep` most recently modified sessions.
    """
    if not sessions_dir.is_dir():
        return CompactResult(kept=0, removed=0)

    session_files = sorted(
        (p for p in sessions_dir.glob("*.jsonl") if p.name != "session_history.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if len(session_files) <= sessions_to_keep:
        return CompactResult(kept=len(session_files), removed=0)

    keep_files = session_files[:sessions_to_keep]
    remove_files = session_files[sessions_to_keep:]

    if dry_run:
        return CompactResult(kept=len(keep_files), removed=len(remove_files))

    ledger_path = sessions_dir / "session_history.jsonl"
    all_errors: list[str] = []

    for jsonl_file in remove_files:
        session_id = jsonl_file.stem
        try:
            entry = _build_ledger_entry(session_id, jsonl_file)
            _append_ledger(ledger_path, entry)
        except Exception as exc:
            all_errors.append(f"session {session_id}: ledger write failed: {exc}")
            continue

        delete_errors = _delete_session_files(sessions_dir, session_id)
        all_errors.extend(delete_errors)

    return CompactResult(
        kept=len(keep_files),
        removed=len(remove_files),
        errors=all_errors,
    )
