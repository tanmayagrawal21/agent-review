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
    out = subprocess.run(["git", "-C", str(repo), "diff", "--numstat", f"{base}...{head}"], check=True,
                         capture_output=True, text=True).stdout
    files, lines = [], 0
    for row in out.splitlines():
        add, rem, path = row.split("\t", 2)
        files.append(path)
        lines += (int(add) if add.isdigit() else 0) + (int(rem) if rem.isdigit() else 0)
    return files, lines


def decide(cfg: dict, files: list[str], lines: int, draft: bool, has_key: bool) -> dict:
    if not has_key:
        return {"run": False, "agents": [], "reason": "no Claude credential (fork PRs get no secrets)"}
    if draft and cfg["skip"]["draft"]:
        return {"run": False, "agents": [], "reason": "draft PR"}
    if cfg["skip"]["max_changed_lines"] and lines > cfg["skip"]["max_changed_lines"]:
        return {"run": False, "agents": [], "reason": f"{lines} changed lines exceeds skip.max_changed_lines"}
    agents = []
    for name in AGENTS:
        a = cfg["agents"][name]
        if not a.get("enabled"):
            continue
        paths = a.get("paths") or []
        if paths and not any(fnmatch.fnmatch(f, p) for f in files for p in paths):
            continue
        agents.append(name)
    return {"run": bool(agents), "agents": agents, "reason": "" if agents else "no agent in scope"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--config", default=".github/agent-review.yml", help="path in the repo, read at --base")
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--draft", default="false")
    ap.add_argument("--has-key", default="false")
    a = ap.parse_args()
    cfg = load_at(a.repo, a.base, a.config)
    files, lines = changed(a.repo, a.base, a.head)
    d = decide(cfg, files, lines, a.draft == "true", a.has_key == "true")
    print(json.dumps(d))
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as fh:
            fh.write(f"run={str(d['run']).lower()}\nagents={json.dumps(d['agents'])}\nreason={d['reason']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
