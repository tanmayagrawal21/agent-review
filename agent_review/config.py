"""Per-repo configuration, `.github/agent-review.yml`, always read at the PR's BASE commit so a pull request
cannot change its own review. Every key is optional; unknown keys are an error."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .diff import git

AGENTS = ("reviewer", "privacy", "safety")
DEFAULTS: dict = {
    "api": "openai",  # openai (any OpenAI-compatible endpoint) | anthropic (Messages API)
    "base_url": "",  # e.g. https://api.openai.com/v1, https://openrouter.ai/api/v1, https://api.anthropic.com
    "model": "",  # e.g. gpt-5.1-mini, google/gemini-3-flash, claude-sonnet-4-5, qwen3.5:9b
    "agents": {
        "reviewer": {"enabled": True, "paths": []},
        "privacy": {"enabled": True, "paths": []},
        "safety": {"enabled": True, "paths": []},  # [] = every PR; globs scope an agent to matching files
    },
    "redact": True,  # every text passes through the local PII model before any model sees it
    "blocking": False,  # true: the check fails when an agent requests changes, errors, or cannot run
    "max_context_chars": 150_000,  # diff + changed files sent to the model; a larger diff is not reviewed
    "skip": {"draft": True, "max_changed_lines": 2000},
    "instructions": "",  # notes for every agent about this codebase
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


def parse(text: str) -> dict:
    raw = yaml.safe_load(text) if text.strip() else None
    if raw is not None and not isinstance(raw, dict):
        raise ConfigError("config must be a mapping")
    cfg = _merge(DEFAULTS, raw or {})
    if cfg["api"] not in ("openai", "anthropic"):
        raise ConfigError("api must be openai or anthropic")
    url = urlparse(str(cfg["base_url"]))
    local = url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")
    if cfg["base_url"] and not ((url.scheme == "https" and url.hostname) or local):
        raise ConfigError("base_url must be https (or http://localhost for a self-hosted model)")
    if cfg["model"] and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}", str(cfg["model"])):
        raise ConfigError("model must be a plain model name")
    for name in AGENTS:
        if not isinstance(cfg["agents"][name].get("paths", []), list):
            raise ConfigError(f"agents.{name}.paths must be a list of globs")
    return cfg


def load(repo: Path, base: str, path: str = ".github/agent-review.yml") -> dict:
    try:
        text = git(repo, "show", f"{base}:{path}")
    except Exception:  # noqa: BLE001 - no config file at the base commit: defaults
        text = ""
    return parse(text)
