"""ProviderContract schema — declarative manifest for a provider adapter."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from weave.schemas.policy import RiskClass
from weave.schemas.protocol import PROTOCOL_VERSIONS


class ProviderFeature(str, Enum):
    STREAMING = "streaming"
    STRUCTURED_OUTPUT = "structured-output"
    TOOL_USE = "tool-use"
    THINKING = "thinking"
    MULTIMODAL_INPUT = "multimodal-input"
    FILE_EDIT = "file-edit"
    SHELL_EXEC = "shell-exec"


class AdapterRuntime(str, Enum):
    BASH = "bash"
    PYTHON = "python"
    NODE = "node"
    BINARY = "binary"  # direct exec, no interpreter


class ProviderProtocol(BaseModel):
    request_schema: str
    response_schema: str

    @field_validator("request_schema")
    @classmethod
    def _validate_request_schema(cls, v: str) -> str:
        if v not in PROTOCOL_VERSIONS:
            raise ValueError(
                f"unknown request_schema {v!r}; "
                f"known: {sorted(PROTOCOL_VERSIONS.keys())}"
            )
        return v

    @field_validator("response_schema")
    @classmethod
    def _validate_response_schema(cls, v: str) -> str:
        if v not in PROTOCOL_VERSIONS:
            raise ValueError(
                f"unknown response_schema {v!r}; "
                f"known: {sorted(PROTOCOL_VERSIONS.keys())}"
            )
        return v


class ProviderContract(BaseModel):
    """Declarative contract for a provider adapter.

    Loaded by `ProviderRegistry` from `.contract.json` sidecar files.
    `source` is set by the loader (builtin vs user) and any value present
    in the JSON file is ignored and overwritten.
    """

    contract_version: Literal["1"] = "1"
    name: str
    display_name: str
    adapter: str  # relative to manifest directory
    adapter_runtime: AdapterRuntime
    capability_ceiling: RiskClass
    protocol: ProviderProtocol
    declared_features: list[ProviderFeature] = Field(default_factory=list)
    health_check: str | None = None
    source: Literal["builtin", "user"] = "builtin"
