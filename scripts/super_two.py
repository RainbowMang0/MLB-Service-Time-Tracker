"""
super_two.py
-------------
The real Super Two cutoff, computed from this project's own population.

WHY THIS CAN EXIST NOW
=======================
Super Two was a documented limitation of this project for good reason. The
rule is not a fixed threshold:

    A player with at least two but less than three years of service is
    eligible for salary arbitration a year early if he has accumulated at
    least 86 days of service in the immediately preceding season AND ranks
    in the top 22 percent, by total service, of the class of players with
    at least two but less than three years.

"Top 22 percent of the class" needs the whole class -- every player in the
2.000-3.000 band, league-wide -- which MLB does not publish. So the code
FLAGGED candidates using a flat 86-day proxy and said plainly that it was
not authoritative.

That premise expired when the database was completed. 5,568 players, each
with a season-by-season breakdown, IS the league-wide population. The class
and the cutoff are now computable directly, for any past season, with no
extra API calls.

WHAT IS COMPUTED, AND WHAT IS ASSUMED
======================================
For the offseason following season Y:

  class    -- players whose service time AT THE END OF SEASON Y falls in
              [2.000, 3.000), AND who accrued at least 86 days of service
              in season Y.
  cutoff   -- the service time of the last player inside the top 22% of
              that class (rounded to the nearest whole number), ranked by
              total service descending.
  eligible -- class members at or above the cutoff.

⚠️ CORRECTED 2026-10-30. This used to read "and who accrued at least one
day in season Y" for the class, with the 86-day floor applied only as a
second test on top of the ranking. That is NOT what MLB's own description
of the rule says. The glossary at mlb.com/glossary/transactions/super-two
states it as one sentence, not two: a player must "rank in the top 22
percent, in terms of service time, among those who have amassed between
two and three years in the Majors and have accrued at least 86 days of
service time during the preceding season" -- the 86-day floor describes
the POOL being ranked, not an extra filter applied after ranking it.

validate_super_two.py's original four-of-five failures against
data/reference_super_two.json are consistent with this: rebuilding the
class with the 86-day floor, and rounding the 22% share to the nearest
whole number instead of flooring it (see below), takes 2021 from +0d to
an exact match and 2022 from +7d to +1d -- both inside the 2-day
tolerance. 2023-2025 still miss (+12d, +3d, -3d); those are consistent
with known per-player service-time defects tracked separately (see
handoff/contract-and-profiles.md and the unmerged
servicetime/fix-service-time-accuracy branch) rather than with this
class/rounding rule, which a boundary that sits on exactly the right two
players either side of it cannot itself produce.

The "accrued >= 86 days in season Y" test is also a data-derived stand-in
for "was in the major leagues that year, long enough to be in the pool at
all" -- the rule's class is players with 2-3 years of service who are
*also* candidates for the 86-day test, which in practice means players
materially in MLB that season; a man who last played in 2015 and sits at
2.5 years is not in the 2025 class. Using accrual rather than a roster
snapshot keeps this consistent with everything else here: prefer a check
the data can settle.

ROUNDING
========
"Top 22 percent" of a class whose size is not a multiple of 50 is not a
whole number. mlb.com's own glossary text does not spell out how the
fraction is rounded, but "rounded to the nearest whole number" is how this
kind of percentile cutoff is conventionally stated, and it is also the
better empirical fit: on the reference seasons it reproduces 2021 exactly
and 2022 to within a day, where flooring the fraction (the previous
behaviour) left both further out. A plain floor() silently drops the
player who was meant to round up and in; `_round_half_up()` below makes it
explicit and deterministic (Python's builtin `round()` rounds .5 to even,
which is not "nearest" as a human would read it).

It undercounts by a hair -- a player on a 40-man roster who spent the
whole year in the minors accrues nothing and drops out of the class
denominator, where the real rule would keep him. He could never qualify
anyway (he fails the 86-day floor too), so the effect is limited to
shifting the 22% boundary by a fraction of a player.

ACCURACY
========
The computed cutoffs land in the 2.11-2.14 band across recent seasons,
which is where reported Super Two cutoffs have historically fallen. As of
2026-10-30 that is backed by `data/reference_super_two.json` (published
cutoffs, 2021-2025, entered by hand) rather than being merely plausible:
`scripts/validate_super_two.py` checks the computed figure against each
published one. Two of five are inside the 2-day tolerance after this
correction (2021 exact, 2022 +1d); the other three (2023 +12d, 2024 +3d,
2025 -3d) are believed to be downstream of per-player service-time
accuracy, not of the class or rounding rule -- see the note above.
"""

