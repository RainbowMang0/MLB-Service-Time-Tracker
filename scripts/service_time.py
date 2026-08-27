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
import re
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

# DOCUMENTATION ONLY -- these lists no longer drive matching.
# ============================================================================
# They describe the vocabulary the parser cares about, but editing them
# changes nothing: matching is done by _START_RE / _STOP_RE further down,
# because real feed wording puts the player's name between the verb and its
# object and plain substrings cannot match it. See the note above those
# patterns. Kept because they read more clearly than the regexes and because
# other modules import them.
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
    "returned to",  # "returned to <club> from <club>": Rule 5 returns and
                    # ended assignments. Always a departure -- see the note
                    # below _STOP_RE.
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


# --- Matching has to tolerate the player's name in the middle ---------------
# The keyword lists above read like the transaction descriptions but do not
# match them. Real feed wording puts the team first and the player's position
# and name between the verb and its object:
#
#     "Cleveland Guardians designated RHP Some Name for assignment."
#     "Boston Red Sox sent LHP Some Name outright to Worcester."
#     "New York Mets claimed C Some Name off waivers from Miami."
#     "Chicago Cubs placed RHP Some Name on the 15-day injured list."
#
# So every multi-word keyword ("designated for assignment", "sent outright",
# "claimed off waivers", "placed on the", "reinstated from the") silently
# matched nothing at all. Measured over the 64,635 cached descriptions, 11 of
# the 16 keywords never fired once and 61% of transactions were ignored.
#
# The damage was one-sided: DFA and outright are STOPS, so players removed
# from a roster kept accruing. Measured over the 1,322 cached histories,
# matching these correctly removes 22,133 phantom days from 229 players
# (17%) -- Tyler Austin 9.97y -> 3.02y, whose real service time is about
# three years. Players who were never DFA'd are untouched: Scherzer, Judge,
# Lindor, Goldschmidt, Jansen and Verlander do not move at all.
#
# The patterns below allow that interpolated name. Keep them anchored on the
# verb AND its object so "sent ... outright" cannot match "sent ... on a
# rehab assignment".
_NAME = r".{0,60}?"  # position + player name, bounded so it can't span clauses

_START_RE = re.compile(
    r"selected the contract|purchased the contract|contract selected"
    r"|\brecalled\b|\bactivated\b|\breinstated\b"
    r"|added to the active roster|returned to the active roster"
    rf"|claimed{_NAME}off waivers",
    re.IGNORECASE,
)

_STOP_RE = re.compile(
    r"\boptioned\b|\breleased\b|\boutrighted\b"
    rf"|designated{_NAME}for assignment"
    rf"|sent{_NAME}outright"
    rf"|sent{_NAME}to the minor"
    # "<Player> returned to <club A> from <club B>" -- see the note below.
    r"|returned to (?!the active roster).{0,60}?\bfrom\b",
    re.IGNORECASE,
)

# --- Why "returned to X from Y" is a stop (finding #14) ---------------------
# Found 2026-08-23 by probing Christian Cairo, who read 172 days for a 2025
# season he spent entirely in the minors. His feed:
#
#   2024-12-11  Atlanta Braves activated SS Christian Cairo.          <- START
#   2024-12-11  Atlanta Braves purchased contract ... in the Rule 5 Draft
#   2025-03-20  SS Christian Cairo returned to Cleveland Guardians
#               from Atlanta Braves.                                  <- nothing
#
# A Rule 5 selection opens an interval that nothing ever closes, because
# being returned to your original organization is phrased with a verb the
# parser did not know. The interval ran to the horizon and credited him a
# full season.
#
# The direction is unambiguous, and it was measured rather than assumed. Of
# the 53 "returned to" rows in the 65,109-row cache:
#
#     18   returned to an MLB club from an MLB club   (Rule 5 return)
#     34   returned to a minor league club from MLB   (assignment ends)
#      0   returned to an MLB club from a minor club  <- would be a START
#
# In every one the player LEAVES the club named after "from", and that club
# is a major league one in 52 of 53 (the 53rd involves no MLB club at all and
# is dropped by the club filter before it gets here). There is no case where
# this wording puts a player ON a major league roster, so it is always a stop.
#
# The negative lookahead keeps it off "returned to the active roster", which
# is a START -- no such row exists in the cache today, but the two readings
# are one word apart and the guard costs nothing.
#
# Measured over the 1,364 cached rostered players: 34 changed (2.5%), 3,337
# days removed, 0 added. Only-removes is the expected shape for a rule that
# can only close an interval. Jacob Latz -4.34 years, Brewer Hicklen -4.23,
# Brendon Little -3.31, Cairo -2.15.

