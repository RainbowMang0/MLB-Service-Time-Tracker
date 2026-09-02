#!/usr/bin/env python3
"""
accrual_model.py
----------------
Measures, from this project's own database, how many service days a player
ACTUALLY accrued in the season after one in which he was in the majors.

WHY THIS EXISTS
===============
The contract tools have to answer "when do I reach free agency?" and "what
does the arbitration path look like?". Both need a view of future accrual,
and there are two ways to get one:

  1. Assume a full season (172 days) every year. Simple, and wrong in the
     direction that matters -- it produces the most optimistic possible date
     and presents it as the answer.
  2. Measure what comparable players actually did.

This does (2). The project already holds 5,578 players with season-by-season
day counts, which is the whole league for two decades. That is a real
population, not a prior.

THE CONDITIONING, AND WHY IT IS WHAT A READER IS ASKING
=======================================================
A user opening this is on a major league roster now. So the population is
player-seasons where the player accrued at least one day in the PREVIOUS
season, and the measurement is what he accrued in the NEXT one. Including
players who were already out of baseball would drag every band toward zero
and answer a question nobody asked.

Bands are by cumulative credited service at the START of the season, because
that is what a user knows about himself.

WHAT IS EXCLUDED, AND WHY
=========================
  * 2020. Prorated by agreement (186/B), so its day counts are not
    comparable with any other season. Including it would put a spike at 172
    in every band.
  * The current season. It is in progress, so its counts are not final and
    would understate every band.
  * Seasons marked `presumed` -- credited from the debut date rather than
    from transactions. They cluster at the 172 cap by construction, so they
    would bias the distribution upward exactly where the feed is thinnest.

OUTPUT
======
docs/data/accrual_model.json -- p20/p50/p80 days per band, plus the sample
size behind each. The sample size ships too: a band measured on 40
player-seasons should not be read like one measured on 4,000, and the UI
says which it is.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cba  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "docs" / "data" / "service_time.json"
OUT_PATH = ROOT / "docs" / "data" / "accrual_model.json"

# Prorated by agreement; its day counts are not comparable. Read from the
# ruleset rather than hardcoded, for the same reason as everything else.
EXCLUDED_SEASONS = set(cba.default().shortened_seasons())

# A band needs enough player-seasons for a percentile to mean anything. Same
# instinct as MIN_CLASS_SIZE in super_two.py: below this, say so rather than
# publishing a confident-looking number drawn from a handful of careers.
MIN_BAND_SAMPLE = 100


def percentile(sorted_values: list[int], p: float) -> int:
    """Nearest-rank percentile. No interpolation: these are whole days."""
    if not sorted_values:
        return 0
    k = max(0, min(len(sorted_values) - 1, int(round(p * (len(sorted_values) - 1)))))
    return sorted_values[k]


def build(db: list[dict], current_year: int, full_year_days: int) -> dict:
    # band index -> [days accrued in the following season]
    bands: dict[int, list[int]] = {}
    transitions = 0

    for player in db:
        seasons = player.get("seasons") or []
        if not seasons:
            continue
        rows = sorted(seasons, key=lambda s: int(s["y"]))
        cumulative = 0
        by_year = {int(r["y"]): r for r in rows}

        for row in rows:
            year = int(row["y"])
            days = int(row.get("d") or 0)

            # Was he in the majors this season? That is the condition the
            # reader satisfies when he opens this page.
            in_majors = days > 0 and year not in EXCLUDED_SEASONS

            nxt = by_year.get(year + 1)
            if (
                in_majors
                and nxt is not None
                and (year + 1) not in EXCLUDED_SEASONS
                and (year + 1) < current_year  # the current season is unfinished
                and nxt.get("src") != "presumed"
                and row.get("src") != "presumed"
            ):
                band = min(6, cumulative // full_year_days)
                bands.setdefault(band, []).append(int(nxt.get("d") or 0))
                transitions += 1

            cumulative += days

    out_bands = []
    for band in range(7):
        values = sorted(bands.get(band, []))
        label = f"{band}.000-{band + 1}.000" if band < 6 else "6.000+"
        entry = {
            "band": band,
            "label": label,
            "sample": len(values),
            "enough_data": len(values) >= MIN_BAND_SAMPLE,
        }
        if values:
            entry.update(
                {
                    "p20": percentile(values, 0.20),
                    "p50": percentile(values, 0.50),
                    "p80": percentile(values, 0.80),
                    "mean": round(sum(values) / len(values), 1),
                    "share_full_year": round(
                        sum(1 for v in values if v >= full_year_days) / len(values), 3
                    ),
                    "share_zero": round(sum(1 for v in values if v == 0) / len(values), 3),
                }
            )
        out_bands.append(entry)

    return {
        "generated_at": dt.date.today().isoformat(),
        "source": (
            "Measured from this project's own database: every player-season "
            "transition where the player was in the majors in the first season."
        ),
        "method": (
            "For each player-season in which a player accrued at least one day, "
            "record what he accrued the FOLLOWING season. Banded by cumulative "
            "credited service at the start of that following season. Excludes "
            "prorated seasons, the current unfinished season, and seasons "
            "credited by debut presumption rather than transactions."
        ),
        "caveats": [
            "This is what comparable players DID, not a forecast for any "
            "individual. A player's own health, role and club decide his year.",
            "It is measured from estimated service time, so it inherits every "
            "limitation of the estimates behind it.",
            "Bands with a small sample are marked enough_data: false and should "
            "not be presented as a distribution.",
        ],
        "full_year_days": full_year_days,
        "transitions": transitions,
        "min_band_sample": MIN_BAND_SAMPLE,
        "bands": out_bands,
    }


def main() -> int:
    rules = cba.default()
    full_year_days = rules.require("service_time.days_per_credited_year")
    db = json.loads(DB_PATH.read_text())["players"]
    model = build(db, dt.date.today().year, full_year_days)

    OUT_PATH.write_text(json.dumps(model, indent=1) + "\n", encoding="utf-8")

    print(f"{model['transitions']} player-season transitions measured")
    print(f"{'band':<14} {'n':>6}  {'p20':>4} {'p50':>4} {'p80':>4}   full yr   zero")
    for b in model["bands"]:
        if not b.get("sample"):
            print(f"{b['label']:<14} {0:>6}  (no data)")
            continue
        flag = "" if b["enough_data"] else "  <- thin"
        print(
            f"{b['label']:<14} {b['sample']:>6}  {b['p20']:>4} {b['p50']:>4} "
            f"{b['p80']:>4}   {b['share_full_year']:>5.0%}   {b['share_zero']:>4.0%}{flag}"
        )
    print(f"\nWrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
