#!/usr/bin/env python3
"""
refresh_complete.py
-------------------
The daily job's "already done today" signal.

data/refresh_complete.json is written once, after a roster refresh has
rebuilt every rostered player and published. The workflow reads it from
the branch tip and skips only when that file says a complete run finished
today (UTC).

A rostered player's last_updated is the wrong signal. It moves as soon as
any one of those records is rebuilt, so a rules change that touches a few
players makes the whole day look finished and the rest of the roster is
skipped. It also lives in the checkout the workflow was triggered on, and
the second scheduled run of a day often still has the commit from before
the first run pushed, so a finished refresh is invisible and the whole
pull runs again.

This module is stdlib-only on purpose: the workflow gate runs it before
dependencies are installed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

# Same floor the publisher uses before it will replace the live file
# (update_service_time.MIN_EXPECTED_ROSTER). A shorter roster is not a
# finished daily refresh, even if every fetched player built cleanly.
MIN_ROSTERED = 900

MARKER_REL = pathlib.Path("data") / "refresh_complete.json"


def marker_path(root: pathlib.Path) -> pathlib.Path:
    return root / MARKER_REL


def utc_today() -> str:
    """The runner's UTC date. The gate compares against `date -u`."""
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def should_mark_complete(*, limit: int | None, failures: int, rostered: int) -> bool:
    """True only for a finished roster refresh that published everyone.

    A --limit run is a partial by request. Any player who failed to build
    leaves the day unfinished so the next fire retries it. A short roster
    is the sanity check's "do not trust this pull" case.
    """
    if limit is not None:
        return False
    if failures != 0:
        return False
    if rostered < MIN_ROSTERED:
        return False
    return True


def build_marker(
    *,
    completed_on: str,
    rostered: int,
    updated: int,
    failures: int,
    rules_version: int,
) -> dict:
    return {
        "completed_on": completed_on,
        "rostered": rostered,
        "updated": updated,
        "failures": failures,
        "rules_version": rules_version,
    }


def write_marker(path: pathlib.Path, marker: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marker, indent=2) + "\n")


def read_marker(path: pathlib.Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _count(value) -> int | None:
    # bool is a subclass of int; a boolean is not a count.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def marker_is_complete(
    marker: dict | None,
    today_utc: str,
    *,
    min_rostered: int = MIN_ROSTERED,
) -> bool:
    """True when `marker` records a complete, successful refresh on `today_utc`.

    Missing, unreadable, partial, and failed markers are not complete.
    rules_version is stored for the log and is not part of the decision:
    a finished refresh stays finished for that UTC date.
    """
    if not isinstance(marker, dict):
        return False
    if marker.get("completed_on") != today_utc:
        return False
    rostered = _count(marker.get("rostered"))
    updated = _count(marker.get("updated"))
    failures = _count(marker.get("failures"))
    if rostered is None or updated is None or failures is None:
        return False
    if failures != 0:
        return False
    if rostered < min_rostered or updated != rostered:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print yes when a refresh marker is a completed run for a UTC date."
    )
    parser.add_argument("--today", required=True, help="UTC date, YYYY-MM-DD")
    parser.add_argument("--marker", required=True, help="Path to the marker JSON")
    args = parser.parse_args(argv)
    done = marker_is_complete(read_marker(pathlib.Path(args.marker)), args.today)
    print("yes" if done else "no")
    return 0


if __name__ == "__main__":
    sys.exit(main())
