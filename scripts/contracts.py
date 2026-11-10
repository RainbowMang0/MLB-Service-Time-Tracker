#!/usr/bin/env python3
"""
contracts.py
------------
Reads data/reference/contracts.json. One place decides whether a guaranteed
season, or an option that was already exercised, covers a year.

A player with no row is unchanged. source_url and unconfirmed stay in the
file; label_for() never returns them, and it never returns a salary.
"""

from __future__ import annotations

import html
import json
import pathlib
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]
PATH = ROOT / "data" / "reference" / "contracts.json"

# The free-agent pages are a service-time projection. This sentence is the
# one they show. It does not name a source site or a data file.
PROJECTION_NOTE = "projected by service time; players under contract are excluded"

STATUS_SIGNED = "signed"
STATUS_OPTION_EXERCISED = "option_exercised"
# Recorded, but the club has not confirmed it. Lists and page lines ignore it.
STATUS_UNCONFIRMED = "unconfirmed"
_GOVERNING = (STATUS_SIGNED, STATUS_OPTION_EXERCISED)

_OPTION_WORD = {
    "club": "club option",
    "player": "player option",
    "vesting": "vesting option",
    "mutual": "mutual option",
}

_CACHE: dict[int, "Contract"] | None = None


@dataclass(frozen=True)
class Contract:
    id: int
    name: str
    club: str
    signed: str | None
    first: int
    last: int
    option_years: tuple[int, ...]
    option_type: str | None
    status: str
    source_url: str
    unconfirmed: str

    def governs(self) -> bool:
        """A confirmed guarantee. An unconfirmed row does not change the site."""
        return self.status in _GOVERNING

    def covers_season(self, season: int) -> bool:
        return self.governs() and self.first <= int(season) <= self.last

    def option_note(self) -> str:
        word = _OPTION_WORD.get(self.option_type or "", "option")
        if self.option_years:
            return f"{_year_span(self.option_years)} {word if self.option_type else 'option'}"
        if self.status == STATUS_OPTION_EXERCISED:
            return "option exercised"
        if self.option_type:
            return word
        return ""

    def label(self, as_of: int | None = None) -> str:
        """'Signed through 2030 · 2031 vesting option', or '' if it has lapsed."""
        if not self.governs():
            return ""
        if as_of is not None and self.last < int(as_of):
            return ""
        note = self.option_note()
        base = f"Signed through {self.last}"
        return f"{base} · {note}" if note else base


def _year_span(years: tuple[int, ...]) -> str:
    ordered = tuple(sorted(set(int(y) for y in years)))
    if not ordered:
        return ""
    if len(ordered) == 1:
        return str(ordered[0])
    if list(ordered) == list(range(ordered[0], ordered[-1] + 1)):
        return f"{ordered[0]}\u2013{ordered[-1]}"
    return ", ".join(str(y) for y in ordered)


def _row_from(raw: dict) -> Contract | None:
    if not isinstance(raw, dict) or "id" not in raw:
        return None
    span = raw.get("guaranteed_seasons") or {}
    try:
        pid = int(raw["id"])
        first = int(span["first"])
        last = int(span["last"])
    except (TypeError, ValueError, KeyError):
        return None
    if last < first:
        return None
    years = raw.get("option_years") or []
    try:
        option_years = tuple(int(y) for y in years)
    except (TypeError, ValueError):
        return None
    status = raw.get("status") or STATUS_SIGNED
    if status not in (STATUS_SIGNED, STATUS_OPTION_EXERCISED, STATUS_UNCONFIRMED):
        return None
    return Contract(
        id=pid,
        name=str(raw.get("name") or ""),
        club=str(raw.get("club") or ""),
        signed=raw.get("signed") or None,
        first=first,
        last=last,
        option_years=option_years,
        option_type=raw.get("option_type") or None,
        status=status,
        source_url=str(raw.get("source_url") or ""),
        unconfirmed=str(raw.get("unconfirmed") or ""),
    )


def load(path: pathlib.Path | None = None) -> dict[int, Contract]:
    """{player id -> Contract}. A missing or unreadable file is nobody."""
    global _CACHE
    if path is None and _CACHE is not None:
        return _CACHE
    target = path or PATH
    out: dict[int, Contract] = {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        if path is None:
            _CACHE = out
        return out
    rows = payload.get("contracts") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        rows = []
    for raw in rows:
        row = _row_from(raw)
        if row is not None:
            out[row.id] = row
    if path is None:
        _CACHE = out
    return out


def clear_cache() -> None:
    global _CACHE
    _CACHE = None


def _index(index: dict[int, Contract] | None) -> dict[int, Contract]:
    return load() if index is None else index


def get(player_id, index: dict[int, Contract] | None = None) -> Contract | None:
    if player_id is None:
        return None
    try:
        return _index(index).get(int(player_id))
    except (TypeError, ValueError):
        return None


def covers(player_id, season, index: dict[int, Contract] | None = None) -> bool:
    """True when a confirmed guarantee, including an exercised option, includes `season`.

    An open option year does not count. No row, and an unconfirmed row, mean
    not covered. A missing-from-a-free-agent-list note is not a contract.
    """
    row = get(player_id, index)
    if row is None or season is None or not row.governs():
        return False
    try:
        return row.covers_season(int(season))
    except (TypeError, ValueError):
        return False


def filing_year(
    service_year: int | None,
    player_id,
    next_season,
    index: dict[int, Contract] | None = None,
) -> int | None:
    """The free-agent year to file him under.

    `service_year` is the service-time projection, or None when he is already
    eligible (the "now" group). A confirmed guarantee can only push that year
    later, to its last season. Unconfirmed rows, missing rows, and deals that
    do not cover the season after the projection leave the year alone.
    """
    if next_season is None:
        return service_year
    row = get(player_id, index)
    if row is None or not row.governs():
        return service_year
    try:
        coming = int(next_season)
    except (TypeError, ValueError):
        return service_year
    if service_year is None:
        if row.covers_season(coming):
            return row.last
        return None
    try:
        projected = int(service_year)
    except (TypeError, ValueError):
        return service_year
    if row.covers_season(projected + 1):
        return max(projected, row.last)
    return projected


def label_for(player_id, as_of=None, index: dict[int, Contract] | None = None) -> str:
    """The short line for a page, or '' when he has no active row.

    `as_of` is the season the page is about. A deal whose last guaranteed
    season is already over returns '' so an expired contract changes nothing.
    """
    row = get(player_id, index)
    if row is None:
        return ""
    try:
        year = None if as_of is None else int(as_of)
    except (TypeError, ValueError):
        return ""
    return row.label(year)


def signed_block(player_id, season) -> str:
    """The player-page line, or '' when there is nothing to show."""
    text = label_for(player_id, season)
    if not text:
        return ""
    return f"\n  <p class=\"signed-through\">{html.escape(text)}</p>"


def roster_name(player_id, season, href: str, name: str) -> str:
    """A roster-table name, with the contract line under it when one applies."""
    text = label_for(player_id, season)
    extra = f"<span class='roster-signed'>{html.escape(text)}</span>" if text else ""
    return f"<a href=\"{href}\">{html.escape(name)}</a>{extra}"
