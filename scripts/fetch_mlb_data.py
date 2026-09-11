"""
fetch_mlb_data.py
------------------
Thin client for the public (unofficial, undocumented but widely used)
MLB Stats API at https://statsapi.mlb.com. No API key is required.

This module only does HTTP + light shaping -- all service-time math lives
in service_time.py.
"""

from __future__ import annotations

import datetime as dt
import functools
import sys
import time
from typing import Any

import requests

BASE_URL = "https://statsapi.mlb.com/api/v1"
SPORT_ID_MLB = 1

_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": "mlb-service-time-tracker/1.0 (+https://github.com/; contact: repo owner)",
        "Accept": "application/json",
    }
)

REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2
POLITE_DELAY_SECONDS = 0.15  # small delay between calls to be a good API citizen


def _get(path: str, params: dict[str, Any] | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            time.sleep(POLITE_DELAY_SECONDS)
            return resp.json()
        except requests.RequestException as exc:  # pragma: no cover - network path
            last_err = exc
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"Failed GET {url} after {MAX_RETRIES} attempts: {last_err}")


def get_teams(season: int | None = None) -> list[dict]:
    params = {"sportId": SPORT_ID_MLB}
    if season:
        params["season"] = season
    data = _get("/teams", params)
    return data.get("teams", [])


def get_40man_roster(team_id: int) -> list[dict]:
    data = _get(f"/teams/{team_id}/roster", {"rosterType": "40Man"})
    return data.get("roster", [])


def get_all_40man_players(season: int | None = None) -> list[dict]:
    """
    Returns a de-duplicated list of players currently on any team's 40-man
    roster, with basic bio fields attached.
    """
    players: dict[int, dict] = {}
    for team in get_teams(season=season):
        team_id = team["id"]
        team_name = team.get("name", "")
        for entry in get_40man_roster(team_id):
            person = entry.get("person", {})
            pid = person.get("id")
            if pid is None:
                continue
            players[pid] = {
                "id": pid,
                "fullName": person.get("fullName"),
                "teamId": team_id,
                "team": team_name,
                "position": entry.get("position", {}).get("abbreviation"),
                "status": entry.get("status", {}).get("description"),
            }
    return list(players.values())


# Season-by-season stats hydrated onto the /people call we already make, so
# knowing which club a player was with in a given season costs no extra
# request. The alternative -- deriving it from the transaction feed -- leaves
# 15% of accruing seasons unattributed, because a player who simply plays all
# year generates no transaction at all (Aaron Judge 2017 and 2024).
BIO_SEASON_TEAMS_HYDRATE = "stats(group=[hitting,pitching],type=yearByYear)"


def get_player_bio(player_id: int, with_season_teams: bool = True) -> dict:
    """
    The player's bio block. With `with_season_teams` (the default) it also
    carries a `stats` array of year-by-year splits, each naming the club --
    see season_teams_from_bio().

    The hydrate is best-effort: if it fails or comes back without people, we
    fall back to the plain call rather than lose the debut date, which the
    whole pipeline depends on.
    """
    if with_season_teams:
        try:
            data = _get(f"/people/{player_id}", {"hydrate": BIO_SEASON_TEAMS_HYDRATE})
            people = data.get("people", [])
            if people:
                return people[0]
        except Exception:  # pragma: no cover - network path
            pass
    data = _get(f"/people/{player_id}")
    people = data.get("people", [])
    return people[0] if people else {}


def season_teams_from_bio(bio: dict) -> dict[int, list[int]]:
    """
    {season year -> [club ids]} from a bio hydrated by get_player_bio().

    Returns raw team ids without judging them. Callers must filter against
    the 30 major league club ids: a player's year-by-year splits can include
    minor league lines, and this module has no business deciding which ids
    are major league ones when the /teams endpoint says so directly.
    """
    out: dict[int, set[int]] = {}
    for group in bio.get("stats") or []:
        for split in group.get("splits") or []:
            season = split.get("season")
            team_id = (split.get("team") or {}).get("id")
            if season is None or team_id is None:
                continue
            try:
                year = int(season)
            except (TypeError, ValueError):
                continue
            out.setdefault(year, set()).add(team_id)
    return {year: sorted(ids) for year, ids in out.items()}


# The statline keys worth publishing, per group. A WHITELIST rather than
# "keep everything": the /people hydrate returns a wide object per season and
# the profile shards are deliberately small (~12 KB), so shipping every field
# for 5,592 players would undo the payload work that split them in the first
# place.
#
# Keys are what MLB's yearByYear splits are documented to use. They are NOT
# verified against a live payload -- this sandbox has no route to
# statsapi.mlb.com -- so season_stats_from_bio() keeps whatever it FINDS and
# never requires a key to be present. A missing key yields a thinner row, not
# an exception, and a key we guessed wrong simply never appears.
#
# That is the lesson of the schedule fixture: a fixture written from an
# assumption about a payload tests the assumption, not the payload.
HITTING_KEYS = (
    "gamesPlayed", "atBats", "runs", "hits", "doubles", "triples",
    "homeRuns", "rbi", "baseOnBalls", "strikeOuts", "stolenBases",
    "avg", "obp", "slg", "ops",
)

