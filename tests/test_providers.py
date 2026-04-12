"""Tests for weave.core.providers — provider detection."""
import pytest

from weave.core.providers import ProviderInfo, check_provider_health, detect_providers


def test_detect_providers_returns_list():
    result = detect_providers()
    assert isinstance(result, list)
    # Registry has at least the built-in providers
    assert len(result) >= 4
    names = [p.name for p in result]
    assert "claude-code" in names


def test_detect_providers_all_have_required_fields():
    result = detect_providers()
    for provider in result:
        assert isinstance(provider, ProviderInfo)
        assert provider.name
        assert provider.command
        assert provider.health_check
        assert provider.adapter_script.endswith(f"{provider.name}.sh")
        assert isinstance(provider.installed, bool)


def test_health_check_nonexistent():
    assert check_provider_health("which __weave_fake_binary_xyz__") is False


def test_health_check_valid_command():
    # 'which which' should succeed on any POSIX system
    assert check_provider_health("which which") is True


def test_providers_list_cli_exists():
    from click.testing import CliRunner
    from weave.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["providers", "list", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output.lower()


def test_providers_list_cli_shows_builtins(tmp_path):
    from click.testing import CliRunner
    from weave.cli import main
    import os

    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "config.json").write_text(
        '{"version": "1", "phase": "sandbox", "default_provider": "claude-code", '
        '"providers": {"claude-code": {"command": "claude"}}}'
    )

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.chdir(tmp_path)
        result = runner.invoke(main, ["providers", "list"])

    assert result.exit_code == 0
    assert "claude-code" in result.output
    assert "opencode" in result.output
    assert "vllm" in result.output
    assert "workspace-write" in result.output
    assert "read-only" in result.output


def test_providers_list_json_flag(tmp_path):
    from click.testing import CliRunner
    from weave.cli import main
    import os, json

    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "config.json").write_text(
        '{"version": "1", "phase": "sandbox", "default_provider": "claude-code", '
        '"providers": {"claude-code": {"command": "claude"}}}'
    )

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        os.chdir(tmp_path)
        result = runner.invoke(main, ["providers", "list", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) >= 6
    names = {p["name"] for p in data}
    assert "claude-code" in names
    assert "opencode" in names
