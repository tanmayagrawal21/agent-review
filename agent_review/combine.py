"""Merge the agents' findings into ONE pull-request review: a summary with a section per agent, plus inline
comments on changed lines. Plain code, not an agent. Every posted text is redacted against the private terms.

Exit status: 0, or 1 when `blocking: true` and an agent requested changes or failed (fail closed).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from .config import AGENTS, load_at
from .privacy import load_terms, redact, scan_added

MARKER = "<!-- agent-review -->"
MAX_BODY = 60_000  # GitHub's limit is 65,536 characters
TITLES = {"reviewer": "Reviewer", "privacy": "Privacy", "safety": "Safety", "scanner": "Private-terms scan"}


def commentable(diff: str) -> dict[str, set[int]]:
    """Lines on the new side of each hunk (added or context): the only places GitHub accepts inline comments."""
    out: dict[str, set[int]] = {}
    file, line = "", 0
    prev = ""
    for raw in diff.splitlines():
        header, prev = raw.startswith("+++ ") and prev.startswith("--- "), raw
        if header:
            file = raw[6:] if raw.startswith("+++ b/") else ""
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line = int(m.group(1)) if m else 0
        elif file and (raw.startswith("+") or raw.startswith(" ")):
            out.setdefault(file, set()).add(line)
            line += 1
    return out


def scanner_result(diff: str, terms: list[str], sha: str) -> dict:
    hits = scan_added(diff, terms)
    return {"agent": "scanner", "sha": sha, "status": "ok", "verdict": "changes" if hits else "approve",
            "summary": f"{len(hits)} private-term match(es) in added lines." if hits else "No private terms.",
            "findings": [{"file": h.file, "line": h.line, "severity": "blocking", "category": "private term",
                          "message": f"Matches private term #{h.term}. Remove it before merging."} for h in hits]}


def group(results: list[dict]) -> list[tuple[str, int | None, list[tuple[str, dict]]]]:
    """Findings grouped by (file, line): agents often describe one issue at one line in different words, so a
    line gets ONE comment holding every agent's finding. Findings without a line are never grouped."""
    out: list[tuple[str, int | None, list[tuple[str, dict]]]] = []
    index: dict[tuple, int] = {}
    for r in results:
        for f in r["findings"]:
            key = (f.get("file") or "", f.get("line"))
            if key[1] and key in index:
                out[index[key]][2].append((r["agent"], f))
                continue
            if key[1]:
                index[key] = len(out)
            out.append((key[0], key[1], [(r["agent"], f)]))
    return out


def _clip(text: str) -> str:
    return text if len(text) <= MAX_BODY else text[:MAX_BODY] + "\n\n…(truncated)"


def _item(agent: str, f: dict) -> str:
    cat = f" · {f['category']}" if f.get("category") else ""
    return f"**{f['severity']}** ({TITLES.get(agent, agent)}{cat}): {f['message']}"


def build(results: list[dict], diff: str, sha: str, terms: list[str]) -> tuple[dict, bool]:
    lines = commentable(diff)
    order = {a: i for i, a in enumerate([*AGENTS, "scanner"])}
    results = sorted(results, key=lambda r: order.get(r["agent"], 99))
    failed = any(r["status"] != "ok" or r["verdict"] == "changes" for r in results)
    head = [MARKER, f"## Agent review of `{sha[:7]}`", "",
            "| agent | result | blocking | suggestions |", "|---|---|---|---|"]
    for r in results:
        fs = r["findings"]
        res = "error" if r["status"] != "ok" else ("changes requested" if r["verdict"] == "changes" else "approved")
        blocking = sum(f["severity"] == "blocking" for f in fs)
        head.append(f"| {TITLES.get(r['agent'], r['agent'])} | {res} | {blocking} | {len(fs) - blocking} |")
    comments, body_items = [], []
    for file, line, items in group(results):
        if line and line in lines.get(file, set()):
            text = _item(*items[0]) if len(items) == 1 else "\n".join(f"- {_item(a, f)}" for a, f in items)
            comments.append({"path": file, "line": line, "side": "RIGHT", "body": text})
        else:
            where = f"`{file}:{line}`" if line else f"`{file or '(PR)'}`"
            body_items += [f"- {where} {_item(a, f)}" for a, f in items]
    sections = []
    for r in results:
        sections.append(f"\n### {TITLES.get(r['agent'], r['agent'])}\n" +
                        (r["summary"] if r["status"] == "ok" else f"Did not complete: {r.get('error', 'error')}. "
                         "Treated as not approved."))
    body = "\n".join(head + sections)
    if body_items:
        body += "\n\n### Findings outside the changed lines\n" + "\n".join(body_items)
    body += "\n\n<sub>Automated and advisory: these agents read the diff but do not run the code. A human decides " \
            "the merge.</sub>"
    review = {"commit_id": sha, "event": "COMMENT", "body": _clip(redact(body, terms)),
              "comments": [{**c, "body": redact(c["body"], terms)} for c in comments]}
    return review, failed