# Placements that keep accruing. "disabled list" is the pre-2019 name for the
# injured list (1,419 rows in the cache, all before 2019 bar a handful) and
# must be treated identically. A rehab assignment is a minor league club
# taking the field with a player who is still on his MLB club's IL -- he is
# accruing the whole time, so it must never read as a demotion.
_NON_STOPPING_RE = re.compile(
    r"injured list|disabled list|rehab assignment|paternity|bereavement"
    r"|restricted list|family medical",
    re.IGNORECASE,
)


# --- Signing a major league contract starts the clock -----------------------
# A player who signs as a free agent and goes straight onto the active roster
# never receives an "activated" or "recalled" row, so without this his clock
# never opens. Measured against MLB's own 2014 rosters, this was the single
# largest error in the pipeline: Masahiro Tanaka, who signed in January 2014
# and spent the season on the Yankees' active roster, was credited zero days
# (under-credited on 27 of 28 sampled dates).
#
# The distinction that makes this safe is that the feed marks minor league
# deals explicitly. Across the 1,356 cached histories: 534 major league free
# agent signings, 828 that say "to a minor league contract". Only the former
# may start an MLB clock -- a minor league deal must never.
_SIGNING_RE = re.compile(r"signed free agent|signed as a free agent", re.IGNORECASE)
_MINOR_LEAGUE_DEAL_RE = re.compile(r"minor league (?:contract|deal)", re.IGNORECASE)


# --- Going on a major league IL STARTS the clock, it does not merely fail to
# --- stop it ----------------------------------------------------------------
# A player on his club's major league injured list is accruing service time by
# definition. Until now an IL placement was only listed as non-stopping, which
# is enough for a player who was already active but does nothing for one whose
# clock had already stopped. So a player optioned to the minors and later
# transferred to the 60-day IL stayed stopped, when in fact the transfer put
# him back on a major league list and he should have been accruing again.
#
# Measured against MLB's own 2018 rosters, this was the largest single
# remaining error: Ben Heller spent the season on the 60-day IL and was
# under-credited on 27 of 27 sampled dates, 27 of the 37 total.
#
# Affiliate placements (a Triple-A club's own injured list) are excluded by
# the MLB-club filter in update_service_time, so only real major league lists
# reach this. 6,607 such rows across the cached histories.
_IL_PLACEMENT_RE = re.compile(
    rf"(?:placed|transferred){_NAME}(?:on|to) the [\w\- ]*?(?:injured|disabled) list",
    re.IGNORECASE,
)


def is_active_start(desc: str) -> bool:
    """Public alias: does this description put a player ON a major league roster?"""
    return _is_active_start(desc)


def is_active_stop(desc: str) -> bool:
    """Public alias: does this description take him OFF one?"""
    return _is_active_stop(desc)


def _is_active_start(desc: str) -> bool:
    if _SIGNING_RE.search(desc):
        # A major league signing starts the clock; a minor league one does not.
        return not _MINOR_LEAGUE_DEAL_RE.search(desc)
    if _IL_PLACEMENT_RE.search(desc):
        return True
    return bool(_START_RE.search(desc))


def _is_active_stop(desc: str) -> bool:
    if _NON_STOPPING_RE.search(desc):
        return False
    return bool(_STOP_RE.search(desc))


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


