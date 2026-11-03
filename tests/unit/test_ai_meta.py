# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the Meta API provider (fake transport, no sockets)."""

import json

import pytest
from fastapi import HTTPException

from app.api.ai import _meta_provider
from app.ai.meta import API_URL, MetaProvider, MetaProviderError


def _responder(payload, seen):
    def request(url, api_key, body):
        seen["url"] = url
        seen["key"] = api_key
        seen["body"] = body
        return payload

    return request


@pytest.mark.unit
def test_request_shape_matches_responses_api():
    seen = {}
    provider = MetaProvider("k", request_fn=_responder({"output_text": '{"lights": {}, "screens": {}}'}, seen))
    assert provider.direct("cozy", "both", {"lights": {}, "screens": []}) == {
        "lights": {},
        "screens": {},
    }
    assert seen["url"] == API_URL
    assert seen["key"] == "k"
    body = seen["body"]
    assert body["model"] == "muse-spark-1.3-contributor"
    assert body["stream"] is False
    block = body["input"][0]["content"][0]
    assert block["type"] == "input_text"
    assert "cozy" in block["text"]
    assert "both" in block["text"]


@pytest.mark.unit
def test_custom_model_passes_through():
    seen = {}
    provider = MetaProvider(
        "k", model="muse-wonder-1.0", request_fn=_responder({"output_text": "{}"}, seen)
    )
    provider.direct("x", "both", {})
    assert seen["body"]["model"] == "muse-wonder-1.0"


@pytest.mark.unit
def test_parses_nested_output_blocks():
    raw = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": 'JSON: {"lights": {"a": {"intensity": 5}}, "screens": {}} tail',
                    }
                ],
            }
        ]
    }
    provider = MetaProvider("k", request_fn=lambda url, key, body: raw)
    assert provider.direct("x", "lights", {}) == {"lights": {"a": {"intensity": 5}}, "screens": {}}


@pytest.mark.unit
def test_ignores_input_echo_blocks():
    raw = {
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "input_text", "text": "Request: hi"},
                    {"type": "output_text", "text": '{"lights": {}, "screens": {}}'},
                ],
            }
        ]
    }
    provider = MetaProvider("k", request_fn=lambda url, key, body: raw)
    assert provider.direct("hi", "both", {}) == {"lights": {}, "screens": {}}


@pytest.mark.unit
def test_ai_look_maps_provider_outage_to_502(monkeypatch):
    """A dead provider fails the request honestly; nothing moves, nothing 500s."""
    from fastapi.testclient import TestClient

    from app.api import ai as ai_api
    from app.main import app

    class DownProvider:
        def name(self):
            return "meta"

        def direct(self, prompt, targets, manifest):
            raise MetaProviderError("meta api unreachable: down")

    monkeypatch.setattr(ai_api, "_meta_provider", lambda root: DownProvider())
    response = TestClient(app).post(
        "/api/studios/example-studio/ai/look",
        json={"prompt": "cozy", "targets": "lights", "provider": "meta"},
    )
    assert response.status_code == 502
    assert "unreachable" in response.json()["detail"]


@pytest.mark.unit
def test_transport_and_shape_failures_report():
    def boom(url, key, body):
        raise RuntimeError("down")

    with pytest.raises(MetaProviderError, match="request failed"):
        MetaProvider("k", request_fn=boom).direct("x", "both", {})
    with pytest.raises(MetaProviderError, match="no text"):
        MetaProvider("k", request_fn=lambda u, k, b: {"id": "r1"}).direct("x", "both", {})
    with pytest.raises(MetaProviderError, match="no JSON"):
        MetaProvider("k", request_fn=lambda u, k, b: {"output_text": "just prose"}).direct("x", "both", {})
    with pytest.raises(MetaProviderError, match="bad JSON"):
        MetaProvider("k", request_fn=lambda u, k, b: {"output_text": '{"lights": {"a": }}'}).direct(
            "x", "both", {}
        )


@pytest.mark.unit
def test_meta_key_loader(tmp_path):
    assert _meta_provider(tmp_path) is None
    local = tmp_path / "studios.local"
    local.mkdir()
    (local / "meta.json").write_text(json.dumps({"api_key": "k"}), encoding="utf-8")
    assert _meta_provider(tmp_path).model == "muse-spark-1.3-contributor"
    (local / "meta.json").write_text(
        json.dumps({"api_key": "k", "model": "muse-wonder-1.0"}), encoding="utf-8"
    )
    assert _meta_provider(tmp_path).model == "muse-wonder-1.0"
    (local / "meta.json").write_text(json.dumps({"model": "muse-wonder-1.0"}), encoding="utf-8")
    assert _meta_provider(tmp_path) is None
    (local / "meta.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(HTTPException, match="malformed"):
        _meta_provider(tmp_path)
