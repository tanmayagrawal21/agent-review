"""Decide which agents run on this PR. Prints JSON and, on Actions, writes `agents`/`run`/`reason` outputs."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
from pathlib import Path

from .config import AGENTS, load_at


def changed(repo: Path, base: str, head: str) -> tuple[list[str], int]:
    out = subprocess.run(["git", "-C", str(repo), "diff", "-z", "--numstat", f"{base}...{head}"], check=True,
                         capture_output=True, text=True).stdout
    files, lines, fields = [], 0, out.split("\0")
    i = 0
    while i < len(fields) and fields[i]:
        add, rem, path = fields[i].split("\t", 2)
        if not path:  # a rename: the old and new paths follow as separate fields
            path, i = fields[i + 2], i + 2
        files.append(path)
        lines += (int(add) if add.isdigit() else 0) + (int(rem) if rem.isdigit() else 0)
        i += 1
    return files, lines


def llm_engine(cfg: dict, has_claude: bool, has_api: bool) -> str | None:
    if cfg["engine"] == "auto":
        return "openai-compatible" if has_api and cfg["base_url"] else "claude-code" if has_claude else None
    ok = {"openai-compatible": has_api and bool(cfg["base_url"]), "claude-code": has_claude}[cfg["engine"]]
    return cfg["engine"] if ok else None


def decide(cfg: dict, files: list[str], lines: int, draft: bool, has_claude: bool, has_api: bool = False) -> dict:
    engine = llm_engine(cfg, has_claude, has_api)
    none = {"run": False, "agents": [], "engine": engine or "", "local": False}
    if draft and cfg["skip"]["draft"]:
        return {**none, "reason": "draft PR"}
    if cfg["skip"]["max_changed_lines"] and lines > cfg["skip"]["max_changed_lines"]:
        return {**none, "reason": f"{lines} changed lines exceeds skip.max_changed_lines"}
    agents, missing = [], []
    for name in AGENTS:
        a = cfg["agents"][name]
        if not a.get("enabled"):
            continue
        paths = a.get("paths") or []
        if paths and not any(fnmatch.fnmatch(f, p) for f in files for p in paths):
            continue
        needs_llm = not (name == "privacy" and a.get("engine") == "local")
        if needs_llm and not engine:
            missing.append(name)  # fork PRs get no secrets; a local privacy scan still runs
            if not (name == "privacy" and a.get("engine") == "both"):
                continue
        agents.append(name)
    local = "privacy" in agents and cfg["agents"]["privacy"].get("engine") in ("local", "both")
    reason = f"no model credential for {', '.join(missing)} (fork PRs get no secrets)" if missing else ""
    if not agents:
        reason = reason or "no agent in scope"
    return {"run": bool(agents), "agents": agents, "engine": engine or "", "local": local, "reason": reason,
            "missing": missing}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--config", default=".github/agent-review.yml", help="path in the repo, read at --base")
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--draft", default="false")
    ap.add_argument("--has-key", default="false", help="a Claude credential is available")
    ap.add_argument("--has-api-key", default="false", help="AGENT_REVIEW_API_KEY is available")
    a = ap.parse_args()
    cfg = load_at(a.repo, a.base, a.config)
    files, lines = changed(a.repo, a.base, a.head)
    d = decide(cfg, files, lines, a.draft == "true", a.has_key == "true", a.has_api_key == "true")
    print(json.dumps(d))
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as fh:
            fh.write(f"run={str(d['run']).lower()}\nagents={json.dumps(d['agents'])}\nengine={d['engine']}\n"
                     f"local={str(d['local']).lower()}\n")
    # blocking mode fails closed: a PR that some enabled agent could not review must not pass
    # (drafts can't merge, so they are exempt; an agent out of scope by path is not a gap)
    gap = (not d["run"] and d["reason"] not in ("draft PR", "no agent in scope")) or bool(d.get("missing"))
    if d["reason"]:
        print(f"::{'error' if cfg['blocking'] and gap else 'notice'}::agent-review: {d['reason']}")
    return 1 if cfg["blocking"] and gap else 0


if __name__ == "__main__":
    raise SystemExit(main())
