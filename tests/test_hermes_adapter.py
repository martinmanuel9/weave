import json
import pytest
import stat
from pathlib import Path


def test_hermes_contract_valid():
    contract_path = (
        Path(__file__).resolve().parents[1]
        / ".harness"
        / "providers"
        / "hermes.contract.json"
    )
    assert contract_path.exists(), f"Missing {contract_path}"
    contract = json.loads(contract_path.read_text())

    assert contract["name"] == "hermes"
    assert contract["adapter_runtime"] == "bash"
    assert contract["adapter"] == "hermes.sh"
    assert contract["capability_ceiling"] == "external-network"
    assert "tool-use" in contract["declared_features"]


def test_hermes_adapter_script_exists():
    adapter_path = (
        Path(__file__).resolve().parents[1]
        / ".harness"
        / "providers"
        / "hermes.sh"
    )
    assert adapter_path.exists()
    assert adapter_path.stat().st_mode & stat.S_IEXEC


def test_hermes_adapter_python_exists():
    adapter_path = (
        Path(__file__).resolve().parents[1]
        / ".harness"
        / "providers"
        / "hermes_adapter.py"
    )
    assert adapter_path.exists()
    content = adapter_path.read_text()
    assert "AIAgent" in content
    assert "skip_memory=True" in content
    assert "quiet_mode=True" in content
