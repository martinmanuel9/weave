"""Wire protocol v1 — runtime↔adapter request and response schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AdapterRequestV1(BaseModel):
    """Request payload sent to an adapter on stdin.

    camelCase on workingDir is deliberate — existing adapter shell scripts
    jq this field out of stdin. Changing to snake_case would force a
    simultaneous rewrite of every adapter for zero functional gain.
    """

    protocol: Literal["weave.request.v1"] = "weave.request.v1"
    session_id: str
    task: str
    workingDir: str
    context: str = ""
    timeout: int = 300


class AdapterResponseV1(BaseModel):
    """Response payload emitted by an adapter on stdout.

    camelCase fields match the shell-script jq output used since day one.
    """

    protocol: Literal["weave.response.v1"] = "weave.response.v1"
    exitCode: int
    stdout: str
    stderr: str
    structured: dict | None = None


PROTOCOL_VERSIONS: dict[str, type[BaseModel]] = {
    "weave.request.v1": AdapterRequestV1,
    "weave.response.v1": AdapterResponseV1,
}
