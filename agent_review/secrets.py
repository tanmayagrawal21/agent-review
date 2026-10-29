"""Deterministic secret detection on added lines. Provider formats are blocking even when they look like
placeholders (scanners and attackers cannot tell); a generic key/secret/password assignment is a suggestion."""

from __future__ import annotations

import re

from .privacy import added_lines

PROVIDER = [
    ("Anthropic API key", r"sk-ant-[A-Za-z0-9_-]{20,}"),
    ("OpenAI API key", r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}"),
    ("Stripe secret key", r"[sr]k_live_[A-Za-z0-9]{16,}"),
    ("GitHub token", r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"),
    ("GitLab token", r"glpat-[A-Za-z0-9_-]{20,}"),
    ("AWS access key", r"(?:AKIA|ASIA)[0-9A-Z]{16}"),
    ("Google API key", r"AIza[0-9A-Za-z_-]{35}"),
    ("Slack token", r"xox[abposr]-[A-Za-z0-9-]{10,}"),
    ("private key", r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    ("credentials in a URL", r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s:@]{3,}@[^\s/]+"),
]
GENERIC = re.compile(r"""(?i)\b[\w-]*(?:password|passwd|secret|api_?key|access_?key|auth_?token|token)\b\s*[:=]\s*
                         ["']([^"'\s]{12,})["']""", re.X)
PLACEHOLDER = re.compile(r"(?i)^(?:x+|\*+|changeme|your[_-].*|<.*>|\$\{.*\}|example.*|dummy.*|test.*|fake.*)$")
_PROVIDER = [(name, re.compile(p)) for name, p in PROVIDER]


def scan(diff: str) -> list[dict]:
    out = []
    for file, line, text in added_lines(diff):
        found = [name for name, p in _PROVIDER if p.search(text)]
        for name in found:
            out.append({"file": file, "line": line, "severity": "blocking", "category": name,
                        "message": f"Looks like a {name} in a real provider format. Remove it, rotate it if it was "
                                   "ever real, and load it from a secret store; use an obviously fake value in tests."})
        m = GENERIC.search(text)
        if not found and m and not PLACEHOLDER.match(m.group(1)):
            out.append({"file": file, "line": line, "severity": "suggestion", "category": "possible secret",
                        "message": "A credential-like value is assigned in source. Check it is not real."})
    return out
