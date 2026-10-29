"""The privacy lane without an LLM: secret patterns plus Rampart, a small on-device PII model (CC BY 4.0,
National Design Studio, https://huggingface.co/nationaldesignstudio/rampart). No API key, no network after the
model is cached, and nothing from the diff leaves the runner.

To keep code from reading as names, only prose is classified: string literals and comments in code files, and
whole lines in text, docs and data files. Obviously fictional fixtures are masked first.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import secrets
from .privacy import added_lines

JS = Path(__file__).resolve().parent / "js"
TEXT_SUFFIXES = {".md", ".txt", ".rst", ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env",
                 ".xml", ".html", ".htm", ".log", ".sql", ".properties", ""}
BLOCKING = {"SSN", "CREDIT_CARD", "BANK_ACCOUNT", "ROUTING_NUMBER", "TAX_ID", "PASSPORT", "DRIVERS_LICENSE",
            "GOVERNMENT_ID"}
REVIEW = {"GIVEN_NAME": "name", "SURNAME": "name", "EMAIL": "email address", "PHONE": "phone number",
          "BUILDING_NUMBER": "street address", "STREET_NAME": "street address", "SECONDARY_ADDRESS": "street address",
          "IP_ADDRESS": "IP address"}
# URL, CITY, STATE and ZIP_CODE are not reported: too common in code and rarely identifying on their own.

STRINGS = re.compile(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|`([^`]*)`')
COMMENT = re.compile(r"(?:^|\s)(?:#|//|--|;|/\*|\*|<!--)\s?(.*)$")
FICTIONAL = [
    re.compile(r"[\w.+-]+@(?:[\w-]+\.)*(?:example\.(?:com|org|net)|[\w-]+\.(?:invalid|test|example|localhost))\b",
               re.I),
    re.compile(r"[\w.+-]*no-?reply[\w.+-]*@[\w.-]+", re.I),
    re.compile(r"\b(?:Jane|John|Richard|Mary)\s+(?:Q\.\s+)?(?:Doe|Roe|Public)\b", re.I),
    re.compile(r"(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])?555[\s.-]?01\d\d\b"),
]


def prose(path: str, text: str) -> str:
    if Path(path).suffix.lower() in TEXT_SUFFIXES:
        out = text
    else:
        parts = ["".join(g for g in m.groups() if g) for m in STRINGS.finditer(text)]
        code = STRINGS.sub(" ", text)
        if m := COMMENT.search(code):
            parts.append(m.group(1))
        out = " ".join(p for p in parts if p.strip())
    for pat in FICTIONAL:
        out = pat.sub(" ", out)
    out = " ".join(out.split())
    meaningful = any(c.isalpha() for c in out) or sum(c.isdigit() for c in out) >= 7  # words, or ID-length numbers
    return out if len(out) >= 4 and meaningful else ""  # too short to identify anyone


def classify(texts: list[str]) -> list[list[str]]:
    """Rampart labels for each text. Raises if the local model is not installed (the caller fails closed)."""
    if not texts:
        return []
    if not (JS / "node_modules" / "@nationaldesignstudio" / "rampart").exists():
        raise RuntimeError("local PII model not installed: run `npm ci --ignore-scripts` in agent_review/js")
    r = subprocess.run(["node", str(JS / "pii.mjs")], input=json.dumps([{"id": i, "text": t}
                       for i, t in enumerate(texts)]), capture_output=True, text=True, timeout=900, cwd=JS)
    if r.returncode:
        raise RuntimeError(f"local PII model failed: {r.stderr.strip()[-300:]}")
    by_id = {o["id"]: o["labels"] for o in json.loads(r.stdout)}
    return [by_id.get(i, []) for i in range(len(texts))]


def findings(diff: str, classify=classify) -> list[dict]:
    out = secrets.scan(diff)
    secret_lines = {(f["file"], f["line"]) for f in out if f["severity"] == "blocking"}
    rows = [(f, n, p) for f, n, t in added_lines(diff) if (p := prose(f, t)) and (f, n) not in secret_lines]
    for (file, line, _), labels in zip(rows, classify([p for _, _, p in rows]), strict=True):
        hard = sorted({lb for lb in labels if lb in BLOCKING})
        soft = sorted({REVIEW[lb] for lb in labels if lb in REVIEW})
        if hard:
            out.append({"file": file, "line": line, "severity": "blocking", "category": "personal data",
                        "message": f"Looks like {', '.join(h.lower().replace('_', ' ') for h in hard)}. Remove it "
                                   "or replace it with an obviously fictional value."})
        elif soft:
            out.append({"file": file, "line": line, "severity": "suggestion", "category": "personal data",
                        "message": f"Possible real {', '.join(soft)}. Check it is not real personal data; use an "
                                   "obviously fictional value (Jane Doe, jane@example.com, 555-0100) if it is."})
    return out


def result(agent: str, head: str, diff: str) -> dict:
    try:
        fs = findings(diff)
    except (RuntimeError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return {"agent": agent, "sha": head, "status": "error", "verdict": None, "summary": "", "findings": [],
                "cost_usd": 0.0, "error": str(e)[:300]}
    blocking = sum(f["severity"] == "blocking" for f in fs)
    return {"agent": agent, "sha": head, "status": "ok", "verdict": "changes" if blocking else "approve",
            "summary": f"Local scan (secret patterns + on-device PII model): {blocking} blocking, "
                       f"{len(fs) - blocking} to check.", "findings": fs, "cost_usd": 0.0}