# --- Finding #15: roster time before a player's first game --------------
# Service time is roster time, not playing time. A player selected or
# recalled on Monday who first appears on Tuesday earned Monday. 726 of the
# 1,331 cached players with a debut date have such a row, and carry-in threw
# every one of them away by dropping transactions dated before the debut.
#
# The window is measured, not chosen. The gap between that roster move and
# the debut is BIMODAL across the cache:
#
#     1-10 days    ~400 players   on the roster days before the first game
#    31-98 days       0 players   nothing at all
#   99-985 days    ~300 players   a different thing entirely -- a genuine
#                                 earlier roster stint in which he never
#                                 appeared, sometimes years before
#
# 45 days sits inside the empty band, so it separates the two populations
# rather than cutting through either. The second population may well be owed
# service time too, but it is a different question and needs its own
# measurement.
#
# SIGNINGS ARE EXCLUDED, and that is the whole safety property. Andrew Vaughn
# was drafted and signed by the White Sox on 2019-06-28 and debuted
# 2021-04-02; "Chicago White Sox signed 1B Andrew Vaughn" names a major
# league club and reads as a start. Reaching back to it would credit him two
# phantom years -- finding #2 all over again. Only an actual roster move
# counts here.
PRE_DEBUT_ROSTER_WINDOW_DAYS = 45

_ROSTER_MOVE_RE = re.compile(
    r"selected the contract|purchased the contract|contract selected"
    r"|\brecalled\b|\bactivated\b|\breinstated\b"
    r"|added to the active roster|returned to the active roster"
    rf"|claimed{_NAME}off waivers",
    re.IGNORECASE,
)


def roster_start_before_debut(
    transactions: list[Transaction],
    debut: dt.date | None,
    window_days: int = PRE_DEBUT_ROSTER_WINDOW_DAYS,
) -> dt.date | None:
    """
    The earliest major league ROSTER MOVE in the days just before `debut`,
    or None. Callers use it as the accrual floor in place of the debut.

    Deliberately not `_is_active_start`: that also matches a major league
    signing, and a draft signing two years before a debut is exactly the
    over-credit this window exists to avoid.
    """
    if debut is None:
        return None
    earliest = debut - dt.timedelta(days=window_days)
    dates = [
        t.date
        for t in transactions
        if earliest <= t.date < debut and _ROSTER_MOVE_RE.search(t.description)
    ]
    return min(dates) if dates else None


# --- Finding #16: a trade after a DFA puts a player on the new roster ----
# 1,258 "traded" rows across 751 cached players, and the parser read every
# one as neither a start nor a stop. David Huff was designated for assignment
# by San Francisco on 2014-06-06 and traded to the Yankees on 06-11; the DFA
# closed his clock and nothing reopened it, so he was credited nothing for a
# season MLB's roster shows him active in from mid-June onward. 16 of Yankees
# 2014's 36 wrong judgements were him alone.
#
# "traded = START" is NOT the rule, and the measurement is why. Grouped by
# what happened in the ten days before each trade:
#
#     166  after a DFA      a start is correct -- he joins the new 40-man
#      60  after an option  a start is WRONG -- he reports to the affiliate
#   1,013  neither          usually already active, where a start is a no-op
#
# So what matters is WHY the clock stopped, and the walk below now carries
# the last stop's kind. A trade reopens an interval only out of a DFA.
# Finding #19: a waiver claim that never put the player on the active roster.
#
# "Recalled FROM <minor league club>" is proof of where he was the day before,
# and it is the feed's own words rather than an assumption about MLB's
# vocabulary -- a player cannot be recalled from Norfolk unless he was at
# Norfolk. So when a claim's very next major league move is a recall, the claim
# put him on the 40-man and sent him down, and the days in between are phantom.
#
# ⚠️ The BROAD version of this -- close any interval that straddles any recall
# -- was measured and is catastrophically wrong. Caleb Ferguson's interval runs
# 2019-08-06 to 2022-05-16 across his Tommy John year, which he spent on the
# 60-day IL and therefore ACCRUING, and truncating at the recall deletes about
# three legitimate years. Patrick Corbin loses a whole real season the same way.
# The narrow rule fires only when nothing at all sits between the claim and the
# recall, which is what makes "he was in the minors the whole time" the only
# reading left.
_CLAIM_RE = re.compile(rf"claimed{_NAME}off waivers", re.IGNORECASE)
_RECALL_RE = re.compile(r"\brecalled\b", re.IGNORECASE)
# A waiver claim and a trade both move a player onto the new club's 40-man
# without saying whether he reported to the majors or the affiliate. The recall
# that follows answers it.
_ARRIVAL_RE = re.compile(
    rf"claimed{_NAME}off waivers|\btraded\b", re.IGNORECASE
)


