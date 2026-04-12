"""Shared pytest fixtures and helpers for weave tests."""
from __future__ import annotations

import pytest
from pathlib import Path
import tempfile
import shutil

from weave.schemas.policy import RiskClass
from weave.schemas.provider_contract import (
    AdapterRuntime,
    ProviderContract,
    ProviderFeature,
    ProviderProtocol,
)


@pytest.fixture
def temp_dir():
    d = Path(tempfile.mkdtemp(prefix="weave-test-"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def harness_dir(temp_dir):
    h = temp_dir / ".harness"
    h.mkdir()
    for sub in ["context", "hooks", "providers", "sessions", "integrations"]:
        (h / sub).mkdir()
    return h


def make_contract(
    name: str = "test-provider",
    capability_ceiling: RiskClass = RiskClass.WORKSPACE_WRITE,
    adapter: str = "test-provider.sh",
    adapter_runtime: AdapterRuntime = AdapterRuntime.BASH,
    features: list[ProviderFeature] | None = None,
    source: str = "builtin",
) -> ProviderContract:
    """Build a minimal valid ProviderContract for tests."""
    return ProviderContract(
        name=name,
        display_name=name,
        adapter=adapter,
        adapter_runtime=adapter_runtime,
        capability_ceiling=capability_ceiling,
        protocol=ProviderProtocol(
            request_schema="weave.request.v1",
            response_schema="weave.response.v1",
        ),
        declared_features=features or [],
        source=source,
    )
