"""Unified-diff parsing shared by every step. Headers are recognised only between hunks (by counting each
hunk's lines), so content such as an added '++ x' after a removed '-- x' can't pose as a file header, and
git's C-quoted paths are decoded."""

from __future__ import annotations

import codecs
import re
import subprocess
from pathlib import Path

HUNK = re.compile(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def header_path(p: str) -> str:
    """The new-side path of a '+++' header: git C-quotes unusual names and may append a tab."""
    p = p.split("\t", 1)[0]
    if p.startswith('"') and p.endswith('"'):
        p = codecs.escape_decode(p[1:-1].encode())[0].decode("utf-8", "replace")
    return p[2:] if p.startswith("b/") else ""


def walk(diff: str):
    """Yield (file, kind, new_line, text) for every line: kind is 'meta' (headers, hunk markers), '+', '-' or
    ' '. new_line is the new-side line number for '+' and ' ', else None."""
    file, line, old_left, new_left = "", 0, 0, 0
    for raw in diff.split("\n"):
        if old_left <= 0 and new_left <= 0:
            if raw.startswith("+++ "):
                file = header_path(raw[4:])
            elif m := HUNK.match(raw):
                line, old_left, new_left = int(m.group(2)), int(m.group(1) or 1), int(m.group(3) or 1)
            yield file, "meta", None, raw
        elif raw.startswith("+"):
            yield file, "+", line, raw[1:]
            line, new_left = line + 1, new_left - 1
        elif raw.startswith("-"):
            yield file, "-", None, raw[1:]
            old_left -= 1
        elif raw.startswith(" "):
            yield file, " ", line, raw[1:]
            line, old_left, new_left = line + 1, old_left - 1, new_left - 1
        else:  # "\ No newline at end of file", or a trailing empty line
            yield file, "meta", None, raw


def added(diff: str):
    """(file, line, text) for each added line."""
    return [(f, n, t) for f, kind, n, t in walk(diff) if kind == "+" and f]


def commentable(diff: str) -> dict[str, set[int]]:
    """New-side lines in hunks (added or context): the only places GitHub accepts inline comments."""
    out: dict[str, set[int]] = {}
    for f, kind, n, _ in walk(diff):
        if f and kind in ("+", " "):
            out.setdefault(f, set()).add(n)
    return out


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def changed(repo: Path, base: str, head: str) -> tuple[list[str], int]:
    """Changed paths (new side) and the number of changed lines, from NUL-separated numstat."""
    fields, files, lines, i = git(repo, "diff", "-z", "--numstat", f"{base}...{head}").split("\0"), [], 0, 0
    while i < len(fields) and fields[i]:
        add, rem, path = fields[i].split("\t", 2)
        if not path:  # a rename: old and new paths follow as separate fields
            path, i = fields[i + 2], i + 2
        files.append(path)
        lines += (int(add) if add.isdigit() else 0) + (int(rem) if rem.isdigit() else 0)
        i += 1
    return files, lines