def minor_league_stint_windows(
    transactions: list[Transaction],
) -> list[tuple[dt.date, dt.date]]:
    """(arrival date, recall date) pairs with no major league move in between.

    An arrival is a waiver claim or a trade -- both put a player on the new
    club's 40-man and neither says which roster he reported to. When the very
    next major league row is a RECALL, the feed answers it: he was in the
    minors, because that is the only place a recall can come from.

    The "nothing in between" constraint is what makes that the only available
    reading, and it is also what stops the Caleb Ferguson failure -- see the
    broad-rule warning in build_global_active_intervals.
    """
    # Grouped by date, not row by row. Yohan Ramirez was activated AND recalled
    # from Indianapolis on the same day, so a row-by-row walk fired or did not
    # depending on which of the two the API happened to list first -- the exact
    # sort-order dependence finding #10 was about. A recall anywhere in the next
    # date's rows proves where he was, whatever else shares that date.
    by_date: dict[dt.date, list[str]] = {}
    for t in transactions:
        by_date.setdefault(t.date, []).append(t.description)

    dates = sorted(by_date)
    out = []
    for day, nxt_day in zip(dates, dates[1:]):
        arrived = any(_ARRIVAL_RE.search(d) for d in by_date[day])
        recalled = any(_RECALL_RE.search(d) for d in by_date[nxt_day])
        if arrived and recalled:
            out.append((day, nxt_day))
    return out


# Old name, kept so nothing that imported it breaks.
claim_then_recall_windows = minor_league_stint_windows


_TRADE_RE = re.compile(r"\btraded\b", re.IGNORECASE)
_DFA_RE = re.compile(rf"designated{_NAME}for assignment", re.IGNORECASE)


def is_trade(desc: str) -> bool:
    return bool(_TRADE_RE.search(desc))


