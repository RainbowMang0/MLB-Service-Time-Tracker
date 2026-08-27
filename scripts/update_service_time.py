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
import super_two  # noqa: E402
from write_player_pages import write_player_pages  # noqa: E402
from service_time import (  # noqa: E402
    roster_start_before_debut,
    TRANSACTION_COVERAGE_START_YEAR,
    is_active_start,
    is_active_stop,
    SeasonWindow,
    Transaction,
    compute_service_time,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "docs" / "data"
OUTPUT_FILE = DATA_DIR / "service_time.json"
# What the browser actually downloads. See write_index().
INDEX_FILE = DATA_DIR / "index.json"
# Per-player season detail, sharded. See write_profiles().
PROFILE_DIR = DATA_DIR / "profiles"
PROFILE_SHARDS = 64
CACHE_DIR = ROOT / "data" / "cache" / "transactions"

MIN_TRANSACTION_YEAR = 2005  # don't bother reaching back further than this
TODAY = dt.date.today()

# Stamped on every record. BUMP THIS whenever a change to the service-time
# rules would give a stored player a different figure -- it is what tells the
# backfill's --recompute-all which records are computed from stale rules.
#
# Nothing else works as a marker. `last_updated` is today for every record
# the moment any job runs, and the presence of a season breakdown (what
# --recompute-stale keys on) says nothing about WHICH rules produced it.
#
#   1  first version stamped, after same-date transactions were fixed to
#      group by date with the stop winning, and carry-in was anchored on the
#      debut date so undebuted prospects stop accruing
#   2  "returned to <club> from <club>" reads as a stop (finding #14). A Rule
#      5 selection opened an interval that nothing closed, crediting players
#      full seasons spent in the minors. 3,337 days removed across 34 of the
#      1,364 cached rostered players; none added.
#   3  findings #15 and #16 together. #15: roster time before a player's
#      first game is credited -- the floor reaches back to an actual roster
#      move (never a signing) within 45 days of the debut. #16: a trade out
#      of a DFA reopens the clock, because a DFA'd player who is traded joins
#      the new club's 40-man. Measured over the 1,365 cached players: 483
#      changed (35%), 5,982 interval-days added, 6 removed.
SERVICE_TIME_RULES_VERSION = 5

# The carry-in rule: presume a player is on a roster from his debut onward
# rather than crediting him nothing until a start transaction happens to fire.
# See build_global_active_intervals in service_time.py for why, and for what
# bounds the over-crediting risk.
#
# ON since 2026-08-22, after clearing the gate in both eras. Measured live:
#
#                    agreement        over-credit     under-credit
#   Yankees 2014     96.5% -> 96.8%   0.2% -> 0.2%    3.3% -> 3.1%
#   Yankees 2018     99.0% -> 99.0%   0.2% -> 0.2%    0.8% -> 0.8%
#
# Over-crediting -- the failure mode behind every revert in this project --
# did not move at all in either season, which is the number that decided it.
# 2018 is untouched end to end: carry-in fires only where the feed is silent.
#
# The switch stays, because it is what makes the A/B possible: set
# PRESUME_ACTIVE_FROM_DEBUT=0 to score the old behaviour. It drives the daily
# job, the backfill and the roster validator together, so what the validator
# reports is what production produces.
PRESUME_ACTIVE_FROM_DEBUT = os.environ.get("PRESUME_ACTIVE_FROM_DEBUT", "1") != "0"


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


def _season_teams(
    year: int,
    from_bio: dict[int, list[int]],
    from_txns: dict[int, list[int]],
    carried: list[int],
) -> list[int]:
    """
    Which major league club(s) a player was with in a given season.

    Three sources in descending order of authority:

      1. His year-by-year stat splits, hydrated onto the bio call. This is
         MLB saying where he played, and it is free -- see
         get_player_bio().
      2. Transactions dated inside the season. Reliable when they exist.
      3. The club he was last known to be with. A club change produces a
         transaction, so a season with no rows at all overwhelmingly means
         he stayed put.

    (1) is silent for a season in which he never appeared -- a player on the
    injured list all year has no stat line -- which is exactly where (3)
    earns its place.
    """
    return from_bio.get(year) or from_txns.get(year) or list(carried)


def _build_seasons(
    by_season: dict[int, dict],
    transactions: list[Transaction],
    season_teams_bio: dict[int, list[int]],
    txn_team_ids: dict[int, list[int]],
    first_txn_year: int | None,
) -> list[dict]:
    """
    The per-season rows behind a player's total, for his profile page.

    `src` records HOW each season is known, because after the carry-in rule
    (see service_time.py) the seasons differ a lot in confidence and the
    profile should say so rather than present them all as equal:

      "read"     -- transactions inside this season drove it directly.
      "carry"    -- no transactions this season; status carried forward from
                    an earlier one. A club change would have produced a row,
                    so this is a safe inference, not a guess.
      "presumed" -- earlier than the player's first transaction of any kind.
                    Pure debut presumption, and the least certain: this is
                    what `missing_seasons` counts. Justin Verlander's
                    2005-2014 are all of this kind.
    """
    years_with_txns = {t.date.year for t in transactions}
    rows = []
    carried: list[int] = []
    for year in sorted(by_season):
        detail = by_season[year]
        if year in years_with_txns:
            src = "read"
        elif first_txn_year is not None and year < first_txn_year:
            src = "presumed"
        else:
            src = "carry"
        teams = _season_teams(year, season_teams_bio, txn_team_ids, carried)
        if teams:
            carried = teams
        rows.append(
            {
                "y": year,
                "d": detail["credited_days"],
                # Kept separate so a profile can show WHY a season credits
                # less than its raw days: the 172-day cap, or 2020's
                # proration, both of which are invisible in the total alone.
                "raw": detail["raw_active_days"],
                # 2020 only. The season ran ~66 days instead of ~186 and the
                # agreement scaled service time by 186/B, so raw days alone
                # make that year look like a two-month career. Carried so the
                # profile can show the scaling instead of an unexplained jump.
                "pro": detail["prorated_days"],
                "t": teams,
                "src": src,
            }
        )
    return rows


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

    # Clubs named by the transactions themselves, per season. Destination
    # first: "Team A sent X to Team B" is evidence about where he ended up.
    txn_team_ids: dict[int, list[int]] = {}
    for t in raw_txns:
        if not _involves_mlb_club(t, mlb_ids):
            continue
        for key in ("to_team_id", "team_id", "from_team_id"):
            tid = t.get(key)
            if tid in mlb_ids:
                year = int(t["date"][:4])
                if tid not in txn_team_ids.setdefault(year, []):
                    txn_team_ids[year].append(tid)
                break

    bio = mlb.get_player_bio(player_id)

    # Filtered against the 30 clubs rather than trusted as-is: year-by-year
    # splits can carry minor league lines, and only /teams knows which ids
    # are major league ones.
    season_teams_bio = {
        year: [tid for tid in ids if tid in mlb_ids]
        for year, ids in mlb.season_teams_from_bio(bio).items()
    }
    season_teams_bio = {y: ids for y, ids in season_teams_bio.items() if ids}

    debut = bio.get("mlbDebutDate")
    debut_date = dt.date.fromisoformat(debut) if debut else None

    # Service time cannot start before a player's major league debut. For the
    # rare player who reached an active roster without ever appearing in a
    # game (so has no debut date), fall back to his earliest major-league-club
    # transaction rather than crediting him from his college days.
    accrual_floor = debut_date
    if accrual_floor is None and transactions:
        accrual_floor = min(t.date for t in transactions)

    # Finding #15: service time is roster time, not playing time. A player
    # selected or recalled days before his first game earned those days, and
    # clipping to the debut threw them away -- 726 of 1,331 cached players
    # have such a row. roster_start_before_debut() reaches back only to an
    # actual roster move, never a signing, and only inside a 45-day window
    # measured from the cache's own bimodal distribution. Both the floor and
    # the carry-in presumption move together: leaving the presumption at the
    # debut would open the interval after the floor and credit nothing extra
    # -- compute_service_time derives the presumption FROM the floor, so
    # moving the floor moves both together.
    roster_start = roster_start_before_debut(transactions, debut_date)
    if roster_start is not None:
        accrual_floor = roster_start

    # A player who is done playing has to have his clock stopped explicitly.
    # Careers usually end with "elected free agency", which is deliberately not
    # a stop keyword (adding it would wreck 272 active players -- see the note
    # in service_time.py), so the final interval is left open and would
    # otherwise run to today: the first backfill batch credited Angel Guzman,
    # who last pitched in 2010, with 20.030 years. Cap accrual at the end of
    # the last season he appeared in. `lastPlayedDate` is the authoritative
    # source; his final transaction is the fallback if the API omits it.
    #
    # `lastPlayedDate` is authoritative WHEN THE API SUPPLIES IT, and the
    # fallback used to be "his final transaction of any kind". That fallback
    # is badly wrong for a player who leaves MLB for the minors:
    #
    #   Aaron Sanchez, no lastPlayedDate, last major league appearance 2022.
    #     2024-08-06  Buffalo Bisons released RHP Aaron Sanchez
    #     2026-01-27  Kansas City Royals signed him to a MINOR LEAGUE contract
    #     2026-06-23  Omaha Storm Chasers released RHP Aaron Sanchez
    #
    #   Both releases are by minor league clubs, so the MLB-club filter drops
    #   them and nothing ever closes his interval. The Kansas City row is
    #   kept (it names a major league club), so the old fallback put his
    #   career end in 2026 and the ceiling never bit. He carried one open
    #   interval from 2022-10-03 to today: FOUR YEARS of phantom service.
    #
    # This is finding #6 -- the clock bridging a gap spent outside MLB --
    # which the notes had marked "likely not real". It is real; it was just
    # being looked for in the wrong place.
    #
    # The fallback now asks when the feed last put him on a MAJOR LEAGUE
    # roster, from two sources that are already fetched:
    #
    #   * the last season his year-by-year splits show an appearance
    #   * the last transaction that is actually a roster event (a start or a
    #     stop), which covers a man who was rostered without appearing
    #
    # A minor league signing is neither, so it no longer extends a career.
    last_played = bio.get("lastPlayedDate")
    career_end: dt.date | None = None
    if not currently_rostered:
        if last_played:
            career_end = dt.date.fromisoformat(last_played)
        else:
            candidates = []
            if season_teams_bio:
                candidates.append(dt.date(max(season_teams_bio), 12, 31))
            roster_events = [
                t.date for t in transactions
                if is_active_start(t.description) or is_active_stop(t.description)
            ]
            if roster_events:
                candidates.append(max(roster_events))
            if candidates:
                career_end = max(candidates)
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
        # Carry-in anchors on the DEBUT, never on the floor's fallback.
        # Those are the same date for anyone who has played, but for a player
        # with no debut the floor falls back to his earliest major-league-club
        # transaction -- which for a prospect is the day the club signed or
        # drafted him. Presuming from that credited 34 players who have never
        # appeared in a major league game, one of them 5.000 years: Leandro
        # Lopez, signed 2021-01-15, still without a debut, read as a
        # five-year veteran. A man who has not debuted has no major league
        # service time to presume.
        presume_active_from_debut=presume_active_from_debut and debut_date is not None,
        # Seasons MLB's own splits say he appeared in. Without this the
        # presumption has nothing to bound it -- see compute_service_time.
        seasons_with_appearances=set(season_teams_bio),
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
        # The season-by-season breakdown behind the total. compute_service_time
        # has always produced this and the pipeline always threw it away; it is
        # what a player profile page is made of, and it costs no extra API call.
        "seasons": _build_seasons(
            result.by_season,
            transactions,
            season_teams_bio,
            txn_team_ids,
            min((t.date.year for t in transactions), default=None),
        ),
        "last_updated": TODAY.isoformat(),
        "rules_version": SERVICE_TIME_RULES_VERSION,
    }


