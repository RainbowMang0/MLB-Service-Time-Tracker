"""
fetch_mlb_data.py
------------------
Thin client for the public (unofficial, undocumented but widely used)
MLB Stats API at https://statsapi.mlb.com. No API key is required.

This module only does HTTP + light shaping -- all service-time math lives
in service_time.py.
"""

from __future__ import annotations

import collections
import datetime as dt
import functools
import sys
import time
from dataclasses import dataclass
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


# Seasons whose window came from the HARDCODED estimate (a total outage of both
# endpoints). Read by callers that need to know whether a run is trustworthy --
# see estimated_season_windows(). An estimated window blocks publishing.
_ESTIMATED_WINDOWS: set[int] = set()

# Seasons that fell back from /schedule to /seasons. The window is real, but it
# is the LEAGUE window -- the start is the (overseas) opener applied to every
# club, and the end can include a cancelled makeup game. The per-club-opener
# and real-season-end fixes do not apply to a degraded season, so it reads the
# way it did before those fixes shipped. Loud, but not fatal: a transient
# /schedule hiccup should not halt the daily site.
_DEGRADED_WINDOWS: set[int] = set()


@dataclass
class SeasonSchedule:
    """What one regular season looked like, derived from the /schedule feed.

    `club_openers` is the heart of it: service time is credited from a club's
    OWN first regular-season game, and those differ. In 2024 the Dodgers and
    Padres opened in Seoul on 03-20 while everyone else opened 03-28; in 2026
    the Yankees and Giants opened 03-25 at Oracle Park while 22 clubs opened
    03-26 and six opened 03-27. The /seasons endpoint reports a single
    `regularSeasonStartDate` -- the earliest game anywhere, i.e. the overseas
    opener -- and applying it to all 30 clubs credited everyone else a week of
    phantom days (the ~8-days-high error the Baseball Reference audit found).
    """

    year: int
    club_openers: dict[int, dt.date]  # club id -> that club's first R game
    domestic_opener: dt.date  # the modal opener; the default for an unknown club
    end: dt.date  # the last regular-season game actually on the calendar
    source: str  # "schedule" | "seasons" | "estimate"


# year -> SeasonSchedule, populated as a side effect of get_season_window() so
# the per-club openers are available to callers that know the player's club.
_SEASON_SCHEDULE: dict[int, SeasonSchedule] = {}

# A game with one of these states did NOT happen and is not on the calendar as
# a real game. EXCLUDED from the season-end date -- this is the 2023 fix: the
# /seasons end was 2023-10-02 because of a makeup game scheduled then and later
# cancelled, while the last game actually played was 2023-10-01. NOT excluded
# from a club's opener: a postponed Opening Day still had a roster that day, so
# a player on it was still accruing (service time is roster time, not game
# time). A made-up game simply appears again on its new date and is counted
# there, which is why the real 2024-09-30 Mets-Braves makeup doubleheader keeps
# 2024 ending on 2024-09-30 -- no blanket rule against makeups is needed.
_NOT_PLAYED_STATES = frozenset({"Cancelled", "Canceled", "Postponed"})


def _parse_season_schedule(year: int, payload: dict) -> SeasonSchedule | None:
    """Build a SeasonSchedule from a /schedule payload, or None if it is empty.

    Defensive about the shape because this is parsed from a live feed the
    sandbox cannot reach: a missing field yields a thinner result, never an
    exception.
    """
    openers: dict[int, list[dt.date]] = {}
    end_candidates: list[dt.date] = []
    for block in payload.get("dates") or []:
        try:
            day = dt.date.fromisoformat(block.get("date"))
        except (TypeError, ValueError):
            continue
        for game in block.get("games") or []:
            # Regular season only. We ask the API for gameType=R, but double
            # check: a spring game weeks earlier would otherwise pull a club's
            # opener back into February.
            if game.get("gameType") != "R":
                continue
            teams = game.get("teams") or {}
            for side in ("home", "away"):
                cid = ((teams.get(side) or {}).get("team") or {}).get("id")
                if isinstance(cid, int):
                    openers.setdefault(cid, []).append(day)
            state = (game.get("status") or {}).get("detailedState") or ""
            if state not in _NOT_PLAYED_STATES:
                end_candidates.append(day)
    if not openers or not end_candidates:
        return None
    club_openers = {cid: min(days) for cid, days in openers.items()}
    # The modal opener is the standard league Opening Day; the overseas and
    # early-domestic clubs are the minority outliers. It is only ever used as a
    # default for a player whose club we could not identify.
    domestic = collections.Counter(club_openers.values()).most_common(1)[0][0]
    return SeasonSchedule(
        year=year,
        club_openers=club_openers,
        domestic_opener=domestic,
        end=max(end_candidates),
        source="schedule",
    )


