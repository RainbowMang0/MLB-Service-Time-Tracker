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

Sweeping many club-seasons
--------------------------
--team and --season both take comma-separated lists and are crossed, so

    --team 147,139,114,115 --season 2014,2018,2022

is sixteen club-seasons in one run. Players and their transactions are cached
across the whole sweep, so a club sampled in three seasons costs one fetch per
player rather than three.

Breadth is worth more than depth here for one specific reason: a single
club-season shows a defect that lives in the years BEFORE it as a handful of
missing days, which is how the carry-in bug (nine years on Justin Verlander)
nearly escaped. More clubs and more eras is the cheap way to find the shapes
one club never produces -- Tampa Bay's relentless optioning is what exposed
finding #10, which the Yankees seasons barely saw.

The run also classifies every disagreement by the roster move on either side
of it and prints the counts, because a percentage names no rule to change.

Requires live API access, so run it via Actions -> "Validate Service Time".
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import fetch_mlb_data as mlb  # noqa: E402
from service_time import (  # noqa: E402
    Transaction,
    build_global_active_intervals,
    roster_start_before_debut,
)
from update_service_time import (  # noqa: E402
    PRESUME_ACTIVE_FROM_DEBUT,
    _involves_mlb_club,
    mlb_team_ids,
)


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
# NOTE the second failure above was not just a bug in this script: it meant
# `accrual_floor = mlbDebutDate` in the pipeline under-counted players who sat
# on a roster before debuting. That is finding #15, fixed in rules version 3,
# and this script applies the same rule -- see the floor computed below.

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


# --- what SHAPE do we get wrong? --------------------------------------------
#
# A club-season summary says "0.4% over-credited" and stops there, which is
# where every previous round of this stalled: a percentage names no rule to
# change. So each disagreeing (player, date) is labelled with the roster move
# on either side of it, and those labels are counted.
#
# A rule is worth writing when one label dominates. Findings #13, #14 and #16
# were all found by hand this way, one player at a time; this does it over
# whatever sample the run covers.

_SHAPE_PATTERNS: list[tuple[str, str]] = [
    ("recalled",        r"\brecalled\b"),
    ("optioned",        r"\boptioned\b"),
    ("selected",        r"selected the contract|purchased the contract|contract selected"),
    ("activated-IL",    r"\bactivated\b.{0,40}(injured|disabled) list|reinstated"),
    ("placed-IL",       r"placed .{0,40}(injured|disabled) list"),
    ("DFA",             r"designated .{0,40}for assignment"),
    ("outright",        r"sent .{0,40}outright|assigned .{0,40}outright"),
    ("waiver-claim",    r"claimed .{0,40}off waivers"),
    ("traded",          r"\btraded\b"),
    ("released",        r"\breleased\b"),
    ("free-agency",     r"elected free agency|signed as a free agent|signed free agent"),
    ("rule-5-return",   r"returned to (?!the active roster)"),
    ("activated",       r"\bactivated\b"),
    ("assigned",        r"\bassigned\b"),
    ("signed",          r"\bsigned\b"),
]
_SHAPE_RES = [(label, re.compile(pat, re.IGNORECASE)) for label, pat in _SHAPE_PATTERNS]


def shape_of(description: str) -> str:
    for label, rx in _SHAPE_RES:
        if rx.search(description):
            return label
    return "other"


