import io
import json
import types
import urllib.error

import pytest

from agent_review import local_pii
from agent_review.config import ConfigError, load_text
from agent_review.run_agent import engine_for, merge, run_api, scrub


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_returning(*replies):
    calls = []

    def opener(req, timeout=0):
        calls.append(json.loads(req.data))
        r = replies[len(calls) - 1]
        if isinstance(r, Exception):
            raise r
        return FakeResp(json.dumps(r).encode())
    return opener, calls


def _reply(obj):
    return {"choices": [{"message": {"content": json.dumps(obj)}}], "usage": {"prompt_tokens": 900,
                                                                              "completion_tokens": 80}}


def test_api_engine_sends_diff_and_files_and_parses_the_verdict(repo, monkeypatch):
    r, base, head = repo
    monkeypatch.setenv("AGENT_REVIEW_API_KEY", "k-test-123")
    cfg = load_text("engine: openai-compatible\nmodel: some-model\nbase_url: https://llm.invalid/v1\n")
    opener, calls = opener_returning(_reply({"verdict": "approve", "summary": "ok", "findings": [
        {"file": "app.py", "line": 2, "severity": "blocking", "message": "b"}]}))
    a = types.SimpleNamespace(agent="reviewer", repo=r, base=base, head=head)
    res = run_api(a, cfg, ["reviewer"], opener=opener)
    assert (res["status"], res["verdict"]) == ("ok", "changes")
    assert res["tokens"] == {"input": 900, "cached": 0, "output": 80} and res["engine"] == "openai-compatible"
    sent = calls[0]
    assert sent["model"] == "some-model" and sent["response_format"]["type"] == "json_schema"
    user = sent["messages"][1]["content"]
    assert "<diff>" in user and '<file path="app.py">' in user and "    2| b = 20" in user
    assert "tools" not in sent  # no tools: nothing to sandbox


def test_api_engine_falls_back_when_json_schema_is_rejected_and_fails_closed_on_garbage(repo, monkeypatch):
    r, base, head = repo
    cfg = load_text("engine: openai-compatible\nmodel: m\nbase_url: http://localhost:11434/v1\n")
    a = types.SimpleNamespace(agent="safety", repo=r, base=base, head=head)
    err = urllib.error.HTTPError("u", 501, "no schema", {}, None)
    opener, calls = opener_returning(err, err, {"choices": [{"message": {"content": "```json\n" + json.dumps(
        {"verdict": "approve", "summary": "fine", "findings": []}) + "\n```"}}]})
    assert run_api(a, cfg, None, opener=opener)["verdict"] == "approve"
    assert calls[1]["response_format"] == {"type": "json_object"} and "response_format" not in calls[2]
    assert "JSON Schema" in calls[2]["messages"][1]["content"]
    opener, _ = opener_returning({"choices": [{"message": {"content": "LGTM!"}}]})
    assert run_api(a, cfg, None, opener=opener)["status"] == "error"
    assert run_api(a, load_text("engine: openai-compatible\nbase_url: https://x.invalid/v1\n"), None,
                   opener=opener)["status"] == "error"  # no model configured


def test_engine_auto_picks_by_credential_and_config_validates():
    cfg = load_text("base_url: https://api.example.com/v1\nmodel: m\n")
    assert engine_for(cfg, {"AGENT_REVIEW_API_KEY": "x"}) == "openai-compatible"
    assert engine_for(cfg, {"ANTHROPIC_API_KEY": "x"}) == "claude-code"
    assert engine_for(load_text(""), {"AGENT_REVIEW_API_KEY": "x"}) is None  # no base_url: not usable
    assert engine_for(cfg, {}) is None
    with pytest.raises(ConfigError):
        load_text("base_url: http://evil.example/v1\n")
    with pytest.raises(ConfigError):
        load_text("agents:\n  privacy: {engine: magic}\n")


