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


def get_season_window(year: int) -> tuple[dt.date, dt.date]:
    """
    Returns (regular season start, regular season end) for a given year.
    Falls back to a hardcoded estimate if the seasons endpoint doesn't have
    the data (e.g. far-future seasons not yet scheduled).
    """
    try:
        data = _get("/seasons", {"sportId": SPORT_ID_MLB, "season": year})
        seasons = data.get("seasons", [])
        if seasons:
            s = seasons[0]
            start = dt.date.fromisoformat(s["regularSeasonStartDate"])
            end = dt.date.fromisoformat(s["regularSeasonEndDate"])
            return start, end
    except Exception:
        pass
    # Fallback estimate (late March - early October)
    return dt.date(year, 3, 28), dt.date(year, 10, 1)