def classify(
    txns: list["Transaction"], day: "dt.date"
) -> tuple[str, str]:
    """Label a disagreeing date by the roster move before and after it.

    Returned as "<shape>+<n>d" for the preceding move and "<shape>-<n>d" for
    the following one, so a one-day round trip reads `recalled+0d / optioned-1d`
    and is immediately recognisable across many players.
    """
    before = [t for t in txns if t.date <= day]
    after = [t for t in txns if t.date > day]
    prev = max(before, key=lambda t: t.date) if before else None
    nxt = min(after, key=lambda t: t.date) if after else None
    prev_s = f"{shape_of(prev.description)}+{(day - prev.date).days}d" if prev else "(nothing before)"
    next_s = f"{shape_of(nxt.description)}-{(nxt.date - day).days}d" if nxt else "(nothing after)"
    return prev_s, next_s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--team", required=True,
        help="MLB team id, e.g. 147. Comma-separate for a sweep: 147,139,114",
    )
    ap.add_argument(
        "--season", required=True,
        help="Season year. Comma-separate for a sweep: 2014,2018. Every team is "
             "crossed with every season, and club-seasons with no roster are skipped.",
    )
    ap.add_argument("--interval", type=int, default=7, help="days between samples")
    ap.add_argument(
        "--carry-in",
        choices=("off", "on", "both"),
        default="both",
        help="Carry-in rule: treat a player as rostered from his debut rather "
             "than crediting nothing until a start transaction fires. 'both' "
             "scores each way from the same fetched data and prints an A/B.",
    )
    args = ap.parse_args()

    variants = {"off": ["off"], "on": ["on"], "both": ["off", "on"]}[args.carry_in]

    teams = [int(t) for t in str(args.team).split(",") if t.strip()]
    seasons = [int(y) for y in str(args.season).split(",") if y.strip()]
    pairs = [(t, y) for y in seasons for t in teams]
    print(f"Sweeping {len(pairs)} club-season(s): "
          f"teams {teams} x seasons {seasons}")
    print(f"Sampling every {args.interval} days. carry-in scored: {', '.join(variants)}\n")

    club_ids = mlb_team_ids()
    names: dict[int, str] = {}
    codes = collections.Counter()
    off_roster_codes = collections.Counter()

    # Cached across the whole sweep. A player recurs in many club-seasons of
    # the same club, and his transactions and debut do not change between
    # them, so without this a 12-club-season run refetches the same player a
    # dozen times and spends its budget on nothing.
    txn_cache: dict[int, list[Transaction]] = {}
    debut_cache: dict[int, dt.date | None] = {}
    floor_cache: dict[int, dt.date | None] = {}

    tallies = {v: [0, 0, 0] for v in variants}
    per_player = {v: collections.defaultdict(lambda: [0, 0, 0]) for v in variants}
    per_season: dict[tuple[int, int], list[int]] = {}
    # Only the "on" variant is diagnosed: it is what production runs.
    shapes = {"over": collections.Counter(), "under": collections.Counter()}
    shape_examples: dict[tuple[str, str, str], list[str]] = collections.defaultdict(list)

    for team, season in pairs:
        try:
            season_start, season_end = mlb.get_season_window(season)
        except Exception as exc:
            print(f"  season {season}: FAILED ({exc})", file=sys.stderr)
            continue
        dates = sample_dates(season_start, season_end, args.interval)

        truth: dict[int, dict[dt.date, bool]] = collections.defaultdict(dict)
        for d in dates:
            try:
                active = mlb._get(
                    f"/teams/{team}/roster",
                    {"rosterType": "active", "date": d.isoformat()},
                ).get("roster", [])
                forty = mlb._get(
                    f"/teams/{team}/roster",
                    {"rosterType": "40Man", "date": d.isoformat()},
                ).get("roster", [])
            except Exception as exc:
                print(f"  {team} {d}: FAILED ({exc})", file=sys.stderr)
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
                    truth[pid][d] = True
                elif is_il_code(code):
                    truth[pid][d] = True
                else:
                    truth[pid][d] = False
                    off_roster_codes[code] += 1

        if not truth:
            print(f"  team {team} season {season}: no roster returned, skipped.")
            continue

        season_tally = [0, 0, 0]
        for pid, by_date in truth.items():
            if pid not in txn_cache:
                try:
                    raw = mlb.get_player_transactions(pid, dt.date(2005, 1, 1), dt.date.today())
                except Exception as exc:
                    print(f"  {names.get(pid)}: FAILED ({exc})", file=sys.stderr)
                    txn_cache[pid] = []
                    raw = []
                txn_cache[pid] = [
                    Transaction(
                        date=dt.date.fromisoformat(t["date"]),
                        description=t.get("description", ""),
                    )
                    for t in raw
                    if t.get("date") and _involves_mlb_club(flatten(t), club_ids)
                ]
                try:
                    bio = mlb.get_player_bio(pid)
                    dd = bio.get("mlbDebutDate")
                    debut_cache[pid] = dt.date.fromisoformat(dd) if dd else None
                except Exception:
                    debut_cache[pid] = None
                # Finding #15: the pipeline's floor, not the bare debut date.
                floor = debut_cache[pid]
                roster_start = roster_start_before_debut(txn_cache[pid], floor)
                floor_cache[pid] = roster_start if roster_start is not None else floor

            txns = txn_cache[pid]
            floor = floor_cache[pid]

            for variant in variants:
                intervals = build_global_active_intervals(
                    txns,
                    season_end,
                    accrual_floor=floor,
                    presume_active_from=floor if variant == "on" else None,
                )
                for d, actually_accruing in by_date.items():
                    model_says = any(a <= d <= b for a, b in intervals)
                    if model_says == actually_accruing:
                        idx = 0
                    elif model_says and not actually_accruing:
                        idx = 1
                    else:
                        idx = 2
                    tallies[variant][idx] += 1
                    per_player[variant][pid][idx] += 1
                    if variant == "on":
                        season_tally[idx] += 1
                        if idx:
                            kind = "over" if idx == 1 else "under"
                            prev_s, next_s = classify(txns, d)
                            shapes[kind][(prev_s, next_s)] += 1
                            ex = shape_examples[(kind, prev_s, next_s)]
                            if len(ex) < 3:
                                ex.append(f"{names.get(pid, pid)} {d}")

        per_season[(team, season)] = season_tally
        tot = sum(season_tally) or 1
        print(f"  team {team:>3} {season}: {len(truth):>3} players, "
              f"agree {season_tally[0]/tot:.1%}  over {season_tally[1]/tot:.1%}  "
              f"under {season_tally[2]/tot:.1%}")

    print(f"\n{sum(sum(v) for v in per_season.values())} player-date judgements "
          f"across {len(names)} players and {len(per_season)} club-season(s).")
    print(f"status codes seen: {dict(codes.most_common())}")
    print(f"treated as NOT accruing (off active roster, not IL): "
          f"{dict(off_roster_codes.most_common())}")
    print("  (truth is roster membership + IL shape; codes are reported, not interpreted)\n")

    scores = {v: report(v) for v in variants}

    # --- the actionable half ------------------------------------------------
    if "on" in variants and (shapes["over"] or shapes["under"]):
        print("=" * 70)
        print("WHAT SHAPE ARE THE DISAGREEMENTS? (carry-in on -- what production runs)")
        print("  read as <move before>+<days since> / <move after>-<days until>")
        for kind in ("over", "under"):
            counted = shapes[kind]
            if not counted:
                continue
            total = sum(counted.values())
            print(f"\n  MODEL {kind.upper()} -- {total} judgement(s), "
                  f"{len(counted)} distinct shape(s):")
            for (prev_s, next_s), n in counted.most_common(12):
                share = n / total
                ex = ", ".join(shape_examples[(kind, prev_s, next_s)])
                print(f"    {n:>4}  ({share:>5.1%})  {prev_s:<22} / {next_s:<22}  e.g. {ex}")
        print()
        print("  A shape that dominates is a candidate rule. A long tail of")
        print("  singletons is not -- it is the residue of a feed that does not")
        print("  record everything, and chasing it is how this project has")
        print("  historically shipped a plausible rule that measurement refused.")
        print()

    if len(variants) == 2:
        (a0, o0, u0), (a1, o1, u1) = scores["off"], scores["on"]
        print("=" * 70)
        print("A/B -- carry-in off -> on")
        print(f"  agreement     {a0:.1%} -> {a1:.1%}   ({a1-a0:+.1%})")
        print(f"  over-credit   {o0:.1%} -> {o1:.1%}   ({o1-o0:+.1%})")
        print(f"  under-credit  {u0:.1%} -> {u1:.1%}   ({u1-u0:+.1%})")
        print()
        print("Gate: agreement >=95% AND over-crediting <=2%.")
        print(f"  off  {'PASS' if a0 >= 0.95 and o0 <= 0.02 else 'FAIL'}")
        print(f"  on   {'PASS' if a1 >= 0.95 and o1 <= 0.02 else 'FAIL'}")
        print()
        print("Carry-in only ADDS accruing days, so under-crediting should fall")
        print("and over-crediting is the number to watch. A change that merely")
        print("inflates figures shows up as over-crediting rising with it.")


if __name__ == "__main__":
    main()
