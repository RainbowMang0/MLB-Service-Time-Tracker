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
  * 2.000-3.000 years AND among the top ~22% in service time of all players
    with between two and three years of service -> "Super Two", also
    arbitration eligible a year early.

    THIS MODULE ONLY FLAGS CANDIDATES. The real cutoff is not a fixed
    threshold -- it falls wherever the class falls that year -- so what
    happens here is a cheap per-player heuristic with no league context.
    scripts/super_two.py computes the actual boundary from the whole
    population and overwrites the flag in a later pass; see
    apply_super_two() and the note in update_service_time._write_outputs().
    Do not read this flag as authoritative on its own.
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

import cba

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# These are no longer literals. Every threshold below is read from the CBA
# ruleset in config/cba/, because the 2022 agreement expires 2026-12-01 and
# service time, arbitration and free agency are the central issues in the
# dispute -- any of them could move. See scripts/cba.py.
#
# The module-level names are kept because other scripts import them, and they
# are bound to the DEFAULT ruleset so that behaviour is unchanged for every
# existing caller. Anything that needs to compute under a DIFFERENT agreement
# passes `ruleset=` to compute_service_time() rather than reading these.

_DEFAULT_RULES = cba.default()

FULL_YEAR_DAYS = _DEFAULT_RULES.require("service_time.days_per_credited_year")
FREE_AGENCY_YEARS = _DEFAULT_RULES.require("free_agency.credited_years_required")
ARBITRATION_YEARS = _DEFAULT_RULES.require("arbitration.standard_years_required")
SUPER_TWO_MIN_YEARS, SUPER_TWO_MAX_YEARS = _DEFAULT_RULES.require(
    "arbitration.super_two.service_range_years"
)

# --- Shortened seasons -----------------------------------------------------
# The 2020 season ran roughly 66 days instead of the usual ~186. MLB and the
# MLBPA agreed service time that year would be prorated rather than credited
# as raw days: a player's actual days were scaled by 186/B (B = the season's
# length), so someone rostered all season earned a full year rather than ~66
# days. Without this, every player active in 2020 reads about 0.6 years low.
#
# The span itself is read from the ruleset at the point of use, in
# _prorate_shortened_season(). A module constant bound to the default agreement
# would be the wrong number the moment a caller passes a different ruleset,
# which is the whole reason config/cba/ exists.
SHORTENED_SEASONS = set(_DEFAULT_RULES.shortened_seasons())

# --- Transaction coverage floor --------------------------------------------
# ⚠️ THIS IS NOT A CUTOFF, and nothing about a player's completeness is keyed
# to it any more. The original claim here -- that the feed returns nothing
# before 2009 -- was DISPROVED by scripts/probe_coverage.py, which pulled real
# major league rows from 2005-2008:
#
#     2005-01-13 | Pittsburgh Pirates purchased Jack Wilson.
#     2006-08-11 | Minnesota Twins activated LF Lew Ford from the 15-day DL.
#
# The original zeros were a sampling artifact: the sampled players had no
# professional existence before 2009, so zero rows was the expected result
# either way and proved nothing. Coverage THINS as you go back; it does not
# switch on. `_missing_seasons()` in update_service_time.py measures it per
# player instead, which is why a 2003 debut whose 2003 rows are in the feed
# now reads complete rather than "partial".
#
# What survives is a weak historical prior, used only where a genuinely
# arbitrary earliest year is needed: backfill_history.py's --min-year default.
# Nothing published to a reader may depend on it -- the disclaimer that used
# to quote it was removed for exactly that reason.
TRANSACTION_COVERAGE_START_YEAR = 2009


def _prorate_shortened_season(
    season: "SeasonWindow", raw_days: int, ruleset: "cba.Ruleset | None" = None
) -> int:
    """Scale raw active days for a shortened season per the 2020 agreement.

    Which seasons are shortened, and what span they scale to, come from the
    ruleset -- a future agreement could prorate a season the same way, and
    that should be a config edit rather than a code change.
    """
    rules = ruleset or _DEFAULT_RULES
    shortened = rules.shortened_seasons()
    if season.year not in shortened:
        return raw_days
    normal_span = shortened[season.year]
    span = (season.end - season.start).days + 1
    if span <= 0 or span >= normal_span:
        return raw_days  # not actually shortened; leave untouched
    return int(round(raw_days * normal_span / span))

