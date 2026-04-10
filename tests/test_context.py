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


def test_assemble_context_normalizes_line_endings(temp_dir):
    """CRLF and LF produce identical stable_hash when content is semantically equal."""
    from weave.core.context import assemble_context

    # Two separate temp directories with semantically-identical content
    # but different line endings
    lf_dir = temp_dir / "lf"
    crlf_dir = temp_dir / "crlf"
    lf_dir.mkdir()
    crlf_dir.mkdir()

    lf_context = lf_dir / ".harness" / "context"
    crlf_context = crlf_dir / ".harness" / "context"
    lf_context.mkdir(parents=True)
    crlf_context.mkdir(parents=True)

    # Same semantic content, different raw bytes
    lf_content = "line one\nline two\nline three\n"
    crlf_content = "line one\r\nline two\r\nline three\r\n"

    # Write as bytes to bypass any platform auto-translation
    (lf_context / "spec.md").write_bytes(lf_content.encode("utf-8"))
    (crlf_context / "spec.md").write_bytes(crlf_content.encode("utf-8"))

    lf_result = assemble_context(lf_dir)
    crlf_result = assemble_context(crlf_dir)

    assert lf_result.stable_hash == crlf_result.stable_hash
    assert lf_result.stable_prefix == crlf_result.stable_prefix
