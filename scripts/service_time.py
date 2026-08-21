"""
service_time.py
----------------
Core logic for estimating MLB service time from a player's roster
transaction history.

BACKGROUND / RULES MODELED
===========================
Major League service time is credited for each day a player spends on a
Major League team's active roster (currently 26 players) or on that team's
Major League injured list (10-day / 15-day / 60-day IL), during the period
from that season's Opening Day through the end of the championship season
(the last day of the regular season, including any tiebreaker games).

Key thresholds:
  * 172 days of service = 1 full credited year (a player cannot earn more
    than one full year of credit in a single season, even though a full
    season is a few days longer than 172).
  * 3.000 years  -> arbitration eligible ("arb-eligible").
  * 2.000-2.172 years AND among the top ~22% in service time of all players
    with between two and three years of service -> "Super Two", also
    arbitration eligible a year early. Computing the real Super Two cutoff
    requires league-wide data for the full 2-3 year bucket each off-season,
    which MLB/the union does not publish in real time, so this tool only
    FLAGS a player as a "possible Super Two" if they fall in the
    historically common qualifying window (SUPER_TWO_HEURISTIC_MIN_DAYS+)
    and otherwise reports plain accrued years. It should not be relied on
    as an authoritative Super Two determination.
  * 6.000+ years -> free agency eligible.

WHAT COUNTS / WHAT DOESN'T (approximated from transaction descriptions)
=========================================================================
Days START accruing on transactions such as: contract purchase/selected,
recalled, activated, reinstated from IL, placed on active roster.

Days STOP accruing on transactions such as: optioned to the minors, sent
outright, released, designated for assignment (conservatively treated as a
stop, since a DFA'd player is off the active roster), claimed off waivers by
a club that immediately option/DFAs (handled the same as any other stop).

Trades do NOT stop accrual (the player is presumed to remain on an active
roster / IL unless a separate transaction says otherwise) -- only the team
attribution changes.

LIMITATIONS
===========
This is a best-effort estimate built from PUBLIC transaction records (the
MLB Stats API). It does not have access to the authoritative, official
service-time ledger MLB and the MLBPA jointly maintain, and will not
perfectly match it in every edge case (paternity/bereavement list handling,
the 2020 taxi squad, retroactive grievance adjustments, "phantom" days for
players kept in the minors to manipulate service time, etc.). Treat all
numbers here as informed estimates, not official figures.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FULL_YEAR_DAYS = 172  # days of service credited for a full year
FREE_AGENCY_YEARS = 6.000
ARBITRATION_YEARS = 3.000
SUPER_TWO_MIN_YEARS = 2.000
SUPER_TWO_MAX_YEARS = 3.000
# Historically the Super Two cutoff has fallen in roughly the 2.115-2.140
# range (~86-140 days into a player's third year). We use a conservative
# lower bound purely to FLAG candidates for manual review.
SUPER_TWO_HEURISTIC_MIN_DAYS = 86

# --- Shortened seasons -----------------------------------------------------
# The 2020 season ran roughly 66 days instead of the usual ~186. MLB and the
# MLBPA agreed service time that year would be prorated rather than credited
# as raw days: a player's actual days were scaled by 186/B (B = the season's
# length), so someone rostered all season earned a full year rather than ~66
# days. Without this, every player active in 2020 reads about 0.6 years low.
NORMAL_SEASON_SPAN_DAYS = 186
SHORTENED_SEASONS = {2020}

# --- Transaction coverage floor --------------------------------------------
# Measured empirically against the live API by sampling six players per season
# and counting transactions involving a major league club:
#
#     2005: 0    2006: 0    2007: 0    2008: 0
#     2009: 17   2010: 17   2011: 20   2012: 33   2013: 43
#
# The feed switches on in 2009. Before that the endpoint returns essentially
# nothing -- Jim Abbott was traded during 1995 and his feed is empty -- so a
# player who debuted earlier can never have his full history reconstructed and
# will always read low. We can't fix that, but we can be honest about it: any
# player whose debut predates this year is flagged history_complete=false.
TRANSACTION_COVERAGE_START_YEAR = 2009


def _prorate_shortened_season(season: "SeasonWindow", raw_days: int) -> int:
    """Scale raw active days for a shortened season per the 2020 agreement."""
    if season.year not in SHORTENED_SEASONS:
        return raw_days
    span = (season.end - season.start).days + 1
    if span <= 0 or span >= NORMAL_SEASON_SPAN_DAYS:
        return raw_days  # not actually shortened; leave untouched
    return int(round(raw_days * NORMAL_SEASON_SPAN_DAYS / span))

# Transaction description keywords (lower-cased substring match).
ACTIVE_START_KEYWORDS = [
    "selected the contract",
    "purchased the contract",
    "recalled",
    "activated",
    "reinstated from the",
    "reinstated from paternity",
    "reinstated from bereavement",
    "contract selected",
    "added to the active roster",
    "claimed off waivers",  # start clock for the claiming club; a
                             # subsequent option/DFA transaction will end it
    "returned to the active roster",
]

ACTIVE_STOP_KEYWORDS = [
    "optioned",
    "outrighted",
    "released",
    "designated for assignment",
    "sent outright",
    "sent to the minor",
    "placed on the",  # e.g. "placed on the paternity list" -- see note below
    "transferred to the 60-day",  # still technically counts, handled specially
]

# --- Why "elected free agency" is NOT a stop keyword ------------------------
# It is by far the most common way a player's roster tenure ends: 799 rows
# across the 1,356 cached transaction histories, phrased "RHP Some Name
# elected free agency." Adding it here looks obviously correct and is
# actively harmful, because there is no matching START keyword for the
# re-signing that usually follows -- the feed says "Team signed free agent
# RHP Some Name", and "signed" cannot be a start keyword since minor league
# deals ("...to a minor league contract", 350+ rows) use the same verb.
#
# Measured over those 1,356 cached players: treating it as a stop drops 272
# currently-rostered players' totals, because their clock stops at the first
# free agency election and never restarts. Max Scherzer loses 628 days (3.6
# years), Kenley Jansen 473, Nick Martinez 1,249. Every one of those is wrong.
#
# The retired-player problem this appears to solve (a career-ending election
# leaving an interval open forever) is handled instead by `accrual_ceiling`,
# which targets only players who are actually done playing. See
# build_global_active_intervals().

# Some "placed on the ... list" transactions still accrue service time
# (paternity, bereavement, restricted, and the 10/15/60-day injured list all
# count). Only a genuine optional assignment/outright/DFA/release should stop
# the clock. We special-case the accruing lists here so the generic
# "placed on the" stop keyword above doesn't wrongly zero them out.
NON_STOPPING_PLACEMENT_KEYWORDS = [
    "injured list",
    "il ",
    "paternity list",
    "bereavement",
    "restricted list",
    "family medical emergency",
]


@dataclass
class Transaction:
    date: dt.date
    description: str
    team: str | None = None


@dataclass
class SeasonWindow:
    year: int
    start: dt.date
    end: dt.date


@dataclass
class ServiceTimeResult:
    total_years: int
    remaining_days: int
    total_days: int
    is_free_agent_eligible: bool
    is_arbitration_eligible: bool
    is_super_two_candidate: bool
    by_season: dict = field(default_factory=dict)

    @property
    def formatted(self) -> str:
        return f"{self.total_years}.{self.remaining_days:03d}"


def _is_active_start(desc: str) -> bool:
    d = desc.lower()
    return any(k in d for k in ACTIVE_START_KEYWORDS)


def _is_active_stop(desc: str) -> bool:
    d = desc.lower()
    if any(nk in d for nk in NON_STOPPING_PLACEMENT_KEYWORDS):
        return False
    return any(k in d for k in ACTIVE_STOP_KEYWORDS)


def default_season_window(year: int) -> SeasonWindow:
    """
    Reasonable fallback Opening-Day / season-end estimates when the live
    schedule API isn't available. Real pipeline runs should fetch exact
    dates from /api/v1/seasons?sportId=1&season={year} instead.
    """
    return SeasonWindow(
        year=year,
        start=dt.date(year, 3, 28),
        end=dt.date(year, 10, 1),
    )


def build_global_active_intervals(
    transactions: list[Transaction],
    horizon_end: dt.date,
    accrual_floor: dt.date | None = None,
    accrual_ceiling: dt.date | None = None,
) -> list[tuple[dt.date, dt.date]]:
    """
    Walk a player's ENTIRE chronological transaction history (not bounded to
    a single season) and return [start, end] date ranges during which the
    player was accruing MLB service time.

    This is intentionally NOT computed per-season: a player who is called up
    once and simply stays on the roster for years afterward may have no
    further transactions at all, so status has to carry forward across
    season boundaries (offseasons themselves don't accrue service time, but
    a player doesn't need a fresh "activated" transaction every spring to
    still be considered active -- that only shows up implicitly by there
    being no option/release/DFA transaction in between).

    `horizon_end` caps an still-open ("currently active") interval, normally
    today's date or the end of the last season being considered.

    `accrual_floor` is the earliest date at which this player could possibly
    have been accruing MLB service time -- in practice their MLB debut date.
    This matters because the /transactions endpoint returns a player's ENTIRE
    tracked history, not just his major league one: high school showcases,
    college teams, minor league affiliates, and All-Star/Futures Game rosters
    all appear, and many are phrased with the same verbs as real major league
    moves ("Grand Canyon Antelopes activated SS Jacob Wilson"). Without a
    floor, those start the clock years early and the player is credited with
    service time he never earned. Intervals are clipped to begin no earlier
    than the floor, and intervals entirely before it are dropped.

    Transactions dated after `horizon_end` are ignored entirely, not just
    excluded from the trailing open interval. This matters for computing
    service time "as of" a past date (e.g. a prior Opening Day) from a
    transaction list that also contains later history: without it, a stop
    transaction (option, DFA, release) dated after `horizon_end` would still
    truncate an interval that, as of `horizon_end`, hadn't ended yet.

    `accrual_ceiling` is the mirror image of `accrual_floor`: the latest date
    at which this player could still have been accruing, in practice the end
    of the last season he appeared in. It exists because a career does not
    always end with a transaction this parser recognizes -- a player's final
    roster move is very often "elected free agency", which is deliberately
    NOT a stop keyword (see ACTIVE_STOP_KEYWORDS), so the interval it leaves
    open would otherwise run all the way to `horizon_end` and credit a
    retired player for every season since. Lowering the horizon to the
    ceiling closes that interval at the right place and drops the later
    transactions along with it.
    """
    if accrual_ceiling is not None:
        horizon_end = min(horizon_end, accrual_ceiling)

    txns = sorted(
        (t for t in transactions if t.date <= horizon_end), key=lambda t: t.date
    )

    intervals: list[tuple[dt.date, dt.date]] = []
    active_since: dt.date | None = None

    for t in txns:
        if _is_active_start(t.description):
            if active_since is None:
                active_since = t.date
        elif _is_active_stop(t.description):
            if active_since is not None:
                intervals.append((active_since, t.date - dt.timedelta(days=1)))
                active_since = None

    if active_since is not None:
        intervals.append((active_since, horizon_end))

    if accrual_floor is not None:
        clipped: list[tuple[dt.date, dt.date]] = []
        for start, end in intervals:
            if end < accrual_floor:
                continue  # entirely pre-debut (college, showcase, minors)
            clipped.append((max(start, accrual_floor), end))
        intervals = clipped

    return intervals


def _overlap_days(
    interval: tuple[dt.date, dt.date], window_start: dt.date, window_end: dt.date
) -> int:
    start = max(interval[0], window_start)
    end = min(interval[1], window_end)
    if end < start:
        return 0
    return (end - start).days + 1


def days_in_intervals(intervals: list[tuple[dt.date, dt.date]]) -> int:
    total = 0
    for start, end in intervals:
        if end >= start:
            total += (end - start).days + 1
    return total


def compute_service_time(
    transactions: list[Transaction],
    seasons: list[SeasonWindow],
    carry_in_active_first_season: bool = False,  # deprecated, kept for API compatibility; no-op
    horizon_end: dt.date | None = None,
    accrual_floor: dt.date | None = None,
    accrual_ceiling: dt.date | None = None,
) -> ServiceTimeResult:
    """
    Compute total MLB service time across multiple seasons.

    Status (active vs. not) is derived once from the player's FULL
    transaction history and then intersected with each season's date
    window -- this correctly handles players who go multiple seasons
    without a new transaction because they simply never leave the roster.
    """
    ordered_seasons = sorted(seasons, key=lambda s: s.year)
    if horizon_end is None:
        horizon_end = ordered_seasons[-1].end if ordered_seasons else dt.date.today()

    global_intervals = build_global_active_intervals(
        transactions,
        horizon_end,
        accrual_floor=accrual_floor,
        accrual_ceiling=accrual_ceiling,
    )

    total_days = 0
    by_season: dict[int, dict] = {}

    for season in ordered_seasons:
        season_intervals = [
            (max(s, season.start), min(e, season.end))
            for s, e in global_intervals
            if e >= season.start and s <= season.end
        ]
        days = sum(_overlap_days(iv, season.start, season.end) for iv in global_intervals)
        prorated_days = _prorate_shortened_season(season, days)
        credited_days = min(prorated_days, FULL_YEAR_DAYS)
        total_days += credited_days
        by_season[season.year] = {
            "raw_active_days": days,
            "prorated_days": prorated_days,
            "credited_days": credited_days,
            "intervals": [(s.isoformat(), e.isoformat()) for s, e in season_intervals],
        }

    total_years, remaining_days = divmod(total_days, FULL_YEAR_DAYS)
    fractional_years = total_years + remaining_days / FULL_YEAR_DAYS

    is_super_two_candidate = (
        SUPER_TWO_MIN_YEARS <= fractional_years < SUPER_TWO_MAX_YEARS
        and remaining_days >= SUPER_TWO_HEURISTIC_MIN_DAYS
    )

    return ServiceTimeResult(
        total_years=total_years,
        remaining_days=remaining_days,
        total_days=total_days,
        is_free_agent_eligible=fractional_years >= FREE_AGENCY_YEARS,
        is_arbitration_eligible=(fractional_years >= ARBITRATION_YEARS) or is_super_two_candidate,
        is_super_two_candidate=is_super_two_candidate,
        by_season=by_season,
    )
