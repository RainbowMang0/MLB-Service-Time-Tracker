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
import re
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import accrual_model  # noqa: E402
import cba  # noqa: E402
import fetch_mlb_data as mlb  # noqa: E402
import refresh_complete  # noqa: E402
import salary_estimates  # noqa: E402
import service_time_exceptions  # noqa: E402
import super_two  # noqa: E402
import write_player_pages  # noqa: E402
from write_player_pages import write_player_pages as _write_pages  # noqa: E402
from service_time import (  # noqa: E402
    roster_start_before_debut,
    is_active_stop,
    is_free_agency_election,
    is_trade,
    row_opens_roster,
    SeasonWindow,
    Transaction,
    TRANSACTION_COVERAGE_START_YEAR,
    compute_service_time,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "docs" / "data"
# Hand-entered published Super Two cutoffs -- the only independent check
# super_two.py has. See validate_super_two.py.
REFERENCE_SUPER_TWO = ROOT / "data" / "reference_super_two.json"
OUTPUT_FILE = DATA_DIR / "service_time.json"
# The CBA agreement in force. Every threshold this pipeline publishes comes
# from here rather than from a literal -- see scripts/cba.py.
_RULES = cba.default()
# {mlbam_id -> {season, ...}} the 2022 CBA's Prospect Promotion Incentive
# credited a full service year (a top-two Rookie of the Year finish). Loaded
# once; the list is hand-maintained in config/cba/roy_full_year_awards.json and
# validated by cba.py. Empty until verified entries are added.
_ROY_FULL_YEAR_SEASONS = cba.roy_full_year_seasons_by_player()
# {mlbam_id -> [exception, ...]} the hand-kept restricted-list service-time
# exceptions (paid DV administrative leave, 2020 opt-outs, suspensions the feed
# shows only as a restricted-list placement). Validated on load. See
# data/service_time_exceptions.json and scripts/service_time_exceptions.py.
_SERVICE_TIME_EXCEPTIONS = service_time_exceptions.load_exceptions()
# The DFA grace window (rules v7): a player designated for assignment DURING the
# season keeps earning service until the move that resolves it, capped at this
# many days. From config/cba/, never a literal -- a successor agreement that
# changes the window is a config edit.
_DFA_LIMBO_DAYS = _RULES.require("designated_for_assignment.max_service_days")
# config/ is the source of truth for rulesets, but it sits outside docs/ and
# GitHub Pages serves docs/ as the site root, so the browser cannot reach it.
# publish_config() copies it in. Source stays canonical; this is a build
# artifact like everything else under docs/data.
CONFIG_SRC = ROOT / "config"
CONFIG_OUT = DATA_DIR / "config"
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
#   4  finding #19: a waiver claim whose very next major league move is a
#      recall never put the player on the active roster -- he cannot be
#      recalled from Norfolk unless he was at Norfolk. 16 of 1,368 cached
#      players changed, 301 credited days removed, 0 added. Cleveland 2011
#      went to a perfect 1,083 of 1,083 judgements.
#   5  the same reasoning extended from waiver claims to TRADES, which move a
#      player onto a 40-man without saying which roster he reported to. 31 of
#      1,370 changed, 687 days removed, 0 added. Detection is grouped by date
#      so it cannot depend on the order the API lists two same-day rows in --
#      the sort-order trap of finding #10, which was worth 160 days there.
#   6  four accuracy fixes, the first time the season WINDOW itself was wrong
#      rather than the interval walk (finding #22). (a) Season windows come
#      from the /schedule feed, so each season starts on the player's own
#      club's first game -- the Seoul/Tokyo openers a week early, the rest on
#      their real domestic Opening Day -- instead of the single overseas opener
#      /seasons reports applied to all 30 clubs (the ~8-days-high error the
#      Baseball Reference audit found). (b) A season ends on the last game
#      actually played, not /seasons' end, which in 2023 was a makeup game
#      scheduled 10-02 and later cancelled (real last day 10-01). (c) The
#      20-day optional-assignment rule: a player optioned for fewer than 20
#      days in a season is credited those days as service. (d) The Prospect
#      Promotion Incentive: a top-two Rookie of the Year finish is credited a
#      full year (config/cba/roy_full_year_awards.json). Measured on the 1,394
#      reproducible cached players: ~586 change, the opener/end fix only
#      removing days and the 20-day rule only adding them; reference veterans
#      unmoved.
#
#   7  service-time DATA fixes, after the v6 recompute exposed two outliers as
#      data bugs rather than rules bugs. (a) The restricted list STOPS the clock
#      by default -- a restricted-list player is not paid and earns no service
#      (MLB Rule 2(c)(6)) -- instead of being treated as a non-stopping
#      placement. The spells that DO earn service (paid DV administrative leave,
#      2020 opt-outs) and the suspensions that look like a leave in the feed are
#      restored or removed per player from the hand-kept, sourced
#      data/service_time_exceptions.json. (b) A missing lastPlayedDate no longer
#      falls back to the last roster-transaction date, which extended careers
#      past the last season a player actually had MLB service. He keeps
#      accruing past his last appearance while still on a roster -- currently
#      rostered, or bio-active with his latest major-league roster move a
#      placement/activation rather than a release/outright/option/DFA/free-
#      agency election (the _still_on_ml_roster guard) -- which is what keeps a
#      60-day-IL season from being erased. Once he has actually left,
#      career_end is the last season with an MLB appearance (plus any
#      counts=true exception); a player who never appeared at all falls back to
#      his last major-league roster START (an activation, IL placement, recall,
#      contract selection or trade -- never a bare STOP, which cannot extend a
#      career on its own). (c) A DFA during
#      the season keeps the clock running until the move that resolves it,
#      capped at designated_for_assignment.max_service_days (7); an offseason
#      DFA still adds nothing. (d) A transaction cache written before team IDs
#      were stored (ID-less rows) is discarded and refetched, so minor-league
#      rows can no longer read as MLB moves -- the Velázquez cache bug.
#      Trevor Bauer 8.157 -> 8.007 (1,383 days), Jose Alvarado 9.093 -> 9.009
#      (1,557 days; ~5 days under a 9.014 estimate -- cause not tracked down,
#      noted on the PR), Nelson Velázquez -> 2.047 (391 days: the cache fix
#      holds him at 386/2.042 and the DFA grace window adds his five 2026 limbo
#      days). The restricted-list and DFA rules each move a few dozen other
#      players by a handful of days. (Service-time days run 0-171, so .178 is
#      not a valid figure; the earlier "7.178" target was an arithmetic slip.)
#
# Only-removes is the expected shape for 4 and 5: a rule that can only CLOSE an
# interval must never add a day. 6 and 7 both move figures in BOTH directions --
# 7 because the restricted-list stop and the lastPlayedDate fix remove days
# while the DFA grace window and the counts=true exceptions add them.
#
# 8  phantom seasons and zeroed real seasons, from the v7 recompute.
#    (a) A bare "signed free agent" row filed the same day as a sibling that
#        says "to a minor league contract" no longer opens an MLB stint
#        (Snelten, Ravelo). (b) An assignment, release, outright, or
#        retirement that names only a minor-league club is kept and closes
#        an open stint -- the MLB-club filter had been dropping the very
#        move that ended the career (Kaminsky, Jackson). (c) A free-agency
#        election on the same day as an activation wins, so the activation
#        does not reopen the clock (Almonte). A lone election was not a
#        global stop in v8; v9 makes it one. (d) "reassigned ... to the minor leagues" is a stop.
#    (e) Where the stat feed shows a substantial number of MLB games and the
#        transaction walk credited almost none, the span from the first game
#        to the last is credited, split by any stop the feed did record
#        (Strange-Gordon 2017, Butler 2025-26, Allen 2025, Sears 2023-24).
#        That fill only adds days, and only across a gap with no stop.
#
# 9  electing or declaring free agency always stops the clock, and a DFA
#    with a trade on the same day does not stop it. Re-measured on the 1,441
#    cached histories (2026-11-08): 15 totals fall, 2 rise, 1,424 unchanged.
#    The falls are unsigned or out-of-league seasons (Fedde 2023, Lambert
#    2025, Cortes 2026). A same-day trade keeps the days (Heim 2026). A
#    return worded only as "assigned to <club>" still starts late (Gallen,
#    Kinley). Rules v10 is that case.
#
# 10 an assignment onto the major-league active roster restarts a stopped
#    clock, the same way a signing does. "Assigned to <MLB club>" is that
#    return only when a later row shows he was on the active roster (an
#    injured-list placement or an option) before any row that shows he was
#    still in the minors (a minor-league assignment, a recall, or a contract
#    selection). A same-day minor-league assignment still wins. Optional
#    assignments, outrights, and rehab assignments do not restart anything.
#    Re-measured on the 1,409 cached histories with a debut: 3 totals rise,
#    none fall. Zac Gallen 6.061 -> 6.155, Tyler Kinley 7.109 -> 8.001,
#    Rowdy Tellez 6.123 -> 7.018. The other 1,406 stay put -- treating every
#    assignment as a start had moved 35, including full phantom seasons.
SERVICE_TIME_RULES_VERSION = 10

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


def _row_is_legacy_format(txn: dict) -> bool:
    """A cached transaction written before team IDs were stored.

    The old shape carried only date/description/team; the three id keys
    (team_id, from_team_id, to_team_id) were simply absent. Key PRESENCE, not
    value, is the test -- a freshly-fetched row always has the keys, even when
    the feed gave no club for that move and all three are null -- so a healthy
    cache is never mistaken for a legacy one.
    """
    return not any(
        k in txn for k in ("team_id", "from_team_id", "to_team_id")
    )


def _cache_is_legacy(cached: list[dict]) -> bool:
    """True if ANY row is in the pre-team-id format.

    _involves_mlb_club keeps an ID-less row (it cannot judge it), so a single
    legacy row lets a minor-league or foreign-league move read as an MLB one --
    the Nelson Velázquez bug, where his whole cache predated team IDs and his
    Mexican-League and Triple-A moves opened phantom MLB stretches. One such row
    makes the file untrustworthy, so the whole thing is refetched.
    """
    return any(_row_is_legacy_format(t) for t in cached)


def _save_cached_transactions(player_id: int, txns: list[dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(player_id).write_text(json.dumps(txns, indent=2, default=str))


# Feed fields the service-time engine does not read. Stored on every newly
# fetched row so a later display can use a move's kind and its effective
# date without parsing the description. They are NOT a reason to refetch:
# a cache row written before this shipped simply lacks the keys, and a
# missing key reads as None. Do not treat that as a legacy cache — the
# legacy path exists for missing team ids, which the engine cannot judge.
# These fields must not be fed into compute_service_time.
_TXN_CACHE_FIELDS = (
    "typeCode",
    "typeDesc",
    "effectiveDate",
    "resolutionDate",
    "id",
)


def _jsonable_txn_value(value):
    """A cache value is a JSON scalar, or None when the feed omitted it.

    bool is a subclass of int, and a boolean is not a date or an id, so it
    is rejected rather than stored as 0 or 1.
    """
    if isinstance(value, bool):
        return None
    if value is None or isinstance(value, (str, int, float)):
        return value
    return None


def _cache_transaction(raw: dict) -> dict | None:
    """One raw feed row in the on-disk cache shape.

    The five ``_TXN_CACHE_FIELDS`` are always written, including when the
    feed's value is null, so a row fetched from now on is distinguishable
    from an older row that never had the key. A row with no date is not a
    transaction this cache can order, and is dropped — same as before.
    """
    date = raw.get("date")
    if not date:
        return None
    row = {
        "date": date,
        "description": raw.get("description", ""),
        "team": (raw.get("toTeam") or {}).get("name") or (raw.get("fromTeam") or {}).get("name"),
        "team_id": (raw.get("team") or {}).get("id"),
        "from_team_id": (raw.get("fromTeam") or {}).get("id"),
        "to_team_id": (raw.get("toTeam") or {}).get("id"),
    }
    for key in _TXN_CACHE_FIELDS:
        row[key] = _jsonable_txn_value(raw.get(key))
    return row


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

    if cached and _cache_is_legacy(cached):
        if mlb_fetch_enabled():
            # Throw the stale file away; the fetch below rebuilds the full
            # history from scratch, this time with team IDs on every row.
            cached = []
        else:
            # Offline (the fetch-off path never recomputes in production, but
            # keep it safe for any other caller): we cannot refetch, so drop the
            # untrustworthy ID-less rows rather than let them read as MLB moves.
            return [t for t in cached if not _row_is_legacy_format(t)]

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
    #
    # typeCode, typeDesc, effectiveDate, resolutionDate and the transaction id
    # are stored too. An incremental run only asks for dates AFTER the newest
    # cached row, so rows already on disk keep whatever keys they were written
    # with. Filling the old rows in is a full refresh of that player, not this
    # fetch, and it is not required for the rows to stay readable.
    new_txns = []
    for raw in new_raw:
        row = _cache_transaction(raw)
        if row is not None:
            new_txns.append(row)

    merged = {t["date"] + "|" + t["description"]: t for t in (cached + new_txns)}
    result = sorted(merged.values(), key=lambda t: t["date"])
    if use_cache:
        _save_cached_transactions(player_id, result)
    return result


def _season_windows_for(years: range) -> list[SeasonWindow]:
    """League windows: the domestic opener and the real season end. Used where
    the player's club is not known. build_player_record uses the per-club
    variant below instead."""
    windows = []
    for year in years:
        start, end = mlb.get_season_window(year)
        windows.append(SeasonWindow(year=year, start=start, end=end))
    return windows


def _opening_day_for(
    year: int, opening_day_club: int | None, candidate_clubs: list[int]
) -> dt.date:
    """The season-window START for a player (rules v7: the CBA-uniform opener).

    CBA Art. XXI(A)(1) credits service "uniformly to all Players and all Clubs
    ... notwithstanding differences in a particular Club's schedule", so every
    club shares ONE Opening Day -- the league-wide uniform start -- and a club
    that opened a day LATE domestically (DET 04-08 vs the league 04-07 in 2022)
    does not cost its players a day. The one exception is Art. V(A): a club that
    opened OVERSEAS earlier (Seoul/Tokyo/Mexico, outside the 187-day window)
    keeps its own earlier date; otherwise applying the league opener to it would
    erase the early series it really played (the ~8-days-high regression the
    Baseball Reference audit caught). Both are captured by taking, for a club,
    min(its own opener, the uniform start): a late domestic opener snaps up to
    the uniform date, an early overseas opener stays put.

      * `opening_day_club` known -> min(that club's own opener, uniform). This is
        the club he was with at Opening Day (the earliest club of the season by
        transaction date -- see _opening_day_clubs), the ONLY defensible choice
        for a traded player: one who opened overseas and was traded away in June
        still earned the early start, while one who opened domestically and was
        traded TO an overseas club never played its early series.
      * unknown -> the LATEST of his candidate clubs' CBA starts. Conservative:
        it can never credit a day before any of his clubs began, and
        over-crediting is the failure mode behind every revert in this project,
        so a player reading a few days low is the safe way to be wrong.
      * no clubs at all -> the uniform start.
    """
    uniform = mlb.get_uniform_season_start(year)
    if opening_day_club:
        return min(mlb.get_club_season_start(year, opening_day_club), uniform)
    starts = [
        min(mlb.get_club_season_start(year, c), uniform)
        for c in candidate_clubs
        if c
    ]
    return max(starts) if starts else uniform


def _season_windows_for_player(
    years: range,
    season_teams_bio: dict[int, list[int]],
    txn_team_ids: dict[int, list[int]],
    opening_day_clubs: dict[int, int],
) -> list[SeasonWindow]:
    """Per-player season windows: each season starts on the opener of the club
    the player was with at Opening Day, and ends on the real last-played date.

    `opening_day_clubs` maps season year -> the club he opened with, derived
    from his earliest transaction of that season (see _opening_day_clubs). For
    a season with no transactions the club is resolved the same way
    _season_teams() does -- stat splits, then transactions, then carried
    forward from the prior season -- because a silent season almost always
    means he stayed put, so last year's club is still this year's at the opener.
    """
    windows = []
    carried: list[int] = []
    for year in years:
        _, league_end = mlb.get_season_window(year)
        candidates = season_teams_bio.get(year) or txn_team_ids.get(year) or carried
        if candidates:
            carried = candidates
        # The season ends on the league's last regularly scheduled game for
        # almost everyone; it runs a day LATER only for a club that actually
        # played a tiebreaker (rules v7). get_club_season_end floors at the
        # league end, so a club whose own games ended earlier is unaffected.
        end = league_end
        for c in candidates:
            if c:
                end = max(end, mlb.get_club_season_end(year, c))
        windows.append(
            SeasonWindow(
                year=year,
                start=_opening_day_for(year, opening_day_clubs.get(year), candidates),
                end=end,
            )
        )
    return windows


def _opening_day_clubs(raw_txns: list[dict], mlb_ids: set[int]) -> dict[int, int]:
    """{season year -> the club this player opened the season with}.

    The Opening Day club is the ORIGIN of his earliest transaction that season
    -- the club he was already with going into that move. For a trade,
    "A traded X to B", the origin is A (from_team); for "A optioned X" it is the
    acting club A; for "B claimed X off waivers from A" it is A, the club he was
    leaving. So the preference is from_team, then the acting team, then to_team,
    and the chronologically first transaction of the year wins.

    A year with no transaction at all is absent from the result, and
    _opening_day_for falls back to the carried-forward club for it.
    """
    out: dict[int, int] = {}
    for t in sorted(raw_txns, key=lambda t: t.get("date") or ""):
        date = t.get("date")
        if not date or not _involves_mlb_club(t, mlb_ids):
            continue
        year = int(date[:4])
        if year in out:
            continue  # first (earliest) transaction of the year wins
        origin = next(
            (
                t.get(key)
                for key in ("from_team_id", "team_id", "to_team_id")
                if t.get(key) in mlb_ids
            ),
            None,
        )
        if origin is not None:
            out[year] = origin
    return out


_MLB_TEAM_IDS: set[int] | None = None


def mlb_team_ids() -> set[int]:
    """The 30 major league club IDs, fetched once and reused."""
    global _MLB_TEAM_IDS
    if _MLB_TEAM_IDS is None:
        _MLB_TEAM_IDS = {t["id"] for t in mlb.get_teams() if isinstance(t.get("id"), int)}
    return _MLB_TEAM_IDS


_ASSIGNED_TO_RE = re.compile(r"\bassigned to\b", re.I)
_REHAB_RE = re.compile(r"rehab", re.I)


def _is_minor_assignment(txn: dict, mlb_ids: set[int]) -> bool:
    """True when this row sends the player to a club that is not major league.

    "Assigned to Durham" closes an open major-league stint. "Assigned to the
    Yankees" does not -- that destination is a major-league club, and the
    wording is how a spring-training roster note is phrased. A rehab
    assignment keeps him on his major-league injured list, so it is never
    this kind of departure.
    """
    desc = txn.get("description") or ""
    if not _ASSIGNED_TO_RE.search(desc) or _REHAB_RE.search(desc):
        return False
    to_id = txn.get("to_team_id")
    return isinstance(to_id, int) and to_id not in mlb_ids


_NOT_ACTIVE_ASSIGNMENT_RE = re.compile(r"outright|\boption", re.I)


def _is_mlb_assignment(txn: dict, mlb_ids: set[int]) -> bool:
    """True when this row assigns the player to one of the 30 clubs.

    The flag is not itself a start. Spring-training roster notes use the
    same words, and so does a minor-league assignment read the wrong way.
    build_global_active_intervals restarts a stopped clock only when a later
    row shows he was on that club's active roster. Rehab, optional, and
    outright assignments are never this kind of return.
    """
    desc = txn.get("description") or ""
    if (
        not _ASSIGNED_TO_RE.search(desc)
        or _REHAB_RE.search(desc)
        or _NOT_ACTIVE_ASSIGNMENT_RE.search(desc)
    ):
        return False
    to_id = txn.get("to_team_id")
    return isinstance(to_id, int) and to_id in mlb_ids


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
        if detail.get("has_il"):
            rows[-1]["il"] = True
    return rows


def _stat_groups_with_games(stat: dict) -> dict[str, int]:
    """{'hitting': games, 'pitching': games} for groups that have games.

    The bio statline stores each group under that name. A missing or
    non-numeric gamesPlayed reads as no games, which simply skips the
    appearance fill for that group.
    """
    out: dict[str, int] = {}
    if not isinstance(stat, dict):
        return out
    for group in ("hitting", "pitching"):
        row = stat.get(group)
        if not isinstance(row, dict):
            continue
        try:
            games = int(row.get("gamesPlayed") or 0)
        except (TypeError, ValueError):
            games = 0
        if games > 0:
            out[group] = games
    return out


def _still_on_ml_roster(transactions: list[Transaction]) -> bool:
    """Does the player's most recent major-league roster move leave him ON a
    roster?

    A player stashed on the 60-day injured list is still carried by his club and
    still accruing service, but he drops off the 40-man LISTING, so the published
    on_40_man flag (which the backfill feeds in as `currently_rostered`) reads
    False for him. Used together with the bio's `active` flag to tell such a
    player apart from one who is genuinely done (retired, or gone to the minors).

    True if his latest roster-defining move is a START -- an activation, recall,
    contract selection, or an injured-list placement (IL placements read as a
    start, see is_active_start) -- or a trade, which keeps him in the majors.
    False if it is a STOP (release, outright, option, DFA, restricted list), a
    free-agency election, or if he has no major-league roster move at all.

    A free-agency election counts as leaving. The service-time walk stops the
    clock on the same wording (rules v9); a later signing starts it again.
    This guard only runs for a player who is NOT currently rostered, and it
    reads the LATEST event: an active player who elected free agency and
    re-signed has a later start/recall that wins first.
    Without this, a player who was selected to the 40-man and then elected free
    agency and drifted through the minors (Aaron Sanchez: 2022 selection, then
    'elected free agency', then only minor-league deals) read as still on a
    roster off a two-year-old selection and accrued phantom service to today.
    """
    # Date-grouped, and a stop or a free-agency election wins over a start
    # on the same day. Walking row by row made the answer depend on which
    # of two same-day rows came first: an activation filed next to
    # "elected free agency" (Yency Almonte, 2024-11-04) read as still on a
    # roster and the clock ran on.
    by_date: dict[dt.date, list[Transaction]] = {}
    for t in transactions:
        by_date.setdefault(t.date, []).append(t)
    for day in sorted(by_date, reverse=True):
        rows = by_date[day]
        descs = [r.description for r in rows]
        has_stop = any(is_active_stop(d) or r.minor_assignment for r, d in zip(rows, descs))
        has_fa = any(is_free_agency_election(d) for d in descs)
        has_start = any(row_opens_roster(d, descs) for d in descs)
        has_trade = any(is_trade(d) for d in descs)
        if has_stop or has_fa:
            return False
        if has_start or has_trade:
            return True
    return False


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
    transactions = []
    for t in raw_txns:
        minor_assignment = _is_minor_assignment(t, mlb_ids)
        mlb_assignment = _is_mlb_assignment(t, mlb_ids)
        desc = t.get("description") or ""
        # A minor-league release, outright, retirement, or assignment is the
        # move that ends a lot of careers. The MLB-club filter used to drop
        # every one of them, so the earlier major-league signing stayed open.
        if not (
            _involves_mlb_club(t, mlb_ids)
            or minor_assignment
            or mlb_assignment
            or is_active_stop(desc)
            or is_free_agency_election(desc)
        ):
            continue
        transactions.append(
            Transaction(
                date=dt.date.fromisoformat(t["date"]),
                description=desc,
                team=(t.get("team") or {}).get("name") if isinstance(t.get("team"), dict) else t.get("team"),
                minor_assignment=minor_assignment,
                mlb_assignment=mlb_assignment,
            )
        )

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

    # The statline the /people hydrate has always returned. Minor-league
    # club lines are dropped here, where the 30 club ids are known. A
    # mid-season trade also arrives as a no-team combined row (numTeams >= 2);
    # that row is the season total and is kept, with one split per club.
    # See select_major_league_season_stats().
    season_stats = mlb.select_major_league_season_stats(
        mlb.season_stats_from_bio(bio), mlb_ids
    )

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

    # Hand-kept restricted-list service-time exceptions for this player (rules
    # v7). counts=true spells (paid DV administrative leave, 2020 opt-outs) are
    # credited back; counts=false spells (a suspension the feed shows only as a
    # restricted-list placement) are removed. See data/service_time_exceptions.json.
    force_active_periods, force_inactive_periods = service_time_exceptions.periods_for_player(
        _SERVICE_TIME_EXCEPTIONS, player_id
    )

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
    # roster. The source it TRUSTS is the player's own year-by-year splits:
    #
    #   * the last season those splits show an MLB appearance, and
    #   * the end of any counts=true service exception (paid administrative
    #     leave is real MLB service and extends the career the same way an
    #     appearance does -- this is what keeps Trevor Bauer's 2022 Opening-Day
    #     days in the window).
    #
    # It deliberately does NOT reach for "the last roster transaction" any more
    # (rules v7). That was the Trevor Bauer bug: with lastPlayedDate null, his
    # last roster event was a January-2023 DFA and release -- months after his
    # last MLB service -- which stretched his career through 2023. A stray
    # offseason transaction must not make a career longer. Only when the splits
    # show no appearance at ALL (a man who reached a roster without ever
    # playing) do we fall back to a roster event, and then a bare transaction.
    # A player on the 60-day injured list is still carried by his club and still
    # accruing, but he drops off the 40-man LISTING, so the published on_40_man
    # flag -- which the backfill feeds in as `currently_rostered` -- reads False
    # for him. Capping his career at his last game then erased every IL season
    # after it: Anthony Rendon, two full seasons on the 60-day IL, lost 345 days;
    # Brusdar Graterol, Joe Jimenez and ~10 others lost a year or two each. If
    # the bio still lists him active AND his latest major-league roster move
    # keeps him on a roster (an IL placement or an activation, not a
    # release/outright/option/DFA), he has not stopped playing and must keep
    # accruing through the horizon exactly as a listed player does. The
    # _still_on_ml_roster guard is what stops this from reviving a player who
    # really did leave MLB for the minors (Aaron Sanchez): his last major-league
    # roster event is a free-agency election, so the guard is False and the cap
    # still applies.
    still_accruing = currently_rostered or (
        bool(bio.get("active")) and _still_on_ml_roster(transactions)
    )
    last_played = bio.get("lastPlayedDate")
    career_end: dt.date | None = None
    if not still_accruing:
        if last_played:
            career_end = dt.date.fromisoformat(last_played)
        else:
            # No last-played date: the feed never recorded a final game, so his
            # clock ends when he actually left a major-league roster. That is the
            # LATEST of three things: the end of the last season his splits show
            # an appearance; the end of a counts=true service exception; and the
            # date of his last major-league roster START -- an activation, IL
            # placement, recall, contract selection, or a trade (each of which
            # put him ON a roster that day). That last term is finding #6's real
            # fix: a player stashed on the 60-day injured list across a season he
            # never appeared in, then outrighted, earned every roster day up to
            # that outright. Capping at his last appearance season instead threw
            # those IL seasons away -- Alec Marsh and Rob Zastryzny each spent
            # 2025-26 on the IL and lost a year or two. Christian Bethancourt hits
            # the same bug from a different roster START: minor-league deals
            # through 2025-26, then a 2026-09-01 Pirates selection, lost the same
            # way. The later outright/DFA is a STOP, so compute_service_time
            # closes the final interval at it on its own; only the real roster
            # time is credited.
            #
            # A STOP must NOT extend career_end on its own -- that was the Trevor
            # Bauer bug the old "last transaction" fallback caused: with
            # lastPlayedDate null his last event was a January-2023 DFA and
            # release, months after his last MLB service, which stretched his
            # window into 2023. His last START is in 2022, so career_end stays in
            # 2022 and the stray offseason stop is ignored. A minor-league signing
            # or assignment (Aaron Sanchez's 2026 Royals minor-league deal) is
            # neither a start nor a trade, so it cannot stretch a career either.
            # A start that shares its day with a stop, a free-agency
            # election, a minor-league assignment, or a minor-league-deal
            # sibling did not leave him on a major-league roster. Counting
            # it used to stretch the career through a season he had already
            # left (Kaminsky's 2022 signing, closed the same morning by the
            # assignment to Arkansas).
            by_day: dict[dt.date, list[Transaction]] = {}
            for t in transactions:
                by_day.setdefault(t.date, []).append(t)
            roster_starts = []
            for day, rows in by_day.items():
                descs = [r.description for r in rows]
                if any(r.minor_assignment for r in rows):
                    continue
                if any(is_free_agency_election(d) or is_active_stop(d) for d in descs):
                    continue
                if any(row_opens_roster(d, descs) or is_trade(d) for d in descs):
                    roster_starts.append(day)
            candidates = []
            if season_teams_bio:
                candidates.append(dt.date(max(season_teams_bio), 12, 31))
            candidates.extend(end for _start, end in force_active_periods)
            if roster_starts:
                candidates.append(max(roster_starts))
            if candidates:
                career_end = max(candidates)
            else:
                # Never appeared and never started: last resort is a real roster
                # stop, and only then a bare transaction date.
                roster_stops = [
                    t.date for t in transactions if is_active_stop(t.description)
                ]
                if roster_stops:
                    career_end = max(roster_stops)
                elif transactions:
                    career_end = max(t.date for t in transactions)

    debut_year = debut_date.year if debut_date else MIN_TRANSACTION_YEAR
    first_year = max(debut_year, MIN_TRANSACTION_YEAR)
    last_year = horizon_end.year
    if career_end is not None:
        last_year = min(last_year, max(career_end.year, first_year))
    years = range(first_year, last_year + 1)
    # Per-club season windows: each season starts on the opener of the club
    # this player was with at Opening Day (Seoul/Tokyo clubs a week early, the
    # rest on their own domestic Opening Day) and ends on the real last-played
    # date. See _season_windows_for_player.
    opening_day_clubs = _opening_day_clubs(raw_txns, mlb_ids)
    seasons = _season_windows_for_player(
        years, season_teams_bio, txn_team_ids, opening_day_clubs
    )

    # End of his final season, not his final game: service time is roster
    # time, so a player keeps accruing after his last appearance if he stays
    # on the active roster or the IL through the end of the year.
    accrual_ceiling = seasons[-1].end if (career_end is not None and seasons) else None

    missing = _missing_seasons(debut_date, transactions)

    def _run_compute(appearance_dates: list[dt.date] | None = None):
        return compute_service_time(
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
            # Seasons a top-two Rookie of the Year finish credits as a full year,
            # keyed by this player's id. Empty for almost everyone.
            roy_full_year_seasons=_ROY_FULL_YEAR_SEASONS.get(player_id),
            accrual_floor=accrual_floor,
            accrual_ceiling=accrual_ceiling,
            # Stop the clock at horizon_end (today, for the daily job) rather than
            # at the end of the current season -- otherwise every player currently
            # on a roster is credited with the remaining weeks of a season that
            # hasn't been played yet.
            horizon_end=horizon_end,
            # Rules v7: the DFA grace window and the hand-kept restricted-list
            # service exceptions for this player.
            dfa_limbo_days=_DFA_LIMBO_DAYS,
            force_active_periods=force_active_periods,
            force_inactive_periods=force_inactive_periods,
            appearance_dates=appearance_dates,
        )

    result = _run_compute()
    # A season with a lot of MLB games and almost no credited days is a
    # recall the transaction feed never recorded. Pull that season's game
    # dates (one request per group) and credit the span they prove. Only
    # seasons inside the transaction era: earlier careers are presumed, and
    # a game log must not rewrite them.
    appearance_dates: list[dt.date] = []
    for year, stat in season_stats.items():
        if year < TRANSACTION_COVERAGE_START_YEAR:
            continue
        groups = _stat_groups_with_games(stat)
        if not groups:
            continue
        games = sum(groups.values())
        credited = result.by_season.get(year, {}).get("credited_days", 0)
        if games >= 20 and credited + 15 < games:
            for group in groups:
                try:
                    appearance_dates.extend(mlb.appearance_dates(player_id, year, group))
                except Exception:
                    # A missing game log leaves the transaction figure in
                    # place. The sanity check reports the season if it stays
                    # near zero.
                    pass
    if appearance_dates:
        result = _run_compute(appearance_dates)

    return {
        "id": player_id,
        "name": roster_entry.get("fullName") or bio.get("fullName"),
        "team": roster_entry.get("team"),
        "team_id": roster_entry.get("teamId"),
        "position": roster_entry.get("position"),
        "mlb_debut": debut,
        # Age, not service time, is what the valuation literature is about.
        # Fair (2008) estimates peak performance at 27.6 (OPS) / 28.3 (OBP) /
        # 26.5 (ERA), and Solow & Krautmann (2020) forecast a contract's value
        # by ageing the player through that curve -- so a 26-year-old rookie
        # and a 22-year-old rookie have identical clocks and very different
        # remaining value. See research/valuation-literature.md.
        #
        # It costs nothing to store: /people is already fetched for every
        # player and its response has carried birthDate all along. We simply
        # threw it away.
        "birth_date": bio.get("birthDate"),
        # Season-by-season statline, keyed by year. Costs no extra API call --
        # see season_stats_from_bio(). Stored on the record but NOT shipped in
        # the compact table index: statlines are far larger than day counts and
        # the index is 0.22 MB on purpose. They ride in the profile shards,
        # which are fetched one at a time only when a profile is opened.
        "season_stats": {str(y): v for y, v in sorted(season_stats.items())},
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
        # The date this one record was rebuilt. It is not the daily job's
        # "already done today" signal: one record moving to today does not
        # mean the roster refresh finished. That signal is
        # data/refresh_complete.json, written only after a full successful run.
        "last_updated": TODAY.isoformat(),
        "rules_version": SERVICE_TIME_RULES_VERSION,
    }


def _rules_block() -> dict:
    """The CBA thresholds the browser needs to turn a day count into a status.

    These used to be literals in docs/app.js -- a second implementation of the
    CBA rules, in a second language, which had to be edited in step with the
    Python or the table and the static pages would disagree.

    Stamped into BOTH published data files, not just the compact index. app.js
    falls back from index.json to service_time.json when an older deployment
    has not regenerated the index yet, and that fallback used to leave every
    threshold null: the meter read 100% for every player, formatDays() returned
    "Infinity.NaN", and the profile footer said "null days credit a full year".
    A fallback that renders nonsense is worse than no fallback, and shipping
    this block in both files is what makes the documented one actually work.
    """
    return {
        "version": _RULES.version,
        "full_year_days": _RULES.require("service_time.days_per_credited_year"),
        "free_agency_years": _RULES.require("free_agency.credited_years_required"),
        "arbitration_years": _RULES.require("arbitration.standard_years_required"),
        "super_two_min_years": _RULES.require(
            "arbitration.super_two.service_range_years"
        )[0],
        "super_two_max_years": _RULES.require(
            "arbitration.super_two.service_range_years"
        )[1],
        "super_two_min_days": _RULES.require(
            "arbitration.super_two.heuristic_min_days"
        ),
        # The league minimum is the one dollar figure this project can state as
        # a FACT rather than an estimate: it is written into the agreement, not
        # inferred from anything. It is what a pre-arbitration player is paid,
        # which is a different question from what he is worth -- and the only
        # one of the two this site can answer.
        "mlb_minimum": _RULES.require("salary.mlb_minimum"),
        "mlb_minimum_year": _RULES.require("salary.mlb_minimum_year"),
    }


def _birth_year(player: dict) -> int | None:
    """
    Birth YEAR from the stored ISO birth date, or None.

    None is the honest answer for a record written before birth_date was
    persisted, and the browser must render it as "age unknown" rather than
    computing an age from a missing field -- the same discipline as the null
    tax rates. Every rostered player picks one up on the next daily run; the
    non-rostered ones only on a recompute.
    """
    raw = player.get("birth_date")
    if not raw:
        return None
    try:
        return int(str(raw)[:4])
    except (TypeError, ValueError):
        return None


def _published_super_two_history() -> dict | None:
    """
    The PUBLISHED Super Two cutoffs, from data/reference_super_two.json.

    Shipped so the page can describe how the real cutoff moves instead of
    quoting this project's own computed figures back at the reader. app.js
    used to say the cutoff "has moved between 2.126 and 2.136 over the last
    four years" -- both literals were OUR estimates, and the published range
    over 2021-2025 is wider than that on both ends (2.116 to 2.140). A site
    that measures its own error has no business understating the thing it is
    estimating.

    Returns None when no figure has been entered, so the UI drops the claim
    rather than inventing a range from one point.
    """
    try:
        rows = json.loads(REFERENCE_SUPER_TWO.read_text())
    except (OSError, ValueError):
        return None

    figures = {
        int(r["season"]): str(r["published"])
        for r in rows
        if isinstance(r, dict) and r.get("season") and r.get("published")
    }
    if not figures:
        return None

    return {
        "seasons": [min(figures), max(figures)],
        "low": min(figures.values()),
        "high": max(figures.values()),
        "count": len(figures),
        "by_season": {str(k): v for k, v in sorted(figures.items(), reverse=True)},
        "source": "MLB Trade Rumors / Associated Press offseason reports, entered by hand",
    }


def write_index(
    db: dict[str, dict],
    super_two_cutoff: dict | None = None,
    ever_published: set[int] | None = None,
) -> None:
    """
    Emit the compact file the frontend downloads.

    `service_time.json` is the database: every field, one object per player,
    and the pipeline's own source of truth on the next run. At 5,592 players it
    is 9.1 MB, and the page was fetching all of it on every visit for a table
    that reads eleven fields.

    This writes a derived view instead, 0.22 MB (98% smaller), by removing
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
            # Does this player have a static page? The table links to one when
            # he does, and renders a plain button when he does not, because a
            # link to a 404 is worse than no link.
            #
            # Shipped as a flag rather than recomputed in the browser: the rule
            # is _should_publish() in write_player_pages.py, and the browser
            # cannot evaluate it -- the compact index carries no season
            # breakdown, so it cannot count a player's credited seasons. A
            # second copy of the rule in JS would be the exact drift the CBA
            # thresholds were consolidated to stop.
            #
            # `ever_published` makes this agree with write_player_pages():
            # a player's page is never taken down just because he left a
            # roster, so this flag must not go back to 0 the day that happens
            # either, or the table would show a dead-end "no page" button for
            # a player who still has one.
            1 if write_player_pages._should_publish(p, ever_published) else 0,
            # Birth year, not the full date. The contract tool needs to say how
            # old a player will be when he reaches a threshold, which only
            # needs the year; shipping the exact birthday would add a personal
            # detail to a 0.22 MB payload for no gain in what the page can say.
            _birth_year(p),
        ])

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "MLB Stats API (statsapi.mlb.com, unofficial public endpoint)",
        "disclaimer": (
            "Service time figures are estimates from public data, "
            "not official MLB numbers."
        ),
        "player_count": len(rows),
        "teams": teams,
        "positions": positions,
        # Self-documenting, so the row layout is readable without the code.
        "super_two_cutoff": super_two_cutoff,
        # What was actually published, as distinct from what we compute.
        "super_two_published": _published_super_two_history(),
        # Shipping the thresholds here rather than making the browser fetch the
        # ruleset keeps the page to one request. See _rules_block().
        "rules": _rules_block(),
        "fields": [
            "id", "name", "team", "position", "days", "on_40_man",
            "missing_seasons", "super_two", "has_page", "birth_year",
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
            # Empty for any record written before this shipped. The tab says
            # "not yet collected" rather than rendering blank cells, and the
            # next full run fills it in.
            "season_stats": p.get("season_stats") or {},
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


def mlb_fetch_enabled() -> bool:
    """The backup switch: MLB_FETCH_ENABLED=false skips every MLB Stats API
    call and rebuilds the published site from whatever is already stored.

    Defaults to enabled (true). Read as a string, not a Python bool, because
    it comes from an environment variable / GitHub Actions workflow input,
    where both arrive as text -- "false" and "0" are the only recognized
    ways to disable it, so a blank or unset value is unambiguously "on".
    See README.md, "Turning off live fetching" for how to flip it.
    """
    return os.environ.get("MLB_FETCH_ENABLED", "true").strip().lower() not in (
        "false", "0", "no", "off",
    )


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

    if not mlb_fetch_enabled():
        print(
            "MLB_FETCH_ENABLED is false -- skipping every MLB Stats API call "
            "and rebuilding the published site from the stored database only."
        )
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
    # After publish, and only then. A crash while writing leaves the previous
    # marker in place, so a later run does not treat a half-finished publish
    # as done. --recompute-derived and a disabled fetch return above and do
    # not get here: neither one refreshed the roster from the API.
    _mark_refresh_complete(args.limit, failures, len(roster))


# Sanity bounds for a daily run. Every one of them describes a failure that
# publishes CONFIDENTLY WRONG data rather than crashing, which is the only
# kind worth a gate: a crash leaves yesterday's good file in place, a silent
# success overwrites it.
# 30 clubs x 40 = 1,200 nominal, with slack. refresh_complete.MIN_ROSTERED
# is the same floor: a run that was too short to publish is too short to
# count as the day's refresh. tests/test_refresh_complete.py keeps them equal.
MIN_EXPECTED_ROSTER = 900
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
    # A season window this run had to guess at rather than fetch. Every day
    # count for that season is then computed against Mar 28 - Oct 1 instead of
    # the real schedule, which is the same estimate that once put Jose Ramirez
    # 8 days out and made him look like a model defect. The daily job only ever
    # asks for seasons that have already happened, so a fallback here is an
    # outage, never a legitimately-unscheduled year.
    estimated = mlb.estimated_season_windows()
    if estimated:
        problems.append(
            "season windows were estimated rather than fetched for "
            f"{', '.join(str(y) for y in sorted(estimated))} -- every figure "
            "touching those seasons is computed against a guessed schedule"
        )
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


def _mark_refresh_complete(limit: int | None, failures: int, rostered: int) -> None:
    """Write the completion marker after a full roster refresh has published.

    A limited run, any player who failed to build, or a short roster leaves
    the previous marker alone. The next scheduled run then still does the work.
    """
    if not refresh_complete.should_mark_complete(
        limit=limit, failures=failures, rostered=rostered
    ):
        limited = ", limited run" if limit is not None else ""
        print(
            "Not marking the daily refresh complete "
            f"({failures} failure(s), {rostered} rostered{limited})."
        )
        return
    completed_on = refresh_complete.utc_today()
    marker = refresh_complete.build_marker(
        completed_on=completed_on,
        rostered=rostered,
        updated=rostered - failures,
        failures=failures,
        rules_version=SERVICE_TIME_RULES_VERSION,
    )
    path = refresh_complete.marker_path(ROOT)
    refresh_complete.write_marker(path, marker)
    print(
        "Marked the daily refresh complete for "
        f"{completed_on} ({marker['updated']} of {marker['rostered']} rostered players)."
    )


def publish_config() -> list[pathlib.Path]:
    """
    Copy config/ into docs/data/config/ so the browser can read the rulesets.

    The duty-day tool runs entirely client-side, which means it needs the tax
    rules table and the day-type rules as fetchable files. They live in
    config/ because that is where a human edits them and where the Python
    reads them; this publishes a copy under the site root.

    Copied wholesale rather than filtered: a ruleset that exists but was not
    published would give the browser a different answer than the pipeline,
    which is the exact failure this project already had once when index.json
    sat frozen while the database updated underneath it.
    """
    written: list[pathlib.Path] = []
    if not CONFIG_SRC.exists():
        print(f"  !! no config/ directory at {CONFIG_SRC}; nothing published")
        return written

    if CONFIG_OUT.exists():
        shutil.rmtree(CONFIG_OUT)  # rebuilt wholesale, like the player pages
    for src in sorted(CONFIG_SRC.rglob("*.json")):
        dest = CONFIG_OUT / src.relative_to(CONFIG_SRC)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        written.append(dest)
    print(f"Published {len(written)} config file(s) to {CONFIG_OUT}")
    return written


def _stamp_contract_extensions(db: dict[str, dict]) -> None:
    """Mark players whose extension the lists must honor.

    Reads the transaction cache and data/contract_overrides.json. No API
    calls. The service-time flag `arbitration_eligible` stays a service-time
    fact; `contract_extension` is what takes him off the winter lists.
    """
    overrides = salary_estimates.load_contract_overrides()
    for record in db.values():
        pid = record.get("id")
        try:
            pid_int = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            pid_int = None
        record["contract_extension"] = salary_estimates.has_contract_extension(
            pid_int, overrides
        )


def _write_outputs(db: dict[str, dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    publish_config()

    # Loaded once and threaded through write_index() and _write_pages() below,
    # so the `has_page` column and the actual docs/p/ directory are built from
    # the same snapshot of "who has ever had a page". write_player_pages()
    # grows and re-saves this file; write_index() only reads it.
    ever_published = write_player_pages.load_ever_published()

    # Super Two needs the whole population, so it can only be settled once
    # every record exists -- see scripts/super_two.py. This replaces the flat
    # 86-day flag that compute_service_time() sets per player without any
    # league context.
    _stamp_contract_extensions(db)

    reference_season = super_two.latest_complete_season(db, TODAY.year)
    # The hand-kept, unofficial reported cutoff (see data/super_two_reported.json)
    # only applies to the SAME season the computed cutoff was just measured
    # from (compute_cutoff's "offseason after `year`" convention, matching
    # data/reference_super_two.json) -- load_reported_cutoff() checks that
    # and returns None for a stale or mismatched entry, the graceful
    # fallback to a single cutoff.
    reported = super_two.load_reported_cutoff(projected_season=reference_season)
    cutoff = super_two.apply_super_two(db, reference_season, reported=reported)
    if cutoff:
        range_note = ""
        if cutoff.get("range") and cutoff["range"]["is_range"]:
            range_note = (
                f"; reported high end {cutoff['range']['high']} gives a range of "
                f"{cutoff['range']['low']}-{cutoff['range']['high']} "
                f"({cutoff['projected_bubble']} on the bubble)"
            )
        print(
            f"Super Two cutoff after {cutoff['season']}: {cutoff['cutoff']} "
            f"({cutoff['class_size']} in the 2-3 year class, top "
            f"{cutoff['qualifying_count']} qualify); "
            f"{cutoff['projected_candidates']} players currently project above it"
            f"{range_note}"
        )
    else:
        print("Super Two: class too small to measure a cutoff; heuristic flags kept.")

    output = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "MLB Stats API (statsapi.mlb.com, unofficial public endpoint)",
        # NO COVERAGE CUTOFF YEAR. This used to read "Transaction coverage
        # begins in 2009; players who debuted earlier are marked as having
        # incomplete history" -- a claim this project's own probe disproved.
        # scripts/probe_coverage.py returned real 2005-2008 major league rows
        # ("Minnesota Twins activated LF Lew Ford from the 15-day disabled
        # list", 2006-08-11), so coverage THINS going back rather than
        # switching on in a particular year, and _missing_seasons() has
        # measured it per player since. Publishing the old sentence meant the
        # most prominent line on the site asserted something the pipeline
        # behind it had stopped believing. Same wording as write_index() now,
        # so the two files cannot describe the data differently.
        "disclaimer": (
            "Service time figures are estimates from public data, "
            "not official MLB numbers."
        ),
        "rules": _rules_block(),
        "player_count": len(db),
        "super_two_cutoff": cutoff,
        "incomplete_history_count": sum(
            1 for p in db.values() if not p.get("history_complete", True)
        ),
        "players": sorted(db.values(), key=lambda p: p.get("name") or ""),
    }
    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"Wrote {len(db)} player records to {OUTPUT_FILE}")
    write_index(db, cutoff, ever_published)
    write_profiles(db)
    # The empirical accrual distribution behind the contract page's threshold
    # projections. Derived from the database, so it belongs with the other
    # derived passes and is refreshed by --recompute-derived like the rest.
    _write_accrual_model(db)
    # Crawlable static pages. Hash routing is invisible to search engines, so
    # without these no player could be found by searching for him.
    _write_pages(db, output["generated_at"], super_two_cutoff=cutoff, ever_published=ever_published)
    report_debuted_but_empty(db)
    # A league-minimum salary ESTIMATE for clearly pre-arbitration players,
    # skipping anyone with a known or detected extension. No API calls --
    # see scripts/salary_estimates.py.
    salary_path = salary_estimates.write(db)
    print(f"Wrote salary estimates to {salary_path}")


def _write_accrual_model(db: dict[str, dict]) -> None:
    """Measure how many days comparable players accrued the following season.

    The contract page projects threshold crossings from this rather than from
    an assumption of full seasons -- see scripts/accrual_model.py for why that
    distinction matters and what the population is conditioned on.
    """
    model = accrual_model.build(
        list(db.values()),
        TODAY.year,
        _RULES.require("service_time.days_per_credited_year"),
    )
    accrual_model.OUT_PATH.write_text(json.dumps(model, indent=1) + "\n", encoding="utf-8")
    bands = sum(1 for b in model["bands"] if b.get("enough_data"))
    print(
        f"Wrote accrual model: {model['transitions']:,} player-season transitions, "
        f"{bands} of {len(model['bands'])} bands with enough data"
    )


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
