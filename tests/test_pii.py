import pytest

from agent_review import pii

from .stub import model


def test_every_line_is_redacted_whole_and_fictional_values_are_skipped():
    seen = []

    def spy(texts):
        seen.extend(texts)
        return model(texts)
    items = [("app.py", "from django import x"), ("app.py", 'OWNER = "Priya Ramanathan"  # contact'),
             ("t.py", 'u = {"n": "Jane Doe", "e": "jane@example.com"}'), ("README.md", "Ask Hannah Castellanos")]
    lines, found = pii.process(items, spy)
    assert "from django import x" in seen
    assert lines[1] == 'OWNER = "[GIVEN_NAME_1]"  # contact' and lines[3] == "Ask [GIVEN_NAME_1]"
    assert found[1]["labels"] == ["GIVEN_NAME"] and found[3]["labels"] == ["GIVEN_NAME"]


def test_identifiers_are_masked_for_the_model_but_not_reported():
    def django_is_a_name(texts):
        return [(t.replace("django", "[GIVEN_NAME_1]"), ["GIVEN_NAME"] if "django" in t else []) for t in texts]
    lines, found = pii.process([("app.py", "from django import x")], django_is_a_name)
    assert lines[0] == "from [GIVEN_NAME_1] import x" and found[0]["labels"] == []


def test_lines_no_parser_understands_are_still_redacted():
    tricky = [("a.py", "Priya Ramanathan, 472-81-0094"),  # inside a docstring opened earlier
              ("a.rb", "<<~EOS Priya Ramanathan EOS"), ("a.lua", "x = [[Priya Ramanathan]]"),
              ("a.cs", 'var s = @"Priya Ramanathan'), ("a.py", "Q = '\"\"\"'; Priya Ramanathan")]
    lines, found = pii.process(tricky, model)
    assert all("Ramanathan" not in ln for ln in lines)
    assert found[0]["labels"] == ["GIVEN_NAME", "SSN"]  # multi-word and digits: reported even outside a string


def test_secrets_are_redacted_before_the_model_sees_the_line():
    seen = []
    lines, found = pii.process([("a.py", 'K = "ghp_' + "a" * 36 + '"  # rotate monthly')],
                               lambda t: (seen.extend(t), model(t))[1])
    assert lines[0] == 'K = "[SECRET]"  # rotate monthly' and found[0]["secrets"] == ["GitHub token"]
    assert not any("ghp_" in t for t in seen)


def test_findings_policy_rampart_pii_is_never_blocking():
    hit = {"secrets": [], "labels": ["SSN", "GIVEN_NAME"], "line": 'x = "Ann Lee 472-81-0094"',
           "spans": [(5, 12, ["GIVEN_NAME", "SURNAME"]), (13, 24, ["SSN"])]}
    f = pii.findings("a.py", 3, hit)
    assert [(x["severity"], x["category"]) for x in f] == [("suggestion", "potential PII")]  # one per line
    assert "can be wrong; check it yourself" in f[0]["message"]
    card = pii.findings("a.py", 1, {"secrets": ["Stripe secret key"], "labels": ["CREDIT_CARD"], "line": "k",
                                    "spans": [(0, 1, ["CREDIT_CARD"])]})
    assert [(x["severity"], x["category"]) for x in card] == [("blocking", "Stripe secret key")]


def test_only_name_shaped_text_is_reported_as_a_name():
    assert pii.plausible("Priya Ramanathan", ["GIVEN_NAME", "SURNAME"])
    for code in ('" "] * len', "detected", "OTHER", "verdicts"):
        assert not pii.plausible(code, ["GIVEN_NAME"])
    assert pii.plausible("472-81-0094", ["SSN"])


def test_highlight_survives_backticks_and_tabs():
    out = pii.highlight("\tsee ```Ann Lee```", [(8, 15, ["GIVEN_NAME"])])
    assert out.startswith("\n````\n") and out.endswith("\n````")
    line, carets = out.split("\n")[2:4]
    assert line[carets.index("^"):carets.index("^") + 7] == "Ann Lee"


def test_scrub_and_missing_model(monkeypatch, tmp_path):
    out = pii.scrub("key sk-proj-" + "A" * 40 + " and topsecretvalue", ["topsecretvalue"])
    assert "sk-proj" not in out and "topsecretvalue" not in out
    monkeypatch.setattr(pii, "JS", tmp_path)
    with pytest.raises(RuntimeError, match="not installed"):
        pii.run_model(["hello"])


def test_model_process_gets_no_secrets_and_incomplete_output_is_an_error(monkeypatch, tmp_path):
    (tmp_path / "node_modules" / "@nationaldesignstudio" / "rampart").mkdir(parents=True)
    monkeypatch.setattr(pii, "JS", tmp_path)
    monkeypatch.setenv("AGENT_REVIEW_API_KEY", "should-not-pass")
    seen = {}

    def fake(cmd, **kw):
        seen.update(kw["env"])
        return type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""})()
    monkeypatch.setattr(pii.subprocess, "run", fake)
    with pytest.raises(RuntimeError, match="incomplete"):
        pii.run_model(["hello"])
    assert "AGENT_REVIEW_API_KEY" not in seen


def test_replaced_maps_placeholders_back_to_the_original():
    assert pii.replaced("Call Priya Ramanathan at 472-81-0094.", "Call [GIVEN_NAME_1] at [SSN_1].") == [
        (5, 21, "GIVEN_NAME"), (25, 36, "SSN")]
    assert pii.replaced("Priya", "[GIVEN_NAME_1]") == [(0, 5, "GIVEN_NAME")]


def test_a_name_split_into_two_placeholders_counts_as_several_words():
    def split(texts):
        return [(t.replace("Priya", "[GIVEN_NAME_1]").replace("Ramanathan", "[SURNAME_1]"),
                 ["GIVEN_NAME", "SURNAME"] if "Priya" in t else []) for t in texts]
    _, found = pii.process([("a.lua", "x = [[Priya Ramanathan]]")], split)
    assert found[0]["labels"] == ["GIVEN_NAME", "SURNAME"]


def test_numbers_and_emails_around_fakes_still_reach_the_redactor():
    seen = []
    lines = ["card 4000 0555 0123 0000", "+44 20 7555 0123", "acct 98765550123", "priya.r@qa.test.acmecorp.com",
             "priya@example.com.au"]
    pii.process([("a.md", ln) for ln in lines], lambda t: (seen.extend(t), model(t))[1])
    assert seen == lines  # every line goes to the redactor whole; fakes are only exempt from findings


def test_fictional_values_are_not_reported_and_source_placeholders_are_ignored():
    def tag(texts):
        return [(t.replace("Jane Doe", "[GIVEN_NAME_1]"), ["GIVEN_NAME"] if "Jane Doe" in t else []) for t in texts]
    _, found = pii.process([("t.py", 'u = "Jane Doe"'), ("t.py", 'x = "[SSN_1]"')], tag)
    assert found[0]["labels"] == [] and found[1]["labels"] == []


def test_findings_highlight_the_detected_text_in_place():
    lines, found = pii.process([("a.py", 'OWNER = "Priya Ramanathan"  # see ghp_' + "a" * 36)], model)
    msg = pii.findings("a.py", 1, found[0])[-1]["message"]
    assert 'OWNER = "Priya Ramanathan"  # see [SECRET]' in msg  # PII shown and underlined, secret still masked
    assert "^^^^^^^^^^^^^^^^ name" in msg and "ghp_" not in msg
