"""Tests for src/weave/core/translate.py"""
import pytest
from pathlib import Path

from weave.core.translate import (
    translate_context,
    check_for_hand_edits,
    _content_hash,
)


def _make_context(project_dir: Path) -> None:
    """Create minimal .harness/context/ files."""
    ctx = project_dir / ".harness" / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    (ctx / "conventions.md").write_text("# Conventions\nUse snake_case.")
    (ctx / "brief.md").write_text("# Brief\nThis is a test project.")
    (ctx / "spec.md").write_text("# Spec\nBuild something great.")


# ---------------------------------------------------------------------------
# test_translate_generates_files
# ---------------------------------------------------------------------------

def test_translate_generates_files(tmp_path):
    _make_context(tmp_path)
    result = translate_context(tmp_path, ["claude-code", "gemini"])

    assert (tmp_path / "CLAUDE.md").exists(), "CLAUDE.md should have been generated"
    assert (tmp_path / "GEMINI.md").exists(), "GEMINI.md should have been generated"

    assert "CLAUDE.md" in result["generated"]
    assert "GEMINI.md" in result["generated"]
    assert result["skipped"] == []


# ---------------------------------------------------------------------------
# test_translate_detects_hand_edits
# ---------------------------------------------------------------------------

def test_translate_detects_hand_edits(tmp_path):
    _make_context(tmp_path)
    translate_context(tmp_path, ["claude-code"])

    # Simulate a hand edit
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(claude_md.read_text() + "\n<!-- hand edit -->")

    edited = check_for_hand_edits(tmp_path)
    assert "CLAUDE.md" in edited


# ---------------------------------------------------------------------------
# test_translate_skips_edited
# ---------------------------------------------------------------------------

def test_translate_skips_edited(tmp_path):
    _make_context(tmp_path)
    translate_context(tmp_path, ["claude-code"])

    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(claude_md.read_text() + "\n<!-- hand edit -->")

    # Second translate without force — should skip the edited file
    result = translate_context(tmp_path, ["claude-code"])
    assert "CLAUDE.md" in result["skipped"]
    assert "CLAUDE.md" not in result["generated"]


# ---------------------------------------------------------------------------
# test_translate_force_overwrites
# ---------------------------------------------------------------------------

def test_translate_force_overwrites(tmp_path):
    _make_context(tmp_path)
    translate_context(tmp_path, ["claude-code"])

    claude_md = tmp_path / "CLAUDE.md"
    original_content = claude_md.read_text()
    claude_md.write_text(original_content + "\n<!-- hand edit -->")

    # force=True should overwrite
    result = translate_context(tmp_path, ["claude-code"], force=True)
    assert "CLAUDE.md" in result["generated"]
    assert "CLAUDE.md" not in result["skipped"]

    # Content should be back to the generated version
    assert claude_md.read_text() == original_content


# ---------------------------------------------------------------------------
# test_translate_deduplicates
# ---------------------------------------------------------------------------

def test_translate_deduplicates(tmp_path):
    _make_context(tmp_path)
    # codex and ollama both map to AGENTS.md
    result = translate_context(tmp_path, ["codex", "ollama"])

    agents_md = tmp_path / "AGENTS.md"
    assert agents_md.exists(), "AGENTS.md should exist"

    # AGENTS.md should appear only once in generated
    assert result["generated"].count("AGENTS.md") == 1
    assert result["skipped"] == []
