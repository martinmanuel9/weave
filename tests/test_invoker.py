"""Tests for weave provider invoker."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from weave.core.invoker import InvokeResult, _build_argv, _get_git_changed_files, invoke_provider
from weave.core.registry import ProviderRegistry
from weave.schemas.policy import RiskClass
from weave.schemas.provider_contract import (
    AdapterRuntime,
    ProviderContract,
    ProviderProtocol,
)


def _valid_contract_for(name: str, adapter_filename: str = "adapter.sh") -> ProviderContract:
    """Return a minimal valid contract for testing."""
    return ProviderContract(
        name=name,
        display_name=name,
        adapter=adapter_filename,
        adapter_runtime=AdapterRuntime.BASH,
        capability_ceiling=RiskClass.WORKSPACE_WRITE,
        protocol=ProviderProtocol(
            request_schema="weave.request.v1",
            response_schema="weave.response.v1",
        ),
    )


def _make_registry(name: str, contract: ProviderContract, adapter_dir: Path) -> ProviderRegistry:
    """Build a ProviderRegistry with one contract wired to adapter_dir."""
    registry = ProviderRegistry()
    registry._contracts[name] = contract
    registry._manifest_dirs[name] = adapter_dir
    return registry


# -------------------------------------------------------------------
# Tests for _get_git_changed_files and InvokeResult (unchanged)
# -------------------------------------------------------------------

def test_get_git_changed_files_no_git(tmp_path):
    """In a non-git directory, returns empty list without raising."""
    files = _get_git_changed_files(tmp_path)
    assert isinstance(files, list)


def test_invoke_result_dataclass():
    """InvokeResult fields are accessible."""
    r = InvokeResult(
        exit_code=0,
        stdout="out",
        stderr="",
        structured={"key": "val"},
        duration=42.5,
        files_changed=["foo.py"],
    )
    assert r.exit_code == 0
    assert r.files_changed == ["foo.py"]
    assert r.duration == 42.5


# -------------------------------------------------------------------
# Contract-based invoke_provider tests
# -------------------------------------------------------------------

def test_invoke_missing_adapter(tmp_path):
    """Contract points to a nonexistent adapter file."""
    contract = _valid_contract_for("ghost", adapter_filename="ghost.sh")
    registry = _make_registry("ghost", contract, tmp_path)

    result = invoke_provider(
        contract=contract,
        task="do something",
        session_id="sess-001",
        working_dir=tmp_path,
        registry=registry,
    )
    assert result.exit_code == 1
    assert "not found" in result.stderr.lower()
    assert result.structured is None


def test_invoke_contract_valid_response_populates_structured(tmp_path):
    """Adapter returns valid weave.response.v1 JSON — structured is populated."""
    adapter = tmp_path / "adapter.sh"
    response_obj = {
        "protocol": "weave.response.v1",
        "exitCode": 0,
        "stdout": "hello",
        "stderr": "",
        "structured": {"key": "val"},
    }
    adapter.write_text(
        "#!/usr/bin/env bash\n"
        "cat /dev/stdin > /dev/null\n"
        f"echo '{json.dumps(response_obj)}'\n"
    )
    adapter.chmod(adapter.stat().st_mode | stat.S_IEXEC)

    contract = _valid_contract_for("good", adapter_filename="adapter.sh")
    registry = _make_registry("good", contract, tmp_path)

    result = invoke_provider(
        contract=contract,
        task="hello world",
        session_id="sess-002",
        working_dir=tmp_path,
        registry=registry,
    )
    assert result.exit_code == 0
    assert result.structured is not None
    assert result.structured["stdout"] == "hello"
    assert result.structured["structured"] == {"key": "val"}
    assert result.duration >= 0


def test_invoke_contract_non_json_response_flags_as_error(tmp_path):
    """Adapter returns plain text — should be flagged as error under contract."""
    adapter = tmp_path / "adapter.sh"
    adapter.write_text("#!/usr/bin/env bash\ncat /dev/stdin > /dev/null\necho 'plain output'\n")
    adapter.chmod(adapter.stat().st_mode | stat.S_IEXEC)

    contract = _valid_contract_for("plain", adapter_filename="adapter.sh")
    registry = _make_registry("plain", contract, tmp_path)

    result = invoke_provider(
        contract=contract,
        task="test",
        session_id="sess-003",
        working_dir=tmp_path,
        registry=registry,
    )
    assert result.exit_code == 1
    assert result.structured is None
    assert "not valid json" in result.stderr.lower()


def test_invoke_contract_schema_violation_is_error(tmp_path):
    """Adapter returns valid JSON but not conforming to response schema."""
    adapter = tmp_path / "adapter.sh"
    # Missing required fields like exitCode, stdout, stderr
    bad_response = {"protocol": "weave.response.v1", "random": "data"}
    adapter.write_text(
        "#!/usr/bin/env bash\n"
        "cat /dev/stdin > /dev/null\n"
        f"echo '{json.dumps(bad_response)}'\n"
    )
    adapter.chmod(adapter.stat().st_mode | stat.S_IEXEC)

    contract = _valid_contract_for("bad-schema", adapter_filename="adapter.sh")
    registry = _make_registry("bad-schema", contract, tmp_path)

    result = invoke_provider(
        contract=contract,
        task="test",
        session_id="sess-004",
        working_dir=tmp_path,
        registry=registry,
    )
    assert result.exit_code == 1
    assert result.structured is None
    assert "weave.response.v1" in result.stderr


def test_invoke_contract_request_includes_protocol_and_session_id(tmp_path):
    """Verify the request payload sent to the adapter includes protocol + session_id."""
    adapter = tmp_path / "adapter.sh"
    # Adapter echoes back the input as structured response
    adapter.write_text(
        '#!/usr/bin/env bash\n'
        'input=$(cat)\n'
        'protocol=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)[\'protocol\'])")\n'
        'sid=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)[\'session_id\'])")\n'
        'echo "{\\"protocol\\": \\"weave.response.v1\\", \\"exitCode\\": 0, '
        '\\"stdout\\": \\"$protocol\\", \\"stderr\\": \\"$sid\\"}"\n'
    )
    adapter.chmod(adapter.stat().st_mode | stat.S_IEXEC)

    contract = _valid_contract_for("echo-req", adapter_filename="adapter.sh")
    registry = _make_registry("echo-req", contract, tmp_path)

    result = invoke_provider(
        contract=contract,
        task="proto-check",
        session_id="sess-proto-42",
        working_dir=tmp_path,
        registry=registry,
    )
    assert result.exit_code == 0
    assert result.structured is not None
    assert result.structured["stdout"] == "weave.request.v1"
    assert result.structured["stderr"] == "sess-proto-42"


def test_invoke_contract_python_runtime_spawns_python3(tmp_path, monkeypatch):
    """Python runtime should use ['python3', path] as argv."""
    captured_calls = []

    def fake_run(argv, **kwargs):
        captured_calls.append(list(argv))

        class FakeProc:
            returncode = 0
            stdout = json.dumps({
                "protocol": "weave.response.v1",
                "exitCode": 0,
                "stdout": "",
                "stderr": "",
            })
            stderr = ""

        return FakeProc()

    monkeypatch.setattr("weave.core.invoker.subprocess.run", fake_run)

    adapter = tmp_path / "adapter.py"
    adapter.write_text("# placeholder")

    contract = ProviderContract(
        name="py-prov",
        display_name="py-prov",
        adapter="adapter.py",
        adapter_runtime=AdapterRuntime.PYTHON,
        capability_ceiling=RiskClass.WORKSPACE_WRITE,
        protocol=ProviderProtocol(
            request_schema="weave.request.v1",
            response_schema="weave.response.v1",
        ),
    )
    registry = _make_registry("py-prov", contract, tmp_path)

    invoke_provider(
        contract=contract,
        task="test",
        session_id="sess-py",
        working_dir=tmp_path,
        registry=registry,
    )
    assert captured_calls[0][0] == "python3"
    assert captured_calls[0][1].endswith("adapter.py")


def test_invoke_contract_binary_runtime_spawns_direct(tmp_path, monkeypatch):
    """Binary runtime should use [path] as argv — no interpreter prefix."""
    captured_calls = []

    def fake_run(argv, **kwargs):
        captured_calls.append(list(argv))

        class FakeProc:
            returncode = 0
            stdout = json.dumps({
                "protocol": "weave.response.v1",
                "exitCode": 0,
                "stdout": "",
                "stderr": "",
            })
            stderr = ""

        return FakeProc()

    monkeypatch.setattr("weave.core.invoker.subprocess.run", fake_run)

    adapter = tmp_path / "adapter-bin"
    adapter.write_text("# placeholder")
    adapter.chmod(adapter.stat().st_mode | stat.S_IEXEC)

    contract = ProviderContract(
        name="bin-prov",
        display_name="bin-prov",
        adapter="adapter-bin",
        adapter_runtime=AdapterRuntime.BINARY,
        capability_ceiling=RiskClass.WORKSPACE_WRITE,
        protocol=ProviderProtocol(
            request_schema="weave.request.v1",
            response_schema="weave.response.v1",
        ),
    )
    registry = _make_registry("bin-prov", contract, tmp_path)

    invoke_provider(
        contract=contract,
        task="test",
        session_id="sess-bin",
        working_dir=tmp_path,
        registry=registry,
    )
    assert len(captured_calls[0]) == 1
    assert captured_calls[0][0].endswith("adapter-bin")