from __future__ import annotations

import json
import math
import pathlib

import cba

# Every threshold below comes from the CBA ruleset in config/cba/ rather than
# being a literal, for the reason set out in scripts/cba.py: the agreement
# expires 2026-12-01 and the Super Two percentile is exactly the sort of value
# a successor agreement changes. The module-level names stay bound to the
# agreement in force so existing callers are unaffected.
_DEFAULT_RULES = cba.default()

FULL_YEAR_DAYS = _DEFAULT_RULES.require("service_time.days_per_credited_year")

# CBA: top 22% of the two-to-three-year class.
SUPER_TWO_TOP_FRACTION = _DEFAULT_RULES.require(
    "arbitration.super_two.top_percentile"
) / 100.0

# CBA: at least 86 days of service in the immediately preceding season.
# Note this is the SAME number the old heuristic used, but in its correct
# role. The heuristic treated "86 days into your third year" as the whole
# test. It is not a secondary filter applied after the 22% ranking -- it
# defines who is IN the pool being ranked (see "CORRECTED 2026-10-30" above)
# -- and apply_super_two() also re-checks it against the *current* season
# for a live candidate, which is a separate use of the same number.
SUPER_TWO_MIN_PRECEDING_SEASON_DAYS = _DEFAULT_RULES.require(
    "arbitration.super_two.minimum_prior_season_days"
)

# Below this the population is too thin for a 22% boundary to mean anything
# -- an early or partial dataset would produce a confident-looking cutoff
# from a handful of players. Fall back to the flat heuristic and say so.
MIN_CLASS_SIZE = _DEFAULT_RULES.require("arbitration.super_two.minimum_class_size")

REPORTED_CUTOFF_FILE = pathlib.Path(__file__).resolve().parents[1] / "data" / "super_two_reported.json"


def format_service(days: int, ruleset: "cba.Ruleset | None" = None) -> str:
    full_year = (ruleset or _DEFAULT_RULES).require(
        "service_time.days_per_credited_year"
    )
    return f"{days // full_year}.{days % full_year:03d}"


def parse_service(value: str, ruleset: "cba.Ruleset | None" = None) -> int:
    """The inverse of format_service(): "2.132" -> 476. Raises ValueError on
    anything that is not exactly years.days (an empty string, missing dot,
    non-digits) rather than guessing -- callers that want a graceful fallback
    (e.g. load_reported_cutoff()) catch it themselves."""
    full_year = (ruleset or _DEFAULT_RULES).require(
        "service_time.days_per_credited_year"
    )
    years_str, dot, days_str = value.partition(".")
    if not dot:
        raise ValueError(f"not a years.days figure: {value!r}")
    return int(years_str) * full_year + int(days_str)


