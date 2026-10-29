"""Benchmark: each case becomes a git repo (base commit carries the setup's config, head commit the PR) and runs
through the real pipeline: prepare (real Rampart) -> review per agent -> publish --dry-run. Findings are scored
against the planted lines.

    uv run python bench/run.py --setup local --out bench/results/local.json
    uv run python bench/run.py --setup cloud --out bench/results/cloud.json

Cloud reads the key from the macOS Keychain (service "agent-review-api-key"; add it with
`security add-generic-password -a "$USER" -s agent-review-api-key -w`), and the endpoint and model from
AGENT_REVIEW_BASE_URL / AGENT_REVIEW_MODEL, so none of them is typed on a command line or written to the results.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cases import CASES  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ["reviewer", "privacy", "safety"]


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def build_repo(d: Path, case: dict, config: str) -> tuple[str, str]:
    git(d, "init", "-q", "-b", "main")
    git(d, "config", "user.email", "bench@example.com")
    git(d, "config", "user.name", "bench")
    (d / ".github").mkdir()
    (d / ".github" / "agent-review.yml").write_text(config)
    for files in (case["base"], case["head"]):
        for name, text in files.items():
            (d / name).parent.mkdir(parents=True, exist_ok=True)
            (d / name).write_text(text)
        git(d, "add", "-A")
        git(d, "commit", "-qm", "base" if files is case["base"] else case["title"])
    return git(d, "rev-parse", "HEAD~1"), git(d, "rev-parse", "HEAD")


def py(*args, env):
    return subprocess.run([sys.executable, "-m", *args], cwd=ROOT, env=env, capture_output=True, text=True)


def run_case(cid: str, case: dict, config: str, parallel: bool) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        repo, out = Path(tmp) / "repo", Path(tmp)
        repo.mkdir()
        base, head = build_repo(repo, case, config)
        env = {**os.environ, "HAS_KEY": "true", "PR_TITLE": case["title"], "PR_BODY": case["body"]}
        t0 = time.time()
        py("agent_review.prepare", "--repo", str(repo), "--base", base, "--head", head,
           "--out", str(out / "bundle.json"), env=env)
        prep_s = round(time.time() - t0, 1)
        bundle = json.loads((out / "bundle.json").read_text())

        def agent(name):
            t = time.time()
            py("agent_review.review", "--agent", name, "--bundle", str(out / "bundle.json"),
               "--out", str(out / "findings" / f"findings-{name}" / f"{name}.json"), env=env)
            return name, round(time.time() - t, 1)
        with ThreadPoolExecutor(3 if parallel else 1) as pool:
            seconds = dict(pool.map(agent, bundle["agents"]))
        review = json.loads(py("agent_review.publish", "--bundle", str(out / "bundle.json"), "--findings",
                               str(out / "findings"), "--dry-run", env=env).stdout)
        rs = {a: json.loads(p.read_text()) for a in bundle["agents"]
              if (p := out / "findings" / f"findings-{a}" / f"{a}.json").exists()}
        lines = {f: case["head"][f].splitlines() for f in case["head"]}
        expected = [(f, lines[f].index(code) + 1, lane) for f, code, lane in case["expect"]]
        placed: dict[tuple, set] = {}
        for c in review["comments"]:
            placed.setdefault((c["path"], c["line"]), set()).add("blocking" if "**blocking**" in c["body"] else "s")
        exp = {(f, n) for f, n, _ in expected}
        sent = {k: bundle[k] for k in ("diff", "log", "title", "body", "files")}  # what a model can receive
        leaked = [w for w in case.get("never_sent", []) if w in json.dumps(sent)]
        return {"case": cid, "expected": len(expected), "clean": not expected,
                "caught": sum((f, n) in placed for f, n, _ in expected),
                "blocked": sum("blocking" in placed.get((f, n), set()) for f, n, _ in expected),
                "stray_blocking": sum("blocking" in v and k not in exp for k, v in placed.items()),
                "missed": [lane for f, n, lane in expected if (f, n) not in placed],
                "errors": [a for a in bundle["agents"] if a not in rs or rs[a]["status"] != "ok"],
                "sent_unredacted": leaked,
                "tokens": {k: sum(r["tokens"].get(k, 0) for r in rs.values()) for k in ("input", "cached", "output")},
                "seconds": {"prepare": prep_s, **seconds}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", choices=["local", "cloud"], required=True)
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--api", default="openai")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.setup == "local":
        os.environ.setdefault("AGENT_REVIEW_API_KEY", "ollama")
        config = f"api: {a.api}\nbase_url: {a.base_url}\nmodel: {a.model}\n"
    else:
        if not os.environ.get("AGENT_REVIEW_API_KEY"):
            os.environ["AGENT_REVIEW_API_KEY"] = subprocess.run(
                ["security", "find-generic-password", "-s", "agent-review-api-key", "-w"],
                capture_output=True, text=True, check=True).stdout.strip()
        if not (os.environ.get("AGENT_REVIEW_BASE_URL") and os.environ.get("AGENT_REVIEW_MODEL")):
            raise SystemExit("set AGENT_REVIEW_BASE_URL and AGENT_REVIEW_MODEL for the cloud setup")
        config, a.model = f"api: {a.api}\n", "(from AGENT_REVIEW_MODEL)"  # never recorded in the results
    rows = [run_case(cid, case, config, a.setup == "cloud") for cid, case in CASES.items()]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"setup": a.setup, "model": a.model, "rows": rows}, indent=2))
    exp = sum(r["expected"] for r in rows)
    tok = {k: sum(r["tokens"][k] for r in rows) for k in ("input", "cached", "output")}
    print(f"{a.setup} ({a.model}): caught {sum(r['caught'] for r in rows)}/{exp}, "
          f"blocked {sum(r['blocked'] for r in rows)}/{exp}, "
          f"false blocks on clean PRs {sum(r['stray_blocking'] for r in rows if r['clean'])}, "
          f"errors {sum(len(r['errors']) for r in rows)}, "
          f"unredacted sends {sum(len(r['sent_unredacted']) for r in rows)}, "
          f"tokens {tok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
