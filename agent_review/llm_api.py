"""Provider-neutral engine: any OpenAI-compatible chat-completions endpoint (OpenAI, Gemini, OpenRouter, Azure
via a gateway, Groq, Together, Mistral, Ollama, vLLM, LM Studio, Anthropic's compatibility endpoint, ...).

The model gets no tools at all: the diff and the head version of each changed file are put in the prompt, so
there is nothing to sandbox. Structured output is requested with a JSON schema; providers that reject that get
the schema in the prompt instead, and anything unparseable is an error, never an approval.
"""

from __future__ import annotations

import http.client
import json
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

KEY_ENV = "AGENT_REVIEW_API_KEY"


def file_context(repo: Path, head: str, files: list[str], budget: int) -> str:
    """Head versions of the changed files, numbered, until the character budget runs out."""
    parts, used = [], 0
    for f in files:
        r = subprocess.run(["git", "-C", str(repo), "show", f"{head}:{f}"], capture_output=True)
        if r.returncode or b"\0" in r.stdout[:8000]:
            continue  # deleted or binary
        text = r.stdout.decode("utf-8", "replace")
        numbered = "\n".join(f"{i:5}| {ln}" for i, ln in enumerate(text.splitlines(), 1))
        block = f'<file path="{f}">\n{numbered}\n</file>\n'
        if used + len(block) > budget:
            parts.append(f'<file path="{f}" omitted="context budget reached"/>\n')
            continue
        parts.append(block)
        used += len(block)
    return "".join(parts)


def _extract(content: str) -> dict | None:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)  # a fenced or prefixed JSON object
        try:
            return json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # never resend the key to another URL
        return None


_open = urllib.request.build_opener(_NoRedirect).open
UNSUPPORTED = {400, 404, 415, 422, 501}  # how providers say "not this request format"


def call(base_url: str, model: str, key: str, system: str, user: str, schema: dict, timeout: int = 600,
         opener=_open) -> dict:
    """Returns {"structured": dict|None, "usage": {...}, "error": str|None}. Tries a JSON-schema response, then
    plain JSON mode, then no format option, with the schema in the prompt for the last two."""
    url = base_url.rstrip("/") + "/chat/completions"
    asked = user + f"\n\nReply with only a JSON object matching this JSON Schema:\n{json.dumps(schema)}"
    ladder = [({"type": "json_schema", "json_schema": {"name": "review", "schema": schema}}, user),
              ({"type": "json_object"}, asked), (None, asked)]
    code = None
    for fmt, content in ladder:
        body = {"model": model, "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
        if fmt:
            body["response_format"] = fmt
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": "agent-review"})
        try:
            with opener(req, timeout=timeout) as resp:
                d = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            code = e.code
            if e.code in UNSUPPORTED:
                continue
            return {"structured": None, "usage": {}, "error": f"HTTP {e.code} from the model endpoint"}
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
            return {"structured": None, "usage": {}, "error": f"model endpoint unreachable ({type(e).__name__})"}
        try:
            text = d["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError, AttributeError):
            return {"structured": None, "usage": {}, "error": "no message in the response"}
        usage = d.get("usage") if isinstance(d.get("usage"), dict) else {}
        return {"structured": _extract(str(text)), "usage": usage, "error": None}
    return {"structured": None, "usage": {}, "error": f"HTTP {code} from the model endpoint"}
