"""2020 proration length.

The first game was 23 July 2020, and only the Yankees, Nationals, Dodgers,
and Giants played it. The other 26 clubs opened on 24 July. Published service
time scales a 2020 season on the opening club's length. The rulebook does too.
The days that count are unchanged; only the length in the scale changes.
"""

from __future__ import annotations

import datetime as dt

import cba
from mlb_clubs import MLB_CLUB_IDS

# Club ids that played on 23 July 2020: Dodgers, Nationals, Giants, Yankees.
EARLY_CLUBS = frozenset({119, 120, 137, 147})
FIRST_GAME = dt.date(2020, 7, 23)
GENERAL_OPENER = dt.date(2020, 7, 24)
SEASON_END = dt.date(2020, 9, 27)


def opening_club(dated_ids, season_clubs: list[int] | None = None) -> int | None:
    """The club that sets a player's 2020 proration length.

    `dated_ids` is `(date, ids)` with `ids` from `rulebook.row_ids`. The
    origin of his earliest 2020 major-league transaction is the opening club.
    With none, the clubs on the published season are used, and the later
    opener wins when they disagree. Otherwise the club he was already with.
    """
    rows = []
    for day, ids in dated_ids:
        present = {i for i in (ids["team"], ids["from"], ids["to"]) if isinstance(i, int)}
        if present and not (present & MLB_CLUB_IDS):
            continue
        club = next((ids[key] for key in ("from", "team", "to") if ids[key] in MLB_CLUB_IDS), None)
        if club is not None:
            rows.append((day, club))
    rows.sort()
    opening = None
    carried = None
    for day, club in rows:
        if day.year < 2020:
            carried = club
        elif day.year == 2020 and opening is None:
            opening = club
    if opening is not None:
        return opening
    clubs = [club for club in (season_clubs or []) if isinstance(club, int)]
    if clubs:
        late = [club for club in clubs if club not in EARLY_CLUBS]
        return late[0] if late else clubs[0]
    return carried


def proration_start(year: int, accrual_start: dt.date, club_id: int | None) -> dt.date:
    """First day of the length used to scale a shortened season.

    Other years use the accrual window. 2020 uses 23 July for the four clubs
    that played that day, and 24 July for every other club.
    """
    if year != 2020:
        return accrual_start
    if club_id in EARLY_CLUBS:
        return FIRST_GAME
    return GENERAL_OPENER


def _spans() -> tuple[int, int]:
    early = (SEASON_END - FIRST_GAME).days + 1
    general = (SEASON_END - GENERAL_OPENER).days + 1
    return early, general


def _scaled(raw: int, span: int, normal: int, cap: int) -> int:
    if span <= 0 or span >= normal:
        return min(cap, raw)
    return min(cap, int(round(raw * normal / span)))


def site_scale_action(
    year: int,
    raw: int,
    rule_days: int,
    published_days: int,
    earlier_days: int | None,
) -> str | None:
    """How a 2020 scale change treats a total the site already shows.

    "keep" — the site has the other length of these same raw days, and the
    rulebook is higher. Leave the published total.
    "hold" — the new length has reached an older snapshot but is still under
    the total on the site. Keep that total and the hold.
    None — the ordinary publish rules apply.
    """
    if year != 2020 or raw <= 0 or not published_days or rule_days == published_days:
        return None
    rules = cba.default()
    normal = int(rules.require("service_time.shortened_seasons.2020.scale_to_span_days"))
    cap = int(rules.require("service_time.max_days_creditable_per_season"))
    early, general = _spans()
    old = _scaled(raw, early, normal, cap)
    new = _scaled(raw, general, normal, cap)
    if rule_days in {old, new} and published_days in {old, new} and rule_days > published_days:
        return "keep"
    if (
        earlier_days is not None
        and rule_days == new
        and old < earlier_days <= new < published_days
    ):
        return "hold"
    return None
