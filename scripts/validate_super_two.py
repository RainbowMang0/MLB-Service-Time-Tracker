#!/usr/bin/env python3
"""
validate_super_two.py
----------------------
Checks the computed Super Two cutoff against published figures.

Runs entirely offline: the cutoff is derived from the season breakdowns
already stored in docs/data/service_time.json, so this needs no network and
no MLB API access. That also means it validates exactly what the site
publishes.

    python scripts/validate_super_two.py
    python scripts/validate_super_two.py --tolerance-days 2

Fill in `published` in data/reference_super_two.json first; rows left null
are reported as uncomputed rather than passing silently.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from super_two import FULL_YEAR_DAYS, compute_cutoff  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB_FILE = ROOT / "docs" / "data" / "service_time.json"
REFERENCE_FILE = ROOT / "data" / "reference_super_two.json"

# A cutoff is a boundary between two adjacent players, so it can legitimately
# land a day or two off if one borderline player's service time is slightly
# wrong. Wider than that means the class or the ranking is wrong.
DEFAULT_TOLERANCE_DAYS = 2


def _parse(value: str) -> int:
    years, days = value.split(".")
    return int(years) * FULL_YEAR_DAYS + int(days)


def evaluate(
    db: dict[str, dict], rows: list[dict], tolerance_days: int
) -> tuple[list[str], int, int, int]:
    """
    Pure core of the check: no I/O, no argparse, no sys.exit, so it is
    callable directly from a test instead of only through a subprocess.

    Returns (print_lines, failures, entered_count, pending_count). A row
    with no published figure is reported but never counts toward
    `failures` -- there is nothing yet to be wrong about. A row whose class
    is too small to draw a cutoff through DOES count as a failure once it
    has a published figure to fail against: a cutoff the method refuses to
    compute cannot be scored "ok" against one MLB actually published.
    """
    entered = [r for r in rows if r.get("published")]
    pending = [r for r in rows if not r.get("published")]

    lines = []
    failures = 0
    for row in sorted(rows, key=lambda r: -r["season"]):
        season = row["season"]
        cutoff = compute_cutoff(db, season)
        published = row.get("published")
        if cutoff is None:
            lines.append(f"  ---- {season}: class too small to measure")
            if published:
                failures += 1
            continue
        line = (
            f"  {season}: ours {cutoff['cutoff']} "
            f"(class {cutoff['class_size']}, top {cutoff['qualifying_count']})"
        )
        if published:
            delta = cutoff["cutoff_days"] - _parse(published)
            ok = abs(delta) <= tolerance_days
            if not ok:
                failures += 1
            line = f"  {'ok  ' if ok else 'FAIL'} {line[2:]}, published {published}, delta {delta:+d}d"
        lines.append(line)

    return lines, failures, len(entered), len(pending)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tolerance-days", type=int, default=DEFAULT_TOLERANCE_DAYS)
    args = ap.parse_args()

    db = {
        str(p["id"]): p
        for p in json.loads(DB_FILE.read_text())["players"]
    }
    rows = [r for r in json.loads(REFERENCE_FILE.read_text()) if "season" in r]

    print(f"Super Two cutoffs computed from {len(db)} player records.\n")

    # Always show what we compute, entered or not -- the figures are useful
    # on their own and this is how you find the numbers to go look up.
    lines, failures, entered_count, pending_count = evaluate(db, rows, args.tolerance_days)
    for line in lines:
        print(line)

    if not entered_count:
        print(
            f"\nNo published cutoffs entered yet ({pending_count} row(s) awaiting one).\n"
            "The computed figures above land in the band where Super Two cutoffs\n"
            "have historically fallen, which is encouraging and is NOT evidence.\n"
            "Enter real published figures in data/reference_super_two.json to\n"
            "turn this into an actual check."
        )
        return

    print(f"\n{entered_count - failures} passed, {failures} failed, {pending_count} awaiting a figure")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
