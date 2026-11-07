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
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

import requests

BASE_URL = "https://statsapi.mlb.com/api/v1"
SPORT_ID_MLB = 1

# An honest User-Agent naming the site and a way to reach whoever runs it --
# statsapi.mlb.com is public and undocumented, and the polite thing an
# unofficial client can do is make itself identifiable rather than blend in
# with a browser's default string.
USER_AGENT = "bigleagueservicetime.com (contact@bigleagueservicetime.com)"

_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
)

REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2

# The pause between requests, read fresh from the environment on every call
# rather than frozen at import time -- so a workflow step, a test, or a
# one-off shell command can tune it (`MLB_FETCH_PAUSE_SECONDS=0 python3 ...`
# for a local dry run) without editing this file. Defaults to roughly one
# second per the owner's instruction to be a polite, identifiable client of
# a public, undocumented, unpaid API; see the daily-update workflow for the
# request-count estimate that this default was chosen against.
DEFAULT_POLITE_DELAY_SECONDS = 1.0


def polite_delay_seconds() -> float:
    raw = os.environ.get("MLB_FETCH_PAUSE_SECONDS")
    if raw is None or raw.strip() == "":
        return DEFAULT_POLITE_DELAY_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_POLITE_DELAY_SECONDS


def polite_pause() -> None:
    """Sleep the configured pause. One call site, so every fetcher honors the
    same knob -- see MLB_FETCH_PAUSE_SECONDS above."""
    time.sleep(polite_delay_seconds())


