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

# Status codes are NOT hardcoded from assumption -- they are calibrated from
# the data on every run. See calibrate_codes().
#
# The first version of this script did hardcode them, and got it wrong in a
# way that produced a confident, plausible, completely misleading number.
# "RM" was guessed to mean the restricted list (accruing). It is in fact what
# an optioned player gets -- there is no "OPT" code in the feed at all -- so a
# fifth of the sample was inverted and the run reported 23.3% under-crediting
# that did not exist. Guessing at MLB's semantics is the single most reliable
# way to break this project.
#
# Only these two are safe a priori, because their meaning is unambiguous:
CERTAIN_ACCRUING = {"A"}  # Active
# ...and anything matching the injured-list shape (D7/D10/D15/D60), since a
# player on his club's major league IL is accruing by definition.
IL_CODE_PREFIX = "D"


def calibrate_codes(
    observations: list[tuple[str, dt.date, dt.date | None]],
) -> tuple[set[str], set[str], dict[str, int]]:
    """
    Work out which status codes mean "accruing" from evidence, not assumption.

    The lever is the debut date: a player cannot be earning major league
    service time before he has ever played in the majors. So any code seen on
    a date earlier than that player's mlbDebutDate cannot be an accruing code.
    That single rule is enough to classify the codes that matter, and it uses
    only data the pipeline already fetches.

    `observations` is (code, date, debut_date) per player-date.

    Returns (accruing, non_accruing, pre_debut_counts).
    """
    seen: set[str] = set()
    pre_debut: dict[str, int] = collections.Counter()

    for code, date, debut in observations:
        seen.add(code)
        if debut is not None and date < debut:
            pre_debut[code] += 1

    non_accruing = {c for c in seen if pre_debut.get(c)}
    accruing = set()
    for c in seen - non_accruing:
        if c in CERTAIN_ACCRUING or (c.startswith(IL_CODE_PREFIX) and c[1:].isdigit()):
            accruing.add(c)

    return accruing, non_accruing, dict(pre_debut)


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

    # --- collect raw roster observations -----------------------------------
    raw_obs: list[tuple[int, dt.date, str]] = []
    names: dict[int, str] = {}
    codes = collections.Counter()

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
            raw_obs.append((pid, d, code))

    print(f"{len(raw_obs)} roster observations across {len(names)} players.")
    print(f"status codes seen: {dict(codes.most_common())}\n")

    # --- bios (needed for calibration and for the model) -------------------
    debuts: dict[int, dt.date | None] = {}
    for pid in names:
        try:
            bio = mlb.get_player_bio(pid)
            dd = bio.get("mlbDebutDate")
            debuts[pid] = dt.date.fromisoformat(dd) if dd else None
        except Exception:
            debuts[pid] = None

    # --- calibrate what the codes mean -------------------------------------
    accruing, non_accruing, pre_debut = calibrate_codes(
        [(code, d, debuts.get(pid)) for pid, d, code in raw_obs]
    )
    print("code calibration (from debut dates, not assumption):")
    for c in sorted(codes):
        if c in non_accruing:
            print(f"  {c:<5} NOT accruing -- seen {pre_debut[c]}x before a player's MLB debut")
        elif c in accruing:
            why = "Active" if c in CERTAIN_ACCRUING else "injured-list shape"
            print(f"  {c:<5} accruing ({why})")
        else:
            print(f"  {c:<5} UNCLASSIFIED -- excluded from the comparison")
    unclassified = {c: n for c, n in codes.items() if c not in accruing and c not in non_accruing}
    if unclassified:
        share = sum(unclassified.values()) / max(sum(codes.values()), 1)
        print(f"!! {share:.1%} of observations are unclassified {unclassified}. "
              "The accuracy figure below covers only the rest.")
    print()

    truth: dict[int, dict[dt.date, bool]] = collections.defaultdict(dict)
    for pid, d, code in raw_obs:
        if code in accruing:
            truth[pid][d] = True
        elif code in non_accruing:
            truth[pid][d] = False

    print(f"Collected {sum(len(v) for v in truth.values())} player-date judgements "
          f"across {len(truth)} players.\n")

    # --- our model ---------------------------------------------------------
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
