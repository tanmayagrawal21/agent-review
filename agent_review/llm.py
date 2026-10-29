"""One engine: a chat call to any provider. `api: openai` speaks the OpenAI-compatible chat-completions format
(OpenAI, Gemini, OpenRouter, Mistral, Groq, Together, Ollama, vLLM, LM Studio, ...); `api: anthropic` speaks
Anthropic's Messages API. The model gets no tools; everything it needs is in the prompt. Redirects are refused
so the key is never resent elsewhere. Any failure is returned as an error, never as an answer.
"""

from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

KEY_ENV = "AGENT_REVIEW_API_KEY"
UNSUPPORTED = {400, 404, 415, 422, 501}  # how providers say "not this request format"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_open = urllib.request.build_opener(_NoRedirect).open


def extract(content: str) -> dict | None:
    """A JSON object from a reply, bare or wrapped in prose or a code fence."""
    try:
        d = json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)
        try:
            d = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            return None
    return d if isinstance(d, dict) else None


def _post(url: str, body: dict, headers: dict, opener, timeout: int) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "agent-review", **headers})
    with opener(req, timeout=timeout) as resp:
        d = json.loads(resp.read())
    if not isinstance(d, dict):
        raise ValueError("response is not an object")
    return d


def tokens(usage: dict) -> dict:
    """{input, cached, output} from either API's usage block."""
    usage = usage if isinstance(usage, dict) else {}
    if "prompt_tokens" in usage:
        cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        return {"input": int(usage.get("prompt_tokens") or 0) - cached, "cached": cached,
                "output": int(usage.get("completion_tokens") or 0)}
    return {"input": int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0),
            "cached": int(usage.get("cache_read_input_tokens") or 0), "output": int(usage.get("output_tokens") or 0)}


def call(cfg: dict, key: str, system: str, user: str, schema: dict, opener=_open, timeout: int = 600) -> dict:
    """Returns {"structured": dict | None, "tokens": {...}, "error": str | None}."""
    asked = user + f"\n\nReply with only a JSON object matching this JSON Schema:\n{json.dumps(schema)}"
    base = cfg["base_url"].rstrip("/")
    host = urlparse(base).hostname or ""
    api = "anthropic" if host == "anthropic.com" or host.endswith(".anthropic.com") else cfg["api"]
    try:
        if api == "anthropic":  # no temperature: newer models reject it
            d = _post(base.removesuffix("/v1") + "/v1/messages", {"model": cfg["model"], "max_tokens": 8192,
                                              "system": system, "messages": [{"role": "user", "content": asked}]},
                      {"x-api-key": key, "anthropic-version": "2023-06-01"}, opener, timeout)
            text = "".join(b.get("text", "") for b in d.get("content") or [] if isinstance(b, dict))
            return {"structured": extract(text), "tokens": tokens(d.get("usage")), "error": None}
        code = None
        for fmt, content in ((
                {"type": "json_schema", "json_schema": {"name": "review", "schema": schema}}, user),
                ({"type": "json_object"}, asked), (None, asked)):
            body = {"model": cfg["model"],
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
            if fmt:
                body["response_format"] = fmt
            try:
                d = _post(base + "/chat/completions", body, {"Authorization": f"Bearer {key}"}, opener, timeout)
            except urllib.error.HTTPError as e:
                code = e.code
                if e.code in UNSUPPORTED:
                    continue  # try a simpler response format
                raise
            text = str(d["choices"][0]["message"]["content"] or "")
            return {"structured": extract(text), "tokens": tokens(d.get("usage")), "error": None}
        return {"structured": None, "tokens": tokens({}), "error": f"HTTP {code} from the model endpoint"}
    except urllib.error.HTTPError as e:
        return {"structured": None, "tokens": tokens({}), "error": f"HTTP {e.code} from the model endpoint"}
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
        return {"structured": None, "tokens": tokens({}), "error": f"model endpoint unreachable ({type(e).__name__})"}
    except (KeyError, IndexError, TypeError, AttributeError):
        return {"structured": None, "tokens": tokens({}), "error": "unexpected response shape"}
