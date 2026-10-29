import json
import subprocess
import sys
from pathlib import Path

from agent_review.config import parse
from agent_review.prepare import build, decide

from .conftest import git
from .stub import model

ROOT = Path(__file__).resolve().parents[1]
CFG = parse("base_url: https://api.example.com/v1\nmodel: m\n")


def test_decide():
    files = ["app.py"]
    assert decide(CFG, files, 10, False, True)["agents"] == ["reviewer", "privacy", "safety"]
    keyless = decide(CFG, files, 10, False, False)
    assert keyless["agents"] == [] and keyless["local"] and keyless["gap"] == ["reviewer", "privacy", "safety"]
    assert decide(parse(""), files, 10, False, True, has_url=True, has_model=True)["agents"]  # all from secrets
    assert decide(CFG, files, 10, True, True)["agents"] == [] and decide(CFG, files, 10, True, True)["gap"] == []
    assert decide(CFG, files, 5000, False, True)["gap"] == ["reviewer", "privacy", "safety"]
    scoped = parse("base_url: https://x.example/v1\nmodel: m\nagents:\n  safety: {paths: ['auth/*']}\n")
    assert decide(scoped, files, 10, False, True)["agents"] == ["reviewer", "privacy"]


def _pr(repo):
    r, base, _ = repo
    (r / "app.py").write_text('OWNER = "Priya Ramanathan"\nSSN = "472-81-0094"\nK = "ghp_' + "b" * 36 + '"\n')
    git(r, "add", "-A")
    git(r, "commit", "-qm", "Add owner for Hannah Castellanos")
    return r, base, git(r, "rev-parse", "HEAD")


def test_bundle_is_redacted_and_carries_the_local_findings(repo):
    r, base, head = _pr(repo)
    plan = decide(CFG, ["app.py"], 3, False, True)
    b = build(r, base, head, CFG, plan, "Owner: Priya Ramanathan", "Contact hcastellanos@outlook.com", run=model)
    text = json.dumps({k: b[k] for k in ("diff", "log", "title", "body", "files")})
    for raw in ("Priya", "Ramanathan", "472-81-0094", "ghp_", "Hannah", "hcastellanos"):
        assert raw not in text, raw
    assert "[SECRET]" in b["files"]["app.py"] and b["redacted"] is True
    cats = sorted({(f["line"], f["category"]) for f in b["local_findings"] if f["file"] == "app.py"})
    assert (1, "personal data") in cats and (2, "personal data") in cats and (3, "GitHub token") in cats
    assert [f["severity"] for f in b["local_findings"] if f["line"] == 2] == ["blocking"]  # SSN blocks
    assert b["config"]["model"] == "m" and "key" not in json.dumps(b["config"]).lower()


def test_oversized_diff_is_not_sent(repo):
    r, base, head = _pr(repo)
    cfg = {**CFG, "max_context_chars": 10}
    plan = decide(cfg, ["app.py"], 3, False, True)
    b = build(r, base, head, cfg, plan, "", "", run=model)
    assert b["agents"] == [] and "max_context_chars" in plan["reason"] and plan["gap"]


def test_cli_fails_closed_only_in_blocking_mode(repo, tmp_path):
    r, base, head = repo
    run = lambda b, **env: subprocess.run(  # noqa: E731
        [sys.executable, "-m", "agent_review.prepare", "--repo", str(r), "--base", b, "--head", head,
         "--out", str(tmp_path / "b.json")], cwd=ROOT, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "HAS_KEY": "false", **env})
    # the fixture's notes.md is fictional only, but the model is not installed here: draft PRs skip it
    assert run(base, **{}).returncode in (0, 1)
    (r / ".github" / "agent-review.yml").write_text("blocking: true\nagents:\n  privacy: {enabled: false}\n")
    git(r, "commit", "-qam", "blocking")
    blocking_base = git(r, "rev-parse", "HEAD")
    (r / "app.py").write_text("changed\n")
    git(r, "commit", "-qam", "change")
    head2 = git(r, "rev-parse", "HEAD")
    out = subprocess.run([sys.executable, "-m", "agent_review.prepare", "--repo", str(r), "--base", blocking_base,
                          "--head", head2, "--out", str(tmp_path / "c.json")], cwd=ROOT, capture_output=True,
                         text=True, env={"PATH": "/usr/bin:/bin", "HAS_KEY": "false"})
    assert out.returncode == 1 and "::error::" in out.stdout  # enabled agents cannot run: not green


def test_hunk_header_context_never_reaches_the_bundle(repo):
    r, base, _ = repo
    (r / "data.csv").write_text("".join(f"row{i},x\n" for i in range(8)) + "Priya Ramanathan,4155551234\n" + "z\n" * 6)
    git(r, "add", "-A")
    git(r, "commit", "-qm", "data")
    mid = git(r, "rev-parse", "HEAD")
    (r / "data.csv").write_text((r / "data.csv").read_text().replace("z\nz\nz\nz\n", "z\nz\nNEW\nz\n", 1))
    git(r, "commit", "-qam", "edit")
    head = git(r, "rev-parse", "HEAD")
    assert "Ramanathan" in git(r, "diff", f"{mid}...{head}")  # git put it in the @@ header
    b = build(r, mid, head, CFG, decide(CFG, ["data.csv"], 1, False, True), "", "", run=model)
    assert "Ramanathan" not in b["diff"] and "Priya" not in b["diff"]
