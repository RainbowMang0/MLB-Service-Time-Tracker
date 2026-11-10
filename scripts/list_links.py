"""Short links from a player or a list page to a page that already exists.

Eligibility is decided by the caller. This module only turns that decision
into one line of HTML, and finds season-overlap teammates from seasons the
player record already stores. It does not compute service time.
"""

from __future__ import annotations

import html

LEAGUE_LISTS = (
    ("arbitration-eligible.html", "Arbitration eligible"),
    ("super-two.html", "Super Two"),
    ("non-tender-watch.html", "Non-tender watch"),
    ("free-agents.html", "Free agents"),
    ("options.html", "Out of options"),
)


def _a(href: str, label: str) -> str:
    return f'<a href="{html.escape(href, quote=True)}">{html.escape(label)}</a>'


def related_league_line(current: str, base: str) -> str:
    """One line on a league list, pointing at the other four."""
    parts = [_a(f"{base}{href}", label) for href, label in LEAGUE_LISTS if href != current]
    if len(parts) < 2:
        return ""
    return f'<p class="list-links">Also: {" · ".join(parts)}.</p>'


def winter_line(
    base: str,
    *,
    free_agent: bool = False,
    arbitration: bool = False,
    super_two: bool = False,
    non_tender: bool = False,
    out_of_options: bool = False,
    club: str = "",
    club_free_agents: str = "",
    club_arbitration: str = "",
    club_non_tender: str = "",
) -> str:
    """One line when a rostered player is on a current list. Empty when he is on none."""
    parts: list[str] = []
    if free_agent:
        parts.append(_a(f"{base}free-agents.html", "free agent list"))
        if club and club_free_agents:
            parts.append(_a(club_free_agents, f"{club} free agents"))
    if arbitration:
        parts.append(_a(f"{base}arbitration-eligible.html", "arbitration list"))
        if club and club_arbitration:
            parts.append(_a(club_arbitration, f"{club} arbitration"))
    if super_two:
        parts.append(_a(f"{base}super-two.html", "Super Two list"))
    if non_tender:
        parts.append(_a(f"{base}non-tender-watch.html", "non-tender watch"))
        if club and club_non_tender:
            parts.append(_a(club_non_tender, f"{club} non-tender"))
    if out_of_options:
        parts.append(_a(f"{base}options.html", "out of options list"))
    if not parts:
        return ""
    return f'<p class="list-links">On this winter\'s {" · ".join(parts)}.</p>'


def contract_line(base: str) -> str:
    return f'<p class="list-links"><a href="{html.escape(base, quote=True)}contract.html">Contract clock</a></p>'


def alumni_lists(base: str, letter_href: str, letter: str) -> str:
    """The surname list and the A–Z directory. Both already exist."""
    if letter == "#":
        label = "More previous players"
    else:
        label = f"More previous players, {letter}"
    return (
        '<p class="list-links">'
        + _a(f"{base}{letter_href}", label)
        + " · "
        + _a(f"{base}alumni/", "Previous players A–Z")
        + ".</p>"
    )


def last_club_label(club_html: str, href: str | None, *, on_roster: bool) -> str:
    """Rostered: the club name, already linked from the breadcrumb.

    Former player: 'Last with' the club page when that page exists. An old
    club name with no page stays plain text, so the link cannot 404.
    """
    if not club_html:
        return ""
    if on_roster or not href:
        return club_html if on_roster else f"Last with {club_html}"
    return f'Last with <a href="{html.escape(href, quote=True)}">{club_html}</a>'


def index_seasons(rows) -> dict[tuple[int, int], list[dict]]:
    """(year, club id) -> players who have a published page and a credited season there.

    `rows` is (id, name, seasons). Days are not stored: a one-day tick must
    not change who is listed.
    """
    index: dict[tuple[int, int], list[dict]] = {}
    for pid, name, seasons in rows:
        if pid is None or not name:
            continue
        for season in seasons or []:
            year = season.get("y")
            try:
                days = int(season.get("d") or 0)
                year_i = int(year)
            except (TypeError, ValueError):
                continue
            if year is None or days <= 0:
                continue
            for tid in season.get("t") or []:
                try:
                    key = (year_i, int(tid))
                except (TypeError, ValueError):
                    continue
                index.setdefault(key, []).append({"id": pid, "name": name})
    return index


def pick_teammates(player: dict, index: dict, limit: int = 8) -> list[dict]:
    """Players who shared a club-season with him. Most recent season, then name.

    Empty when the record has no club-season, or when no index was passed.
    Order does not use service-time days.
    """
    if not index:
        return []
    pid = player.get("id")
    best: dict = {}
    for season in player.get("seasons") or []:
        try:
            year_i = int(season.get("y"))
            days = int(season.get("d") or 0)
        except (TypeError, ValueError):
            continue
        if days <= 0:
            continue
        for tid in season.get("t") or []:
            try:
                key = (year_i, int(tid))
            except (TypeError, ValueError):
                continue
            for mate in index.get(key, ()):
                if mate.get("id") == pid:
                    continue
                prev = best.get(mate["id"])
                if prev is None or year_i > prev[0] or (year_i == prev[0] and mate["name"] < prev[1]):
                    best[mate["id"]] = (year_i, mate["name"], mate["id"])
    ranked = sorted(best.values(), key=lambda row: (-row[0], row[1]))
    return [{"id": row[2], "name": row[1]} for row in ranked[:limit]]


def teammates_html(items: list[tuple[str, str]]) -> str:
    """(href, name) pairs. Names only: a teammate's day count must not rewrite this page."""
    if not items:
        return ""
    lis = "".join(
        f'<li><a href="{html.escape(href, quote=True)}">{html.escape(name)}</a></li>'
        for href, name in items
    )
    return (
        "<h2>Teammates</h2>"
        '<p class="pp-note">Players who shared a club with him in a season on record.</p>'
        f"<ul class='teammates'>{lis}</ul>"
    )
