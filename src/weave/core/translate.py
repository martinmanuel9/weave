"""
Context translation: generate CLAUDE.md / GEMINI.md / AGENTS.md from .harness/context/
with SHA256 hash-based edit detection.
"""
import hashlib
import json
from pathlib import Path

PROVIDER_FILE_MAP = {
    "claude-code": "CLAUDE.md",
    "codex": "AGENTS.md",
    "gemini": "GEMINI.md",
    "ollama": "AGENTS.md",
}

_HASHES_FILE = ".harness/context/.hashes.json"
_CONTEXT_FILES = ["conventions.md", "brief.md", "spec.md"]


def _content_hash(content: str) -> str:
    """Return the first 16 chars of the SHA256 hex digest of content."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _load_hashes(project_dir) -> dict:
    """Read .harness/context/.hashes.json; return empty dict if missing."""
    path = Path(project_dir) / _HASHES_FILE
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_hashes(project_dir, hashes: dict) -> None:
    """Write hashes dict to .harness/context/.hashes.json."""
    path = Path(project_dir) / _HASHES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hashes, indent=2))


def _load_context(project_dir) -> str:
    """Concatenate conventions.md + brief.md + spec.md with '---' separator."""
    context_dir = Path(project_dir) / ".harness" / "context"
    parts = []
    for filename in _CONTEXT_FILES:
        p = context_dir / filename
        if p.exists():
            parts.append(p.read_text())
    return "\n---\n".join(parts)


def check_for_hand_edits(project_dir) -> list[str]:
    """
    Compare current on-disk hash of each generated file against the stored hash.
    Returns a list of filenames (e.g. ["CLAUDE.md"]) that have been hand-edited.
    """
    project_dir = Path(project_dir)
    hashes = _load_hashes(project_dir)
    edited = []
    for filename, stored_hash in hashes.items():
        file_path = project_dir / filename
        if file_path.exists():
            current_hash = _content_hash(file_path.read_text())
            if current_hash != stored_hash:
                edited.append(filename)
    return edited


def translate_context(
    project_dir, providers: list[str], force: bool = False
) -> dict:
    """
    Generate provider-specific context files from .harness/context/.

    Returns a dict: {"generated": [...], "skipped": [...]}.

    - Skips files that have been hand-edited (unless force=True).
    - Deduplicates: if multiple providers map to the same file, it is only
      written once.
    - Updates .hashes.json after writing.
    """
    project_dir = Path(project_dir)
    content = _load_context(project_dir)
    hashes = _load_hashes(project_dir)

    # Detect hand-edited files upfront
    edited_files = set(check_for_hand_edits(project_dir)) if not force else set()

    # Build deduplicated mapping: filename -> first provider that maps to it
    seen_files: dict[str, str] = {}
    for provider in providers:
        filename = PROVIDER_FILE_MAP.get(provider)
        if filename and filename not in seen_files:
            seen_files[filename] = provider

    generated = []
    skipped = []

    for filename in seen_files:
        file_path = project_dir / filename

        if not force and filename in edited_files:
            skipped.append(filename)
            continue

        file_path.write_text(content)
        hashes[filename] = _content_hash(content)
        generated.append(filename)

    _save_hashes(project_dir, hashes)
    return {"generated": generated, "skipped": skipped}