def build_global_active_intervals(
    transactions: list[Transaction],
    horizon_end: dt.date,
    accrual_floor: dt.date | None = None,
    accrual_ceiling: dt.date | None = None,
    presume_active_from: dt.date | None = None,
    claim_recall_is_minors: bool = True,
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

    `presume_active_from` (the "carry-in" rule) treats the player as already
    on a roster at that date -- in practice his MLB debut -- rather than
    waiting for a start transaction to open the first interval. Without it,
    a player is presumed OFF the roster until the feed explicitly says he is
    on it, which silently zeroes out anyone who simply stayed put:

        Justin Verlander debuted 2005-07-04 and sat on Detroit's roster
        without being optioned, DFA'd, released or injured until 2015. His
        first major-league transaction of ANY kind is 2015-04-08. Ten
        seasons -- six of them inside the era where coverage is known to be
        good -- produced no interval at all, and he read 11.000 against a
        published 20.002.

    This is the exact mirror of `accrual_ceiling`, which already presumes a
    player stayed on the roster to the end of his last season rather than
    demanding a transaction to prove it.

    The risk is over-crediting, which is the failure mode behind every revert
    in this project, so note what bounds it. `accrual_floor` stops the
    presumption before the debut; `accrual_ceiling` stops it after the last
    season played; and in between, any option, DFA, release or outright in
    the feed closes the interval normally. What is left exposed is a player
    who left an MLB roster in a way the feed does not record -- which is
    mostly a pre-2009 coverage problem, since 2009+ demotions are reliably
    present. Max Scherzer is the live example: he was optioned down and
    recalled during 2008, neither move is in the feed, so carry-in credits
    his whole 2008 rather than the 79 days he earned.

    Transactions dated before `presume_active_from` are dropped: nothing that
    happened before a player's major league debut can bear on whether he was
    on a major league roster after it, and leaving them in would let a
    pre-debut stop close the carried-in interval before it began.
    """
    if accrual_ceiling is not None:
        horizon_end = min(horizon_end, accrual_ceiling)

    if presume_active_from is not None and presume_active_from > horizon_end:
        presume_active_from = None

    txns = sorted(
        (
            t
            for t in transactions
            if t.date <= horizon_end
            and (presume_active_from is None or t.date >= presume_active_from)
        ),
        key=lambda t: t.date,
    )

    intervals: list[tuple[dt.date, dt.date]] = []
    active_since: dt.date | None = presume_active_from

    # Grouped by DATE, not walked row by row, because the feed gives no
    # intra-day order and the answer used to depend on one.
    #
    # Ben Bowden, 2022-04-29, two rows on the same day:
    #     "Tampa Bay Rays claimed LHP Ben Bowden off waivers"   START
    #     "Tampa Bay Rays optioned LHP Ben Bowden to Durham"    STOP
    #
    # Walked start-first he is credited 0 days; walked stop-first the stop
    # is a no-op (he was already optioned by Colorado in March), the start
    # opens an interval, and nothing ever closes it -- 160 phantom days to
    # the end of the season. Python's sort is stable, so which one happened
    # was decided by the order the API returned two rows in.
    #
    # When a date carries both, THE STOP WINS: the player ends that day off
    # the active roster. Order-independent, and measured rather than
    # reasoned -- of 223 same-date start/stop pairs in the cached histories,
    # 212 end in an option, a DFA or an outright:
    #
    #     127  activated from the IL   + optioned
    #      61  claimed off waivers     + optioned
    #      32  recalled                + optioned
    #      24  contract selected       + optioned
    #
    #     "Cincinnati Reds activated LHP Sam Moll from the 15-day injured
    #      list."  +  "Cincinnati Reds optioned LHP Sam Moll to Louisville
    #      Bats."   -- same day. He is in Triple-A that night.
    #
    # A REJECTED RULE, recorded so it is not retried: treat the pair as a
    # wash leaving the player in whatever state he was already in. It reads
    # well (claimed-then-optioned never reaches the roster; optioned-then-
    # recalled never leaves it) and it is wrong where it matters most. A
    # player activated off the IL is already accruing, so "unchanged" keeps
    # him accruing through an option -- and that is the commonest pair of
    # the four. Measured across the cache it ADDED 5,623 days. Stop-wins
    # only ever removes them.
    #
    # The cost is a genuine paper move -- optioned and recalled the same
    # day, never actually leaving -- which now reads as a stop. Rare, and
    # in the safe direction.
    by_date: dict[dt.date, list[Transaction]] = {}
    for t in txns:
        by_date.setdefault(t.date, []).append(t)

    # Why the clock last stopped, which decides whether a later trade
    # reopens it. See the finding #16 note above.
    last_stop_kind: str | None = None

    for day in sorted(by_date):
        rows = by_date[day]
        has_start = any(_is_active_start(r.description) for r in rows)
        has_stop = any(_is_active_stop(r.description) for r in rows)
        has_trade = any(is_trade(r.description) for r in rows)

        if has_stop:
            last_stop_kind = (
                "dfa" if any(_DFA_RE.search(r.description) for r in rows) else "other"
            )
            if active_since is not None:
                intervals.append((active_since, day - dt.timedelta(days=1)))
                active_since = None
        elif has_start:
            if active_since is None:
                active_since = day
        elif has_trade and active_since is None and last_stop_kind == "dfa":
            # A DFA'd player who is traded joins the new club's 40-man; an
            # optioned one reports to its affiliate. Only the first reopens.
            active_since = day
            last_stop_kind = None

    if active_since is not None:
        intervals.append((active_since, horizon_end))

    if accrual_floor is not None:
        clipped: list[tuple[dt.date, dt.date]] = []
        for start, end in intervals:
            if end < accrual_floor:
                continue  # entirely pre-debut (college, showcase, minors)
            clipped.append((max(start, accrual_floor), end))
        intervals = clipped

    # Finding #19: a waiver claim whose very next major league move is a recall
    # never put the player on the active roster. Applied last, so it trims an
    # interval the walk has already built rather than complicating the walk.
    if claim_recall_is_minors:
        for arrival, recall in minor_league_stint_windows(transactions):
            trimmed = []
            for start, end in intervals:
                # Only the interval the claim itself opened, and only when the
                # recall falls inside it -- an interval that merely overlaps the
                # window for some other reason is left alone.
                if start == arrival and start < recall <= end:
                    trimmed.append((recall, end))
                else:
                    trimmed.append((start, end))
            intervals = trimmed

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
    presume_active_from_debut: bool = False,
    horizon_end: dt.date | None = None,
    accrual_floor: dt.date | None = None,
    accrual_ceiling: dt.date | None = None,
    seasons_with_appearances: set[int] | None = None,
    carry_in_active_first_season: bool | None = None,  # deprecated alias
) -> ServiceTimeResult:
    """
    Compute total MLB service time across multiple seasons.

    Status (active vs. not) is derived once from the player's FULL
    transaction history and then intersected with each season's date
    window -- this correctly handles players who go multiple seasons
    without a new transaction because they simply never leave the roster.

    `presume_active_from_debut` enables the carry-in rule: the player is
    treated as on a roster from `accrual_floor` (his debut) onward instead of
    accruing nothing until some start transaction happens to fire. See
    build_global_active_intervals for what bounds the risk. It requires a
    floor -- with no debut date there is no defensible place to start
    presuming, so it is silently inert.

    `carry_in_active_first_season` is the old name for this. It spent a long
    time as a documented no-op; if passed it is honoured as an alias.

    `seasons_with_appearances` is the set of seasons in which MLB's own
    year-by-year splits show the player appearing. When given, a season is
    credited NOTHING if all three hold: no transaction is dated inside it,
    he has no appearance in it, and the only reason he looked active was an
    interval carried or presumed in from elsewhere.

    That guard exists because the presumption is unbounded without it.
    Erick Almonte debuted in 2001, played about thirty major league games,
    and had no transaction of any kind until 2011 -- so carry-in credited
    him 2005 through 2010 at the full 172-day cap, six consecutive seasons
    with no stat line and no transaction in any of them, for 6.064 years.
    Measured across the database: 57 players, 112 seasons, 19,264 days --
    112 player-years -- and 96% of it falls in 2005-2008, exactly where the
    transaction feed is thinnest.

    Note what this does NOT do. It is evidence-based, not era-based: nothing
    here mentions a cutoff year, and the concentration in the sparse era is
    a result rather than an assumption. Verlander and Scherzer keep every
    presumed season, because their splits place them on a club in all of
    them -- no reference player moves at all.

    The accepted cost is a player who spent a whole season on the injured
    list without a single dated transaction: no appearance, nothing to
    corroborate him, and his year is dropped. Rare enough not to show up in
    the modern era at all (one season in 2010, four in 2009, none after).
    """
    if carry_in_active_first_season is not None:
        presume_active_from_debut = carry_in_active_first_season
    ordered_seasons = sorted(seasons, key=lambda s: s.year)
    if horizon_end is None:
        horizon_end = ordered_seasons[-1].end if ordered_seasons else dt.date.today()

    global_intervals = build_global_active_intervals(
        transactions,
        horizon_end,
        accrual_floor=accrual_floor,
        accrual_ceiling=accrual_ceiling,
        presume_active_from=accrual_floor if presume_active_from_debut else None,
    )

    total_days = 0
    by_season: dict[int, dict] = {}
    years_with_transactions = {t.date.year for t in transactions}

    for season in ordered_seasons:
        season_intervals = [
            (max(s, season.start), min(e, season.end))
            for s, e in global_intervals
            if e >= season.start and s <= season.end
        ]
        days = sum(_overlap_days(iv, season.start, season.end) for iv in global_intervals)
        if (
            seasons_with_appearances is not None
            and days > 0
            and season.year not in years_with_transactions
            and season.year not in seasons_with_appearances
        ):
            # Nothing in this season says he was on a major league roster.
            days = 0
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
