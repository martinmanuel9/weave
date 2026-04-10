"""Tests for deterministic context assembly."""
from pathlib import Path


def _make_context_dir(root: Path, files: dict[str, str]) -> Path:
    """Helper: create .harness/context/ with the given files."""
    context_dir = root / ".harness" / "context"
    context_dir.mkdir(parents=True)
    for name, content in files.items():
        (context_dir / name).write_text(content)
    return context_dir


def test_assemble_context_canonical_ordering(temp_dir):
    """Canonical files come first in defined order; rest alphabetical."""
    from weave.core.context import assemble_context
    _make_context_dir(temp_dir, {
        "brief.md": "brief",
        "spec.md": "spec",
        "conventions.md": "conv",
        "extra.md": "extra",
        "another.md": "another",
    })
    ca = assemble_context(temp_dir)
    assert ca.source_files == [
        "conventions.md",
        "brief.md",
        "spec.md",
        "another.md",
        "extra.md",
    ]


def test_assemble_context_byte_stable_across_runs(temp_dir):
    """Same inputs produce byte-identical outputs across repeated calls."""
    from weave.core.context import assemble_context
    _make_context_dir(temp_dir, {
        "conventions.md": "stable conventions",
        "brief.md": "stable brief",
        "spec.md": "stable spec",
    })

    first = assemble_context(temp_dir)
    second = assemble_context(temp_dir)

    assert first.stable_prefix == second.stable_prefix
    assert first.full == second.full
    assert first.stable_hash == second.stable_hash
    assert first.full_hash == second.full_hash
    assert first.source_files == second.source_files
