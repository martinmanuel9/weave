"""Tests for the weave adapter wire protocol v1 schemas."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from weave.schemas.protocol import (
    PROTOCOL_VERSIONS,
    AdapterRequestV1,
    AdapterResponseV1,
)


def test_adapter_request_v1_defaults_protocol_literal():
    req = AdapterRequestV1(
        session_id="sess_abc",
        task="hello",
        workingDir="/tmp/proj",
    )
    assert req.protocol == "weave.request.v1"
    assert req.context == ""
    assert req.timeout == 300


def test_adapter_request_v1_round_trips_via_json():
    req = AdapterRequestV1(
        session_id="sess_abc",
        task="hello",
        workingDir="/tmp/proj",
        context="ctx",
        timeout=60,
    )
    blob = req.model_dump_json()
    parsed = json.loads(blob)
    assert parsed["protocol"] == "weave.request.v1"
    assert parsed["session_id"] == "sess_abc"
    assert parsed["task"] == "hello"
    assert parsed["workingDir"] == "/tmp/proj"
    assert parsed["context"] == "ctx"
    assert parsed["timeout"] == 60
    again = AdapterRequestV1.model_validate(parsed)
    assert again == req


def test_adapter_response_v1_accepts_well_formed_dict():
    resp = AdapterResponseV1.model_validate({
        "protocol": "weave.response.v1",
        "exitCode": 0,
        "stdout": "ok",
        "stderr": "",
        "structured": {"key": "val"},
    })
    assert resp.exitCode == 0
    assert resp.structured == {"key": "val"}


def test_adapter_response_v1_rejects_missing_exit_code():
    with pytest.raises(ValidationError):
        AdapterResponseV1.model_validate({
            "protocol": "weave.response.v1",
            "stdout": "",
            "stderr": "",
        })


def test_adapter_response_v1_rejects_wrong_protocol_literal():
    with pytest.raises(ValidationError):
        AdapterResponseV1.model_validate({
            "protocol": "weave.response.v0",
            "exitCode": 0,
            "stdout": "",
            "stderr": "",
        })


def test_adapter_response_v1_allows_structured_none_and_empty():
    none_resp = AdapterResponseV1.model_validate({
        "protocol": "weave.response.v1",
        "exitCode": 0,
        "stdout": "",
        "stderr": "",
        "structured": None,
    })
    empty_resp = AdapterResponseV1.model_validate({
        "protocol": "weave.response.v1",
        "exitCode": 0,
        "stdout": "",
        "stderr": "",
        "structured": {},
    })
    assert none_resp.structured is None
    assert empty_resp.structured == {}


def test_protocol_versions_registry_contains_v1_entries():
    assert "weave.request.v1" in PROTOCOL_VERSIONS
    assert "weave.response.v1" in PROTOCOL_VERSIONS
    assert PROTOCOL_VERSIONS["weave.request.v1"] is AdapterRequestV1
    assert PROTOCOL_VERSIONS["weave.response.v1"] is AdapterResponseV1
