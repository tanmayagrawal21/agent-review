"""Step 1, the only step that sees the PR checkout. Reads the config at the base commit, decides which agents
run, scans and redacts every text locally, and writes bundle.json: redacted text plus the local findings.
Review jobs read only that bundle.

    python -m agent_review.prepare --repo pr --base <sha> --head <sha> --out bundle.json
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
from pathlib import Path

from . import pii
from .config import AGENTS, load
from .diff import HUNK, changed, git, walk


def decide(cfg: dict, files: list[str], lines: int, draft: bool, has_key: bool, has_url: bool = False,
           has_model: bool = False) -> dict:
    """Which LLM agents run, whether the local privacy scan runs, and any gap (an enabled agent that can't)."""
    plan = _decide(cfg, files, lines, draft, has_key, has_url, has_model)
    warn = cfg["warn_changed_lines"]
    plan["warning"] = (f"this is a long PR ({lines} changed lines); review quality drops on large changes, "
                       "so consider splitting it") if warn and lines > warn else ""
    return plan


def _decide(cfg: dict, files: list[str], lines: int, draft: bool, has_key: bool, has_url: bool,
            has_model: bool) -> dict:
    if draft and cfg["skip"]["draft"]:
        return {"agents": [], "local": False, "gap": [], "reason": "draft PR"}
    if cfg["skip"]["max_changed_lines"] and lines > cfg["skip"]["max_changed_lines"]:
        return {"agents": [], "local": False, "gap": list(AGENTS),
                "reason": f"{lines} changed lines exceeds skip.max_changed_lines"}
    in_scope = [a for a in AGENTS if cfg["agents"][a]["enabled"] and (
        not cfg["agents"][a]["paths"] or any(fnmatch.fnmatch(f, p) for f in files for p in cfg["agents"][a]["paths"]))]
    usable = has_key and (bool(cfg["model"]) or has_model) and (bool(cfg["base_url"]) or has_url)
    gap = [] if usable else in_scope
    reason = "" if usable or not in_scope else "no model configured or no key (fork PRs get no secrets)"
    return {"agents": in_scope if usable else [], "local": "privacy" in in_scope, "gap": gap, "reason": reason}


def text_files(repo: Path, head: str, files: list[str]) -> dict[str, str]:
    out = {}
    for f in files:
        r = subprocess.run(["git", "-C", str(repo), "show", f"{head}:{f}"], capture_output=True)
        if r.returncode == 0 and b"\0" not in r.stdout[:8000]:
            out[f] = r.stdout.decode("utf-8", "replace")
    return out


def build(repo: Path, base: str, head: str, cfg: dict, plan: dict, title: str, body: str,
          run=pii.run_model) -> dict:
    diff = git(repo, "diff", f"{base}...{head}")
    log = git(repo, "log", "--format=%h %s%n%b", f"{base}..{head}")
    files, _ = changed(repo, base, head)
    texts = text_files(repo, head, files)
    rows = list(walk(diff))
    items = [(f, t) for f, kind, _, t in rows if kind != "meta"]  # diff content lines first, in order
    ranges: dict[str, tuple[int, int]] = {}

    def add(key: str, path: str, text: str) -> None:
        lines = text.split("\n")
        ranges[key] = (len(items), len(items) + len(lines))
        items.extend((path, ln) for ln in lines)

    for key, text in (("title", title), ("body", body), ("log", log)):
        add(key, pii.PROSE, text)
    for f, text in texts.items():
        add(f"file:{f}", f, text)
    redacted, found = pii.process(items, run)
    get = lambda key: "\n".join(redacted[slice(*ranges[key])])  # noqa: E731

    i, new_diff, local = 0, [], []
    for f, kind, n, t in rows:
        if kind == "meta":  # git appends a nearby source line to hunk headers: keep only the line ranges
            m = HUNK.match(t)
            new_diff.append(m.group(0) if m else t)
            continue
        new_diff.append(kind + redacted[i])
        if kind == "+" and f:
            local += pii.findings(f, n, found[i])
        i += 1
    r_title, r_body, r_log = get("title"), get("body"), get("log")
    r_files = {f: get(f"file:{f}") for f in texts}
    keep = cfg["redact"]
    bundle = {"base": base, "head": head, "agents": plan["agents"], "redacted": keep,
              "diff": "\n".join(new_diff) if keep else diff, "log": r_log if keep else log,
              "title": r_title if keep else title, "body": r_body if keep else body,
              "files": r_files if keep else texts, "local_findings": local,
              "config": {k: cfg[k] for k in ("api", "base_url", "model", "instructions", "max_context_chars",
                                             "blocking")}}
    if plan["agents"] and len(bundle["diff"]) > cfg["max_context_chars"]:
        plan["gap"] = sorted(set(plan["gap"]) | set(plan["agents"]))
        bundle["agents"] = plan["agents"] = []  # the job outputs come from the plan: clear both
        plan["reason"] = f"diff is {len(bundle['diff'])} chars, over max_context_chars: not sent to a model"
    return bundle


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--config", default=".github/agent-review.yml", help="path in the repo, read at --base")
    ap.add_argument("--draft", default="false")
    a = ap.parse_args()
    cfg = load(a.repo, a.base, a.config)
    files, lines = changed(a.repo, a.base, a.head)
    plan = decide(cfg, files, lines, a.draft == "true", os.environ.get("HAS_KEY") == "true",
                  os.environ.get("HAS_BASE_URL") == "true", os.environ.get("HAS_MODEL") == "true")
    run = bool(plan["agents"] or plan["local"] or plan["gap"])  # a skipped review still says so on the PR
    if run:
        if plan["agents"] or plan["local"]:
            bundle = build(a.repo, a.base, a.head, cfg, plan, os.environ.get("PR_TITLE", ""),
                           os.environ.get("PR_BODY", ""))
        else:  # nothing may run (e.g. too large): a bundle that carries only the reason, no PR content
            bundle = {"base": a.base, "head": a.head, "agents": [], "redacted": True, "diff": "", "files": {},
                      "local_findings": [], "config": {"blocking": cfg["blocking"]}}
        bundle["privacy"], bundle["notice"], bundle["warning"] = plan["local"], plan["reason"], plan["warning"]
        bundle["gap"] = plan["gap"]  # enabled agents that could not run: shown as "not reviewed", never omitted
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(bundle))
    print(json.dumps({k: plan[k] for k in ("agents", "local", "gap", "reason")}))
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as fh:
            fh.write(f"run={str(run).lower()}\nagents={json.dumps(plan['agents'])}\n")
    if plan["reason"]:
        fail = cfg["blocking"] and plan["gap"]
        print(f"::{'error' if fail else 'notice'}::agent-review: {plan['reason']}")
    return 1 if cfg["blocking"] and plan["gap"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
