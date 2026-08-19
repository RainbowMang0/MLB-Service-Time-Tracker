#!/usr/bin/env python3
"""
generate_demo_data.py
----------------------
Builds a small, realistic-looking docs/data/service_time.json using
HAND-WRITTEN sample transactions (no network calls). This exists purely so
the site has something meaningful to show immediately after download/deploy,
before you've run the real update_service_time.py against the live MLB Stats
API (which this sandbox's network can't reach, but GitHub Actions or your own
machine can).

Run: python scripts/generate_demo_data.py
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from service_time import SeasonWindow, Transaction, compute_service_time  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT_FILE = ROOT / "docs" / "data" / "service_time.json"

TODAY = dt.date(2026, 8, 15)


def season_window(year: int) -> SeasonWindow:
    return SeasonWindow(year, dt.date(year, 3, 28), dt.date(year, 10, 1))


SAMPLE_PLAYERS = [
    {
        "id": 1001,
        "name": "Jordan Castillo",
        "team": "Sample City Marlins",
        "position": "SP",
        "debut_year": 2019,
        "txns": [
            (2019, 6, 3, "Team selected the contract of Jordan Castillo"),
            (2020, 8, 1, "Team optioned Jordan Castillo to Triple-A"),
            (2020, 9, 1, "Team recalled Jordan Castillo from Triple-A"),
        ],
    },
    {
        "id": 1002,
        "name": "Miguel Ortega",
        "team": "Riverside Aviators",
        "position": "2B",
        "debut_year": 2022,
        "txns": [
            (2022, 5, 12, "Team purchased the contract of Miguel Ortega"),
            (2023, 4, 15, "Team placed Miguel Ortega on the 10-day injured list"),
            (2023, 5, 1, "Team activated Miguel Ortega from the 10-day injured list"),
        ],
    },
    {
        "id": 1003,
        "name": "Devon Whitfield",
        "team": "Lakeshore Anchors",
        "position": "CF",
        "debut_year": 2024,
        "txns": [
            (2024, 9, 2, "Team selected the contract of Devon Whitfield"),
            (2025, 3, 25, "Team optioned Devon Whitfield to Triple-A"),
            (2025, 6, 10, "Team recalled Devon Whitfield"),
        ],
    },
    {
        "id": 1004,
        "name": "Casey Nakamura",
        "team": "Prairie Wind Sox",
        "position": "RP",
        "debut_year": 2017,
        "txns": [
            (2017, 4, 6, "Team selected the contract of Casey Nakamura"),
        ],
    },
    {
        "id": 1005,
        "name": "Blake Sorensen",
        "team": "Sample City Marlins",
        "position": "3B",
        "debut_year": 2026,
        "txns": [
            (2026, 6, 20, "Team selected the contract of Blake Sorensen"),
        ],
    },
    {
        "id": 1006,
        "name": "Ellis Marchetti",
        "team": "Riverside Aviators",
        "position": "C",
        "debut_year": 2021,
        "txns": [
            (2021, 7, 1, "Team purchased the contract of Ellis Marchetti"),
            (2021, 8, 15, "Team optioned Ellis Marchetti to Double-A"),
            (2022, 4, 1, "Team recalled Ellis Marchetti"),
            (2024, 7, 30, "Team A traded Ellis Marchetti to Team B"),
        ],
    },
    {
        "id": 1007,
        "name": "Trent Ibarra",
        "team": "Lakeshore Anchors",
        "position": "SP",
        "debut_year": 2016,
        "txns": [
            (2016, 5, 5, "Team selected the contract of Trent Ibarra"),
        ],
        # No longer on a 40-man roster -- demonstrates the "previous
        # players" archive behavior.
        "on_40_man": False,
        "retired_after": 2024,
    },
]


def build_record(p: dict) -> dict:
    years = range(p["debut_year"], min(p.get("retired_after", TODAY.year), TODAY.year) + 1)
    seasons = [season_window(y) for y in years]
    txns = [Transaction(dt.date(y, m, d), desc) for (y, m, d, desc) in p["txns"]]
    horizon = seasons[-1].end if p.get("on_40_man", True) is False else min(TODAY, seasons[-1].end)
    result = compute_service_time(txns, seasons, horizon_end=horizon)
    return {
        "id": p["id"],
        "name": p["name"],
        "team": p["team"],
        "team_id": None,
        "position": p["position"],
        "mlb_debut": f"{p['debut_year']}-04-01",
        "service_time": result.formatted,
        "service_days_total": result.total_days,
        "free_agent_eligible": result.is_free_agent_eligible,
        "arbitration_eligible": result.is_arbitration_eligible,
        "super_two_candidate": result.is_super_two_candidate,
        "on_40_man": p.get("on_40_man", True),
        "last_updated": TODAY.isoformat(),
    }


def main():
    players = [build_record(p) for p in SAMPLE_PLAYERS]
    output = {
        "generated_at": dt.datetime.combine(TODAY, dt.time(8, 0), tzinfo=dt.timezone.utc).isoformat(),
        "source": "DEMO DATA -- hand-written sample transactions, not live MLB data",
        "disclaimer": (
            "Service time figures are ESTIMATES computed from public roster "
            "transaction records, not official MLB/MLBPA figures. This "
            "specific file is sample/demo data for preview purposes -- run "
            "scripts/update_service_time.py against the live MLB Stats API "
            "to populate real data."
        ),
        "player_count": len(players),
        "players": sorted(players, key=lambda p: p["name"]),
    }
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"Wrote {len(players)} demo players to {OUTPUT_FILE}")
    for p in output["players"]:
        print(f"  {p['name']:20s} {p['service_time']:>7s}  40-man={p['on_40_man']}")


if __name__ == "__main__":
    main()
