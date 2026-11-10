"""Rule 5 eligibility from a birth date and professional signings.

The age date is the June 5 immediately before the first professional
contract. Eighteen or younger on that date means the fifth December
meeting after the signing; nineteen or older means the fourth. December
of the signing year is meeting one, except for a signing that falls
after the season has ended and before that meeting. That delay is
applied when the public season dates settle it, and left unknown when
they do not.

This module does not use prospect ranks or minor-league day counts.
A player with more than one professional signing is classified from the
earliest one and flagged estimated. A winter or spring international
signing is flagged estimated as well, because that June 5 age date is
still unsettled. A major league debut is ignored.

Nothing here is a service-time calculation.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

RULE5_YEAR = 2026

ELIGIBLE_FIRST = "Eligible (first time)"
ELIGIBLE_AGAIN = "Eligible (again)"
NOT_YET = "Not yet"
UNKNOWN = "Unknown"

REASON_SEVERAL = "More than one professional signing; the earliest is used."
REASON_WINTER = (
    "Winter or spring international signing; the June 5 age date is unsettled."
)
NOTE_BIRTH = "No birth date on record."
NOTE_SIGNING = "No signing date on record."
NOTE_WINDOW = (
    "Signed after the season, and the schedule does not settle whether "
    "that December meeting counts."
)

# Countries whose amateurs enter through the draft rather than an
# international signing. Compared to the people feed's birthCountry.
DRAFT_COUNTRIES = frozenset({
    "USA",
    "US",
    "U.S.",
    "U.S.A.",
    "United States",
    "United States of America",
    "Canada",
    "Puerto Rico",
})

_ROUND_RE = re.compile(r"^(\d+)")


@dataclass(frozen=True)
class Signing:
    on: dt.date
    type_code: str
    description: str = ""


@dataclass(frozen=True)
class Draft:
    year: int
    round: str | None = None
    pick: int | None = None


@dataclass(frozen=True)
class SeasonEnds:
    """Dates that close the post-season signing window for one year.

    all_minor_leagues is the last day of every minor-league season,
    including the postseason. majors_championship is the last day of the
    major-league championship season, which the feed records as the
    regular season. Either may be missing.
    """

    all_minor_leagues: dt.date | None = None
    majors_championship: dt.date | None = None


@dataclass(frozen=True)
class Decision:
    status: str
    estimated: bool = False
    reasons: tuple[str, ...] = ()
    first_eligible_year: int | None = None
    age_on_june5: int | None = None
    june5: dt.date | None = None
    signing: Signing | None = None
    meetings: int | None = None
    note: str = ""
    meeting_years: tuple[int, ...] = field(default_factory=tuple)


def age_on(birth: dt.date, on: dt.date) -> int:
    """Completed years on `on`. A birthday on that date counts."""
    years = on.year - birth.year
    if (on.month, on.day) < (birth.month, birth.day):
        years -= 1
    return years


def june5_before(signing: dt.date) -> dt.date:
    """The June 5 immediately before the signing date.

    A signing on June 5 itself is not after that day's June 5, so the
    date used is June 5 of the previous year. A signing on June 6 uses
    June 5 of the same year.
    """
    this_june = dt.date(signing.year, 6, 5)
    if signing > this_june:
        return this_june
    return dt.date(signing.year - 1, 6, 5)


def is_winter_or_spring(day: dt.date) -> bool:
    """December through June 5. June 6 onward is the summer signing window."""
    if day.month == 12 or day.month <= 5:
        return True
    return day.month == 6 and day.day <= 5


def is_international(
    signing: Signing, birth_country: str | None, drafts: list[Draft] | None
) -> bool:
    """An amateur free-agent signing from outside the draft countries.

    A same-year draft means the player came through the draft, whatever
    the birth country says. A missing country is not treated as
    international.
    """
    if signing.type_code != "SFA":
        return False
    if not birth_country or birth_country.strip() in DRAFT_COUNTRIES:
        return False
    for draft in drafts or []:
        if draft.year == signing.on.year:
            return False
    return True


def how_text(
    signing: Signing, drafts: list[Draft] | None, birth_country: str | None
) -> str:
    """How the contract was signed: a draft round, international, or free agent."""
    same_year = [
        d for d in (drafts or []) if d.year == signing.on.year and d.round
    ]
    if signing.type_code == "SGN" and same_year:
        return f"Round {same_year[0].round}"
    if signing.type_code == "SGN":
        return "Draft"
    if is_international(signing, birth_country, drafts):
        return "International"
    return "Free agent"


def round_sort_key(round_label: str | None) -> tuple[int, str]:
    """Lower draft rounds sort first. A missing round sorts last."""
    if not round_label:
        return (999, "")
    match = _ROUND_RE.match(round_label)
    if not match:
        return (900, round_label)
    return (int(match.group(1)), round_label[match.end():])


def selection_delay(signing: dt.date, ends: SeasonEnds | None) -> bool | None:
    """Whether December of the signing year does not count as meeting one.

    True means meeting one is December of the following year. False means
    meeting one is December of the signing year. None means the public
    season dates do not settle a signing that might fall in the gap after
    the minor-league year and before the major-league championship season
    ends.

    Without season dates, January through August is not that gap,
    September and October are unsettled, and November and December are
    after the season.
    """
    if ends is None or ends.all_minor_leagues is None:
        if signing.month <= 8:
            return False
        if signing.month >= 11:
            return True
        return None

    if signing <= ends.all_minor_leagues:
        return False
    club_end = ends.majors_championship
    if club_end is None:
        if signing.month >= 11:
            return True
        return None
    if signing <= club_end:
        return None
    return True


def _dedupe(signings: list[Signing]) -> list[Signing]:
    seen: set[tuple[dt.date, str]] = set()
    out: list[Signing] = []
    for signing in signings:
        if signing.type_code not in {"SGN", "SFA"}:
            continue
        key = (signing.on, signing.type_code)
        if key in seen:
            continue
        seen.add(key)
        out.append(signing)
    out.sort(key=lambda s: (s.on, s.type_code))
    return out


def _years(signing: dt.date, meetings: int, delay: bool | None) -> list[int]:
    if delay is None:
        return [
            signing.year + meetings - 1,
            signing.year + 1 + meetings - 1,
        ]
    start = signing.year + (1 if delay else 0)
    return [start + meetings - 1]


def _status(year: int, rule5_year: int) -> str:
    if year < rule5_year:
        return ELIGIBLE_AGAIN
    if year == rule5_year:
        return ELIGIBLE_FIRST
    return NOT_YET


def _unknown(note: str, **kwargs) -> Decision:
    return Decision(status=UNKNOWN, note=note, **kwargs)


def classify(
    birth: dt.date | None,
    signings: list[Signing],
    *,
    birth_country: str | None = None,
    drafts: list[Draft] | None = None,
    season_ends: SeasonEnds | None = None,
    rule5_year: int = RULE5_YEAR,
    mlb_debut: dt.date | None = None,
) -> Decision:
    """Eligibility for the December `rule5_year` meeting.

    `mlb_debut` is accepted and ignored. The clock starts at the first
    professional contract, not at a debut.
    """
    del mlb_debut  # a debut does not start the clock
    kept = _dedupe(signings)
    if birth is None:
        return _unknown(NOTE_BIRTH)
    if not kept:
        return _unknown(NOTE_SIGNING)

    first = kept[0]
    reasons: list[str] = []
    if len(kept) > 1:
        reasons.append(REASON_SEVERAL)
    if is_international(first, birth_country, drafts) and is_winter_or_spring(first.on):
        reasons.append(REASON_WINTER)

    june5 = june5_before(first.on)
    age = age_on(birth, june5)
    meetings = 5 if age <= 18 else 4
    delay = selection_delay(first.on, season_ends)
    years = _years(first.on, meetings, delay)
    statuses = {_status(year, rule5_year) for year in years}
    shared = dict(
        estimated=bool(reasons),
        reasons=tuple(reasons),
        age_on_june5=age,
        june5=june5,
        signing=first,
        meetings=meetings,
    )
    if len(statuses) != 1:
        return _unknown(NOTE_WINDOW, **shared)
    only_year = years[0] if len(set(years)) == 1 else None
    meeting_years: tuple[int, ...] = ()
    if delay is not None and only_year is not None:
        start = first.on.year + (1 if delay else 0)
        meeting_years = tuple(range(start, start + meetings))
    return Decision(
        status=statuses.pop(),
        first_eligible_year=only_year,
        meeting_years=meeting_years,
        **shared,
    )
