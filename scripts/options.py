"""Option years from a player's saved transaction history.

A season is used when the player spent at least the ruleset's day threshold
on optional assignment during that championship season. Days are the
championship-season dates from the option through the day before a recall.
The option date counts. The recall date does not. An option dated before
Opening Day starts counting on Opening Day. An option still open at the end
of the regular season stops at the last day of that season. An option dated
after the regular season ends does not count toward that season, and it is
not moved into the next one.

Two limits are not handled. Do not guess past them.

* The fourth-option exception. A player with fewer than five seasons of
  major- and minor-league active time can be eligible for a fourth option
  year. That test needs minor-league active-list days, which this record
  does not have. Remaining is always the standard number of option years
  minus seasons actually counted. No extra year is added, and nothing here
  labels a player as having one.
* A player who debuted before his own transaction record starts. Earlier
  option years may be missing. He is marked unknown/incomplete. The years
  that are visible are not published as a total.

Descriptions are read only to classify a move. They are never copied onto
a page.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

import cba
from il_stints import SeasonWindows
from mlb_clubs import MLB_CLUB_IDS

# Classify only. The same words the transaction text uses for these moves.
_OPTION_RE = re.compile(r"\boptioned\b", re.IGNORECASE)
_RECALL_RE = re.compile(r"\brecalled\b", re.IGNORECASE)
_ALTERNATE_RE = re.compile(r"alternate (?:training )?site", re.IGNORECASE)
_DFA_RE = re.compile(r"designated\b.{0,40}\bfor assignment", re.IGNORECASE)
_RELEASE_RE = re.compile(r"\breleased\b", re.IGNORECASE)
_OUTRIGHT_RE = re.compile(r"\boutrighted\b", re.IGNORECASE)
_SELECT_RE = re.compile(
    r"selected the contract|purchased the contract|contract selected",
    re.IGNORECASE,
)
_IL_RE = re.compile(
    r"\bplaced\b.{0,80}\bon the \d+\s*-?\s*day (?:injured|disabled) list",
    re.IGNORECASE,
)
_ACT_RE = re.compile(r"\b(?:activated|reinstated)\b", re.IGNORECASE)
_FA_RE = re.compile(r"elected free agency|declared free agency", re.IGNORECASE)
_RULE5_RE = re.compile(r"\brule 5\b", re.IGNORECASE)
_RETURN_RE = re.compile(r"\breturned\b", re.IGNORECASE)
_TRADE_RE = re.compile(r"\btraded\b", re.IGNORECASE)
_CLAIM_RE = re.compile(r"claimed\b.{0,40}\boff waivers", re.IGNORECASE)

# Same-day order: a player who is recalled and optioned on one date is
# optioned at the end of that date. Arrivals (recall, activation, injured-list
# placement) are applied first. The optional assignment, and a designation,
# release, or outright, are applied last.
_RANK = {
    "recall": 0,
    "end_excl": 1,
    "trade": 2,
    "option": 3,
    "end_incl": 4,
}


@dataclass
class _Event:
    date: dt.date
    kind: str
    club_id: int | None
    ends_il: bool = False
    starts_il: bool = False


@dataclass
class _Open:
    start: dt.date
    season_year: int
    club_id: int | None
    opening: dt.date
    closing: dt.date
    inferred: bool = False


@dataclass
class _Spell:
    start: dt.date
    end_exclusive: dt.date
    season_year: int
    opening: dt.date
    closing: dt.date
    inferred: bool = False


@dataclass
class OptionCount:
    """One player's option years.

    `remaining` and `out_of_options` are None when `incomplete` is set.
    Publishing a number in that case would be a guess.
    """

    used_years: list[int] = field(default_factory=list)
    saved_years: list[int] = field(default_factory=list)
    days: dict[int, int] = field(default_factory=dict)
    remaining: int | None = None
    out_of_options: bool | None = None
    incomplete: bool = False
    # Why the record is incomplete. "pre_feed" is the only reason that
    # changes what a page may say. The fourth-option exception is never a
    # reason here: it is not detected, and it is not guessed.
    reason: str = ""

    @property
    def used(self) -> int:
        return len(self.used_years)


def player_line(count: OptionCount) -> str:
    """The one sentence a player page shows. Our wording, no day threshold."""
    if count.incomplete or count.remaining is None:
        return "Option years: unknown."
    if count.out_of_options:
        years = _year_list(count.used_years)
        return (
            f"Option years used: {count.used}{years}. "
            f"Remaining: {count.remaining}. Out of options."
        )
    years = _year_list(count.used_years)
    return f"Option years used: {count.used}{years}. Remaining: {count.remaining}."


def _year_list(years: list[int]) -> str:
    if not years:
        return ""
    return " (" + ", ".join(str(year) for year in years) + ")"


def _as_date(value) -> dt.date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _int_id(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _mlb_ids(row: dict) -> list[int]:
    found = []
    for key in ("from_team_id", "team_id", "to_team_id"):
        club = _int_id(row.get(key))
        if club is not None and club in MLB_CLUB_IDS and club not in found:
            found.append(club)
    return found


def _assigning_club(row: dict) -> int | None:
    """The major league club that optioned him, when the row names one."""
    for key in ("from_team_id", "team_id", "to_team_id"):
        club = _int_id(row.get(key))
        if club is not None and club in MLB_CLUB_IDS:
            return club
    return None


def _classify(row: dict) -> _Event | None:
    """One roster move, or None when the row is not an option event.

    An ending move counts only when a major league club is a party to it.
    An affiliate-only activation or injured-list placement is not one.
    A recall between two affiliates is not one either, apart from a 2020
    alternate-site recall, which is.
    """
    if not isinstance(row, dict):
        return None
    day = _as_date(row.get("date"))
    text = row.get("description") or ""
    if day is None or not isinstance(text, str) or not text:
        return None
    clubs = _mlb_ids(row)
    mlb = bool(clubs)

    if _OPTION_RE.search(text):
        return _Event(day, "option", _assigning_club(row))
    if _RECALL_RE.search(text) and (mlb or _ALTERNATE_RE.search(text)):
        return _Event(day, "recall", clubs[0] if clubs else None)
    if not mlb:
        return None
    if _DFA_RE.search(text) or _RELEASE_RE.search(text) or _OUTRIGHT_RE.search(text):
        return _Event(day, "end_incl", clubs[0])
    if _IL_RE.search(text):
        return _Event(day, "end_excl", clubs[0], starts_il=True)
    if _SELECT_RE.search(text) or _FA_RE.search(text) or (
        _RULE5_RE.search(text) and _RETURN_RE.search(text)
    ):
        return _Event(day, "end_excl", clubs[0])
    if _ACT_RE.search(text):
        return _Event(day, "end_excl", clubs[0], ends_il=True)
    if _TRADE_RE.search(text) or _CLAIM_RE.search(text):
        return _Event(day, "trade", clubs[0])
    return None


def _bounds(
    windows: SeasonWindows, year: int, club_id: int | None
) -> tuple[dt.date, dt.date] | None:
    return windows.for_club(year, club_id)


def _clip_days(spell: _Spell, today: dt.date) -> int:
    """Championship-season days in the spell, none of them after `today`."""
    start = max(spell.start, spell.opening)
    stop = min(spell.closing, today)
    end = min(spell.end_exclusive, stop + dt.timedelta(days=1))
    if end <= start:
        return 0
    return (end - start).days


def _close(open_spell: _Open, end_exclusive: dt.date) -> _Spell:
    return _Spell(
        start=open_spell.start,
        end_exclusive=end_exclusive,
        season_year=open_spell.season_year,
        opening=open_spell.opening,
        closing=open_spell.closing,
        inferred=open_spell.inferred,
    )


def count_option_years(
    transactions: list[dict] | None,
    windows: SeasonWindows,
    today: dt.date,
) -> tuple[dict[int, int], bool]:
    """Days on optional assignment per season, and whether a spell was inferred.

    Returns ``(days_by_year, inferred)``. Years with zero days are omitted.
    This does not decide used versus saved, and it does not apply the
    fourth-option exception. Callers compare the day totals with the
    ruleset threshold.
    """
    events: list[_Event] = []
    for row in transactions or []:
        event = _classify(row)
        if event is not None and event.date <= today:
            events.append(event)
    events.sort(key=lambda event: (event.date, _RANK[event.kind]))

    spells: list[_Spell] = []
    open_spell: _Open | None = None
    on_il = False
    recalled_on: dt.date | None = None
    last_mlb: dt.date | None = None
    inferred = False

    def finish_prior_season(event: _Event) -> None:
        nonlocal open_spell
        if open_spell is None or event.date.year <= open_spell.season_year:
            return
        # A later year's move must not close an earlier season's assignment.
        # The assignment ended when that season did.
        spells.append(_close(open_spell, open_spell.closing + dt.timedelta(days=1)))
        open_spell = None

    for event in events:
        finish_prior_season(event)
        if event.kind == "option":
            if open_spell is not None and open_spell.season_year == event.date.year:
                # A second option the same season continues the assignment
                # already open. A duplicate row is the same case.
                last_mlb = event.date
                continue
            bounds = _bounds(windows, event.date.year, event.club_id)
            if bounds is None:
                last_mlb = event.date
                continue
            opening, closing = bounds
            if event.date > closing:
                # After the regular season. Not this season, and not next.
                last_mlb = event.date
                continue
            open_spell = _Open(
                start=event.date,
                season_year=event.date.year,
                club_id=event.club_id,
                opening=opening,
                closing=closing,
            )
            last_mlb = event.date
            continue
        if event.kind == "recall":
            if open_spell is not None:
                spells.append(_close(open_spell, event.date))
                open_spell = None
            elif on_il or recalled_on == event.date:
                # A recall while he is on the major league injured list is a
                # rehab return, not an optional assignment. A second recall
                # the same day is a duplicate row.
                pass
            else:
                bounds = _bounds(windows, event.date.year, event.club_id)
                if bounds is not None:
                    opening, closing = bounds
                    if opening <= event.date <= closing:
                        start = opening
                        if last_mlb is not None and last_mlb > opening:
                            start = last_mlb
                        if start < event.date:
                            inferred = True
                            spells.append(
                                _Spell(
                                    start=start,
                                    end_exclusive=event.date,
                                    season_year=event.date.year,
                                    opening=opening,
                                    closing=closing,
                                    inferred=True,
                                )
                            )
            recalled_on = event.date
            last_mlb = event.date
            continue
        if event.kind in ("end_incl", "end_excl") and open_spell is not None:
            if event.kind == "end_incl":
                # The designation, release, or outright day itself counts.
                end = event.date + dt.timedelta(days=1)
            else:
                end = event.date
            spells.append(_close(open_spell, end))
            open_spell = None
        if event.starts_il:
            on_il = True
        if event.ends_il:
            on_il = False
        last_mlb = event.date

    if open_spell is not None:
        spells.append(_close(open_spell, open_spell.closing + dt.timedelta(days=1)))

    days: dict[int, int] = {}
    for spell in spells:
        counted = _clip_days(spell, today)
        if counted:
            days[spell.season_year] = days.get(spell.season_year, 0) + counted
    return days, inferred


def _debut_year(player: dict) -> int | None:
    raw = player.get("mlb_debut")
    if not raw or raw == "—":
        return None
    text = str(raw)[:4]
    if len(text) == 4 and text.isdigit():
        return int(text)
    return None


def _feed_start_year(transactions: list[dict] | None) -> int | None:
    years = []
    for row in transactions or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get("date") or "")[:4]
        if len(text) == 4 and text.isdigit():
            years.append(int(text))
    return min(years) if years else None


def record_is_incomplete(player: dict, transactions: list[dict] | None) -> bool:
    """True when a remaining-options number would be a guess.

    The fourth-option exception is not detected here. It is not handled at
    all: remaining always uses the standard three, and a player is never
    marked unknown merely because he might qualify for a fourth. The case
    this function catches is a career that starts before the saved
    transactions do.
    """
    if transactions is None:
        return True
    if player.get("history_complete") is False:
        return True
    missing = player.get("missing_seasons")
    if isinstance(missing, int) and not isinstance(missing, bool) and missing > 0:
        return True
    debut = _debut_year(player)
    if debut is None:
        # No major league debut to predate the record. Count what is there.
        return False
    first = _feed_start_year(transactions)
    if first is None:
        return True
    return debut < first


def summarize(
    player: dict,
    transactions: list[dict] | None,
    windows: SeasonWindows,
    today: dt.date,
) -> OptionCount:
    """Option years used, remaining under the standard rule, and the flag."""
    rules = cba.default()
    threshold = rules.require_verified("options.days_to_burn_option_year")
    standard = rules.require("options.standard_option_years")
    incomplete = record_is_incomplete(player, transactions)
    if transactions is None:
        return OptionCount(incomplete=True, reason="pre_feed")

    days, _inferred = count_option_years(transactions, windows, today)
    used = [year for year, total in sorted(days.items()) if total >= threshold]
    saved = [year for year, total in sorted(days.items()) if 0 < total < threshold]
    if incomplete:
        # The visible seasons are kept for tests. They are not a published total.
        return OptionCount(
            used_years=used,
            saved_years=saved,
            days=days,
            remaining=None,
            out_of_options=None,
            incomplete=True,
            reason="pre_feed",
        )
    remaining = max(0, int(standard) - len(used))
    return OptionCount(
        used_years=used,
        saved_years=saved,
        days=days,
        remaining=remaining,
        out_of_options=len(used) >= int(standard),
        incomplete=False,
    )
