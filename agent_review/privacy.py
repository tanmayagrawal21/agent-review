"""Deterministic private-terms scanner. The terms come from a secret, are matched here in plain code and never
reach a model, so a pull request cannot talk an agent into revealing them. Findings name the term by index only.
"""

from __future__ import annotations

import codecs
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


def header_path(p: str) -> str:
    """The new-side path of a '+++' header: git C-quotes unusual names and may append a tab."""
    p = p.rstrip("\n").split("\t", 1)[0]
    if p.startswith('"') and p.endswith('"'):
        p = codecs.escape_decode(p[1:-1].encode())[0].decode("utf-8", "replace")
    return p[2:] if p.startswith("b/") else ""


HUNK = re.compile(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def walk(diff: str):
    """(file, kind, new-side line) for each hunk line, kind in '+', ' ', '-' (line None for '-'), plus
    ('header', file) entries. Headers are recognised only between hunks, by counting each hunk's lines, so
    content such as an added line '++ x' after a removed '-- x' cannot pose as a file header."""
    file, line, old_left, new_left = "", 0, 0, 0
    for raw in diff.splitlines():
        if old_left <= 0 and new_left <= 0:
            if raw.startswith("+++ "):
                file = header_path(raw[4:])
                yield file, "header", None, ""
                continue
            m = HUNK.match(raw)
            if m:
                line, old_left, new_left = int(m.group(2)), int(m.group(1) or 1), int(m.group(3) or 1)
            continue
        if raw.startswith("\\"):
            continue
        if raw.startswith("+"):
            yield file, "+", line, raw[1:]
            line, new_left = line + 1, new_left - 1
        elif raw.startswith("-"):
            yield file, "-", None, raw[1:]
            old_left -= 1
        else:
            yield file, " ", line, raw[1:]
            line, old_left, new_left = line + 1, old_left - 1, new_left - 1


def added_lines(diff: str):
    """(file, new-side line number, text) for every added line of a unified diff."""
    for file, kind, line, text in walk(diff):
        if kind == "+" and file:
            yield file, line, text


def new_files(diff: str) -> list[str]:
    return [f for f, kind, _, _ in walk(diff) if kind == "header" and f]


def scan_added(diff: str, terms: list[str]) -> list[Hit]:
    """Hits in added lines and in the paths of changed files."""
    pats = patterns(terms)
    hits = [Hit(f, None, i) for f in new_files(diff) for i, p in enumerate(pats, 1) if p.search(f)]
    hits += [Hit(f, n, i) for f, n, text in added_lines(diff) for i, p in enumerate(pats, 1) if p.search(text)]
    return hits


def redact(text: str, terms: list[str]) -> str:
    for p in patterns(terms):
        text = p.sub("[redacted]", text)
    return text