def write_index(db: dict[str, dict], super_two_cutoff: dict | None = None) -> None:
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
        service_time, free_agent_eligible and
        arbitration_eligible each reproduced exactly, 0 mismatches. Super
        Two is the exception and now ships as a flag: it depends on the
        league-wide 2-3 year class, not on one player's day count.
      * the key names themselves -- rows are arrays, which at 5,568 players
        is the single biggest saving
      * repeated team and position strings, replaced by indexes into lookup
        tables (33 teams, 12 positions)

    `missing` is the number of seasons of a player's career the feed cannot
    see: 0 means complete, -1 means incomplete by an unknown amount (a record
    written before missing_seasons existed).
    """
    players = sorted(db.values(), key=lambda p: p.get("name") or "")

    # A club is published for rostered players only. What is stored for
    # everyone else is the last club we saw them with, which is stale by
    # construction -- printing it next to a retired player's name asserts a
    # roster spot he does not hold. The database keeps it (the profile's
    # season rows need per-year clubs, which ARE dated facts); the table's
    # payload does not carry it.
    def _team(p: dict) -> str:
        return (p.get("team") or "") if p.get("on_40_man") else ""

    teams = sorted({_team(p) for p in players})
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
            team_ix[_team(p)],
            pos_ix[p.get("position") or ""],
            p.get("service_days_total", 0),
            1 if p.get("on_40_man") else 0,
            missing,
            # Super Two is the one status the browser cannot derive from the
            # day count: it depends on where a player ranks in the league-wide
            # 2-3 year class and on how many days he accrued last season. So
            # it ships as a flag rather than being recomputed client-side.
            1 if p.get("super_two_candidate") else 0,
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
        "super_two_cutoff": super_two_cutoff,
        "fields": [
            "id", "name", "team", "position", "days", "on_40_man",
            "missing_seasons", "super_two",
        ],
        "players": rows,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"Wrote compact index for {len(rows)} players to {INDEX_FILE}")


def write_profiles(db: dict[str, dict]) -> None:
    """
    Per-player season detail, for the profile page.

    Sharded rather than shipped whole. The full season breakdown for 5,568
    players is about 0.9 MB -- four times the compact table index, which was
    deliberately cut to 0.21 MB so the page loads fast. Bundling it back in
    would undo that for a view most visitors never open.

    So it is split into PROFILE_SHARDS buckets by `player_id % 64`, giving
    ~14 KB per file: opening a profile fetches one shard, and the table load
    is untouched. Modulo rather than a name or team prefix because it spreads
    evenly and never changes -- a player who is traded stays in his shard.

    Team ids are resolved to names here rather than in the browser, since a
    shard is small and self-contained; the id is kept alongside for linking.
    """
    players = list(db.values())
    team_names: dict[int, str] = {}
    for p in players:
        tid, tname = p.get("team_id"), p.get("team")
        if tid and tname:
            team_names.setdefault(int(tid), tname)

    shards: dict[int, dict[str, dict]] = {}
    mismatches: list[tuple] = []
    with_seasons = 0
    for p in players:
        pid = p.get("id")
        if pid is None:
            continue
        seasons = p.get("seasons")
        if not seasons:
            # A record written before profiles existed. Skipped rather than
            # faked: the profile page says "not yet computed" and the next
            # full run fills it in.
            continue
        with_seasons += 1
        # The seasons ARE the total -- if they disagree, the profile page
        # would show a running total that lands somewhere other than the
        # figure in the table, and the first person to notice would rightly
        # stop trusting both. Cheap to check, so check every player.
        season_sum = sum(int(row.get("d") or 0) for row in seasons)
        if season_sum != int(p.get("service_days_total") or 0):
            mismatches.append((p.get("name"), pid, season_sum, p.get("service_days_total")))
        shards.setdefault(int(pid) % PROFILE_SHARDS, {})[str(pid)] = {
            "id": pid,
            "name": p.get("name"),
            "team": p.get("team"),
            "position": p.get("position"),
            "mlb_debut": p.get("mlb_debut"),
            "last_played": p.get("last_played"),
            "service_time": p.get("service_time"),
            "days": p.get("service_days_total", 0),
            "on_40_man": bool(p.get("on_40_man")),
            "missing_seasons": p.get("missing_seasons", 0),
            "first_transaction": p.get("first_transaction"),
            "seasons": seasons,
        }

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    existing = {f.name for f in PROFILE_DIR.glob("*.json")}
    written = set()
    for bucket in range(PROFILE_SHARDS):
        name = f"{bucket:02d}.json"
        path = PROFILE_DIR / name
        payload = {
            "teams": {str(k): v for k, v in sorted(team_names.items())},
            "players": shards.get(bucket, {}),
        }
        path.write_text(json.dumps(payload, separators=(",", ":")))
        written.add(name)

    for stale in existing - written:
        (PROFILE_DIR / stale).unlink()

    if mismatches:
        print(
            f"!! WARNING: {len(mismatches)} player(s) whose season rows do not sum "
            f"to their published total. The profile page would contradict the table."
        )
        for name, pid, got, want in mismatches[:10]:
            print(f"   {name} ({pid}): seasons sum to {got}, total says {want}")

    total = sum(f.stat().st_size for f in PROFILE_DIR.glob("*.json"))
    print(
        f"Wrote {PROFILE_SHARDS} profile shards for {with_seasons} players "
        f"({total / 1e6:.2f} MB total, ~{total / PROFILE_SHARDS / 1e3:.0f} KB each) "
        f"to {PROFILE_DIR}"
    )


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
    parser.add_argument(
        "--recompute-derived",
        action="store_true",
        help=(
            "Skip the API entirely: reload the stored database, redo the "
            "derived post-passes (Super Two, index, profiles) and rewrite the "
            "published files. For a change to how something is DERIVED from "
            "records that are already correct -- no point spending half an "
            "hour re-fetching transactions that have not changed."
        ),
    )
    parser.add_argument(
        "--ignore-sanity",
        action="store_true",
        help=(
            "Publish even if the run looks anomalous (see check_run_is_sane). "
            "For the day the guard is wrong, not for getting past it."
        ),
    )
    args = parser.parse_args()

    print(f"[{dt.datetime.now().isoformat(timespec='seconds')}] Starting service-time update...")

    db = load_existing_db()
    print(f"Loaded existing database with {len(db)} previously-seen players.")

    if args.recompute_derived:
        print("Recomputing derived data only -- no API calls, no record changes.")
        _write_outputs(db)
        return

    roster = mlb.get_all_40man_players()
    print(f"Fetched {len(roster)} players currently on a 40-man roster.")
    if args.limit:
        roster = roster[: args.limit]

    previous_rostered = sum(1 for p in db.values() if p.get("on_40_man"))

    current_ids = set()
    failures = 0
    for i, entry in enumerate(roster, 1):
        pid = str(entry["id"])
        current_ids.add(pid)
        try:
            record = build_player_record(entry, args.full_refresh)
            db[pid] = record
            print(f"  [{i}/{len(roster)}] {record['name']}: {record['service_time']}")
        except Exception as exc:  # keep going even if one player fails
            failures += 1
            print(f"  [{i}/{len(roster)}] FAILED for player {pid}: {exc}", file=sys.stderr)

    # Checked BEFORE the de-flagging pass below and before anything is
    # written, because both are destructive: the pass rewrites every record
    # in the database and the write replaces a good published file.
    problems = [] if args.limit else check_run_is_sane(
        len(roster), previous_rostered, failures
    )
    if problems and not args.ignore_sanity:
        print("\n!! REFUSING TO PUBLISH THIS RUN:", file=sys.stderr)
        for problem in problems:
            print(f"     - {problem}", file=sys.stderr)
        print(
            "\n   Nothing was written, so the published data is unchanged and\n"
            "   still correct. Re-run once the API is healthy, or pass\n"
            "   --ignore-sanity if this really is what the day looks like.",
            file=sys.stderr,
        )
        sys.exit(1)
    if problems:
        print("\n!! Sanity checks failed but --ignore-sanity was passed:", file=sys.stderr)
        for problem in problems:
            print(f"     - {problem}", file=sys.stderr)

    # Players no longer on a 40-man roster stay in the DB (the "previous
    # players" log) but get flagged as such.
    for pid, record in db.items():
        if pid not in current_ids:
            record["on_40_man"] = False

    _write_outputs(db)


# Sanity bounds for a daily run. Every one of them describes a failure that
# publishes CONFIDENTLY WRONG data rather than crashing, which is the only
# kind worth a gate: a crash leaves yesterday's good file in place, a silent
# success overwrites it.
MIN_EXPECTED_ROSTER = 900      # 30 clubs x 40 = 1,200 nominal, with slack
MAX_ROSTER_SHRINK = 0.25       # vs. what the stored database already knows
MAX_FAILURE_RATE = 0.10        # per-player build failures are swallowed


def check_run_is_sane(
    roster_size: int, previous_rostered: int, failures: int
) -> list[str]:
    """
    Reasons this run must NOT be published, or an empty list.

    THE FAILURE THIS EXISTS FOR: main() flags every player not seen in
    today's roster fetch as no longer on a 40-man. If that fetch comes back
    empty -- an API hiccup, a changed endpoint, a rate limit answered with an
    empty list rather than an error -- the loop simply does not run, nobody
    lands in current_ids, and all 5,569 records get on_40_man=False. The job
    then reports success and publishes a site claiming nobody in baseball is
    on a roster, over the top of a database that was correct.

    Nothing in the pipeline would have caught that. Every bug in this project
    so far was found by a human noticing a number looked silly, which works
    at 20 players and fails at 5,569 -- and fails completely at 3am.

    Per-player failures are swallowed by design so one bad record cannot lose
    a whole run, but that same design means a systemic outage looks like a
    normal run with a quieter log.
    """
    problems = []
    if roster_size < MIN_EXPECTED_ROSTER:
        problems.append(
            f"only {roster_size} players came back from the 40-man fetch, "
            f"below the {MIN_EXPECTED_ROSTER} floor (30 clubs carry ~1,200)"
        )
    if previous_rostered and roster_size < previous_rostered * (1 - MAX_ROSTER_SHRINK):
        problems.append(
            f"the roster shrank from {previous_rostered} to {roster_size}, "
            f"more than the {MAX_ROSTER_SHRINK:.0%} a real day of transactions "
            "could account for"
        )
    if roster_size and failures / roster_size > MAX_FAILURE_RATE:
        problems.append(
            f"{failures} of {roster_size} players failed to build "
            f"({failures / roster_size:.0%}), above the "
            f"{MAX_FAILURE_RATE:.0%} tolerance"
        )
    return problems


def _write_outputs(db: dict[str, dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Super Two needs the whole population, so it can only be settled once
    # every record exists -- see scripts/super_two.py. This replaces the flat
    # 86-day flag that compute_service_time() sets per player without any
    # league context.
    cutoff = super_two.apply_super_two(
        db, super_two.latest_complete_season(db, TODAY.year)
    )
    if cutoff:
        print(
            f"Super Two cutoff after {cutoff['season']}: {cutoff['cutoff']} "
            f"({cutoff['class_size']} in the 2-3 year class, top "
            f"{cutoff['qualifying_count']} qualify); "
            f"{cutoff['projected_candidates']} players currently project above it"
        )
    else:
        print("Super Two: class too small to measure a cutoff; heuristic flags kept.")

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
        "super_two_cutoff": cutoff,
        "incomplete_history_count": sum(
            1 for p in db.values() if not p.get("history_complete", True)
        ),
        "players": sorted(db.values(), key=lambda p: p.get("name") or ""),
    }
    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"Wrote {len(db)} player records to {OUTPUT_FILE}")
    write_index(db, cutoff)
    write_profiles(db)
    # Crawlable static pages. Hash routing is invisible to search engines, so
    # without these no player could be found by searching for him.
    write_player_pages(db, output["generated_at"])
    report_debuted_but_empty(db)


def report_debuted_but_empty(db: dict[str, dict]) -> list[dict]:
    """
    Players who reached the major leagues inside the window this pipeline
    computes, and were still credited nothing.

    A player who appeared in a major league game was on a major league roster
    that day, so at least one day is owed him. Zero means an interval failed
    to open or closed too early.

    The debut year matters: a man who debuted in 1998 and was finished by
    2003 correctly reads zero, because nothing before MIN_TRANSACTION_YEAR is
    computed at all. Only a debut inside the window makes zero a defect.

    Found by Elih Villanueva, whose whole career was 2011-06-15 -- he debuted
    and last played on the same day. His season row reads src "read", so the
    feed did have transactions for him; the one-day interval just came out
    empty. One record today, which is why this reports rather than fails: it
    is a signal that a class of interval bug exists, not a reason to throw
    away a thirty-minute run.
    """
    suspects = [
        p for p in db.values()
        if p.get("mlb_debut")
        and int(p["mlb_debut"][:4]) >= MIN_TRANSACTION_YEAR
        and int(p.get("service_days_total") or 0) == 0
    ]
    if not suspects:
        return []
    print(
        f"\n!! {len(suspects)} player(s) debuted inside the computed window "
        f"(>= {MIN_TRANSACTION_YEAR}) and credited 0 days. A player who "
        "appeared in a game was rostered that day, so each of these is an "
        "interval that never opened or closed too early:"
    )
    for p in sorted(suspects, key=lambda x: x.get("mlb_debut") or "")[:20]:
        print(
            f"     {p.get('name'):<26} debut {p.get('mlb_debut')}"
            f"  last played {p.get('last_played') or '(still active)'}"
        )
    if len(suspects) > 20:
        print(f"     ... and {len(suspects) - 20} more")
    return suspects


if __name__ == "__main__":
    main()
