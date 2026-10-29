"""Deterministic private-terms scanner. The terms come from a secret, are matched here in plain code and never
reach a model, so a pull request cannot talk an agent into revealing them. Findings name the term by index only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


def load_terms(path: Path | None) -> list[str]:
    if not path or not path.exists():
        return []
    return [t.strip() for t in path.read_text().splitlines() if t.strip() and not t.strip().startswith("#")]


def _pattern(term: str) -> re.Pattern:
    digits = re.sub(r"\D", "", term)
    if len(digits) >= 7 and len(digits) >= len(re.sub(r"\s", "", term)) * 0.6:  # phone-like: ignore separators
        return re.compile(r"\D{0,3}".join(digits))
    return re.compile(re.escape(term), re.I)


def patterns(terms: list[str]) -> list[re.Pattern]:
    return [_pattern(t) for t in terms]


@dataclass
class Hit:
    file: str
    line: int | None
    term: int  # 1-based index into the terms file


def scan_added(diff: str, terms: list[str]) -> list[Hit]:
    """Hits in added lines and in new file paths of a unified diff."""
    pats, hits, file, line = patterns(terms), [], "", 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            file = raw[6:] if raw.startswith("+++ b/") else ""
            hits += [Hit(file, None, i) for i, p in enumerate(pats, 1) if file and p.search(file)]
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line = int(m.group(1)) if m else 0
        elif raw.startswith("+"):
            hits += [Hit(file, line, i) for i, p in enumerate(pats, 1) if p.search(raw[1:])]
            line += 1
        elif not raw.startswith("-") and not raw.startswith("\\"):
            line += 1
    return hits


def redact(text: str, terms: list[str]) -> str:
    for p in patterns(terms):
        text = p.sub("[redacted]", text)
    return text