def test_local_privacy_engine_with_a_stub_model():
    diff = ("--- a/app.py\n+++ b/app.py\n@@ -0,0 +1,4 @@\n+from django import x\n+OWNER = \"Priya Ramanathan\"\n"
            "+K = \"ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789\"\n"
            "+t = {\"n\": \"Jane Doe\", \"e\": \"jane@example.com\"}\n")
    seen = []

    def stub(texts):
        seen.extend(texts)
        return [["GIVEN_NAME", "SURNAME"] if "Priya" in t else [] for t in texts]
    fs = local_pii.findings(diff, classify=stub)
    assert seen == ["Priya Ramanathan"]  # code-only, secret and fictional lines never reach the model
    assert [(f["line"], f["severity"], f["category"]) for f in fs] == [
        (3, "blocking", "GitHub token"), (2, "suggestion", "personal data")]


def test_local_engine_missing_model_is_an_error_not_a_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(local_pii, "JS", tmp_path)
    r = local_pii.result("privacy", "h", "--- a/x.md\n+++ b/x.md\n@@ -0,0 +1 @@\n+hello Priya\n")
    assert r["status"] == "error" and "not installed" in r["error"]


def test_merge_and_scrub_cover_other_providers():
    a = {"status": "ok", "verdict": "approve", "summary": "local", "findings": [], "engine": "local",
         "tokens": {"input": 0, "cached": 0, "output": 0}}
    b = {"status": "ok", "verdict": "changes", "summary": "llm", "findings": [{"m": 1}], "engine": "claude-code",
         "tokens": {"input": 5, "cached": 7, "output": 3}}
    m = merge("privacy", "h", [a, b])
    assert m["verdict"] == "changes" and m["tokens"] == {"input": 5, "cached": 7, "output": 3}
    half = merge("privacy", "h", [b, {"status": "error", "error": "x", "findings": []}])
    assert half["status"] == "error" and half["findings"] == [{"m": 1}]
    leaked = "sk-proj-" + "A" * 40 + " and ghp_" + "b" * 36
    assert "sk-proj-" not in json.dumps(scrub({"s": leaked}, [])) and "ghp_" not in json.dumps(scrub([leaked], []))


def test_quoted_non_ascii_and_tabbed_paths_are_scanned():
    from agent_review.privacy import added_lines, scan_added
    diff = ('--- /dev/null\n+++ "b/caf\\303\\251.py"\n@@ -0,0 +1 @@\n+K = "Jane Roe-private"\n'
            "--- a/a b.txt\t\n+++ b/a b.txt\t\n@@ -1 +1 @@\n-x\n+y\n")
    assert [f for f, _, _ in added_lines(diff)] == ["café.py", "a b.txt"]
    assert [(h.file, h.line) for h in scan_added(diff, ["Jane Roe-private"])] == [("café.py", 1)]


def test_oversized_diff_is_not_reviewed(repo):
    r, base, head = repo
    cfg = load_text("engine: openai-compatible\nmodel: m\nbase_url: https://x.invalid/v1\nmax_context_chars: 10\n")
    a = types.SimpleNamespace(agent="reviewer", repo=r, base=base, head=head)
    opener, calls = opener_returning()
    res = run_api(a, cfg, None, opener=opener)
    assert res["status"] == "error" and "max_context_chars" in res["error"] and calls == []


def test_localhost_lookalikes_are_rejected_and_node_gets_no_secrets(monkeypatch, tmp_path):
    with pytest.raises(ConfigError):
        load_text("base_url: http://localhost.evil.example/v1\n")
    assert load_text("base_url: http://localhost:11434/v1\n")["base_url"]
    (tmp_path / "node_modules" / "@nationaldesignstudio" / "rampart").mkdir(parents=True)
    monkeypatch.setattr(local_pii, "JS", tmp_path)
    monkeypatch.setenv("AGENT_REVIEW_API_KEY", "should-not-pass")
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw["env"])
        return types.SimpleNamespace(returncode=0, stdout="[]", stderr="")
    monkeypatch.setattr(local_pii.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError):  # node answered for none of the texts: incomplete, not "no PII"
        local_pii.classify(["hello Priya"])
    assert "AGENT_REVIEW_API_KEY" not in seen
