"""
Hermes integration — apply Weave-owned context to a hermes-agent checkout.

Hermes-agent is upstream code (nousresearch/hermes-agent) we don't control.
Rather than committing local mods that conflict on every upstream pull, Weave
owns the canonical context (CLAUDE.md, .claude/ skills, AGENTS.md GitNexus
snippet) and applies it via:

  * symlinks for untracked files (CLAUDE.md, .claude/)
  * marker-block injection for AGENTS.md (the only tracked file we extend),
    bracketed by <!-- gitnexus:start --> ... <!-- gitnexus:end --> so future
    upstream pulls and removals are mechanical.

Both apply and remove are idempotent.
"""
from __future__ import annotations

import shutil
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
ASSETS_DIR = _REPO_ROOT / "integrations" / "hermes"
WORKING_TREE_DIR = ASSETS_DIR / "working-tree"
AGENTS_SNIPPET_PATH = ASSETS_DIR / "agents-snippet.md"

_SYMLINKED_PATHS = ("CLAUDE.md", ".claude")
_AGENTS_FILE = "AGENTS.md"
_MARKER_START = "<!-- gitnexus:start -->"
_MARKER_END = "<!-- gitnexus:end -->"


def apply_context(repo_path: Path, *, force: bool = False) -> dict[str, str]:
    """Symlink CLAUDE.md and .claude/ from Weave assets, inject AGENTS.md snippet.

    Existing real files at the symlink targets are preserved unless `force=True`.
    Returns {path: action} where action ∈ {"linked", "already-linked",
    "skipped-needs-force", "snippet-injected", "snippet-replaced",
    "snippet-unchanged"}.
    """
    repo_path = repo_path.resolve()
    if not repo_path.is_dir():
        raise FileNotFoundError(f"Target repo does not exist: {repo_path}")

    actions: dict[str, str] = {}
    for name in _SYMLINKED_PATHS:
        actions[name] = _apply_symlink(
            target=repo_path / name,
            asset=WORKING_TREE_DIR / name,
            force=force,
        )
    actions[_AGENTS_FILE] = _apply_agents_snippet(repo_path / _AGENTS_FILE)
    return actions


def remove_context(repo_path: Path) -> dict[str, str]:
    """Delete the Weave-applied symlinks and remove the AGENTS.md marker block."""
    repo_path = repo_path.resolve()
    actions: dict[str, str] = {}
    for name in _SYMLINKED_PATHS:
        target = repo_path / name
        if target.is_symlink():
            target.unlink()
            actions[name] = "removed"
        elif target.exists():
            actions[name] = "skipped-not-symlink"
        else:
            actions[name] = "absent"
    actions[_AGENTS_FILE] = _remove_agents_snippet(repo_path / _AGENTS_FILE)
    return actions


def _apply_symlink(*, target: Path, asset: Path, force: bool) -> str:
    if not asset.exists():
        raise FileNotFoundError(f"Weave asset missing: {asset}")
    if target.is_symlink():
        if target.resolve() == asset.resolve():
            return "already-linked"
        target.unlink()
    elif target.exists():
        if not force:
            return "skipped-needs-force"
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    target.symlink_to(asset)
    return "linked"


def _apply_agents_snippet(agents_path: Path) -> str:
    if not agents_path.exists():
        raise FileNotFoundError(f"AGENTS.md not found: {agents_path}")
    snippet = AGENTS_SNIPPET_PATH.read_text().rstrip()
    body = agents_path.read_text()

    start = body.find(_MARKER_START)
    end = body.find(_MARKER_END)

    if start == -1 and end == -1:
        new_body = body.rstrip() + "\n\n" + snippet + "\n"
        action = "snippet-injected"
    elif start == -1 or end == -1 or end < start:
        raise ValueError(
            f"AGENTS.md has malformed gitnexus markers in {agents_path}"
        )
    else:
        end_inclusive = end + len(_MARKER_END)
        if body[start:end_inclusive] == snippet:
            return "snippet-unchanged"
        new_body = body[:start] + snippet + body[end_inclusive:]
        action = "snippet-replaced"

    agents_path.write_text(new_body)
    return action


def _remove_agents_snippet(agents_path: Path) -> str:
    if not agents_path.exists():
        return "absent"
    body = agents_path.read_text()
    start = body.find(_MARKER_START)
    end = body.find(_MARKER_END)
    if start == -1 and end == -1:
        return "absent"
    if start == -1 or end == -1 or end < start:
        raise ValueError(
            f"AGENTS.md has malformed gitnexus markers in {agents_path}"
        )
    end_inclusive = end + len(_MARKER_END)
    new_body = body[:start].rstrip() + "\n" + body[end_inclusive:].lstrip("\n")
    if not new_body.endswith("\n"):
        new_body += "\n"
    agents_path.write_text(new_body)
    return "removed"
