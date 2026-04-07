"""Tests for src/weave/integrations/detection.py"""
import pytest

from weave.integrations.detection import detect_integrations, IntegrationStatus


def _status_by_name(statuses: list[IntegrationStatus], name: str) -> IntegrationStatus:
    for s in statuses:
        if s.name == name:
            return s
    raise KeyError(f"Integration '{name}' not found in results")


# ---------------------------------------------------------------------------
# test_detect_with_no_env
# ---------------------------------------------------------------------------

def test_detect_with_no_env(monkeypatch):
    """With an empty environment and no CLIs, all integrations should be unavailable."""
    # Ensure notebooklm CLI is not detected even if installed on the machine
    monkeypatch.setattr("weave.integrations.detection.shutil.which", lambda _: None)
    results = detect_integrations(env={})

    for integration in results:
        assert not integration.available, (
            f"{integration.name} should be unavailable with no env vars / CLIs"
        )


# ---------------------------------------------------------------------------
# test_detect_with_open_brain
# ---------------------------------------------------------------------------

def test_detect_with_open_brain():
    env = {
        "OPEN_BRAIN_URL": "http://localhost:54321",
        "OPEN_BRAIN_KEY": "super-secret-key",
    }
    results = detect_integrations(env=env)
    ob = _status_by_name(results, "open-brain")

    assert ob.available is True
    assert ob.type == "memory"
    assert ob.config.get("url") == "http://localhost:54321"


# ---------------------------------------------------------------------------
# test_detect_with_linear
# ---------------------------------------------------------------------------

def test_detect_with_linear():
    env = {"LINEAR_API_KEY": "lin_api_abc123"}
    results = detect_integrations(env=env)
    linear = _status_by_name(results, "linear")

    assert linear.available is True
    assert linear.type == "tracking"


# ---------------------------------------------------------------------------
# Extra: partial open-brain config (only one var set)
# ---------------------------------------------------------------------------

def test_detect_open_brain_partial_env():
    """Only OPEN_BRAIN_URL set — should still be unavailable."""
    env = {"OPEN_BRAIN_URL": "http://localhost:54321"}
    results = detect_integrations(env=env)
    ob = _status_by_name(results, "open-brain")
    assert ob.available is False


def test_detect_21st_dev_alternate_key():
    """21ST_DEV_KEY as the alternate env var name."""
    env = {"21ST_DEV_KEY": "some-key"}
    results = detect_integrations(env=env)
    tfd = _status_by_name(results, "21st-dev")
    assert tfd.available is True
    assert tfd.type == "ui"
