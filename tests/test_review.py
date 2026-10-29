import io
import json
import urllib.error

from agent_review import review

BUNDLE = {"head": "h" * 40, "agents": ["reviewer", "privacy", "safety"], "redacted": True,
          "title": "T", "body": "B", "log": "c1 msg", "diff": "--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+x = 1\n",
          "files": {"a.py": "x = 1\n"}, "local_findings": [{"file": "a.py", "line": 1, "category": "GitHub token"}],
          "config": {"api": "openai", "base_url": "https://api.example.com/v1", "model": "m", "instructions": "",
                     "max_context_chars": 150000, "blocking": False}}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(*replies):
    calls = []

    def o(req, timeout=0):
        calls.append((req.full_url, dict(req.headers), json.loads(req.data)))
        r = replies[len(calls) - 1]
        if isinstance(r, Exception):
            raise r
        return Resp(json.dumps(r).encode())
    return o, calls


OK = {"verdict": "approve", "summary": "fine", "findings": [
    {"file": "a.py", "line": 1, "severity": "blocking", "message": "x"}]}


def test_brief_has_lanes_redaction_note_and_privacy_sees_local_findings():
    system, user = review.brief("privacy", BUNDLE)
    assert "privacy agent" in system and "the reviewer agent covers it" in user
    assert "redactions, not code" in user and "a.py:1 GitHub token" in user and "    1| x = 1" in user
    _, solo = review.brief("reviewer", {**BUNDLE, "agents": ["reviewer"]})
    assert solo.count("you also cover") == 2


def test_openai_ladder_tokens_and_fail_closed():
    err = urllib.error.HTTPError("u", 501, "no", {}, None)
    o, calls = opener(err, err, {"choices": [{"message": {"content": "```json\n" + json.dumps(OK) + "\n```"}}],
                                 "usage": {"prompt_tokens": 900, "completion_tokens": 80,
                                           "prompt_tokens_details": {"cached_tokens": 100}}})
    r = review.run("reviewer", BUNDLE, "k-1234567890", o)
    assert (r["status"], r["verdict"], r["tokens"]) == ("ok", "changes", {"input": 800, "cached": 100, "output": 80})
    assert calls[0][2]["response_format"]["type"] == "json_schema" and "response_format" not in calls[2][2]
    assert calls[0][1]["Authorization"] == "Bearer k-1234567890"
    o, _ = opener({"choices": [{"message": {"content": "LGTM"}}]})
    assert review.run("reviewer", BUNDLE, "k", o)["status"] == "error"
    o, _ = opener(urllib.error.HTTPError("u", 401, "no", {}, None))
    assert "HTTP 401" in review.run("reviewer", BUNDLE, "k", o)["error"]


def test_anthropic_api_and_key_scrub():
    leaky = {**OK, "summary": "the key is k-ant-secret-0000"}
    o, calls = opener({"content": [{"type": "text", "text": json.dumps(leaky)}],
                       "usage": {"input_tokens": 50, "cache_read_input_tokens": 7, "output_tokens": 9}})
    b = {**BUNDLE, "config": {**BUNDLE["config"], "api": "anthropic", "base_url": "https://api.anthropic.com"}}
    r = review.run("safety", b, "k-ant-secret-0000", o)
    assert calls[0][0] == "https://api.anthropic.com/v1/messages" and calls[0][1]["X-api-key"] == "k-ant-secret-0000"
    assert r["tokens"] == {"input": 50, "cached": 7, "output": 9} and "k-ant-secret" not in json.dumps(r)


def test_endpoint_and_model_from_secrets_and_never_echoed(monkeypatch):
    monkeypatch.setenv(review.BASE_URL_ENV, "https://private.example/v1")
    monkeypatch.setenv(review.MODEL_ENV, "secret-model-7")
    o, calls = opener({"choices": [{"message": {"content": json.dumps({**OK, "summary": "as secret-model-7"})}}]})
    r = review.run("reviewer", {**BUNDLE, "config": {**BUNDLE["config"], "base_url": "", "model": ""}}, "k", o)
    assert calls[0][0] == "https://private.example/v1/chat/completions" and calls[0][2]["model"] == "secret-model-7"
    assert "secret-model-7" not in json.dumps(r)
