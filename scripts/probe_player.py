#!/usr/bin/env python3
"""
probe_player.py
----------------
Everything the model knows about one player in one season, side by side with
what MLB's own rosters say, on every sampled date.

WHY THIS EXISTS
================
`validate_against_rosters.py` scores a whole club-season and names its worst
players. That tells you WHO is wrong. It does not tell you why, and every
diagnosis in this project so far has come from reading one player's actual
transaction list until the cause was obvious -- the Jacob Wilson college
"activation", Angel Guzman's unstopped clock, Verlander's ten silent
seasons, Juan Soto's debut date preceding his contract selection.

Doing that by hand meant a throwaway script each time. This is that script,
kept.

    python scripts/probe_player.py --player 641386 --team 139 --season 2022

Prints, in order:

  1. the bio the pipeline uses (debut, last played) -- both are accrual
     boundaries, and a surprise in either explains a lot on its own
  2. every transaction in range, marked with whether the MLB-club filter
     keeps it and whether the parser reads it as a start or a stop
  3. the accrual intervals built from those transactions, carry-in both ways
  4. every sampled date: the roster's verdict, the model's, and whether they
     agree

Needs the live API, so run it from Actions:
    Actions -> "Validate Service Time" -> check: player
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import fetch_mlb_data as mlb  # noqa: E402
from service_time import (  # noqa: E402
    Transaction,
    _is_active_start,
    _is_active_stop,
    build_global_active_intervals,
)
from update_service_time import _involves_mlb_club, mlb_team_ids  # noqa: E402
from validate_against_rosters import flatten, is_il_code, sample_dates  # noqa: E402


def _verb(description: str) -> str:
    if _is_active_start(description):
        return "START"
    if _is_active_stop(description):
        return "STOP "
    return "  .  "


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--player", type=int, required=True, help="MLB person id")
    ap.add_argument("--team", type=int, required=True, help="MLB team id the roster comes from")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--interval", type=int, default=7, help="days between sampled dates")
    args = ap.parse_args()

    bio = mlb.get_player_bio(args.player)
    debut = bio.get("mlbDebutDate")
    debut_date = dt.date.fromisoformat(debut) if debut else None
    start, end = mlb.get_season_window(args.season)

    print(f"{bio.get('fullName') or args.player} (id {args.player})")
    print(f"  MLB debut   : {debut or '(none)'}")
    print(f"  last played : {bio.get('lastPlayedDate') or '(still active)'}")
    print(f"  season      : {args.season}, {start} to {end}\n")

    club_ids = mlb_team_ids()
    raw = mlb.get_player_transactions(args.player, dt.date(2005, 1, 1), end)

    print(f"TRANSACTIONS through {end} ({len(raw)} rows; 'mlb' = kept by the club filter)")
    kept = []
    for row in sorted(raw, key=lambda r: r.get("date") or ""):
        date_str = row.get("date")
        if not date_str:
            continue
        description = row.get("description", "")
        is_mlb = _involves_mlb_club(flatten(row), club_ids)
        mark = "mlb" if is_mlb else "   "
        # Highlight the season under test: the rows that decide this run are
        # almost always in it, or are the last one before it.
        here = ">>" if date_str[:4] == str(args.season) else "  "
        print(f"  {here} {date_str}  {mark}  {_verb(description)}  {description[:95]}")
        if is_mlb:
            kept.append(Transaction(date=dt.date.fromisoformat(date_str), description=description))

    print(f"\n  {len(kept)} of {len(raw)} rows involve a major league club.\n")

    variants = {
        "off": build_global_active_intervals(kept, end, accrual_floor=debut_date),
        "on": build_global_active_intervals(
            kept, end, accrual_floor=debut_date, presume_active_from=debut_date
        ),
    }
    for label, intervals in variants.items():
        print(f"ACCRUAL INTERVALS (carry-in {label}):")
        if not intervals:
            print("    (none)")
        for s, e in intervals:
            inside = "  <- overlaps this season" if e >= start and s <= end else ""
            print(f"    {s} .. {e}{inside}")
        print()

    dates = sample_dates(start, end, args.interval)
    print(f"DAY BY DAY ({len(dates)} sampled dates)")
    print(f"  {'date':<12} {'code':<6} {'roster says':<12} {'model says':<11} verdict")

    agree = over = under = missing = 0
    for d in dates:
        try:
            active = mlb._get(
                f"/teams/{args.team}/roster",
                {"rosterType": "active", "date": d.isoformat()},
            ).get("roster", [])
            forty = mlb._get(
                f"/teams/{args.team}/roster",
                {"rosterType": "40Man", "date": d.isoformat()},
            ).get("roster", [])
        except Exception as exc:
            print(f"  {d}  FAILED ({exc})", file=sys.stderr)
            continue

        active_ids = {(e.get("person") or {}).get("id") for e in active}
        entry = next(
            (e for e in forty if (e.get("person") or {}).get("id") == args.player), None
        )
        if entry is None:
            missing += 1
            print(f"  {d}  {'--':<6} {'not on 40-man':<12} {'':<11} (not compared)")
            continue

        code = ((entry.get("status") or {}).get("code") or "").upper()
        truth = args.player in active_ids or is_il_code(code)
        model = any(s <= d <= e for s, e in variants["on"])
        if model == truth:
            verdict, _ = "agree", agree
            agree += 1
        elif model:
            verdict = "MODEL OVER"
            over += 1
        else:
            verdict = "MODEL UNDER"
            under += 1
        print(
            f"  {d}  {code:<6} {('accruing' if truth else 'not accruing'):<12} "
            f"{('accruing' if model else 'not'):<11} {verdict}"
        )

    print(
        f"\n  agree {agree} | over {over} | under {under} | "
        f"{missing} date(s) he was not on this club's 40-man"
    )


if __name__ == "__main__":
    main()