def load_reported_cutoff(
    path: pathlib.Path | None = None,
    projected_season: int | None = None,
    ruleset: "cba.Ruleset | None" = None,
) -> dict | None:
    """A single hand-kept, UNOFFICIAL reported Super Two cutoff, from
    data/super_two_reported.json -- see that file's own _README.

    This is the only input in the Super Two pipeline that is not derived
    from this project's own data: a number heard from reporting (e.g.
    "expected to land a day above player X's service time") ahead of the
    actual published figure MLB Trade Rumors / the Associated Press report
    each November, which data/reference_super_two.json holds once it exists.

    Returns None -- never raises -- if the file is missing, is not valid
    JSON, is missing a required field, has an unparseable `cutoff`, or (when
    `projected_season` is given) its `season` does not match: a stale or
    malformed reported figure must fall back to the single computed cutoff,
    not break the page or mix seasons.
    """
    rules = ruleset or _DEFAULT_RULES
    p = path or REPORTED_CUTOFF_FILE
    try:
        raw = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None

    season = raw.get("season")
    cutoff_str = raw.get("cutoff")
    if not isinstance(season, int) or not isinstance(cutoff_str, str):
        return None
    if projected_season is not None and season != projected_season:
        return None

    try:
        cutoff_days = parse_service(cutoff_str, rules)
    except (ValueError, TypeError):
        return None

    return {
        "season": season,
        "cutoff_days": cutoff_days,
        "cutoff": format_service(cutoff_days, rules),
        "source": raw.get("source") or "",
        "entered": raw.get("entered") or "",
    }


def cutoff_range(computed: dict | None, reported: dict | None) -> dict | None:
    """Combine this project's own computed cutoff with the hand-kept
    reported figure (see load_reported_cutoff()) into a low/high band for
    the SAME season.

    low is always this project's own computed cutoff -- the figure the rest
    of the site already treats as the Super Two line. high is the reported
    figure when one exists for the matching season, ordered with
    min()/max() rather than assumed, so a reported figure that happens to
    sit below the computed one (it can; it is someone's guess, not a
    guarantee) still produces a valid low<=high band instead of a crossed
    one. With no usable reported figure -- or an identical one -- this
    collapses to a single-valued, degenerate range (`is_range` False),
    which is the graceful fallback to "current behaviour": a single cutoff.

    Returns None only when there is no computed cutoff to anchor on at all.
    """
    if computed is None:
        return None
    computed_days = computed["cutoff_days"]
    reported_days = None
    if reported is not None and reported.get("season") == computed.get("season"):
        reported_days = reported["cutoff_days"]

    if reported_days is None:
        low_days = high_days = computed_days
    else:
        low_days, high_days = min(computed_days, reported_days), max(computed_days, reported_days)

    return {
        "season": computed["season"],
        "low_days": low_days,
        "high_days": high_days,
        "low": format_service(low_days),
        "high": format_service(high_days),
        "is_range": low_days != high_days,
        "reported": reported if reported_days is not None else None,
    }


def _service_through(seasons: list[dict], year: int) -> int:
    return sum(int(row.get("d") or 0) for row in seasons if int(row["y"]) <= year)


def _days_in(seasons: list[dict], year: int) -> int:
    for row in seasons:
        if int(row["y"]) == year:
            return int(row.get("d") or 0)
    return 0


def build_class(
    db: dict[str, dict], year: int, ruleset: "cba.Ruleset | None" = None
) -> list[tuple[int, int, dict]]:
    """
    (service_days_through_year, days_in_year, record) for the 2-3 year class
    at the end of `year`, ranked by service descending.

    Membership requires BOTH conditions mlb.com's glossary states as one
    sentence: 2-3 years of total service, AND at least
    `minimum_prior_season_days` (86) of service in `year` itself. Before
    2026-10-30 this required only >0 days in `year`, treating the 86-day
    floor as a filter applied only after the ranking -- see the
    "CORRECTED" note in the module docstring for why that read the rule
    backwards and how it was measured.

    The class boundaries and the day floor are the ruleset's, not literals:
    a successor agreement could define either differently.
    """
    rules = ruleset or _DEFAULT_RULES
    full_year = rules.require("service_time.days_per_credited_year")
    low_years, high_years = rules.require("arbitration.super_two.service_range_years")
    low_days, high_days = low_years * full_year, high_years * full_year
    min_prior_days = rules.require("arbitration.super_two.minimum_prior_season_days")
    members = []
    for record in db.values():
        seasons = record.get("seasons")
        if not seasons:
            continue
        in_year = _days_in(seasons, year)
        if in_year < min_prior_days:
            continue
        through = _service_through(seasons, year)
        if low_days <= through < high_days:
            members.append((through, in_year, record))
    members.sort(key=lambda m: (-m[0], m[2].get("name") or ""))
    return members


