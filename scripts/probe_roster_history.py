#!/usr/bin/env python3
"""
probe_roster_history.py
------------------------
Tests whether the roster endpoint can answer "who was on this club's roster
on date X" for a date in the past.

Why it matters
--------------
Everything in this project is inferred from transaction *wording*, and every
bug found so far has come from that inference being wrong in a way nobody
could see without a reference. Service time is defined as days on an active
roster or major league IL -- so if

    /teams/{id}/roster?rosterType=active&date=2015-06-15

really returns the roster as it stood on that date, then the definition is
directly observable and we can measure how accurate the transaction-derived
intervals are, instead of guessing.

That would not replace the transaction pipeline. Reconstructing every
player's career this way costs roughly 30 clubs x 186 days x N seasons of
API calls, which is far too many. But it is ideal for VALIDATION: sample a
few dozen dates for a player, compare against what our intervals claim, and
get a real accuracy number.

What this checks
----------------
1. Does the endpoint accept a `date` parameter at all?
2. Does it actually honour it -- i.e. do two distant dates return different
   rosters? (An endpoint that silently ignores `date` and returns today's
   roster would be worse than useless: it would look like it worked.)
3. Do the rosters look sane for their era -- players whose careers match?

Run via Actions -> "Probe Transaction Coverage" -> probe: roster-history.
The sandbox cannot reach statsapi.mlb.com.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import fetch_mlb_data as mlb  # noqa: E402

# New York Yankees; any long-established club works.
PROBE_TEAM = 147
PROBE_DATES = ["2012-06-15", "2018-06-15", "2024-06-15"]
ROSTER_TYPES = ["active", "40Man"]


def fetch(team_id: int, roster_type: str, date: str | None) -> list[dict] | str:
    params = {"rosterType": roster_type}
    if date:
        params["date"] = date
    try:
        data = mlb._get(f"/teams/{team_id}/roster", params)
    except Exception as exc:
        return f"ERROR {exc}"
    return data.get("roster", [])


def names(roster) -> set[str]:
    if isinstance(roster, str):
        return set()
    return {(e.get("person") or {}).get("fullName", "?") for e in roster}


def main() -> None:
    print(f"Probing team {PROBE_TEAM} roster history.\n")

    today = fetch(PROBE_TEAM, "active", None)
    today_names = names(today)
    print(f"active roster, no date param: {len(today_names)} players")
    print(f"  sample: {sorted(today_names)[:4]}\n")

    honoured = True
    seen: dict[str, set[str]] = {}

    for rtype in ROSTER_TYPES:
        print(f"--- rosterType={rtype} ---")
        for date in PROBE_DATES:
            roster = fetch(PROBE_TEAM, rtype, date)
            if isinstance(roster, str):
                print(f"  {date}: {roster}")
                honoured = False
                continue
            n = names(roster)
            seen[f"{rtype}:{date}"] = n
            overlap = len(n & today_names)
            print(
                f"  {date}: {len(n):>3} players, {overlap:>3} also on today's roster"
            )
            print(f"      sample: {sorted(n)[:4]}")
        print()

    # The decisive check: distant dates must differ from each other and from
    # today. If they are identical the parameter is being ignored.
    a = seen.get(f"active:{PROBE_DATES[0]}", set())
    b = seen.get(f"active:{PROBE_DATES[-1]}", set())
    print("=" * 70)
    if not a or not b:
        print("VERDICT: could not retrieve dated rosters -- endpoint does not "
              "support historical lookup, or the parameter shape is different.")
        return
    if a == b == today_names:
        print("VERDICT: `date` is IGNORED -- every date returned today's roster.\n"
              "Do NOT build validation on this endpoint.")
        return
    if a == b:
        print("VERDICT: two dates twelve years apart returned identical rosters.\n"
              "`date` is probably being ignored. Treat as unusable.")
        return

    print(
        f"VERDICT: `date` IS honoured.\n"
        f"  {PROBE_DATES[0]} and {PROBE_DATES[-1]} differ by "
        f"{len(a ^ b)} players; {len(a & today_names)} of the {PROBE_DATES[0]} "
        f"roster are still on today's.\n"
        "Historical rosters are observable, so service time can be validated\n"
        "directly against them by sampling dates. See CLAUDE.md."
    )


if __name__ == "__main__":
    main()
