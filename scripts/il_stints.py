"""Injured-list stints for player pages.

Display only. A day on the injured list is still a day of major league
service time; nothing in this module is read by the service-time engine,
and it does not change a stored figure.

Rows come from the transaction cache. Older rows omit effectiveDate and
the other fields added later; a missing key is None.

Days are regular-season days inside the club's own season window, from
the start through the day before the return. A return the feed does not
show is a lower bound through the last injured-list row, never a guessed
return date.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
from dataclasses import dataclass, field

from mlb_clubs import MLB_CLUB_IDS, _int_id, involves_mlb_club

WINDOWS_PATH = pathlib.Path(__file__).resolve().parents[1] / "data" / "season_windows.json"

_MONTHS = {
    name: i
    for i, name in enumerate(
        (
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ),
        start=1,
    )
}

# Descriptions are read only to classify. They are never copied onto a page.
_PLACE_RE = re.compile(
    r"\bplaced\b.{0,80}?\bon the (\d+)\s*-?\s*day (?:injured|disabled) list",
    re.IGNORECASE,
)
_TRANSFER_RE = re.compile(
    r"\btransferred\b.{0,80}?\bfrom the (\d+)\s*-?\s*day (?:injured|disabled) list"
    r" to the (\d+)\s*-?\s*day",
    re.IGNORECASE,
)
_REHAB_RE = re.compile(r"rehab assignment", re.IGNORECASE)
_IL_ACT_RE = re.compile(
    r"\bactivated\b.{0,80}?\bfrom the \d+\s*-?\s*day (?:injured|disabled) list",
    re.IGNORECASE,
)
_RESERVE_ACT_RE = re.compile(
    r"\bactivated\b.{0,80}?\bfrom the (?:reserve|restricted) list",
    re.IGNORECASE,
)
_SKIP_ACT_RE = re.compile(
    r"paternity|bereavement|family medical|suspended",
    re.IGNORECASE,
)
_PLAIN_ACT_RE = re.compile(r"\b(?:activated|reinstated)\b", re.IGNORECASE)
_OPTION_RE = re.compile(r"\boptioned\b", re.IGNORECASE)
_OUTRIGHT_RE = re.compile(r"\boutrighted\b|sent .{0,60}? outright", re.IGNORECASE)
_RELEASE_RE = re.compile(r"\breleased\b", re.IGNORECASE)
_DFA_RE = re.compile(r"designated .{0,60}? for assignment", re.IGNORECASE)
_FA_RE = re.compile(r"elected free agency|declared free agency", re.IGNORECASE)
_REASSIGN_RE = re.compile(r"\breassigned\b", re.IGNORECASE)
_RULE5_RE = re.compile(r"returned to .{0,80}? \bfrom\b", re.IGNORECASE)
_TRADE_RE = re.compile(r"\btraded\b", re.IGNORECASE)
_CLAIM_RE = re.compile(r"claimed .{0,60}? off waivers", re.IGNORECASE)
_RETRO_RE = re.compile(r"retroactive to ([A-Za-z]+ \d{1,2}, \d{4})", re.IGNORECASE)


@dataclass
class Stint:
    """One major league injured-list stay.

    `kind` is "exact" (a return or a roster move closed it), "lower"
    (the return is not in the record; `days` is a floor), or "open"
    (still out; `days` runs through today or the season's last day and
    must not be printed, because it changes every day of the season).

    `reasons` is the placement/transfer rows' feed text, in date order,
    kept only long enough for `scripts/injury_labels.py` to derive a
    coarse body-part label at build time -- it is never stored or
    published. `label`, `label_source`, `label_conflict`, and `multi`
    are that derived result; a page prints only those four fields.
    """

    start: dt.date
    end: dt.date | None
    lists: list[tuple[int, dt.date, str]]
    clubs: list[int]
    days: int
    kind: str
    close: str
    last_evidence: dt.date
    opened_without_placement: bool = False
    segments: list[tuple[dt.date, int]] = field(default_factory=list)
    last_placement_row: dt.date | None = None
    reasons: list[tuple[dt.date, str, str]] = field(default_factory=list)
    label: str | None = None
    label_source: str | None = None
    label_conflict: bool = False
    multi: bool = False

    @property
    def lower_bound(self) -> bool:
        return self.kind == "lower"


@dataclass
class _Open:
    start: dt.date
    lists: list[tuple[int, dt.date, str]]
    clubs: list[int]
    segments: list[tuple[dt.date, int]]
    last_evidence: dt.date
    last_placement_row: dt.date
    opened_without_placement: bool = False
    season_year: int = 0
    reasons: list[tuple[dt.date, str, str]] = field(default_factory=list)


class SeasonWindows:
    """Per-club regular-season bounds, loaded from disk.

    Page builds must not call out for a schedule. The file is the saved
    result of reading each club's own first and last regular-season day.
    """

    def __init__(self, years: dict[int, dict]) -> None:
        self.years = years

    @classmethod
    def load(cls, path: pathlib.Path | None = None) -> "SeasonWindows":
        raw = json.loads((path or WINDOWS_PATH).read_text())
        years: dict[int, dict] = {}
        for key, row in (raw.get("years") or {}).items():
            year = int(key)
            clubs = {
                int(cid): dt.date.fromisoformat(day)
                for cid, day in (row.get("clubs") or {}).items()
            }
            ends = {
                int(cid): dt.date.fromisoformat(day)
                for cid, day in (row.get("ends") or {}).items()
            }
            years[year] = {
                "start": dt.date.fromisoformat(row["start"]),
                "end": dt.date.fromisoformat(row["end"]),
                "clubs": clubs,
                "ends": ends,
            }
        return cls(years)

    def for_club(self, year: int, club_id: int | None) -> tuple[dt.date, dt.date] | None:
        row = self.years.get(year)
        if row is None:
            return None
        start = row["start"]
        end = row["end"]
        if club_id is not None and club_id in row["clubs"]:
            start = row["clubs"][club_id]
        if club_id is not None and club_id in row["ends"]:
            end = max(end, row["ends"][club_id])
        return start, end

    def modal_start(self, year: int) -> dt.date | None:
        row = self.years.get(year)
        return row["start"] if row else None


def _as_date(value) -> dt.date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_retro(desc: str) -> dt.date | None:
    match = _RETRO_RE.search(desc or "")
    if not match:
        return None
    month_name, day_s, year_s = match.group(1).replace(",", "").split()
    month = _MONTHS.get(month_name.lower())
    if month is None:
        return None
    try:
        return dt.date(int(year_s), month, int(day_s))
    except ValueError:
        return None


def placement_start(row_date: dt.date, desc: str, effective) -> dt.date:
    """When the stay began.

    effectiveDate wins when the row has one and it is not after the row.
    Otherwise a "retroactive to …" date 0–14 days earlier, including a
    one-year typo repair. Otherwise the row date.
    """
    eff = _as_date(effective)
    if eff is not None and 0 <= (row_date - eff).days <= 400:
        return eff
    retro = _parse_retro(desc)
    if retro is not None and 0 <= (row_date - retro).days <= 14:
        return retro
    if retro is not None:
        for year in (row_date.year, row_date.year - 1):
            try:
                repaired = retro.replace(year=year)
            except ValueError:
                continue
            gap = (row_date - repaired).days
            if 0 <= gap <= 14:
                return repaired
    return row_date


def _mlb_club(row: dict, prefer: tuple[str, ...]) -> int | None:
    for key in prefer:
        club = _int_id(row.get(key))
        if club is not None and club in MLB_CLUB_IDS:
            return club
    return None


def _both_mlb(row: dict) -> bool:
    src = _int_id(row.get("from_team_id"))
    dst = _int_id(row.get("to_team_id"))
    return src in MLB_CLUB_IDS and dst in MLB_CLUB_IDS


def classify_row(row: dict) -> tuple | None:
    """What an injured-list walk should do with one row, or None to skip it.

    Returned shapes:
      ("placement", length, start)
      ("transfer", from_length, to_length, start)
      ("rehab",)
      ("activation",)
      ("departure", reason)
      ("trade",)
      ("claim",)
    """
    desc = row.get("description") or ""
    row_date = _as_date(row.get("date"))
    if row_date is None:
        return None
    if _REHAB_RE.search(desc):
        return ("rehab",)
    transfer = _TRANSFER_RE.search(desc)
    if transfer:
        return (
            "transfer",
            int(transfer.group(1)),
            int(transfer.group(2)),
            placement_start(row_date, desc, row.get("effectiveDate")),
        )
    placed = _PLACE_RE.search(desc)
    if placed:
        # November through January placements are offseason bookkeeping.
        if row_date.month in (11, 12, 1):
            return None
        return (
            "placement",
            int(placed.group(1)),
            placement_start(row_date, desc, row.get("effectiveDate")),
        )
    if _IL_ACT_RE.search(desc) or _RESERVE_ACT_RE.search(desc):
        return ("activation",)
    if _PLAIN_ACT_RE.search(desc) and not _SKIP_ACT_RE.search(desc):
        return ("activation",)
    if _OPTION_RE.search(desc):
        return ("departure", "option")
    if _OUTRIGHT_RE.search(desc):
        return ("departure", "outright")
    if _RELEASE_RE.search(desc):
        return ("departure", "release")
    if _DFA_RE.search(desc):
        return ("departure", "dfa")
    if _FA_RE.search(desc):
        return ("departure", "free_agency")
    if _REASSIGN_RE.search(desc):
        return ("departure", "reassign")
    if _RULE5_RE.search(desc) and _both_mlb(row):
        return ("departure", "rule5")
    if _TRADE_RE.search(desc):
        return ("trade",)
    if _CLAIM_RE.search(desc):
        return ("claim",)
    return None


def _keep_row(row: dict) -> bool:
    """Major league rows, plus a free-agency election even when its id is not."""
    desc = row.get("description") or ""
    if _FA_RE.search(desc):
        return True
    if not involves_mlb_club(row):
        return False
    # A placement has to name a major league club. An id-less row cannot.
    if _PLACE_RE.search(desc) or _TRANSFER_RE.search(desc):
        return _mlb_club(row, ("to_team_id", "team_id", "from_team_id")) is not None
    return True


def count_regular_days(
    start: dt.date,
    end_exclusive: dt.date,
    segments: list[tuple[dt.date, int]],
    windows: SeasonWindows,
) -> int:
    """Regular-season days in [start, end_exclusive), clipped per club."""
    if end_exclusive <= start:
        return 0
    total = 0
    day = start
    one = dt.timedelta(days=1)
    while day < end_exclusive:
        club = None
        for seg_day, seg_club in segments:
            if seg_day <= day:
                club = seg_club
            else:
                break
        bounds = windows.for_club(day.year, club)
        if bounds is not None and bounds[0] <= day <= bounds[1]:
            total += 1
        day += one
    return total


def _finalize(
    open_stint: _Open,
    kind: str,
    close: str,
    end: dt.date | None,
    windows: SeasonWindows,
    today: dt.date,
) -> Stint:
    if kind == "exact" and end is not None:
        days = count_regular_days(open_stint.start, end, open_stint.segments, windows)
    elif kind == "lower":
        days = count_regular_days(
            open_stint.start,
            open_stint.last_evidence + dt.timedelta(days=1),
            open_stint.segments,
            windows,
        )
        end = None
    else:
        days = count_regular_days(
            open_stint.start,
            today + dt.timedelta(days=1),
            open_stint.segments,
            windows,
        )
        end = None
        kind = "open"
    return Stint(
        start=open_stint.start,
        end=end,
        lists=list(open_stint.lists),
        clubs=list(open_stint.clubs),
        days=days,
        kind=kind,
        close=close,
        last_evidence=open_stint.last_evidence,
        opened_without_placement=open_stint.opened_without_placement,
        segments=list(open_stint.segments),
        last_placement_row=open_stint.last_placement_row,
        reasons=list(open_stint.reasons),
    )


def _open_from(
    start: dt.date,
    length: int | None,
    lengths: list[int],
    club: int | None,
    row_date: dt.date,
    *,
    without_placement: bool,
    how: str,
    reason_kind: str = "",
    desc: str = "",
) -> _Open | None:
    if club is None:
        return None
    lists: list[tuple[int, dt.date, str]] = []
    if length is not None:
        lists.append((length, row_date, how))
    for extra in lengths:
        if all(extra != have for have, _, _how in lists):
            lists.append((extra, row_date, how))
    reasons: list[tuple[dt.date, str, str]] = []
    if reason_kind:
        reasons.append((row_date, desc, reason_kind))
    return _Open(
        start=start,
        lists=lists,
        clubs=[club],
        segments=[(start, club)],
        last_evidence=max(start, row_date) if start <= row_date else row_date,
        last_placement_row=row_date,
        opened_without_placement=without_placement,
        season_year=start.year,
        reasons=reasons,
    )


def _add_list(open_stint: _Open, length: int, row_date: dt.date, how: str) -> None:
    if all(length != have for have, _, _how in open_stint.lists):
        open_stint.lists.append((length, row_date, how))


def _add_club(open_stint: _Open, club: int | None, on: dt.date) -> None:
    if club is None or club in open_stint.clubs:
        return
    open_stint.clubs.append(club)
    open_stint.segments.append((on, club))


def _placement_continues(open_stint: _Open, length: int, row_date: dt.date) -> bool:
    """A re-placement that is still the same stay, not a new one."""
    gap = (row_date - open_stint.last_placement_row).days
    if gap <= 3:
        return True
    if length == 60:
        return True
    current = open_stint.lists[-1][0] if open_stint.lists else None
    # The 7-day list moving to the 10-day list is one stay.
    if current == 7 and length == 10:
        return True
    return False


def _is_continuation(open_stint: _Open, parsed: tuple, row_date: dt.date) -> bool:
    kind = parsed[0]
    if kind in ("rehab", "transfer", "activation"):
        return True
    if kind == "placement":
        return _placement_continues(open_stint, parsed[1], row_date)
    return False


def build_stints(
    rows: list[dict],
    windows: SeasonWindows,
    today: dt.date,
    mlb_ids: set[int] | frozenset[int] | None = None,
) -> list[Stint]:
    """Injured-list stays in `rows`, in order.

    `mlb_ids` is accepted so a test can pass a smaller set. The default
    is the 30 clubs. Trades and waiver claims do not close a stay; the
    new club is recorded on it.
    """
    del mlb_ids  # the club set lives with the classifiers; kept for callers
    ordered = sorted(
        enumerate(rows or []),
        key=lambda item: ((item[1].get("date") or ""), item[0]),
    )
    seen: set[tuple] = set()
    kept: list[dict] = []
    for _, row in ordered:
        if not isinstance(row, dict) or not _keep_row(row):
            continue
        key = (
            row.get("date"),
            row.get("description"),
            row.get("from_team_id"),
            row.get("to_team_id"),
        )
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)

    stints: list[Stint] = []
    current: _Open | None = None

    def close(kind: str, reason: str, end: dt.date | None) -> None:
        nonlocal current
        if current is None:
            return
        stints.append(_finalize(current, kind, reason, end, windows, today))
        current = None

    for row in kept:
        row_date = _as_date(row.get("date"))
        if row_date is None:
            continue
        parsed = classify_row(row)
        if parsed is None:
            continue
        desc = row.get("description") or ""
        if current is not None:
            nxt = windows.modal_start(current.season_year + 1)
            if nxt is not None and row_date >= nxt and not _is_continuation(current, parsed, row_date):
                close("lower", "missed_return", None)
        kind = parsed[0]
        if kind == "placement":
            length, start = parsed[1], parsed[2]
            club = _mlb_club(row, ("to_team_id", "team_id", "from_team_id"))
            if current is not None and _placement_continues(current, length, row_date):
                if start < current.start:
                    current.start = start
                    if current.segments:
                        current.segments[0] = (start, current.segments[0][1])
                _add_list(current, length, row_date, "place")
                current.reasons.append((row_date, desc, "placement"))
                current.last_evidence = max(current.last_evidence, row_date, start)
                current.last_placement_row = row_date
                continue
            if current is not None:
                close("lower", "missed_return", None)
            opened = _open_from(
                start, length, [], club, row_date, without_placement=False, how="place",
                reason_kind="placement", desc=desc,
            )
            if opened is not None:
                current = opened
            continue
        if kind == "transfer":
            _from, to_len, start = parsed[1], parsed[2], parsed[3]
            club = _mlb_club(row, ("to_team_id", "team_id", "from_team_id"))
            if current is None:
                current = _open_from(
                    start, _from, [to_len], club, row_date,
                    without_placement=True, how="transfer",
                    reason_kind="transfer", desc=desc,
                )
                continue
            if start < current.start:
                current.start = start
                if current.segments:
                    current.segments[0] = (start, current.segments[0][1])
            _add_list(current, _from, row_date, "transfer")
            _add_list(current, to_len, row_date, "transfer")
            current.reasons.append((row_date, desc, "transfer"))
            current.last_evidence = max(current.last_evidence, row_date)
            current.last_placement_row = row_date
            continue
        if kind == "rehab":
            if current is not None:
                current.last_evidence = max(current.last_evidence, row_date)
            continue
        if kind == "activation":
            if current is not None:
                close("exact", "activation", row_date)
            continue
        if kind == "departure":
            if current is not None:
                close("exact", parsed[1], row_date)
            continue
        if kind in ("trade", "claim"):
            if current is not None:
                _add_club(current, _mlb_club(row, ("to_team_id",)), row_date)
            continue

    if current is not None:
        nxt = windows.modal_start(current.season_year + 1)
        if nxt is not None and today >= nxt:
            close("lower", "missed_return", None)
        else:
            close("open", "open", None)
    return stints


def list_label(stint: Stint) -> str:
    """'10-day', '10-day → 60-day', or '15-day / 10-day' on one day."""
    seen: list[tuple[int, dt.date, str]] = []
    for length, when, how in stint.lists:
        if seen and seen[-1][0] == length:
            continue
        seen.append((length, when, how))
    if not seen:
        base = "injured list"
    elif len(seen) == 1:
        base = f"{seen[0][0]}-day"
    else:
        same_day = len({when for _, when, _how in seen}) == 1
        transferred = any(how == "transfer" for _length, _when, how in seen)
        sep = " / " if same_day and not transferred else " → "
        base = sep.join(f"{length}-day" for length, _when, _how in seen)
    return base
