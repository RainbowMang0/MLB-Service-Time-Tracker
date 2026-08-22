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
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import fetch_mlb_data as mlb  # noqa: E402
from service_time import (  # noqa: E402
    TRANSACTION_COVERAGE_START_YEAR,
    SeasonWindow,
    Transaction,
    compute_service_time,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "docs" / "data"
OUTPUT_FILE = DATA_DIR / "service_time.json"
# What the browser actually downloads. See write_index().
INDEX_FILE = DATA_DIR / "index.json"
CACHE_DIR = ROOT / "data" / "cache" / "transactions"

MIN_TRANSACTION_YEAR = 2005  # don't bother reaching back further than this
TODAY = dt.date.today()

# The carry-in rule: presume a player is on a roster from his debut onward
# rather than crediting him nothing until a start transaction happens to fire.
# See build_global_active_intervals in service_time.py for why, and for what
# bounds the over-crediting risk.
#
# Kept behind a single switch on purpose. This project's rule is one change,
# one before/after measurement -- flipping it here changes the daily job, the
# backfill and the roster validator together, so the number the validator
# reports is the number production would produce. Do not hardcode it True
# until Yankees 2014 and 2018 both still pass the gate (>=95% agreement,
# <=2% over-crediting) with it on.
PRESUME_ACTIVE_FROM_DEBUT = os.environ.get("PRESUME_ACTIVE_FROM_DEBUT", "") == "1"


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


def _fetch_transactions_incremental(
    player_id: int, full_refresh: bool, use_cache: bool = True
) -> list[dict]:
    """
    Fetch a player's transactions, reusing the on-disk cache when allowed.

    `use_cache=False` is for historical backfill: a retired player's service
    time never changes, so caching ~4,000 of those histories would add tens of
    megabytes to the repo in exchange for nothing.
    """
    cached = [] if (full_refresh or not use_cache) else _load_cached_transactions(player_id)

    if cached:
        last_date = max(dt.date.fromisoformat(t["date"]) for t in cached)
        start = last_date + dt.timedelta(days=1)
    else:
        start = dt.date(MIN_TRANSACTION_YEAR, 1, 1)

    if start > TODAY:
        return cached

    new_raw = mlb.get_player_transactions(player_id, start, TODAY)
    # Team IDs are retained deliberately. The raw feed mixes in college,
    # showcase, minor league and All-Star events phrased with the same verbs as
    # major league moves, and the only reliable way to tell them apart is
    # whether a real MLB club is involved. An earlier version of this function
    # stored only the team NAME, which silently reduced that filter to a no-op.
    new_txns = [
        {
            "date": t.get("date"),
            "description": t.get("description", ""),
            "team": (t.get("toTeam") or {}).get("name") or (t.get("fromTeam") or {}).get("name"),
            "team_id": (t.get("team") or {}).get("id"),
            "from_team_id": (t.get("fromTeam") or {}).get("id"),
            "to_team_id": (t.get("toTeam") or {}).get("id"),
        }
        for t in new_raw
        if t.get("date")
    ]

    merged = {t["date"] + "|" + t["description"]: t for t in (cached + new_txns)}
    result = sorted(merged.values(), key=lambda t: t["date"])
    if use_cache:
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
    ids = {txn.get("team_id"), txn.get("from_team_id"), txn.get("to_team_id")}
    ids = {i for i in ids if isinstance(i, int)}
    if not ids:
        # Legacy cache entry written before team IDs were stored, or a row the
        # feed gave us no team for. Can't judge it here, so keep it and let the
        # debut-date floor do the filtering instead.
        return True
    return bool(ids & mlb_ids)


def build_player_record(
    roster_entry: dict,
    full_refresh: bool,
    use_cache: bool = True,
    horizon_end: dt.date | None = None,
    currently_rostered: bool = True,
    presume_active_from_debut: bool | None = None,
) -> dict:
    """
    `horizon_end` defaults to today (the normal daily-update behavior). Pass
    a past date to compute what this player's service time WOULD HAVE READ
    as of that date -- e.g. a prior Opening Day, for validating against a
    fixed reference figure like Baseball Reference's `s.YYYY` snapshot. See
    validate_service_time.py.

    `currently_rostered` must be False for a player who is no longer on any
    40-man roster (i.e. every player the historical backfill processes).
    Without it their service clock never stops -- see the accrual-ceiling
    block below.
    """
    horizon_end = horizon_end or TODAY
    if presume_active_from_debut is None:
        presume_active_from_debut = PRESUME_ACTIVE_FROM_DEBUT
    player_id = roster_entry["id"]
    raw_txns = _fetch_transactions_incremental(player_id, full_refresh, use_cache=use_cache)

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

    # A player who is done playing has to have his clock stopped explicitly.
    # Careers usually end with "elected free agency", which is deliberately not
    # a stop keyword (adding it would wreck 272 active players -- see the note
    # in service_time.py), so the final interval is left open and would
    # otherwise run to today: the first backfill batch credited Angel Guzman,
    # who last pitched in 2010, with 20.030 years. Cap accrual at the end of
    # the last season he appeared in. `lastPlayedDate` is the authoritative
    # source; his final transaction is the fallback if the API omits it.
    last_played = bio.get("lastPlayedDate")
    career_end: dt.date | None = None
    if not currently_rostered:
        if last_played:
            career_end = dt.date.fromisoformat(last_played)
        elif transactions:
            career_end = max(t.date for t in transactions)

    debut_year = debut_date.year if debut_date else MIN_TRANSACTION_YEAR
    first_year = max(debut_year, MIN_TRANSACTION_YEAR)
    last_year = horizon_end.year
    if career_end is not None:
        last_year = min(last_year, max(career_end.year, first_year))
    years = range(first_year, last_year + 1)
    seasons = _season_windows_for(years)

    # End of his final season, not his final game: service time is roster
    # time, so a player keeps accruing after his last appearance if he stays
    # on the active roster or the IL through the end of the year.
    accrual_ceiling = seasons[-1].end if (career_end is not None and seasons) else None

    missing = _missing_seasons(debut_date, transactions)

    result = compute_service_time(
        transactions,
        seasons,
        presume_active_from_debut=presume_active_from_debut,
        accrual_floor=accrual_floor,
        accrual_ceiling=accrual_ceiling,
        # Stop the clock at horizon_end (today, for the daily job) rather than
        # at the end of the current season -- otherwise every player currently
        # on a roster is credited with the remaining weeks of a season that
        # hasn't been played yet.
        horizon_end=horizon_end,
    )

    return {
        "id": player_id,
        "name": roster_entry.get("fullName") or bio.get("fullName"),
        "team": roster_entry.get("team"),
        "team_id": roster_entry.get("teamId"),
        "position": roster_entry.get("position"),
        "mlb_debut": debut,
        # Persisted so a suspect number can be checked directly instead of
        # reverse-engineered. Without it there is no way to tell from the
        # published data where a retired player's clock was stopped, which is
        # exactly the diagnosis that stalled on Lew Ford (see
        # report_impossible_totals in backfill_history.py).
        "last_played": last_played,
        "accrual_ceiling": accrual_ceiling.isoformat() if accrual_ceiling else None,
        "service_time": result.formatted,
        "service_days_total": result.total_days,
        "free_agent_eligible": result.is_free_agent_eligible,
        "arbitration_eligible": result.is_arbitration_eligible,
        "super_two_candidate": result.is_super_two_candidate,
        "on_40_man": True,
        # Kept as a boolean for the frontend, but now derived per player from
        # what the feed actually shows rather than from a cutoff year.
        "history_complete": missing == 0,
        "missing_seasons": missing,
        "first_transaction": (
            min(t.date for t in transactions).isoformat() if transactions else None
        ),
        "last_updated": TODAY.isoformat(),
    }


def write_index(db: dict[str, dict]) -> None:
    """
    Emit the compact file the frontend downloads.

    `service_time.json` is the database: every field, one object per player,
    and the pipeline's own source of truth on the next run. At 5,568 players
    it is 2.8 MB, and the page was fetching all of it on every visit for a
    table that reads eleven fields.

    This writes a derived view instead, 0.17 MB (94% smaller), by removing
    everything the browser does not need:

      * fields the table never reads (id, team_id, mlb_debut, last_played,
        accrual_ceiling, first_transaction)
      * fields that are pure functions of service_days_total, recomputed in
        the browser instead of shipped. Verified against all 5,568 records:
        service_time, free_agent_eligible, super_two_candidate and
        arbitration_eligible each reproduce exactly, 0 mismatches.
      * the key names themselves -- rows are arrays, which at 5,568 players
        is the single biggest saving
      * repeated team and position strings, replaced by indexes into lookup
        tables (33 teams, 12 positions)

    `missing` is the number of seasons of a player's career the feed cannot
    see: 0 means complete, -1 means incomplete by an unknown amount (a record
    written before missing_seasons existed).
    """
    players = sorted(db.values(), key=lambda p: p.get("name") or "")
    teams = sorted({p.get("team") or "" for p in players})
    positions = sorted({p.get("position") or "" for p in players})
    team_ix = {t: i for i, t in enumerate(teams)}
    pos_ix = {p: i for i, p in enumerate(positions)}

    rows = []
    for p in players:
        missing = p.get("missing_seasons")
        if missing is None:
            # Legacy record: we know completeness but not the size of the gap.
            missing = 0 if p.get("history_complete", True) else -1
        rows.append([
            # The id is the only stable identity: the dataset contains two
            # different Luis Perdomos, both Padres pitchers, and two different
            # Daniel Robertsons. Name+team+position is NOT unique.
            p.get("id"),
            p.get("name") or "",
            team_ix[p.get("team") or ""],
            pos_ix[p.get("position") or ""],
            p.get("service_days_total", 0),
            1 if p.get("on_40_man") else 0,
            missing,
        ])

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "MLB Stats API (statsapi.mlb.com, unofficial public endpoint)",
        "disclaimer": (
            "Service time figures are ESTIMATES computed from public roster "
            "transaction records, not official MLB/MLBPA figures. Where the "
            "transaction feed cannot see the start of a player's career, his "
            "figure is a floor rather than an estimate and the table says so."
        ),
        "player_count": len(rows),
        "teams": teams,
        "positions": positions,
        # Self-documenting, so the row layout is readable without the code.
        "fields": ["id", "name", "team", "position", "days", "on_40_man", "missing_seasons"],
        "players": rows,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"Wrote compact index for {len(rows)} players to {INDEX_FILE}")


