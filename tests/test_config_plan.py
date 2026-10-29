import pytest

from agent_review.config import ConfigError, load_at, load_text
from agent_review.plan import changed, decide


def test_defaults_and_unknown_keys():
    cfg = load_text("")
    assert cfg["agents"]["safety"]["paths"] == [] and cfg["blocking"] is False
    with pytest.raises(ConfigError):
        load_text("agents:\n  reviewr: {enabled: true}\n")
    with pytest.raises(ConfigError):
        load_text("model: 'sonnet; rm -rf /'\n")


def test_config_is_read_at_the_base_so_a_pr_cannot_opt_out(repo):
    r, base, head = repo
    cfg = load_at(r, base, ".github/agent-review.yml")
    assert cfg["agents"]["reviewer"]["enabled"] is True
    assert cfg["agents"]["safety"]["paths"] == ["auth/*"]
    assert load_at(r, base, "missing.yml")["agents"]["reviewer"]["enabled"] is True  # no file: defaults


def test_plan_scopes_and_skips(repo):
    r, base, head = repo
    files, lines = changed(r, base, head)
    assert set(files) == {".github/agent-review.yml", "app.py", "notes.md"} and lines > 0
    cfg = load_at(r, base, ".github/agent-review.yml")
    assert decide(cfg, files, lines, False, True)["agents"] == ["reviewer", "privacy"]  # safety out of scope
    assert decide(cfg, ["auth/login.py"], 5, False, True)["agents"] == ["reviewer", "privacy", "safety"]
    assert decide(cfg, files, lines, True, True)["run"] is False  # draft
    assert decide(cfg, files, 5000, False, True)["run"] is False  # too large
    keyless = decide(cfg, files, lines, False, False)
    assert keyless["agents"] == ["privacy"] and keyless["local"] and "fork" in keyless["reason"]  # local scan only
    assert decide(cfg, files, lines, False, True)["engine"] == "claude-code"
    api = load_text("base_url: https://api.example.com/v1\nmodel: m\n")
    assert decide(api, files, lines, False, False, has_api=True)["engine"] == "openai-compatible"


def test_changed_files_handles_non_ascii_and_renames(repo):
    from .conftest import git
    r, base, head = repo
    (r / "café.py").write_text("x = 1\n")
    git(r, "mv", "notes.md", "renamed notes.md")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "more")
    files, _ = changed(r, base, git(r, "rev-parse", "HEAD"))
    assert "café.py" in files and "renamed notes.md" in files
