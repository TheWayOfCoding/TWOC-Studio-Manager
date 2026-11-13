# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the OpenAI-compatible provider (fake transport, no sockets)."""

import json

import pytest
from fastapi import HTTPException

from app.api.orchestrator import _openai_provider
from app.orchestrator.openai_compat import OpenAICompatError, OpenAICompatProvider, _headers


def _responder(payload, seen):
    def request(url, api_key, body):
        seen["url"] = url
        seen["key"] = api_key
        seen["body"] = body
        return payload

    return request


def _chat(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


@pytest.mark.unit
def test_request_shape_matches_chat_completions():
    seen = {}
    provider = OpenAICompatProvider(
        "local",
        "http://127.0.0.1:8081/v1",
        "gemma-4-E4B-it-IQ4_NL",
        request_fn=_responder(_chat('{"lights": {}, "screens": {}}'), seen),
    )
    assert provider.direct("cozy", "both", {"lights": {}, "screens": []}) == {
        "lights": {},
        "screens": {},
    }
    assert seen["url"] == "http://127.0.0.1:8081/v1/chat/completions"
    assert seen["key"] is None
    body = seen["body"]
    assert body["model"] == "gemma-4-E4B-it-IQ4_NL"
    assert body["stream"] is False
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    message = body["messages"][0]
    assert message["role"] == "user"
    assert "cozy" in message["content"]
    assert "both" in message["content"]
    assert "usual" in message["content"]


@pytest.mark.unit
def test_base_url_trailing_slash_and_key_pass_through():
    seen = {}
    provider = OpenAICompatProvider(
        "free",
        "https://example.invalid/v1/",
        "free-model",
        api_key="k",
        request_fn=_responder(_chat("{}"), seen),
    )
    provider.direct("x", "both", {})
    assert seen["url"] == "https://example.invalid/v1/chat/completions"
    assert seen["key"] == "k"
    assert seen["body"]["model"] == "free-model"


@pytest.mark.unit
def test_auth_header_only_with_key():
    assert "Authorization" not in _headers(None)
    assert "Authorization" not in _headers("")
    assert _headers("k")["Authorization"] == "Bearer k"
    assert _headers("k")["Content-Type"] == "application/json"


@pytest.mark.unit
def test_parses_chat_and_legacy_text_shapes():
    provider = OpenAICompatProvider(
        "local",
        "http://127.0.0.1:8081/v1",
        "m",
        request_fn=lambda url, key, body: _chat('JSON: {"lights": {"a": {"intensity": 5}}} tail'),
    )
    assert provider.direct("x", "lights", {}) == {"lights": {"a": {"intensity": 5}}, "screens": {}}
    legacy = OpenAICompatProvider(
        "local",
        "http://127.0.0.1:8081/v1",
        "m",
        request_fn=lambda url, key, body: {"choices": [{"text": '{"lights": {}, "screens": {}}'}]},
    )
    assert legacy.direct("x", "both", {}) == {"lights": {}, "screens": {}}


@pytest.mark.unit
def test_orchestrator_direct_maps_provider_outage_to_502(monkeypatch):
    """A dead local/free endpoint fails honestly; nothing moves, nothing 500s."""
    from fastapi.testclient import TestClient

    from app.api import orchestrator as orch_api
    from app.main import app

    class DownProvider:
        def name(self):
            return "local"

        def direct(self, prompt, targets, manifest):
            raise OpenAICompatError("local provider unreachable: down")

    monkeypatch.setattr(orch_api, "_openai_provider", lambda root, pid: DownProvider())
    response = TestClient(app).post(
        "/api/studios/example-studio/orchestrator/direct",
        json={"prompt": "cozy", "targets": "lights", "provider": "local"},
    )
    assert response.status_code == 502
    assert "unreachable" in response.json()["detail"]


@pytest.mark.unit
def test_unconfigured_provider_is_503(monkeypatch):
    """Missing orchestrator.json section answers 503, naming the file to fix."""
    from fastapi.testclient import TestClient

    from app.api import orchestrator as orch_api
    from app.main import app

    monkeypatch.setattr(orch_api, "_openai_provider", lambda root, pid: None)
    for provider in ("local", "free"):
        response = TestClient(app).post(
            "/api/studios/example-studio/orchestrator/direct",
            json={"prompt": "cozy", "targets": "lights", "provider": provider},
        )
        assert response.status_code == 503
        assert "orchestrator.json" in response.json()["detail"]


@pytest.mark.unit
def test_transport_and_shape_failures_report():
    def boom(url, key, body):
        raise RuntimeError("down")

    with pytest.raises(OpenAICompatError, match="request failed"):
        OpenAICompatProvider("local", "http://127.0.0.1:8081/v1", "m", request_fn=boom).direct(
            "x", "both", {}
        )
    with pytest.raises(OpenAICompatError, match="no text"):
        OpenAICompatProvider(
            "local", "http://127.0.0.1:8081/v1", "m", request_fn=lambda u, k, b: {"id": "r1"}
        ).direct("x", "both", {})
    with pytest.raises(OpenAICompatError, match="no JSON"):
        OpenAICompatProvider(
            "local", "http://127.0.0.1:8081/v1", "m", request_fn=lambda u, k, b: _chat("just prose")
        ).direct("x", "both", {})
    with pytest.raises(OpenAICompatError, match="bad JSON"):
        OpenAICompatProvider(
            "local",
            "http://127.0.0.1:8081/v1",
            "m",
            request_fn=lambda u, k, b: _chat('{"lights": {"a": }}'),
        ).direct("x", "both", {})


@pytest.mark.unit
def test_config_loader(tmp_path):
    assert _openai_provider(tmp_path, "local") is None
    local = tmp_path / "studios.local"
    local.mkdir()
    (local / "orchestrator.json").write_text(
        json.dumps({"local": {"base_url": "http://127.0.0.1:8081/v1/", "model": "m"}}),
        encoding="utf-8",
    )
    provider = _openai_provider(tmp_path, "local")
    assert provider.name() == "local"
    assert provider.base_url == "http://127.0.0.1:8081/v1"
    assert provider.api_key is None
    assert provider.timeout == 120
    assert _openai_provider(tmp_path, "free") is None
    (local / "orchestrator.json").write_text(
        json.dumps(
            {
                "local": {"base_url": "http://127.0.0.1:8081/v1", "model": "m"},
                "free": {
                    "base_url": "https://example.invalid/v1",
                    "model": "f",
                    "api_key": "k",
                    "timeout_s": 45,
                },
            }
        ),
        encoding="utf-8",
    )
    free = _openai_provider(tmp_path, "free")
    assert free.name() == "free"
    assert free.api_key == "k"
    assert free.timeout == 45
    (local / "orchestrator.json").write_text(
        json.dumps({"free": {"base_url": "https://example.invalid/v1", "model": "f"}}),
        encoding="utf-8",
    )
    assert _openai_provider(tmp_path, "free") is None
    (local / "orchestrator.json").write_text(json.dumps({"local": {"model": "m"}}), encoding="utf-8")
    assert _openai_provider(tmp_path, "local") is None
    (local / "orchestrator.json").write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    assert _openai_provider(tmp_path, "local") is None
    (local / "orchestrator.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(HTTPException, match="malformed"):
        _openai_provider(tmp_path, "local")
    (local / "orchestrator.json").write_text(
        json.dumps({"local": {"base_url": "http://127.0.0.1:8081/v1", "model": "m", "timeout_s": "soon"}}),
        encoding="utf-8",
    )
    with pytest.raises(HTTPException, match="timeout_s"):
        _openai_provider(tmp_path, "local")
    (local / "orchestrator.json").write_text(
        json.dumps({"local": {"base_url": "http://127.0.0.1:8081/v1", "model": "m", "timeout_s": -5}}),
        encoding="utf-8",
    )
    with pytest.raises(HTTPException, match="timeout_s"):
        _openai_provider(tmp_path, "local")


@pytest.mark.unit
def test_dashboard_lists_new_providers():
    from fastapi.testclient import TestClient

    from app.main import app

    html = TestClient(app).get("/").text
    assert '<option value="local" selected>local</option>' in html
    assert '<option value="free">free</option>' in html
    assert '<option value="meta">meta</option>' in html
