"""
NotebookLM integration — sync harness context to a NotebookLM notebook.

Uses the notebooklm-py CLI (must be installed: pip install notebooklm-py).
"""

import json
import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def is_notebooklm_installed() -> bool:
    """Check if the notebooklm CLI is available."""
    try:
        result = subprocess.run(
            ["notebooklm", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def get_notebooks() -> list[dict]:
    """List all NotebookLM notebooks."""
    try:
        result = subprocess.run(
            ["notebooklm", "metadata", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        # Fallback: parse list output
        return []
    except Exception as e:
        logger.warning(f"Failed to list notebooks: {e}")
        return []


def sync_context_to_notebook(notebook_id: str, context_dir: Path,
                             project_name: str = "") -> dict:
    """
    Sync .harness/context/ files to a NotebookLM notebook as sources.

    Concatenates context files into a single markdown doc and adds it
    as a source to the specified notebook.

    Args:
        notebook_id: NotebookLM notebook ID (or partial ID)
        context_dir: Path to .harness/context/
        project_name: Project name for source title

    Returns:
        dict with: synced (bool), source_id (str or None), error (str or None)
    """
    # Collect context
    parts = []
    for name in ["brief.md", "conventions.md", "spec.md"]:
        path = context_dir / name
        if path.exists():
            content = path.read_text().strip()
            if content:
                parts.append(content)

    if not parts:
        return {"synced": False, "source_id": None, "error": "No context files found"}

    combined = f"# {project_name or 'Project'} — Harness Context (auto-synced)\n\n"
    combined += "\n\n---\n\n".join(parts)

    # Write to temp file and add as source
    try:
        # Set notebook context
        subprocess.run(
            ["notebooklm", "use", notebook_id],
            capture_output=True, text=True, timeout=10,
        )

        # Write combined context to temp markdown file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, prefix="weave-sync-") as f:
            f.write(combined)
            temp_path = f.name

        # Add as source
        result = subprocess.run(
            ["notebooklm", "source", "add", temp_path],
            capture_output=True, text=True, timeout=30,
        )

        # Clean up temp file
        Path(temp_path).unlink(missing_ok=True)

        if result.returncode == 0:
            # Parse source ID from output
            source_id = result.stdout.strip().split(":")[-1].strip() if ":" in result.stdout else None
            logger.info(f"Synced context to NotebookLM notebook {notebook_id}")
            return {"synced": True, "source_id": source_id, "error": None}
        else:
            return {"synced": False, "source_id": None, "error": result.stderr.strip() or "Add source failed"}

    except Exception as e:
        Path(temp_path).unlink(missing_ok=True) if 'temp_path' in locals() else None
        return {"synced": False, "source_id": None, "error": str(e)}


def sync_file_to_notebook(notebook_id: str, file_path: Path) -> dict:
    """
    Add a single file as a source to a NotebookLM notebook.

    Args:
        notebook_id: NotebookLM notebook ID
        file_path: Path to file to add (must be .md, .txt, or .pdf)

    Returns:
        dict with: synced (bool), source_id (str or None), error (str or None)
    """
    if not file_path.exists():
        return {"synced": False, "source_id": None, "error": f"File not found: {file_path}"}

    try:
        subprocess.run(
            ["notebooklm", "use", notebook_id],
            capture_output=True, text=True, timeout=10,
        )

        result = subprocess.run(
            ["notebooklm", "source", "add", str(file_path)],
            capture_output=True, text=True, timeout=30,
        )

        if result.returncode == 0:
            source_id = result.stdout.strip().split(":")[-1].strip() if ":" in result.stdout else None
            return {"synced": True, "source_id": source_id, "error": None}
        else:
            return {"synced": False, "source_id": None, "error": result.stderr.strip() or "Add failed"}

    except Exception as e:
        return {"synced": False, "source_id": None, "error": str(e)}
