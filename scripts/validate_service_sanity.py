#!/usr/bin/env python3
"""
validate_service_sanity.py
--------------------------
Two data checks the service-time rules keep getting wrong in opposite
directions, run against the published database:

  * a player-season with a substantial number of MLB games and about zero
    credited days (a recall or reinstatement the transaction feed never
    closed back open -- Strange-Gordon 2017, Butler, Allen, Sears)
  * a season with credited days AFTER the player's last MLB season, on a
    player who is not on a 40-man roster and whose season is not an
    injured-list stint (an open signing or IL placement that a release,
    outright, minor-league assignment, or free-agency election never
    closed -- Kaminsky, Snelten, Ravelo)

Pre-2009 seasons are not flagged. The transaction feed is sparse there and
those zeros are a different, already-measured problem.

Prints the before/after counts when run directly. Exits 1 when either
count is above zero so CI fails on a regression. `summarize()` returns the
counts without exiting, which is what the tests call.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from service_time import TRANSACTION_COVERAGE_START_YEAR  # noqa: E402

DB_PATH = ROOT / "docs" / "data" / "service_time.json"

# "Significant" games and "about zero" days. Not a CBA threshold: the bar
# for a season the stat feed says he clearly played and the clock did not.
SIGNIFICANT_GAMES = 20
NEAR_ZERO_DAYS = 5
# A few days after the last game with a stat line are roster time the stat
# line does not show (a September selection, an injured-list stay of a week).
# A month or more, with no injured-list mark, is the phantom-season bug.
PHANTOM_MONTH_DAYS = 30


def _games(stat: dict) -> int:
    if not isinstance(stat, dict):
        return 0
    best = 0
    for group in ("hitting", "pitching"):
        row = stat.get(group)
        if not isinstance(row, dict):
            continue
        try:
            best = max(best, int(row.get("gamesPlayed") or 0))
        except (TypeError, ValueError):
            continue
    return best


def summarize(players: list[dict]) -> dict:
    """Counts and a short sample of each failure.

    `zero_despite_games` is player-seasons. `days_after_last_season` is
    player-seasons too. Samples are (name, id, year, games or days).
    """
    zero = []
    phantom = []
    for player in players:
        stats = player.get("season_stats") or {}
        seasons = {
            int(row["y"]): row
            for row in (player.get("seasons") or [])
            if isinstance(row, dict) and row.get("y") is not None
        }
        game_years: dict[int, int] = {}
        for year_s, stat in stats.items():
            try:
                year = int(year_s)
            except (TypeError, ValueError):
                continue
            games = _games(stat)
            if games:
                game_years[year] = games
        for year, games in game_years.items():
            if year < TRANSACTION_COVERAGE_START_YEAR or games < SIGNIFICANT_GAMES:
                continue
            days = int((seasons.get(year) or {}).get("d") or 0)
            if days <= NEAR_ZERO_DAYS:
                zero.append((player.get("name"), player.get("id"), year, games, days))
        if not game_years or player.get("on_40_man"):
            continue
        last_game_year = max(game_years)
        for year, row in seasons.items():
            if year <= last_game_year:
                continue
            days = int(row.get("d") or 0)
            if days <= 0 or row.get("il"):
                continue
            phantom.append((player.get("name"), player.get("id"), year, days, last_game_year))
    zero.sort(key=lambda r: (-r[3], r[0] or "", r[2]))
    phantom.sort(key=lambda r: (-r[3], r[0] or "", r[2]))
    long_phantom = [row for row in phantom if row[3] >= PHANTOM_MONTH_DAYS]
    return {
        "zero_despite_games": len(zero),
        "days_after_last_season": len(phantom),
        "days_after_last_season_month": len(long_phantom),
        "zero_sample": zero[:12],
        "phantom_sample": phantom[:12],
    }


def main() -> int:
    db = json.loads(DB_PATH.read_text())
    players = db.get("players") or []
    report = summarize(players)
    print(
        f"Seasons with {SIGNIFICANT_GAMES}+ MLB games and <={NEAR_ZERO_DAYS} "
        f"service days (from {TRANSACTION_COVERAGE_START_YEAR}): "
        f"{report['zero_despite_games']}"
    )
    for name, pid, year, games, days in report["zero_sample"]:
        print(f"    {name} ({pid}) {year}: {games} games, {days} days")
    print(
        "Seasons with service days after the player's last MLB season, "
        f"off the 40-man, not an injured-list season: {report['days_after_last_season']} "
        f"({report['days_after_last_season_month']} of them are "
        f"{PHANTOM_MONTH_DAYS}+ days)"
    )
    for name, pid, year, days, last in report["phantom_sample"]:
        print(f"    {name} ({pid}) {year}: {days} days, last MLB season {last}")
    if report["zero_despite_games"] or report["days_after_last_season_month"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
