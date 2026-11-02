# SPDX-License-Identifier: AGPL-3.0-or-later
"""Meta API provider: prompted looks via the dev.meta.ai responses endpoint.

Key + model live hub-local in studios.local/meta.json (git-ignored, like
obs.json): {"api_key": "...", "model": "muse-spark-1.3-contributor"}.
Stdlib HTTP only, so the hub needs no new dependency. The responses payload
follows the OpenAI shape (input blocks in, output_text out); parsing stays
tolerant so minor API drift degrades to an honest error, not a crash.
"""

import json
import urllib.error
import urllib.request

from .base import Provider

API_URL = "https://api.meta.ai/v1/responses"
DEFAULT_MODEL = "muse-spark-1.3-contributor"
TIMEOUT_S = 30

PROMPT_TEMPLATE = (
    "You are the Studio Hub lighting director. Reply with ONE JSON object only, no prose: "
    '{"lights": {"<device-id>": {"intensity": 0..100, "cct": Kelvin, "hue": 0..360, '
    '"saturation": 0..100}}, "screens": {"<screen-id>": {"theme": str, "text": str}}}. '
    "Apply to these targets only: %(targets)s. "
    "Use only these device ids and ranges (stay inside aiCap): %(manifest)s. "
    "Each light lists usual, its normal everyday look: treat it as the baseline "
    "(moody or cozy asks go below it, neutral asks stay near it). "
    "usual may carry both a white baseline (cct) and a color baseline "
    "(hue/saturation): use the keys matching the requested look. "
    "intensity 0 means dark and is allowed when the request wants a light off "
    '(GVM panels also accept "power": "off"; DMX lights use 0). '
    "Request: %(prompt)s"
)


class MetaProviderError(Exception):
    """Transport failure or unparseable provider output (never carries the key)."""


def _post_json(url, api_key, payload, timeout=TIMEOUT_S):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
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
        raise MetaProviderError(f"meta api refused ({exc.code}): {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise MetaProviderError(f"meta api unreachable: {exc.reason}") from exc
    except ValueError as exc:
        raise MetaProviderError(f"meta api returned bad JSON: {exc}") from exc


def _output_text(node):
    """First assistant text in a responses-style payload (tolerant of shape)."""
    if isinstance(node, dict):
        direct = node.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct
        if node.get("type") == "output_text" and isinstance(node.get("text"), str):
            if node["text"].strip():
                return node["text"]
        for key in ("output", "data", "content", "message"):
            if key in node:
                found = _output_text(node[key])
                if found:
                    return found
        for key, value in node.items():
            if key in ("output", "data", "content", "message", "output_text", "text", "type"):
                continue
            found = _output_text(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _output_text(value)
            if found:
                return found
    return None


def _directives(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise MetaProviderError("meta api returned no JSON object")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise MetaProviderError(f"meta api returned bad JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MetaProviderError("meta api directives must be an object")
    return {"lights": data.get("lights") or {}, "screens": data.get("screens") or {}}


class MetaProvider(Provider):
    """Directs via POST {API_URL} with a caller-supplied key (never logged)."""

    def __init__(self, api_key, model=DEFAULT_MODEL, request_fn=None):
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self._request = request_fn or _post_json

    def name(self):
        return "meta"

    def direct(self, prompt, targets, manifest):
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": PROMPT_TEMPLATE
                            % {
                                "targets": targets,
                                "manifest": json.dumps(manifest),
                                "prompt": prompt or "",
                            },
                        }
                    ],
                }
            ],
            "stream": False,
        }
        try:
            raw = self._request(API_URL, self.api_key, payload)
        except MetaProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - transports fail many ways; all report the same
            raise MetaProviderError(f"meta api request failed: {exc}") from exc
        text = _output_text(raw)
        if not text:
            raise MetaProviderError("meta api returned no text")
        return _directives(text)
