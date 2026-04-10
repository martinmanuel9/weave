"""Deterministic context assembly from .harness/context/ markdown files."""
from __future__ import annotations

import hashlib
from pathlib import Path

from weave.schemas.context import ContextAssembly


# Canonical order for well-known files — these come first when present.
# Other markdown files follow in alphabetical order.
_CANONICAL_ORDER = ["conventions.md", "brief.md", "spec.md"]

_SEPARATOR = "\n---\n"


def assemble_context(working_dir: Path) -> ContextAssembly:
    """Assemble a deterministic ContextAssembly from .harness/context/*.md.

    Ordering rules:
      1. Files in _CANONICAL_ORDER appear first, in that exact order
      2. Remaining *.md files follow in alphabetical order
      3. Canonical files are removed from the 'rest' partition before
         alphabetical ordering — no file is ever concatenated twice
      4. Hidden files (starting with '.') are excluded
      5. Missing canonical files are silently skipped

    Content rules:
      1. Each file's content is read as UTF-8
      2. Line endings are normalized: \\r\\n -> \\n, then \\r -> \\n
      3. Normalized contents are joined with '\\n---\\n' (no trailing whitespace)

    Phase 2.3: volatile_task is always empty, so full == stable_prefix
    and full_hash == stable_hash. Phase 3 can populate volatile_task.
    """
    context_dir = working_dir / ".harness" / "context"
    if not context_dir.exists():
        return _empty_assembly()

    # Discover all non-hidden .md files
    all_files = sorted(
        f for f in context_dir.glob("*.md")
        if not f.name.startswith(".")
    )

    # Partition into canonical (in defined order) and rest (alphabetical)
    canonical_files: list[Path] = []
    for name in _CANONICAL_ORDER:
        candidate = context_dir / name
        if candidate in all_files:
            canonical_files.append(candidate)

    rest = [f for f in all_files if f not in canonical_files]
    ordered = canonical_files + rest

    if not ordered:
        return _empty_assembly()

    # Read and normalize each file
    parts: list[str] = []
    source_files: list[str] = []
    for f in ordered:
        content = f.read_text(encoding="utf-8")
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        parts.append(normalized)
        source_files.append(f.name)

    stable_prefix = _SEPARATOR.join(parts)
    volatile_task = ""
    full = stable_prefix  # Phase 2.3: no volatile content

    stable_hash = hashlib.sha256(stable_prefix.encode("utf-8")).hexdigest()
    full_hash = hashlib.sha256(full.encode("utf-8")).hexdigest()

    return ContextAssembly(
        stable_prefix=stable_prefix,
        volatile_task=volatile_task,
        full=full,
        stable_hash=stable_hash,
        full_hash=full_hash,
        source_files=source_files,
    )


def _empty_assembly() -> ContextAssembly:
    """Return an empty but well-formed ContextAssembly."""
    empty_hash = hashlib.sha256(b"").hexdigest()
    return ContextAssembly(
        stable_prefix="",
        volatile_task="",
        full="",
        stable_hash=empty_hash,
        full_hash=empty_hash,
        source_files=[],
    )
