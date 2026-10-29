import pytest

from agent_review import pii

from .stub import model


def test_only_prose_reaches_the_model_and_fictional_values_are_skipped():
    seen = []

    def spy(texts):
        seen.extend(texts)
        return model(texts)
    items = [("app.py", "from django import x"), ("app.py", 'OWNER = "Priya Ramanathan"  # contact'),
             ("t.py", 'u = {"n": "Jane Doe", "e": "jane@example.com"}'), ("README.md", "Ask Hannah Castellanos")]
    lines, found = pii.process(items, spy)
    assert seen == ["Priya Ramanathan", "contact", "Ask Hannah Castellanos"]
    assert lines[1] == 'OWNER = "[GIVEN_NAME_1]"  # contact' and lines[3] == "Ask [GIVEN_NAME_1]"
    assert lines[0] == "from django import x" and lines[2] == items[2][1]
    assert found[1]["labels"] == ["GIVEN_NAME"]


def test_secrets_are_redacted_before_the_model_sees_the_line():
    seen = []
    lines, found = pii.process([("a.py", 'K = "ghp_' + "a" * 36 + '"  # rotate monthly')],
                               lambda t: (seen.extend(t), model(t))[1])
    assert lines[0] == 'K = "[SECRET]"  # rotate monthly' and found[0]["secrets"] == ["GitHub token"]
    assert not any("ghp_" in t for t in seen)


def test_findings_policy():
    f = pii.findings("a.py", 3, {"secrets": [], "labels": ["SSN", "GIVEN_NAME"]})
    assert [(x["severity"], x["category"]) for x in f] == [("blocking", "personal data")]
    soft = pii.findings("a.py", 1, {"secrets": [], "labels": ["EMAIL", "URL"]})
    assert [x["severity"] for x in soft] == ["suggestion"]
    card = pii.findings("a.py", 1, {"secrets": ["Stripe secret key"], "labels": ["CREDIT_CARD"]})
    assert [x["category"] for x in card] == ["Stripe secret key"]  # a key is not also reported as a card


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


def test_unknown_formats_are_whole_line_and_spans_never_overlap():
    assert pii.spans("contact.vcf", "FN:Priya Ramanathan") == [(0, 19)]
    assert pii.spans("notes", "anything") == [(0, 8)]
    line = '# hi "Hannah Castellanos" ok'
    assert pii.spans("a.py", line) == [(2, len(line))]
    lines, _ = pii.process([("a.py", line), ("a.py", 'x = "Priya Ramanathan"  # and "Priya Ramanathan"')], model)
    assert lines[0] == '# hi "[GIVEN_NAME_1]" ok'
    assert lines[1] == 'x = "[GIVEN_NAME_1]"  # and "[GIVEN_NAME_1]"'


def test_lines_inside_multiline_strings_and_comments_are_classified_whole():
    src = 'X = 1\nFIXTURE = """\nPriya Ramanathan, 415-555-1234\n"""\n/* Hannah Castellanos\nstill comment */\ny = 2\n'
    inside = pii.block_lines("a.py", src)
    assert inside == {2, 3, 4, 5, 6}
    assert pii.spans("a.py", "Priya Ramanathan, 415-555-1234") == []  # alone, a code line looks like code...
    lines, found = pii.process([("a.py", "Priya Ramanathan, 415-555-1234", True)], model)  # ...but not in a block
    assert lines[0].startswith("[GIVEN_NAME_1]") and found[0]["labels"] == ["GIVEN_NAME"]
    assert pii.spans("run.sh", "echo Priya") == [(0, 10)]