def gh(*args: str, payload: dict | None = None) -> str:
    r = subprocess.run(["gh", "api", *args, *(["--input", "-"] if payload is not None else [])],
                       input=json.dumps(payload) if payload is not None else None, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:500])
    return r.stdout


def post(repo: str, pr: int, review: dict) -> None:
    base = f"repos/{repo}/pulls/{pr}/reviews"
    pages = json.loads(gh(base, "--paginate", "--slurp") or "[]")
    old = [rv for page in pages for rv in page if MARKER in (rv.get("body") or "")
           and (rv.get("user") or {}).get("login") == "github-actions[bot]"
           and rv.get("commit_id") != review["commit_id"]]
    try:
        gh(base, "--method", "POST", payload=review)
    except RuntimeError:  # e.g. a line GitHub will not anchor: post everything in the body instead
        extra = "\n".join(f"- `{c['path']}:{c['line']}` {c['body']}" for c in review["comments"])
        gh(base, "--method", "POST", payload={**review, "comments": [],
                                               "body": _clip(review["body"] + "\n\n### Inline findings\n" + extra)})
    for rv in old:  # best effort: collapse earlier reviews so only the latest one reads as current
        try:
            gh(f"{base}/{rv['id']}", "--method", "PUT",
               payload={"body": f"{MARKER}\nSuperseded by the agent review of `{review['commit_id'][:7]}`."})
        except RuntimeError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", type=Path, required=True)
    ap.add_argument("--config", default=".github/agent-review.yml", help="path in the repo, read at --base")
    ap.add_argument("--findings", type=Path, required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--agents", default="[]", help="JSON list of agents that were planned")
    ap.add_argument("--terms", type=Path)
    ap.add_argument("--pr", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = load_at(a.repo_dir, a.base, a.config)
    terms = load_terms(a.terms)
    diff = subprocess.run(["git", "-C", str(a.repo_dir), "diff", f"{a.base}...{a.head}"], check=True,
                          capture_output=True, text=True).stdout
    results = []
    for agent in json.loads(a.agents):
        p = a.findings / f"findings-{agent}" / f"{agent}.json"  # each agent's own artifact, never merged
        r = json.loads(p.read_text()) if p.exists() else None
        if not r or r.get("sha") != a.head or r.get("agent") != agent:  # missing, stale or forged: fail closed
            r = {"agent": agent, "sha": a.head, "status": "error", "verdict": None, "summary": "", "findings": [],
                 "error": "no result for this commit"}
        r["findings"] = [{**f, "severity": "blocking" if f.get("severity") == "blocking" else "suggestion",
                          "message": str(f.get("message", ""))} for f in r.get("findings") or [] if isinstance(f, dict)]
        results.append(r)
    if "privacy" in json.loads(a.agents) and terms:
        results.append(scanner_result(diff, terms, a.head))
    review, failed = build(results, diff, a.head, terms)
    if a.dry_run or not a.pr:
        print(json.dumps(review, indent=2))
    else:
        post(os.environ["GITHUB_REPOSITORY"], a.pr, review)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(summary).write_text(review["body"])
    return 1 if failed and cfg["blocking"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
