"""Session binding — compute, write, read, and validate session compatibility fingerprints."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from weave.schemas.config import WeaveConfig
from weave.schemas.session_binding import SessionBinding


def _hash_config(config: WeaveConfig) -> str:
    """Compute a byte-stable sha256 of the config as canonicalized JSON.

    Uses json.dumps(sort_keys=True, separators=(",", ":")) to produce
    deterministic output regardless of dict iteration order or Pydantic
    serialization internals.
    """
    data = config.model_dump(mode="json")
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_binding(ctx) -> SessionBinding:
    """Build a SessionBinding from a PreparedContext.

    Not strictly pure because created_at reads wall-clock time via
    datetime.now(timezone.utc), but created_at is excluded from
    validate_session comparisons so the four compatibility fields
    (provider_name, adapter_script_hash, context_stable_hash, config_hash)
    are deterministic for identical inputs.

    No filesystem writes.

    Adapter script hash uses sha256(b"") as a fallback when the adapter
    file is missing — the binding stays well-formed even when scaffolding
    is incomplete.

    The ctx parameter is intentionally untyped to avoid a circular import
    with runtime.py (which defines PreparedContext and will later import
    this module). The function accesses ctx.session_id, ctx.active_provider,
    ctx.adapter_script, ctx.context.stable_hash, ctx.config via duck typing.
    """
    if ctx.adapter_script.exists():
        adapter_bytes = ctx.adapter_script.read_bytes()
        adapter_script_hash = hashlib.sha256(adapter_bytes).hexdigest()
    else:
        adapter_script_hash = hashlib.sha256(b"").hexdigest()

    return SessionBinding(
        session_id=ctx.session_id,
        created_at=datetime.now(timezone.utc),
        provider_name=ctx.active_provider,
        adapter_script_hash=adapter_script_hash,
        context_stable_hash=ctx.context.stable_hash,
        config_hash=_hash_config(ctx.config),
    )