def _round_half_up(value: float) -> int:
    """
    "Rounded to the nearest whole number", spelled out rather than left to
    Python's builtin `round()`, which rounds a tie (x.5) to the nearest
    EVEN integer rather than up -- not what a human reads "nearest whole
    number" to mean, and a silent source of an off-by-one on a class size
    that lands exactly on a half.
    """
    return math.floor(value + 0.5)


def compute_cutoff(
    db: dict[str, dict], year: int, ruleset: "cba.Ruleset | None" = None
) -> dict | None:
    """
    The Super Two cutoff for the offseason after `year`, or None if the
    class is too small to draw a boundary through.
    """
    rules = ruleset or _DEFAULT_RULES
    top_fraction = rules.require("arbitration.super_two.top_percentile") / 100.0
    min_class_size = rules.require("arbitration.super_two.minimum_class_size")

    members = build_class(db, year, rules)
    if len(members) < min_class_size:
        return None

    # Rounded to the nearest whole number, not floored -- see
    # _round_half_up() and the "ROUNDING" section of the module docstring.
    qualifying_count = _round_half_up(len(members) * top_fraction)
    if qualifying_count < 1:
        return None

    cutoff_days = members[qualifying_count - 1][0]
    return {
        "season": year,
        "cutoff_days": cutoff_days,
        "cutoff": format_service(cutoff_days),
        "class_size": len(members),
        "qualifying_count": qualifying_count,
        "method": (
            f"top {SUPER_TWO_TOP_FRACTION:.0%} of the {len(members)} players at "
            f"2.000-3.000 years after the {year} season, who also accrued "
            f"{SUPER_TWO_MIN_PRECEDING_SEASON_DAYS}+ days that year"
        ),
    }


def _max_days_in(db: dict[str, dict], year: int) -> int:
    """
    The most service any single player accrued in `year`.

    A cheap, data-derived read on how far a season has actually progressed --
    no calendar assumptions, no season-window lookup. Credited days are
    capped at 172, so this maxes out exactly when somebody has banked a full
    year.
    """
    return max(
        (_days_in(record.get("seasons") or [], year) for record in db.values()),
        default=0,
    )


def latest_complete_season(db: dict[str, dict], today_year: int) -> int:
    """
    The most recent season the cutoff can be measured from.

    A cutoff drawn through a half-played year would move every day and would
    not be the figure anyone means by "the Super Two cutoff", so the season
    has to be finished. Whether it is gets measured rather than assumed: if
    somebody has reached the 172-day cap this year, the year has run its
    course. Assuming `today_year - 1` unconditionally would go stale every
    offseason, quietly publishing a cutoff a full season out of date.
    """
    if _max_days_in(db, today_year) >= FULL_YEAR_DAYS:
        return today_year
    return today_year - 1


def qualifying_season(db: dict[str, dict], reference_year: int) -> int:
    """
    Which season the 86-day condition is tested against.

    The rule says "the immediately preceding season" -- preceding the
    offseason in which eligibility is decided. For the projection this makes
    (see apply_super_two), that offseason is the NEXT one, so the season that
    matters is the one being played now, not the one the cutoff came from.

    Anthony Kay is why this is not the same thing. He sat at 2.004 after
    2023, accrued nothing at all in 2024 or 2025, then came back and banked
    151 days in 2026. Tested against 2025 he has zero days and fails; tested
    against 2026 he clears 86 comfortably. The second is the right answer to
    "is he on track for next offseason".

    Early in a season nobody has 86 days yet, and switching then would empty
    the list rather than project it -- so the current season is only used
    once someone has actually reached 86 days in it. Measured, not assumed
    from the calendar.
    """
    current = reference_year + 1
    if _max_days_in(db, current) >= SUPER_TWO_MIN_PRECEDING_SEASON_DAYS:
        return current
    return reference_year


