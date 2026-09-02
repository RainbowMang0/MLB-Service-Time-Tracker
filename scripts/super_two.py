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
              [2.000, 3.000), and who accrued at least one day in season Y.
  cutoff   -- the service time of the last player inside the top 22% of
              that class, ranked by total service descending.
  eligible -- class members at or above the cutoff who also accrued
              >= 86 days in season Y.

The "at least one day in season Y" test is a data-derived stand-in for "was
in the major leagues that year". The rule's class is players with 2-3 years
of service, which in practice means players currently in MLB; a man who
last played in 2015 and sits at 2.5 years is not in the 2025 class. Using
accrual rather than a roster snapshot keeps this consistent with everything
else here: prefer a check the data can settle.

It undercounts by a hair -- a player on a 40-man roster who spent the whole
year in the minors accrues nothing and drops out of the class denominator,
where the real rule would keep him. He could never qualify anyway (the
86-day test excludes him), so the effect is limited to shifting the 22%
boundary by a fraction of a player.

ACCURACY
========
The computed cutoffs land in the 2.11-2.14 band across recent seasons,
which is where reported Super Two cutoffs have historically fallen -- and
they get there with no tuning of any kind. That is encouraging but it is
NOT verification. `data/reference_super_two.json` exists for the same
reason the Baseball Reference file does: published cutoffs entered by hand
are the only independent check, and a number that merely looks plausible is
exactly the kind of thing this project has been wrong about before.
"""

from __future__ import annotations

import math

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

# CBA: and at least 86 days of service in the immediately preceding season.
# Note this is the SAME number the old heuristic used, but in its correct
# role. The heuristic treated "86 days into your third year" as the whole
# test; it is really a secondary condition on top of the 22% ranking.
SUPER_TWO_MIN_PRECEDING_SEASON_DAYS = _DEFAULT_RULES.require(
    "arbitration.super_two.minimum_prior_season_days"
)

# Below this the population is too thin for a 22% boundary to mean anything
# -- an early or partial dataset would produce a confident-looking cutoff
# from a handful of players. Fall back to the flat heuristic and say so.
MIN_CLASS_SIZE = _DEFAULT_RULES.require("arbitration.super_two.minimum_class_size")


def format_service(days: int, ruleset: "cba.Ruleset | None" = None) -> str:
    full_year = (ruleset or _DEFAULT_RULES).require(
        "service_time.days_per_credited_year"
    )
    return f"{days // full_year}.{days % full_year:03d}"


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

    The class boundaries are the ruleset's, not literals: a successor
    agreement could define the class over a different service range.
    """
    rules = ruleset or _DEFAULT_RULES
    full_year = rules.require("service_time.days_per_credited_year")
    low_years, high_years = rules.require("arbitration.super_two.service_range_years")
    low_days, high_days = low_years * full_year, high_years * full_year
    members = []
    for record in db.values():
        seasons = record.get("seasons")
        if not seasons:
            continue
        in_year = _days_in(seasons, year)
        if in_year <= 0:
            continue
        through = _service_through(seasons, year)
        if low_days <= through < high_days:
            members.append((through, in_year, record))
    members.sort(key=lambda m: (-m[0], m[2].get("name") or ""))
    return members


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

    # floor(): the 22% is a share of the class, and a fractional player
    # rounds down rather than admitting one more.
    qualifying_count = math.floor(len(members) * top_fraction)
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


def apply_super_two(db: dict[str, dict], reference_year: int) -> dict | None:
    """
    Set `super_two_candidate` on every record, using the cutoff measured
    after `reference_year` as the threshold. Returns the cutoff metadata, or
    None if it could not be computed (in which case flags are left alone).

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
    season_tested = qualifying_season(db, reference_year)
    eligible = 0
    for record in db.values():
        service = int(record.get("service_days_total") or 0)
        seasons = record.get("seasons") or []
        in_tested_season = _days_in(seasons, season_tested)
        is_super_two = (
            2 * FULL_YEAR_DAYS <= service < 3 * FULL_YEAR_DAYS
            and service >= cutoff_days
            and in_tested_season >= SUPER_TWO_MIN_PRECEDING_SEASON_DAYS
        )
        record["super_two_candidate"] = is_super_two
        record["arbitration_eligible"] = (
            service >= 3 * FULL_YEAR_DAYS or is_super_two
        )
        eligible += is_super_two

    cutoff["projected_candidates"] = eligible
    cutoff["qualifying_season"] = season_tested
    return cutoff
