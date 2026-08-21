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
from update_service_time import _involves_mlb_club, mlb_team_ids  # noqa: E402


def flatten(t: dict) -> dict:
    """
    Raw API row -> the shape the pipeline's MLB-club filter expects.

    The filter has to run here too. Without it the validator scores a
    DIFFERENT model than the one that actually ships: the raw feed carries
    minor league and exhibition rows phrased with the same verbs, so
    affiliate "activated" rows open intervals the real pipeline discards,
    and the measurement over-credits relative to production.
    """
    return {
        "team_id": (t.get("team") or {}).get("id"),
        "from_team_id": (t.get("fromTeam") or {}).get("id"),
        "to_team_id": (t.get("toTeam") or {}).get("id"),
    }

# Truth comes from ROSTER MEMBERSHIP, not from interpreting status codes.
# ============================================================================
# Two earlier attempts both failed by trying to read meaning into the codes:
#
#   1. Hardcoding them. "RM" was guessed to be the restricted list
#      (accruing); it is what an OPTIONED player gets -- there is no "OPT"
#      code in the feed at all. A fifth of the sample was inverted and the
#      run reported 23.3% under-crediting that did not exist.
#
#   2. Calibrating them from debut dates, on the rule that nothing accrues
#      before a player's MLB debut. That rule is FALSE: service time is days
#      on the active roster, and a September call-up who sits on the bench
#      for a week accrues every one of those days without appearing in a
#      game. So "A" legitimately shows up pre-debut -- 8 times in the 2014
#      Yankees sample -- which flipped "A" to non-accruing and manufactured
#      63.7% over-crediting out of nothing. Derek Jeter, who played 145
#      games that year, came out as over-credited on all 28 sampled dates.
#
# So this version interprets nothing. Service time is defined as days on the
# active roster or the major league IL, and both are directly observable:
#
#   * rosterType=active  -- membership IS the definition. No code needed.
#   * rosterType=40Man   -- adds the IL players, who are identifiable by the
#                           shape of their status code (D7/D10/D15/D60) and
#                           who accrue by definition.
#
# Anything on the 40-man that is neither on the active roster nor IL-shaped
# is not accruing. Whatever its code happens to mean is irrelevant.
#
# NOTE the second failure above is not just a bug in this script: it means
# `accrual_floor = mlbDebutDate` in the pipeline is also slightly wrong, and
# under-counts players who sat on a roster before debuting. See CLAUDE.md.

IL_CODE_PREFIX = "D"


def is_il_code(code: str) -> bool:
    """D7 / D10 / D15 / D60 -- a major league injured list, which accrues."""
    return code.startswith(IL_CODE_PREFIX) and code[1:].isdigit()


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

    # --- observe rosters ---------------------------------------------------
    truth: dict[int, dict[dt.date, bool]] = collections.defaultdict(dict)
    names: dict[int, str] = {}
    codes = collections.Counter()
    # Codes seen on players who are on the 40-man but NOT on the active
    # roster and NOT IL-shaped. These are treated as not accruing. Reported
    # so an unexpectedly common one (a paternity or bereavement list, say,
    # which would actually accrue) cannot hide.
    off_roster_codes = collections.Counter()

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
            print(f"  {d}: FAILED ({exc})", file=sys.stderr)
            continue

        active_ids = {
            (e.get("person") or {}).get("id")
            for e in active
            if (e.get("person") or {}).get("id") is not None
        }

        for entry in forty:
            person = entry.get("person") or {}
            pid = person.get("id")
            if pid is None:
                continue
            names[pid] = person.get("fullName", "?")
            code = ((entry.get("status") or {}).get("code") or "").upper()
            codes[code] += 1

            if pid in active_ids:
                truth[pid][d] = True  # on the active roster: accruing, by definition
            elif is_il_code(code):
                truth[pid][d] = True  # major league IL: accruing, by definition
            else:
                truth[pid][d] = False
                off_roster_codes[code] += 1

    print(f"{sum(len(v) for v in truth.values())} player-date judgements "
          f"across {len(names)} players.")
    print(f"status codes seen: {dict(codes.most_common())}")
    print(f"treated as NOT accruing (off active roster, not IL): "
          f"{dict(off_roster_codes.most_common())}")
    print("  (truth is roster membership + IL shape; codes are reported, not interpreted)\n")

    debuts: dict[int, dt.date | None] = {}
    for pid in names:
        try:
            bio = mlb.get_player_bio(pid)
            dd = bio.get("mlbDebutDate")
            debuts[pid] = dt.date.fromisoformat(dd) if dd else None
        except Exception:
            debuts[pid] = None

    # --- our model ---------------------------------------------------------
    club_ids = mlb_team_ids()
    over = under = agree = 0
    per_player: dict[int, list[int]] = collections.defaultdict(lambda: [0, 0, 0])

    for pid, by_date in truth.items():
        try:
            raw = mlb.get_player_transactions(pid, dt.date(2005, 1, 1), end)
        except Exception as exc:
            print(f"  {names.get(pid)}: FAILED ({exc})", file=sys.stderr)
            continue
        floor = debuts.get(pid)
        txns = [
            Transaction(date=dt.date.fromisoformat(t["date"]), description=t.get("description", ""))
            for t in raw
            if t.get("date") and _involves_mlb_club(flatten(t), club_ids)
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