# DOCUMENTATION ONLY -- these lists no longer drive matching.
# ============================================================================
# They describe the vocabulary the parser cares about, but editing them
# changes nothing: matching is done by _START_RE / _STOP_RE further down,
# because real feed wording puts the player's name between the verb and its
# object and plain substrings cannot match it. See the note above those
# patterns.
#
# Kept purely because they read more clearly than the regexes do. NOTHING
# IMPORTS THEM -- an earlier version of this note also claimed other modules
# did, which was never true and would have stopped a maintainer from touching
# them. If they ever stop matching what _START_RE / _STOP_RE actually do,
# delete them rather than leaving documentation that lies.
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

# --- Electing or declaring free agency stops the clock ----------------------
# It is the most common way a roster tenure ends. An earlier measurement
# (2026-11-06) refused to make a lone election a stop: 118 cached players
# would have lost a later season, because the re-signing was not always a
# row the parser read as a start. Major-league signings are starts now
# ("signed free agent" / "signed as a free agent"), and re-measuring the
# cached histories on 2026-11-08 with that in place moved 17 of 1,441
# players: 15 down, 2 up. The seasons that fall are ones with no major-league
# games (a year in another league, or a summer spent unsigned). A player who
# re-signs over the winter picks the clock back up on the signing.
#
# What restarts it after the election: a signing, a selection, a recall, an
# injured-list placement, or an assignment onto the major-league active
# roster. "Assigned to <club>" is that last one only when a later row shows
# he was actually on the active roster — see _mlb_assignment_reopens. A
# spring-training "assigned to the Yankees" is not, and neither is a
# minor-league assignment, an optional assignment, or an outright. The
# accrual ceiling still closes a career that ends with no election at all.
#
# Two related cases were already stops, and they stay stops:
#   * An election on the SAME DAY as an activation/signing. Clubs activate a
#     player off the injured list and elect free agency in one paperwork
#     day (Yency Almonte, 2024-11-04). The activation is not a return to the
#     roster; the election is the outcome, and same-day stop-wins applies.
#   * A minor-league release, outright, retirement, or assignment to a
#     minor-league club. Those rows name only the affiliate, so the MLB-club
#     filter used to drop them and an earlier major-league signing stayed
#     open for years (Rob Kaminsky, Ryan Jackson, D.J. Snelten).

# Some "placed on the ... list" transactions still accrue service time
# (paternity, bereavement, and the 10/15/60-day injured list all count). Only a
# genuine optional assignment/outright/DFA/release -- or, now, a restricted-list
# placement -- should stop the clock. We special-case the accruing lists here so
# the generic "placed on the" stop keyword above doesn't wrongly zero them out.
#
# THE RESTRICTED LIST IS NOT HERE ANY MORE (rules v7). Under MLB Rule 2(c)(6)
# and the standard glossaries a restricted-list player is not paid and does NOT
# earn service, so a placement STOPS the clock (it moved into _STOP_RE below).
# The handful of restricted-list spells that DO earn service -- paid domestic-
# violence administrative leave, the 2020 high-risk COVID opt-outs -- are not a
# property of the wording (the feed phrases them identically to a suspension),
# so they cannot be distinguished here. They are restored per player from the
# hand-kept, sourced data/service_time_exceptions.json, applied as
# `force_active_periods` in build_global_active_intervals.
NON_STOPPING_PLACEMENT_KEYWORDS = [
    "injured list",
    "il ",
    "paternity list",
    "bereavement",
    "family medical emergency",
]


