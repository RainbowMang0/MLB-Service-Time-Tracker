"""Whether a player with three option years may have a fourth.

A fourth option year is allowed when the player has fewer than five full
professional seasons after the first contract, measured when the third
option year is used. A full season is one with at least 90 days on an
active professional roster. The ruleset does not encode that test, so the
listing rule lives here and does not change service time.

The signing date is the first screen. If fewer than five full seasons
could have fit between that date and the end of the third option year,
he qualifies. The year is not removed from anyone who already used four.

When five seasons could have fit and the transactions do not show whether
five of them were full, the result is unknown. The caller leaves him off
the out-of-options list instead of calling him out.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from il_stints import SeasonWindows

# Days on an active professional roster that make one full season, and how
# many of those seasons close off a fourth option year.
FULL_SEASON_DAYS = 90
FULL_SEASONS = 5

_SIGN_RE = re.compile(r"\bsigned\b", re.IGNORECASE)
_EXT_RE = re.compile(r"\bextension\b", re.IGNORECASE)
_COLLEGE_RE = re.compile(r"college workout", re.IGNORECASE)
_REHAB_RE = re.compile(r"rehab", re.IGNORECASE)
_IL_RE = re.compile(
    r"placed\b.{0,80}\bon the (?:\d+\s*-?\s*day )?(?:injured|disabled) list"
    r"|placed\b.{0,40}\bon the (?:paternity|bereavement|restricted) list"
    r"|placed\b.{0,40}\bon the reserve list",
    re.IGNORECASE,
)
_ACT_RE = re.compile(r"\b(?:activated|reinstated)\b", re.IGNORECASE)
_RELEASE_RE = re.compile(
    r"\breleased\b|elected free agency|declared free agency|\bretired\b",
    re.IGNORECASE,
)
_START_RE = re.compile(
    r"\boptioned\b|\brecalled\b|selected the contract|purchased the contract"
    r"|\boutrighted\b|\bassigned\b",
    re.IGNORECASE,
)

YES = "yes"
NO = "no"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class FourthOption:
    """`decision` is yes (a fourth year remains), no, or unknown."""

    decision: str
    signed: dt.date | None = None
    possible: int | None = None
    full: int | None = None


def _date(value) -> dt.date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def first_contract(rows: list[dict] | None) -> dt.date | None:
    """Earliest signing in the saved rows. An extension is not a first contract."""
    found: dt.date | None = None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        desc = row.get("description") or ""
        if not isinstance(desc, str) or not desc:
            continue
        if _COLLEGE_RE.search(desc) or _EXT_RE.search(desc):
            continue
        code = row.get("typeCode") or ""
        if code not in ("SGN", "SFA") and not _SIGN_RE.search(desc):
            continue
        day = _date(row.get("date"))
        if day is not None and (found is None or day < found):
            found = day
    return found


def _window(windows: SeasonWindows, year: int):
    return windows.for_club(year, None)


def possible_full_seasons(
    signed: dt.date, through: int, windows: SeasonWindows
) -> int:
    """Seasons that could contain a full active stretch after `signed`."""
    count = 0
    for year in range(signed.year, through + 1):
        bounds = _window(windows, year)
        if bounds is None:
            continue
        opening, closing = bounds
        start = max(signed, opening)
        if (closing - start).days + 1 >= FULL_SEASON_DAYS:
            count += 1
    return count


def _kind(desc: str) -> str:
    """start, stop, or skip. Rehab is not time on an active roster."""
    if _REHAB_RE.search(desc) or _COLLEGE_RE.search(desc):
        return "skip"
    if _IL_RE.search(desc) or _RELEASE_RE.search(desc):
        return "stop"
    if _ACT_RE.search(desc) or _START_RE.search(desc):
        return "start"
    return "skip"


def _events(rows: list[dict] | None) -> list[tuple[dt.date, str]]:
    found = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        desc = row.get("description") or ""
        day = _date(row.get("date"))
        if day is None or not isinstance(desc, str) or not desc:
            continue
        kind = _kind(desc)
        if kind != "skip":
            found.append((day, kind))
    found.sort(key=lambda item: (item[0], 0 if item[1] == "stop" else 1))
    return found


def _season_active_days(
    events: list[tuple[dt.date, str]],
    opening: dt.date,
    closing: dt.date,
    today: dt.date,
) -> tuple[int, bool]:
    """Active days inside one season, and whether a silent year was skipped.

    The boolean is True when this season sits in a multi-year hole with no
    roster row, so the days are not evidence.
    """
    prior = [item for item in events if item[0] < opening]
    inside = [item for item in events if opening <= item[0] <= closing]
    if not prior and not inside:
        return 0, True
    # Carry a state into the season only from the previous offseason or the
    # previous season. An older row is a hole, not a roster.
    carry = None
    if prior:
        last_day, last_kind = prior[-1]
        previous = opening - dt.timedelta(days=200)
        if last_day >= previous:
            carry = last_kind
        elif not inside:
            return 0, True
    active = carry == "start"
    cursor = opening
    days = 0
    stop_at = min(closing, today)

    def add(until: dt.date) -> None:
        nonlocal days, cursor
        end = min(until, stop_at)
        if active and end >= cursor:
            days += (end - cursor).days + 1
        cursor = max(cursor, end + dt.timedelta(days=1))

    for day, kind in inside:
        if day > today:
            break
        add(day - dt.timedelta(days=1))
        active = kind == "start"
        cursor = day
    if active and cursor <= stop_at:
        # The last row in the season left him on a roster. Count through
        # the end of that season. A later year's row does not erase it.
        add(stop_at)
    return days, False


def full_seasons(
    rows: list[dict] | None,
    signed: dt.date,
    through: int,
    today: dt.date,
    windows: SeasonWindows,
) -> tuple[int, int]:
    """Return (proven full seasons, unknown seasons) through `through`."""
    events = _events(rows)
    proven = 0
    unknown = 0
    for year in range(signed.year, through + 1):
        bounds = _window(windows, year)
        if bounds is None:
            unknown += 1
            continue
        opening, closing = bounds
        if closing < signed:
            continue
        days, hole = _season_active_days(events, opening, closing, today)
        if hole:
            unknown += 1
        elif days >= FULL_SEASON_DAYS:
            proven += 1
    return proven, unknown


def decide(
    rows: list[dict] | None,
    used_years: list[int] | tuple[int, ...],
    today: dt.date,
    windows: SeasonWindows,
) -> FourthOption:
    """Fourth-option decision for a player who has used exactly three years.

    Players who already used four are not passed here. This does not delete
    an option year.
    """
    if not used_years:
        return FourthOption(NO)
    through = max(used_years)
    signed = first_contract(rows)
    if signed is None or signed.year > through:
        return FourthOption(UNKNOWN, signed=signed)
    possible = possible_full_seasons(signed, through, windows)
    if possible < FULL_SEASONS:
        return FourthOption(YES, signed, possible, full=possible)
    proven, unknown = full_seasons(rows, signed, through, today, windows)
    # Five seasons already on the record is enough. A hole in some other
    # year does not give the year back.
    if proven >= FULL_SEASONS:
        return FourthOption(NO, signed, possible, proven)
    # Even if every unsettled season were full, he would still be under five.
    if proven + unknown < FULL_SEASONS:
        return FourthOption(YES, signed, possible, proven)
    return FourthOption(UNKNOWN, signed, possible, proven)
