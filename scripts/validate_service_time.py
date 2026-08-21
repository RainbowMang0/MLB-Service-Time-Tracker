#!/usr/bin/env python3
"""
validate_service_time.py
-------------------------
Checks computed service time against a small set of known-good reference
figures, as of a fixed past date. This is the missing piece flagged in
CLAUDE.md: nothing currently checks these numbers against anything but
themselves.

Why "as of a past date" and not "right now"
--------------------------------------------
A player's live service time is a moving target -- it changes every day he's
on an active roster, so there's nothing fixed to compare it against. But his
service time AS OF A PAST OPENING DAY is a historical fact, and outlets like
Baseball Reference publish an Opening Day snapshot (`s.YYYY` on a player's
page) that doesn't change after the fact. That makes it a usable fixed target
for regression-testing this project's math against a real, independently
reported number.

Where the reference numbers come from
--------------------------------------
This script does NOT scrape Baseball Reference or any other site -- there's
no automated fetch of expected values here, by design (scraping is against
their terms, and hand-checking a handful of well-known players is plenty to
catch systemic bugs). You look up a player's `s.YYYY` figure on their
Baseball Reference page yourself and add a row to the reference file.

Usage
-----
    python scripts/validate_service_time.py
    python scripts/validate_service_time.py --reference path/to/other.json
    python scripts/validate_service_time.py --tolerance-days 5

Reference file format (see data/reference_service_time.example.json):
    [
      {
        "player_id": 592450,
        "name": "Aaron Judge",
        "as_of": "2021-04-01",
        "expected": "5.014",
        "source": "baseball-reference.com player page, s.2021 snapshot"
      }
    ]

Each row is computed independently with horizon_end=as_of, exactly as if
the daily job had run on that date, and compared to `expected`. A mismatch
beyond --tolerance-days (default 3, to absorb Opening Day date fuzziness
and rounding) is reported as a failure and the script exits non-zero -- so
this can also run as a CI check once the reference file has real entries.

Requires network access to the live MLB Stats API (statsapi.mlb.com), same
as update_service_time.py -- there's nothing to validate against a local
transaction cache alone for players who aren't already cached.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from service_time import FULL_YEAR_DAYS  # noqa: E402
from update_service_time import build_player_record  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE_FILE = ROOT / "data" / "reference_service_time.json"
EXAMPLE_REFERENCE_FILE = ROOT / "data" / "reference_service_time.example.json"

DEFAULT_TOLERANCE_DAYS = 3


def _parse_formatted(value: str) -> int:
    """'5.014' -> total days (5 * 172 + 14)."""
    years_str, days_str = value.split(".")
    return int(years_str) * FULL_YEAR_DAYS + int(days_str)


def load_reference(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"No reference file at {path}.\n"
            f"Copy {EXAMPLE_REFERENCE_FILE.relative_to(ROOT)} to "
            f"{path.relative_to(ROOT)} and fill in real Baseball Reference "
            "figures for a handful of players, then re-run."
        )
    return json.loads(path.read_text())


def validate_one(case: dict, tolerance_days: int) -> tuple[bool, str]:
    player_id = case["player_id"]
    as_of = dt.date.fromisoformat(case["as_of"])
    expected_days = _parse_formatted(case["expected"])

    roster_entry = {"id": player_id, "fullName": case.get("name"), "team": None, "teamId": None, "position": None}
    record = build_player_record(roster_entry, full_refresh=False, use_cache=True, horizon_end=as_of)

    actual_days = record["service_days_total"]
    delta = actual_days - expected_days
    ok = abs(delta) <= tolerance_days

    label = case.get("name") or str(player_id)
    status = "ok  " if ok else "FAIL"
    msg = (
        f"  {status} - {label} as of {as_of}: expected {case['expected']} "
        f"({expected_days}d), got {record['service_time']} ({actual_days}d), "
        f"delta {delta:+d}d"
    )
    if not record.get("history_complete", True):
        msg += "  [partial history -- pre-2009 debut, expect a low reading]"
    return ok, msg


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=pathlib.Path, default=DEFAULT_REFERENCE_FILE)
    parser.add_argument("--tolerance-days", type=int, default=DEFAULT_TOLERANCE_DAYS)
    args = parser.parse_args()

    cases = load_reference(args.reference)
    if not cases:
        print(f"{args.reference} is empty -- nothing to validate.")
        return

    print(f"Validating {len(cases)} case(s) from {args.reference} (tolerance: {args.tolerance_days}d)...\n")

    passed = 0
    failed = 0
    for case in cases:
        try:
            ok, msg = validate_one(case, args.tolerance_days)
        except Exception as exc:
            failed += 1
            print(f"  FAIL - {case.get('name') or case.get('player_id')}: ERROR {exc}")
            continue
        print(msg)
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
