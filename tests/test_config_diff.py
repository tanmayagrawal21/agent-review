import pytest

from agent_review.config import ConfigError, load, parse
from agent_review.diff import added, changed, commentable, walk

from .conftest import git


def test_defaults_and_validation():
    cfg = parse("")
    assert cfg["redact"] is True and cfg["blocking"] is False and cfg["api"] == "openai"
    for bad in ("agents:\n  reviewr: {}\n", "model: 'x; rm -rf /'\n", "api: magic\n",
                "base_url: http://example.com/v1\n", "base_url: http://localhost.evil.example/v1\n",
                "max_context_chars: true\n", "skip: 3\n", "agents:\n  reviewer: 3\n"):
        with pytest.raises(ConfigError):
            parse(bad)
    assert parse("base_url: http://localhost:11434/v1\nmodel: qwen3.5:9b\n")["model"] == "qwen3.5:9b"


def test_config_is_read_at_the_base_so_a_pr_cannot_opt_out(repo):
    r, base, head = repo
    assert load(r, base)["agents"]["reviewer"]["enabled"] is True  # head disables it; base wins
    assert load(r, base, "missing.yml") == parse("")


def test_walk_handles_quoted_paths_lookalike_headers_and_no_newline():
    diff = ('--- /dev/null\n+++ "b/caf\\303\\251.py"\n@@ -0,0 +1,2 @@\n+a\n+b\n\\ No newline at end of file\n'
            "--- a/q.sql\t\n+++ b/q.sql\t\n@@ -1,2 +1,2 @@\n keep\n--- old\n+++ new\n")
    assert added(diff) == [("café.py", 1, "a"), ("café.py", 2, "b"), ("q.sql", 2, "++ new")]
    assert commentable(diff) == {"café.py": {1, 2}, "q.sql": {1, 2}}
    assert sum(1 for _, kind, _, _ in walk(diff) if kind == "meta") == 8  # 6 headers/hunks, \ marker, final ""


def test_changed_files_with_non_ascii_and_renames(repo):
    r, base, _ = repo
    (r / "café.py").write_text("x = 1\n")
    git(r, "mv", "notes.md", "renamed notes.md")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "more")
    files, lines = changed(r, base, git(r, "rev-parse", "HEAD"))
    assert {"café.py", "renamed notes.md", "app.py"} <= set(files) and lines > 0
