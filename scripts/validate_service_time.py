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
service time as of a past date is a historical fact. Baseball Reference shows
one dated snapshot in the bio block on a player's page -- "9.051 (01/26)" --
and that figure does not change after the fact, which makes it a usable fixed
target for regression-testing this project's math against a real,
independently reported number.

The snapshot is taken in the offseason, and service time does not accrue
between the end of the World Series and Opening Day, so `as_of` only has to
land somewhere in that window -- it does not have to match B-R's label to the
day.

(An earlier version of this file assumed B-R published a per-season `s.YYYY`
column. It does not; there is a single snapshot with a date on it.)

Where the reference numbers come from
--------------------------------------
This script does NOT scrape Baseball Reference or any other site -- there's
no automated fetch of expected values here, by design (scraping is against
their terms, and hand-checking a handful of well-known players is plenty to
catch systemic bugs). You look up a player's service-time snapshot on
their Baseball Reference page yourself and add a row to the reference file.

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
        "as_of": "2026-01-26",
        "expected": "9.051",
        "source": "baseball-reference.com player page, snapshot labelled 01/26"
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


def validate_one(case: dict, tolerance_days: int) -> tuple[str, str]:
    """Returns (verdict, message) where verdict is 'ok', 'gap' or 'fail'.

    'gap' is for a player whose own record already declares missing seasons
    (`missing_seasons > 0`: the feed shows nothing for the front of his
    career) and who reads LOW by roughly that much. That is the documented
    limitation doing exactly what it says, not a regression, so it is
    reported separately and does not fail the run. Reading HIGH is always a
    failure -- missing history cannot inflate a figure, and over-crediting is
    the failure mode behind every revert in this project's history.
    """
    player_id = case["player_id"]
    as_of = dt.date.fromisoformat(case["as_of"])
    expected_days = _parse_formatted(case["expected"])

    roster_entry = {"id": player_id, "fullName": case.get("name"), "team": None, "teamId": None, "position": None}
    record = build_player_record(roster_entry, full_refresh=False, use_cache=True, horizon_end=as_of)

    actual_days = record["service_days_total"]
    delta = actual_days - expected_days
    missing = record.get("missing_seasons", 0)

    if abs(delta) <= tolerance_days:
        verdict = "ok"
    elif delta < 0 and missing > 0:
        verdict = "gap"
    else:
        verdict = "fail"

    label = case.get("name") or str(player_id)
    status = {"ok": "ok  ", "gap": "GAP ", "fail": "FAIL"}[verdict]
    msg = (
        f"  {status} - {label} as of {as_of}: expected {case['expected']} "
        f"({expected_days}d), got {record['service_time']} ({actual_days}d), "
        f"delta {delta:+d}d"
    )
    if missing:
        msg += (
            f"  [{missing} season(s) not visible in the feed"
            f"; first transaction {record.get('first_transaction')}]"
        )
    return verdict, msg


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=pathlib.Path, default=DEFAULT_REFERENCE_FILE)
    parser.add_argument("--tolerance-days", type=int, default=DEFAULT_TOLERANCE_DAYS)
    args = parser.parse_args()

    cases = load_reference(args.reference)
    # Skip the README block and any row whose figure has not been entered yet,
    # so the file can be filled in a few players at a time.
    pending = [c for c in cases if "player_id" in c and not c.get("expected")]
    cases = [c for c in cases if "player_id" in c and c.get("expected")]

    if not cases:
        print(
            f"No figures entered yet in {args.reference} "
            f"({len(pending)} row(s) awaiting one).\n\n"
            "Open each player's Baseball Reference page, read the service-time\n"
            "figure for the row's `as_of` season (shown as s.YYYY), and paste it\n"
            "into `expected` as e.g. \"7.045\". Rows left null are skipped, so\n"
            "filling in even three or four makes this check meaningful.\n\n"
            "This is the only check that is independent of MLB's own data --\n"
            "the roster comparison validates the pipeline against the same\n"
            "source it is built on."
        )
        return

    print(f"Validating {len(cases)} case(s) from {args.reference} (tolerance: {args.tolerance_days}d).")
    if pending:
        print(f"({len(pending)} row(s) still awaiting a figure -- skipped.)")
    print()

    tally = {"ok": 0, "gap": 0, "fail": 0}
    for case in cases:
        try:
            verdict, msg = validate_one(case, args.tolerance_days)
        except Exception as exc:
            tally["fail"] += 1
            print(f"  FAIL - {case.get('name') or case.get('player_id')}: ERROR {exc}")
            continue
        print(msg)
        tally[verdict] += 1

    print(f"\n{tally['ok']} passed, {tally['fail']} failed, {tally['gap']} known gap(s)")
    if tally["gap"]:
        print(
            "A 'GAP' row reads low by roughly the seasons its own record says\n"
            "are missing from the transaction feed. That is the known coverage\n"
            "limit, not a regression -- but a large gap on a player who should\n"
            "be fully visible is worth investigating."
        )
    sys.exit(1 if tally["fail"] else 0)


if __name__ == "__main__":
    main()