@dataclass
class Transaction:
    date: dt.date
    description: str
    team: str | None = None
    # Set by the pipeline when this row assigns the player to a minor-league
    # club ("assigned to Durham Bulls"). The wording is not itself a stop --
    # "assigned to the Yankees" is a spring-training roster note -- but a
    # destination that is not a major-league club closes an open MLB stint.
    # Default false so every existing caller is unchanged.
    minor_assignment: bool = False
    # Set by the pipeline when this row assigns him to one of the 30 clubs
    # ("assigned to the Arizona Diamondbacks"). Not a start by itself: most
    # of those rows are spring-training notes. The walk restarts a stopped
    # clock only when a later row shows he was on that club's active roster.
    # Default false so every existing caller is unchanged.
    mlb_assignment: bool = False


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
    r"\boptioned\b|\breleased\b|\boutrighted\b|\bretired\b"
    rf"|designated{_NAME}for assignment"
    rf"|sent{_NAME}outright"
    rf"|sent{_NAME}to the minor"
    # "reassigned ... to the minor leagues" is how an end-of-year or
    # post-trade demotion is often phrased. It is a departure from the
    # major-league roster, the same fact as "sent ... to the minor".
    rf"|reassigned{_NAME}to the minor"
    # A restricted-list placement stops the clock (rules v7): the player is not
    # paid and earns no service. The exceptions that DO earn -- paid DV
    # administrative leave, 2020 opt-outs -- are restored per player from
    # data/service_time_exceptions.json, not from the wording, because the feed
    # phrases a leave and a suspension identically. "activated/reinstated ...
    # from the restricted list" is still a START (it matches _START_RE), so a
    # genuine return restarts the clock.
    rf"|placed{_NAME}on the restricted list"
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
    r"|family medical",
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
_FREE_AGENCY_RE = re.compile(r"\b(?:elected|declared) free agency\b", re.IGNORECASE)


def is_free_agency_election(desc: str) -> bool:
    """'elected/declared free agency' -- this stops the clock.

    See the note above ACTIVE_STOP_KEYWORDS. A same-day activation does
    not reopen it; the next recognized start (a signing, a recall, an
    injured-list placement, or an assignment onto the active roster) does.
    """
    return bool(_FREE_AGENCY_RE.search(desc or ""))


