"""The 30 major league clubs, for display.

Ids are stable. A rename or a move changes the club's name, not its id.
CLUB_NAMES is the one name the site shows for that id, including seasons
played under an older name. This module does not keep a historical name.
Nothing here is a service-time rule.
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


def _club_id(team_id) -> int | None:
    if isinstance(team_id, bool) or team_id is None:
        return None
    if isinstance(team_id, int):
        return team_id
    try:
        return int(team_id)
    except (TypeError, ValueError):
        return None


def club_name(team_id: int | None, team_names: dict | None = None, *, short: bool = False) -> str:
    """The site's name for a club id.

    A known id always uses CLUB_NAMES (CLUB_SHORT when asked). `team_names`
    cannot rename it; that argument is only a fallback for an id this module
    does not know. One name per id, including seasons from before a rename.
    """
    tid = _club_id(team_id)
    if tid is None:
        return "a major league club"
    if tid in CLUB_NAMES:
        if short:
            return CLUB_SHORT.get(tid, CLUB_NAMES[tid])
        return CLUB_NAMES[tid]
    if team_names and not short:
        found = team_names.get(tid) or team_names.get(str(tid))
        if found:
            return str(found)
    return "Club" if short else "a major league club"


def name_for_record(team_id, stored: str | None = None) -> str:
    """Display name for one stored player row.

    A known club id uses CLUB_NAMES even when `stored` is an older name from
    the feed. An unknown id keeps `stored`.
    """
    tid = _club_id(team_id)
    if tid in CLUB_NAMES:
        return CLUB_NAMES[tid]
    return stored or ""


def ids_for_display_names(names: list[str]) -> list[int | None]:
    """Club ids parallel to display names. An empty or unknown name is None."""
    by_name = {name: tid for tid, name in CLUB_NAMES.items()}
    return [by_name.get(name) if name else None for name in names]


def the_club(team_id: int | None, team_names: dict | None = None) -> str:
    return f"the {club_name(team_id, team_names)}"


# Sheet order is the alphabetical order of the current names. Abbreviations
# are navigation labels, not a second name list. Fixed, not read off today's
# roster: the menu must not depend on build-time roster data to list a club.
_SHEET_ABBR = (
    (109, "ARI"),
    (144, "ATL"),
    (133, "ATH"),
    (110, "BAL"),
    (111, "BOS"),
    (112, "CHC"),
    (145, "CWS"),
    (113, "CIN"),
    (114, "CLE"),
    (115, "COL"),
    (116, "DET"),
    (117, "HOU"),
    (118, "KC"),
    (108, "LAA"),
    (119, "LAD"),
    (146, "MIA"),
    (158, "MIL"),
    (142, "MIN"),
    (121, "NYM"),
    (147, "NYY"),
    (143, "PHI"),
    (134, "PIT"),
    (135, "SD"),
    (137, "SF"),
    (136, "SEA"),
    (138, "STL"),
    (139, "TB"),
    (140, "TEX"),
    (141, "TOR"),
    (120, "WSH"),
)

CLUB_ABBR = tuple((CLUB_NAMES[team_id], abbr) for team_id, abbr in _SHEET_ABBR)
