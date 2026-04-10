"""Security scanning — supply chain rules and write deny list."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path


def check_write_deny(
    files_changed: list[str],
    working_dir: Path,
    patterns: list[str],
) -> list[str]:
    """Return the subset of files_changed that match any deny pattern.

    Symlink-aware: resolves real paths before pattern matching, so a symlink
    pointing at a denied target is itself denied.
    """
    denied: list[str] = []
    for rel in files_changed:
        abs_path = (working_dir / rel).resolve()
        if _any_match(rel, patterns):
            denied.append(rel)
            continue
        try:
            rel_resolved = abs_path.relative_to(working_dir.resolve())
            if _any_match(str(rel_resolved), patterns):
                denied.append(rel)
                continue
        except ValueError:
            # abs_path escapes working_dir; suspicious
            denied.append(rel)
            continue
        basename = os.path.basename(rel)
        if _any_match(basename, patterns):
            denied.append(rel)
    return denied


def _any_match(path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(path, pat):
            return True
    return False