def row_opens_roster(desc: str, same_day: list[str]) -> bool:
    """Would this description open an MLB interval, given the other rows
    dated the same day?

    A bare "Team signed free agent Name" is a major-league signing and
    opens the clock. The feed often files that row NEXT TO a sibling that
    says "to a minor league contract" (D.J. Snelten 2020-11-16, Rangel
    Ravelo 2021-01-09). The sibling is the truth; the bare row must not
    open a phantom stint. A same-day recall, selection, or injured-list
    placement still opens one -- only the pure signing is suppressed.
    """
    if (
        _SIGNING_RE.search(desc)
        and not _MINOR_LEAGUE_DEAL_RE.search(desc)
        and not _START_RE.search(desc)
        and not _IL_PLACEMENT_RE.search(desc)
        and any(_MINOR_LEAGUE_DEAL_RE.search(other) for other in same_day)
    ):
        return False
    return _is_active_start(desc)


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
    dfa_limbo_days: int | None = None,
    season_starts: list[dt.date] | None = None,
    force_active_periods: list[tuple[dt.date, dt.date]] | None = None,
    force_inactive_periods: list[tuple[dt.date, dt.date]] | None = None,
    appearance_dates: list[dt.date] | None = None,
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
    roster move is very often "elected free agency". That election now stops
    the clock (see the note above ACTIVE_STOP_KEYWORDS). The ceiling is still
    what closes a career that ends some other way the parser does not read
    as a stop, so the open interval cannot run to `horizon_end` and credit a
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
        published x.yyy.

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

    `dfa_limbo_days` turns on the DFA grace window (rules v7). A player who is
    designated for assignment DURING the season keeps earning service until the
    move that resolves it (a trade, a waiver claim, an outright/option/other
    assignment, or a release), capped at this many days and never past the
    season (the season clip does that). None keeps the old behaviour -- the DFA
    stops the clock the day it happens -- which is what the direct unit tests
    exercise. It only adds days to a player who WAS active at the DFA; an
    offseason DFA, where no interval is open, adds nothing.

    `season_starts` are the clubs' Opening Days, used only to keep the grace
    window from crossing one. A player designated for assignment BEFORE a
    season's Opening Day earns no service for that season (OFRU; he was never on
    its active roster), so a spring DFA's limbo is clipped at the day before the
    next opener rather than spilling regular-season days he did not earn.

    `force_active_periods` and `force_inactive_periods` are hand-kept,
    per-player service-time exceptions (data/service_time_exceptions.json),
    applied after the walk. An active period is unioned in (paid DV
    administrative leave, a 2020 high-risk opt-out -- restricted-list spells the
    feed cannot tell from a suspension); an inactive period is cut out (a
    suspension the feed shows only as a restricted-list placement). Both are
    clipped to the same floor/ceiling/horizon as everything else.
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

    day_order = sorted(by_date)
    for idx, day in enumerate(day_order):
        rows = by_date[day]
        descs = [r.description for r in rows]
        has_start = any(row_opens_roster(d, descs) for d in descs)
        has_stop = any(_is_active_stop(d) for d in descs) or any(
            r.minor_assignment for r in rows
        )
        has_trade = any(is_trade(d) for d in descs)
        # Electing or declaring free agency beats an activation or signing,
        # same-day or not. The activation is the paperwork that lets him
        # elect; it is not a return to the roster (Yency Almonte,
        # 2024-11-04). And a LONE election -- no same-day start at all -- is
        # also a stop: without this, a player who sits out a season before
        # re-signing (Michael Conforto, elected 2021-11-03, unsigned until
        # 2023-01-06) keeps accruing through the gap season as phantom
        # service. Measured over the cached histories this removes phantom
        # days from Nick Martinez (~625d, a 2018 season spent in Japan before
        # re-signing in 2022), Conforto (~182d, the 2022 season he sat out)
        # and others with the same shape. The re-signing itself is already a
        # recognized start (`row_opens_roster`/_SIGNING_RE), so a player who
        # re-signs the same winter picks the clock back up at that signing.
        if any(is_free_agency_election(d) for d in descs):
            has_start = False
            has_stop = True

        # An assignment onto the major-league active roster restarts a stopped
        # clock, the same way a signing does. Same-day stop still wins (a
        # minor-league assignment, an option, an outright, a release, or the
        # free-agency election itself). See _mlb_assignment_reopens for why a
        # bare "assigned to <club>" is not enough.
        if (
            not has_stop
            and active_since is None
            and any(r.mlb_assignment for r in rows)
            and _mlb_assignment_reopens(idx, day_order, by_date)
        ):
            has_start = True

        if has_stop:
            is_dfa = any(_DFA_RE.search(r.description) for r in rows)
            # A same-day move that resolves the DFA: an outright, release or
            # option (a non-DFA stop), or a trade. A same-day claim/activation
            # is caught by has_start. When any of these shares the DFA's date
            # the outcome is already known that day, so there is no limbo to
            # credit (Waguespack 2021-03-10, Rico Garcia 2023-07-07, Juan Brito
            # 2026-08-03 -- an outright/option on the DFA day used to get a full
            # 7-day over-credit because the guard only checked has_start).
            has_non_dfa_stop = any(
                _is_active_stop(r.description) and not _DFA_RE.search(r.description)
                for r in rows
            )
            last_stop_kind = "dfa" if is_dfa else "other"
            if active_since is not None:
                close = day - dt.timedelta(days=1)
                # DFA grace window (rules v7): a player DFA'd in-season keeps
                # earning until the next move that resolves it, capped at
                # dfa_limbo_days. Only when the DFA stands alone that day -- any
                # same-day start/stop/trade already names the outcome, so there
                # is no limbo. The season clip later enforces "never past season
                # end"; here it is only capped at the horizon.
                if (
                    dfa_limbo_days
                    and is_dfa
                    and not has_start
                    and not has_trade
                    and not has_non_dfa_stop
                ):
                    limbo_end = day + dt.timedelta(days=dfa_limbo_days - 1)
                    nxt = _next_resolving_day(day_order, idx, by_date)
                    if nxt is not None:
                        limbo_end = min(limbo_end, nxt - dt.timedelta(days=1))
                    limbo_end = min(limbo_end, horizon_end)
                    # A spring DFA -- before a season's Opening Day -- earns no
                    # service for that season, so the grace window must not cross
                    # an opener that falls after the DFA. Without this a DFA in
                    # the days before Opening Day credited regular-season limbo
                    # days the player was never on the roster for (20 cached
                    # cases, e.g. 676724 2024-03-27, 596117 2026-03-25).
                    if season_starts:
                        later_openers = [s for s in season_starts if s > day]
                        if later_openers:
                            limbo_end = min(limbo_end, min(later_openers) - dt.timedelta(days=1))
                    if limbo_end > close:
                        close = limbo_end
                intervals.append((active_since, close))
                active_since = None
            # A trade dated the SAME DAY as the DFA joins him to the new
            # club's 40-man that day -- the DFA never actually left him off
            # a roster, so the clock must not stop at all. Without this, the
            # `elif has_trade` branch below can never fire (has_stop already
            # took the day), so the reopening only happened on a LATER day
            # that carried nothing but the trade, and a same-day DFA+trade
            # under-credited the gap (Jonah Heim, DFA and traded both on
            # 2026-05-04, under-credited ~146 days).
            if is_dfa and has_trade and not has_non_dfa_stop:
                active_since = day
                last_stop_kind = None
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

    # Hand-kept service-time exceptions (rules v7), applied last so they
    # override the generic walk for the few players whose restricted-list
    # placement the default rule gets wrong. A counts=true period is a paid
    # leave / opt-out that DID earn service; a counts=false period is a stretch
    # that did not. See data/service_time_exceptions.json.
    if force_active_periods:
        for lo, hi in force_active_periods:
            if accrual_floor is not None:
                lo = max(lo, accrual_floor)
            hi = min(hi, horizon_end)
            if lo <= hi:
                intervals.append((lo, hi))
        intervals = _merge_intervals(intervals)
    if appearance_dates:
        intervals = _union_appearance_spans(
            intervals, appearance_dates, transactions, horizon_end, accrual_floor
        )

    if force_inactive_periods:
        for lo, hi in force_inactive_periods:
            intervals = _subtract_period(intervals, lo, hi)

    return intervals


