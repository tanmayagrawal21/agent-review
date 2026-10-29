import json
import subprocess
import sys
from pathlib import Path

from .conftest import git

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run([sys.executable, "-m", *args], cwd=ROOT, capture_output=True, text=True)


def _write(findings: Path, folder: str, **r):
    d = findings / f"findings-{folder}"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{folder}.json").write_text(json.dumps({"agent": folder, "status": "ok", "verdict": "approve",
                                                   "summary": "fine", "findings": [], **r}))


def test_combine_rejects_forged_and_stale_results(repo, tmp_path):
    r, base, head = repo
    f = tmp_path / "findings"
    _write(f, "reviewer", sha=head, agent="privacy")  # another agent's file posing as the reviewer
    _write(f, "privacy", sha="0" * 40)  # stale
    out = run("agent_review.combine", "--repo-dir", str(r), "--findings", str(f), "--base", base, "--head", head,
              "--agents", '["reviewer","privacy"]', "--dry-run")
    body = json.loads(out.stdout)["body"]
    assert body.count("no result for this commit") == 2


def test_blocking_mode_fails_closed_when_review_is_skipped(repo):
    r, base, head = repo
    (r / ".github" / "agent-review.yml").write_text("blocking: true\n")
    git(r, "commit", "-qam", "blocking")
    new_base = git(r, "rev-parse", "HEAD")
    (r / "app.py").write_text("changed\n")
    git(r, "commit", "-qam", "change")
    head2 = git(r, "rev-parse", "HEAD")
    args = ("agent_review.plan", "--repo", str(r), "--base", new_base, "--head", head2)
    assert run(*args, "--has-key", "false").returncode == 1  # no credential: not reviewed, so not green
    assert run(*args, "--has-key", "true").returncode == 0
    assert run(*args, "--has-key", "true", "--draft", "true").returncode == 0  # drafts can't merge anyway
    assert run("agent_review.plan", "--repo", str(r), "--base", base, "--head", head, "--has-key", "false"
               ).returncode == 0  # advisory config at that base: a skip is only a notice
