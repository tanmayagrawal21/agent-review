"""Step 3. Merges the local findings and each agent's findings into ONE pull-request review: a summary table and
inline comments. Plain code, not an agent. Works from the redacted bundle alone (no checkout).

    python -m agent_review.publish --bundle bundle.json --findings findings/ --pr 12 [--dry-run]

Exit status: 0, or 1 when `blocking: true` and something requested changes or did not complete.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from . import pii
from .config import AGENTS
from .diff import commentable

MARKER = "<!-- agent-review -->"
MAX_BODY = 60_000  # GitHub's limit is 65,536 characters
TITLES = {"reviewer": "Reviewer", "privacy": "Privacy", "safety": "Safety"}
ZERO = {"input": 0, "cached": 0, "output": 0}


def results(bundle: dict, findings_dir: Path) -> list[dict]:
    """One result per agent that ran, plus privacy's local half. Missing, stale or forged results fail closed."""
    out = []
    for agent in bundle["agents"]:
        p = findings_dir / f"findings-{agent}" / f"{agent}.json"
        r = json.loads(p.read_text()) if p.exists() else None
        if not r or r.get("sha") != bundle["head"] or r.get("agent") != agent:
            r = {"agent": agent, "status": "error", "verdict": None, "summary": "", "findings": [], "tokens": ZERO,
                 "error": "no result for this commit"}
        r["findings"] = [{**f, "severity": "blocking" if f.get("severity") == "blocking" else "suggestion",
                          "message": str(f.get("message", ""))} for f in r.get("findings") or [] if isinstance(f, dict)]
        out.append(r)
    if bundle.get("privacy"):
        local = bundle["local_findings"]
        llm = next((r for r in out if r["agent"] == "privacy"), None)
        summary = f"Local scan (Rampart on-device PII model + secret patterns): {len(local)} finding(s)."
        if llm is None:
            out.append({"agent": "privacy", "status": "ok", "summary": summary + " No model pass (no key).",
                        "findings": local, "tokens": ZERO, "engine": "local",
                        "verdict": "changes" if any(f["severity"] == "blocking" for f in local) else "approve"})
        else:
            llm["findings"] = local + llm["findings"]
            llm["summary"] = f"{summary} {llm['summary']}".strip()
            if llm["status"] == "ok" and any(f["severity"] == "blocking" for f in local):
                llm["verdict"] = "changes"
    return sorted(out, key=lambda r: AGENTS.index(r["agent"]))


def anchor(findings: list[dict], files: dict[str, str]) -> list[dict]:
    """Models misnumber lines; their quoted `code` does not lie. Move each finding to the line holding that text,
    the nearest one when it occurs more than once."""
    out = []
    for f in findings:
        code, text = (f.get("code") or "").strip(), files.get(f.get("file") or "")
        if code and text:
            hits = [i for i, ln in enumerate(text.split("\n"), 1) if ln.strip() == code]
            if hits:
                f = {**f, "line": min(hits, key=lambda i: abs(i - (f.get("line") or 0)))}
        out.append(f)
    return out


def _item(agent: str, f: dict) -> str:
    cat = f" · {f['category']}" if f.get("category") else ""
    return f"**{f['severity']}** ({TITLES[agent]}{cat}): {f['message']}"


