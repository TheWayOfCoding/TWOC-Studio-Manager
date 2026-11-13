# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenAI-compatible provider: local llama-server or any free chat endpoint.

Config lives hub-local in studios.local/orchestrator.json (git-ignored,
like meta.json): {"local": {"base_url": ..., "model": ...},
"free": {"base_url": ..., "model": ..., "api_key": ...}}.
Stdlib HTTP only, so the hub needs no new dependency. The request follows
the /chat/completions shape (messages in, choices[0] text out); parsing
stays tolerant so minor API drift degrades to an honest error, not a crash.
"""

import json
import urllib.error
import urllib.request

from .base import Provider
from .director import PROMPT_TEMPLATE

TIMEOUT_S = 120


class OpenAICompatError(Exception):
    """Transport failure or unparseable provider output (never carries the key)."""


def _headers(api_key):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _post_json(url, api_key, payload, timeout=TIMEOUT_S):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers(api_key),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        raise OpenAICompatError(f"provider refused ({exc.code}): {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise OpenAICompatError(f"provider unreachable: {exc.reason}") from exc
    except ValueError as exc:
        raise OpenAICompatError(f"provider returned bad JSON: {exc}") from exc


def _chat_text(node):
    """First assistant text in a chat-completions payload (tolerant of shape)."""
    if isinstance(node, dict):
        choices = node.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message") or {}
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content
                text = first.get("text")
                if isinstance(text, str) and text.strip():
                    return text
    return None


def _directives(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise OpenAICompatError("provider returned no JSON object")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise OpenAICompatError(f"provider returned bad JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise OpenAICompatError("provider directives must be an object")
    return {"lights": data.get("lights") or {}, "screens": data.get("screens") or {}}


class OpenAICompatProvider(Provider):
    """Directs via POST {base_url}/chat/completions (key optional, never logged)."""

    def __init__(self, name, base_url, model, api_key=None, timeout=TIMEOUT_S, request_fn=None):
        self._name = name
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout or TIMEOUT_S
        self._request = request_fn or self._post

    def _post(self, url, api_key, payload):
        """Real transport with the configured timeout (fakes keep the 3-arg shape)."""
        return _post_json(url, api_key, payload, self.timeout)

    def name(self):
        return self._name

    def direct(self, prompt, targets, manifest):
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": PROMPT_TEMPLATE
                    % {
                        "targets": targets,
                        "manifest": json.dumps(manifest),
                        "prompt": prompt or "",
                    },
                }
            ],
            "stream": False,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        try:
            raw = self._request(f"{self.base_url}/chat/completions", self.api_key, payload)
        except OpenAICompatError:
            raise
        except Exception as exc:  # noqa: BLE001 - transports fail many ways; all report the same
            raise OpenAICompatError(f"{self._name} request failed: {exc}") from exc
        text = _chat_text(raw)
        if not text:
            raise OpenAICompatError(f"{self._name} returned no text")
        try:
            return _directives(text)
        except OpenAICompatError as exc:
            raise OpenAICompatError(f"{self._name}: {exc}") from exc