def _fetch_season_schedule(year: int) -> SeasonSchedule:
    """The fallback chain behind get_season_window(). See it for the contract."""
    # 1. /schedule -- per-club openers and the real last-played end date. This
    #    is what the per-club-opener (fix) and the 2023-end (fix) both need.
    try:
        payload = _get(
            "/schedule", {"sportId": SPORT_ID_MLB, "season": year, "gameType": "R"}
        )
        sched = _parse_season_schedule(year, payload)
        if sched is not None:
            return sched
    except Exception as exc:  # pragma: no cover - network path
        print(f"  !! /schedule failed for {year}: {exc}", file=sys.stderr)

    # 2. /seasons -- a real window, but the LEAGUE one: the start is the
    #    (overseas) opener applied to every club, and the end can include a
    #    cancelled makeup. No per-club data, so the two schedule fixes do not
    #    apply for this season. Degraded, not estimated -- the numbers are the
    #    pre-fix numbers, which are wrong by at most a week and only for a
    #    season whose /schedule call happened to fail.
    try:
        data = _get("/seasons", {"sportId": SPORT_ID_MLB, "season": year})
        seasons = data.get("seasons", [])
        if seasons:
            s = seasons[0]
            start = dt.date.fromisoformat(s["regularSeasonStartDate"])
            end = dt.date.fromisoformat(s["regularSeasonEndDate"])
            _DEGRADED_WINDOWS.add(year)
            print(
                f"  !! /schedule unavailable for {year}; fell back to /seasons. "
                "Per-club openers and the real season-end are NOT applied for "
                "this season -- it reads the way it did before those fixes.",
                file=sys.stderr,
            )
            return SeasonSchedule(year, {}, start, end, "seasons")
    except Exception as exc:  # pragma: no cover - network path
        print(f"  !! /seasons failed for {year}: {exc}", file=sys.stderr)

    # 3. Hardcoded estimate -- both endpoints are down. This is an OUTAGE, not a
    #    far-future season: the daily job only asks for seasons that happened.
    #    CLAUDE.md records this exact estimate (Mar 28 - Oct 1) putting Jose
    #    Ramirez 8 days off, so it blocks publishing (see check_run_is_sane).
    _ESTIMATED_WINDOWS.add(year)
    print(
        f"  !! ESTIMATING the {year} season window as Mar 28 - Oct 1. Every "
        f"figure that depends on {year} is computed against a guess, not the "
        "real schedule.",
        file=sys.stderr,
    )
    return SeasonSchedule(year, {}, dt.date(year, 3, 28), dt.date(year, 10, 1), "estimate")


@functools.lru_cache(maxsize=None)
def get_season_window(year: int) -> tuple[dt.date, dt.date]:
    """
    Returns (default season start, regular season end) for a given year.

    The START is the DOMESTIC opener -- the modal club opener, the day most
    clubs begin -- which is the right default for a caller that does not know
    the player's club. For the per-club date (the Seoul/Tokyo openers, the
    2026 Oracle Park opener), use get_club_season_start(). The END is the last
    regular-season game actually played, which is NOT what /seasons reports in
    a year with a cancelled makeup game (2023: /seasons said 10-02, the last
    game played was 10-01).

    Both come from the /schedule feed now, not /seasons -- see
    _fetch_season_schedule() for the fallback chain and SeasonSchedule for why
    per-club openers matter.

    MEMOISED, and that is not a micro-optimisation. The pipeline asks for a
    window once per player per season of his career, so a daily run over
    ~1,370 rostered players made 7,179 calls to fetch the same 22 distinct
    answers -- 7,157 of them redundant. A season's dates do not move within a
    run, so one call each is all that is correct. The per-season schedule is
    stashed in _SEASON_SCHEDULE as a side effect so the per-club openers are
    fetched the same once-per-season way.
    """
    sched = _fetch_season_schedule(year)
    _SEASON_SCHEDULE[year] = sched
    return sched.domestic_opener, sched.end


def get_club_season_start(year: int, club_id: int | None) -> dt.date:
    """That club's own first regular-season game date for the year.

    Falls back to the domestic opener when the club is unknown, had no games
    in the feed, or the season's schedule could only be fetched from /seasons
    (which has no per-club data). Ensures the season is fetched and memoised
    first, so this costs no extra API call beyond the one get_season_window()
    already makes.
    """
    start, _ = get_season_window(year)  # populates _SEASON_SCHEDULE[year]
    sched = _SEASON_SCHEDULE.get(year)
    if sched is not None and club_id is not None and club_id in sched.club_openers:
        return sched.club_openers[club_id]
    return start


def estimated_season_windows() -> set[int]:
    """Seasons this process had to estimate rather than fetch. Empty is healthy."""
    return set(_ESTIMATED_WINDOWS)


def degraded_season_windows() -> set[int]:
    """Seasons that fell back to /seasons, so the schedule fixes did not apply."""
    return set(_DEGRADED_WINDOWS)
