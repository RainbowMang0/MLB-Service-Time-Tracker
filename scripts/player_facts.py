"""Facts a player page states about club and whether the career is over.

The stored team on a player who is off the 40-man is whatever club a roster
feed last attached to him. It is not updated after he leaves, so it goes
stale (Aaron Bummer is still stored as a White Sox). Season rows list every
club that season, and that list is not in stint order, so its last id is
not the last club either.

The last club is the major league club on his latest roster stint: the
latest transaction that put him on a major league roster, or took him off
one. A minor-league signing names the parent club and is not a stint.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re

import service_time
from mlb_clubs import MLB_CLUB_IDS, club_name, name_for_record

ROOT = pathlib.Path(__file__).resolve().parents[1]
_RAW_DIR = ROOT / "data" / "raw_transactions"
_CACHE_DIR = ROOT / "data" / "cache" / "transactions"
_ACTIVE_PATH = ROOT / "data" / "player_active.json"
_ACTIVE: dict | None = None

# A retirement names whatever club the feed still has on the person, which
# is often not the last roster. A number change and a bare status note do
# the same. None of those is a stint.
_STATUS_ROW_RE = re.compile(
    r"\bretired\b|changed number|roster status changed",
    re.IGNORECASE,
)
_RETIRED_RE = re.compile(r"\bretired\b", re.IGNORECASE)


def _as_id(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _row_id(row: dict, flat: str, nested: str) -> int | None:
    found = _as_id(row.get(flat))
    if found is not None:
        return found
    obj = row.get(nested)
    if isinstance(obj, dict):
        return _as_id(obj.get("id"))
    return None


def _mlb_club_id(row: dict) -> int | None:
    """The major league club named on a roster move, destination first."""
    for flat, nested in (
        ("to_team_id", "toTeam"),
        ("from_team_id", "fromTeam"),
        ("team_id", "team"),
    ):
        club = _row_id(row, flat, nested)
        if club in MLB_CLUB_IDS:
            return club
    return None


def _is_status_row(row: dict) -> bool:
    code = str(row.get("typeCode") or "").upper()
    if code in {"RET", "NUM"}:
        return True
    return bool(_STATUS_ROW_RE.search(row.get("description") or ""))


def has_retirement(transactions: list | None) -> bool:
    for row in transactions or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("typeCode") or "").upper() == "RET":
            return True
        if _RETIRED_RE.search(row.get("description") or ""):
            return True
    return False


def active_flag(player: dict) -> bool | None:
    """MLB's active flag when we have it. A bool on the player wins."""
    global _ACTIVE
    if isinstance(player.get("active"), bool):
        return player["active"]
    if _ACTIVE is None:
        if _ACTIVE_PATH.exists():
            try:
                loaded = json.loads(_ACTIVE_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                loaded = {}
            _ACTIVE = loaded if isinstance(loaded, dict) else {}
        else:
            _ACTIVE = {}
    pid = player.get("id")
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    value = _ACTIVE.get(str(pid))
    return value if isinstance(value, bool) else None


def last_mlb_club_id(transactions: list | None) -> int | None:
    """Club id of the latest major league stint, or None when no move says.

    Walks the feed in date order. A move onto a major league roster, a
    trade, or a move off one records that club. A minor-league contract
    does not, even when the parent club's id is on the row.
    """
    rows = [row for row in (transactions or []) if isinstance(row, dict)]
    rows.sort(key=lambda row: (str(row.get("date") or row.get("effectiveDate") or ""), _as_id(row.get("id")) or 0))
    by_date: dict[str, list[str]] = {}
    for row in rows:
        day = str(row.get("date") or row.get("effectiveDate") or "")
        by_date.setdefault(day, []).append(row.get("description") or "")

    last = None
    for row in rows:
        desc = row.get("description") or ""
        day = str(row.get("date") or row.get("effectiveDate") or "")
        others = [text for text in by_date.get(day, []) if text != desc]
        if _is_status_row(row):
            continue
        club = _mlb_club_id(row)
        if club is None:
            continue
        opened = service_time.row_opens_roster(desc, others) or service_time.is_trade(desc)
        closed = service_time.is_active_stop(desc) or service_time.is_free_agency_election(desc)
        if opened or closed:
            last = club
    return last


def load_transaction_rows(player_id) -> list:
    """Full saved feed for one player, or the roster cache when that is all we have."""
    try:
        player_id = int(player_id)
    except (TypeError, ValueError):
        return []
    raw_path = _RAW_DIR / f"{player_id}.json"
    if raw_path.exists():
        try:
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("transactions"), list):
            return payload["transactions"]
        if isinstance(payload, list):
            return payload
    cache_path = _CACHE_DIR / f"{player_id}.json"
    if not cache_path.exists():
        return []
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return payload if isinstance(payload, list) else []


def _single_season_club(player: dict) -> int | None:
    """When the newest season names exactly one club, that club is the stint."""
    for season in reversed(player.get("seasons") or []):
        clubs = [
            club for club in (season.get("t") or [])
            if isinstance(club, int) and not isinstance(club, bool)
        ]
        if len(clubs) == 1:
            return clubs[0]
        if len(clubs) > 1:
            return None
    return None


def display_club(player: dict, team_names: dict | None = None, transactions: list | None = None) -> str:
    """Club name to show. A 40-man player uses that roster club.

    Anyone else uses the latest major league stint. The stored team is not
    a fallback. With no stint to read, the page names no club.
    """
    if player.get("on_40_man"):
        return name_for_record(player.get("team_id"), player.get("team") or "")
    rows = transactions
    if rows is None and player.get("id") is not None:
        rows = load_transaction_rows(player.get("id"))
    club_id = last_mlb_club_id(rows)
    if club_id is None:
        club_id = _single_season_club(player)
    if club_id is None:
        return ""
    return club_name(int(club_id), team_names)


def days_in_season(player: dict, year: int) -> int:
    total = 0
    for season in player.get("seasons") or []:
        try:
            season_year = int(season.get("y"))
        except (TypeError, ValueError):
            continue
        if season_year != year:
            continue
        try:
            total += int(season.get("d") or 0)
        except (TypeError, ValueError):
            continue
    return total


def career_is_closed(
    player: dict,
    season_year: int | None = None,
    transactions: list | None = None,
) -> bool:
    """Whether the page should say the career closed.

    Closed when MLB lists him inactive, or the feed has a retirement.
    A player MLB still lists as active does not get that sentence, whether
    or not he has days in the reporting season. ``season_year`` is unused
    for the decision and kept so callers can pass the page's year.
    """
    del season_year
    if player.get("on_40_man"):
        return False
    rows = transactions
    if rows is None and player.get("id") is not None:
        rows = load_transaction_rows(player.get("id"))
    if has_retirement(rows):
        return True
    active = active_flag(player)
    if active is True:
        return False
    if active is False:
        return True
    return False


def reporting_year(generated_at: str | None) -> int:
    text = generated_at or ""
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return dt.date.today().year
