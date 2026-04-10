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


def write_binding(binding: SessionBinding, sessions_dir: Path) -> Path:
    """Serialize a SessionBinding to a .binding.json sidecar.

    Creates sessions_dir if it does not exist (matching append_activity's
    behavior). Returns the written path.
    """
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = sessions_dir / f"{binding.session_id}.binding.json"
    sidecar_path.write_text(binding.model_dump_json(indent=2))
    return sidecar_path


def read_binding(session_id: str, sessions_dir: Path) -> SessionBinding | None:
    """Load a SessionBinding from its .binding.json sidecar.

    Returns None if the file does not exist. Raises on malformed JSON
    or Pydantic validation errors — a broken binding is an operator-facing
    error, not silently ignorable.
    """
    sidecar_path = sessions_dir / f"{session_id}.binding.json"
    if not sidecar_path.exists():
        return None
    return SessionBinding.model_validate_json(sidecar_path.read_text())


def validate_session(
    session_id: str,
    ctx,
    sessions_dir: Path,
) -> list[str]:
    """Return the list of binding field names that differ between the
    stored binding and the current PreparedContext.

    Empty list means the session is reusable against ctx. Non-empty
    means one or more invalidating inputs changed.

    Raises FileNotFoundError if the binding sidecar does not exist —
    a nonexistent binding is qualitatively different from a mismatched
    binding. Callers should treat them as distinct signals.

    The comparison checks exactly four fields: provider_name,
    adapter_script_hash, context_stable_hash, config_hash. session_id
    and created_at are identity fields, not compatibility fields.

    The ctx parameter is untyped for the same circular-import reason
    as compute_binding.
    """
    binding = read_binding(session_id, sessions_dir)
    if binding is None:
        raise FileNotFoundError(f"No binding sidecar for session {session_id}")

    current = compute_binding(ctx)
    mismatches: list[str] = []
    if binding.provider_name != current.provider_name:
        mismatches.append("provider_name")
    if binding.adapter_script_hash != current.adapter_script_hash:
        mismatches.append("adapter_script_hash")
    if binding.context_stable_hash != current.context_stable_hash:
        mismatches.append("context_stable_hash")
    if binding.config_hash != current.config_hash:
        mismatches.append("config_hash")
    return mismatches
