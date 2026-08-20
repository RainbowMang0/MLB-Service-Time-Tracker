#!/usr/bin/env python3
"""
update_service_time.py
-----------------------
Main entry point for the daily data refresh.

What it does, in order:
  1. Loads the existing player database (docs/data/service_time.json), if any.
     This is what lets "previous" players (no longer on a 40-man roster)
     stay logged even after they drop off. Think of it as an
     append-and-update database rather than a fresh snapshot every day.
  2. Pulls the current 40-man roster for all 30 teams from the MLB Stats API.
  3. For each of those players, fetches (and locally caches) their full
     transaction history, computes service time via service_time.py, and
     updates their record.
  4. Any player already in the database who is NOT on a 40-man roster today
     is kept as-is (marked `"on_40_man": false`) rather than deleted, so the
     site keeps a running log of previous players too.
  5. Writes the merged result back to docs/data/service_time.json, which is
     the file the static frontend reads.

Run it with no arguments for the normal incremental daily update:
    python scripts/update_service_time.py

Use --full-refresh to ignore the transaction cache and re-pull each
player's entire history (slower, but useful after changing the
service_time.py rules, or for the very first run):
    python scripts/update_service_time.py --full-refresh
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import fetch_mlb_data as mlb  # noqa: E402
from service_time import (  # noqa: E402
    SeasonWindow,
    Transaction,
    compute_service_time,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "docs" / "data"
OUTPUT_FILE = DATA_DIR / "service_time.json"
CACHE_DIR = ROOT / "data" / "cache" / "transactions"

MIN_TRANSACTION_YEAR = 2005  # don't bother reaching back further than this
TODAY = dt.date.today()


def _cache_path(player_id: int) -> pathlib.Path:
    return CACHE_DIR / f"{player_id}.json"


def _load_cached_transactions(player_id: int) -> list[dict]:
    p = _cache_path(player_id)
    if p.exists():
        return json.loads(p.read_text())
    return []


def _save_cached_transactions(player_id: int, txns: list[dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(player_id).write_text(json.dumps(txns, indent=2, default=str))


def _fetch_transactions_incremental(player_id: int, full_refresh: bool) -> list[dict]:
    cached = [] if full_refresh else _load_cached_transactions(player_id)

    if cached:
        last_date = max(dt.date.fromisoformat(t["date"]) for t in cached)
        start = last_date + dt.timedelta(days=1)
    else:
        start = dt.date(MIN_TRANSACTION_YEAR, 1, 1)

    if start > TODAY:
        return cached

    new_raw = mlb.get_player_transactions(player_id, start, TODAY)
    new_txns = [
        {
            "date": t.get("date"),
            "description": t.get("description", ""),
            "team": (t.get("toTeam") or {}).get("name") or (t.get("fromTeam") or {}).get("name"),
        }
        for t in new_raw
        if t.get("date")
    ]

    merged = {t["date"] + "|" + t["description"]: t for t in (cached + new_txns)}
    result = sorted(merged.values(), key=lambda t: t["date"])
    _save_cached_transactions(player_id, result)
    return result


def _season_windows_for(years: range) -> list[SeasonWindow]:
    windows = []
    for year in years:
        start, end = mlb.get_season_window(year)
        windows.append(SeasonWindow(year=year, start=start, end=end))
    return windows


_MLB_TEAM_IDS: set[int] | None = None


def mlb_team_ids() -> set[int]:
    """The 30 major league club IDs, fetched once and reused."""
    global _MLB_TEAM_IDS
    if _MLB_TEAM_IDS is None:
        _MLB_TEAM_IDS = {t["id"] for t in mlb.get_teams() if isinstance(t.get("id"), int)}
    return _MLB_TEAM_IDS


def _involves_mlb_club(txn: dict, mlb_ids: set[int]) -> bool:
    """
    True if this transaction involves an actual major league club.

    The /transactions endpoint returns a player's whole tracked history --
    high school showcases, college programs, minor league affiliates, and
    All-Star/Futures Game rosters included. Those are phrased with the same
    verbs as major league moves ("Grand Canyon Antelopes activated SS Jacob
    Wilson", "American League Futures activated LHP Gage Jump"), so matching
    on wording alone starts a player's service clock years before his debut.
    """
    seen_any_team = False
    for key in ("team", "fromTeam", "toTeam"):
        value = txn.get(key)
        if isinstance(value, dict) and isinstance(value.get("id"), int):
            seen_any_team = True
            if value["id"] in mlb_ids:
                return True
    # No usable team IDs on the row at all -- we can't judge it here, so keep
    # it and let the debut-date floor below do the filtering instead.
    return not seen_any_team


def build_player_record(roster_entry: dict, full_refresh: bool) -> dict:
    player_id = roster_entry["id"]
    raw_txns = _fetch_transactions_incremental(player_id, full_refresh)

    mlb_ids = mlb_team_ids()
    transactions = [
        Transaction(
            date=dt.date.fromisoformat(t["date"]),
            description=t["description"],
            team=(t.get("team") or {}).get("name") if isinstance(t.get("team"), dict) else t.get("team"),
        )
        for t in raw_txns
        if _involves_mlb_club(t, mlb_ids)
    ]

    bio = mlb.get_player_bio(player_id)
    debut = bio.get("mlbDebutDate")
    debut_date = dt.date.fromisoformat(debut) if debut else None

    # Service time cannot start before a player's major league debut. For the
    # rare player who reached an active roster without ever appearing in a
    # game (so has no debut date), fall back to his earliest major-league-club
    # transaction rather than crediting him from his college days.
    accrual_floor = debut_date
    if accrual_floor is None and transactions:
        accrual_floor = min(t.date for t in transactions)

    debut_year = debut_date.year if debut_date else MIN_TRANSACTION_YEAR
    first_year = max(debut_year, MIN_TRANSACTION_YEAR)
    years = range(first_year, TODAY.year + 1)
    seasons = _season_windows_for(years)

    result = compute_service_time(
        transactions,
        seasons,
        carry_in_active_first_season=False,
        accrual_floor=accrual_floor,
        # Stop the clock at today, not at the end of the current season --
        # otherwise every player currently on a roster is credited with the
        # remaining weeks of a season that hasn't been played yet.
        horizon_end=TODAY,
    )

    return {
        "id": player_id,
        "name": roster_entry.get("fullName") or bio.get("fullName"),
        "team": roster_entry.get("team"),
        "team_id": roster_entry.get("teamId"),
        "position": roster_entry.get("position"),
        "mlb_debut": debut,
        "service_time": result.formatted,
        "service_days_total": result.total_days,
        "free_agent_eligible": result.is_free_agent_eligible,
        "arbitration_eligible": result.is_arbitration_eligible,
        "super_two_candidate": result.is_super_two_candidate,
        "on_40_man": True,
        "last_updated": TODAY.isoformat(),
    }


# Real MLB person IDs are six digits; the bundled demo dataset uses 1001-1007.
# Anything below this threshold is sample data that shipped with the project
# and should never survive into a live run -- the merge logic deliberately
# never deletes players, so without this the fake names persist forever as
# "previous players."
MIN_REAL_PLAYER_ID = 100000


def _is_demo_record(player: dict) -> bool:
    try:
        return int(player.get("id", 0)) < MIN_REAL_PLAYER_ID
    except (TypeError, ValueError):
        return True


def load_existing_db() -> dict[str, dict]:
    if OUTPUT_FILE.exists():
        data = json.loads(OUTPUT_FILE.read_text())
        players = data.get("players", [])
        kept = {str(p["id"]): p for p in players if not _is_demo_record(p)}
        dropped = len(players) - len(kept)
        if dropped:
            print(f"Dropped {dropped} bundled demo player record(s).")
        return kept
    return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full-refresh", action="store_true", help="Ignore cache, re-pull full history.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N roster players (useful for local testing).",
    )
    args = parser.parse_args()

    print(f"[{dt.datetime.now().isoformat(timespec='seconds')}] Starting service-time update...")

    db = load_existing_db()
    print(f"Loaded existing database with {len(db)} previously-seen players.")

    roster = mlb.get_all_40man_players()
    print(f"Fetched {len(roster)} players currently on a 40-man roster.")
    if args.limit:
        roster = roster[: args.limit]

    current_ids = set()
    for i, entry in enumerate(roster, 1):
        pid = str(entry["id"])
        current_ids.add(pid)
        try:
            record = build_player_record(entry, args.full_refresh)
            db[pid] = record
            print(f"  [{i}/{len(roster)}] {record['name']}: {record['service_time']}")
        except Exception as exc:  # keep going even if one player fails
            print(f"  [{i}/{len(roster)}] FAILED for player {pid}: {exc}", file=sys.stderr)

    # Players no longer on a 40-man roster stay in the DB (the "previous
    # players" log) but get flagged as such.
    for pid, record in db.items():
        if pid not in current_ids:
            record["on_40_man"] = False

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "MLB Stats API (statsapi.mlb.com, unofficial public endpoint)",
        "disclaimer": (
            "Service time figures are ESTIMATES computed from public roster "
            "transaction records, not official MLB/MLBPA figures."
        ),
        "player_count": len(db),
        "players": sorted(db.values(), key=lambda p: p.get("name") or ""),
    }
    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"Wrote {len(db)} player records to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
