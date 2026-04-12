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
