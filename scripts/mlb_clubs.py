"""The 30 major league clubs, for display.

Ids are stable. The Athletics' move changed the club's name, not its id.
Names are the ones the site already uses. Nothing here is a service-time rule.
"""

from __future__ import annotations

# 108-121, 133-147, and 158. Same set fetch_schedules.py keeps.
MLB_CLUB_IDS = frozenset(list(range(108, 122)) + list(range(133, 148)) + [158])

CLUB_NAMES = {
    108: "Los Angeles Angels",
    109: "Arizona Diamondbacks",
    110: "Baltimore Orioles",
    111: "Boston Red Sox",
    112: "Chicago Cubs",
    113: "Cincinnati Reds",
    114: "Cleveland Guardians",
    115: "Colorado Rockies",
    116: "Detroit Tigers",
    117: "Houston Astros",
    118: "Kansas City Royals",
    119: "Los Angeles Dodgers",
    120: "Washington Nationals",
    121: "New York Mets",
    133: "Athletics",
    134: "Pittsburgh Pirates",
    135: "San Diego Padres",
    136: "Seattle Mariners",
    137: "San Francisco Giants",
    138: "St. Louis Cardinals",
    139: "Tampa Bay Rays",
    140: "Texas Rangers",
    141: "Toronto Blue Jays",
    142: "Minnesota Twins",
    143: "Philadelphia Phillies",
    144: "Atlanta Braves",
    145: "Chicago White Sox",
    146: "Miami Marlins",
    147: "New York Yankees",
    158: "Milwaukee Brewers",
}

# Short labels for table cells. Timeline sentences use the full name.
CLUB_SHORT = {
    108: "Angels",
    109: "D-backs",
    110: "Orioles",
    111: "Red Sox",
    112: "Cubs",
    113: "Reds",
    114: "Guardians",
    115: "Rockies",
    116: "Tigers",
    117: "Astros",
    118: "Royals",
    119: "Dodgers",
    120: "Nationals",
    121: "Mets",
    133: "Athletics",
    134: "Pirates",
    135: "Padres",
    136: "Mariners",
    137: "Giants",
    138: "Cardinals",
    139: "Rays",
    140: "Rangers",
    141: "Blue Jays",
    142: "Twins",
    143: "Phillies",
    144: "Braves",
    145: "White Sox",
    146: "Marlins",
    147: "Yankees",
    158: "Brewers",
}


def _int_id(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def involves_mlb_club(row: dict, mlb_ids: frozenset[int] | set[int] = MLB_CLUB_IDS) -> bool:
    """True when the row names a major league club, or names no club at all.

    A row with no id cannot be judged. Callers that must not treat an
    unaffiliated row as major league (an injured-list placement) check for a
    positive club id themselves.
    """
    ids = [
        i
        for i in (
            _int_id(row.get("team_id")),
            _int_id(row.get("from_team_id")),
            _int_id(row.get("to_team_id")),
        )
        if i is not None
    ]
    if not ids:
        return True
    return any(i in mlb_ids for i in ids)


def club_name(team_id: int | None, team_names: dict | None = None, *, short: bool = False) -> str:
    """Our name for a club. `team_names` wins when the page already resolved it."""
    if team_id is None:
        return "a major league club"
    if team_names:
        found = team_names.get(team_id) or team_names.get(str(team_id))
        if found and not short:
            return str(found)
    if short:
        return CLUB_SHORT.get(team_id, CLUB_NAMES.get(team_id, "Club"))
    return CLUB_NAMES.get(team_id, "a major league club")


def the_club(team_id: int | None, team_names: dict | None = None) -> str:
    return f"the {club_name(team_id, team_names)}"
