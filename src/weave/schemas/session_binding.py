"""Session binding schema — compatibility fingerprint of a session's creation-time inputs."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SessionBinding(BaseModel):
    """Compatibility fingerprint of a session's creation-time inputs.

    Written as a .binding.json sidecar alongside the session JSONL.
    Captured once at prepare() time; never updated. Future callers
    that want to reuse a session can load this binding and compare
    its hashes against the current PreparedContext — any mismatch
    means the session has drifted from its original conditions and
    should not be reused.

    Phase 2.2 (MAR-141) produces bindings but does not yet gate
    anything on them. validate_session() exists as a pure comparison
    function for future consumers.
    """
    session_id: str
    created_at: datetime
    provider_name: str
    adapter_script_hash: str
    context_stable_hash: str
    config_hash: str
