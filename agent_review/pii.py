"""One local pass over every text a model could see: it redacts, and it records what it found. It runs on the
Actions runner; nothing it sees leaves the machine.

Per line: provider-format secrets -> [SECRET] (Rampart is a personal-data model and does not detect keys), then
Rampart, the on-device PII model (https://huggingface.co/nationaldesignstudio/rampart), replaces personal data
with stable placeholders ([GIVEN_NAME_1], [EMAIL_1], ...). Every line of every file is redacted whole: no
parsing of strings or comments that a heredoc or an unusual quote could defeat. Obviously fictional fixtures
(example.com, Jane Doe, 555-01xx) are left alone. Findings are stricter than redaction: in code files a
detection is reported only inside a string or comment, or when it looks like data (several words, digits, @),
so an identifier such as `django` may be masked for the model but is not reported as a name. Line structure is
preserved, so line numbers
stay valid. If the model cannot run this raises, and the caller fails closed: nothing is sent unredacted.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

JS = Path(__file__).resolve().parent / "js"
PROSE = "PR.md"  # path used for the title, description and commit messages
# Source code: only string literals and comments are classified. Every other file (docs, data, config, .vcf,
# .eml, unknown types) is classified whole-line, so an unrecognised format can't slip personal data through.
CODE_SUFFIXES = {".py", ".pyi", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".kts",
                 ".scala", ".groovy", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".cs", ".fs", ".rb", ".php", ".swift",
                 ".m", ".mm", ".dart", ".lua", ".pl", ".r", ".jl", ".ex", ".exs", ".erl", ".hs", ".clj", ".elm",
                 ".vue", ".svelte"}  # shell scripts are text: their arguments are bare words

SECRETS = [(name, re.compile(p)) for name, p in [
    ("Anthropic API key", r"sk-ant-[A-Za-z0-9_-]{20,}"),
    ("OpenAI API key", r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}"),
    ("Stripe secret key", r"[sr]k_live_[A-Za-z0-9]{16,}"),
    ("GitHub token", r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"),
    ("GitLab token", r"glpat-[A-Za-z0-9_-]{20,}"),
    ("AWS access key", r"(?:AKIA|ASIA)[0-9A-Z]{16}"),
    ("Google API key", r"AIza[0-9A-Za-z_-]{35}"),
    ("Slack token", r"xox[abposr]-[A-Za-z0-9-]{10,}"),
    ("private key", r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    ("credentials in a URL", r"(?<=://)[^/\s:@]+:[^/\s:@]{3,}(?=@)"),
]]
STRINGS = re.compile(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|`([^`]*)`')
COMMENT = re.compile(r"(?:^|\s)(?:#|//|--|;|/\*|\*|<!--)\s?(.*)$")
FICTIONAL = re.compile("|".join([
    r"[\w.+-]+@(?:[\w-]+\.)*(?:example\.(?:com|org|net)|[\w-]+\.(?:invalid|test|example|localhost))\b",
    r"[\w.+-]*no-?reply[\w.+-]*@[\w.-]+",
    r"\b(?:Jane|John|Richard|Mary)\s+(?:Q\.\s+)?(?:Doe|Roe|Public)\b",
    r"(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])?555[\s.-]?01\d\d\b",
]), re.I)
BLOCKING = {"SSN", "CREDIT_CARD", "BANK_ACCOUNT", "ROUTING_NUMBER", "TAX_ID", "PASSPORT", "DRIVERS_LICENSE",
            "GOVERNMENT_ID"}
REVIEW = {"GIVEN_NAME": "name", "SURNAME": "name", "EMAIL": "email address", "PHONE": "phone number",
          "BUILDING_NUMBER": "street address", "STREET_NAME": "street address", "SECONDARY_ADDRESS": "street address",
          "IP_ADDRESS": "IP address"}


def prose(path: str, line: str) -> list[tuple[int, int]]:
    """Ranges of `line` that are prose: all of it in non-code files, else string literals and comments."""
    if Path(path).suffix.lower() not in CODE_SUFFIXES:
        return [(0, len(line))]
    blanked = STRINGS.sub(lambda m: " " * len(m.group(0)), line)  # comment markers inside strings don't count
    comment = COMMENT.search(blanked)
    start = comment.start(1) if comment else len(line)
    out = [(m.start(g), m.end(g)) for m in STRINGS.finditer(line) for g in (1, 2, 3)
           if m.group(g) and m.end(g) <= start]
    return out + ([(start, len(line))] if comment else [])


def reportable(path: str, line: str, a: int, b: int) -> bool:
    """Whether a detection at line[a:b] is reported (it is redacted either way)."""
    t = line[a:b]
    return (any(x <= a and b <= y for x, y in prose(path, line)) or " " in t.strip() or "@" in t
            or any(c.isdigit() for c in t))


PLACE = re.compile(r"\[([A-Z_]+?)_\d+\]")


def replaced(orig: str, red: str) -> list[tuple[int, int, str]]:
    """(start, end, label) of each original span replaced by a placeholder. The text between placeholders is
    unchanged, so each literal chunk of `red` is found in `orig` in order; what lies between is replaced."""
    out, i_o, i_r = [], 0, 0
    marks = list(PLACE.finditer(red))
    for n, m in enumerate(marks):
        before = red[i_r:m.start()]
        start = orig.find(before, i_o) + len(before) if before else i_o
        after = red[m.end():marks[n + 1].start() if n + 1 < len(marks) else len(red)]
        end = orig.find(after, start) if after else (len(orig) if n + 1 == len(marks) else start)
        if start < len(before) or end < start:
            return []  # could not align: the caller is conservative
        out.append((start, end, m.group(1)))
        i_o, i_r = end, m.end()
    return out


def pieces(line: str, a: int, b: int) -> list[tuple[int, int]]:
    """Sub-ranges of [a, b) outside fictional fixtures, worth classifying (words, or ID-length numbers)."""
    out, cur = [], a
    for m in FICTIONAL.finditer(line, a, b):
        out.append((cur, m.start()))
        cur = m.end()
    out.append((cur, b))
    keep = []
    for x, y in out:
        t = line[x:y].strip()
        if len(t) >= 3 and (any(c.isalpha() for c in t) or sum(c.isdigit() for c in t) >= 7):
            keep.append((x, y))
    return keep


def run_model(texts: list[str]) -> list[tuple[str, list[str]]]:
    if not texts:
        return []
    if not (JS / "node_modules" / "@nationaldesignstudio" / "rampart").exists():
        raise RuntimeError("local PII model not installed: run `npm ci --ignore-scripts` in agent_review/js")
    r = subprocess.run(["node", str(JS / "pii.mjs")], cwd=JS, capture_output=True, text=True, timeout=1800,
                       input=json.dumps([{"id": i, "text": t} for i, t in enumerate(texts)]),
                       env={k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG") if k in os.environ})
    if r.returncode:
        raise RuntimeError(f"local PII model failed: {r.stderr.strip()[-300:]}")
    by_id = {o["id"]: (o["text"], o["labels"]) for o in json.loads(r.stdout)}
    if set(by_id) != set(range(len(texts))):
        raise RuntimeError("local PII model returned an incomplete result")
    return [by_id[i] for i in range(len(texts))]


def process(items: list[tuple[str, str]], run=run_model) -> tuple[list[str], list[dict]]:
    """Redact (path, line) pairs in one batch. Returns the redacted lines and, per line, what was found:
    {"secrets": [names], "labels": [reportable Rampart labels]}."""
    lines, found = [], []
    for _, text in items:
        hit = {"secrets": [], "labels": []}
        for name, pat in SECRETS:
            if pat.search(text):
                hit["secrets"].append(name)
                text = pat.sub("[SECRET]", text)
        lines.append(text)
        found.append(hit)
    segs = [(i, x, y) for i, line in enumerate(lines) for x, y in pieces(line, 0, len(line))]
    results = run([lines[i][x:y] for i, x, y in segs])
    original = list(lines)
    for (i, x, y), (new, labels) in sorted(zip(segs, results, strict=True), key=lambda s: (s[0][0], -s[0][1])):
        orig = original[i][x:y]
        spans = replaced(orig, new) or [(0, len(orig), lb) for lb in labels]  # alignment failed: be conservative
        groups: list[list] = []  # adjacent spans ("Priya" + "Ramanathan") are one detection
        for a, b, lb in spans:
            if groups and not orig[groups[-1][1]:a].strip():
                groups[-1][1], groups[-1][2] = b, groups[-1][2] + [lb]
            else:
                groups.append([a, b, [lb]])
        found[i]["labels"] += [lb for a, b, lbs in groups if reportable(items[i][0], original[i], x + a, x + b)
                               for lb in lbs]
        lines[i] = lines[i][:x] + new.replace("\n", " ") + lines[i][y:]  # right to left keeps offsets valid
    return lines, found


def findings(file: str, line: int, hit: dict) -> list[dict]:
    """What the local scan reports for one added line."""
    out = [{"file": file, "line": line, "severity": "blocking", "category": name,
            "message": f"Looks like a {name} in a real provider format. Remove it, rotate it if it was ever real, "
                       "and load it from a secret store; use an obviously fake value in tests."}
           for name in hit["secrets"]]
    hard = sorted({lb for lb in hit["labels"] if lb in BLOCKING} - ({"CREDIT_CARD"} if hit["secrets"] else set()))
    soft = sorted({REVIEW[lb] for lb in hit["labels"] if lb in REVIEW})
    if hard:
        out.append({"file": file, "line": line, "severity": "blocking", "category": "personal data",
                    "message": f"Looks like {', '.join(h.lower().replace('_', ' ') for h in hard)}. Remove it or use "
                               "an obviously fictional value."})
    elif soft:
        out.append({"file": file, "line": line, "severity": "suggestion", "category": "personal data",
                    "message": f"Possible real {', '.join(soft)}. Check it is not real personal data; use an obviously "
                               "fictional value (Jane Doe, jane@example.com, 555-0100) if it is."})
    return out


def scrub(text: str, secrets: list[str]) -> str:
    """Last line of defence on anything posted: exact credential values and provider key formats."""
    for s in secrets:
        text = text.replace(s, "[credential removed]")
    for _, pat in SECRETS:
        text = pat.sub("[credential removed]", text)
    return text