def _missing_seasons(
    debut_date: dt.date | None, transactions: list[Transaction]
) -> int:
    """
    How many of this player's seasons are invisible to the transaction feed.

    Measured per player rather than assumed from a cutoff year. The old
    version returned `debut.year >= 2009`, on the premise that the feed
    carries nothing before 2009. `scripts/probe_coverage.py` disproved that
    (finding #1): pre-2009 rows exist, they are just sparse -- "Minnesota
    Twins activated LF Lew Ford from the 15-day disabled list", 2006-08-11.
    So a fixed year flags plenty of perfectly good figures as "partial" and
    tells you nothing about how much is actually missing.

    What matters is whether we can see the *front* of a player's career. If
    his earliest major-league transaction lands in his debut season, we have
    him from the beginning and his total is a real estimate. If it lands
    years later, everything before that is unrecoverable and his total is a
    floor -- and now we can say by how much.

    Returns 0 when nothing is missing (including for a player who never
    reached the majors, where there is nothing to miss).
    """
    if debut_date is None:
        return 0
    if not transactions:
        # He debuted, but we can see none of it.
        return max(0, TODAY.year - debut_date.year + 1)
    first_seen = min(t.date for t in transactions)
    return max(0, first_seen.year - debut_date.year)


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
            "transaction records, not official MLB/MLBPA figures. Transaction "
            f"coverage begins in {TRANSACTION_COVERAGE_START_YEAR}; players who "
            "debuted earlier are marked as having incomplete history and their "
            "figures are a floor, not an estimate."
        ),
        "coverage_start_year": TRANSACTION_COVERAGE_START_YEAR,
        "player_count": len(db),
        "incomplete_history_count": sum(
            1 for p in db.values() if not p.get("history_complete", True)
        ),
        "players": sorted(db.values(), key=lambda p: p.get("name") or ""),
    }
    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"Wrote {len(db)} player records to {OUTPUT_FILE}")
    write_index(db)


if __name__ == "__main__":
    main()
