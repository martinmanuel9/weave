"""Weave manifest read/write helpers for .harness/ directories."""
from __future__ import annotations

from pathlib import Path

from weave.schemas.manifest import Manifest


def write_manifest(unit_dir: Path, manifest: Manifest) -> None:
    """Write a Manifest as JSON to <unit_dir>/.harness/manifest.json."""
    path = unit_dir / ".harness" / "manifest.json"
    path.write_text(manifest.model_dump_json(indent=2))


def read_manifest(unit_dir: Path) -> Manifest:
    """Read and validate a Manifest from <unit_dir>/.harness/manifest.json."""
    path = unit_dir / ".harness" / "manifest.json"
    return Manifest.model_validate_json(path.read_text())
