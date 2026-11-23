"""Rookie eligibility from season totals and active-roster days.

Previous seasons count until they exceed 130 at-bats, 50 innings, or 45
active-roster days. Injured-list time does not count. Before 2020,
September 1 through the end of the season does not count; from 2020 on it
does. A day range that crosses 45 is unconfirmed.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

import service_time
from il_stints import SeasonWindows, build_stints
from mlb_clubs import MLB_CLUB_IDS, involves_mlb_club
from season_stat_tables import ip_to_outs

AB_LIMIT = 130
IP_OUTS_LIMIT = 50 * 3
DAY_LIMIT = 45
SEPTEMBER_COUNTS_FROM = 2020

_NAME = r".{0,80}?"
_ON_RE = re.compile(
    r"selected the contract|purchased the contract|contract selected"
    r"|\brecalled\b|\bactivated\b|\breinstated\b"
    r"|added to the active roster|returned to the active roster"
    rf"|claimed{_NAME}off waivers",
    re.IGNORECASE,
)
_OFF_RE = re.compile(
    r"\boptioned\b|\breleased\b|\boutrighted\b|\bretired\b"
    rf"|designated{_NAME}for assignment"
    rf"|sent{_NAME}outright"
    rf"|sent{_NAME}to the minor"
    rf"|reassigned{_NAME}to the minor"
    rf"|placed{_NAME}on the restricted list"
    r"|returned to (?!the active roster).{0,80}?\bfrom\b"
    r"|\b(?:elected|declared) free agency\b",
    re.IGNORECASE,
)
_LEAVE_RE = re.compile(
    rf"placed{_NAME}on the (?:paternity|bereavement|family medical|suspended)",
    re.IGNORECASE,
)
_FA_RE = re.compile(r"elected free agency|declared free agency", re.IGNORECASE)
_ASSIGNED_RE = re.compile(r"\bassigned to\b", re.IGNORECASE)
_REHAB_RE = re.compile(r"rehab", re.IGNORECASE)
_CLAIM_OR_TRADE_RE = re.compile(
    rf"claimed{_NAME}off waivers|\btraded\b", re.IGNORECASE,
)
_RECALL_RE = re.compile(r"\brecalled\b", re.IGNORECASE)
_MILITARY_RE = re.compile(r"\bmilitary\b", re.IGNORECASE)

_WINDOWS: SeasonWindows | None = None


@dataclass
class RookieStatus:
    state: str
    grad_year: int
    at_bats: int | None = None
    innings_outs: int | None = None
    days_low: int | None = None
    days_high: int | None = None
    crossed: tuple[str, ...] = ()
    september_2020: bool = False

    @property
    def days_exact(self) -> int | None:
        if self.days_low is None or self.days_low != self.days_high:
            return None
        return self.days_low


@dataclass
class _Clock:
    certain: set[dt.date] = field(default_factory=set)
    possible: set[dt.date] = field(default_factory=set)
    forced_unclear: bool = False

    def low(self) -> int:
        return len(self.certain)

    def high(self) -> int:
        if self.forced_unclear and self.low() <= DAY_LIMIT:
            return DAY_LIMIT + 1 + self.low()
        return len(self.certain | self.possible)


@dataclass
class _Span:
    start: dt.date
    end: dt.date
    clubs: list[tuple[dt.date, int]]


def rookie_season(today: dt.date, windows: SeasonWindows) -> int:
    """During a championship season, that season. After it ends, the next one."""
    this = windows.years.get(today.year)
    if this is not None and today <= this["end"]:
        return today.year
    if this is not None and today > this["end"]:
        return today.year + 1
    prev = windows.years.get(today.year - 1)
    if prev is not None and today > prev["end"]:
        return today.year
    return today.year


def _windows() -> SeasonWindows:
    global _WINDOWS
    if _WINDOWS is None:
        _WINDOWS = SeasonWindows.load()
    return _WINDOWS


def _as_date(value) -> dt.date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _mlb_id(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value in MLB_CLUB_IDS:
        return value
    return None


def _row_club(row: dict) -> int | None:
    for key in ("to_team_id", "team_id", "from_team_id"):
        club = _mlb_id(row.get(key))
        if club is not None:
            return club
    return None


def _minor_assignment(row: dict) -> bool:
    desc = row.get("description") or ""
    if not _ASSIGNED_RE.search(desc) or _REHAB_RE.search(desc):
        return False
    to_id = row.get("to_team_id")
    return isinstance(to_id, int) and not isinstance(to_id, bool) and to_id not in MLB_CLUB_IDS


def _keep(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    desc = row.get("description") or ""
    if _FA_RE.search(desc) or _minor_assignment(row):
        return True
    return involves_mlb_club(row)


def _effect(rows: list[dict]) -> str:
    off = False
    on = False
    for row in rows:
        desc = row.get("description") or ""
        if _minor_assignment(row) or _OFF_RE.search(desc) or service_time._IL_PLACEMENT_RE.search(desc) or _LEAVE_RE.search(desc):
            off = True
        elif _ON_RE.search(desc):
            on = True
    if off:
        return "off"
    if on:
        return "on"
    return "none"


def _club_on(clubs: list[tuple[dt.date, int]], day: dt.date) -> int | None:
    current = None
    for when, club in clubs:
        if when <= day:
            current = club
        else:
            break
    return current


def _claim_recall_gaps(by_date: dict[dt.date, list[dict]]) -> list[tuple[dt.date, dt.date]]:
    """A claim or trade whose next row is a recall never reached the active roster."""
    dates = sorted(by_date)
    gaps = []
    for day, nxt in zip(dates, dates[1:]):
        descs = [row.get("description") or "" for row in by_date[day]]
        nxt_descs = [row.get("description") or "" for row in by_date[nxt]]
        if any(_CLAIM_OR_TRADE_RE.search(d) for d in descs) and any(_RECALL_RE.search(d) for d in nxt_descs):
            gaps.append((day, nxt))
    return gaps


def _spans(rows: list[dict], today: dt.date) -> tuple[list[_Span], set[dt.date]]:
    kept = [row for row in rows if _keep(row) and _as_date(row.get("date")) and _as_date(row.get("date")) <= today]
    by_date: dict[dt.date, list[dict]] = {}
    for row in kept:
        by_date.setdefault(_as_date(row.get("date")), []).append(row)
    spans: list[_Span] = []
    active: dt.date | None = None
    clubs: list[tuple[dt.date, int]] = []
    for day in sorted(by_date):
        club = None
        for row in by_date[day]:
            found = _row_club(row)
            if found is not None:
                club = found
        effect = _effect(by_date[day])
        if effect == "off":
            if active is not None:
                end = day - dt.timedelta(days=1)
                if end >= active:
                    spans.append(_Span(active, end, list(clubs)))
            active = None
            clubs = []
        elif effect == "on" and active is None:
            active = day
            clubs = [(day, club)] if club is not None else []
        elif club is not None and active is not None and (not clubs or clubs[-1][1] != club):
            clubs.append((day, club))
    if active is not None:
        spans.append(_Span(active, today, clubs))
    on_dates = {span.start for span in spans}
    trimmed: list[_Span] = []
    gaps = _claim_recall_gaps(by_date)
    for span in spans:
        start = span.start
        for arrival, recall in gaps:
            if start == arrival and start < recall <= span.end:
                start = recall
        if span.end >= start:
            trimmed.append(_Span(start, span.end, span.clubs))
    return trimmed, on_dates


def _in_season(day: dt.date, club: int | None, windows: SeasonWindows, count_2020_september: bool) -> bool:
    bounds = windows.for_club(day.year, club)
    if bounds is None or not (bounds[0] <= day <= bounds[1]):
        return False
    if day.month >= 9 and day.year < SEPTEMBER_COUNTS_FROM:
        return False
    if day.month >= 9 and day.year == 2020 and not count_2020_september:
        return False
    return True


def _dates_between(start: dt.date, end: dt.date):
    if end < start:
        return
    day = start
    step = dt.timedelta(days=1)
    while day <= end:
        yield day
        day += step


def _add_dates(target: set[dt.date], start: dt.date, end: dt.date, clubs, windows, through: int, september: bool) -> None:
    for day in _dates_between(start, end):
        if day.year > through:
            continue
        club = _club_on(clubs, day) if clubs else None
        if _in_season(day, club, windows, september):
            target.add(day)


def _season_bounds(windows: SeasonWindows, year: int) -> tuple[dt.date, dt.date] | None:
    return windows.for_club(year, None)


def _apply_il(clock: _Clock, stints, on_dates: set[dt.date], windows: SeasonWindows, today: dt.date, through: int, september: bool) -> None:
    for stint in stints:
        if stint.kind == "exact" and stint.end is not None:
            end = stint.end - dt.timedelta(days=1)
            remove_end = end
            tail_from = None
        else:
            remove_end = stint.last_evidence
            tail_from = stint.last_evidence + dt.timedelta(days=1)
        clubs = [(stint.start, stint.clubs[0])] if stint.clubs else []
        removing: set[dt.date] = set()
        _add_dates(removing, stint.start, remove_end, clubs, windows, through, september)
        clock.certain -= removing
        clock.possible -= removing
        if tail_from is None:
            continue
        bounds = _season_bounds(windows, stint.start.year)
        if bounds is None:
            clock.forced_unclear = True
            continue
        later = [day for day in on_dates if day > stint.last_evidence]
        tail_end = bounds[1]
        if later:
            tail_end = min(tail_end, min(later) - dt.timedelta(days=1))
        in_season = today <= bounds[1]
        if stint.kind == "open" and in_season:
            continue
        if tail_end >= tail_from:
            _add_dates(clock.possible, tail_from, tail_end, clubs, windows, through, september)


def _played_years(player: dict) -> set[int]:
    years = set()
    for season in player.get("seasons") or []:
        if not isinstance(season, dict):
            continue
        try:
            year = int(season.get("y"))
            days = int(season.get("d") or 0)
        except (TypeError, ValueError):
            continue
        if days > 0:
            years.add(year)
    return years


def _stat_years(stats: dict) -> set[int]:
    years = set()
    for key in stats or {}:
        try:
            years.add(int(key))
        except (TypeError, ValueError):
            continue
    return years


def _has_value(row: dict, key: str) -> bool:
    value = row.get(key)
    return not isinstance(value, bool) and value not in (None, "")


def _as_int(value) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _group_total(group: dict | None, key: str, parse) -> tuple[int, bool]:
    """(total, known). A missing group is a known zero. A broken value is not."""
    if not isinstance(group, dict):
        return 0, True
    if _has_value(group, key):
        parsed = parse(group.get(key))
        if parsed is None:
            return 0, False
        return parsed, True
    splits = [row for row in (group.get("splits") or []) if isinstance(row, dict)]
    if not splits:
        return (0, False) if group else (0, True)
    total = 0
    saw = False
    for row in splits:
        if not _has_value(row, key):
            continue
        saw = True
        parsed = parse(row.get(key))
        if parsed is None:
            return 0, False
        total += parsed
    if not saw:
        return 0, False
    return total, True


def _year_group(stats: dict, year: int, name: str) -> dict | None:
    rec = stats.get(str(year))
    if rec is None:
        rec = stats.get(year)
    if not isinstance(rec, dict):
        return None
    group = rec.get(name)
    return group if isinstance(group, dict) else None


def counting_stats(player: dict, through: int) -> tuple[int | None, int | None, tuple[int, ...]]:
    """At-bats, outs, and service years with no stat line."""
    stats = player.get("season_stats") if isinstance(player.get("season_stats"), dict) else {}
    recorded = {year for year in _stat_years(stats) if year <= through}
    gaps = tuple(sorted(year for year in _played_years(player) if year <= through and year not in recorded))
    if not recorded:
        return 0, 0, gaps
    at_bats = 0
    outs = 0
    ab_known = True
    ip_known = True
    for year in recorded:
        ab, ab_ok = _group_total(_year_group(stats, year, "hitting"), "atBats", _as_int)
        ip, ip_ok = _group_total(_year_group(stats, year, "pitching"), "inningsPitched", ip_to_outs)
        if ab_ok:
            at_bats += ab
        else:
            ab_known = False
        if ip_ok:
            outs += ip
        else:
            ip_known = False
    return (at_bats if ab_known else None), (outs if ip_known else None), gaps


def _appearance_years(player: dict, through: int) -> set[int]:
    stats = player.get("season_stats") if isinstance(player.get("season_stats"), dict) else {}
    found = set()
    for year in _stat_years(stats):
        if year > through:
            continue
        hit = _year_group(stats, year, "hitting") or {}
        pitch = _year_group(stats, year, "pitching") or {}
        games = 0
        for group, key in ((hit, "gamesPlayed"), (pitch, "gamesPlayed")):
            if isinstance(group, dict) and _has_value(group, key):
                games += _as_int(group.get(key)) or 0
        ab, _ab_ok = _group_total(hit if hit else None, "atBats", _as_int)
        ip, _ip_ok = _group_total(pitch if pitch else None, "inningsPitched", ip_to_outs)
        if games > 0 or ab > 0 or ip > 0:
            found.add(year)
    return found


def _service_years(player: dict) -> dict[int, int]:
    out = {}
    for season in player.get("seasons") or []:
        if not isinstance(season, dict):
            continue
        try:
            out[int(season.get("y"))] = int(season.get("d") or 0)
        except (TypeError, ValueError):
            continue
    return out


def _mlb_through(player: dict, through: int) -> bool:
    debut = _as_date(player.get("mlb_debut"))
    if debut is not None and debut.year <= through:
        return True
    if any(year <= through for year in _played_years(player)):
        return True
    stats = player.get("season_stats") if isinstance(player.get("season_stats"), dict) else {}
    return any(year <= through for year in _stat_years(stats))


def active_clock(
    player: dict,
    rows: list[dict] | None,
    today: dt.date,
    windows: SeasonWindows,
    through: int,
    *,
    count_2020_september: bool = True,
) -> _Clock:
    clock = _Clock()
    if not _mlb_through(player, through):
        return clock
    kept = [row for row in (rows or []) if isinstance(row, dict) and _keep(row) and _as_date(row.get("date"))]
    if rows is None or not kept:
        clock.forced_unclear = True
        return clock
    if any(_MILITARY_RE.search(row.get("description") or "") for row in rows if isinstance(row, dict)):
        clock.forced_unclear = True
    spans, on_dates = _spans(rows, today)
    for span in spans:
        _add_dates(clock.certain, span.start, span.end, span.clubs, windows, through, count_2020_september)
    needed = {year for year in (_played_years(player) | _stat_years(player.get("season_stats") or {})) if year <= through}
    if any(year not in windows.years for year in needed):
        clock.forced_unclear = True
    service = _service_years(player)
    stats = player.get("season_stats") if isinstance(player.get("season_stats"), dict) else {}
    if stats or service:
        for year in {day.year for day in clock.certain}:
            if year in _stat_years(stats) or service.get(year, 0) > 0:
                continue
            if stats and year not in _stat_years(stats) and service.get(year, 0) <= 0:
                clock.certain = {day for day in clock.certain if day.year != year}
    debut = _as_date(player.get("mlb_debut"))
    kept_dates = sorted({_as_date(row.get("date")) for row in rows if _keep(row) and _as_date(row.get("date"))})
    if debut is not None and kept_dates and debut < kept_dates[0]:
        _add_dates(clock.possible, debut, kept_dates[0] - dt.timedelta(days=1), [], windows, through, count_2020_september)
    elif debut is not None and not kept_dates:
        clock.forced_unclear = True
    if rows:
        stints = build_stints(rows, windows, today)
        _apply_il(clock, stints, on_dates, windows, today, through, count_2020_september)
    for year in _appearance_years(player, through):
        if any(day.year == year for day in clock.certain):
            continue
        bounds = _season_bounds(windows, year)
        if bounds is None:
            clock.forced_unclear = True
            continue
        start = max(bounds[0], debut) if debut and debut.year == year else bounds[0]
        _add_dates(clock.possible, start, bounds[1], [], windows, through, count_2020_september)
    clock.possible -= clock.certain
    return clock


def _with_gaps(at_bats, outs, gaps, clock: _Clock):
    if not gaps or at_bats is None or outs is None:
        return at_bats, outs
    if clock.forced_unclear:
        return None, None
    touched = {day.year for day in clock.certain | clock.possible}
    if any(year in touched for year in gaps):
        return None, None
    return at_bats, outs


def _stat_reasons(at_bats: int | None, outs: int | None) -> tuple[str, ...]:
    reasons = []
    if at_bats is not None and at_bats > AB_LIMIT:
        reasons.append("at_bats")
    if outs is not None and outs > IP_OUTS_LIMIT:
        reasons.append("innings")
    return tuple(reasons)


def _day_verdict(clock: _Clock) -> str:
    if clock.low() > DAY_LIMIT:
        return "over"
    if not clock.forced_unclear and clock.high() <= DAY_LIMIT:
        return "under"
    return "unclear"


def _overall(at_bats: int | None, outs: int | None, clock: _Clock) -> tuple[str, tuple[str, ...]]:
    reasons = _stat_reasons(at_bats, outs)
    if reasons:
        return "over", reasons + (("days",) if _day_verdict(clock) == "over" else ())
    stats_under = (
        at_bats is not None and outs is not None
        and at_bats <= AB_LIMIT and outs <= IP_OUTS_LIMIT
    )
    days = _day_verdict(clock)
    if days == "over":
        return "over", ("days",)
    if stats_under and days == "under":
        return "under", ()
    return "unclear", ()


def _bucket(state: str) -> str:
    if state == "eligible":
        return "rookie"
    if state in ("graduated", "veteran"):
        return "not"
    return "unclear"


def evaluate(
    player: dict,
    rows: list[dict] | None,
    today: dt.date,
    windows: SeasonWindows | None = None,
) -> RookieStatus:
    windows = windows or _windows()
    grad_year = rookie_season(today, windows) - 1
    before_ab, before_ip, before_gaps = counting_stats(player, grad_year - 1)
    after_ab, after_ip, after_gaps = counting_stats(player, grad_year)
    if _stat_reasons(before_ab, before_ip) and not before_gaps:
        return RookieStatus("veteran", grad_year, before_ab, before_ip)
    before_days = active_clock(player, rows, today, windows, grad_year - 1)
    after_days = active_clock(player, rows, today, windows, grad_year)
    before_ab, before_ip = _with_gaps(before_ab, before_ip, before_gaps, before_days)
    after_ab, after_ip = _with_gaps(after_ab, after_ip, after_gaps, after_days)
    before, _before_why = _overall(before_ab, before_ip, before_days)
    after, why = _overall(after_ab, after_ip, after_days)
    if after == "under":
        state = "eligible"
    elif after == "over" and before == "under":
        state = "graduated"
    elif after == "over":
        state = "veteran"
    else:
        state = "unconfirmed"
    bare = active_clock(player, rows, today, windows, grad_year, count_2020_september=False)
    bare_before = active_clock(player, rows, today, windows, grad_year - 1, count_2020_september=False)
    bare_after, _why = _overall(after_ab, after_ip, bare)
    bare_prior, _prior_why = _overall(before_ab, before_ip, bare_before)
    if bare_after == "under":
        bare_state = "eligible"
    elif bare_after == "over" and bare_prior == "under":
        bare_state = "graduated"
    elif bare_after == "over":
        bare_state = "veteran"
    else:
        bare_state = "unconfirmed"
    flagged = _bucket(state) != _bucket(bare_state)
    low, high = after_days.low(), after_days.high()
    if after_days.forced_unclear and after_days.low() <= DAY_LIMIT:
        high = None
    return RookieStatus(
        state, grad_year, after_ab, after_ip, low, high, why, flagged,
    )