def _union_appearance_spans(
    intervals: list[tuple[dt.date, dt.date]],
    appearance_dates: list[dt.date],
    transactions: list[Transaction],
    horizon_end: dt.date,
    accrual_floor: dt.date | None,
) -> list[tuple[dt.date, dt.date]]:
    """Credit roster time the transaction walk missed but the stat feed saw.

    A player cannot appear in a major-league game unless he is on the active
    roster that day. When the transaction feed drops a recall (Lawrence
    Butler after 2024-05-14, JP Sears for all of 2023-24) or never records
    the reinstatement that ends a restricted-list stop (Dee Strange-Gordon,
    "roster status changed" on 2016-07-28), the walk stays closed through
    seasons he played. Each appearance date is proof. Consecutive
    appearances are one stint unless a stop falls strictly between them, so
    a suspension or an option that the feed DID record is not filled back
    in, and a season of starts with no stop in between becomes the span
    from the first game to the last.
    """
    dates = sorted({
        d for d in appearance_dates
        if (accrual_floor is None or d >= accrual_floor) and d <= horizon_end
    })
    if not dates:
        return intervals

    by_date: dict[dt.date, list[Transaction]] = {}
    for t in transactions:
        if t.date <= horizon_end:
            by_date.setdefault(t.date, []).append(t)
    stop_dates = set()
    for day, rows in by_date.items():
        descs = [r.description for r in rows]
        if any(r.minor_assignment for r in rows) or any(_is_active_stop(d) for d in descs):
            stop_dates.add(day)

    spans: list[tuple[dt.date, dt.date]] = []
    start = prev = dates[0]
    for day in dates[1:]:
        if any(prev < stop < day for stop in stop_dates):
            spans.append((start, prev))
            start = day
        prev = day
    spans.append((start, prev))
    return _merge_intervals(list(intervals) + spans)


def _assignment_followup(rows: list["Transaction"]) -> str:
    """What a later day says about an earlier 'assigned to <MLB club>' row.

    'yes' -- he was on that club's active roster (injured list or optioned).
    'no'  -- he was still in the minors, or the next roster move is a new
             start rather than proof the assignment stuck.
    'skip' -- this day says nothing (a number change, a rehab, a status note).
    """
    if any(r.minor_assignment for r in rows):
        return "no"
    descs = [r.description for r in rows]
    if any(_RECALL_RE.search(d) for d in descs) or any(_SELECT_RE.search(d) for d in descs):
        return "no"
    if any(_IL_PLACEMENT_RE.search(d) or _OPTION_RE.search(d) for d in descs):
        return "yes"
    if any(
        is_free_agency_election(d) or _is_active_stop(d) or _is_active_start(d)
        for d in descs
    ):
        return "no"
    return "skip"


