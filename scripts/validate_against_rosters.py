#!/usr/bin/env python3
"""
validate_against_rosters.py
----------------------------
Measures how accurate the transaction-derived service time actually is, by
checking it against MLB's own historical rosters.

The idea
--------
Service time is days spent on a major league active roster or major league
injured list. `/teams/{id}/roster?rosterType=40Man&date=YYYY-MM-DD` returns
the roster as it stood on that date, with a status for each player -- and
that status distinguishes Active and Injured List from Optioned. So the
thing this project estimates is directly observable, one date at a time.

Rebuilding every career this way is far too expensive (30 clubs x ~186 days
x N seasons of API calls). Sampling is cheap, though, and gives a real
number instead of a vibe: take one club and one season, ask for the roster
every N days, and compare each answer against what our intervals claim for
those same players on those same dates.

One club-season at a 7-day interval is about 27 calls and yields roughly
1,100 player-date judgements. That is a genuine accuracy measurement.

What "correct" means here
-------------------------
For each (player, sampled date) the roster says accruing or not, and our
model says accruing or not. Four outcomes:

  agree-accruing / agree-not   -- fine
  MODEL OVER   -- we credit a day the roster says he was optioned or absent
  MODEL UNDER  -- the roster has him active or on the IL, we credit nothing

Over-crediting is the failure mode this project keeps hitting, so it is
reported separately rather than rolled into one accuracy percentage.

Status codes
------------
The exact set of status codes the API uses is not documented anywhere we
trust, so this script does NOT guess. It classifies the codes it knows,
counts everything else under "unrecognised", and prints the distribution so
the list can be extended from evidence. If unrecognised codes are a
meaningful share of the sample, the accuracy number is not yet trustworthy
-- that is reported explicitly rather than buried.

Usage
-----
    python scripts/validate_against_rosters.py --team 147 --season 2018
    python scripts/validate_against_rosters.py --team 147 --season 2018 --interval 14

Requires live API access, so run it via Actions -> "Validate Service Time".
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import fetch_mlb_data as mlb  # noqa: E402
from service_time import Transaction, build_global_active_intervals  # noqa: E402

# Status codes that mean the player is earning service time that day: on the
# active roster, or on a major league injured/bereavement/paternity list.
# Derived from observed values -- extend only with evidence from a real run.
ACCRUING_CODES = {
    "A",  # Active
    "D7", "D10", "D15", "D60",  # injured lists (7/10/15/60-day)
    "DL",  # legacy disabled list
    "BRV",  # bereavement
    "PL",  # paternity
    "FME",  # family medical emergency
    "RM",  # restricted -- still on a major league list
}

# Codes that mean he is NOT accruing: in the minors, or gone.
NON_ACCRUING_CODES = {
    "OPT",  # optioned to the minors
    "MIN",  # minor league
    "RES",  # reserve / inactive
    "DES",  # designated for assignment
    "REL",  # released
    "SU",  # suspended
    "NRI",  # non-roster invitee
}


def sample_dates(season_start: dt.date, season_end: dt.date, interval: int) -> list[dt.date]:
    out, d = [], season_start
    while d <= season_end:
        out.append(d)
        d += dt.timedelta(days=interval)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", type=int, required=True, help="MLB team id, e.g. 147")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--interval", type=int, default=7, help="days between samples")
    args = ap.parse_args()

    start, end = mlb.get_season_window(args.season)
    dates = sample_dates(start, end, args.interval)
    print(f"Team {args.team}, season {args.season}: {start} to {end}")
    print(f"Sampling {len(dates)} dates every {args.interval} days.\n")

    # --- ground truth ------------------------------------------------------
    truth: dict[int, dict[dt.date, bool]] = collections.defaultdict(dict)
    names: dict[int, str] = {}
    codes = collections.Counter()
    unrecognised = collections.Counter()

    for d in dates:
        try:
            data = mlb._get(
                f"/teams/{args.team}/roster",
                {"rosterType": "40Man", "date": d.isoformat()},
            )
        except Exception as exc:
            print(f"  {d}: FAILED ({exc})", file=sys.stderr)
            continue
        for entry in data.get("roster", []):
            person = entry.get("person") or {}
            pid = person.get("id")
            if pid is None:
                continue
            names[pid] = person.get("fullName", "?")
            code = ((entry.get("status") or {}).get("code") or "").upper()
            codes[code] += 1
            if code in ACCRUING_CODES:
                truth[pid][d] = True
            elif code in NON_ACCRUING_CODES:
                truth[pid][d] = False
            else:
                unrecognised[code] += 1
                # Deliberately not guessed: left out of the comparison.

    print(f"Collected {sum(len(v) for v in truth.values())} player-date judgements "
          f"across {len(truth)} players.")
    print(f"status codes seen: {dict(codes.most_common())}")
    if unrecognised:
        share = sum(unrecognised.values()) / max(sum(codes.values()), 1)
        print(f"!! UNRECOGNISED codes {dict(unrecognised)} -- {share:.1%} of the sample, "
              "excluded from the comparison. Extend ACCRUING/NON_ACCRUING_CODES "
              "before trusting the accuracy figure.")
    print()

    # --- our model ---------------------------------------------------------
    over = under = agree = 0
    per_player: dict[int, list[int]] = collections.defaultdict(lambda: [0, 0, 0])

    for pid, by_date in truth.items():
        try:
            raw = mlb.get_player_transactions(pid, dt.date(2005, 1, 1), end)
            bio = mlb.get_player_bio(pid)
        except Exception as exc:
            print(f"  {names.get(pid)}: FAILED ({exc})", file=sys.stderr)
            continue
        debut = bio.get("mlbDebutDate")
        floor = dt.date.fromisoformat(debut) if debut else None
        txns = [
            Transaction(date=dt.date.fromisoformat(t["date"]), description=t.get("description", ""))
            for t in raw
            if t.get("date")
        ]
        intervals = build_global_active_intervals(txns, end, accrual_floor=floor)

        for d, actually_accruing in by_date.items():
            model_says = any(s <= d <= e for s, e in intervals)
            if model_says == actually_accruing:
                agree += 1
                per_player[pid][0] += 1
            elif model_says and not actually_accruing:
                over += 1
                per_player[pid][1] += 1
            else:
                under += 1
                per_player[pid][2] += 1

    total = agree + over + under
    if not total:
        raise SystemExit("No comparisons made.")

    print("=" * 70)
    print(f"AGREE          {agree:>6}  ({agree/total:.1%})")
    print(f"MODEL OVER     {over:>6}  ({over/total:.1%})   credited a day the roster says he was not")
    print(f"MODEL UNDER    {under:>6}  ({under/total:.1%})   missed a day the roster says he was")
    print()

    worst = sorted(per_player.items(), key=lambda kv: -(kv[1][1] + kv[1][2]))[:12]
    print("worst players (over / under / agree):")
    for pid, (a, o, u) in worst:
        if o or u:
            print(f"  {names.get(pid, pid):<26} over={o:<3} under={u:<3} agree={a}")


if __name__ == "__main__":
    main()
