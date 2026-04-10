"""Context assembly schema — deterministic project context with cache-key hashes."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ContextAssembly(BaseModel):
    """Deterministic assembly of project context.

    Separates stable prefix (project context, conventions, spec) from
    volatile per-turn content. Produces byte-stable output for identical
    inputs across runs — enables prompt cache stability and reliable
    session binding (Phase 2.2 / MAR-141).

    In Phase 2.3, volatile_task is always empty, so full == stable_prefix
    and full_hash == stable_hash. Phase 3 can populate volatile_task
    without schema changes.
    """
    stable_prefix: str
    volatile_task: str = ""
    full: str
    stable_hash: str
    full_hash: str
    source_files: list[str] = Field(default_factory=list)