def _mlb_assignment_reopens(
    idx: int,
    day_order: list[dt.date],
    by_date: dict[dt.date, list["Transaction"]],
) -> bool:
    """Did this 'assigned to <MLB club>' row put him on the active roster?

    The feed uses that wording for a real return (Zac Gallen, 2026-02-15,
    elected free agency in November, on the injured list in July) and for a
    spring-training roster note (Kevin Ginkel, assigned in March, contract
    selected from Reno in August). The difference is the next row that
    actually says where he was. An injured-list placement or an option means
    he was on the active roster. A minor-league assignment, a recall, or a
    contract selection means he was not, and the assignment must not open a
    season he spent in the minors.

    Measured on the cached histories: treating every such row as a start
    moved 35 players, including full phantom seasons. Requiring this
    confirmation moves three: Gallen, Tyler Kinley, and Rowdy Tellez (traded
    while optioned, assigned to the Brewers the next day).
    """
    for later in day_order[idx + 1:]:
        verdict = _assignment_followup(by_date[later])
        if verdict == "skip":
            continue
        return verdict == "yes"
    return False


def _next_resolving_day(
    day_order: list[dt.date],
    idx: int,
    by_date: dict[dt.date, list["Transaction"]],
) -> dt.date | None:
    """The first day after `day_order[idx]` carrying a move that resolves a DFA.

    A DFA ends on the next trade, waiver claim, outright/option/assignment or
    release -- in feed terms, any start, stop, trade, or assignment that
    actually put him back on the active roster. Returns that date, or None if
    nothing follows (the grace window then runs its full length).
    """
    for j, day in enumerate(day_order[idx + 1:], start=idx + 1):
        rows = by_date[day]
        if any(
            _is_active_start(r.description)
            or _is_active_stop(r.description)
            or is_trade(r.description)
            for r in rows
        ):
            return day
        if any(r.mlb_assignment for r in rows) and _mlb_assignment_reopens(j, day_order, by_date):
            return day
    return None


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


def _merge_intervals(
    intervals: list[tuple[dt.date, dt.date]]
) -> list[tuple[dt.date, dt.date]]:
    """Collapse overlapping or day-adjacent intervals into a disjoint set.

    The day count in compute_service_time sums each interval's overlap with a
    season independently, so two intervals that overlap inside one season would
    be counted twice. The walk never produces overlaps, but a hand-kept
    `force_active_periods` exception can (it is added on top of the walk's
    output), so the result is merged before it is used. Adjacent ranges
    (end, end+1 day) are joined too -- they describe one unbroken stretch.
    """
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + dt.timedelta(days=1):
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _subtract_period(
    intervals: list[tuple[dt.date, dt.date]], lo: dt.date, hi: dt.date
) -> list[tuple[dt.date, dt.date]]:
    """Remove the inclusive [lo, hi] window from a disjoint interval list.

    Used for a `force_inactive_periods` exception -- a stretch the feed credits
    (or would credit) but which earned no service (e.g. a suspension the feed
    shows only as a restricted-list placement). The result stays disjoint.
    """
    out: list[tuple[dt.date, dt.date]] = []
    for start, end in intervals:
        if hi < start or lo > end:
            out.append((start, end))
            continue
        if start < lo:
            out.append((start, lo - dt.timedelta(days=1)))
        if end > hi:
            out.append((hi + dt.timedelta(days=1), end))
    return out


# --- The 20-day optional-assignment rule ------------------------------------
# Under the CBA, a player whose optional assignments total FEWER than 20 days in
# a championship season is credited Major League service for those days -- a
# brief demotion does not cost service time (and, the same threshold, does not
# burn an option year). Sources are in config/cba/2022.json under
# options.days_to_burn_option_year, now marked verified:
#   https://www.baseballamerica.com/stories/brett-baty-s-early-season-assignment-highlights-lesser-known-option-rule/
#   https://www.mlb.com/glossary/transactions/minor-league-options
#
# The engine credits nothing back for a DFA, a release, an outright or an IL
# move -- only an OPTION. So the gaps between a player's active intervals are
# attributed to an option only when the day the interval closed is itself an
# "optioned" transaction date. That is how we tell an option gap from any other
# gap without guessing: the feed's own word for the move that opened it.
_OPTION_RE = re.compile(r"\boptioned\b", re.IGNORECASE)
# A contract selection is the call-up. When it is the next roster move after
# an "assigned to <club>" row, that assignment was a spring-training note and
# he was still in the minors. See _mlb_assignment_reopens.
_SELECT_RE = re.compile(
    r"selected the contract|purchased the contract|contract selected",
    re.IGNORECASE,
)

