"""Tests for weave provider invoker."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from weave.core.invoker import InvokeResult, invoke_provider, _get_git_changed_files


def test_invoke_missing_adapter(tmp_path):
    result = invoke_provider(
        adapter_script=tmp_path / "nonexistent.sh",
        task="do something",
        working_dir=tmp_path,
    )
    assert result.exit_code == 1
    assert "not found" in result.stderr.lower()
    assert result.structured is None


def test_invoke_simple_adapter(tmp_path):
    """Adapter echoes the task back as JSON; verify stdout matches."""
    adapter = tmp_path / "adapter.sh"
    adapter.write_text(
        "#!/usr/bin/env bash\n"
        "input=$(cat)\n"
        'task=$(echo "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)[\'task\'])")\n'
        'echo "{\\\"task\\\": \\\"$task\\\"}"\n'
    )
    adapter.chmod(adapter.stat().st_mode | stat.S_IEXEC)

    result = invoke_provider(
        adapter_script=adapter,
        task="hello world",
        working_dir=tmp_path,
    )
    assert result.exit_code == 0
    assert result.structured is not None
    assert result.structured.get("task") == "hello world"
    assert result.duration >= 0


def test_invoke_non_json_output(tmp_path):
    """Adapter returns plain text — structured should be None, stdout preserved."""
    adapter = tmp_path / "plain.sh"
    adapter.write_text("#!/usr/bin/env bash\ncat /dev/stdin > /dev/null\necho 'plain output'\n")
    adapter.chmod(adapter.stat().st_mode | stat.S_IEXEC)

    result = invoke_provider(
        adapter_script=adapter,
        task="test",
        working_dir=tmp_path,
    )
    assert result.exit_code == 0
    assert result.structured is None
    assert "plain output" in result.stdout


def test_invoke_timeout(tmp_path):
    """Adapter that sleeps past timeout returns exit_code=124."""
    adapter = tmp_path / "slow.sh"
    adapter.write_text("#!/usr/bin/env bash\nsleep 60\n")
    adapter.chmod(adapter.stat().st_mode | stat.S_IEXEC)

    result = invoke_provider(
        adapter_script=adapter,
        task="slow task",
        working_dir=tmp_path,
        timeout=1,
    )
    assert result.exit_code == 124
    assert "timed out" in result.stderr.lower()


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
