#!/usr/bin/env python3
"""Outside-site names stay out of published pages and repo data.

docs/, data/, and config/ (copied to docs/data/config/ on publish) may not
name Baseball-Reference, MLB Trade Rumors, FanGraphs, Spotrac, or Cot's, and
may not link to those sites. Say "public data" or "public reporting" instead.

The patterns below are the check, same as the other banned-name lists. One
published line is allowed: the note stripper in docs/app.js names the sites
only so it can delete them from a hand-written note before the page shows it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("data", "docs", "config")
# Images are not pages or data files. docs/og.png is the one binary here.
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2"}

# The published note stripper. It is not a citation.
ALLOW_LINE = "FanGraphs|Baseball-Reference|Baseball Reference"
ALLOW_REL = "docs/app.js"

PATTERN = re.compile(
    r"baseball[-\s]?reference"
    r"|fangraphs"
    r"|spotrac"
    r"|mlb\s*trade\s*rumors"
    r"|mlbtraderumors"
    r"|\bmlbtr\b"
    r"|\bcot['\u2019]s\b"
    r"|\bcots\b",
    re.IGNORECASE,
)

PASS = 0
FAIL = 0


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def _hits(path: Path) -> list[str]:
    rel = path.relative_to(ROOT).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [f"{rel}: not utf-8"]
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if rel == ALLOW_REL and ALLOW_LINE in line:
            continue
        match = PATTERN.search(line)
        if match:
            found.append(f"{rel}:{lineno}: {match.group(0)}")
            if len(found) >= 8:
                found.append(f"{rel}: more matches omitted")
                break
    return found


def main() -> int:
    problems = []
    scanned = 0
    for name in SCAN_ROOTS:
        root = ROOT / name
        check(f"{name}/ exists", root.is_dir())
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            scanned += 1
            problems.extend(_hits(path))
    check(f"scanned files under {', '.join(SCAN_ROOTS)}", scanned > 0)
    check("no outside-site names in docs/, data/, or config/", not problems)
    for problem in problems[:40]:
        print(f"    {problem}")
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