# How far before a club's Opening Day an option is still read as a spring-
# training assignment that counts from Opening Day. Spring training runs ~6
# weeks; 45 days covers it without reaching into the prior season (which ended
# the previous October). Same constant the pre-debut floor uses, and for the
# same reason -- it separates two populations rather than tuning a boundary.
_SPRING_OPTION_LOOKBACK_DAYS = 45


def _optional_assignment_days(
    season_intervals: list[tuple[dt.date, dt.date]],
    option_dates: set[dt.date],
    season: "SeasonWindow",
) -> int:
    """Total days this player spent on OPTIONAL ASSIGNMENT during the season.

    `season_intervals` are his active (on-roster) intervals already clipped to
    the season window, in chronological order. The days he was OFF the roster
    because of an option are the gaps between them -- counted only when the gap
    was opened by an "optioned" transaction, so a DFA/release/IL gap is never
    swept in.

    Three regions, none overlapping:

      * between two consecutive intervals, when the first closed on an option;
      * before the first interval, when he was optioned in spring training (an
        option in the 45 days up to the first interval, with the first interval
        starting after Opening Day) -- counted from the club's Opening Day;
      * after the last interval, when he was optioned and never recalled that
        season -- counted through the end of the season.
    """
    if not season_intervals:
        return 0
    ivs = sorted(season_intervals)
    gap = 0

    # Between consecutive intervals.
    for (_s1, e1), (s2, _e2) in zip(ivs, ivs[1:]):
        stop_day = e1 + dt.timedelta(days=1)
        if stop_day in option_dates:
            gap += (s2 - stop_day).days

    # Before the first interval: optioned in spring, recalled during the season.
    first_start = ivs[0][0]
    if first_start > season.start:
        earliest = season.start - dt.timedelta(days=_SPRING_OPTION_LOOKBACK_DAYS)
        if any(earliest <= d <= first_start for d in option_dates):
            gap += (first_start - season.start).days

    # After the last interval: optioned and never recalled -- the assignment
    # runs to the end of the season.
    last_end = ivs[-1][1]
    if last_end < season.end:
        stop_day = last_end + dt.timedelta(days=1)
        if stop_day in option_dates:
            gap += (season.end - last_end).days

    return gap


