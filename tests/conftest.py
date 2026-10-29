import subprocess
from pathlib import Path

import pytest


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path) -> tuple[Path, str, str]:
    """A repo with a base commit (config + app.py) and a PR head that edits app.py and adds notes.md."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "T")
    (r / ".github").mkdir()
    (r / ".github" / "agent-review.yml").write_text("agents:\n  safety: {paths: ['auth/*']}\n")
    (r / "app.py").write_text("a = 1\nb = 2\nc = 3\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    base = git(r, "rev-parse", "HEAD")
    (r / "app.py").write_text("a = 1\nb = 20\nc = 3\nd = 4\n")
    (r / "notes.md").write_text("call Jane Doe at (555) 010-4477\n")
    (r / ".github" / "agent-review.yml").write_text("agents:\n  reviewer: {enabled: false}\n")  # PR tries to opt out
    git(r, "add", "-A")
    git(r, "commit", "-qm", "head")
    return r, base, git(r, "rev-parse", "HEAD")