def _get(path: str, params: dict[str, Any] | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            polite_pause()
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


def _team_id_of(split: dict) -> int | None:
    """The club id on a yearByYear split, or None when the split has no team.

    The feed's combined season row (a mid-season trade) is the one with no
    team. A bool is rejected because bool is a subclass of int.
    """
    team = split.get("team") or {}
    if not isinstance(team, dict):
        return None
    team_id = team.get("id")
    if isinstance(team_id, bool) or not isinstance(team_id, int):
        return None
    return team_id


def _num_teams_of(split: dict) -> int | None:
    """split.numTeams as an int, or None when the feed did not send one."""
    raw = split.get("numTeams")
    if isinstance(raw, bool) or raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _is_combined_total(row: dict) -> bool:
    """The feed's own season total: no club, and at least two teams.

    A no-team row with numTeams missing or below 2 is not that total and is
    not kept. Club splits are never this, even when they also carry numTeams,
    because they name a team.
    """
    num = row.get("numTeams")
    return (
        row.get("team_id") is None
        and isinstance(num, int)
        and not isinstance(num, bool)
        and num >= 2
    )


def _stat_line(split: dict, keys: tuple) -> dict | None:
    """One whitelist row from a split. Empty (no kept key) returns None.

    Zero is a real value — a pitcher's batting line is 0 at-bats — so only
    None and "" are absent. team_id is None on the combined row.
    """
    stat = split.get("stat") or {}
    if not isinstance(stat, dict) or not stat:
        return None
    row = {k: stat[k] for k in keys if stat.get(k) not in (None, "")}
    if not row:
        return None
    row["team_id"] = _team_id_of(split)
    num_teams = _num_teams_of(split)
    if num_teams is not None:
        row["numTeams"] = num_teams
    return row


def _finish_season_group(rows: list[dict]) -> dict | None:
    """Collapse one group-year's splits into a season line plus club splits.

    The combined no-team row IS the season total. Its rate stats (avg, era,
    ops) are the feed's, so nothing is summed or averaged here. Per-club
    rows are kept, in feed order, under ``splits`` for a later display.
    A season with one club and no combined row stays a flat line, which is
    what every single-club season already was.
    """
    clubs = [r for r in rows if r.get("team_id") is not None]
    combined = [r for r in rows if _is_combined_total(r)]
    if combined:
        total = dict(combined[-1])
        if clubs:
            total["splits"] = [
                {k: v for k, v in club.items() if k != "numTeams"} for club in clubs
            ]
        return total
    if len(clubs) == 1:
        return {k: v for k, v in clubs[0].items() if k != "numTeams"}
    if clubs:
        return {
            "splits": [
                {k: v for k, v in club.items() if k != "numTeams"} for club in clubs
            ]
        }
    return None


def season_stats_from_bio(bio: dict) -> dict[int, dict]:
    """
    {season year -> {"hitting": {...}, "pitching": {...}}} from a hydrated bio.

    The pipeline has ALWAYS fetched this. BIO_SEASON_TEAMS_HYDRATE asks for
    yearByYear hitting and pitching on the /people call made for every player,
    and season_teams_from_bio() reads exactly one field out of each split --
    the team id -- discarding the entire statline unread. This reads it.

    A mid-season trade is several splits for one year: one per club, then a
    combined row with no team and ``numTeams`` >= 2. Keeping "the later"
    split made that combined row the whole season, and the major-league
    filter then dropped it for having no team id — the year vanished. The
    combined row is the season total (rate stats included, unsummed). The
    club rows are kept on ``splits``.

    Team ids are NOT filtered here, for the same reason season_teams_from_bio()
    does not filter them: this module has no business deciding which ids are
    major league ones. select_major_league_season_stats() does that, and it
    keeps a no-team combined row when numTeams >= 2.
    """
    pending: dict[int, dict[str, list]] = {}
    for group in bio.get("stats") or []:
        if not isinstance(group, dict):
            continue
        name = ((group.get("group") or {}).get("displayName") or "").lower()
        keys = _STAT_KEYS.get(name)
        if not keys:
            continue
        for split in group.get("splits") or []:
            if not isinstance(split, dict):
                continue
            season = split.get("season")
            if season is None:
                continue
            try:
                year = int(season)
            except (TypeError, ValueError):
                continue
            row = _stat_line(split, keys)
            if row is None:
                continue
            pending.setdefault(year, {}).setdefault(name, []).append(row)

    out: dict[int, dict] = {}
    for year, groups in pending.items():
        built = {}
        for name, rows in groups.items():
            finished = _finish_season_group(rows)
            if finished:
                built[name] = finished
        if built:
            out[year] = built
    return out


def _publish_total(row: dict) -> dict:
    """Season-total fields only. team_id is None on a combined row; omit it."""
    return {
        k: v
        for k, v in row.items()
        if k not in ("team_id", "splits") and v is not None
    }


def _publish_club(row: dict) -> dict:
    """One club's line. numTeams belongs to the season total, not the club."""
    return {
        k: v
        for k, v in row.items()
        if k not in ("splits", "numTeams") and v is not None
    }


def _publish_clubs(clubs: list[dict]) -> dict | None:
    if not clubs:
        return None
    if len(clubs) == 1:
        return _publish_club(clubs[0])
    return {"splits": [_publish_club(club) for club in clubs]}


def _store_major_league_group(row: dict, mlb_ids: set[int]) -> dict | None:
    """Apply the 30-club filter to one group-year.

    A combined total is kept only when every club split beside it is a
    major-league club. A total that also folds in a minor-league line is not
    a major-league season, so the major-league club lines are kept on their
    own and the mix is not published as the total. A no-team row with
    numTeams >= 2 and no non-major club beside it is never dropped.
    """
    splits = row.get("splits")
    if not splits:
        if _is_combined_total(row):
            return _publish_total(row)
        if row.get("team_id") in mlb_ids:
            return _publish_club(row)
        return None

    mlb_splits = [s for s in splits if s.get("team_id") in mlb_ids]
    dropped_a_club = len(mlb_splits) != len(list(splits))
    if _is_combined_total(row) and not dropped_a_club:
        published = _publish_total(row)
        if mlb_splits:
            published["splits"] = [_publish_club(s) for s in mlb_splits]
        return published
    return _publish_clubs(mlb_splits)


def select_major_league_season_stats(
    by_year: dict[int, dict], mlb_ids: set[int]
) -> dict[int, dict]:
    """The season lines worth storing, given the 30 major-league club ids.

    season_stats_from_bio() keeps every split, including a minor-league team
    id and the no-team combined row. This drops the minor-league clubs and
    keeps the combined row (numTeams >= 2, no team) as the season total
    unless that total mixes in a club that is not major league.
    """
    out: dict[int, dict] = {}
    for year, groups in by_year.items():
        if not isinstance(groups, dict):
            continue
        kept: dict[str, dict] = {}
        for name, row in groups.items():
            if not isinstance(row, dict):
                continue
            stored = _store_major_league_group(row, mlb_ids)
            if stored:
                kept[name] = stored
        if kept:
            out[year] = kept
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


def appearance_dates(player_id: int, season: int, group: str) -> list[dt.date]:
    """Dates this player appeared in a regular-season MLB game.

    The game log is the stat feed's own list of days he was on the active
    roster. Used only for a season whose transaction walk credited almost
    no days despite a substantial number of games -- a missing recall. One
    request per group (hitting or pitching).
    """
    data = _get(
        f"/people/{player_id}/stats",
        {
            "stats": "gameLog",
            "group": group,
            "season": season,
            "sportId": SPORT_ID_MLB,
            "gameType": "R",
        },
    )
    dates: list[dt.date] = []
    for block in data.get("stats") or []:
        for split in block.get("splits") or []:
            if split.get("gameType") not in (None, "R"):
                continue
            raw = split.get("date")
            if not raw:
                continue
            try:
                dates.append(dt.date.fromisoformat(str(raw)[:10]))
            except ValueError:
                continue
    return dates


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
    end: dt.date  # the last REGULARLY SCHEDULED game (tiebreakers excluded)
    source: str  # "schedule" | "seasons" | "estimate"
    # The CBA-uniform season start (rules v7): CBA Art. XXI(A)(1) credits service
    # "uniformly to all Players and all Clubs ... notwithstanding differences in
    # a particular Club's schedule", so a staggered DOMESTIC opener (DET 04-08
    # vs the league's 04-07 in 2022) must not cost a player a day. This is the
    # modal opener among clubs that opened within 187 days of `end` -- the date
    # most of THOSE clubs share. A club that opened EARLIER than that, whether
    # overseas (Art. V(A): Tokyo/Seoul/Mexico, outside the window) or an
    # early-domestic outlier inside it (2026's Yankees/Giants Oracle Park game),
    # keeps its own earlier date, which is why club_openers is kept too. Equals
    # domestic_opener whenever every club falls inside the window, which is the
    # common case.
    uniform_opener: dt.date = None  # type: ignore[assignment]
    # club id -> that club's last PLAYED R game INCLUDING a tiebreaker it played.
    # A tiebreaker ("game 163") is not a regularly scheduled game, so it is
    # excluded from the league `end` above; but a club that actually played one
    # earned that extra day for its own players (CBA reading confirmed against
    # the reference: Cole/Arenado/Soto, whose clubs did NOT play a tiebreaker,
    # lose the phantom day, while a club that did keeps it). Empty for a
    # degraded/estimated window, where per-club data is unavailable.
    club_ends: dict[int, dt.date] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.uniform_opener is None:
            self.uniform_opener = self.domestic_opener
        if self.club_ends is None:
            self.club_ends = {}


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

    ⚠️ LIVE BUG, caught 2026-10-29 by running this against the real feed
    instead of a synthetic one: `get_season_window(2023)` still returned
    2023-10-02 after the makeup-game fix above, because the suspended
    Marlins@Mets game from 2023-09-28 (rain, "Completed Early") is RELISTED by
    the API under the date it finished resuming -- 2023-10-02 -- with
    `resumedFrom` pointing back to when it actually started. That relisting
    carries the SAME gamePk and the SAME result as the original row; it is not
    a second game. Keying the played date off the surrounding date BUCKET (as
    the first version of this function did) reads that bucket as a real game
    on 10-02 and pulls the season end forward a day past the last game anyone
    actually played, 2023-10-01.

    The date of record for a game is its `officialDate` field, not the bucket
    it is filed under -- the two normally agree, but a resumed game's
    duplicate listing is exactly the case where they do not (the duplicate's
    own officialDate is still 2023-09-28). So every game's day is read from
    `officialDate` now, falling back to the bucket date only if the field is
    missing, and a row carrying `resumedFrom` is skipped entirely -- it
    contributes nothing that the original listing did not already contribute.

    This is NOT the same shape as a postponed-and-rescheduled game, which must
    still count. A rainout carries `rescheduledFrom` (a different field) and
    produces two REAL rows: a stub with `detailedState: "Postponed"` on the
    original date (already excluded by _NOT_PLAYED_STATES below) and a genuine
    `Final` game on the makeup date, which is the actual game and must count
    normally. Skipping on `resumedFrom` specifically, rather than on "this
    gamePk already appeared", is what tells the two apart: the postponed stub
    and the makeup Final share a gamePk but neither carries `resumedFrom`.
    """
    openers: dict[int, list[dt.date]] = {}
    club_last: dict[int, list[dt.date]] = {}
    end_candidates: list[dt.date] = []
    # Defense in depth against the resumed-game shape above, in case a future
    # payload omits `resumedFrom` on the duplicate row: a gamePk that has
    # already contributed one end candidate cannot contribute a second. This
    # must key on having actually CONTRIBUTED (i.e. after the not-played-state
    # check below), not merely "seen" -- a postponed stub is seen first and
    # excluded by status, and gating on bare presence would wrongly suppress
    # the real makeup game that follows it under the same gamePk.
    counted_end_game_pks: set[int] = set()
    for block in payload.get("dates") or []:
        bucket_date = block.get("date")
        for game in block.get("games") or []:
            # Regular season only. We ask the API for gameType=R, but double
            # check: a spring game weeks earlier would otherwise pull a club's
            # opener back into February.
            if game.get("gameType") != "R":
                continue
            if game.get("resumedFrom"):
                continue

            # officialDate is the date of record for the game, used uniformly
            # for both a club's opener and the season end -- not the bucket,
            # which a resumed game's (now-skipped) duplicate listing shows can
            # disagree with it.
            official = game.get("officialDate") or bucket_date
            try:
                day = dt.date.fromisoformat(official)
            except (TypeError, ValueError):
                continue

            teams = game.get("teams") or {}
            for side in ("home", "away"):
                cid = ((teams.get(side) or {}).get("team") or {}).get("id")
                if isinstance(cid, int):
                    openers.setdefault(cid, []).append(day)

            state = (game.get("status") or {}).get("detailedState") or ""
            if state in _NOT_PLAYED_STATES:
                continue
            game_pk = game.get("gamePk")
            if game_pk is not None:
                if game_pk in counted_end_game_pks:
                    continue
                counted_end_game_pks.add(game_pk)
            # This game was actually PLAYED. Record it as each club's own
            # last-played candidate (club_ends), including a tiebreaker -- a club
            # that played "game 163" earned that day for its own players.
            for side in ("home", "away"):
                cid = ((teams.get(side) or {}).get("team") or {}).get("id")
                if isinstance(cid, int):
                    club_last.setdefault(cid, []).append(day)
            # A tiebreaker ("game 163") is NOT a regularly scheduled game (CBA
            # Art. XXI(A)(1): service runs through the last regularly scheduled
            # game "uniformly to all ... Clubs"), so it does NOT set the
            # league-wide season end. The 2013 TEX/TB 09-30 and 2018 MIL/CHC and
            # COL/LAD 10-01 tiebreakers used to drag the end a day past the last
            # real game, crediting a phantom day to every club.
            if game.get("tiebreaker") == "Y":
                continue
            end_candidates.append(day)
    if not openers or not end_candidates:
        return None
    club_openers = {cid: min(days) for cid, days in openers.items()}
    club_ends = {cid: max(days) for cid, days in club_last.items()}
    # The modal opener is the standard league Opening Day; the overseas and
    # early-domestic clubs are the minority outliers. It is only ever used as a
    # default for a player whose club we could not identify.
    domestic = collections.Counter(club_openers.values()).most_common(1)[0][0]
    end = max(end_candidates)
    # CBA-uniform start: the MODE of the openers within 187 days of the end. A
    # club that opened OVERSEAS earlier than that (more than 186 days before the
    # end) is beyond the window and excluded, keeping its own earlier date
    # (Art. V(A)); every other club shares this one Opening Day.
    #
    # It must be the mode, not the earliest, of that window. "Earliest opener in
    # the window" was tried and reverted: it correctly excluded a true overseas
    # opener (outside the window), but an early-DOMESTIC outlier that is INSIDE
    # it (2026's Yankees/Giants Oracle Park game, 03-25) was wrongly adopted as
    # the shared Opening Day every other club's player snapped down to, even
    # though those clubs did not open until 03-26/03-27 and were never on an
    # Opening Day roster that early.
    earliest_in_window = end - dt.timedelta(days=186)
    within = [d for d in club_openers.values() if d >= earliest_in_window]
    uniform = collections.Counter(within).most_common(1)[0][0] if within else domestic
    return SeasonSchedule(
        year=year,
        club_openers=club_openers,
        domestic_opener=domestic,
        end=end,
        source="schedule",
        uniform_opener=uniform,
        club_ends=club_ends,
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


def get_uniform_season_start(year: int) -> dt.date:
    """The CBA-uniform Opening Day for the year (rules v7).

    The single season start that Art. XXI(A)(1) applies to every club, so a
    staggered DOMESTIC opener does not cost a player a day. A club that opened
    overseas earlier keeps its own date -- callers take min(own opener, this).
    Falls back to the domestic opener for a degraded/estimated window.
    """
    get_season_window(year)  # populates _SEASON_SCHEDULE[year]
    sched = _SEASON_SCHEDULE.get(year)
    return sched.uniform_opener if sched is not None else get_season_window(year)[0]


def get_club_season_end(year: int, club_id: int | None) -> dt.date:
    """That club's own last regular-season day for the year (rules v7).

    Equals the league season end for almost every club; it is LATER only for a
    club that actually played a tiebreaker ("game 163"), which earns that club's
    own players the extra day even though the tiebreaker does not set the
    league-wide end. Never EARLIER than the league end -- a club out of
    contention still rostered players through the last scheduled day, and
    service time is roster time, not game time -- so this is max(league end,
    the club's own last played game). Falls back to the league end when the club
    is unknown or the window was degraded/estimated.
    """
    _, end = get_season_window(year)  # populates _SEASON_SCHEDULE[year]
    sched = _SEASON_SCHEDULE.get(year)
    if sched is not None and club_id is not None and club_id in sched.club_ends:
        return max(end, sched.club_ends[club_id])
    return end


def estimated_season_windows() -> set[int]:
    """Seasons this process had to estimate rather than fetch. Empty is healthy."""
    return set(_ESTIMATED_WINDOWS)


def degraded_season_windows() -> set[int]:
    """Seasons that fell back to /seasons, so the schedule fixes did not apply."""
    return set(_DEGRADED_WINDOWS)
