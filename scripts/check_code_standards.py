#!/usr/bin/env python3
"""CI checks for the code-organization rules in CLAUDE.md.

1. A code file (.py, .js, .css under scripts/, docs/ except docs/data, and
   tests/) may not go past 600 lines, and a file already past it may not
   grow. scripts/code_size_allowlist.txt records the count for each file
   that was already over the cap.

2. Club names live in scripts/mlb_clubs.py only. Another file fails if it
   carries a second copy of that table (many CLUB_NAMES strings) or a
   hard-coded former franchise name (Cleveland Indians, Oakland Athletics,
   and the other names this module does not use).

3. .github/workflows/ci.yml must name every suite under tests/, and the
   manual tests job in validate.yml must run the same globs. A new suite
   that nobody wired up fails here.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mlb_clubs import CLUB_NAMES  # noqa: E402

LIMIT = 600
ALLOWLIST = ROOT / "scripts" / "code_size_allowlist.txt"
CODE_SUFFIXES = {".py", ".js", ".css"}
CLUB_MODULE = ROOT / "scripts" / "mlb_clubs.py"
THIS_FILE = Path(__file__).resolve()
# A sentence can name a few clubs. A copied table names most of them.
TABLE_THRESHOLD = 8

# Former names. Display uses CLUB_NAMES, so these must not come back as a
# second table. Exact string literals only: a transaction sentence that
# merely contains one of these words is not a name table.
FORMER_NAMES = frozenset(
    {
        "Cleveland Indians",
        "Oakland Athletics",
        "Florida Marlins",
        "Los Angeles Angels of Anaheim",
        "Anaheim Angels",
        "California Angels",
        "Tampa Bay Devil Rays",
        "Montreal Expos",
    }
)

_QUOTED = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')


def line_count(path: Path) -> int:
    """Newline count, matching `wc -l`."""
    return path.read_bytes().count(b"\n")


def code_files() -> list[Path]:
    roots = [ROOT / "scripts", ROOT / "docs", ROOT / "tests"]
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in CODE_SUFFIXES:
                continue
            if "data" in path.relative_to(ROOT).parts:
                continue
            found.append(path)
    return sorted(found)


def load_allowlist() -> dict[str, int]:
    if not ALLOWLIST.exists():
        return {}
    allowed: dict[str, int] = {}
    for lineno, raw in enumerate(ALLOWLIST.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2 or not parts[1].isdigit():
            raise SystemExit(f"{ALLOWLIST}:{lineno}: expected 'path count', got {raw!r}")
        allowed[parts[0]] = int(parts[1])
    return allowed


def check_sizes() -> list[str]:
    allowed = load_allowlist()
    errors = []
    for path in code_files():
        rel = path.relative_to(ROOT).as_posix()
        count = line_count(path)
        cap = allowed.get(rel)
        if count > LIMIT and (cap is None or count > cap):
            if cap is None:
                errors.append(f"{rel} is {count} lines, over the {LIMIT}-line cap")
            else:
                errors.append(
                    f"{rel} grew from {cap} to {count} lines while already over {LIMIT}"
                )
    return errors


def _python_strings(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
    return found


def _quoted_strings(path: Path) -> set[str]:
    found: set[str] = set()
    for token in _QUOTED.findall(path.read_text(encoding="utf-8", errors="replace")):
        try:
            value = ast.literal_eval(token)
        except (SyntaxError, ValueError):
            value = token[1:-1]
        if isinstance(value, str):
            found.add(value)
    return found


def string_literals(path: Path) -> set[str]:
    if path.suffix == ".py":
        return _python_strings(path)
    return _quoted_strings(path)


def check_club_tables() -> list[str]:
    names = set(CLUB_NAMES.values())
    errors = []
    for path in code_files():
        if path.resolve() == CLUB_MODULE.resolve():
            continue
        literals = string_literals(path)
        rel = path.relative_to(ROOT).as_posix()
        copied = sorted(literals & names)
        if len(copied) >= TABLE_THRESHOLD:
            errors.append(
                f"{rel} hard-codes {len(copied)} club names "
                f"({', '.join(copied[:4])}, ...). Use mlb_clubs.CLUB_NAMES."
            )
        if path.resolve() == THIS_FILE.resolve():
            continue
        former = sorted(literals & FORMER_NAMES)
        if former:
            errors.append(
                f"{rel} hard-codes a former club name ({', '.join(former)}). "
                "Display names come from mlb_clubs.CLUB_NAMES."
            )
    return errors


def test_suite_files() -> list[Path]:
    tests = ROOT / "tests"
    found = []
    for path in tests.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("test_") and path.suffix == ".py":
            found.append(path)
        elif path.name.endswith(".test.cjs") or path.name.endswith(".test.js"):
            found.append(path)
    return sorted(found)


def check_workflows() -> list[str]:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    validate = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
    errors = []
    for path in test_suite_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel not in ci:
            errors.append(f"ci.yml does not run {rel}")
    for glob in ("tests/test_*.py", "tests/*.test.cjs"):
        if glob not in validate:
            errors.append(f"validate.yml tests job does not run {glob}")
    return errors


def main() -> None:
    errors = check_sizes() + check_club_tables() + check_workflows()
    if errors:
        print(f"{len(errors)} code-organization problem(s):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        sys.exit(1)
    print(
        f"code standards ok ({len(code_files())} code files, "
        f"{len(test_suite_files())} test suites)"
    )


if __name__ == "__main__":
    main()
