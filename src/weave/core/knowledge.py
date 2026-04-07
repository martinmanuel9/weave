"""Append-only knowledge register at .harness/knowledge.md."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

_KNOWLEDGE_FILE = ".harness/knowledge.md"


def append_knowledge(project_dir: Path, entry: str) -> None:
    """Append a timestamped entry to .harness/knowledge.md."""
    harness_dir = project_dir / ".harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    knowledge_file = project_dir / _KNOWLEDGE_FILE
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"- [{timestamp}] {entry}\n"
    with knowledge_file.open("a", encoding="utf-8") as fh:
        fh.write(line)


def read_knowledge(project_dir: Path) -> list[str]:
    """Return all knowledge entries (lines starting with '- [')."""
    knowledge_file = project_dir / _KNOWLEDGE_FILE
    if not knowledge_file.exists():
        return []
    entries: list[str] = []
    for line in knowledge_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("- ["):
            entries.append(line)
    return entries
