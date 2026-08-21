#!/usr/bin/env python3
"""
probe_coverage.py
------------------
Answers finding #9 in CLAUDE.md: does /transactions actually carry pre-2009
history, or does coverage really begin in 2009?

Why this exists as a script and a workflow
-------------------------------------------
It is one curl. But the development sandbox cannot reach statsapi.mlb.com --
the egress proxy returns 403 for that host -- so the question cannot be
settled locally. GitHub Actions runners can reach it, which is how the whole
pipeline works, so this runs there instead. Actions -> "Probe Transaction
Coverage" -> Run workflow, then read the log.

What it reports, per player, per year:
  * how many transactions come back at all
  * how many involve one of the 30 major league clubs (the distinction that
    matters -- a minor league signing in 2006 is not evidence that a player's
    MLB roster history is visible)

Reading the result
------------------
If MLB-club rows come back for 2005-2008 for players who were in the majors
then, finding #1 ("coverage begins in 2009") is wrong, and several things
that depend on it need revisiting -- report_impossible_totals(), the
history_complete flag, and the "no data" display treatment.

If only non-MLB rows come back, finding #1 stands and the backfill's
over-credited records are real defects after all.

Usage
-----
    python scripts/probe_coverage.py                  # the flagged players
    python scripts/probe_coverage.py "Joe Mauer" ...  # specific names
"""

from __future__ import annotations

import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import fetch_mlb_data as mlb  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data" / "service_time.json"

# Players whose backfilled totals exceed what a 2009-start window allows.
# Their arithmetic only works if pre-2009 seasons are being credited, which
# is exactly what this probe is testing.
DEFAULT_NAMES = [
    "Angel Guzman",
    "Lew Ford",
    "Gary Majewski",
    "Guillermo Mota",
    "Jack Wilson",
]

PROBE_START = 2005
PROBE_END = 2012


def load_ids(names: list[str]) -> list[tuple[str, int, str | None]]:
    players = json.loads(DATA.read_text())["players"]
    by_name = {p["name"]: p for p in players}
    out = []
    for n in names:
        p = by_name.get(n)
        if p is None:
            print(f"  ! {n}: not in the database, skipping", file=sys.stderr)
            continue
        out.append((n, p["id"], p.get("mlb_debut")))
    return out


def main() -> None:
    names = sys.argv[1:] or DEFAULT_NAMES
    targets = load_ids(names)
    if not targets:
        raise SystemExit("No probe targets resolved.")

    mlb_ids = {t["id"] for t in mlb.get_teams() if isinstance(t.get("id"), int)}
    print(f"Resolved {len(mlb_ids)} major league club IDs.\n")

    verdict_rows = 0
    for name, pid, debut in targets:
        print(f"=== {name}  (id {pid}, debut {debut}) ===")
        total = collections.Counter()
        major = collections.Counter()
        samples: list[str] = []

        for year in range(PROBE_START, PROBE_END + 1):
            import datetime as dt

            try:
                txns = mlb.get_player_transactions(
                    pid, dt.date(year, 1, 1), dt.date(year, 12, 31)
                )
            except Exception as exc:
                print(f"  {year}: FAILED ({exc})")
                continue

            for t in txns:
                total[year] += 1
                ids = {
                    (t.get("team") or {}).get("id"),
                    (t.get("fromTeam") or {}).get("id"),
                    (t.get("toTeam") or {}).get("id"),
                }
                if ids & mlb_ids:
                    major[year] += 1
                    if year < 2009 and len(samples) < 4:
                        samples.append(f"{t.get('date')} | {t.get('description', '')[:88]}")

        for year in range(PROBE_START, PROBE_END + 1):
            marker = "  <-- PRE-2009 MLB ROWS" if (year < 2009 and major[year]) else ""
            print(f"  {year}: {total[year]:>3} transactions, {major[year]:>3} involve an MLB club{marker}")

        pre = sum(major[y] for y in range(PROBE_START, 2009))
        verdict_rows += pre
        if samples:
            print("  sample pre-2009 major league rows:")
            for s in samples:
                print(f"    {s}")
        print()

    print("=" * 70)
    if verdict_rows:
        print(
            f"VERDICT: {verdict_rows} pre-2009 major-league transaction(s) returned.\n"
            "Finding #1 is WRONG -- the feed does carry pre-2009 history.\n"
            "Revisit: report_impossible_totals(), the history_complete flag, the\n"
            "'no data' display treatment, and finding #6's gap-bridging concern."
        )
    else:
        print(
            "VERDICT: no pre-2009 major-league transactions returned.\n"
            "Finding #1 STANDS -- coverage really does begin in 2009, and the\n"
            "backfill records that exceed their window are real defects."
        )


if __name__ == "__main__":
    main()