def apply_super_two(
    db: dict[str, dict],
    reference_year: int,
    reported: dict | None = None,
) -> dict | None:
    """
    Set `super_two_candidate` on every record, using the cutoff measured
    after `reference_year` as the threshold. Returns the cutoff metadata, or
    None if it could not be computed (in which case flags are left alone).

    `reported` is an optional hand-kept figure from load_reported_cutoff(),
    for the one-sided range the site shows around the computed cutoff (see
    cutoff_range()). It never changes WHO is flagged `super_two_candidate` --
    that stays exactly "at or above this project's own computed cutoff",
    the only number the rest of the site (selection, badges, salary gating)
    has ever keyed off and the one every existing test already pins. It only
    adds `super_two_bubble`: True for a candidate whose service sits below
    the reported high end, i.e. inside the low-to-high band rather than
    past all of it -- extra nuance a renderer can use, never a narrower
    gate.

    TWO DIFFERENT QUESTIONS, and it matters which one this answers.

    `compute_cutoff(db, 2025)` is a historical fact: the boundary that
    actually applied to the class that existed after the 2025 season. Those
    players have since accrued another year and are mostly past 3.000 now --
    flagging them today would be reporting a decision that has already been
    made.

    What a reader wants instead is the question about the NEXT offseason:
    "is this player on track to be Super Two?" That cannot be a fact, because
    the class he will be measured against does not exist yet -- the season is
    still being played. So it is a projection, made honestly: take his
    service time as it stands, and compare it to the most recent real
    boundary.

    A player is flagged when all three hold:

      * his service time right now is in [2.000, 3.000)
      * it is at or above the most recent measured cutoff
      * he accrued 86+ days in the season the next offseason will look back
        on -- see qualifying_season(). The rule's own second test, now
        checked exactly rather than approximated.

    The threshold moves a little year to year (2.126 to 2.136 across the last
    four), so a player sitting within a few days of it could land either
    side. This is a much better answer than the flat 86-day proxy it
    replaces, and it is still an estimate.
    """
    cutoff = compute_cutoff(db, reference_year)
    if cutoff is None:
        return None

    cutoff_days = cutoff["cutoff_days"]
    rng = cutoff_range(cutoff, reported)
    high_days = rng["high_days"] if rng else cutoff_days
    season_tested = qualifying_season(db, reference_year)
    eligible = 0
    bubble = 0
    for record in db.values():
        service = int(record.get("service_days_total") or 0)
        seasons = record.get("seasons") or []
        in_tested_season = _days_in(seasons, season_tested)
        is_super_two = (
            2 * FULL_YEAR_DAYS <= service < 3 * FULL_YEAR_DAYS
            and service >= cutoff_days
            and in_tested_season >= SUPER_TWO_MIN_PRECEDING_SEASON_DAYS
        )
        is_bubble = is_super_two and rng is not None and rng["is_range"] and service < high_days
        record["super_two_candidate"] = is_super_two
        record["super_two_bubble"] = is_bubble
        record["arbitration_eligible"] = (
            service >= 3 * FULL_YEAR_DAYS or is_super_two
        )
        eligible += is_super_two
        bubble += is_bubble

    cutoff["range"] = rng
    cutoff["projected_candidates"] = eligible
    cutoff["projected_bubble"] = bubble
    cutoff["qualifying_season"] = season_tested
    # Is the season the 86-day test runs against actually finished?
    #
    # The page tells a reader that a player can still cross 86 days and join
    # the list "while that season is still being played" -- true in July, and
    # false from the moment the season ends, which is most of the year. It was
    # unconditional, so in late October it asserted an in-progress season that
    # had been over for weeks.
    #
    # Measured the same way latest_complete_season() measures it, rather than
    # inferred from the calendar: if anybody reached the cap that year, the
    # year has run its course.
    cutoff["qualifying_season_complete"] = (
        _max_days_in(db, season_tested) >= FULL_YEAR_DAYS
    )
    return cutoff