def compute_service_time(
    transactions: list[Transaction],
    seasons: list[SeasonWindow],
    presume_active_from_debut: bool = False,
    horizon_end: dt.date | None = None,
    accrual_floor: dt.date | None = None,
    accrual_ceiling: dt.date | None = None,
    seasons_with_appearances: set[int] | None = None,
    roy_full_year_seasons: set[int] | None = None,
    ruleset: "cba.Ruleset | None" = None,
    dfa_limbo_days: int | None = None,
    force_active_periods: list[tuple[dt.date, dt.date]] | None = None,
    force_inactive_periods: list[tuple[dt.date, dt.date]] | None = None,
    appearance_dates: list[dt.date] | None = None,
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

    `roy_full_year_seasons` is the set of seasons in which this player is
    credited a FULL year regardless of days accrued, because he finished first
    or second in Rookie of the Year voting (the 2022 CBA's Prospect Promotion
    Incentive). The list of who qualified is hand-maintained in
    config/cba/roy_full_year_awards.json and resolved per player by the caller; the
    full-year VALUE comes from the ruleset, not a literal.

    `ruleset` is the CBA agreement to compute under, from config/cba/. It
    defaults to the agreement currently in force, so every existing caller is
    unaffected. Pass a different one to run a player under a successor
    agreement and diff the two -- which is the point of the whole exercise
    once the 2022 agreement expires on 2026-12-01.

    `dfa_limbo_days`, `force_active_periods` and `force_inactive_periods` are
    passed straight through to build_global_active_intervals -- the DFA grace
    window and the hand-kept restricted-list exceptions (rules v7). All default
    off, so a caller that does not opt in gets the old behaviour.
    """
    rules = ruleset or _DEFAULT_RULES
    full_year_days = rules.require("service_time.days_per_credited_year")
    max_per_season = rules.require("service_time.max_days_creditable_per_season")
    fa_years = rules.require("free_agency.credited_years_required")
    arb_years = rules.require("arbitration.standard_years_required")
    s2_enabled = rules.require("arbitration.super_two.enabled")
    s2_min_years, s2_max_years = rules.require(
        "arbitration.super_two.service_range_years"
    )
    s2_min_days = rules.require("arbitration.super_two.heuristic_min_days")
    # The 20-day optional-assignment threshold. require_verified rather than
    # require: this credits days back, so a number nobody has checked against
    # the CBA text must not drive it -- a wrong threshold is worse than a crash.
    option_credit_threshold = rules.require_verified("options.days_to_burn_option_year")

    ordered_seasons = sorted(seasons, key=lambda s: s.year)
    if horizon_end is None:
        horizon_end = ordered_seasons[-1].end if ordered_seasons else dt.date.today()

    global_intervals = build_global_active_intervals(
        transactions,
        horizon_end,
        accrual_floor=accrual_floor,
        accrual_ceiling=accrual_ceiling,
        presume_active_from=accrual_floor if presume_active_from_debut else None,
        dfa_limbo_days=dfa_limbo_days,
        season_starts=[s.start for s in ordered_seasons],
        force_active_periods=force_active_periods,
        force_inactive_periods=force_inactive_periods,
        appearance_dates=appearance_dates,
    )

    total_days = 0
    by_season: dict[int, dict] = {}
    years_with_transactions = {t.date.year for t in transactions}
    option_dates = {t.date for t in transactions if _OPTION_RE.search(t.description)}
    # An injured-list placement inside an interval means a zero-game season
    # can still be real service (a player who never appeared because he was
    # hurt). The sanity check uses this so it does not call that season phantom.
    il_dates = {t.date for t in transactions if _IL_PLACEMENT_RE.search(t.description)}

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
        # The 20-day optional-assignment rule: if the days this player spent on
        # optional assignment this season total fewer than the threshold, those
        # days count as Major League service. Added to raw days before
        # proration and the cap, so a short option on top of an otherwise full
        # season still cannot exceed max_days_creditable_per_season.
        if days > 0:
            option_days = _optional_assignment_days(season_intervals, option_dates, season)
            if 0 < option_days < option_credit_threshold:
                days += option_days
        prorated_days = _prorate_shortened_season(season, days, rules)
        credited_days = min(prorated_days, max_per_season)
        # The Prospect Promotion Incentive: a top-two Rookie of the Year finish
        # is credited a full year for that season. It can only RAISE the figure
        # (a player who already has a full season is unaffected), and the
        # full-year value is the ruleset's, never a literal.
        if roy_full_year_seasons and season.year in roy_full_year_seasons:
            credited_days = max(credited_days, full_year_days)
        has_il = any(
            s <= d <= e
            for s, e in global_intervals
            if e >= season.start and s <= season.end
            for d in il_dates
        )
        total_days += credited_days
        by_season[season.year] = {
            "raw_active_days": days,
            "prorated_days": prorated_days,
            "credited_days": credited_days,
            "intervals": [(s.isoformat(), e.isoformat()) for s, e in season_intervals],
            "has_il": has_il,
        }

    total_years, remaining_days = divmod(total_days, full_year_days)
    fractional_years = total_years + remaining_days / full_year_days

    is_super_two_candidate = bool(
        s2_enabled
        and s2_min_years <= fractional_years < s2_max_years
        and remaining_days >= s2_min_days
    )

    return ServiceTimeResult(
        total_years=total_years,
        remaining_days=remaining_days,
        total_days=total_days,
        is_free_agent_eligible=fractional_years >= fa_years,
        is_arbitration_eligible=(fractional_years >= arb_years) or is_super_two_candidate,
        is_super_two_candidate=is_super_two_candidate,
        by_season=by_season,
    )