PITCHING_KEYS = (
    "gamesPlayed", "gamesStarted", "wins", "losses", "saves",
    "inningsPitched", "strikeOuts", "baseOnBalls", "hits", "earnedRuns",
    "homeRuns", "era", "whip",
)

_STAT_KEYS = {"hitting": HITTING_KEYS, "pitching": PITCHING_KEYS}


def season_stats_from_bio(bio: dict) -> dict[int, dict]:
    """
    {season year -> {"hitting": {...}, "pitching": {...}}} from a hydrated bio.

    The pipeline has ALWAYS fetched this. BIO_SEASON_TEAMS_HYDRATE asks for
    yearByYear hitting and pitching on the /people call made for every player,
    and season_teams_from_bio() reads exactly one field out of each split --
    the team id -- discarding the entire statline unread. This reads it.

    Third time this project has found data it was already paying for and
    throwing away: `by_season` became the profiles, `birthDate` became the age
    column, and this is the statline.

    Team ids are NOT filtered here, for the same reason season_teams_from_bio()
    does not filter them: this module has no business deciding which ids are
    major league ones. A split carrying a minor league team id is dropped by
    the caller, which knows the 30 club ids.
    """
    out: dict[int, dict] = {}
    for group in bio.get("stats") or []:
        name = ((group.get("group") or {}).get("displayName") or "").lower()
        keys = _STAT_KEYS.get(name)
        if not keys:
            continue
        for split in group.get("splits") or []:
            season = split.get("season")
            stat = split.get("stat") or {}
            team_id = (split.get("team") or {}).get("id")
            if season is None or not stat:
                continue
            try:
                year = int(season)
            except (TypeError, ValueError):
                continue
            # Keep what is there; never require a key.
            row = {k: stat[k] for k in keys if stat.get(k) not in (None, "")}
            if not row:
                continue
            row["team_id"] = team_id
            # A player traded mid-season has two splits in the same year. The
            # later one wins rather than the two being summed: summing rate
            # stats (avg, era, ops) is simply wrong, and this project would
            # rather show one club's real line than an invented combined one.
            out.setdefault(year, {})[name] = row
    return out


def get_player_transactions(
    player_id: int,
    start_date: dt.date,
    end_date: dt.date,
) -> list[dict]:
    """
    Fetch all roster transactions for a single player between two dates
    (inclusive). The API caps how much date range it will happily return in
    one call for some query shapes, so callers doing multi-decade pulls
    should chunk by year if they see truncated results.
    """
    data = _get(
        "/transactions",
        {
            "playerId": player_id,
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
        },
    )
    return data.get("transactions", [])


# Seasons whose window came from the fallback below rather than from the API.
# Read by callers that need to know whether a run is trustworthy -- see
# estimated_season_windows().
_ESTIMATED_WINDOWS: set[int] = set()


@functools.lru_cache(maxsize=None)
def get_season_window(year: int) -> tuple[dt.date, dt.date]:
    """
    Returns (regular season start, regular season end) for a given year.
    Falls back to a hardcoded estimate if the seasons endpoint doesn't have
    the data (e.g. far-future seasons not yet scheduled).

    MEMOISED, and that is not a micro-optimisation. The pipeline asks for a
    window once per player per season of his career, so a daily run over
    ~1,370 rostered players made 7,179 calls to fetch the same 22 distinct
    answers -- 7,157 of them redundant, about 32 minutes of wall clock, and
    7,000 needless requests a day against a free public endpoint this project
    goes out of its way to be polite to. A season's start and end date do not
    move within a run, so one call each is all that is correct.

    THE FALLBACK IS LOUD ON PURPOSE. An estimated window is not a cosmetic
    degradation: CLAUDE.md records that this exact estimate (Mar 28 - Oct 1)
    put Jose Ramirez 8 days off and made him look like the one modern outlier
    worth chasing, when the estimate was the error. A /seasons outage would
    otherwise recompute every player against wrong windows and publish the
    result with nothing saying so.
    """
    try:
        data = _get("/seasons", {"sportId": SPORT_ID_MLB, "season": year})
        seasons = data.get("seasons", [])
        if seasons:
            s = seasons[0]
            start = dt.date.fromisoformat(s["regularSeasonStartDate"])
            end = dt.date.fromisoformat(s["regularSeasonEndDate"])
            return start, end
    except Exception as exc:  # pragma: no cover - network path
        print(f"  !! /seasons failed for {year}: {exc}", file=sys.stderr)
    _ESTIMATED_WINDOWS.add(year)
    print(
        f"  !! ESTIMATING the {year} season window as Mar 28 - Oct 1. Every "
        f"figure that depends on {year} is computed against a guess, not the "
        "real schedule.",
        file=sys.stderr,
    )
    # Fallback estimate (late March - early October)
    return dt.date(year, 3, 28), dt.date(year, 10, 1)


def estimated_season_windows() -> set[int]:
    """Seasons this process had to estimate rather than fetch. Empty is healthy."""
    return set(_ESTIMATED_WINDOWS)
