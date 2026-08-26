#!/usr/bin/env python3
"""
make_reference_worksheet.py
---------------------------
Pick the players worth hand-checking against Baseball Reference, and emit
rows ready to paste into data/reference_service_time.json.

WHY THIS EXISTS
===============
The roster check (validate_against_rosters.py) is broad but not independent:
it validates the pipeline against the same MLB feed the pipeline is built
from, so a systematic misreading of MLB's semantics passes it. The Baseball
Reference check is the only genuinely independent evidence this project has,
and it currently rests on nineteen figures.

Those figures are entered BY HAND, deliberately -- both to respect B-R's
terms, which prohibit automated extraction, and because a handful of
hand-checked numbers is enough to catch a systemic error. Scraping five
thousand would add accuracy the project could not legitimately use.

So the constraint is human effort, and the useful thing a script can do is
spend that effort well: choose the ~40 players whose figures would actually
discriminate between a correct model and a broken one, rather than 40
arbitrary names.

HOW PLAYERS ARE CHOSEN
======================
Only players whose records claim COMPLETE history are eligible. A player with
missing seasons scores GAP rather than pass/fail, so his figure cannot fail
the check and tells us nothing about a regression.

Among those, priority goes to CHURN -- the count of roster moves the parser
had to get right. A player who was never optioned, never DFA'd and never
traded exercises almost none of the rules in service_time.py, so his figure
matching proves little; the same match on someone with thirty moves exercises
option boundaries, IL transitions, waiver claims and trades all at once.

Coverage is then spread deliberately across service-time bands and debut eras
so the sample cannot all be modern short-career players, and each requested
row says WHICH rule it is there to exercise, so a future reader knows why the
name is on the list.

USAGE
=====
    python scripts/make_reference_worksheet.py            # 40 players
    python scripts/make_reference_worksheet.py --count 25
    python scripts/make_reference_worksheet.py --json     # paste-ready rows

Offline: reads the published database and the transaction cache only.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from service_time import is_active_start, is_active_stop, is_trade  # noqa: E402

DB_PATH = ROOT / "docs" / "data" / "service_time.json"
CACHE = ROOT / "data" / "cache" / "transactions"
REFERENCE = ROOT / "data" / "reference_service_time.json"

# What a player's history has to contain for his figure to test a given rule.
RULE_MARKERS = {
    "option boundaries": ("optioned", "recalled"),
    "DFA / outright": ("designated", "outright"),
    "waiver claim": ("claimed",),
    "trade mid-career": ("traded",),
    "injured list": ("injured list", "disabled list"),
    "Rule 5 return": ("returned to",),
}


def churn(descriptions: list[str]) -> int:
    """Roster moves the interval walk had to classify correctly."""
    return sum(
        1 for d in descriptions
        if is_active_start(d) or is_active_stop(d) or is_trade(d)
    )


def rules_exercised(descriptions: list[str]) -> list[str]:
    blob = " ".join(descriptions).lower()
    return [rule for rule, marks in RULE_MARKERS.items()
            if any(m in blob for m in marks)]


def band(days: int) -> str:
    years = days // 172
    if years >= 12:
        return "12+ yrs"
    if years >= 8:
        return "8-11 yrs"
    if years >= 5:
        return "5-7 yrs"
    if years >= 3:
        return "3-4 yrs"
    return "under 3 yrs"


def era(debut: str | None) -> str:
    if not debut:
        return "no debut"
    year = int(debut[:4])
    if year < 2012:
        return "pre-2012"
    if year < 2018:
        return "2012-2017 (disabled list wording)"
    return "2018+ (injured list wording)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=40)
    ap.add_argument("--json", action="store_true", help="emit paste-ready JSON rows")
    args = ap.parse_args()

    db = json.loads(DB_PATH.read_text())["players"]
    already = {
        row.get("player_id")
        for row in json.loads(REFERENCE.read_text())
        if isinstance(row, dict) and row.get("player_id")
    }

    candidates = []
    for p in db:
        if not p.get("on_40_man"):
            continue          # B-R pages are easiest to read for current players
        if p.get("missing_seasons"):
            continue          # would score GAP, so it cannot fail -- no signal
        if p["id"] in already:
            continue
        if not p.get("service_days_total"):
            continue
        cache_file = CACHE / f"{p['id']}.json"
        if not cache_file.exists():
            continue
        try:
            rows = json.loads(cache_file.read_text())
        except ValueError:
            continue
        rows = rows if isinstance(rows, list) else rows.get("transactions", [])
        descriptions = [r.get("description") or "" for r in rows]
        candidates.append({
            "player": p,
            "churn": churn(descriptions),
            "rules": rules_exercised(descriptions),
        })

    if not candidates:
        raise SystemExit("No eligible candidates -- is the cache populated?")

    # Spread across bands and eras rather than taking the top N by churn, which
    # would be all veterans of one era.
    by_bucket: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for c in candidates:
        key = (band(c["player"]["service_days_total"]), era(c["player"].get("mlb_debut")))
        by_bucket[key].append(c)
    for bucket in by_bucket.values():
        bucket.sort(key=lambda c: -c["churn"])

    picked: list[dict] = []
    round_no = 0
    while len(picked) < args.count:
        took_any = False
        for key in sorted(by_bucket):
            bucket = by_bucket[key]
            if round_no < len(bucket) and len(picked) < args.count:
                picked.append(bucket[round_no])
                took_any = True
        if not took_any:
            break
        round_no += 1

    today = dt.date.today().isoformat()
    if args.json:
        rows = [{
            "player_id": c["player"]["id"],
            "name": c["player"]["name"],
            "as_of": today,
            "expected": None,
            "source": "baseball-reference.com player page, service-time snapshot",
            "mlb_debut": c["player"].get("mlb_debut"),
            "history_complete": True,
            "ours_at_generation": c["player"].get("service_time"),
            "why_this_player": f"{c['churn']} roster moves; exercises "
                               f"{', '.join(c['rules']) or 'few rules'}",
        } for c in picked]
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    print(f"{len(picked)} players worth hand-checking against Baseball Reference")
    print(f"(from {len(candidates)} eligible: on a 40-man, complete history, not "
          f"already in the reference file)\n")
    print(f"{'player':<26} {'ours':>8}  {'moves':>5}  what its figure would exercise")
    print("-" * 100)
    for c in picked:
        p = c["player"]
        print(f"{p['name']:<26} {p.get('service_time', ''):>8}  {c['churn']:>5}  "
              f"{', '.join(c['rules']) or '(few rules)'}")

    print("\nHow to use this:")
    print("  1. Open each player's Baseball Reference page.")
    print("  2. In the bio block, read the Service Time snapshot, e.g. '9.051 (01/26)'.")
    print("  3. Add a row to data/reference_service_time.json with that figure as")
    print("     `expected` and the snapshot's date as `as_of`. Run with --json to")
    print("     get the rows prefilled with everything except the figure itself.")
    print("  4. Actions -> 'Validate Service Time' -> check: reference")
    print("\nRows left null are skipped, so filling in five is still progress.")


if __name__ == "__main__":
    main()
