"""Tests for the ProviderRegistry."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from weave.core.registry import (
    ProviderRegistry,
    ProviderRegistryError,
    get_registry,
)
from weave.schemas.policy import RiskClass


BUILTIN_NAMES = {"claude-code", "codex", "gemini", "ollama", "opencode", "vllm"}


def _valid_user_contract(name: str, adapter_filename: str) -> dict:
    return {
        "contract_version": "1",
        "name": name,
        "display_name": name,
        "adapter": adapter_filename,
        "adapter_runtime": "bash",
        "capability_ceiling": "read-only",
        "protocol": {
            "request_schema": "weave.request.v1",
            "response_schema": "weave.response.v1",
        },
        "declared_features": [],
        "health_check": None,
    }


def _write_user_provider(root: Path, name: str, contract_override: dict | None = None) -> None:
    providers_dir = root / ".harness" / "providers"
    providers_dir.mkdir(parents=True, exist_ok=True)
    adapter_filename = f"{name}.sh"
    (providers_dir / adapter_filename).write_text("#!/usr/bin/env bash\necho '{}'\n")
    (providers_dir / adapter_filename).chmod(0o755)
    manifest = _valid_user_contract(name, adapter_filename)
    if contract_override:
        manifest.update(contract_override)
    (providers_dir / f"{name}.contract.json").write_text(json.dumps(manifest))


def test_registry_loads_all_five_builtins(tmp_path):
    reg = ProviderRegistry()
    reg.load(tmp_path)
    names = {c.name for c in reg.list()}
    assert names == BUILTIN_NAMES


def test_registry_builtin_files_exist_on_disk():
    import weave
    root = Path(weave.__file__).parent / "providers" / "builtin"
    assert root.is_dir()
    for name in BUILTIN_NAMES:
        assert (root / f"{name}.contract.json").exists(), f"{name}.contract.json missing"
        assert (root / f"{name}.sh").exists(), f"{name}.sh missing"


def test_registry_get_returns_contract(tmp_path):
    reg = ProviderRegistry()
    reg.load(tmp_path)
    claude = reg.get("claude-code")
    assert claude.name == "claude-code"
    assert claude.capability_ceiling == RiskClass.WORKSPACE_WRITE
    assert claude.source == "builtin"


def test_registry_get_raises_for_unknown(tmp_path):
    reg = ProviderRegistry()
    reg.load(tmp_path)
    with pytest.raises(KeyError):
        reg.get("no-such-provider")


def test_registry_has(tmp_path):
    reg = ProviderRegistry()
    reg.load(tmp_path)
    assert reg.has("claude-code") is True
    assert reg.has("no-such-provider") is False


def test_registry_list_is_sorted(tmp_path):
    reg = ProviderRegistry()
    reg.load(tmp_path)
    names = [c.name for c in reg.list()]
    assert names == sorted(names)


def test_registry_resolve_adapter_path_for_builtin(tmp_path):
    reg = ProviderRegistry()
    reg.load(tmp_path)
    path = reg.resolve_adapter_path("claude-code")
    assert path.exists()
    assert path.name == "claude-code.sh"


def test_registry_load_is_idempotent_for_same_root(tmp_path):
    reg = ProviderRegistry()
    reg.load(tmp_path)
    first_count = len(reg.list())
    reg.load(tmp_path)
    assert len(reg.list()) == first_count


def test_registry_reload_for_different_root(tmp_path):
    a = tmp_path / "proj_a"
    b = tmp_path / "proj_b"
    a.mkdir()
    b.mkdir()
    _write_user_provider(b, "extra")
    reg = ProviderRegistry()
    reg.load(a)
    assert reg.has("extra") is False
    reg.load(b)
    assert reg.has("extra") is True


def test_registry_loads_user_contract(tmp_path):
    _write_user_provider(tmp_path, "localtool")
    reg = ProviderRegistry()
    reg.load(tmp_path)
    assert reg.has("localtool")
    contract = reg.get("localtool")
    assert contract.source == "user"


def test_registry_user_overrides_builtin_with_warning(tmp_path, caplog):
    _write_user_provider(
        tmp_path,
        "claude-code",
        contract_override={"capability_ceiling": "read-only"},
    )
    reg = ProviderRegistry()
    with caplog.at_level(logging.WARNING, logger="weave.core.registry"):
        reg.load(tmp_path)
    contract = reg.get("claude-code")
    assert contract.source == "user"
    assert contract.capability_ceiling == RiskClass.READ_ONLY
    assert any("overrides built-in" in rec.message for rec in caplog.records)


def test_registry_skips_adapter_without_manifest(tmp_path, caplog):
    providers_dir = tmp_path / ".harness" / "providers"
    providers_dir.mkdir(parents=True)
    (providers_dir / "orphan.sh").write_text("#!/usr/bin/env bash\necho '{}'\n")
    (providers_dir / "orphan.sh").chmod(0o755)
    reg = ProviderRegistry()
    with caplog.at_level(logging.ERROR, logger="weave.core.registry"):
        reg.load(tmp_path)
    assert not reg.has("orphan")
    assert any("orphan" in rec.message and "no contract manifest" in rec.message
               for rec in caplog.records)


def test_registry_rejects_filename_stem_mismatch(tmp_path, caplog):
    providers_dir = tmp_path / ".harness" / "providers"
    providers_dir.mkdir(parents=True)
    (providers_dir / "mismatch.sh").write_text("#!/usr/bin/env bash\n")
    (providers_dir / "mismatch.sh").chmod(0o755)
    manifest = _valid_user_contract("wrong-name", "mismatch.sh")
    (providers_dir / "mismatch.contract.json").write_text(json.dumps(manifest))
    reg = ProviderRegistry()
    with caplog.at_level(logging.ERROR, logger="weave.core.registry"):
        reg.load(tmp_path)
    assert not reg.has("wrong-name")
    assert not reg.has("mismatch")
    assert any("filename stem" in rec.message.lower() for rec in caplog.records)


def test_registry_get_singleton_returns_same_instance(tmp_path):
    from weave.core import registry as registry_module
    # Reset singleton
    registry_module._REGISTRY_SINGLETON = None
    a = get_registry()
    b = get_registry()
    assert a is b
