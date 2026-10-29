"""Per-repo configuration (.github/agent-review.yml). Every key is optional; unknown keys are an error."""

from __future__ import annotations

import copy
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import yaml

AGENTS = ("reviewer", "privacy", "safety")

DEFAULTS: dict = {
    "agents": {
        "reviewer": {"enabled": True},
        "privacy": {"enabled": True, "engine": "local"},  # local (no LLM) | llm | both
        "safety": {"enabled": True, "paths": []},  # empty = every PR; globs scope it to matching files
    },
    "blocking": False,  # advisory by default: the check stays green and humans decide
    "engine": "auto",  # auto | openai-compatible | claude-code; auto picks by the credential the repo has
    "model": "",  # required for openai-compatible (e.g. gpt-5.1-mini, gemini-3-flash); claude-code: sonnet
    "base_url": "",  # openai-compatible endpoint, e.g. https://api.openai.com/v1, https://openrouter.ai/api/v1
    "max_context_chars": 150_000,  # openai-compatible: diff + changed files sent to the model
    "max_budget_usd": 1.0,  # per agent per run
    "skip": {"draft": True, "max_changed_lines": 2000},
    "instructions": "",  # repo-specific notes every agent reads (conventions, risky areas)
}


class ConfigError(ValueError):
    pass


def _merge(base: dict, over: dict, where: str = "") -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if k not in base:
            raise ConfigError(f"unknown key {where}{k}")
        out[k] = _merge(base[k], v, f"{where}{k}.") if isinstance(base[k], dict) and isinstance(v, dict) else v
    return out


def load_at(repo: Path, ref: str, path: str) -> dict:
    """The config as of `ref` (the PR's base), so a pull request cannot switch off its own review."""
    r = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{path}"], capture_output=True, text=True)
    return load_text(r.stdout if r.returncode == 0 else "")


def load(path: Path | None) -> dict:
    return load_text(path.read_text() if path and path.exists() else "")


def load_text(text: str) -> dict:
    raw = yaml.safe_load(text) if text.strip() else None
    if raw is not None and not isinstance(raw, dict):
        raise ConfigError("config must be a mapping")
    cfg = _merge(DEFAULTS, raw or {})
    if not isinstance(cfg["agents"]["safety"].get("paths", []), list):
        raise ConfigError("agents.safety.paths must be a list of globs")
    if cfg["engine"] not in ("auto", "openai-compatible", "claude-code"):
        raise ConfigError("engine must be auto, openai-compatible or claude-code")
    if cfg["agents"]["privacy"].get("engine") not in ("local", "llm", "both"):
        raise ConfigError("agents.privacy.engine must be local, llm or both")
    url = str(cfg["base_url"])
    parsed = urlparse(url)
    local = parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if url and not ((parsed.scheme == "https" and parsed.hostname) or local):
        raise ConfigError("base_url must be https (or http://localhost for a self-hosted model)")
    if cfg["model"] and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}", str(cfg["model"])):
        raise ConfigError("model must be a plain model name (e.g. gpt-5.1-mini, openai/gpt-4o, qwen3.5:9b)")
    return cfg