def build(rs: list[dict], bundle: dict) -> tuple[dict, bool]:
    lines = commentable(bundle["diff"])
    failed = any(r["status"] != "ok" or r["verdict"] == "changes" for r in rs)
    rows, total = [], dict(ZERO)
    for r in rs:
        fs, t = r["findings"], {k: int((r.get("tokens") or {}).get(k) or 0) for k in ZERO}
        total = {k: total[k] + t[k] for k in ZERO}
        res = "did not complete" if r["status"] != "ok" else (
            "changes requested" if r["verdict"] == "changes" else "approved")
        blocking = sum(f["severity"] == "blocking" for f in fs)
        rows.append(f"| {TITLES[r['agent']]} | {res} | {blocking} | {len(fs) - blocking} | "
                    f"{t['input']:,} / {t['cached']:,} / {t['output']:,} |")
    head = [MARKER, f"## Agent review of `{bundle['head'][:7]}`", "",
            "| agent | result | blocking | suggestions | tokens in / cached / out |", "|---|---|---|---|---|", *rows,
            f"| **total** | | | | **{total['input']:,} / {total['cached']:,} / {total['output']:,}** |"]
    by_line: dict[tuple, list] = {}
    loose = []
    for r in rs:
        for f in anchor(r["findings"], bundle["files"]):
            key = (f.get("file") or "", f.get("line"))
            if key[1] and key[1] in lines.get(key[0], set()):
                by_line.setdefault(key, []).append((r["agent"], f))
            else:
                where = f"`{key[0]}:{key[1]}`" if key[1] else f"`{key[0] or '(PR)'}`"
                loose.append(f"- {where} {_item(r['agent'], f)}")
    comments = [{"path": p, "line": n, "side": "RIGHT",
                 "body": _item(*items[0]) if len(items) == 1 else "\n".join(f"- {_item(a, f)}" for a, f in items)}
                for (p, n), items in by_line.items()]
    if not rs:  # nothing could run: say why instead of staying silent
        head = head[:2] + ["", f"**Not reviewed:** {bundle.get('notice') or 'no agent could run'}."]
        failed = True
    elif bundle.get("notice"):
        head += ["", f"> Note: {bundle['notice']}."]
    body = "\n".join(head)
    for r in rs:
        body += f"\n\n### {TITLES[r['agent']]}\n" + (r["summary"] if r["status"] == "ok" else
                                                     f"Did not complete: {r.get('error', 'error')}. Not approved.")
    if loose:
        body += "\n\n### Findings outside the changed lines\n" + "\n".join(loose)
    body += ("\n\n<sub>Automated and advisory. Agents read a locally redacted copy of the diff and never run the "
             "code. A human decides the merge.</sub>")
    return {"commit_id": bundle["head"], "event": "COMMENT", "body": body[:MAX_BODY], "comments": comments}, failed


def gh(*args: str, payload: dict | None = None) -> str:
    r = subprocess.run(["gh", "api", *args, *(["--input", "-"] if payload is not None else [])],
                       input=json.dumps(payload) if payload is not None else None, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:500])
    return r.stdout


def post(repo: str, pr: int, review: dict) -> None:
    base = f"repos/{repo}/pulls/{pr}/reviews"
    old = [rv for page in json.loads(gh(base, "--paginate", "--slurp") or "[]") for rv in page
           if MARKER in (rv.get("body") or "") and (rv.get("user") or {}).get("login") == "github-actions[bot]"
           and rv.get("commit_id") != review["commit_id"]]
    try:
        gh(base, "--method", "POST", payload=review)
    except RuntimeError:  # a line GitHub will not anchor: put everything in the body
        extra = "\n".join(f"- `{c['path']}:{c['line']}` {c['body']}" for c in review["comments"])
        body = (review["body"] + "\n\n### Inline findings\n" + extra)[:MAX_BODY]
        gh(base, "--method", "POST", payload={**review, "comments": [], "body": body})
    for rv in old:  # best effort: only the latest review reads as current
        try:
            gh(f"{base}/{rv['id']}", "--method", "PUT",
               payload={"body": f"{MARKER}\nSuperseded by the agent review of `{review['commit_id'][:7]}`."})
        except RuntimeError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--findings", type=Path, required=True)
    ap.add_argument("--pr", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    bundle = json.loads(a.bundle.read_text())
    review, failed = build(results(bundle, a.findings), bundle)
    review = json.loads(pii.scrub(json.dumps(review), []))  # last line of defence on everything posted
    if a.dry_run or not a.pr:
        print(json.dumps(review, indent=2))
    else:
        post(os.environ["GITHUB_REPOSITORY"], a.pr, review)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(summary).write_text(review["body"])
    return 1 if failed and bundle["config"]["blocking"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
