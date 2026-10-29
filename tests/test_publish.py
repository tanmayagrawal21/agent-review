import json
import subprocess
import sys
from pathlib import Path

from agent_review.publish import anchor, build, results

ROOT = Path(__file__).resolve().parents[1]
DIFF = "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,3 @@\n x = 1\n-y = 2\n+y = 20\n+z = 3\n"
BUNDLE = {"head": "h" * 40, "agents": ["reviewer", "privacy", "safety"], "privacy": True, "diff": DIFF,
          "files": {"a.py": "x = 1\ny = 20\nz = 3\n"}, "config": {"blocking": False},
          "local_findings": [{"file": "a.py", "line": 3, "severity": "blocking", "category": "GitHub token",
                              "message": "key"}]}


def write(d: Path, folder: str, **r):
    (d / f"findings-{folder}").mkdir(parents=True, exist_ok=True)
    (d / f"findings-{folder}" / f"{folder}.json").write_text(json.dumps(
        {"agent": folder, "sha": "h" * 40, "status": "ok", "verdict": "approve", "summary": "s", "findings": [],
         "tokens": {"input": 10, "cached": 5, "output": 2}, **r}))


def test_results_reject_forged_and_stale_and_merge_privacy(tmp_path):
    write(tmp_path, "reviewer", agent="safety")  # another agent's result posing as the reviewer
    write(tmp_path, "safety", sha="0" * 40)
    write(tmp_path, "privacy", findings=[{"file": "a.py", "line": 2, "severity": "suggestion", "message": "m"}])
    rs = {r["agent"]: r for r in results(BUNDLE, tmp_path)}
    assert rs["reviewer"]["status"] == rs["safety"]["status"] == "error"
    assert rs["privacy"]["verdict"] == "changes" and len(rs["privacy"]["findings"]) == 2  # local + model


def test_keyless_privacy_is_local_only(tmp_path):
    rs = results({**BUNDLE, "agents": []}, tmp_path)
    assert [r["agent"] for r in rs] == ["privacy"] and rs[0]["status"] == "ok" and "No model pass" in rs[0]["summary"]


def test_anchor_and_one_review():
    moved = anchor([{"file": "a.py", "line": 9, "code": "z = 3", "severity": "blocking", "message": "m"}],
                   BUNDLE["files"])
    assert moved[0]["line"] == 3
    rs = [{"agent": "reviewer", "status": "ok", "verdict": "changes", "summary": "s", "tokens": {"input": 1200},
           "findings": [{"file": "a.py", "line": 2, "severity": "blocking", "category": "bug", "message": "b"},
                        {"file": "a.py", "line": 50, "severity": "suggestion", "message": "far"}]},
          {"agent": "safety", "status": "ok", "verdict": "changes", "summary": "s", "tokens": {"output": 30},
           "findings": [{"file": "a.py", "line": 2, "severity": "blocking", "category": "limit", "message": "c"}]}]
    review, failed = build(rs, BUNDLE)
    assert failed and review["event"] == "COMMENT" and len(review["comments"]) == 1
    assert "(Reviewer · bug)" in review["comments"][0]["body"] and "(Safety · limit)" in review["comments"][0]["body"]
    assert "`a.py:50`" in review["body"] and "**1,200 / 0 / 30**" in review["body"] and "$" not in review["body"]


def test_cli_scrubs_credentials_from_what_is_posted(tmp_path):
    write(tmp_path / "f", "reviewer", summary="it used ghp_" + "c" * 36)
    b = {**BUNDLE, "agents": ["reviewer"], "privacy": False}
    (tmp_path / "b.json").write_text(json.dumps(b))
    out = subprocess.run([sys.executable, "-m", "agent_review.publish", "--bundle", str(tmp_path / "b.json"),
                          "--findings", str(tmp_path / "f"), "--dry-run"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert "ghp_" not in out and "[credential removed]" in out


def test_a_skipped_review_is_announced_not_silent():
    b = {"head": "h" * 40, "agents": [], "privacy": False, "diff": "", "files": {}, "local_findings": [],
         "config": {"blocking": False}, "notice": "3138 changed lines exceeds skip.max_changed_lines"}
    review, failed = build(results(b, Path("/nonexistent")), b)
    assert "**Not reviewed:** 3138 changed lines" in review["body"] and review["comments"] == [] and failed


def test_a_long_pr_is_warned_about_in_the_review():
    review, _ = build([], {**BUNDLE, "agents": [], "warning": "this is a long PR (3138 changed lines)"})
    assert "> Warning: this is a long PR (3138 changed lines)." in review["body"]


def test_throttled_posts_are_retried(monkeypatch):
    from agent_review import publish
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        if len(calls) == 1:
            err = "exceeded a secondary rate limit (HTTP 403)"
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": err})()
        return type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()
    monkeypatch.setattr(publish.subprocess, "run", fake)
    monkeypatch.setattr(publish.time, "sleep", lambda s: None)
    assert publish.gh("repos/x/pulls/1/reviews", "--method", "POST", payload={}) == "{}" and len(calls) == 2


def test_agents_that_could_not_run_are_shown_not_hidden(tmp_path):
    b = {**BUNDLE, "agents": [], "gap": ["reviewer", "privacy", "safety"],
         "notice": "diff is 194363 chars, over max_context_chars: not sent to a model"}
    rs = {r["agent"]: r for r in results(b, tmp_path)}
    assert set(rs) == {"reviewer", "privacy", "safety"}
    assert rs["reviewer"]["status"] == rs["safety"]["status"] == "error"
    assert "not reviewed: diff is 194363" in rs["reviewer"]["error"]
    assert rs["privacy"]["status"] == "ok" and "No model pass: diff is 194363" in rs["privacy"]["summary"]
    review, failed = build(list(rs.values()), b)
    assert failed and review["body"].count("Not approved.") == 2


def test_privacy_section_counts_rampart_pii_and_keeps_other_concerns(tmp_path):
    local = [{"file": "a.py", "line": 2, "severity": "suggestion", "category": "potential PII", "message": "m"},
             {"file": "a.py", "line": 2, "severity": "suggestion", "category": "potential PII", "message": "m"}]
    write(tmp_path, "privacy", summary="Logs the full request body.")
    rs = {r["agent"]: r for r in results({**BUNDLE, "local_findings": local}, tmp_path)}
    s = rs["privacy"]["summary"]
    assert s.startswith("**1 line(s) with potential PII** flagged by Rampart") and "never blocking" in s
    assert "Other privacy concerns: Logs the full request body." in s
    review, _ = build(list(rs.values()), {**BUNDLE, "local_findings": local})
    assert sum("potential PII" in c["body"] for c in review["comments"]) == 1  # identical findings posted once
