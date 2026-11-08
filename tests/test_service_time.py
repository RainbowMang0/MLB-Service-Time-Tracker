"""
Lightweight unit tests for service_time.py. No pytest dependency required --
run directly with `python tests/test_service_time.py`.
"""

import datetime as dt
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import cba  # noqa: E402
from service_time import (  # noqa: E402
    FULL_YEAR_DAYS,
    SHORTENED_SEASONS,
    SeasonWindow,
    is_active_start,
    is_active_stop,
    roster_start_before_debut,
    Transaction,
    compute_service_time,
    days_in_intervals,
    build_global_active_intervals,
    claim_then_recall_windows,
    minor_league_stint_windows,
)

PASS = 0
FAIL = 0


def check(label: str, condition: bool):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def _tbody_row_count(html_out: str) -> int:
    """Number of <tr> rows inside the first <tbody>...</tbody>, i.e. data
    rows only, with the <thead> row excluded."""
    body = html_out.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    return body.count("<tr>")


def test_single_full_season():
    season = SeasonWindow(2023, dt.date(2023, 3, 30), dt.date(2023, 10, 1))
    txns = [
        Transaction(dt.date(2023, 3, 30), "Player selected the contract of X"),
    ]
    intervals = build_global_active_intervals(txns, season.end)
    days = days_in_intervals(intervals)
    check("full season active days == season length", days == (season.end - season.start).days + 1)

    result = compute_service_time(txns, [season])
    check("full season caps at 172 credited days", result.by_season[2023]["credited_days"] == 172)
    check("full season -> 1.000 formatted", result.formatted == "1.000")


def test_option_and_recall():
    season = SeasonWindow(2023, dt.date(2023, 3, 30), dt.date(2023, 10, 1))
    txns = [
        Transaction(dt.date(2023, 4, 1), "Team selected the contract of X"),
        Transaction(dt.date(2023, 6, 1), "Team optioned X to Triple-A"),
        Transaction(dt.date(2023, 8, 1), "Team recalled X from Triple-A"),
    ]
    result = compute_service_time(txns, [season])
    # active: Apr 1 - May 31 (61 days) + Aug 1 - Oct 1 (62 days) = 123
    check("option/recall interval math", result.by_season[2023]["raw_active_days"] == 61 + 62)


def test_injured_list_still_accrues():
    season = SeasonWindow(2023, dt.date(2023, 3, 30), dt.date(2023, 10, 1))
    txns = [
        Transaction(dt.date(2023, 3, 30), "Team selected the contract of X"),
        Transaction(dt.date(2023, 5, 1), "Team placed X on the 10-day injured list"),
        Transaction(dt.date(2023, 5, 20), "Team activated X from the 10-day injured list"),
    ]
    result = compute_service_time(txns, [season])
    check(
        "IL stint does not break active streak",
        result.by_season[2023]["raw_active_days"] == (season.end - season.start).days + 1,
    )


def test_multi_year_accumulation_and_free_agency():
    seasons = [
        SeasonWindow(y, dt.date(y, 3, 30), dt.date(y, 10, 1)) for y in range(2018, 2024)
    ]
    txns = [Transaction(dt.date(2018, 3, 30), "Team selected the contract of X")]
    result = compute_service_time(txns, seasons)
    check("6 full seasons -> free agency eligible", result.is_free_agent_eligible is True)
    check("6 full seasons -> 6.000 formatted", result.formatted == "6.000")


def test_arbitration_boundary():
    seasons = [
        SeasonWindow(y, dt.date(y, 3, 30), dt.date(y, 10, 1)) for y in range(2021, 2024)
    ]
    txns = [Transaction(dt.date(2021, 3, 30), "Team selected the contract of X")]
    result = compute_service_time(txns, seasons)
    check("3 full seasons -> arbitration eligible", result.is_arbitration_eligible is True)


def test_status_carries_across_seasons_with_no_new_transactions():
    """
    Regression test: a player called up once and never optioned/DFA'd again
    must keep accruing in later seasons even though there are no further
    transactions in those seasons' date windows.
    """
    seasons = [
        SeasonWindow(y, dt.date(y, 3, 30), dt.date(y, 10, 1)) for y in range(2020, 2023)
    ]
    txns = [Transaction(dt.date(2020, 3, 30), "Team selected the contract of X")]
    result = compute_service_time(txns, seasons)
    check(
        "later seasons still credited without new transactions",
        result.by_season[2022]["credited_days"] == 172,
    )


def test_trade_does_not_stop_clock():
    season = SeasonWindow(2023, dt.date(2023, 3, 30), dt.date(2023, 10, 1))
    txns = [
        Transaction(dt.date(2023, 3, 30), "Team A selected the contract of X"),
        Transaction(dt.date(2023, 7, 1), "Team A traded X to Team B"),
    ]
    result = compute_service_time(txns, [season])
    check(
        "trade mid-season keeps accruing",
        result.by_season[2023]["raw_active_days"] == (season.end - season.start).days + 1,
    )


def test_pre_debut_events_do_not_start_the_clock():
    """
    Regression test built from Jacob Wilson's real transaction feed.

    The /transactions endpoint returns a player's whole tracked history, so
    his college team ("Grand Canyon Antelopes activated SS Jacob Wilson",
    2023-02-08) and an All-Star roster both appear using the same verbs as
    major league moves. Before the accrual_floor fix these started his clock
    in Feb 2023 -- ~17 months before his actual debut -- and the pipeline
    reported him at 3.000 years instead of ~2.048.
    """
    txns = [
        Transaction(dt.date(2023, 2, 8), "Grand Canyon Antelopes activated SS Jacob Wilson."),
        Transaction(dt.date(2023, 7, 31), "Lansing Lugnuts activated SS Jacob Wilson."),
        Transaction(dt.date(2024, 3, 7), "Oakland Athletics Prospects activated SS Jacob Wilson."),
        Transaction(dt.date(2024, 7, 19), "Oakland Athletics selected the contract of SS Jacob Wilson."),
        Transaction(dt.date(2025, 7, 14), "American League All-Stars activated SS Jacob Wilson."),
    ]
    debut = dt.date(2024, 7, 19)
    seasons = [
        SeasonWindow(2024, dt.date(2024, 3, 28), dt.date(2024, 10, 1)),
        SeasonWindow(2025, dt.date(2025, 3, 28), dt.date(2025, 10, 1)),
    ]

    intervals = build_global_active_intervals(txns, dt.date(2025, 10, 1), accrual_floor=debut)
    check(
        "pre-debut college/showcase events never start the clock",
        all(start >= debut for start, _ in intervals),
    )

    unfloored = compute_service_time(txns, seasons)
    floored = compute_service_time(txns, seasons, accrual_floor=debut)
    check(
        "accrual floor reduces credited time vs. unfiltered history",
        floored.total_days < unfloored.total_days,
    )
    check(
        "debut-year credit starts at the debut, not opening day",
        floored.by_season[2024]["credited_days"] == (dt.date(2024, 10, 1) - debut).days + 1,
    )


def test_horizon_stops_at_today_not_end_of_season():
    """
    A player active right now must not be credited for the remainder of a
    season that hasn't been played yet.
    """
    season = SeasonWindow(2026, dt.date(2026, 3, 28), dt.date(2026, 10, 1))
    txns = [Transaction(dt.date(2026, 3, 28), "Athletics selected the contract of X")]
    today = dt.date(2026, 8, 19)

    to_today = compute_service_time(txns, [season], horizon_end=today)
    to_season_end = compute_service_time(txns, [season])

    check(
        "open interval stops at today, not at season end",
        to_today.total_days == (today - season.start).days + 1,
    )
    check(
        "stopping at today credits fewer days than stopping at season end",
        to_today.total_days < to_season_end.total_days,
    )


def test_as_of_past_date_ignores_later_transactions():
    """
    Regression test for the --as-of validation feature (validate_service_time.py).

    Computing service time "as of" a past date requires a transaction list
    that also contains LATER history (the same list the live API would
    return today), with everything after horizon_end ignored -- not just
    excluded from the trailing open interval. Before this fix, a stop
    transaction (option/DFA/release) dated after horizon_end would still
    truncate an interval that, as of horizon_end, hadn't ended yet.
    """
    season = SeasonWindow(2021, dt.date(2021, 4, 1), dt.date(2021, 10, 3))
    as_of = dt.date(2021, 4, 15)
    txns = [
        Transaction(dt.date(2021, 4, 1), "Yankees selected the contract of X"),
        # Happens AFTER as_of -- should not exist yet from as_of's perspective.
        Transaction(dt.date(2021, 6, 1), "Yankees optioned X to Triple-A"),
        Transaction(dt.date(2021, 8, 1), "Yankees recalled X from Triple-A"),
    ]

    as_of_result = compute_service_time(txns, [season], horizon_end=as_of)
    check(
        "as-of computation credits through the as-of date, unaffected by later transactions",
        as_of_result.by_season[2021]["raw_active_days"] == (as_of - season.start).days + 1,
    )

    full_result = compute_service_time(txns, [season], horizon_end=season.end)
    check(
        "later transactions still apply when horizon_end is not restricted",
        full_result.by_season[2021]["raw_active_days"] == 61 + 64,  # Apr1-May31, Aug1-Oct3
    )


def test_retired_player_clock_stops_at_final_season():
    """
    Regression test from the first live backfill batch, which credited 246 of
    500 retired players with 15+ years of service.

    A career usually ends with "elected free agency". That election now stops
    the clock (rules v9), so Angel Guzman, who last pitched in 2010, no longer
    needs the ceiling to avoid reading 20.030 years. The ceiling still matters
    when the career ends with no election the parser can see.
    """
    seasons = [
        SeasonWindow(y, dt.date(y, 3, 30), dt.date(y, 10, 1)) for y in range(2009, 2027)
    ]
    txns = [
        Transaction(dt.date(2009, 4, 10), "Cubs recalled RHP Angel Guzman."),
        Transaction(dt.date(2010, 11, 3), "RHP Angel Guzman elected free agency."),
    ]
    today = dt.date(2026, 8, 21)

    uncapped = compute_service_time(txns, seasons, horizon_end=today)
    check(
        "a free-agency election stops the clock without a ceiling",
        uncapped.total_years < 15,
    )
    check(
        "the election credits the seasons he played and nothing after",
        uncapped.formatted == "2.000",
    )

    capped = compute_service_time(
        txns, seasons, horizon_end=today, accrual_ceiling=dt.date(2010, 10, 1)
    )
    # Two seasons on the roster, each capped at the 172-day maximum.
    check("accrual ceiling stops the clock at the final season", capped.total_days == 344)
    check("retired player credited 2.000 years, not 20.030", capped.formatted == "2.000")
    check(
        "no season after the ceiling is credited",
        all(s["credited_days"] == 0 for y, s in capped.by_season.items() if y > 2010),
    )


def test_active_player_is_unaffected_by_free_agency_elections():
    """
    A player who elects free agency and re-signs must keep accruing. The
    election stops the clock; "Team signed free agent" starts it again.
    A minor-league deal uses the same verb and must not.
    """
    seasons = [
        SeasonWindow(y, dt.date(y, 3, 30), dt.date(y, 10, 1)) for y in range(2021, 2024)
    ]
    txns = [
        Transaction(dt.date(2021, 3, 30), "Mets selected the contract of RHP X."),
        Transaction(dt.date(2021, 11, 3), "RHP X elected free agency."),
        Transaction(dt.date(2021, 12, 1), "Rangers signed free agent RHP X."),
    ]
    result = compute_service_time(txns, seasons, horizon_end=dt.date(2023, 10, 1))
    check(
        "a re-signing after free agency restarts the clock",
        result.formatted == "3.000",
    )


def test_impossible_total_is_detected():
    """
    A record cannot credit more service time than its own season window allows.

    Transaction coverage starts in 2009, so nothing accrues before then, and
    accrual stops at the ceiling. Lew Ford's backfilled record showed 1,085
    days against a 2009-2013 window that caps at 5 * 172 = 860 -- proof that
    his clock ran through the years he spent in independent ball, where the
    move that ended his MLB tenure was never recognized as a stop.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    from backfill_history import max_creditable_days

    ford = {"mlb_debut": "2003-05-29", "accrual_ceiling": "2013-10-01"}
    check("coverage floor bounds the window, not the debut", max_creditable_days(ford) == 5 * 172)

    ok = {"mlb_debut": "2004-04-05", "accrual_ceiling": "2018-10-01"}
    check("a full 2009-2018 career caps at ten seasons", max_creditable_days(ok) == 10 * 172)

    check(
        "a still-accruing record has no bound to check",
        max_creditable_days({"mlb_debut": "2020-01-01", "accrual_ceiling": None}) is None,
    )


def test_real_feed_wording_is_matched():
    """
    The feed puts the player's position and name between a verb and its
    object, so plain substring keywords never matched. Measured over the
    64,635 cached descriptions, 11 of 16 keywords never fired once and 61%
    of transactions were ignored -- including DFA and outright, which are
    STOPS, so players removed from a roster kept accruing.

    Every string here is a real template taken from the cached feed.
    """
    from service_time import _is_active_start, _is_active_stop

    stops = [
        "Cleveland Guardians designated RHP Some Name for assignment.",
        "Boston Red Sox sent LHP Some Name outright to Worcester Red Sox.",
        "Chicago Cubs optioned RHP Some Name to Iowa Cubs.",
        "Miami Marlins released C Some Name.",
    ]
    for d in stops:
        check(f"stop: {d[:38]}...", _is_active_stop(d))

    starts = [
        "New York Mets recalled RHP Some Name from Syracuse Mets.",
        "Texas Rangers selected the contract of LHP Some Name.",
        "Seattle Mariners claimed C Some Name off waivers from Miami Marlins.",
        "Atlanta Braves activated RHP Some Name from the 15-day injured list.",
    ]
    for d in starts:
        check(f"start: {d[:38]}...", _is_active_start(d))

    # Placements and rehab stints keep accruing: a player on the IL is still
    # earning service time, and a rehab assignment means his MLB club still
    # holds him -- the affiliate fielding him is not a demotion.
    accruing = [
        "Chicago Cubs placed RHP Some Name on the 15-day injured list.",
        "Detroit Tigers placed LHP Some Name on the 15-day disabled list.",
        "Houston Astros sent RHP Some Name on a rehab assignment to Sugar Land.",
        "Chicago White Sox placed C Some Name on the paternity list.",
    ]
    for d in accruing:
        check(f"not a stop: {d[:34]}...", not _is_active_stop(d))

    # "sent ... outright" must not be confused with "sent ... on a rehab
    # assignment"; both start with the same verb.
    check(
        "rehab assignment is not read as an outright",
        not _is_active_stop("Houston Astros sent RHP Some Name on a rehab assignment to Sugar Land."),
    )


def test_major_league_signing_starts_the_clock_minor_league_does_not():
    """
    Measured against MLB's own 2014 rosters, this was the single largest
    error in the pipeline. Masahiro Tanaka signed in January 2014 and spent
    the season on the Yankees' active roster, but a signing produces no
    "activated" or "recalled" row, so his clock never opened and he was
    credited zero days -- wrong on 27 of 28 sampled dates.

    What makes it safe to fix is that the feed marks minor league deals
    explicitly: 534 major league free agent signings in the cache against
    828 that say "to a minor league contract". Only the former may start an
    MLB clock.
    """
    from service_time import _is_active_start

    check(
        "major league free agent signing starts the clock",
        _is_active_start("New York Yankees signed free agent RHP Masahiro Tanaka."),
    )
    check(
        "minor league contract does NOT start the clock",
        not _is_active_start(
            "Chicago Cubs signed free agent RHP Some Name to a minor league contract."
        ),
    )

    season = SeasonWindow(2014, dt.date(2014, 3, 22), dt.date(2014, 9, 28))
    signed = [Transaction(dt.date(2014, 1, 22), "New York Yankees signed free agent RHP X.")]
    minors = [
        Transaction(dt.date(2014, 1, 22), "New York Yankees signed free agent RHP X to a minor league contract.")
    ]
    check(
        "signing then sitting on the roster accrues the season",
        compute_service_time(signed, [season], horizon_end=season.end).total_days == 172,
    )
    check(
        "a minor league deal alone accrues nothing",
        compute_service_time(minors, [season], horizon_end=season.end).total_days == 0,
    )


def test_injured_list_placement_restarts_a_stopped_clock():
    """
    A player on his club's major league IL is accruing by definition. Listing
    an IL placement as merely non-stopping is enough for a player who was
    already active, but does nothing for one whose clock had already stopped.

    Measured against MLB's own 2018 rosters this was the largest remaining
    error: Ben Heller spent the season on the 60-day IL and was under-credited
    on every one of the 27 sampled dates -- 27 of the 37 total.
    """
    seasons = [
        SeasonWindow(y, dt.date(y, 3, 30), dt.date(y, 10, 1)) for y in (2017, 2018)
    ]
    txns = [
        Transaction(dt.date(2017, 4, 5), "Yankees recalled RHP X from Scranton."),
        Transaction(dt.date(2017, 6, 1), "Yankees optioned RHP X to Scranton."),
        # Back on a MAJOR LEAGUE list: accruing again, even though no recall
        # or activation ever follows.
        Transaction(dt.date(2018, 3, 30), "Yankees transferred RHP X to the 60-day injured list."),
    ]
    result = compute_service_time(txns, seasons, horizon_end=dt.date(2018, 10, 1))
    check(
        "60-day IL transfer restarts a clock stopped by an option",
        result.by_season[2018]["credited_days"] == 172,
    )

    # ...and the option itself must still stop it.
    stopped = compute_service_time(
        txns[:2], [seasons[0]], horizon_end=dt.date(2017, 10, 1)
    )
    check(
        "an option still stops the clock",
        stopped.by_season[2017]["credited_days"] < 172,
    )

    # A rehab assignment is not an IL placement and must not start anything.
    check(
        "rehab assignment does not start a stopped clock",
        not __import__("service_time")._is_active_start(
            "Houston Astros sent RHP X on a rehab assignment to Sugar Land."
        ),
    )


def test_missing_seasons_is_measured_per_player_not_assumed_from_a_year():
    """
    history_complete used to be `debut.year >= 2009`, on the premise that the
    feed carries nothing earlier. probe_coverage.py disproved that (finding
    #1): pre-2009 rows exist, they are just sparse. So the cutoff flagged
    good figures as "partial" and said nothing about how much was missing.

    What matters is whether the FRONT of a career is visible.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    import update_service_time as u

    def txns(*years):
        return [Transaction(dt.date(y, 6, 1), "Team recalled X.") for y in years]

    # Modern player, seen from his debut season: nothing missing.
    check(
        "debut season visible -> 0 missing",
        u._missing_seasons(dt.date(2016, 8, 13), txns(2016, 2018)) == 0,
    )

    # THE CASE THE OLD RULE GOT WRONG: a pre-2009 debut whose own debut
    # season IS in the feed. Complete, despite being "before the cutoff".
    check(
        "pre-2009 debut with visible debut season -> 0 missing",
        u._missing_seasons(dt.date(2003, 5, 29), txns(2003, 2006, 2012)) == 0,
    )

    # Genuinely missing the front of a career, and by a countable amount.
    check(
        "first sighting six years after debut -> 6 missing",
        u._missing_seasons(dt.date(2003, 5, 29), txns(2009, 2012)) == 6,
    )

    # Never reached the majors: nothing to be missing.
    check("no debut -> 0 missing", u._missing_seasons(None, txns(2024)) == 0)

    # Debuted but entirely invisible.
    check(
        "debut with no visible transactions -> everything missing",
        u._missing_seasons(dt.date(2005, 4, 1), []) > 0,
    )


def test_never_debuted_player_has_no_service_time():
    """A 40-man prospect with only minor league history accrues nothing."""
    txns = [
        Transaction(dt.date(2025, 3, 6), "activated LHP Gage Jump."),
        Transaction(dt.date(2025, 7, 11), "American League Futures activated LHP Gage Jump."),
    ]
    seasons = [SeasonWindow(2025, dt.date(2025, 3, 28), dt.date(2025, 10, 1))]
    result = compute_service_time(
        txns, seasons, accrual_floor=dt.date(2026, 5, 26), horizon_end=dt.date(2026, 5, 26)
    )
    check("minor league activations alone yield 0.000", result.formatted == "0.000")


def test_2020_season_is_prorated_to_a_full_year():
    """
    The 2020 season ran ~66 days instead of ~186. Per the MLB/MLBPA agreement,
    service time that year was scaled by 186/B rather than credited as raw
    days, so a player rostered all season earned a full year. Crediting raw
    days instead leaves every 2020 participant roughly 0.6 years short.
    """
    season_2020 = SeasonWindow(2020, dt.date(2020, 7, 23), dt.date(2020, 9, 27))
    txns = [Transaction(dt.date(2020, 7, 23), "Yankees selected the contract of X")]

    result = compute_service_time(txns, [season_2020], horizon_end=dt.date(2020, 9, 27))
    check(
        "full 2020 season credits a full year (172 days)",
        result.by_season[2020]["credited_days"] == 172,
    )
    check("full 2020 season formats as 1.000", result.formatted == "1.000")
    check(
        "raw 2020 days are still recorded unscaled for transparency",
        result.by_season[2020]["raw_active_days"] == 67,
    )


def test_2020_partial_season_scales_proportionally():
    """Half of 2020 on the roster should credit far more than its raw days."""
    season_2020 = SeasonWindow(2020, dt.date(2020, 7, 23), dt.date(2020, 9, 27))
    txns = [
        Transaction(dt.date(2020, 8, 25), "Yankees selected the contract of X"),
    ]
    result = compute_service_time(txns, [season_2020], horizon_end=dt.date(2020, 9, 27))
    raw = result.by_season[2020]["raw_active_days"]
    credited = result.by_season[2020]["credited_days"]
    check("partial 2020 stint is scaled up, not credited raw", credited > raw)
    check("partial 2020 stint still falls short of a full year", credited < 172)


def test_normal_season_is_not_prorated():
    """Proration must apply to 2020 only and leave every other year alone."""
    season = SeasonWindow(2021, dt.date(2021, 4, 1), dt.date(2021, 10, 3))
    txns = [Transaction(dt.date(2021, 8, 1), "Yankees selected the contract of X")]
    result = compute_service_time(txns, [season], horizon_end=dt.date(2021, 10, 3))
    check(
        "2021 credits raw days with no scaling",
        result.by_season[2021]["credited_days"] == result.by_season[2021]["raw_active_days"],
    )


# --- carry-in ------------------------------------------------------------
#
# Written from a live discrepancy, not invented. Justin Verlander debuted
# 2005-07-04 and his first major-league transaction of any kind is
# 2015-04-08 -- he sat on Detroit's roster for a decade without being
# optioned, DFA'd, released or injured, so the feed had nothing to say about
# him. Ten seasons produced no interval and he read 11.000 against a
# published x.yyy.


def _verlander_shaped_seasons(first: int, last: int) -> list[SeasonWindow]:
    return [
        SeasonWindow(y, dt.date(y, 3, 28), dt.date(y, 10, 1))
        for y in range(first, last + 1)
    ]


def test_carry_in_credits_a_player_who_simply_never_left_the_roster():
    seasons = _verlander_shaped_seasons(2005, 2008)
    debut = dt.date(2005, 7, 4)
    txns = [
        Transaction(dt.date(2008, 4, 8), "Detroit Tigers placed RHP X on the 15-day disabled list."),
    ]
    horizon = dt.date(2008, 10, 1)

    without = compute_service_time(
        txns, seasons, horizon_end=horizon, accrual_floor=debut
    )
    with_carry_in = compute_service_time(
        txns, seasons, horizon_end=horizon, accrual_floor=debut,
        presume_active_from_debut=True,
    )

    check(
        "without carry-in, a decade on the roster credits only from the first "
        "transaction",
        without.by_season[2006]["credited_days"] == 0,
    )
    check(
        "carry-in credits the seasons between debut and first transaction",
        with_carry_in.by_season[2006]["credited_days"] == 172,
    )
    check(
        "carry-in does not credit before the debut date",
        with_carry_in.by_season[2005]["credited_days"]
        < with_carry_in.by_season[2006]["credited_days"],
    )
    check(
        "carry-in raises the total, and only the total it should",
        with_carry_in.total_days > without.total_days,
    )


def test_carry_in_is_closed_by_the_first_stop_it_meets():
    """The presumption is a default, not a floor -- a demotion still ends it."""
    seasons = _verlander_shaped_seasons(2010, 2012)
    debut = dt.date(2010, 4, 5)
    txns = [
        Transaction(dt.date(2011, 5, 1), "Detroit Tigers optioned RHP X to Toledo Mud Hens."),
    ]
    result = compute_service_time(
        txns, seasons, horizon_end=dt.date(2012, 10, 1), accrual_floor=debut,
        presume_active_from_debut=True,
    )
    check(
        "carry-in accrues up to the option",
        result.by_season[2010]["credited_days"] == 172,
    )
    check(
        "carry-in stops at the option and does not resume on its own",
        result.by_season[2012]["credited_days"] == 0,
    )


def test_carry_in_ignores_pre_debut_transactions():
    """
    A pre-debut stop must not close the carried-in interval before it opens.
    The feed carries a player's whole tracked history, so college and minor
    league rows sit in front of the debut; they cannot bear on whether he was
    on a major league roster afterwards.
    """
    seasons = _verlander_shaped_seasons(2015, 2016)
    debut = dt.date(2015, 6, 1)
    txns = [
        Transaction(dt.date(2015, 4, 2), "Toledo Mud Hens optioned RHP X to Erie SeaWolves."),
    ]
    result = compute_service_time(
        txns, seasons, horizon_end=dt.date(2016, 10, 1), accrual_floor=debut,
        presume_active_from_debut=True,
    )
    check(
        "a pre-debut stop does not cancel carry-in",
        result.by_season[2016]["credited_days"] == 172,
    )


def test_carry_in_still_respects_the_accrual_ceiling():
    """A retired player's clock must still stop at his last season."""
    seasons = _verlander_shaped_seasons(2012, 2014)
    debut = dt.date(2012, 4, 10)
    result = compute_service_time(
        [], seasons, horizon_end=dt.date(2014, 10, 1), accrual_floor=debut,
        accrual_ceiling=dt.date(2013, 10, 1),
        presume_active_from_debut=True,
    )
    check(
        "carry-in accrues up to the ceiling",
        result.by_season[2013]["credited_days"] == 172,
    )
    check(
        "carry-in does not run past the ceiling",
        result.by_season[2014]["credited_days"] == 0,
    )


def test_carry_in_is_inert_without_a_debut_date():
    """No debut means no defensible place to start presuming."""
    seasons = _verlander_shaped_seasons(2015, 2016)
    result = compute_service_time(
        [], seasons, horizon_end=dt.date(2016, 10, 1), accrual_floor=None,
        presume_active_from_debut=True,
    )
    check(
        "carry-in credits nothing when there is no debut date",
        result.total_days == 0,
    )


def test_carry_in_must_be_asked_for_at_the_api_level():
    """
    compute_service_time() itself stays conservative: no presumption unless
    the caller opts in. The pipeline opts in (see the next test); a direct
    caller has to say so.
    """
    seasons = _verlander_shaped_seasons(2015, 2016)
    debut = dt.date(2015, 4, 10)
    result = compute_service_time(
        [], seasons, horizon_end=dt.date(2016, 10, 1), accrual_floor=debut
    )
    check("compute_service_time does not presume unless asked", result.total_days == 0)


def test_pipeline_has_carry_in_on():
    """
    Turned on 2026-08-22 after clearing the roster gate in both eras
    (2014: 96.5% -> 96.8% agreement; 2018: 99.0% -> 99.0%), with
    over-crediting flat at 0.2% in both -- the number that decided it.

    Set PRESUME_ACTIVE_FROM_DEBUT=0 to score the old behaviour.
    """
    import update_service_time  # noqa: PLC0415 -- import here to keep this test optional

    check(
        "the pipeline presumes a player is rostered from his debut",
        update_service_time.PRESUME_ACTIVE_FROM_DEBUT is True,
    )


# --- player profiles -----------------------------------------------------


def test_season_provenance_is_classified_three_ways():
    """
    A profile has to say how each season is known. After carry-in, seasons
    behind a total differ a lot in confidence and presenting them as equal
    would overstate what the feed can actually see.
    """
    import update_service_time as U  # noqa: PLC0415

    by_season = {y: {"credited_days": 172, "raw_active_days": 188, "prorated_days": 188}
                 for y in range(2013, 2017)}
    txns = [
        Transaction(dt.date(2015, 4, 8), "Detroit Tigers placed RHP X on the 15-day disabled list."),
    ]
    rows = U._build_seasons(by_season, txns, {}, {2015: [116]}, 2015)
    src = {r["y"]: r["src"] for r in rows}

    check("a season earlier than any transaction is 'presumed'", src[2013] == "presumed")
    check("a season with transactions is 'read'", src[2015] == "read")
    check("a later silent season is 'carried'", src[2016] == "carry")


def test_season_teams_prefer_stats_then_transactions_then_carry_forward():
    """
    15% of accruing seasons have no transaction naming a club -- a player who
    simply plays all year generates none. Stat splits fill those in; failing
    that, the last known club carries forward, because changing clubs would
    have produced a transaction.
    """
    import update_service_time as U  # noqa: PLC0415

    by_season = {y: {"credited_days": 172, "raw_active_days": 172, "prorated_days": 172}
                 for y in (2020, 2021, 2022)}
    txns = [Transaction(dt.date(2020, 4, 1), "New York Yankees recalled RF X.")]
    rows = U._build_seasons(
        by_season,
        txns,
        {2022: [111]},        # stat splits say Boston in 2022
        {2020: [147]},        # a transaction says the Yankees in 2020
        2020,
    )
    teams = {r["y"]: r["t"] for r in rows}

    check("a transaction names the club for its own season", teams[2020] == [147])
    check("a silent season carries the last known club forward", teams[2021] == [147])
    check("stat splits win over carry-forward", teams[2022] == [111])


def test_profile_rows_sum_to_the_published_total():
    """
    The invariant write_profiles() enforces. If the seasons disagreed with
    the total, the profile page would contradict the table it was opened
    from, and a reader would rightly stop trusting both.
    """
    import update_service_time as U  # noqa: PLC0415

    season = SeasonWindow(2023, dt.date(2023, 3, 30), dt.date(2023, 10, 1))
    txns = [Transaction(dt.date(2023, 5, 1), "New York Yankees recalled RF X.")]
    result = compute_service_time(txns, [season], horizon_end=dt.date(2023, 10, 1))
    rows = U._build_seasons(result.by_season, txns, {}, {}, 2023)
    check(
        "season rows sum to the total",
        sum(r["d"] for r in rows) == result.total_days,
    )


# --- Super Two -----------------------------------------------------------
#
# Written from the rule, not from the old heuristic. The heuristic treated
# "86 days into your third year" as the whole test; the real rule states the
# 86-day floor and the top-22% ranking as ONE condition -- mlb.com's glossary
# puts it in a single sentence -- so the 86-day floor defines who is IN the
# class being ranked, not a filter applied after it. See the "CORRECTED
# 2026-10-30" note in scripts/super_two.py.


def _s2_player(pid, name, rows):
    return {"id": pid, "name": name, "seasons": [{"y": y, "d": d} for y, d in rows],
            "service_days_total": sum(d for _, d in rows)}


def _s2_league(n, year=2025):
    """n players spread evenly across the 2.000-3.000 band, each with
    86..171 days in `year` -- clearing the class's 86-day floor -- so every
    one of them lands in that year's class."""
    db = {}
    for i in range(n):
        days_in_year = 86 + (i * 85) // max(n - 1, 1)
        total = 2 * 172 + days_in_year
        db[str(i)] = _s2_player(i, f"P{i:03d}", [(year - 2, 172), (year - 1, 172), (year, days_in_year)])
        assert 2 * 172 <= total < 3 * 172
    return db


def test_super_two_cutoff_is_the_top_22_percent_boundary():
    import super_two as S  # noqa: PLC0415

    db = _s2_league(100)
    cutoff = S.compute_cutoff(db, 2025)
    check("all 100 players form the class", cutoff["class_size"] == 100)
    check("22 of 100 qualify", cutoff["qualifying_count"] == 22)
    ranked = S.build_class(db, 2025)
    check(
        "the cutoff is the 22nd-ranked player's service time",
        cutoff["cutoff_days"] == ranked[21][0],
    )


def test_super_two_class_excludes_players_who_did_not_play_that_year():
    """A retired player sitting at 2.5 years is not in this year's class."""
    import super_two as S  # noqa: PLC0415

    db = _s2_league(60)
    db["retired"] = _s2_player(9001, "Retired Guy", [(2014, 172), (2015, 172), (2016, 100)])
    cutoff = S.compute_cutoff(db, 2025)
    check("the retired player is not counted in the class", cutoff["class_size"] == 60)


def test_super_two_class_requires_86_days_not_just_any_accrual():
    """
    mlb.com's own glossary states the 86-day floor and the top-22% ranking
    as one condition, not two: "rank in the top 22 percent ... among those
    who have amassed between two and three years ... and have accrued at
    least 86 days ... during the preceding season." The floor describes who
    is IN the pool being ranked. Before 2026-10-30 a player with a single
    day of accrual still inflated the class denominator even though he
    could never clear the 86-day floor to qualify.
    """
    import super_two as S  # noqa: PLC0415

    db = _s2_league(60)
    db["cup_of_coffee"] = _s2_player(
        9004, "Cup Of Coffee", [(2023, 172), (2024, 172), (2025, 10)]
    )
    cutoff = S.compute_cutoff(db, 2025)
    check(
        "a player with <86 days in the season is not in the class at all",
        cutoff["class_size"] == 60,
    )
    check(
        "band_size still counts him, so the two numbers can disagree",
        cutoff["band_size"] == 61,
    )
    ranked = S.build_class(db, 2025)
    check(
        "...he does not appear in the ranked class either",
        all(record.get("name") != "Cup Of Coffee" for _, _, record in ranked),
    )


def test_super_two_rounds_the_qualifying_count_rather_than_flooring_it():
    """
    85 * 22% = 18.7. The old floor() dropped the 19th-ranked player; "top 22
    percent" rounded to the nearest whole number admits him. This is the
    change (together with the 86-day class fix above) that took the
    computed 2022 cutoff from +7 days off the published figure to +1, and
    2021 from +0 to an exact match -- see data/reference_super_two.json and
    scripts/validate_super_two.py.
    """
    import super_two as S  # noqa: PLC0415

    db = _s2_league(85)
    cutoff = S.compute_cutoff(db, 2025)
    check("85 players form the class", cutoff["class_size"] == 85)
    check("18.7 rounds up to 19, not down to 18", cutoff["qualifying_count"] == 19)


def test_super_two_requires_86_days_in_the_preceding_season():
    import super_two as S  # noqa: PLC0415

    db = _s2_league(100)
    # Top of the band, but only a cup of coffee last year.
    db["brief"] = _s2_player(9002, "Brief Stint", [(2023, 172), (2024, 172), (2025, 60)])
    S.apply_super_two(db, 2025)
    check(
        "a player above the cutoff with <86 days last season is not flagged",
        db["brief"]["super_two_candidate"] is False,
    )


def test_super_two_flags_only_the_two_to_three_year_band():
    import super_two as S  # noqa: PLC0415

    db = _s2_league(100)
    db["veteran"] = _s2_player(9003, "Veteran", [(2023, 172), (2024, 172), (2025, 172), (2022, 172)])
    S.apply_super_two(db, 2025)
    check(
        "a player past 3.000 is not a Super Two candidate",
        db["veteran"]["super_two_candidate"] is False,
    )
    check(
        "but he is plainly arbitration eligible",
        db["veteran"]["arbitration_eligible"] is True,
    )


def test_load_reported_cutoff_falls_back_gracefully():
    """
    load_reported_cutoff() is the one hand-kept, unofficial input in the
    pipeline (data/super_two_reported.json) -- Job 8's brief says a missing
    or malformed file, or one whose season does not match the season being
    projected, must fall back to the single computed cutoff rather than
    error. Every branch here is a way that can happen.
    """
    import tempfile
    import super_two as S  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)

        missing = tmp / "does_not_exist.json"
        check("a missing file returns None", S.load_reported_cutoff(path=missing) is None)

        malformed = tmp / "malformed.json"
        malformed.write_text("{not valid json")
        check("invalid JSON returns None", S.load_reported_cutoff(path=malformed) is None)

        not_an_object = tmp / "not_an_object.json"
        not_an_object.write_text("[1, 2, 3]")
        check("a JSON array (not an object) returns None",
              S.load_reported_cutoff(path=not_an_object) is None)

        missing_fields = tmp / "missing_fields.json"
        missing_fields.write_text('{"season": 2026}')
        check("a missing cutoff field returns None",
              S.load_reported_cutoff(path=missing_fields) is None)

        bad_figure = tmp / "bad_figure.json"
        bad_figure.write_text('{"season": 2026, "cutoff": "not-a-number"}')
        check("an unparseable cutoff figure returns None",
              S.load_reported_cutoff(path=bad_figure) is None)

        no_dot = tmp / "no_dot.json"
        no_dot.write_text('{"season": 2026, "cutoff": "2132"}')
        check("a figure with no years.days dot returns None",
              S.load_reported_cutoff(path=no_dot) is None)

        good = tmp / "good.json"
        good.write_text('{"season": 2026, "cutoff": "2.132", "source": "reported", "entered": "2026-10-30"}')
        loaded = S.load_reported_cutoff(path=good)
        check("a well-formed file loads", loaded is not None)
        check("cutoff_days is parsed correctly", loaded["cutoff_days"] == 2 * S.FULL_YEAR_DAYS + 132)
        check("source and entered pass through", loaded["source"] == "reported" and loaded["entered"] == "2026-10-30")
        check("a guess is not treated as official", loaded["official"] is False)

        official = tmp / "official.json"
        official.write_text(
            '{"season": 2026, "cutoff": "2.133", "official": true, "source": "official"}'
        )
        loaded_official = S.load_reported_cutoff(path=official)
        check("official: true is kept", loaded_official["official"] is True)
        check("2.133 is 2 years and 133 days",
              loaded_official["cutoff_days"] == 2 * S.FULL_YEAR_DAYS + 133)

        check("a season filter that matches keeps the figure",
              S.load_reported_cutoff(path=good, projected_season=2026) is not None)
        check("a season filter that does not match falls back to None",
              S.load_reported_cutoff(path=good, projected_season=2025) is None)


def test_cutoff_range_orders_low_and_high_and_handles_no_reported_figure():
    """
    cutoff_range() is the one place the low/high band is assembled. Low is
    always this project's own computed cutoff; high is the reported figure
    for the SAME season when one exists, ordered with min()/max() rather
    than assumed, so a reported figure below the computed one (it is
    someone's guess, not a guarantee) still produces a valid band instead
    of a crossed one. No computed cutoff at all collapses to None.
    """
    import super_two as S  # noqa: PLC0415

    computed = {"season": 2026, "cutoff_days": 469, "cutoff": "2.125"}

    check("no computed cutoff at all returns None",
          S.cutoff_range(None, {"season": 2026, "cutoff_days": 476}) is None)

    no_reported = S.cutoff_range(computed, None)
    check("no reported figure collapses to a single-valued range",
          no_reported["low_days"] == no_reported["high_days"] == 469
          and no_reported["is_range"] is False)

    wrong_season = S.cutoff_range(computed, {"season": 2025, "cutoff_days": 476})
    check("a reported figure for a different season is ignored",
          wrong_season["is_range"] is False and wrong_season["high_days"] == 469)

    higher = S.cutoff_range(computed, {"season": 2026, "cutoff_days": 476})
    check("a reported figure above the computed one becomes the high end",
          higher["low_days"] == 469 and higher["high_days"] == 476 and higher["is_range"] is True)

    lower = S.cutoff_range(computed, {"season": 2026, "cutoff_days": 460})
    check("a reported figure BELOW the computed one still orders low <= high",
          lower["low_days"] == 460 and lower["high_days"] == 469 and lower["is_range"] is True)

    equal = S.cutoff_range(computed, {"season": 2026, "cutoff_days": 469})
    check("an identical reported figure is a single line, not a zero-width range",
          equal["is_range"] is False and equal["low_days"] == equal["high_days"] == 469)


def test_apply_super_two_sets_the_bubble_flag_from_a_reported_range():
    """
    apply_super_two()'s optional `reported` argument must never change WHO
    is flagged super_two_candidate -- that stays "at or above this
    project's own computed cutoff", the number every other selector and
    badge on the site already keys off. It only adds super_two_bubble:
    True for a candidate whose service sits below the reported high end.
    """
    import super_two as S  # noqa: PLC0415

    db = _s2_league(100, year=2025)
    cutoff = S.compute_cutoff(db, 2025)
    low_days = cutoff["cutoff_days"]

    # A reported figure a few days above the computed cutoff opens a real
    # bubble band.
    reported = {"season": 2025, "cutoff_days": low_days + 5}
    result = S.apply_super_two(db, 2025, reported=reported)
    check("apply_super_two still returns the cutoff metadata", result is not None)
    check("the range is attached to the returned cutoff", result["range"]["is_range"] is True)

    at_low = next(p for p in db.values() if p.get("service_days_total") == low_days)
    check("a candidate exactly at the low end is a candidate", at_low["super_two_candidate"] is True)
    check("...and is on the bubble (below the reported high end)", at_low["super_two_bubble"] is True)

    above_high = [p for p in db.values() if p.get("service_days_total", 0) >= low_days + 5]
    check("at least one player clears the reported high end in this fixture", len(above_high) > 0)
    for p in above_high:
        check(f"{p['name']} at/above the high end is a candidate but not on the bubble",
              p["super_two_candidate"] is True and p["super_two_bubble"] is False)

    # With no reported figure at all, nobody is ever on the bubble -- the
    # graceful single-cutoff fallback.
    db2 = _s2_league(100, year=2025)
    S.apply_super_two(db2, 2025, reported=None)
    check("with no reported figure, no one is flagged on the bubble",
          not any(p.get("super_two_bubble") for p in db2.values()))


def test_an_official_cutoff_decides_who_is_super_two():
    """A published figure replaces the computed line. Players under it are
    out even when our own ranking had them in; players on it are in."""
    import super_two as S  # noqa: PLC0415

    db = _s2_league(100, year=2025)
    computed = S.compute_cutoff(db, 2025)
    line = computed["cutoff_days"]
    reported = {
        "season": 2025,
        "cutoff_days": line + 1,
        "cutoff": S.format_service(line + 1),
        "official": True,
    }
    result = S.apply_super_two(db, 2025, reported=reported)
    check("the figure the site shows is the official one", result["cutoff"] == reported["cutoff"])
    check("the site does not show a range around a guess", result["range"]["is_range"] is False)
    check("our own computed line is kept beside it", result["computed_cutoff_days"] == line)

    at_computed = next(p for p in db.values() if p.get("service_days_total") == line)
    check("a player under the official line is not Super Two",
          at_computed["super_two_candidate"] is False)
    on_or_above = [p for p in db.values() if p.get("service_days_total", 0) >= line + 1]
    check("players at or above the official line stay Super Two",
          len(on_or_above) > 0 and all(p["super_two_candidate"] for p in on_or_above))


def test_super_two_declines_to_guess_from_a_tiny_population():
    """
    A partial dataset would otherwise produce a confident-looking cutoff
    drawn through a handful of players.
    """
    import super_two as S  # noqa: PLC0415

    check("no cutoff from 10 players", S.compute_cutoff(_s2_league(10), 2025) is None)


def test_validate_super_two_counts_failures_and_would_exit_nonzero():
    """
    `validate_super_two.py`'s exit code is what the "Validate Service Time"
    Action gates on -- a mismatch has to make `failures` nonzero, not just
    print FAIL to a log nobody reads. Pinned directly against `evaluate()`,
    the pure function `main()` calls before deciding `sys.exit()`, rather
    than via subprocess, so it is fast, deterministic, and independent of
    whatever the live reference file says on a given day.
    """
    import validate_super_two as V  # noqa: PLC0415
    import super_two as S  # noqa: PLC0415

    db = _s2_league(100)
    real_cutoff = S.compute_cutoff(db, 2025)
    matching = S.format_service(real_cutoff["cutoff_days"])
    far_off = S.format_service(real_cutoff["cutoff_days"] + 50)

    _, failures, entered, pending = V.evaluate(
        db, [{"season": 2025, "published": matching}], tolerance_days=2
    )
    check("a matching published figure passes", failures == 0)
    check("...and the script would exit 0", (1 if failures else 0) == 0)
    check("it is counted as entered", entered == 1 and pending == 0)

    _, failures, entered, pending = V.evaluate(
        db, [{"season": 2025, "published": far_off}], tolerance_days=2
    )
    check("a far-off published figure fails", failures == 1)
    check("...and the script would exit nonzero", (1 if failures else 0) == 1)

    _, failures, entered, pending = V.evaluate(
        db, [{"season": 2025, "published": None}], tolerance_days=2
    )
    check("a row awaiting a figure never fails", failures == 0)
    check("it is counted as pending, not entered", pending == 1 and entered == 0)

    # A season too small to draw a cutoff through must still fail against a
    # published figure -- "we refuse to compute" cannot score as "ok".
    _, failures, entered, pending = V.evaluate(
        _s2_league(10), [{"season": 2025, "published": "2.130"}], tolerance_days=2
    )
    check("a class too small to measure fails rather than passing vacuously", failures == 1)


def test_qualifying_season_follows_the_season_being_played():
    """
    The 86-day test looks at the season preceding the offseason being
    projected -- which is the one in progress, not the one the cutoff came
    from. Anthony Kay is the case: nothing in 2025, 151 days in 2026.
    """
    import super_two as S  # noqa: PLC0415

    db = _s2_league(100, year=2025)
    for record in db.values():
        record["seasons"].append({"y": 2026, "d": 151})
    check(
        "with the current season well under way, it is the one tested",
        S.qualifying_season(db, 2025) == 2026,
    )


def test_qualifying_season_falls_back_early_in_a_season():
    """
    Switching in April would empty the list rather than project it, since
    nobody has 86 days yet.
    """
    import super_two as S  # noqa: PLC0415

    db = _s2_league(100, year=2025)
    for record in db.values():
        record["seasons"].append({"y": 2026, "d": 12})   # two weeks in
    check(
        "an infant season is not used for the 86-day test",
        S.qualifying_season(db, 2025) == 2025,
    )


def test_latest_complete_season_is_measured_not_assumed():
    """
    `today_year - 1` goes stale every offseason and would publish a cutoff a
    full season out of date. A season is finished when somebody has banked
    the 172-day cap in it.
    """
    import super_two as S  # noqa: PLC0415

    mid = _s2_league(100, year=2025)
    for record in mid.values():
        record["seasons"].append({"y": 2026, "d": 100})
    check("a half-played season is not treated as complete",
          S.latest_complete_season(mid, 2026) == 2025)

    done = _s2_league(100, year=2025)
    for record in done.values():
        record["seasons"].append({"y": 2026, "d": 172})
    check("a finished season is", S.latest_complete_season(done, 2026) == 2026)


# --- same-date transactions ----------------------------------------------
#
# From Ben Bowden, Tampa Bay 2022, who scored over=4 agree=0 on the roster
# check while his stored record credited him 0 days that season. Both
# numbers came from the same code; what differed was the order two rows
# dated 2022-04-29 arrived in.


def _bowden_rows():
    return {
        "recall": Transaction(dt.date(2021, 10, 5), "Colorado Rockies recalled LHP X from Albuquerque Isotopes."),
        "option": Transaction(dt.date(2022, 3, 31), "Colorado Rockies optioned LHP X to Albuquerque Isotopes."),
        "claim": Transaction(dt.date(2022, 4, 29), "Tampa Bay Rays claimed LHP X off waivers from Colorado Rockies."),
        "sent_down": Transaction(dt.date(2022, 4, 29), "Tampa Bay Rays optioned LHP X to Durham Bulls."),
    }


def test_same_date_start_and_stop_do_not_depend_on_feed_order():
    r = _bowden_rows()
    horizon = dt.date(2022, 10, 5)
    floor = dt.date(2021, 4, 2)
    a = build_global_active_intervals(
        [r["recall"], r["option"], r["claim"], r["sent_down"]], horizon, accrual_floor=floor
    )
    b = build_global_active_intervals(
        [r["recall"], r["option"], r["sent_down"], r["claim"]], horizon, accrual_floor=floor
    )
    check("feed order does not change the intervals", a == b)


def test_claimed_and_optioned_the_same_day_credits_nothing():
    """He never reaches the active roster, so the day is not service time."""
    r = _bowden_rows()
    intervals = build_global_active_intervals(
        [r["recall"], r["option"], r["claim"], r["sent_down"]],
        dt.date(2022, 10, 5),
        accrual_floor=dt.date(2021, 4, 2),
    )
    in_2022 = [iv for iv in intervals if iv[1] >= dt.date(2022, 4, 7)]
    check("nothing is credited after the same-day claim and option", in_2022 == [])


def test_activated_off_the_il_then_optioned_stops_the_clock():
    """
    The commonest same-date pair in the data (127 of 223), and the one that
    killed the "a pair is a wash" rule. He is already accruing on the IL, so
    leaving him unchanged keeps him accruing through an option -- measured
    across the cache, that added 5,623 days.
    """
    txns = [
        Transaction(dt.date(2025, 4, 1), "Cincinnati Reds recalled LHP X from Louisville Bats."),
        Transaction(dt.date(2025, 5, 21), "Cincinnati Reds activated LHP X from the 15-day injured list."),
        Transaction(dt.date(2025, 5, 21), "Cincinnati Reds optioned LHP X to Louisville Bats."),
    ]
    intervals = build_global_active_intervals(
        txns, dt.date(2025, 10, 1), accrual_floor=dt.date(2025, 4, 1)
    )
    check(
        "the option ends the interval on the day it is dated",
        intervals == [(dt.date(2025, 4, 1), dt.date(2025, 5, 20))],
    )
    check("and nothing reopens afterwards", len(intervals) == 1)


def test_no_interval_ever_ends_before_it_starts():
    """
    The old walk could emit (2022-04-29, 2022-04-28). Harmless where it was
    guarded, but a signal that the state machine had been driven somewhere
    it should not go.
    """
    r = _bowden_rows()
    intervals = build_global_active_intervals(
        list(r.values()), dt.date(2022, 10, 5), accrual_floor=dt.date(2021, 4, 2)
    )
    check("every interval is well formed", all(s <= e for s, e in intervals))


def test_a_player_who_never_debuted_gets_no_presumed_service():
    """
    The floor falls back to a player's earliest major-league-club
    transaction when he has no debut date -- for a prospect that is the day
    he was signed or drafted. Carry-in must not anchor there: 34 players who
    have never appeared in a major league game were credited service time,
    one of them 5.000 years.
    """
    import update_service_time as U  # noqa: PLC0415

    seasons = [SeasonWindow(y, dt.date(y, 3, 28), dt.date(y, 10, 1)) for y in range(2021, 2026)]
    signed = [Transaction(dt.date(2021, 1, 15), "Texas Rangers signed free agent RHP X to a minor league contract.")]
    # What build_player_record passes when there is no debut: floor falls back
    # to the signing, and carry-in is disabled because debut_date is None.
    result = compute_service_time(
        signed, seasons, horizon_end=dt.date(2025, 10, 1),
        accrual_floor=dt.date(2021, 1, 15),
        presume_active_from_debut=False,
    )
    check("an undebuted prospect accrues nothing", result.total_days == 0)


def test_an_inferred_season_needs_something_to_corroborate_it():
    """
    Erick Almonte: debuted 2001, ~30 major league games, no transaction of
    any kind until 2011. Carry-in credited him 2005-2010 at the full cap --
    six seasons with no stat line and no transaction in any of them, 6.064
    years. Measured across the database: 57 players, 112 seasons, 112
    player-years.
    """
    seasons = [SeasonWindow(y, dt.date(y, 3, 28), dt.date(y, 10, 1)) for y in range(2005, 2009)]
    txns = [Transaction(dt.date(2008, 6, 1), "Milwaukee Brewers recalled SS X from Triple-A.")]
    debut = dt.date(2001, 9, 4)
    kw = dict(horizon_end=dt.date(2008, 10, 1), accrual_floor=debut,
              presume_active_from_debut=True)

    unbounded = compute_service_time(txns, seasons, **kw)
    check(
        "without evidence the presumption credits every silent season",
        unbounded.by_season[2005]["credited_days"] == 172,
    )

    bounded = compute_service_time(txns, seasons, seasons_with_appearances=set(), **kw)
    check(
        "with the guard, a season with no appearance and no transaction is dropped",
        bounded.by_season[2005]["credited_days"] == 0,
    )
    check(
        "the season that does have a transaction is still credited",
        bounded.by_season[2008]["credited_days"] > 0,
    )


def test_an_appearance_alone_is_enough_to_keep_a_presumed_season():
    """
    Verlander's 2005-2014 are presumed with no transactions, but his splits
    place him on Detroit in every one. The guard must not touch him.
    """
    seasons = [SeasonWindow(y, dt.date(y, 3, 28), dt.date(y, 10, 1)) for y in range(2006, 2010)]
    result = compute_service_time(
        [], seasons,
        seasons_with_appearances={2006, 2007, 2008, 2009},
        horizon_end=dt.date(2009, 10, 1),
        accrual_floor=dt.date(2005, 7, 4),
        presume_active_from_debut=True,
    )
    check(
        "a presumed season backed by an appearance is credited in full",
        all(result.by_season[y]["credited_days"] == 172 for y in range(2006, 2010)),
    )


def test_a_minor_league_signing_does_not_extend_a_career():
    """
    Aaron Sanchez, no lastPlayedDate, last major league appearance 2022:

      2024-08-06  Buffalo Bisons released RHP Aaron Sanchez
      2026-01-27  Kansas City Royals signed him to a MINOR LEAGUE contract
      2026-06-23  Omaha Storm Chasers released RHP Aaron Sanchez

    Both releases are by minor league clubs, so the club filter drops them
    and nothing closes his interval. The Kansas City row names a major
    league club and survives, so the old "last transaction of any kind"
    fallback put his career end in 2026 -- one open interval from 2022 to
    today, four years of phantom service.

    A minor league signing is not a roster event, so it must not be what
    decides when a career ended.
    """
    kc = "Kansas City Royals signed free agent RHP X to a minor league contract and invited him to spring training."
    check("a minor league signing is not a start", is_active_start(kc) is False)
    check("nor a stop", is_active_stop(kc) is False)

    recall = "Toronto Blue Jays recalled RHP X from Buffalo Bisons."
    check("an actual roster move still reads as one", is_active_start(recall) is True)


def test_a_player_on_the_60_day_il_is_still_on_a_major_league_roster():
    """Rules v7 regression. A player stashed on the 60-day injured list is still
    carried by his club and still accruing, but he drops off the 40-man LISTING,
    so the backfill recomputes him with currently_rostered=False. Capping his
    career at his last game then erased every IL season after it -- Anthony
    Rendon lost 345 days (two full seasons on the 60-day IL), Brusdar Graterol a
    year each, ~12 players in all. _still_on_ml_roster is the guard that, with
    the bio's active flag, keeps such a player accruing: his latest major-league
    move is an IL placement (a START), not a release/outright/option/DFA."""
    import update_service_time as u

    # Rendon: last move is a transfer to the 60-day IL -- an IL placement reads
    # as a start (is_active_start), so he is still on a major league roster.
    rendon = [
        Transaction(dt.date(2024, 4, 1), "Los Angeles Angels activated 3B Anthony Rendon."),
        Transaction(dt.date(2025, 3, 27), "Los Angeles Angels placed 3B Anthony Rendon on the 10-day injured list."),
        Transaction(dt.date(2025, 5, 20), "Los Angeles Angels transferred 3B Anthony Rendon to the 60-day injured list."),
    ]
    check("a player whose latest move is a 60-day IL transfer is still on a roster",
          u._still_on_ml_roster(rendon) is True)

    # Graterol: optioned/recalled churn, but ends the window on a 60-day IL row.
    graterol = [
        Transaction(dt.date(2024, 7, 1), "Los Angeles Dodgers activated RHP Brusdar Graterol."),
        Transaction(dt.date(2025, 2, 15), "Los Angeles Dodgers placed RHP Brusdar Graterol on the 60-day injured list."),
    ]
    check("Graterol on the 60-day IL is still on a roster",
          u._still_on_ml_roster(graterol) is True)

    # A plainly active player (last move an activation) is on a roster.
    active = [Transaction(dt.date(2026, 4, 1), "Club recalled RHP Y from Triple-A.")]
    check("a recalled player is still on a roster", u._still_on_ml_roster(active) is True)

    # A trade keeps him in the majors (joins the new 40-man).
    traded = [
        Transaction(dt.date(2026, 5, 1), "Club A recalled RHP Z from Triple-A."),
        Transaction(dt.date(2026, 6, 1), "Club A traded RHP Z to Club B."),
    ]
    check("a traded player is still on a roster", u._still_on_ml_roster(traded) is True)

    # Aaron Sanchez: his last major-league move is a release (a STOP), so the
    # cap must still bite -- the guard is False and he is NOT revived.
    sanchez = [
        Transaction(dt.date(2022, 4, 23), "Washington Nationals recalled RHP Aaron Sanchez."),
        Transaction(dt.date(2022, 9, 1), "Washington Nationals released RHP Aaron Sanchez."),
    ]
    check("a player whose latest major-league move is a release is NOT on a roster",
          u._still_on_ml_roster(sanchez) is False)

    # Aaron Sanchez, the REAL sequence: selected to the 40-man, then elected free
    # agency, then only minor-league deals (not roster moves). "elected free
    # agency" is not a global stop keyword, but it DOES mean he left the roster,
    # so the guard must read it as leaving rather than skipping back to the
    # two-year-old selection and reviving him. Without this he accrued phantom
    # service to today (9.109 instead of ~6.1).
    sanchez_real = [
        Transaction(dt.date(2022, 10, 3), "Minnesota Twins selected the contract of RHP Aaron Sanchez from St. Paul Saints."),
        Transaction(dt.date(2022, 11, 6), "RHP Aaron Sanchez elected free agency."),
        Transaction(dt.date(2026, 1, 27), "Kansas City Royals signed free agent RHP Aaron Sanchez to a minor league contract."),
    ]
    check("a player whose latest roster event is a free-agency election is NOT on a roster",
          u._still_on_ml_roster(sanchez_real) is False)

    # But "signed free agent ... to a minor league contract" must not itself be
    # mistaken for the free-agency leaving signal (it is a signing, not a
    # departure); a player recalled after it is still on a roster.
    resigned = [
        Transaction(dt.date(2025, 12, 1), "Club signed free agent RHP Q to a minor league contract."),
        Transaction(dt.date(2026, 4, 1), "Club selected the contract of RHP Q."),
    ]
    check("a signing is not read as a free-agency departure", u._still_on_ml_roster(resigned) is True)

    # A DFA as the last move also leaves him off the roster.
    dfa_last = [
        Transaction(dt.date(2023, 1, 1), "Club selected the contract of RHP W."),
        Transaction(dt.date(2023, 1, 10), "Club designated RHP W for assignment."),
    ]
    check("a player whose latest move is a DFA is NOT on a roster",
          u._still_on_ml_roster(dfa_last) is False)

    # No major-league roster move at all: not on a roster.
    check("a player with no roster move is NOT on a roster", u._still_on_ml_roster([]) is False)


def test_the_ceiling_stops_a_player_who_left_for_the_minors():
    """The interval must not outlive the last major league roster evidence."""
    seasons = [SeasonWindow(y, dt.date(y, 3, 28), dt.date(y, 10, 1)) for y in range(2022, 2027)]
    txns = [Transaction(dt.date(2022, 4, 23), "Toronto Blue Jays recalled RHP X from Buffalo Bisons.")]
    result = compute_service_time(
        txns, seasons,
        horizon_end=dt.date(2026, 8, 22),
        accrual_floor=dt.date(2014, 7, 23),
        accrual_ceiling=dt.date(2022, 10, 1),   # end of his last major league season
        presume_active_from_debut=True,
    )
    check("he is credited in his last major league season", result.by_season[2022]["credited_days"] > 0)
    check(
        "and nothing at all afterwards",
        all(result.by_season[y]["credited_days"] == 0 for y in range(2023, 2027)),
    )


def test_a_rule_5_return_closes_the_interval():
    """
    Finding #14, from Christian Cairo. A Rule 5 selection opens an interval
    with "activated"; being returned to the original club is phrased with a
    verb the parser did not know, so nothing closed it and he was credited a
    full season spent entirely in the minors.
    """
    seasons = [SeasonWindow(y, dt.date(y, 3, 28), dt.date(y, 10, 1)) for y in (2025, 2026)]
    txns = [
        Transaction(dt.date(2024, 12, 11), "Atlanta Braves activated SS X."),
        Transaction(dt.date(2025, 3, 20), "SS X returned to Cleveland Guardians from Atlanta Braves."),
    ]
    result = compute_service_time(
        txns, seasons, horizon_end=dt.date(2026, 8, 23), accrual_floor=dt.date(2024, 12, 1)
    )
    check("a Rule 5 return ends the stint", result.by_season[2025]["credited_days"] == 0)
    check("and nothing accrues afterwards", result.by_season[2026]["credited_days"] == 0)


def test_returned_to_an_affiliate_is_also_a_stop():
    """The commoner shape: an assignment ends and he goes back down."""
    check(
        "returned to a minor league club is a stop",
        is_active_stop("RHP X returned to Durham Bulls from Tampa Bay Rays."),
    )
    check(
        "and it is not read as a start",
        not is_active_start("RHP X returned to Durham Bulls from Tampa Bay Rays."),
    )


def test_returned_to_the_active_roster_is_still_a_start():
    """
    The stop rule and this start are one word apart, so the negative
    lookahead that separates them is worth a test of its own.
    """
    desc = "Boston Red Sox returned to the active roster LHP X."
    check("returning to the active roster is a start", is_active_start(desc))
    check("and must not read as a stop", not is_active_stop(desc))


def test_recalled_from_an_affiliate_is_not_a_return():
    """'from <affiliate>' appears in 5,189 recall rows and must stay a start."""
    desc = "Chicago Cubs recalled C X from Iowa Cubs."
    check("a recall is a start", is_active_start(desc))
    check("and not a stop", not is_active_stop(desc))


def test_an_empty_roster_fetch_is_refused():
    """
    The failure this guard exists for: main() flags everyone not seen in
    today's fetch as off a 40-man, so an empty fetch silently republishes the
    whole database with nobody rostered.
    """
    from update_service_time import check_run_is_sane

    check("an empty fetch is refused", check_run_is_sane(0, 1360, 0))
    check("and so is a badly short one", check_run_is_sane(120, 1360, 0))
    check(
        "a normal day passes",
        not check_run_is_sane(1360, 1359, 3),
    )


def test_a_roster_that_collapses_overnight_is_refused():
    """A real day of transactions moves a handful of players, not a third."""
    from update_service_time import check_run_is_sane

    check("a 60% collapse is refused", check_run_is_sane(950, 1360, 0))
    check(
        "ordinary churn is not",
        not check_run_is_sane(1355, 1360, 0),
    )


def test_widespread_build_failures_are_refused():
    """
    Per-player failures are swallowed so one bad record cannot lose a run --
    which means a systemic outage looks like a normal run with a quieter log.
    """
    from update_service_time import check_run_is_sane

    check("a 30% failure rate is refused", check_run_is_sane(1360, 1360, 400))
    check("a couple of bad records are not", not check_run_is_sane(1360, 1360, 5))


def test_the_guard_is_inert_on_a_first_run():
    """Nothing stored yet means no previous roster to compare against."""
    from update_service_time import check_run_is_sane

    check("a healthy first run passes", not check_run_is_sane(1360, 0, 0))


def test_a_debuted_player_credited_nothing_is_reported():
    """
    A player who appeared in a major league game was on a roster that day, so
    zero credited days means an interval failed to open. Elih Villanueva
    debuted and last played on 2011-06-15 and reads zero.
    """
    from update_service_time import report_debuted_but_empty

    db = {
        "1": {"name": "Debuted But Empty", "mlb_debut": "2011-06-15", "service_days_total": 0},
        "2": {"name": "Normal", "mlb_debut": "2011-06-15", "service_days_total": 40},
        # Correctly zero: his whole career predates the computed window.
        "3": {"name": "Too Early", "mlb_debut": "1998-04-01", "service_days_total": 0},
        # Never debuted, so nothing is owed him.
        "4": {"name": "Never Debuted", "mlb_debut": None, "service_days_total": 0},
    }
    found = {p["name"] for p in report_debuted_but_empty(db)}
    check("the empty debut is reported", "Debuted But Empty" in found)
    check("a normal player is not", "Normal" not in found)
    check("nor is a pre-window career", "Too Early" not in found)
    check("nor is a player who never debuted", "Never Debuted" not in found)


def test_roster_days_before_the_first_game_are_credited():
    """
    Finding #15. Daniel Fields was recalled 2015-06-02 and optioned 06-04;
    his debut is the 4th. Clipping to the debut threw the whole stint away
    and he read 0.000.
    """
    seasons = [SeasonWindow(2015, dt.date(2015, 4, 5), dt.date(2015, 10, 4))]
    txns = [
        Transaction(dt.date(2015, 6, 2), "Detroit Tigers recalled Daniel Fields from Toledo Mud Hens."),
        Transaction(dt.date(2015, 6, 4), "Detroit Tigers optioned CF Daniel Fields to Toledo Mud Hens."),
    ]
    debut = dt.date(2015, 6, 4)
    floor = roster_start_before_debut(txns, debut) or debut
    check("the recall is found", floor == dt.date(2015, 6, 2))
    result = compute_service_time(
        txns, seasons, horizon_end=dt.date(2015, 10, 4),
        accrual_floor=floor, presume_active_from_debut=True,
    )
    check("and those days are credited", result.by_season[2015]["credited_days"] == 2)


def test_a_draft_signing_never_moves_the_floor():
    """
    The trap in finding #15. Andrew Vaughn was signed 2019-06-28 and debuted
    2021-04-02; reaching back to a signing would credit him two phantom years.
    """
    txns = [Transaction(dt.date(2019, 6, 28), "Chicago White Sox signed 1B Andrew Vaughn.")]
    check(
        "a signing is not a roster move",
        roster_start_before_debut(txns, dt.date(2021, 4, 2)) is None,
    )


def test_the_pre_debut_window_is_bounded():
    """A roster move long before the debut is a different phenomenon."""
    far = [Transaction(dt.date(2021, 4, 1), "Oakland Athletics recalled RHP X from Las Vegas Aviators.")]
    check("far outside the window is ignored",
          roster_start_before_debut(far, dt.date(2024, 6, 16)) is None)
    near = [Transaction(dt.date(2024, 6, 1), "Oakland Athletics recalled RHP X from Las Vegas Aviators.")]
    check("just inside it is taken",
          roster_start_before_debut(near, dt.date(2024, 6, 16)) == dt.date(2024, 6, 1))


def test_a_trade_out_of_a_dfa_reopens_the_clock():
    """
    Finding #16, from David Huff: DFA'd by San Francisco 2014-06-06, traded
    to the Yankees 06-11, and MLB's roster shows him active from mid-June.
    """
    txns = [
        Transaction(dt.date(2014, 5, 12), "San Francisco Giants activated LHP David Huff from the 15-day disabled list."),
        Transaction(dt.date(2014, 6, 6), "San Francisco Giants designated LHP David Huff for assignment."),
        Transaction(dt.date(2014, 6, 11), "San Francisco Giants traded LHP David Huff to New York Yankees for cash."),
    ]
    intervals = build_global_active_intervals(txns, dt.date(2014, 10, 1))
    check("the trade reopens the interval", len(intervals) == 2)
    check("starting on the trade date", intervals[1][0] == dt.date(2014, 6, 11))


def test_a_trade_out_of_an_option_does_not():
    """
    The measured counter-case: 60 of the cache's trades follow an option, and
    an optioned player who is traded reports to the new club's affiliate.
    """
    txns = [
        Transaction(dt.date(2014, 5, 12), "San Francisco Giants activated LHP X from the 15-day disabled list."),
        Transaction(dt.date(2014, 6, 6), "San Francisco Giants optioned LHP X to Fresno Grizzlies."),
        Transaction(dt.date(2014, 6, 11), "San Francisco Giants traded LHP X to New York Yankees for cash."),
    ]
    intervals = build_global_active_intervals(txns, dt.date(2014, 10, 1))
    check("an optioned player stays off the roster", len(intervals) == 1)


def test_a_slug_transliterates_an_accent_rather_than_dropping_it():
    """
    The href in app.js and the filename here are built by two different
    functions in two different languages. If they disagree the link 404s and
    nothing in the pipeline notices, so the shared rule is pinned here.

    The first version stripped the accented letter outright -- "Adolis
    García" became "adolis-garc-a" -- which is a bad URL for a quarter of a
    40-man roster.
    """
    from write_player_pages import slug
    check("an acute accent degrades to its base letter", slug("Adolis García") == "adolis-garcia")
    check("so does a tilde", slug("Andrés Muñoz") == "andres-munoz")
    check("and a character NFKD will not split", slug("Jack Sløgren") == "jack-slogren")
    check("punctuation still becomes a separator", slug("A.J. Puk") == "a-j-puk")
    check("a nameless record still gets a slug", slug("") == "player")


def test_a_player_page_is_only_published_for_a_rostered_player():
    """
    A link to a page that does not exist is worse than no link: app.js emits
    an <a> only when on_40_man is true, and this is the other half of that
    agreement.
    """
    from write_player_pages import _should_publish
    check("a rostered player is published", _should_publish({"on_40_man": True}))
    check("a former player is not", not _should_publish({"on_40_man": False}))
    check("a record with no flag at all is not", not _should_publish({}))


def test_the_site_url_follows_docs_cname():
    """
    Moving to a custom domain must not need a source edit. GitHub Pages reads
    docs/CNAME to decide the domain, so the generator reads the same file --
    the two cannot then disagree, and every canonical, og:url, sitemap entry
    and JSON-LD url follows automatically.
    """
    import write_player_pages as w
    check("a custom domain is served from the root", w._base_path("https://example.com") == "/")
    check("a trailing slash does not double", w._base_path("https://example.com/") == "/")
    check(
        "a project page keeps its path prefix",
        w._base_path("https://user.github.io/Repo-Name") == "/Repo-Name/",
    )


def test_a_club_page_path_is_derived_the_same_way_as_a_player_page():
    """
    Club pages are the hubs the player pages link up to, so a crawler sees a
    section rather than 1,358 leaves hanging off the index.
    """
    from write_player_pages import club_path
    check("club paths live under /t/", club_path("Philadelphia Phillies") == "t/philadelphia-phillies.html")
    check("and transliterate like player slugs", club_path("Montréal Expos") == "t/montreal-expos.html")


def test_a_breadcrumb_marks_the_current_page_as_the_last_step():
    from write_player_pages import _crumbs
    out = _crumbs(("Players", "../"), ("Club", "../t/club.html"), ("Player", None))
    check("earlier steps are links", '<a href="../">Players</a>' in out)
    check("the current page is not a link", "<b>Player</b>" in out)
    check("and it is the last one", out.rstrip().endswith("<b>Player</b></nav>"))


def test_no_crumb_still_says_all_players():
    """
    The crumb's first step used to read "All players", which disagreed with
    the "Players" tab the shared header carries (PR #28). Checked on a
    sample of every generated template plus the hand-written pages that
    carry a crumb, rather than trusted from one call site.
    """
    import re

    for label, html_text in _menu_sample_pages().items():
        if "<nav class=\"crumbs\">" not in html_text:
            continue
        check(f"{label}: no crumb says 'All players'", "All players" not in html_text)
        check(f"{label}: the first crumb step says 'Players'",
              '>Players<' in re.search(r'<nav class="crumbs">.*?</nav>', html_text, re.S).group(0))


def test_the_club_directory_is_reachable_in_one_hop():
    """
    The club hubs were only reachable through a player page, so a crawler
    landing on the homepage had to go index -> player -> club to find them --
    backwards for pages meant to BE the section hubs. index.html carries one
    hand-written link to the generated directory at t/, and it is the only
    hand-written link into the generated section: 30 club slugs in a
    hand-maintained file would 404 silently the first time a club renamed.
    """
    docs = pathlib.Path(__file__).resolve().parents[1] / "docs"
    index = (docs / "index.html").read_text()
    check('the homepage links to the club directory', 'href="t/"' in index)
    check("the homepage declares a canonical URL", 'rel="canonical"' in index)
    check("the directory is a real file", (docs / "t" / "index.html").exists())


def _render_explainer_under(overrides: dict) -> str:
    """Render the explainer against a hypothetical successor agreement.

    Writes a throwaway ruleset, points cba.DEFAULT_VERSION at it, reloads the
    page generator so its module-level constants re-read, and renders. The
    module is reloaded again on the way out so no other test sees the change.
    """
    import importlib
    import json as _json

    config_dir = pathlib.Path(__file__).resolve().parents[1] / "config" / "cba"
    base = _json.loads((config_dir / "2022.json").read_text())
    for path, value in overrides.items():
        node = base
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    scratch = config_dir / "9999.json"
    scratch.write_text(_json.dumps(base))

    import write_player_pages

    real_default = cba.DEFAULT_VERSION
    try:
        cba.DEFAULT_VERSION = "9999"
        cba.load.cache_clear()
        importlib.reload(write_player_pages)
        return write_player_pages.render_explainer(
            "2026-01-01T00:00:00+00:00",
            {"cutoff": "2.130", "cutoff_days": 470, "season": 2025},
        )
    finally:
        scratch.unlink(missing_ok=True)
        cba.DEFAULT_VERSION = real_default
        cba.load.cache_clear()
        importlib.reload(write_player_pages)


def test_the_explainer_prose_follows_the_ruleset_not_a_literal():
    """
    The arithmetic was never the problem. The pipeline computed from
    config/cba/ while roughly twenty sentences a READER sees said "172" as a
    literal -- the page footers, the threshold table's day counts (516, 1,032,
    1,376, 1,720), the meta description and the FAQ structured data. The
    project's headline claim is that filling in config/cba/2027.json makes the
    whole site current the same day; that was true of the numbers and false of
    the prose, which is the half anybody actually believes.

    The 2022 agreement expires 2026-12-01, so this is not hypothetical. The
    check is a positive control: a parameter that changes nothing might simply
    not be wired in.
    """
    import write_player_pages as w

    live = w.render_explainer("2026-01-01T00:00:00+00:00", None)
    check(
        "under the agreement in force the explainer still says 172",
        "172 days credit a full year" in live or "<h2>172 days make a year</h2>" in live,
    )

    moved = _render_explainer_under({
        "service_time.days_per_credited_year": 165,
        "service_time.max_days_creditable_per_season": 165,
        "service_time.normal_season_span_days": 180,
        "free_agency.credited_years_required": 5.0,
    })
    check("a changed credited year reaches the heading", "<h2>165 days make a year</h2>" in moved)
    check("...and the season span", "A season runs about 180 days" in moved)
    check("...and the free agency row (5 x 165 = 825)", ">825<" in moved)
    check("...and the arbitration row (3 x 165 = 495)", ">495<" in moved)
    check("...and the gold card row (8 x 165 = 1,320)", ">1,320<" in moved)
    check("...and the service-time-manipulation example", "leaves him at 0.164" in moved)
    check("NO stale 172 survives anywhere in the page", "172" not in moved)


def test_the_structured_data_and_the_visible_prose_agree_on_a_season():
    """
    They did not. The FAQ JSON-LD said a season runs "about 186 days" while the
    paragraph on the same page said 187 -- and a search engine reads both. 186
    is the project's own measured value: it is what reproduces Aaron Judge's
    figure through the 2020 proration, it is what config/cba/2022.json holds,
    and CLAUDE.md records 187 being explicitly rejected when the brief proposed
    it. Both are derived from the ruleset now, so they cannot disagree again.
    """
    import write_player_pages as w

    html = w.render_explainer("2026-01-01T00:00:00+00:00", None)
    span = cba.default().require("service_time.normal_season_span_days")
    prose = f"A season runs about {span} days"
    structured = f"A major league season runs about {span} days"
    check(f"the visible prose says {span}", prose in html)
    check(f"the structured data says {span} too", structured in html)
    check("neither says 187", "187 days" not in html)


def test_a_season_window_is_fetched_once_not_once_per_player():
    """
    The pipeline asks for a season window once per player per season of his
    career, so a daily run over ~1,370 rostered players made 7,179 calls to
    fetch the same 22 distinct answers. That is ~32 minutes of wall clock and
    ~7,000 needless requests a day against a free public endpoint this project
    goes out of its way to be polite to -- and a season's start and end date
    cannot move within a run.
    """
    import fetch_mlb_data as mlb

    calls = []

    def fake_schedule(year):
        # A minimal /schedule payload: two clubs, one R game each on Opening Day.
        return {"dates": [{"date": f"{year}-03-28", "games": [{
            "gameType": "R",
            "status": {"detailedState": "Final"},
            "teams": {"home": {"team": {"id": 147}}, "away": {"team": {"id": 111}}},
        }]}]}

    real_get = mlb._get
    mlb.get_season_window.cache_clear()
    mlb._SEASON_SCHEDULE.clear()
    try:
        mlb._get = lambda path, params=None: (
            calls.append(params.get("season")),
            fake_schedule(params["season"]),
        )[1]
        for _ in range(50):
            for year in (2024, 2025, 2026):
                mlb.get_season_window(year)
    finally:
        mlb._get = real_get
        mlb.get_season_window.cache_clear()
        mlb._SEASON_SCHEDULE.clear()

    check(
        f"150 requests for 3 seasons cost 3 API calls, not 150 (got {len(calls)})",
        len(calls) == 3,
    )


def test_an_estimated_season_window_stops_the_run_from_publishing():
    """
    get_season_window() falls back to a hardcoded Mar 28 - Oct 1 estimate when
    /seasons cannot be reached. That is right for a far-future season nobody
    has scheduled and dangerous for one that has already been played: CLAUDE.md
    records this exact estimate putting Jose Ramirez 8 days out and making him
    look like the one modern outlier worth chasing, when the estimate was the
    error. The daily job only ever asks for seasons that have happened, so a
    fallback there is an outage -- and it used to publish silently.
    """
    import fetch_mlb_data as mlb
    import update_service_time as u

    real_get = mlb._get
    mlb.get_season_window.cache_clear()
    mlb._ESTIMATED_WINDOWS.clear()
    try:
        clean = u.check_run_is_sane(1200, 1200, 0)
        check("a healthy run has nothing to report", clean == [])

        def boom(path, params=None):
            raise RuntimeError("simulated /seasons outage")

        mlb._get = boom
        start, end = mlb.get_season_window(2025)
        check("the fallback still returns a usable window", start.year == 2025)
        check("...and records that it guessed", 2025 in mlb.estimated_season_windows())

        problems = u.check_run_is_sane(1200, 1200, 0)
        check("an estimated window refuses the run", len(problems) == 1)
        check(
            "...and the reason names the season",
            problems and "2025" in problems[0],
        )
    finally:
        mlb._get = real_get
        mlb.get_season_window.cache_clear()
        mlb._ESTIMATED_WINDOWS.clear()


def test_both_published_files_carry_the_cba_rules_block():
    """
    docs/app.js falls back from index.json to service_time.json when a
    deployment has not regenerated the index yet, and that fallback used to
    leave every threshold null: applyRules() ran only on the array-shaped index
    path. The meter then read 100% for every player, formatDays() returned
    "Infinity.NaN", and the profile footer said "null days credit a full year".
    A fallback that renders nonsense is worse than no fallback at all.
    """
    import update_service_time as u

    block = u._rules_block()
    for key in (
        "version", "full_year_days", "free_agency_years", "arbitration_years",
        "super_two_min_years", "super_two_max_years", "super_two_min_days",
    ):
        check(f"the rules block carries {key}", key in block)
    check(
        "full_year_days matches the ruleset",
        block["full_year_days"] == cba.default().require(
            "service_time.days_per_credited_year"
        ),
    )

    docs = pathlib.Path(__file__).resolve().parents[1] / "docs"
    app = (docs / "app.js").read_text()
    check(
        "app.js applies the rules on every load path, not just hydrate()",
        "applyRules(data);" in app,
    )
    check(
        "the embedded sample carries its own thresholds",
        "rules: {" in app,
    )


def test_nothing_published_claims_a_transaction_coverage_cutoff_year():
    """
    The database's disclaimer -- the most prominent sentence the site renders
    -- used to read "Transaction coverage begins in 2009; players who debuted
    earlier are marked as having incomplete history". This project's own probe
    disproved that: scripts/probe_coverage.py returned real 2005-2008 major
    league rows, so coverage THINS going back rather than switching on, and
    _missing_seasons() has measured completeness per player ever since.

    Publishing the old sentence meant the site asserted something the pipeline
    behind it had stopped believing. Both published files now describe the data
    the same way, and neither names a year.
    """
    import update_service_time as u

    src = pathlib.Path(u.__file__).read_text()
    body = src.split("def _write_outputs")[1]
    check(
        "the database no longer publishes a coverage_start_year",
        '"coverage_start_year"' not in body,
    )
    check(
        "...and its disclaimer makes no cutoff claim",
        "coverage begins in" not in body,
    )
    check(
        "the two published files describe the data identically",
        src.count("Service time figures are estimates from public data,") >= 2
        and src.count("not official MLB numbers.") >= 2,
    )
    check(
        "the database disclaimer no longer explains the floor",
        "figure is a floor rather than an estimate" not in body,
    )


def test_a_debuted_player_is_never_described_as_never_having_been_up():
    """
    A player who appears in a major league game is on the active roster that
    day, so a debut date is proof of at least one day he is owed. When the
    figure is nevertheless zero, the page used to say:

        "He is on a 40-man roster but has not been on a major league active
         roster or injured list."

    which is flatly false, and false in the one direction a site publishing an
    estimate cannot afford -- it asserted more than the arithmetic knows.

    Found 2026-09-08 on Seth Lonsway (debut 2026-08-29), whose contract was
    selected and who was optioned back on that same date. Stop-wins (finding
    #10) correctly leaves no interval, and finding #15 cannot reach him
    because the selection is ON the debut rather than before it. The zero is
    defensible; the sentence was not.

    Note this pins the PROSE, not the figure. Whether the engine should floor
    a debuted player at one day is a rules question, measured separately: two
    of 1,380 cached rostered players carry a same-date start-and-stop on their
    debut, worth two days in total.
    """
    import write_player_pages as w

    debuted = {
        "id": 675920,
        "name": "Test Debutant", "mlb_debut": "2026-08-29", "on_40_man": True,
        "service_days_total": 0, "service_time": "0.000", "team": "Some Club",
        "seasons": [], "position": "P",
    }
    never_up = {
        "id": 999001,
        "name": "Test Prospect", "mlb_debut": None, "on_40_man": True,
        "service_days_total": 0, "service_time": "0.000", "team": "Some Club",
        "seasons": [], "position": "P",
    }

    check("a debuted player is recognised as such", w._debuted_but_empty(debuted))
    check("a prospect who never came up is not", not w._debuted_but_empty(never_up))

    page = w.render(debuted, {}, "2026-09-08T00:00:00+00:00")
    check(
        "his page does NOT claim he has never been on an active roster",
        "has not been on a major league active roster" not in page
        and "has not yet been on a major league active roster" not in page,
    )
    check("...it names the debut instead", "made his major league debut on 2026-08-29" in page)
    check("...and calls the figure a floor", "floor" in page)

    prospect_page = w.render(never_up, {}, "2026-09-08T00:00:00+00:00")
    check(
        "a genuine prospect IS still described as not yet up",
        "has not yet been on a major league active roster" in prospect_page,
    )
    check(
        "...and is not described as having debuted",
        "made his major league debut" not in prospect_page,
    )


def test_salary_estimate_shown_for_clearly_pre_arb_with_no_extension():
    """
    A clearly pre-arbitration player (below Super Two, arbitration and
    free-agency eligibility, with at least one credited day) and no
    extension signal from either source gets an "estimate" record, and the
    dollar figure is READ FROM THE RULESET, not a literal in
    salary_estimates.py -- the same discipline every other threshold on this
    site follows (see "Where the service-time math actually lives" in
    CLAUDE.md).
    """
    import cba
    import salary_estimates as se

    player = {
        "id": 900000101, "name": "Test Rookie", "on_40_man": True,
        "service_days_total": 40, "free_agent_eligible": False,
        "arbitration_eligible": False, "super_two_candidate": False,
    }
    ruleset = cba.default()
    record = se.classify(player, {}, ruleset)
    check("a clearly pre-arb player with no extension signal gets a record", record is not None)
    check("...status is 'estimate'", record["status"] == se.STATUS_ESTIMATE)
    check(
        "...the minimum is read from the ruleset, not hardcoded",
        record["minimum"] == ruleset.require("salary.mlb_minimum"),
    )
    check(
        "...the minimum YEAR is also read from the ruleset",
        record["minimum_year"] == ruleset.require("salary.mlb_minimum_year"),
    )

    # Swap in a fake ruleset with a different number -- if the output moves
    # with it, the value really is being read through the ruleset rather
    # than copied from the real one by coincidence.
    class _FakeRuleset:
        def require(self, path):
            return {"salary.mlb_minimum": 555, "salary.mlb_minimum_year": 1999}[path]

    fake_record = se.classify(player, {}, _FakeRuleset())
    check("a different ruleset produces a different minimum", fake_record["minimum"] == 555)
    check("...and a different year", fake_record["minimum_year"] == 1999)


def test_known_extension_suppresses_the_salary_estimate():
    """
    The hard rule this feature exists to satisfy: a player with a contract
    extension on file must never be shown the league minimum. The primary
    channel for that is data/known_extensions.json -- see that file's own
    header for why a hand list, not a scrape, is the right tool here.
    """
    import salary_estimates as se

    player = {
        "id": 900000102, "name": "Test Extended Player", "on_40_man": True,
        "service_days_total": 200, "free_agent_eligible": False,
        "arbitration_eligible": False, "super_two_candidate": False,
    }
    known = {900000102: {"note": "Signed an 8-year extension (test fixture)."}}
    record = se.classify(player, known, cba.default())
    check("a known-extension player gets a record", record is not None)
    check("...status is 'extension'", record["status"] == se.STATUS_EXTENSION)
    check("...no minimum is shown", "minimum" not in record)
    check("...the note is carried through", "8-year extension" in record["note"])

    # The real file this project ships: both already-documented cases
    # (Julio Rodríguez, Michael Harris II) must be present and must suppress
    # the estimate the moment they are (hypothetically) still pre-arb.
    real_known = se.load_known_extensions()
    for real_id in (677594, 671739):
        check(f"id {real_id} is in the shipped known_extensions.json", real_id in real_known)
    pretend_still_pre_arb = {
        "id": 677594, "name": "Julio Rodríguez", "on_40_man": True,
        "service_days_total": 100, "free_agent_eligible": False,
        "arbitration_eligible": False, "super_two_candidate": False,
    }
    real_record = se.classify(pretend_still_pre_arb, real_known, cba.default())
    check("the shipped Julio Rodríguez row suppresses the estimate", real_record["status"] == se.STATUS_EXTENSION)


def test_a_transaction_mentioning_an_extension_suppresses_the_estimate():
    """
    The secondary, defensive signal: if the MLB Stats API transaction feed
    (already cached, no new API call) ever DOES mention an extension or a
    multi-year contract, that alone is enough to suppress the estimate, even
    with no entry in the hand-maintained list. Measured against the real
    cache (2026-10-29): this never fires today, which is exactly why the
    hand list in data/known_extensions.json has to exist too.
    """
    import salary_estimates as se

    fake_id = 900000103
    cache_path = se.CACHE_DIR / f"{fake_id}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    assert not cache_path.exists(), "test fixture id collides with a real cached player"
    try:
        cache_path.write_text(json.dumps([
            {"date": "2025-04-01", "description": "Some Club recalled RHP Test Player from Triple-A.",
             "team": "Some Club", "team_id": 100, "from_team_id": None, "to_team_id": None},
            {"date": "2025-06-01",
             "description": "Some Club signed RHP Test Player to a 6-year contract extension.",
             "team": "Some Club", "team_id": 100, "from_team_id": None, "to_team_id": None},
        ]))
        check(
            "the extension wording is detected from the cached transactions",
            se._transaction_suggests_extension(fake_id),
        )
        player = {
            "id": fake_id, "name": "Test Player", "on_40_man": True,
            "service_days_total": 90, "free_agent_eligible": False,
            "arbitration_eligible": False, "super_two_candidate": False,
        }
        record = se.classify(player, {}, cba.default())
        check("...and the estimate is suppressed because of it", record["status"] == se.STATUS_EXTENSION)
    finally:
        cache_path.unlink(missing_ok=True)

    # An ORDINARY free-agent or minor-league signing must not false-positive
    # just because it uses the word "contract". Pre-arb players essentially
    # never have one of these (free agency implies 6+ years), but the guard
    # exists so a coincidental phrase cannot misfire.
    ordinary_id = 900000104
    ordinary_path = se.CACHE_DIR / f"{ordinary_id}.json"
    assert not ordinary_path.exists(), "test fixture id collides with a real cached player"
    try:
        ordinary_path.write_text(json.dumps([
            {"date": "2025-01-10",
             "description": "Some Club signed free agent RHP Test Player to a minor league contract.",
             "team": "Some Club", "team_id": 100, "from_team_id": None, "to_team_id": None},
        ]))
        check(
            "an ordinary free-agent/minor-league signing is not mistaken for an extension",
            not se._transaction_suggests_extension(ordinary_id),
        )
    finally:
        ordinary_path.unlink(missing_ok=True)


def test_no_salary_section_outside_clearly_pre_arbitration():
    """
    The rule only applies to clearly pre-arbitration players. For anyone
    else -- arbitration eligible, Super Two, free-agency eligible, not
    rostered, or never debuted -- classify() must return None, which
    render() turns into no block at all rather than a block saying
    "unsure". "Unsure" and "does not apply" are different facts and the
    page must not conflate them.
    """
    import salary_estimates as se
    import write_player_pages as w

    base = {
        "id": 900000105, "name": "Test Veteran", "on_40_man": True,
        "service_days_total": 900, "team": "Some Club", "position": "RHP",
        "mlb_debut": "2020-04-01", "service_time": "5.108", "seasons": [],
    }
    for flag in ("free_agent_eligible", "arbitration_eligible", "super_two_candidate"):
        player = dict(base, **{flag: True})
        check(f"no salary record when {flag} is true", se.classify(player, {}, cba.default()) is None)
        page = w.render(player, {}, "2026-10-29T00:00:00+00:00")
        check(f"...and no salary block on the page when {flag} is true", "salary-note" not in page)

    not_rostered = dict(base, on_40_man=False)
    check("no salary record for a player off the 40-man", se.classify(not_rostered, {}, cba.default()) is None)

    never_debuted = dict(base, service_days_total=0, free_agent_eligible=False,
                          arbitration_eligible=False, super_two_candidate=False)
    check("no salary record for a player who has never debuted", se.classify(never_debuted, {}, cba.default()) is None)


def test_salary_block_wording_matches_estimate_vs_extension():
    """
    End-to-end through write_player_pages.render(): the estimate case names
    a dollar figure and calls it an estimate; the extension case names no
    figure at all. Mixing those up is exactly the failure this feature
    exists to prevent.
    """
    import write_player_pages as w

    pre_arb = {
        "id": 900000106, "name": "Test Rookie Two", "on_40_man": True,
        "service_days_total": 50, "free_agent_eligible": False,
        "arbitration_eligible": False, "super_two_candidate": False,
        "team": "Some Club", "position": "2B", "mlb_debut": "2026-05-01",
        "service_time": "0.050", "seasons": [],
    }
    page = w.render(pre_arb, {}, "2026-10-29T00:00:00+00:00")
    check("the estimate block appears", "salary-note-estimate" in page)
    check("...names the league minimum in dollars", "$780,000" in page or "$" in page)
    check("...calls it an estimate, not a confirmed figure", "estimate" in page.lower())
    check("...and does not discuss a permitted source", "permitted source" not in page.lower())
    check("...the extension block does not also appear", "salary-note-extension" not in page)

    extended = dict(pre_arb, id=671739, name="Michael Harris II")
    ext_page = w.render(extended, {}, "2026-10-29T00:00:00+00:00")
    check("the extension block appears for a known-extension id", "salary-note-extension" in ext_page)
    check("...and the estimate block does not", "salary-note-estimate" not in ext_page)
    check("...no dollar figure is shown for him", "$780,000" not in ext_page)

    sears = dict(pre_arb, id=676664, name="JP Sears")
    sears_page = w.render(sears, {}, "2026-10-29T00:00:00+00:00")
    check("an extension note is shown for JP Sears", "salary-note-extension" in sears_page)
    check("...without naming an outside stat site", "FanGraphs" not in sears_page and "Baseball-Reference" not in sears_page)


def test_the_profile_overlay_fetches_and_renders_the_same_salary_data():
    """
    The static page (render(), tested above) and the SPA profile overlay
    (app.js) must show the same thing for the same player -- they read the
    SAME published docs/data/salary_estimates.json rather than each
    re-deriving "clearly pre-arbitration" in a second language. This pins
    the wiring on the JS side the way test_the_stats_tab_states_that_war_and_
    salary_are_absent() pins the stats-tab note.
    """
    ROOT = pathlib.Path(__file__).resolve().parents[1]
    app = (ROOT / "docs" / "app.js").read_text()
    for marker in [
        "data/salary_estimates.json",
        "function salaryBlock",
        "profile-salary-estimate",
        "profile-salary-extension",
        "salary.extensions",
        "salary.estimate_ids",
    ]:
        check(f"app.js wires in {marker!r}", marker in app)
    check(
        "renderProfile() is passed the fetched salary data",
        "renderProfile(profile, (shard && shard.teams) || {}, salary)" in app,
    )


def test_statlines_are_read_from_the_bio_the_pipeline_already_fetches():
    """
    BIO_SEASON_TEAMS_HYDRATE has always asked for yearByYear hitting and
    pitching, and season_teams_from_bio() read exactly one field out of each
    split -- the team id -- discarding the statline unread. Third time this
    project has found data it was already paying for, after by_season and
    birthDate.
    """
    import fetch_mlb_data as mlb

    bio = {
        "stats": [
            {
                "group": {"displayName": "hitting"},
                "splits": [
                    {"season": "2024", "team": {"id": 147},
                     "stat": {"gamesPlayed": 158, "homeRuns": 58, "ops": "1.159",
                              "someFieldWeDidNotAskFor": 9}},
                    {"season": "2025", "team": {"id": 147},
                     "stat": {"gamesPlayed": 152, "avg": ".331"}},
                ],
            },
            {
                "group": {"displayName": "pitching"},
                "splits": [
                    {"season": "2024", "team": {"id": 147},
                     "stat": {"era": "2.85", "wins": 14}},
                ],
            },
            {"group": {"displayName": "fielding"},
             "splits": [{"season": "2024", "team": {"id": 147}, "stat": {"assists": 5}}]},
        ]
    }
    out = mlb.season_stats_from_bio(bio)

    check("the statline is read at all", out[2024]["hitting"]["homeRuns"] == 58)
    check("both groups are read", out[2024]["pitching"]["era"] == "2.85")
    check("a group we did not ask for is ignored",
          all("fielding" not in groups for groups in out.values()))
    check("a key outside the whitelist is dropped",
          "someFieldWeDidNotAskFor" not in out[2024]["hitting"])
    # The whitelist is a guess: this sandbox cannot reach statsapi.mlb.com, so
    # the key names were read off documentation rather than a live payload. A
    # missing key must therefore thin the row, never raise.
    check("a missing key thins the row rather than raising",
          "ops" not in out[2025]["hitting"] and out[2025]["hitting"]["avg"] == ".331")
    check("the team id rides along so the caller can filter it",
          out[2024]["hitting"]["team_id"] == 147)


def test_a_malformed_or_empty_bio_yields_no_statline_rather_than_an_error():
    """
    The hydrate is best-effort -- get_player_bio() already falls back to the
    plain call when it fails. A bio without stats, or with nulls where objects
    are expected, must produce an empty result and let the daily job continue.
    """
    import fetch_mlb_data as mlb

    for bad in [
        {},
        {"stats": None},
        {"stats": [{"group": None, "splits": None}]},
        {"stats": [{"group": {"displayName": "hitting"}, "splits": [{"season": None, "stat": {}}]}]},
        {"stats": [{"group": {"displayName": "hitting"}, "splits": [{"season": "abc", "stat": {"hits": 1}}]}]},
    ]:
        try:
            check("malformed bio yields no statline", mlb.season_stats_from_bio(bad) == {})
        except Exception as exc:  # noqa: BLE001
            check(f"malformed bio raised {type(exc).__name__}", False)


def _stat_split(season, stat, team_id=None, num_teams=None):
    """A yearByYear split in the shape the people hydrate returns."""
    row = {"season": str(season), "stat": stat}
    if team_id is not None:
        row["team"] = {"id": team_id}
    if num_teams is not None:
        row["numTeams"] = num_teams
    return row


def test_a_mid_season_trade_keeps_the_combined_total_and_each_club():
    """
    A traded season arrives as one split per club and then a combined row
    with no team and numTeams >= 2. Keeping "the later" split made that
    combined row the whole season, and the major-league filter dropped it
    for having no team id, so the year disappeared.

    The lines below are the feed's own shape for the three seasons the
    research checked: Arrighetti 2026 (HOU + TOR), Baker 2025 (BAL + TB;
    his hitting line is one club and must stay one club), Adames 2021
    (TB + MIL). Rate stats are copied from the combined row, not averaged.
    """
    import fetch_mlb_data as mlb

    # Club ids as the feed reports them.
    HOU, TOR, BAL, TB, MIL, MINORS = 117, 141, 110, 139, 158, 999
    mlb_ids = {HOU, TOR, BAL, TB, MIL}

    bio = {
        "stats": [
            {
                "group": {"displayName": "pitching"},
                "splits": [
                    # Spencer Arrighetti 2026.
                    _stat_split("2026", {"gamesPlayed": 17, "inningsPitched": "88.0", "era": "9.99"}, HOU),
                    _stat_split("2026", {"gamesPlayed": 7, "inningsPitched": "22.2", "era": "1.00"}, TOR),
                    _stat_split(
                        "2026",
                        {"gamesPlayed": 24, "inningsPitched": "110.2", "era": "3.50"},
                        num_teams=2,
                    ),
                    # Bryan Baker 2025. The combined row is last, which is
                    # what used to overwrite both clubs and then get dropped.
                    _stat_split("2025", {"gamesPlayed": 42, "inningsPitched": "38.1"}, BAL),
                    _stat_split("2025", {"gamesPlayed": 31, "inningsPitched": "30.1"}, TB),
                    _stat_split(
                        "2025",
                        {"gamesPlayed": 73, "inningsPitched": "68.2", "era": "2.50"},
                        num_teams=2,
                    ),
                    # Combined row FIRST, then the clubs. The old code kept
                    # whichever split arrived last, so this order has to work too.
                    _stat_split("2020", {"gamesPlayed": 50, "era": "4.00"}, num_teams=2),
                    _stat_split("2020", {"gamesPlayed": 20, "era": "5.00"}, HOU),
                    _stat_split("2020", {"gamesPlayed": 30, "era": "3.00"}, TOR),
                ],
            },
            {
                "group": {"displayName": "hitting"},
                "splits": [
                    # Baker 2025 batting is one club (0 AB). It must not be
                    # swallowed by the pitching trade, and the 0 must survive.
                    _stat_split("2025", {"gamesPlayed": 42, "atBats": 0}, BAL),
                    # Willy Adames 2021.
                    _stat_split("2021", {"gamesPlayed": 41, "atBats": 132, "avg": ".250"}, TB),
                    _stat_split("2021", {"gamesPlayed": 99, "atBats": 365, "avg": ".280"}, MIL),
                    _stat_split(
                        "2021",
                        {"gamesPlayed": 140, "atBats": 497, "avg": ".272"},
                        num_teams=2,
                    ),
                    # A minor-league line beside a major-league one. The
                    # combined row mixes the two levels, so it is not the
                    # major-league total; the major-league club line is.
                    _stat_split("2019", {"gamesPlayed": 10, "atBats": 20}, TB),
                    _stat_split("2019", {"gamesPlayed": 80, "atBats": 300}, MINORS),
                    _stat_split("2019", {"gamesPlayed": 90, "atBats": 320}, num_teams=2),
                    # Minor league only: nothing to publish.
                    _stat_split("2018", {"gamesPlayed": 40, "atBats": 100}, MINORS),
                ],
            },
        ]
    }

    parsed = mlb.season_stats_from_bio(bio)
    # The parser itself does not know which ids are major league, so the
    # minor-league split is still here. The selector is what drops it.
    check(
        "the parser keeps a minor-league split for the caller to filter",
        any(s.get("team_id") == MINORS for s in parsed[2019]["hitting"]["splits"]),
    )

    stored = mlb.select_major_league_season_stats(parsed, mlb_ids)

    arr = stored[2026]["pitching"]
    check("Arrighetti 2026 total is the combined 24 games", arr["gamesPlayed"] == 24)
    check("...and the combined 110.2 innings", arr["inningsPitched"] == "110.2")
    check("...and the combined era, not either club's", arr["era"] == "3.50")
    check("...numTeams is kept on the total", arr["numTeams"] == 2)
    check("...the total has no club", "team_id" not in arr)
    check(
        "Arrighetti's two clubs are kept in feed order",
        [s["team_id"] for s in arr["splits"]] == [HOU, TOR],
    )
    check("HOU's own line is 17 games / 88.0", arr["splits"][0]["gamesPlayed"] == 17
          and arr["splits"][0]["inningsPitched"] == "88.0")
    check("TOR's own line is 7 games / 22.2", arr["splits"][1]["gamesPlayed"] == 7
          and arr["splits"][1]["inningsPitched"] == "22.2")

    baker = stored[2025]["pitching"]
    check("Baker 2025 pitching total is 73 games", baker["gamesPlayed"] == 73)
    check("...and 68.2 innings", baker["inningsPitched"] == "68.2")
    check(
        "Baker's clubs are Baltimore then Tampa Bay",
        [(s["team_id"], s["gamesPlayed"]) for s in baker["splits"]] == [(BAL, 42), (TB, 31)],
    )
    first = stored[2020]["pitching"]
    check("a combined row listed before the clubs is still the total",
          first["gamesPlayed"] == 50 and first["era"] == "4.00")
    check("...and the clubs stay in feed order after it",
          [s["team_id"] for s in first["splits"]] == [HOU, TOR])
    baker_hit = stored[2025]["hitting"]
    check("Baker's 2025 hitting line stays the one club that batted",
          baker_hit["team_id"] == BAL and baker_hit["gamesPlayed"] == 42)
    check("a 0 at-bat line is kept", baker_hit["atBats"] == 0)
    check("the one-club hitting line is not given a phantom split list", "splits" not in baker_hit)

    adames = stored[2021]["hitting"]
    check("Adames 2021 total is 140 games", adames["gamesPlayed"] == 140)
    check("...and the combined 497 at-bats", adames["atBats"] == 497)
    check("...and the combined average", adames["avg"] == ".272")
    check(
        "Adames's clubs are Tampa Bay then Milwaukee",
        [(s["team_id"], s["atBats"]) for s in adames["splits"]] == [(TB, 132), (MIL, 365)],
    )

    check("a mixed major/minor total is not published",
          stored[2019]["hitting"]["gamesPlayed"] == 10
          and stored[2019]["hitting"]["team_id"] == TB
          and "splits" not in stored[2019]["hitting"])
    check("a minor-league-only season is absent", 2018 not in stored)

    # The bug itself: a filter that requires a major-league team id drops
    # the combined row. This selector must not.
    only_combined = {
        2026: {
            "pitching": {
                "team_id": None,
                "numTeams": 2,
                "gamesPlayed": 24,
                "inningsPitched": "110.2",
            }
        }
    }
    kept = mlb.select_major_league_season_stats(only_combined, mlb_ids)
    check(
        "a no-team combined row is kept when numTeams >= 2",
        kept[2026]["pitching"]["gamesPlayed"] == 24 and kept[2026]["pitching"]["numTeams"] == 2,
    )
    not_a_total = {2024: {"hitting": {"team_id": None, "numTeams": 1, "gamesPlayed": 5}}}
    check(
        "a no-team row with numTeams below 2 is not a season",
        mlb.select_major_league_season_stats(not_a_total, mlb_ids) == {},
    )

    app = (pathlib.Path(__file__).resolve().parents[1] / "docs" / "app.js").read_text()
    check(
        "the stats tab no longer says a trade shows only the club he finished with",
        "finished the year with" not in app,
    )
    check("the stats tab attributes the line to public data", "from public data" in app)


def test_the_stats_tab_states_that_war_and_salary_are_absent():
    """
    WAR is not a field MLB's API publishes -- bWAR and fWAR are
    Baseball-Reference's and FanGraphs' own calculations, and neither site
    permits automated extraction, which is the same line this project already
    drew over B-R's service-time figures. Salary has no permitted source
    either. That explanation stays in the comment above the stats tab.

    The note a reader sees is one line. The page does not name those sites,
    and it does not spell out sourcing or permission. A WAR column must not
    quietly appear either.
    """
    import re

    ROOT = pathlib.Path(__file__).resolve().parents[1]
    app = (ROOT / "docs" / "app.js").read_text()
    panel = app.split("function renderStatsPanel", 1)[1].split("function noteForPage", 1)[0]
    visible = re.sub(r"//.*?$", "", panel, flags=re.M)
    check(
        "the stats tab says only the public-data line",
        "Major league seasons only, from public data." in visible,
    )
    for phrase in [
        "No WAR and no salary",
        "One line per season",
        "Baseball-Reference",
        "FanGraphs",
        "automated extraction",
        "permitted source",
    ]:
        check(f"the stats tab does not say {phrase!r}", phrase not in visible)

    # And it must not quietly grow a WAR column.
    check("no WAR column is defined",
          not re.search(r'\["war"|"WAR"\]', app, re.I))


def test_the_super_two_prose_quotes_published_figures_not_our_own():
    """
    The tile's tooltip told the reader the cutoff "has moved between 2.126 and
    2.136 over the last four years". Both literals were THIS PROJECT'S OWN
    computed cutoffs, quoted back as though they described the published
    figure -- and the published range over 2021-2025 is 2.116 to 2.140, wider
    at both ends. A site that measures its own error must not understate the
    thing it is estimating.

    Same failure as the CBA prose: the arithmetic came from data and the
    sentence did not.
    """
    import re
    import update_service_time as u

    ROOT = pathlib.Path(__file__).resolve().parents[1]
    app = (ROOT / "docs" / "app.js").read_text()

    # No hardcoded cutoff literal in anything that RENDERS. Block comments are
    # stripped first: the only remaining mentions sit in the comment recording
    # why those two numbers were wrong, and a number in a comment cannot reach
    # a reader. (The advice lint in the contract tests greps comments too, but
    # that guards a PHRASE someone might paste into copy; this guards a value
    # being interpolated, which a comment cannot do.)
    code = re.sub(r'/\*.*?\*/', '', app, flags=re.S)
    stray = re.findall(r'\b2\.1\d\d\b', code)
    check("no Super Two cutoff literal survives in app.js", stray == [])

    hist = u._published_super_two_history()
    check("published history reaches the payload", bool(hist))
    check("it spans more than one season", hist and hist["count"] >= 2)
    # 2009-2025 published figures. 2019 is the low end (2.115) and 2011 the
    # high end (2.146). The old 2021-2025 window was 2.116 to 2.140.
    check("the range comes from published figures",
          hist and hist["low"] == "2.115" and hist["high"] == "2.146")

    ix = json.loads((ROOT / "docs" / "data" / "index.json").read_text())
    check("index.json carries super_two_published", bool(ix.get("super_two_published")))


def test_an_unpublished_cutoff_is_called_a_prediction_not_a_fact():
    """
    The season super_two.py computes is the latest COMPLETE one, so between
    the end of a season and MLB's November announcement our cutoff is a
    prediction of a figure that does not exist yet. The tooltip said

        "The Super Two cutoff after the 2026 season WAS 2.127,
         measured from this project's own data"

    through the whole of that window -- MLB's number, past tense, asserted as
    settled, in the same breath as admitting it came from our own data. And it
    then closed with "Next offseason's cutoff will differ", which frames the
    live prediction as a stale proxy for itself. Both halves were backwards.

    Which claim is true is asked of the published history rather than inferred
    from the calendar, the same discipline as latest_complete_season().
    """
    import update_service_time as u

    ROOT = pathlib.Path(__file__).resolve().parents[1]
    app = (ROOT / "docs" / "app.js").read_text()

    check("no sentence asserts MLB's cutoff in the past tense",
          "season was `" not in app and "Super Two cutoff after the" not in app)
    check("the branch is keyed on the published history",
          "pub.by_season || {})[String(cutoff" in app)
    check("the unpublished branch says prediction",
          "a prediction, not a published number" in app)
    check("the published branch says the number is already known",
          "a number that is already known" in app)
    check("the rule is stated without tensing it to an event",
          "reach arbitration a year early" in app)

    # And the live payload must actually be in the prediction case: a cutoff
    # computed for a season MLB has already published would mean super_two.py
    # is a season behind, which is its own bug.
    ix = json.loads((ROOT / "docs" / "data" / "index.json").read_text())
    cut = ix.get("super_two_cutoff") or {}
    hist = (ix.get("super_two_published") or {}).get("by_season") or {}
    check("a cutoff is published for the table", bool(cut.get("cutoff")))
    check("the computed season is the one not yet announced",
          str(cut.get("season")) not in hist)
    check("every published season precedes it",
          all(int(y) < int(cut["season"]) for y in hist))


def test_the_page_does_not_claim_a_finished_season_is_still_being_played():
    """
    "While that season is still being played a player can cross 86 days and
    join this list" was unconditional. True in July; false from the end of the
    season until the following spring, which is most of the year -- so in late
    October it asserted an in-progress season that had been over for weeks.

    Whether the season is finished is MEASURED, the same way
    latest_complete_season() measures it, rather than inferred from the
    calendar.
    """
    import sys as _sys
    _sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    import super_two

    ROOT = pathlib.Path(__file__).resolve().parents[1]
    app = (ROOT / "docs" / "app.js").read_text()
    check("the in-progress sentence is conditional",
          "qualifying_season_complete === false" in app)
    check("and a finished season gets its own wording",
          "no longer changes on it" in app)

    # A season somebody completed is complete; one nobody has is not.
    db = {
        "1": {"id": 1, "seasons": [{"y": 2030, "d": super_two.FULL_YEAR_DAYS}]},
        "2": {"id": 2, "seasons": [{"y": 2031, "d": 40}]},
    }
    check("a capped season reads complete",
          super_two._max_days_in(db, 2030) >= super_two.FULL_YEAR_DAYS)
    check("a part-played season does not",
          super_two._max_days_in(db, 2031) < super_two.FULL_YEAR_DAYS)


def test_arbitration_eligible_selection_boundaries():
    """
    select_arbitration_eligible() is the one place "arbitration-eligible
    this winter" is decided for the new page -- 3+ but under 6 years, or
    Super Two, rostered only. Pinning the exact boundaries (3.000 included,
    6.000 excluded) and the non-rostered exclusion so a departed player's
    stale flag can never resurrect him onto the page.
    """
    import write_player_pages as wpp

    arb_days = wpp._days_for(wpp.ARBITRATION_YEARS)
    fa_days = wpp._days_for(wpp.FREE_AGENCY_YEARS)

    players = [
        # Exactly 3.000: the standard threshold is inclusive.
        {"id": 1, "name": "Right At Arb", "team": "A", "on_40_man": True,
         "service_days_total": arb_days, "service_time": "3.000",
         "super_two_candidate": False},
        # One day short of arbitration, not Super Two: excluded.
        {"id": 2, "name": "Just Short", "team": "A", "on_40_man": True,
         "service_days_total": arb_days - 1, "service_time": "2.171",
         "super_two_candidate": False},
        # Exactly 6.000: free agency eligible, excluded even though
        # service_time.py would still set arbitration_eligible for him.
        {"id": 3, "name": "Right At FA", "team": "A", "on_40_man": True,
         "service_days_total": fa_days, "service_time": "6.000",
         "super_two_candidate": False},
        # Super Two, under the standard threshold: included on that basis
        # alone.
        {"id": 4, "name": "Super Twoer", "team": "A", "on_40_man": True,
         "service_days_total": 2 * wpp.FULL_YEAR_DAYS + 100, "service_time": "2.100",
         "super_two_candidate": True},
        # Would otherwise qualify, but has left the 40-man: his flag is
        # stale (apply_super_two recomputes it against a frozen total) and
        # must not resurrect him onto a page about this winter.
        {"id": 5, "name": "Departed", "team": "A", "on_40_man": False,
         "service_days_total": arb_days + 50, "service_time": "3.050",
         "super_two_candidate": False},
    ]
    got = {p["id"] for p in wpp.select_arbitration_eligible(players)}
    check("exactly 3.000 years is included", 1 in got)
    check("one day short of 3.000 is excluded", 2 not in got)
    check("exactly 6.000 years (free agency) is excluded", 3 not in got)
    check("a Super Two candidate under 3.000 is included", 4 in got)
    check("a departed player is excluded regardless of his stored flags", 5 not in got)
    check("nothing else sneaks in", got == {1, 4})


def test_super_two_class_selection_boundaries():
    """
    select_super_two_class() feeds the "who is in the class right now"
    table on the Super Two page. Same half-open band [2.000, 3.000) that
    super_two.build_class() ranks -- a player at exactly 3.000 is excluded
    here for the same reason he is excluded from the class the cutoff
    itself is measured through.
    """
    import write_player_pages as wpp

    low = 2 * wpp.FULL_YEAR_DAYS
    high = 3 * wpp.FULL_YEAR_DAYS
    players = [
        {"id": 1, "name": "Right At Two", "team": "A", "on_40_man": True,
         "service_days_total": low, "service_time": "2.000"},
        {"id": 2, "name": "Just Under Three", "team": "A", "on_40_man": True,
         "service_days_total": high - 1, "service_time": "2.171"},
        {"id": 3, "name": "Right At Three", "team": "A", "on_40_man": True,
         "service_days_total": high, "service_time": "3.000"},
        {"id": 4, "name": "Just Under Two", "team": "A", "on_40_man": True,
         "service_days_total": low - 1, "service_time": "1.171"},
        {"id": 5, "name": "Departed In Band", "team": "A", "on_40_man": False,
         "service_days_total": low + 10, "service_time": "2.010"},
    ]
    got = {p["id"] for p in wpp.select_super_two_class(players)}
    check("exactly 2.000 is included (lower bound inclusive)", 1 in got)
    check("just under 3.000 is included", 2 in got)
    check("exactly 3.000 is excluded (upper bound exclusive)", 3 not in got)
    check("just under 2.000 is excluded", 4 not in got)
    check("a departed player in the band is excluded", 5 not in got)
    check("nothing else sneaks in", got == {1, 2})


def test_super_two_status_labels_ties_and_boundaries():
    """
    _s2_status() is the per-player label on the Super Two page. It must
    not reimplement the Super Two test -- it only compares an
    already-computed day count to an already-computed low/high range, and
    defers to the engine's own super_two_candidate flag whenever that is
    set. See test_super_two_status_labels_with_a_real_range() for the
    low/high boundaries this adds on top of the single-value behaviour
    pinned here.
    """
    import write_player_pages as wpp

    cutoff_days = 2 * wpp.FULL_YEAR_DAYS + 50

    # No reported figure: high_days omitted, collapses to the single-value
    # behaviour this function had before the range existed.
    flagged = {"service_days_total": cutoff_days, "super_two_candidate": True}
    check("the engine's own flag wins: 'Projected Super Two'",
          wpp._s2_status(flagged, cutoff_days) == "Projected Super Two")

    # A tie at the low end, not flagged (e.g. he hasn't met this season's day
    # floor yet) -- described as sitting right on the line, not above or
    # below it.
    tied = {"service_days_total": cutoff_days, "super_two_candidate": False}
    check("a tie at the low end reads as 'right at' it, short of the floor",
          wpp._s2_status(tied, cutoff_days)
          == "Right at the projected low end, short of the season's day floor")

    above_unflagged = {"service_days_total": cutoff_days + 5, "super_two_candidate": False}
    check("above the low end but unflagged names the day-floor gap",
          wpp._s2_status(above_unflagged, cutoff_days)
          == "Above the projected range, short of the season's day floor")

    below = {"service_days_total": cutoff_days - 5, "super_two_candidate": False}
    check("below the low end reads as below the range",
          wpp._s2_status(below, cutoff_days) == "Below the projected range")

    check("no cutoff at all is called out rather than guessed",
          wpp._s2_status(below, None) == "Cutoff not yet measurable")


def test_super_two_status_labels_with_a_real_range():
    """
    With a reported high end distinct from the computed low end, a
    candidate (service >= low, day floor met) splits into "Projected Super
    Two" (>= high) and "On the bubble" (< high) -- the classification Job 8
    asked for: at/above high = Projected Super Two, [low, high) = bubble,
    below low = below the range. Equal ends (no usable reported figure, or
    one identical to the computed cutoff) must keep behaving as a single
    line, which the other test above already pins.
    """
    import write_player_pages as wpp

    low = 2 * wpp.FULL_YEAR_DAYS + 50
    high = 2 * wpp.FULL_YEAR_DAYS + 57

    at_high = {"service_days_total": high, "super_two_candidate": True}
    check("exactly at the high end is 'Projected Super Two' (inclusive)",
          wpp._s2_status(at_high, low, high) == "Projected Super Two")

    above_high = {"service_days_total": high + 3, "super_two_candidate": True}
    check("above the high end is still 'Projected Super Two'",
          wpp._s2_status(above_high, low, high) == "Projected Super Two")

    at_low = {"service_days_total": low, "super_two_candidate": True}
    check("exactly at the low end is 'On the bubble' (inclusive low, exclusive high)",
          wpp._s2_status(at_low, low, high) == "On the bubble")

    mid_bubble = {"service_days_total": low + 3, "super_two_candidate": True}
    check("strictly between low and high is 'On the bubble'",
          wpp._s2_status(mid_bubble, low, high) == "On the bubble")

    one_below_high = {"service_days_total": high - 1, "super_two_candidate": True}
    check("one day short of the high end is still 'On the bubble'",
          wpp._s2_status(one_below_high, low, high) == "On the bubble")

    below_low = {"service_days_total": low - 1, "super_two_candidate": False}
    check("below the low end is 'Below the projected range'",
          wpp._s2_status(below_low, low, high) == "Below the projected range")

    # Equal ends (e.g. a reported figure identical to the computed cutoff):
    # a candidate at the shared value reads as a plain "Projected Super
    # Two", never "On the bubble" -- there is no band to be inside.
    equal_candidate = {"service_days_total": low, "super_two_candidate": True}
    check("equal low and high collapses to a single line, not a bubble",
          wpp._s2_status(equal_candidate, low, low) == "Projected Super Two")


def test_an_extended_player_leaves_the_arbitration_and_non_tender_lists():
    """
    A reported extension buys out the hearing. Service time can still say
    he has crossed the arbitration threshold; the winter lists leave him
    off, and the non-tender watch is built from that same list.
    """
    import write_player_pages as wpp

    players = [
        {"id": 1, "name": "Has An Extension", "team": "A", "on_40_man": True,
         "service_days_total": 4 * wpp.FULL_YEAR_DAYS, "service_time": "4.000",
         "super_two_candidate": False, "arbitration_eligible": True,
         "contract_extension": True},
        {"id": 2, "name": "No Extension", "team": "A", "on_40_man": True,
         "service_days_total": 4 * wpp.FULL_YEAR_DAYS, "service_time": "4.000",
         "super_two_candidate": False, "arbitration_eligible": True},
    ]
    selected = wpp.select_arbitration_eligible(players)
    check("the extended player is not selected", {p["id"] for p in selected} == {2})
    html = wpp.render_arbitration_eligible(players, "2026-10-30T00:00:00+00:00", None, {})
    check("the extended player is absent from the page", "Has An Extension" not in html)
    check("the player without an extension stays on the page", "No Extension" in html)
    watch = wpp.select_non_tender_watch(players)
    check("the extended player is not on non-tender watch",
          all(p["id"] != 1 for p, _reasons in watch))
    sentence = wpp._milestone_sentence(players[0])
    check("his page says the arbitration list leaves him off",
          "arbitration list leaves him off" in sentence)
    check("his page does not call him arbitration-eligible",
          "arbitration-eligible" not in sentence)


def test_the_super_two_page_shows_the_range_and_three_groups():
    """
    Job 8: the Super Two page must show "Projected cutoff range: LOW to
    HIGH" (not a single number) when a reported figure gives it one, and
    classify the live class into three groups -- at/above high: Projected
    Super Two; [low, high): on the bubble; below low: below the range.
    """
    import re
    import super_two as S  # noqa: PLC0415
    import write_player_pages as wpp

    low_days = 2 * wpp.FULL_YEAR_DAYS + 50
    high_days = 2 * wpp.FULL_YEAR_DAYS + 57
    cutoff = {
        "season": 2026, "cutoff_days": low_days, "cutoff": S.format_service(low_days),
        "class_size": 136, "qualifying_count": 30,
        "range": {
            "season": 2026, "low_days": low_days, "high_days": high_days,
            "low": S.format_service(low_days), "high": S.format_service(high_days),
            "is_range": True,
            "reported": {"season": 2026, "cutoff_days": high_days,
                         "source": "reported, unofficial: one day above Jordan Beck's service time",
                         "entered": "2026-10-30"},
        },
    }
    players = [
        {"id": 1, "name": "Above High <script>", "team": "A", "on_40_man": True,
         "service_days_total": high_days, "service_time": "2.057", "super_two_candidate": True},
        {"id": 2, "name": "On The Bubble", "team": "A", "on_40_man": True,
         "service_days_total": low_days, "service_time": "2.050", "super_two_candidate": True},
        {"id": 3, "name": "Below Range", "team": "A", "on_40_man": True,
         "service_days_total": low_days - 1, "service_time": "2.049", "super_two_candidate": False},
    ]
    html_out = wpp.render_super_two(players, {}, "2026-10-30T00:00:00+00:00", cutoff)

    check("the page states the range, not a single number",
          f"Projected cutoff range: {cutoff['range']['low']} to {cutoff['range']['high']}" in html_out)
    check("the reported source note reaches the page",
          "one day above Jordan Beck" in html_out)
    check("a player at/above the high end reads 'Projected Super Two'",
          "Projected Super Two</td>" in html_out)
    check("a player on the bubble reads 'On the bubble'",
          "On the bubble</td>" in html_out)
    check("a player below the low end reads 'Below the projected range'",
          "Below the projected range</td>" in html_out)
    check("a name with HTML-significant characters is escaped",
          "Above High <script>" not in html_out and "Above High &lt;script&gt;" in html_out)
    subtitle = re.search(r'<p class="subtitle">(.*?)</p>', html_out, re.S).group(1)
    subtitle = " ".join(subtitle.split())
    check("the subtitle reports all three group counts",
          "1 project as Super Two" in subtitle
          and "1 is on the bubble" in subtitle
          and "1 fall below the projected range" in subtitle)


def test_arbitration_eligible_badge_distinguishes_the_bubble():
    """
    Job 8: a Super Two candidate who is only "on the bubble" (per
    super_two.apply_super_two's super_two_bubble flag) must read as such on
    the arbitration-eligible page too, not as a flatly-asserted "Super Two".
    """
    import write_player_pages as wpp

    players = [
        {"id": 1, "name": "Bubble Player", "team": "A", "on_40_man": True,
         "service_days_total": 2 * wpp.FULL_YEAR_DAYS + 50, "service_time": "2.050",
         "super_two_candidate": True, "super_two_bubble": True},
        {"id": 2, "name": "Solid Super Two", "team": "A", "on_40_man": True,
         "service_days_total": 2 * wpp.FULL_YEAR_DAYS + 60, "service_time": "2.060",
         "super_two_candidate": True, "super_two_bubble": False},
    ]
    html_out = wpp.render_arbitration_eligible(players, "2026-10-30T00:00:00+00:00", None, {})
    check("the bubble player is labelled 'On the Super Two bubble'",
          "On the Super Two bubble" in html_out)
    bubble_row = html_out.split("Bubble Player</a>")[1].split("</tr>")[0]
    solid_row = html_out.split("Solid Super Two</a>")[1].split("</tr>")[0]
    check("the bubble badge is attached to the bubble player, not the other one",
          "On the Super Two bubble" in bubble_row and "On the Super Two bubble" not in solid_row)
    check("a solid Super Two candidate still reads as plain 'Super Two'",
          ">Super Two<" in solid_row)


def test_the_offseason_pages_are_added_to_the_sitemap_and_workflows():
    """
    New generated paths have to clear three separate places by hand: the
    core sitemap, and the commit_data.sh path lists in both workflow YAMLs
    (see CLAUDE.md, "Add every new generated path to both workflows'
    commit_data.sh lists"). Missing any one of them means the page either
    never gets indexed or never gets committed by the daily job.
    """
    import write_player_pages as wpp

    ROOT = pathlib.Path(__file__).resolve().parents[1]
    core = (ROOT / "docs" / "sitemap-core.xml").read_text()
    for path in (
        wpp.ARBITRATION_ELIGIBLE_PATH, wpp.SUPER_TWO_PAGE_PATH,
        wpp.NON_TENDER_WATCH_PATH, wpp.FREE_AGENTS_PATH,
    ):
        check(f"sitemap-core.xml lists {path}", f"/{path}</loc>" in core)

    workflows = ROOT / ".github" / "workflows"
    for name in ("update-service-time.yml", "backfill-history.yml"):
        body = (workflows / name).read_text()
        check(
            f"{name} commits every root-level offseason page",
            all(
                f"docs/{p}" in body
                for p in (
                    wpp.ARBITRATION_ELIGIBLE_PATH, wpp.SUPER_TWO_PAGE_PATH,
                    wpp.NON_TENDER_WATCH_PATH, wpp.FREE_AGENTS_PATH,
                )
            ),
        )


def test_non_tender_watch_selection_states_its_rule_and_picks_expected_players():
    """
    select_non_tender_watch() applies exactly one rule, stated in its own
    docstring and printed verbatim (in plain language) on the rendered page:
    a player qualifies if he is already in his final arbitration year before
    free agency (within one credited year of free agency, i.e.
    service_days_total >= fa_days - FULL_YEAR_DAYS), OR he played in fewer
    than NON_TENDER_LOW_GAMES_THRESHOLD games in his most recent season.
    Pins the boundary of the first signal and that a player tripping
    neither signal is dropped entirely, not merely unlabeled.
    """
    import write_player_pages as wpp

    fa_days = wpp._days_for(wpp.FREE_AGENCY_YEARS)
    final_year_floor = fa_days - wpp.FULL_YEAR_DAYS

    def with_games(games):
        return {"season_stats": {"2026": {"hitting": {"gamesPlayed": games}}}}

    final_year_only = {
        "id": 1, "name": "Final Year Only", "team": "A", "on_40_man": True,
        "service_days_total": final_year_floor, "service_time": "5.028",
        **with_games(120),
    }
    low_games_only = {
        "id": 2, "name": "Low Games Only", "team": "A", "on_40_man": True,
        "service_days_total": 500, "service_time": "2.156",
        **with_games(5),
    }
    both_signals = {
        "id": 3, "name": "Both Signals", "team": "A", "on_40_man": True,
        "service_days_total": fa_days - 10, "service_time": "5.160",
        **with_games(10),
    }
    one_day_short_of_final_year = {
        "id": 4, "name": "One Day Short", "team": "A", "on_40_man": True,
        "service_days_total": final_year_floor - 1, "service_time": "5.027",
        **with_games(130),
    }
    neither_signal = {
        "id": 5, "name": "Neither Signal", "team": "A", "on_40_man": True,
        "service_days_total": 500, "service_time": "2.156",
        **with_games(50),
    }

    arb_eligible = [
        final_year_only, low_games_only, both_signals,
        one_day_short_of_final_year, neither_signal,
    ]
    watch = wpp.select_non_tender_watch(arb_eligible)
    got = {p["id"]: reasons for p, reasons in watch}

    check("the final-arbitration-year boundary is inclusive", 1 in got)
    check("one day short of that boundary (and not low-games) is excluded",
          4 not in got)
    check("a player on the low-games signal alone is included", 2 in got)
    check("a player on neither signal is dropped entirely, not just unlabeled",
          5 not in got)
    check("a player tripping both signals carries both reasons",
          len(got[3]) == 2)
    check("nothing else sneaks onto the list", set(got) == {1, 2, 3})

    html_out = wpp.render_non_tender_watch(watch, "2026-11-04T00:00:00+00:00", 2027)
    normalized = " ".join(html_out.split())
    check("the page states who it lists and points at the explainer",
          "meet this winter's non-tender watch" in normalized
          and "service-time.html#arbitration" in normalized)
    check("a listed player's reason still reaches the page",
          "final arbitration year before free agency" in normalized)
    check("a listed player's name reaches the page", "Final Year Only" in html_out)
    check("an excluded player's name does not", "Neither Signal" not in html_out)


def test_free_agents_by_year_boundary_at_exactly_six_years():
    """
    select_free_agents_now() and select_free_agents_by_year()/
    free_agency_year() must split cleanly at the free-agency threshold: a
    player already AT it belongs to "free agents now" and nowhere in the
    by-year groups (his free_agent_eligible flag already makes him one),
    while a player one day short of it belongs in the very next season's
    bucket, not the current one.
    """
    import write_player_pages as wpp

    fa_days = wpp._days_for(wpp.FREE_AGENCY_YEARS)
    next_season = 2027

    at_six = {
        "id": 1, "name": "Right At Six", "team": "A", "on_40_man": True,
        "service_days_total": fa_days, "service_time": "6.000",
        "free_agent_eligible": True,
    }
    just_under_six = {
        "id": 2, "name": "Just Under Six", "team": "A", "on_40_man": True,
        "service_days_total": fa_days - 1, "service_time": "5.171",
        "free_agent_eligible": False,
    }
    two_seasons_out = {
        "id": 3, "name": "Two Seasons Out", "team": "A", "on_40_man": True,
        "service_days_total": fa_days - wpp.FULL_YEAR_DAYS - 1, "service_time": "4.171",
        "free_agent_eligible": False,
    }

    players = [at_six, just_under_six, two_seasons_out]
    fa_now = {p["id"] for p in wpp.select_free_agents_now(players)}
    by_year = wpp.select_free_agents_by_year(players, next_season)
    by_year_ids = {year: {p["id"] for p in plist} for year, plist in by_year.items()}

    check("exactly 6.000 years lands in free agents now", 1 in fa_now)
    check("exactly 6.000 years is not grouped by year at all",
          all(1 not in ids for ids in by_year_ids.values()))
    check("one day short of 6.000 is not free agent now", 2 not in fa_now)
    check("one day short of 6.000 lands in the very next season",
          2 in by_year_ids.get(next_season, set()))
    check("needing one more full season after that lands one year later",
          3 in by_year_ids.get(next_season + 1, set()))


def test_club_offseason_subpages_have_correct_counts_titles_and_descriptions():
    """
    Each club gets three standalone offseason subpages. This pins that the
    row counts on each subpage match exactly what was handed to the
    renderer (no cross-club leakage, nothing dropped), and that each page
    carries its own unique, club-specific title and meta description -- not
    a copy-pasted generic one.
    """
    import write_player_pages as wpp

    arb_players = [
        {"id": 1, "name": "Arb Cub One", "team": "Chicago Cubs", "on_40_man": True,
         "service_days_total": 3 * wpp.FULL_YEAR_DAYS, "service_time": "3.000",
         "super_two_candidate": False},
        {"id": 2, "name": "Arb Cub Two", "team": "Chicago Cubs", "on_40_man": True,
         "service_days_total": 4 * wpp.FULL_YEAR_DAYS, "service_time": "4.000",
         "super_two_candidate": False},
    ]
    nt_pairs = [
        (arb_players[0], ["played in just 5 games last season"]),
    ]
    fa_now = [
        {"id": 3, "name": "Cub Free Agent", "team": "Chicago Cubs", "on_40_man": True,
         "service_time": "7.000"},
    ]
    fa_next = [
        {"id": 4, "name": "Cub Next Year FA", "team": "Chicago Cubs", "on_40_man": True,
         "service_time": "5.171"},
    ]

    arb_page = wpp.render_club_arbitration("Chicago Cubs", arb_players, "2026-11-04T00:00:00+00:00", 2027)
    nt_page = wpp.render_club_non_tender("Chicago Cubs", nt_pairs, "2026-11-04T00:00:00+00:00", 2027)
    fa_page = wpp.render_club_free_agents(
        "Chicago Cubs", fa_now, fa_next, "2026-11-04T00:00:00+00:00", 2027
    )

    check("the arbitration subpage lists exactly the two players handed to it",
          _tbody_row_count(arb_page) == 2 and "Arb Cub One" in arb_page and "Arb Cub Two" in arb_page)
    check("the non-tender subpage lists exactly the one pair handed to it",
          _tbody_row_count(nt_page) == 1 and "Arb Cub One" in nt_page)
    check("the free-agents subpage lists one now and one next year",
          "Cub Free Agent" in fa_page and "Cub Next Year FA" in fa_page)

    titles = {
        "arb": arb_page.split("<title>")[1].split("</title>")[0],
        "nt": nt_page.split("<title>")[1].split("</title>")[0],
        "fa": fa_page.split("<title>")[1].split("</title>")[0],
    }
    check("all three subpages have distinct titles", len(set(titles.values())) == 3)
    for name, page in (("arb", arb_page), ("nt", nt_page), ("fa", fa_page)):
        desc = page.split('meta name="description" content="')[1].split('"')[0]
        check(f"the {name} subpage's meta description names the club",
              "Chicago Cubs" in desc)


def test_write_club_pages_produces_three_subpages_per_club_with_right_totals():
    """
    End-to-end through write_player_pages(): for a small two-club roster,
    each club must get a roster page plus exactly three offseason subpages
    on disk, and the league-wide non-tender-watch.html / free-agents.html
    totals must equal the sum of what lands on the per-club subpages --
    the league and club pages are built from the same selectors (see
    _write_club_pages()), so they must never disagree about who is on
    either list.
    """
    import tempfile
    import write_player_pages as wpp

    tmp = tempfile.TemporaryDirectory()
    try:
        tmp_root = pathlib.Path(tmp.name)
        docs = tmp_root / "docs"
        docs.mkdir()

        patched = {
            "DOCS": docs,
            "PAGE_DIR": docs / "p",
            "CLUB_DIR": docs / "t",
            "ALUMNI_DIR": docs / "alumni",
            "LASTMOD_PATH": docs / "data" / "page_lastmod.json",
            "PUBLISHED_IDS_PATH": tmp_root / "data" / "published_ids.json",
        }
        saved = {name: getattr(wpp, name) for name in patched}
        for name, value in patched.items():
            setattr(wpp, name, value)

        try:
            fa_days = wpp._days_for(wpp.FREE_AGENCY_YEARS)
            arb_days = wpp._days_for(wpp.ARBITRATION_YEARS)

            def player(id_, name, team, team_id, days, extra=None):
                rec = {
                    "id": id_, "name": name, "team": team, "team_id": team_id,
                    "on_40_man": True, "missing_seasons": 0,
                    "service_days_total": days, "service_time": wpp._fmt(days),
                    "free_agent_eligible": days >= fa_days,
                }
                rec.update(extra or {})
                return rec

            db = {
                # Club One: one arbitration-eligible non-tender risk, one
                # free agent now.
                "1": player(1, "Club One Arb", "Club One", 1, arb_days,
                            {"season_stats": {"2026": {"hitting": {"gamesPlayed": 3}}}}),
                "2": player(2, "Club One FA Now", "Club One", 1, fa_days),
                # Club Two: one free agent next season, nobody arbitration
                # eligible or on the non-tender watch.
                "3": player(3, "Club Two FA Next", "Club Two", 2, fa_days - 1),
            }

            published = wpp.write_player_pages(db, "2026-11-04T00:00:00+00:00")
            check("all three players publish", len(published) == 3)

            for club in ("club-one", "club-two"):
                for suffix in ("-arbitration", "-non-tender", "-free-agents"):
                    check(f"{club}{suffix}.html was written",
                          (docs / "t" / f"{club}{suffix}.html").exists())

            club_one_arb = (docs / "t" / "club-one-arbitration.html").read_text()
            club_one_nt = (docs / "t" / "club-one-non-tender.html").read_text()
            club_one_fa = (docs / "t" / "club-one-free-agents.html").read_text()
            club_two_fa = (docs / "t" / "club-two-free-agents.html").read_text()

            check("Club One's arbitration subpage lists its one eligible player",
                  "Club One Arb" in club_one_arb and _tbody_row_count(club_one_arb) == 1)
            check("Club One's non-tender subpage lists the same player (low games)",
                  "Club One Arb" in club_one_nt and _tbody_row_count(club_one_nt) == 1)
            check("Club One's free-agents subpage lists its free-agent-now player",
                  "Club One FA Now" in club_one_fa)
            check("Club Two's free-agents subpage lists its next-season player",
                  "Club Two FA Next" in club_two_fa)

            league_nt = (docs / "non-tender-watch.html").read_text()
            league_fa = (docs / "free-agents.html").read_text()
            check("the league-wide non-tender page carries the same player",
                  "Club One Arb" in league_nt)
            check("the league-wide free-agents page carries both free agents",
                  "Club One FA Now" in league_fa and "Club Two FA Next" in league_fa)
        finally:
            for name, value in saved.items():
                setattr(wpp, name, value)
    finally:
        tmp.cleanup()


def _header_block(html_text):
    """The <header class="site-top">...</header> slice, or a hard failure.

    Every page-type test below starts here -- the header is the one piece
    of markup that must be byte-for-byte the same shape everywhere, so every
    assertion about it is scoped to this slice rather than the whole page,
    which would let an unrelated string elsewhere in the page (a footer
    link, a prose mention) pass a check it should not.
    """
    import re
    m = re.search(r'<header class="site-top".*?</header>', html_text, re.S)
    assert m, "page carries no site-top header"
    return m.group(0)


def _header_destinations(block):
    """Every href in a header block, normalised to a BASE_PATH-free key.

    Both href styles collapse to the same key: generated pages are
    BASE_PATH-absolute ("/t/") and the hand-written pages now match them
    exactly (see CLAUDE.md), but lstrip keeps this test from caring which.
    mailto: and the phone search icon's "?q=" link are not page destinations
    and are excluded so they cannot pollute a set-equality check.
    """
    import re
    hrefs = re.findall(r'href="([^"]+)"', block)
    return {
        h.lstrip("/") for h in hrefs
        if not h.startswith("mailto:") and "?" not in h
    }


def _menu_sample_pages():
    """One rendered HTML string per generated template, including the two
    (alumni, 404) that have no standalone render_*() returning a string --
    those are written to a scratch docs/ dir and read back, the same
    monkeypatch-DOCS pattern test_write_club_pages_produces_three_subpages_
    per_club_with_right_totals() uses.
    """
    import tempfile
    import write_player_pages as wpp

    player = {
        "id": 1, "name": "Test Player", "team": "Atlanta Braves",
        "on_40_man": True, "service_time": "1.000", "service_days_total": 172,
        "position": "P", "mlb_debut": "2020-01-01",
    }
    pages = {
        "render_club_index": wpp.render_club_index({"Atlanta Braves": []}, "2026-01-01"),
        "render_club": wpp.render_club("Atlanta Braves", [], "2026-01-01"),
        "render_club_arbitration": wpp.render_club_arbitration("Atlanta Braves", [], "2026-01-01", 2027),
        "render_club_non_tender": wpp.render_club_non_tender("Atlanta Braves", [], "2026-01-01", 2027),
        "render_club_free_agents": wpp.render_club_free_agents("Atlanta Braves", [], [], "2026-01-01", 2027),
        "render (player)": wpp.render(player, {}, "2026-01-01", []),
        "render_explainer": wpp.render_explainer("2026-01-01"),
        "render_arbitration_eligible": wpp.render_arbitration_eligible([], "2026-01-01", None, {}),
        "render_super_two": wpp.render_super_two([], {}, "2026-01-01", None),
        "render_non_tender_watch": wpp.render_non_tender_watch([], "2026-01-01", 2027),
        "render_free_agents": wpp.render_free_agents([], {}, "2026-01-01"),
    }

    tmp = tempfile.TemporaryDirectory()
    try:
        docs = pathlib.Path(tmp.name) / "docs"
        docs.mkdir()
        patched = {"DOCS": docs, "ALUMNI_DIR": docs / "alumni"}
        saved = {name: getattr(wpp, name) for name in patched}
        for name, value in patched.items():
            setattr(wpp, name, value)
        try:
            alumnus = {
                "id": 2, "name": "Old Timer", "team": "Atlanta Braves",
                "on_40_man": False, "service_time": "6.000", "service_days_total": 1032,
                "mlb_debut": "2010-01-01", "last_played": "2018-01-01",
            }
            written = wpp._write_alumni_pages([alumnus], "2026-01-01", wpp._LastMod("2026-01-01"))
            for rel in written:
                label = "render_alumni (letter)" if rel.endswith("/o.html") else "render_alumni (index)"
                pages[label] = (docs / rel).read_text(encoding="utf-8")
            wpp._write_404()
            pages["404"] = (docs / "404.html").read_text(encoding="utf-8")
        finally:
            for name, value in saved.items():
                setattr(wpp, name, value)
    finally:
        tmp.cleanup()
    return pages


_MENU_HAND_WRITTEN_PAGES = [
    "docs/index.html", "docs/contract.html", "docs/lockout.html",
    "docs/about.html", "docs/privacy.html", "docs/neutrality.html",
]


def test_the_site_header_is_the_same_on_every_page():
    """
    The header lands in many places -- six hand-written pages, the
    generated lockout section (built by scripts/build_lockout_page.py, which
    imports this module and reuses _site_header() rather than keeping its
    own copy), and every generated template -- and docs/index.html cannot
    read the Python that builds it. That is the exact shape of the bug that
    left the analytics token wrong in index.html while the generated
    templates were right.

    So the destination SET is asserted rather than trusted, and the
    presence of the logo link, the search form and the More button is
    checked structurally rather than assumed from one page. The club grid's
    30 links are excluded from the destination-set comparison and checked
    on their own in test_every_club_in_the_sheet_links_to_a_real_club_page().

    docs/taxes.html is excluded on purpose. It's parked (CLAUDE.md, "Do NOT
    touch docs/taxes.html") and keeps its pre-menu nav strip.
    """
    import write_player_pages as wpp

    ROOT = pathlib.Path(__file__).resolve().parents[1]
    nav_hrefs = {href for href, _tab, _sheet, _where in wpp.NAV}
    club_hrefs = {wpp.club_path(name) for name, _abbr in wpp.CLUB_ABBR}

    def check_block(label, block):
        check(f"{label}: logo link is present exactly once", block.count('class="brand"') == 1)
        check(f"{label}: search form is present exactly once",
              block.count('<form class="top-search') == 1)
        check(f"{label}: More button is present exactly once", block.count('aria-label="Menu"') == 1)
        check(f"{label}: header reaches every menu destination, and nothing else",
              _header_destinations(block) - club_hrefs == nav_hrefs)

    for rel in _MENU_HAND_WRITTEN_PAGES:
        html_text = (ROOT / rel).read_text(encoding="utf-8")
        check(f"{rel}: header appears exactly once", html_text.count('<header class="site-top"') == 1)
        check_block(rel, _header_block(html_text))

    for label, html_text in _menu_sample_pages().items():
        check(f"{label}: header appears exactly once", html_text.count('<header class="site-top"') == 1)
        check_block(label, _header_block(html_text))


def test_the_tab_row_matches_the_approved_phone_and_desktop_sets():
    """
    Phone and desktop show different numbers of tabs by design (the plan's
    "tabs that fit + sheet" option): four on phone -- Players, Clubs,
    Arbitration, Super Two -- so none is hidden with no cue it scrolls,
    the exact problem the old sideways-scrolling strip had. Lockout and
    Previous players are desktop-only tabs (class="hide-phone"), reachable
    everywhere through the sheet instead.
    """
    import re
    import write_player_pages as wpp

    block = _header_block(wpp._site_header(None))
    tabs = re.search(r'<nav class="tabs".*?</nav>', block, re.S).group(0)
    items = re.findall(r'<a([^>]*)>([^<]*)</a>', tabs)
    phone_labels = [label for attrs, label in items if "hide-phone" not in attrs]
    all_labels = [label for _attrs, label in items]

    check("phone tabs are exactly Players, Clubs, Arbitration, Super Two, in order",
          phone_labels == ["Players", "Clubs", "Arbitration", "Super Two"])
    check("desktop tabs add Lockout and Previous players, in order",
          all_labels == ["Players", "Clubs", "Arbitration", "Super Two", "Lockout", "Previous players"])


def test_the_sheet_lists_every_required_row_and_never_the_shelved_tools():
    """
    The More sheet is the one place every page links to Arbitration
    eligible, Super Two, Previous players A-Z and the Lockout tracker by
    their full names, plus the small site-info links and the contact
    address. Contract clock and Duty days are deliberately absent: both
    tools stay live at their own URLs, but neither is promoted from the
    menu -- the owner's call for this job, reversing the "in development"
    tag this sheet's predecessor nav used to carry for both.
    """
    import re
    import write_player_pages as wpp

    block = _header_block(wpp._site_header(None))
    sheet = re.search(r'<div class="sheet".*?</div></details>', block, re.S)
    assert sheet, "header carries no sheet"
    sheet_text = sheet.group(0)

    for label in (
        "Players", "Arbitration eligible", "Super Two",
        "Previous players A", "Lockout tracker",
        "What is service time?", "About", "Neutrality", "Privacy",
    ):
        check(f"sheet lists {label!r}", label in sheet_text)
    check("sheet lists the contact address",
          "contact@bigleagueservicetime.com" in sheet_text)

    check("header does not link contract.html anywhere", "contract.html" not in block)
    check("header does not link taxes.html anywhere", "taxes.html" not in block)


def test_every_club_in_the_sheet_links_to_a_real_club_page():
    """
    All 30 clubs, by abbreviation, every one reachable in two taps from any
    page. CLUB_ABBR is a fixed list (see its own docstring) rather than
    something read off today's roster, so this test checks it against the
    pages club_path() + slug() actually resolves to on disk -- the same
    function the club pages themselves are written with -- rather than
    trusting the two independently.
    """
    import write_player_pages as wpp

    ROOT = pathlib.Path(__file__).resolve().parents[1]
    block = _header_block(wpp._site_header(None))

    check("the sheet lists all 30 clubs", len(wpp.CLUB_ABBR) == 30)
    for name, abbr in wpp.CLUB_ABBR:
        href = wpp.club_path(name)
        check(f"sheet links {abbr} to {href}",
              f'<li><a href="/{href}">{abbr}</a></li>' in block)
        check(f"{href} exists on disk", (ROOT / "docs" / href).exists())


def test_the_header_marks_the_right_tab_current_and_only_one():
    """
    The CSS keys the current-page underline off aria-current rather than a
    hand-set class, so the accessible state and the visible state cannot
    disagree. That only holds if aria-current is emitted on the one matching
    tab -- and on NO tab for a page type that has none (a player page, a
    club page's offseason subpages, the explainer): claiming a tab that
    is not really this page would mislead more than claiming none.
    """
    import re
    import write_player_pages as wpp

    def current_labels(html_text):
        tabs = re.search(r'<nav class="tabs".*?</nav>', _header_block(html_text), re.S).group(0)
        return re.findall(r'<a[^>]*aria-current="page"[^>]*>([^<]*)</a>', tabs)

    one_tab_cases = [
        ("docs/index.html", "Players"),
        ("docs/lockout.html", "Lockout"),
    ]
    ROOT = pathlib.Path(__file__).resolve().parents[1]
    for rel, label in one_tab_cases:
        html_text = (ROOT / rel).read_text(encoding="utf-8")
        check(f"{rel} marks exactly one tab current", len(current_labels(html_text)) == 1)
        check(f"{rel} marks {label!r} current", current_labels(html_text) == [label])

    generated_cases = [
        ("render_club_index", wpp.render_club_index({"Atlanta Braves": []}, "2026-01-01"), "Clubs"),
        ("render_club", wpp.render_club("Atlanta Braves", [], "2026-01-01"), "Clubs"),
        ("render_arbitration_eligible",
         wpp.render_arbitration_eligible([], "2026-01-01", None, {}), "Arbitration"),
        ("render_super_two", wpp.render_super_two([], {}, "2026-01-01", None), "Super Two"),
    ]
    for label, html_text, tab in generated_cases:
        check(f"{label} marks exactly one tab current", len(current_labels(html_text)) == 1)
        check(f"{label} marks {tab!r} current", current_labels(html_text) == [tab])

    for rel in ("docs/contract.html", "docs/about.html", "docs/privacy.html", "docs/neutrality.html"):
        html_text = (ROOT / rel).read_text(encoding="utf-8")
        check(f"{rel} marks no tab current (none applies)", current_labels(html_text) == [])

    no_tab_generated = {
        "render_explainer": wpp.render_explainer("2026-01-01"),
        "render_non_tender_watch": wpp.render_non_tender_watch([], "2026-01-01", 2027),
        "render_free_agents": wpp.render_free_agents([], {}, "2026-01-01"),
        "render_club_arbitration": wpp.render_club_arbitration("Atlanta Braves", [], "2026-01-01", 2027),
        "render (player)": wpp.render(
            {"id": 1, "name": "Test Player", "team": "Atlanta Braves", "on_40_man": True,
             "service_time": "1.000", "service_days_total": 172, "position": "P",
             "mlb_debut": "2020-01-01"},
            {}, "2026-01-01", [],
        ),
    }
    for label, html_text in no_tab_generated.items():
        check(f"{label} marks no tab current (none applies)", current_labels(html_text) == [])


def test_no_banned_string_appears_in_the_header_or_sheet():
    """
    No script name, file path, data-source name or GitHub link may appear
    anywhere a reader can see it (CLAUDE.md, "Hard rules"), and this menu
    reaches every page on the site, so it is the highest-leverage place to
    pin that. "public MLB data" is banned too -- the site's copy says
    "public data" -- and so are direct links to the two shelved tools,
    which must not be promoted from here under any string.
    """
    import write_player_pages as wpp

    banned = (
        "scripts/", ".py", "Stats API", "github.com",
        "public MLB data", "taxes.html", "contract.html",
    )
    block = _header_block(wpp._site_header(None))
    for needle in banned:
        check(f"header/sheet does not contain {needle!r}", needle not in block)

    for label, html_text in _menu_sample_pages().items():
        block = _header_block(html_text)
        for needle in banned:
            check(f"{label}: header/sheet does not contain {needle!r}", needle not in block)

    ROOT = pathlib.Path(__file__).resolve().parents[1]
    for rel in _MENU_HAND_WRITTEN_PAGES:
        block = _header_block((ROOT / rel).read_text(encoding="utf-8"))
        for needle in banned:
            check(f"{rel}: header/sheet does not contain {needle!r}", needle not in block)


def _footer_block(html_text):
    """The <footer class="site-foot">...</footer> slice, or a hard failure.

    Same reasoning as _header_block(): every footer assertion is scoped to
    this slice so a stray mention elsewhere on the page (the page-header's
    own links, a body paragraph) cannot satisfy a check it should not.
    """
    import re
    m = re.search(r'<footer class="site-foot".*?</footer>', html_text, re.S)
    assert m, "page carries no site-foot footer"
    return m.group(0)


def test_the_footer_is_the_same_on_every_page():
    """
    The footer, like the header, lands in many places that cannot share one
    Python function -- six hand-written pages plus every generated
    template -- so the destination SET is asserted per page/template rather
    than trusted. Same shape, same reason, as
    test_the_site_header_is_the_same_on_every_page().

    docs/taxes.html is excluded on purpose, same as the header test: it is
    parked and keeps its own page footer (CLAUDE.md, "Do NOT touch
    docs/taxes.html").
    """
    import write_player_pages as wpp

    ROOT = pathlib.Path(__file__).resolve().parents[1]
    contact_link = "mailto:contact@bigleagueservicetime.com"
    required_hrefs = {
        "", "t/", "arbitration-eligible.html", "super-two.html", f"{wpp.ALUMNI_DIR_NAME}/",
        "lockout.html", "service-time.html", "about.html", "neutrality.html", "privacy.html",
    }

    def check_block(label, html_text):
        check(f"{label}: footer appears exactly once",
              html_text.count('<footer class="site-foot"') == 1)
        block = _footer_block(html_text)
        check(f"{label}: footer reaches exactly the required destinations",
              _header_destinations(block) == required_hrefs)
        check(f"{label}: footer shows the contact address", contact_link in block)
        check(f"{label}: footer does not link to the contract clock",
              "contract.html" not in block)
        check(f"{label}: footer does not link to duty days",
              "taxes.html" not in block)

    for rel in _MENU_HAND_WRITTEN_PAGES:
        check_block(rel, (ROOT / rel).read_text(encoding="utf-8"))

    for label, html_text in _menu_sample_pages().items():
        check_block(label, html_text)


def test_no_banned_string_appears_in_the_footer():
    """
    Same protection as test_no_banned_string_appears_in_the_header_or_sheet(),
    scoped to the footer -- it is the other piece of markup that reaches
    every page, so a data-source name or GitHub link slipping in here is
    just as visible as one in the header.
    """
    banned = (
        "scripts/", ".py", "Stats API", "github.com", "RainbowMang0", "README",
        "source code", "public MLB data", "contract.html", "taxes.html",
    )

    def check_banned(label, block):
        for needle in banned:
            check(f"{label}: footer does not contain {needle!r}", needle not in block)

    ROOT = pathlib.Path(__file__).resolve().parents[1]
    for rel in _MENU_HAND_WRITTEN_PAGES:
        check_banned(rel, _footer_block((ROOT / rel).read_text(encoding="utf-8")))

    for label, html_text in _menu_sample_pages().items():
        check_banned(label, _footer_block(html_text))


def test_no_page_in_docs_links_to_the_repo():
    """
    The data-source review's one hard requirement for this job: nothing
    published may point back at the repo itself. Checked over every file
    actually shipped under docs/, not just the pages this job touched, so a
    leftover link anywhere else would still be caught.
    """
    ROOT = pathlib.Path(__file__).resolve().parents[1]
    docs = ROOT / "docs"
    offenders = []
    for path in docs.rglob("*.html"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "github.com/RainbowMang0" in text:
            offenders.append(str(path.relative_to(ROOT)))
    check("no page under docs/ links to github.com/RainbowMang0", not offenders)


def test_the_404_template_carries_the_site_header():
    """
    The 404 is served for unknown paths anywhere on the site (including
    under /p/), and it is the one template that carried NO navigation at
    all before this job -- a visitor landing on a typo had exactly one way
    back in, the "Search all players" link. It gets the full header now,
    like every other page.
    """
    import write_player_pages as wpp

    html_text = _menu_sample_pages()["404"]
    check("404 template has a site-top header", html_text.count('<header class="site-top"') == 1)
    block = _header_block(html_text)
    check("404 header has a logo link", 'class="brand"' in block)
    check("404 header has a More button", 'aria-label="Menu"' in block)


def test_the_daily_service_time_tick_is_not_a_content_change():
    """
    Measured 2026-09-08, after Search Console reported 782 pages "Discovered --
    currently not indexed". Across five consecutive daily commits the sitemap
    claimed 1,138-1,144 of ~1,405 URLs had changed that day, every day.

    It was telling the truth, and that was the problem: an accruing player
    gains a day, so his figure really moves. Diffed at N vs N+1 days, the only
    lines that change are the Y.DDD figure, the spelled-out figure and the day
    count. Google asks lastmod to carry the last SIGNIFICANT change, and an
    eight-day-old domain has a small crawl budget -- we were spending it asking
    for 98% of the site to be re-fetched daily while 782 pages had never been
    crawled once.

    Simulated over all 1,370 player pages: 1,348 pages would have moved before,
    4 after.

    The four cases below are the whole contract. The first pass at the
    normalisation list missed the spelled-out figure and case (b) caught it.
    """
    import copy
    import write_player_pages as w

    player = {
        "id": 592450, "name": "Test Player", "team": "Some Club", "team_id": 137,
        "position": "P", "mlb_debut": "2017-04-01", "service_time": "8.156",
        "service_days_total": 1532, "on_40_man": True, "arbitration_eligible": True,
        "seasons": [{"y": 2024, "d": 172, "raw": 172, "pro": 172, "t": [137], "src": "read"},
                    {"y": 2025, "d": 156, "raw": 156, "pro": 156, "t": [137], "src": "read"}],
    }
    at = "2026-09-08T00:00:00+00:00"
    key = lambda rec: w._content_key(w.render(rec, {137: "Some Club"}, at))
    base = key(player)

    def plus(days):
        q = copy.deepcopy(player)
        q["service_days_total"] += days
        q["service_time"] = f"{q['service_days_total'] // 172}.{q['service_days_total'] % 172:03d}"
        q["seasons"][-1]["d"] += days
        return q

    check("an identical page keeps its date", key(player) == base)
    check("(a) one more day does NOT move the date", key(plus(1)) == base)
    check("(b) thirty more days do not either", key(plus(30)) == base)

    traded = copy.deepcopy(player); traded["team"] = "Another Club"
    check("(c) a trade DOES move it", key(traded) != base)

    promoted = copy.deepcopy(player); promoted["free_agent_eligible"] = True
    check("(d) a status change DOES move it", key(promoted) != base)

    extra = copy.deepcopy(player)
    extra["seasons"] = extra["seasons"] + [
        {"y": 2026, "d": 40, "raw": 40, "pro": 40, "t": [137], "src": "read"}
    ]
    check("(e) a new season row DOES move it", key(extra) != base)

    real = w._threshold_sentence
    try:
        w._threshold_sentence = lambda: real() + " An added sentence."
        check("(f) a template edit DOES move every page", key(player) != base)
    finally:
        w._threshold_sentence = real


def test_only_alumni_with_a_defensible_figure_are_published():
    """
    Widened 2026-09-08 from "rostered players only", but not to everybody.

    The non-rostered population is 4,222 and its data is measurably weaker than
    the 40-man's: 27% declare missing seasons and 16% of their credited seasons
    are presumed from the debut rather than read. Publishing all of them would
    have added the site's weakest pages to a queue Google was already behind on
    -- 782 pages sat "Discovered - currently not indexed" the day this was
    decided.

    So the rule takes the 2,466 whose figures stand on their own: the feed can
    see the FRONT of the career (missing_seasons == 0) and there is more than
    one credited season to show. The remaining 1,756 wait for evidence that
    these index at all.
    """
    import write_player_pages as w

    rostered = {"on_40_man": True, "missing_seasons": 3, "seasons": []}
    check("a rostered player is always published", w._should_publish(rostered))

    good = {"on_40_man": False, "missing_seasons": 0,
            "seasons": [{"y": 2019, "d": 172}, {"y": 2020, "d": 60}]}
    check("a complete-history alumnus with 2 seasons is published",
          w._should_publish(good) and w._is_alumnus(good))

    gappy = {"on_40_man": False, "missing_seasons": 4,
             "seasons": [{"y": 2019, "d": 172}, {"y": 2020, "d": 60}]}
    check("...but one declaring missing seasons is NOT", not w._should_publish(gappy))

    thin = {"on_40_man": False, "missing_seasons": 0, "seasons": [{"y": 2019, "d": 12}]}
    check("...nor one with a single credited season", not w._should_publish(thin))

    empty = {"on_40_man": False, "missing_seasons": 0,
             "seasons": [{"y": 2019, "d": 0}, {"y": 2020, "d": 0}]}
    check("...nor one credited nothing at all", not w._should_publish(empty))

    check("a rostered player is never counted as an alumnus",
          not w._is_alumnus(rostered))


def test_a_published_alumnus_is_reachable_and_off_the_club_pages():
    """
    Two things that would each quietly break the experiment.

    ORPHANS. Club pages list the 40-man only, so a published alumnus has no
    inbound internal link unless something else provides one -- a page
    reachable only from the sitemap is the worst possible starting position for
    one whose entire purpose is to be found. The A-Z directory at /alumni/ is
    that something, and index.html carries the one hand-written link into it.

    STALE CLUBS. A retired player's stored `team` is the last club we saw him
    with. Listing him on that club's 40-man page would assert a roster spot he
    does not hold -- the category error already fixed once in the table's
    payload, and _write_club_pages() now filters explicitly rather than
    relying on `published` happening to be the 40-man.
    """
    import glob
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    docs = root / "docs"
    import write_player_pages as w

    db = json.loads((docs / "data" / "service_time.json").read_text())["players"]
    alumni = {str(p["id"]) for p in db if w._is_alumnus(p)}
    check(f"the alumni batch is non-empty ({len(alumni)} players)", bool(alumni))

    linked = set()
    for f in glob.glob(str(docs / "alumni" / "*.html")):
        linked |= set(re.findall(r'href="\.\./p/(\d+)-', pathlib.Path(f).read_text()))
    check("every published alumnus is linked from a letter page",
          not (alumni - linked))

    check("the alumni directory exists", (docs / "alumni" / "index.html").exists())
    check("the homepage links to it",
          'href="alumni/"' in (docs / "index.html").read_text())

    rostered_ids = {str(p["id"]) for p in db if p.get("on_40_man")}
    on_club_pages = set()
    for f in glob.glob(str(docs / "t" / "*.html")):
        on_club_pages |= set(re.findall(r'href="\.\./p/(\d+)-', pathlib.Path(f).read_text()))
    check("no retired player appears on a club page",
          not (on_club_pages - rostered_ids))

    idx = json.loads((docs / "data" / "index.json").read_text())
    check("the index declares a has_page column", "has_page" in idx["fields"])
    col = idx["fields"].index("has_page")
    flagged = sum(1 for r in idx["players"] if r[col] == 1)
    # Sticky ids count too: a player who left his roster keeps his page (see
    # load_ever_published()), and the index's has_page flag must say so.
    ever_published = w.load_ever_published()
    check(f"...and it counts the published pages ({flagged})",
          flagged == sum(1 for p in db if w._should_publish(p, ever_published)))


def test_a_player_who_leaves_the_40man_keeps_his_page():
    """
    OWNER'S RULE (Braxton, 2026-10-30): a player's page outlives his roster
    spot. The 2026-10-30 daily update dropped five pages -- Verlander,
    Scherzer and three others -- the moment they came off a 40-man roster,
    because `_should_publish()` on its own only re-qualifies a non-rostered
    player who passes `_is_alumnus()` (complete history, more than one
    credited season), and these five did not.

    `_should_publish()` now takes the sticky `ever_published` set as a second,
    optional argument: a fresh roster/alumnus check decides who is NEWLY
    published, but an id already in that set stays published regardless.
    """
    import write_player_pages as w

    thin = {"id": 777001, "on_40_man": True, "missing_seasons": 10,
            "seasons": [{"y": 2020, "d": 5}]}
    check("published while rostered, even with thin history",
          w._should_publish(thin))

    thin["on_40_man"] = False
    check("fresh criteria alone drop him the moment he leaves the roster",
          not w._should_publish(thin) and not w._is_alumnus(thin))
    check("...but the sticky set keeps him published",
          w._should_publish(thin, {777001}))
    check("a sticky id for someone else does not publish him",
          not w._should_publish(thin, {1, 2, 3}))


def test_a_previously_published_page_is_never_dropped():
    """
    End-to-end version of the rule above: running write_player_pages() a
    second time, with the player now off the roster and shaped exactly like
    the kind of record `_is_alumnus()` rejects, must not delete his file --
    the generator rebuilds docs/p/ wholesale on every run (see the rmtree in
    write_player_pages()), so the only thing standing between "has a page"
    and "page silently vanishes" is whether his id is remembered.

    Runs against a temporary docs/ tree (the module's directory and
    published-ids constants are monkeypatched for the duration) so this does
    not touch the real site.
    """
    import tempfile
    import write_player_pages as w

    tmp = tempfile.TemporaryDirectory()
    try:
        tmp_root = pathlib.Path(tmp.name)
        docs = tmp_root / "docs"
        docs.mkdir()

        patched = {
            "DOCS": docs,
            "PAGE_DIR": docs / "p",
            "CLUB_DIR": docs / "t",
            "ALUMNI_DIR": docs / "alumni",
            "LASTMOD_PATH": docs / "data" / "page_lastmod.json",
            "PUBLISHED_IDS_PATH": tmp_root / "data" / "published_ids.json",
        }
        saved = {name: getattr(w, name) for name in patched}
        for name, value in patched.items():
            setattr(w, name, value)

        try:
            player_id = 777002
            rostered = {
                "id": player_id, "name": "Test Fading Player", "on_40_man": True,
                "team": "Test Club", "team_id": 1, "missing_seasons": 0,
                "seasons": [{"y": 2026, "d": 20}],
            }
            first_run = w.write_player_pages(
                {str(player_id): rostered}, "2026-10-29T00:00:00+00:00"
            )
            check("published on the first run, while rostered",
                  any(p["id"] == player_id for p in first_run))
            page = w.PAGE_DIR / w._player_filename(rostered)
            check("his page file exists after the first run", page.exists())

            fallen_off = dict(rostered)
            fallen_off["on_40_man"] = False
            check("sanity: fresh criteria alone would reject this record",
                  not w._is_alumnus(fallen_off))

            second_run = w.write_player_pages(
                {str(player_id): fallen_off}, "2026-10-30T00:00:00+00:00"
            )
            check("still published after dropping off the roster",
                  any(p["id"] == player_id for p in second_run))
            check("his page file survives the wholesale rebuild", page.exists())
            check("the sticky id file remembers him",
                  player_id in w.load_ever_published())
        finally:
            for name, value in saved.items():
                setattr(w, name, value)
    finally:
        tmp.cleanup()


def test_the_homepage_has_no_dead_links():
    """
    docs/index.html is the one hand-maintained page carrying real <a href>
    links into generated pages -- the "t/" hub and the region
    `_update_homepage_key_players()` rewrites between KEY_PLAYERS_START/END.
    Those links are only as good as the files they point to: the 2026-10-30
    daily update rebuilt docs/p/ without the five players this file was
    still linking to, and nothing caught it until validate_published.py was
    run by hand. This pins the same check here so the plain test run does
    too.
    """
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    docs = root / "docs"
    text = (docs / "index.html").read_text(encoding="utf-8")

    dead = []
    for href in re.findall(r'href="((?!https?:|#|mailto:)[^"]+)"', text):
        clean = href.split("#")[0].split("?")[0]
        if not clean or clean.startswith("/"):
            continue  # site-absolute; validate_published.py covers these
        target = (docs / clean).resolve()
        if clean.endswith("/") or target.is_dir():
            target = target / "index.html"
        if not target.exists():
            dead.append(href)
    check(f"every relative homepage link resolves to a real file ({len(dead)} dead)",
          not dead)


def test_the_sitemap_is_an_index_split_by_section():
    """
    Search Console reports indexed-vs-submitted per sitemap, and one flat file
    of 1,406 URLs cannot say WHICH 782 pages went unindexed. Split by section,
    the next report answers it: club pages indexing while player pages do not
    is a statement about thin templated pages; both lagging equally is a
    statement about the site's age.

    sitemap.xml stays the entry point and becomes the index, so the URL already
    submitted to Search Console keeps working and nothing has to be
    re-submitted by hand.
    """
    docs = pathlib.Path(__file__).resolve().parents[1] / "docs"
    index = (docs / "sitemap.xml").read_text()
    check("sitemap.xml is a sitemap index", "<sitemapindex" in index)
    for child in ("sitemap-core.xml", "sitemap-clubs.xml",
                  "sitemap-players.xml", "sitemap-alumni.xml"):
        check(f"...it lists {child}", child in index)
        path = docs / child
        check(f"...and {child} exists as a urlset",
              path.exists() and "<urlset" in path.read_text())

    workflows = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows"
    for name in ("update-service-time.yml", "backfill-history.yml"):
        body = (workflows / name).read_text()
        check(
            f"{name} stages the new sitemap files",
            all(c in body for c in
                ("sitemap-core.xml", "sitemap-clubs.xml", "sitemap-players.xml",
                 "sitemap-alumni.xml", "docs/alumni")),
        )


def test_lastmod_moves_only_when_a_page_actually_changes():
    """
    Every sitemap URL used to claim today's date, every day. Between the World
    Series and Opening Day nothing changes at all, so that is five months of
    1,390 daily lies -- the textbook unreliable-lastmod pattern that Google
    answers by ignoring lastmod for the whole site.

    The footer stamp moves daily on every page regardless, so it has to be
    stripped before hashing or nothing would ever compare equal.
    """
    import write_player_pages as w

    page = "<h1>X</h1><p>1092 days</p><p>Last updated 2026-08-25.</p>"
    same_day_later = "<h1>X</h1><p>1092 days</p><p>Last updated 2026-08-26.</p>"
    real_change = "<h1>X</h1><p>1093 days</p><p>Last updated 2026-08-26.</p>"

    check(
        "the daily footer stamp is not content",
        w._content_key(page) == w._content_key(same_day_later),
    )
    check(
        "a changed figure is content",
        w._content_key(page) != w._content_key(real_change),
    )

    tracker = w._LastMod("2026-08-25")
    tracker.previous = {}
    tracker.record("p/x.html", page)
    check("a page with no history is dated today", tracker.of("p/x.html") == "2026-08-25")

    tomorrow = w._LastMod("2026-08-26")
    tomorrow.previous = dict(tracker.current)
    tomorrow.record("p/x.html", same_day_later)
    check(
        "an unchanged page keeps its old date",
        tomorrow.of("p/x.html") == "2026-08-25",
    )

    moved = w._LastMod("2026-08-26")
    moved.previous = dict(tracker.current)
    moved.record("p/x.html", real_change)
    check("a changed page is redated", moved.of("p/x.html") == "2026-08-26")


def test_a_missing_lastmod_manifest_degrades_to_the_old_behaviour():
    """
    The manifest is just a cache. Losing it must not be able to publish a date
    that is wrong in a harmful direction -- "everything changed today" is the
    behaviour this replaced, so it is a safe floor.
    """
    import write_player_pages as w
    tracker = w._LastMod("2026-08-26")
    tracker.previous = {}
    tracker.record("p/x.html", "<p>anything</p>")
    check("falls back to today", tracker.of("p/x.html") == "2026-08-26")
    check("an unknown path also reads today", tracker.of("p/never-seen.html") == "2026-08-26")


def test_a_disagreement_is_labelled_by_the_moves_around_it():
    """
    A gate summary saying "0.4% over-credited" names no rule to change. The
    sweep labels each disagreeing date by the roster move on either side, so
    a repeated shape becomes visible across players.

    Fulford's disputed 2025-04-26 is the worked example: recalled that day,
    optioned the next, and MLB's roster says he never arrived.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    import validate_against_rosters as v

    check("an option is recognised", v.shape_of("Rockies optioned C X to Albuquerque.") == "optioned")
    check("a DFA is not an option", v.shape_of("Giants designated LHP X for assignment.") == "DFA")
    check(
        "coming off the IL is distinguished from going on it",
        v.shape_of("Yankees activated RHP X from the 60-day injured list.") == "activated-IL"
        and v.shape_of("Yankees placed RHP X on the 10-day injured list.") == "placed-IL",
    )
    check(
        "a Rule 5 return is not a plain activation",
        v.shape_of("SS X returned to Cleveland Guardians from Atlanta Braves.") == "rule-5-return",
    )
    check("an unknown verb falls through", v.shape_of("Club did something novel.") == "other")

    txns = [
        Transaction(dt.date(2025, 4, 25), "Rockies optioned C X to Albuquerque Isotopes."),
        Transaction(dt.date(2025, 4, 26), "Rockies recalled C X from Albuquerque Isotopes."),
        Transaction(dt.date(2025, 4, 27), "Rockies optioned C X to Albuquerque Isotopes."),
    ]
    prev_s, next_s = v.classify(txns, dt.date(2025, 4, 26))
    check("the move on the day itself reads +0d", prev_s == "recalled+0d")
    check("the move that undoes it reads -1d", next_s == "optioned-1d")

    # The first version printed the exact day count, so one player wrong for two
    # months and sampled weekly produced nine "distinct shapes" that were one
    # defect. Buckets collapse a continuous error back into a single row.
    check("a week and six weeks land in different buckets",
          v._gap_bucket(7) == "2-7d" and v._gap_bucket(45) == "31-90d")
    check("consecutive weekly samples of one stretch collapse",
          len({v._gap_bucket(n) for n in (31, 38, 45, 52, 59)}) == 1)
    check("but a same-day and a next-day move stay distinguishable",
          v._gap_bucket(0) != v._gap_bucket(1))

    before_everything = v.classify(txns, dt.date(2025, 1, 1))
    check("a date before any move says so", before_everything[0] == "(nothing before)")
    after_everything = v.classify(txns, dt.date(2025, 12, 1))
    check("a date after every move says so", after_everything[1] == "(nothing after)")


def test_bereavement_and_paternity_accrue_but_the_restricted_list_does_not():
    """
    Found 2026-08-26 chasing Zach Agnos, whose 2025 includes three days on the
    bereavement list. The CBA is explicit: a player on the Bereavement/Family
    Medical Emergency or Paternity Leave list counts against the 40-man, does
    NOT count against the active roster, and keeps accruing service time.

    Our model already got this right by accident -- neither placement is a
    start or a stop, so an open interval simply continues. The TRUTH SOURCE
    had it wrong, scoring those days as not accruing and marking us OVER for
    days we credit correctly.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    from validate_against_rosters import accrues_off_active_roster

    for code in ("BRV", "FME", "PL"):
        check(f"{code} accrues", accrues_off_active_roster(code))
    for code in ("D7", "D10", "D15", "D60"):
        check(f"{code} accrues", accrues_off_active_roster(code))
    # The lists that look similar and do NOT accrue must stay excluded.
    for code in ("RM", "RL", "SU", "DEC", ""):
        check(f"{code or '(blank)'} does not accrue", not accrues_off_active_roster(code))


def test_a_bereavement_placement_does_not_stop_the_clock():
    """The model side of the same rule, pinned so a future stop keyword cannot
    quietly swallow it."""
    txns = [
        Transaction(dt.date(2025, 4, 20), "Colorado Rockies selected the contract of RHP X from Albuquerque Isotopes."),
        Transaction(dt.date(2025, 6, 3), "Colorado Rockies placed RHP X on the bereavement list."),
        Transaction(dt.date(2025, 6, 6), "Colorado Rockies activated RHP X from the bereavement list."),
        Transaction(dt.date(2025, 6, 15), "Colorado Rockies optioned RHP X to Albuquerque Isotopes."),
    ]
    intervals = build_global_active_intervals(txns, dt.date(2025, 9, 28))
    check("one unbroken interval across the bereavement days", len(intervals) == 1)
    check("it opens at the contract selection", intervals[0][0] == dt.date(2025, 4, 20))
    check("and closes the day before the option", intervals[0][1] == dt.date(2025, 6, 14))


def test_the_roster_sweep_runs_end_to_end_without_touching_the_network():
    """
    A 32-club-season sweep spent 26 minutes on live API calls and then threw
    all of it away on a NameError in its final line, because the reporting
    function had been deleted in a refactor and nothing exercised that path
    offline.

    So the whole of main() -- collection, scoring, the per-variant report and
    the error-shape breakdown -- runs here against a fake feed. It is fast and
    it is the only thing standing between a refactor and another wasted run.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    import io
    import contextlib
    import validate_against_rosters as v

    season_start, season_end = dt.date(2024, 3, 28), dt.date(2024, 4, 30)

    optioned_on = dt.date(2024, 4, 16)

    class FakeMLB:
        @staticmethod
        def get_season_window(year):
            return season_start, season_end

        @staticmethod
        def _get(path, params):
            # One player: on the active roster from Opening Day until he is
            # optioned, on the 40-man but inactive afterwards. An absolute date,
            # not a day-of-month -- the window crosses a month boundary.
            day = dt.date.fromisoformat(params["date"])
            active = day < optioned_on
            entry = {"person": {"id": 1, "fullName": "Test Player"},
                     "status": {"code": "A" if active else "RM"}}
            if params["rosterType"] == "active" and not active:
                return {"roster": []}
            return {"roster": [entry]}

        @staticmethod
        def get_player_transactions(pid, start, end):
            return [
                {"date": "2024-03-28", "description": "Club selected the contract of RHP Test Player.",
                 "team_id": 147},
                {"date": "2024-04-16", "description": "Club optioned RHP Test Player to Somewhere.",
                 "team_id": 147},
            ]

        @staticmethod
        def get_player_bio(pid):
            return {"mlbDebutDate": "2024-03-28"}

    original_mlb, original_ids, original_argv = v.mlb, v.mlb_team_ids, sys.argv
    try:
        v.mlb = FakeMLB
        v.mlb_team_ids = lambda: {147}
        sys.argv = ["validate_against_rosters.py", "--team", "147", "--season", "2024",
                    "--interval", "1", "--carry-in", "on"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            v.main()
        text = out.getvalue()
    finally:
        v.mlb, v.mlb_team_ids, sys.argv = original_mlb, original_ids, original_argv

    check("it reports a carry-in variant", "carry-in: ON" in text)
    check("it reaches the agreement line", "AGREE" in text)
    check("it reaches the over/under lines", "MODEL OVER" in text and "MODEL UNDER" in text)
    check("the fake feed is scored perfectly", "(100.0%)" in text)
    check("a multi-club sweep parses", "Sweeping 1 club-season(s)" in text)


def test_a_waiver_claim_followed_straight_by_a_recall_is_a_minor_league_stint():
    """
    Finding #19, from Josh Walker: claimed by Baltimore 2025-08-21, and the very
    next major league move is "recalled from Norfolk Tides" 39 days later. He
    cannot be recalled FROM Norfolk unless he was AT Norfolk, so the claim put
    him on the 40-man and sent him down; those 39 days are phantom.

    This is the feed's own words rather than a belief about MLB's vocabulary,
    which is what separates it from the rules findings #4 and #5 had to revert.
    """
    txns = [
        Transaction(dt.date(2025, 8, 19), "Philadelphia Phillies designated LHP X for assignment."),
        Transaction(dt.date(2025, 8, 21), "Baltimore Orioles claimed LHP X off waivers from Philadelphia Phillies."),
        Transaction(dt.date(2025, 9, 29), "Baltimore Orioles recalled LHP X from Norfolk Tides."),
    ]
    windows = claim_then_recall_windows(txns)
    check("the claim-to-recall window is found", windows == [(dt.date(2025, 8, 21), dt.date(2025, 9, 29))])


def test_a_claim_with_a_move_in_between_is_not_the_same_shape():
    """
    The rule fires only when NOTHING sits between the claim and the recall.
    With an option in between the days are already handled correctly, and with
    anything else in between "he was down the whole time" stops being the only
    available reading.
    """
    txns = [
        Transaction(dt.date(2025, 8, 21), "Orioles claimed LHP X off waivers from Phillies."),
        Transaction(dt.date(2025, 8, 25), "Orioles optioned LHP X to Norfolk Tides."),
        Transaction(dt.date(2025, 9, 29), "Orioles recalled LHP X from Norfolk Tides."),
    ]
    check("an intervening option suppresses it", claim_then_recall_windows(txns) == [])

    played = [
        Transaction(dt.date(2025, 8, 21), "Orioles claimed LHP X off waivers from Phillies."),
        Transaction(dt.date(2025, 9, 1), "Orioles placed LHP X on the 15-day injured list."),
        Transaction(dt.date(2025, 9, 29), "Orioles recalled LHP X from Norfolk Tides."),
    ]
    check("so does anything else", claim_then_recall_windows(played) == [])


def test_the_broad_version_of_the_recall_rule_stays_rejected():
    """
    Recorded as a test because the broad reading is the tempting one and it is
    measurably wrong. Caleb Ferguson's interval runs from 2019 across his Tommy
    John year -- spent on the 60-day IL, which ACCRUES -- to a recall in 2022.
    Closing any interval that straddles any recall deletes about three years he
    earned. The narrow rule must not fire on this shape.
    """
    txns = [
        Transaction(dt.date(2019, 8, 6), "Los Angeles Dodgers activated LHP X."),
        Transaction(dt.date(2020, 9, 16), "Los Angeles Dodgers placed LHP X on the 10-day injured list."),
        Transaction(dt.date(2022, 5, 16), "Los Angeles Dodgers recalled LHP X from Oklahoma City Dodgers."),
    ]
    check("no claim, so no window", claim_then_recall_windows(txns) == [])
    intervals = build_global_active_intervals(txns, dt.date(2022, 9, 30))
    check("the long interval survives untouched", intervals[0][0] == dt.date(2019, 8, 6))


def test_the_claim_recall_rule_is_wired_into_the_interval_walk():
    """
    The end-to-end shape, confirmed against MLB's own rosters before shipping:
    Josh Walker 2025 scored agree=0, over=39, under=0 -- every day between the
    claim and the recall shows RM (optioned) on the roster while we credited
    all of them. Cole Sulser 2023 scored agree=0, over=59.
    """
    txns = [
        Transaction(dt.date(2025, 8, 19), "Phillies designated LHP X for assignment."),
        Transaction(dt.date(2025, 8, 21), "Orioles claimed LHP X off waivers from Phillies."),
        Transaction(dt.date(2025, 9, 29), "Orioles recalled LHP X from Norfolk Tides."),
    ]
    on = build_global_active_intervals(txns, dt.date(2025, 10, 1))
    off = build_global_active_intervals(txns, dt.date(2025, 10, 1),
                                        claim_recall_is_minors=False)
    check("the phantom stretch is gone", on == [(dt.date(2025, 9, 29), dt.date(2025, 10, 1))])
    check("and the switch still scores the old behaviour",
          off == [(dt.date(2025, 8, 21), dt.date(2025, 10, 1))])
    check("which is exactly the 39 days the roster disputed",
          (dt.date(2025, 9, 29) - dt.date(2025, 8, 21)).days == 39)


def test_the_rule_only_trims_the_interval_the_claim_itself_opened():
    """
    Guard against the broad version creeping back. An interval opened by
    something else that merely happens to contain a claim-recall window must
    not be truncated -- that is the Caleb Ferguson failure, worth three years.
    """
    txns = [
        Transaction(dt.date(2025, 4, 1), "Orioles selected the contract of LHP X."),
        Transaction(dt.date(2025, 8, 21), "Orioles claimed LHP Y off waivers from Phillies."),
        Transaction(dt.date(2025, 9, 29), "Orioles recalled LHP X from Norfolk Tides."),
    ]
    intervals = build_global_active_intervals(txns, dt.date(2025, 10, 1))
    check("an interval opened earlier keeps its start",
          intervals[0][0] == dt.date(2025, 4, 1))


def test_a_trade_straight_to_a_recall_is_also_a_minor_league_stint():
    """
    Finding #19 extended to trades, confirmed by roster truth first: Yohan
    Ramirez was traded to Cleveland 2022-05-16 and recalled from Columbus
    2022-06-23, and a one-day probe of Cleveland 2022 scored him
    agree=11, over=38, under=0 -- 38 phantom days, exactly the window.

    A trade and a waiver claim are the same situation: both put a player on the
    new club's 40-man without saying which roster he reported to. The recall
    answers it.
    """
    txns = [
        Transaction(dt.date(2022, 5, 13), "Seattle Mariners designated RHP X for assignment."),
        Transaction(dt.date(2022, 5, 16), "Seattle Mariners traded RHP X to Cleveland Guardians."),
        Transaction(dt.date(2022, 6, 23), "Cleveland Guardians recalled RHP X from Columbus Clippers."),
    ]
    check("the trade-to-recall window is found",
          minor_league_stint_windows(txns) == [(dt.date(2022, 5, 16), dt.date(2022, 6, 23))])
    intervals = build_global_active_intervals(txns, dt.date(2022, 10, 5))
    check("and the 38 phantom days are gone",
          intervals == [(dt.date(2022, 6, 23), dt.date(2022, 10, 5))])
    check("the window is exactly 38 days",
          (dt.date(2022, 6, 23) - dt.date(2022, 5, 16)).days == 38)


def test_the_stint_window_does_not_depend_on_row_order():
    """
    Ramirez was ACTIVATED and RECALLED from Indianapolis on the same day. A
    row-by-row walk fired or did not depending on which the API listed first --
    the sort-order dependence finding #10 was about, worth 160 days there.
    Grouping by date removes it: a recall anywhere in the next date's rows
    proves where he was, whatever shares that date.
    """
    base = [
        Transaction(dt.date(2022, 7, 8), "Cleveland Guardians traded RHP X to Pittsburgh Pirates."),
        Transaction(dt.date(2022, 8, 3), "Pittsburgh Pirates activated RHP X."),
        Transaction(dt.date(2022, 8, 3), "Pittsburgh Pirates recalled RHP X from Indianapolis Indians."),
    ]
    swapped = [base[0], base[2], base[1]]
    expected = [(dt.date(2022, 7, 8), dt.date(2022, 8, 3))]
    check("found with activated listed first", minor_league_stint_windows(base) == expected)
    check("and with recalled listed first", minor_league_stint_windows(swapped) == expected)


def test_a_trade_onto_an_active_roster_is_untouched():
    """
    The counter-case, and the reason the rule needs "nothing in between":
    David Huff was DFA'd, traded to the Yankees, and MLB's roster shows him
    ACTIVE from mid-June. No recall follows, so finding #16's reopening must
    survive intact.
    """
    txns = [
        Transaction(dt.date(2014, 6, 6), "San Francisco Giants designated LHP David Huff for assignment."),
        Transaction(dt.date(2014, 6, 11), "San Francisco Giants traded LHP David Huff to New York Yankees for cash."),
    ]
    check("no recall, so no window", minor_league_stint_windows(txns) == [])
    intervals = build_global_active_intervals(txns, dt.date(2014, 10, 1))
    check("the trade still reopens the clock", intervals[-1][0] == dt.date(2014, 6, 11))


# ===========================================================================
# CBA rulesets (config/cba/) -- see scripts/cba.py
# ===========================================================================


def _alt_ruleset(mutate):
    """A copy of the 2022 ruleset with one value changed, for A/B tests."""
    import copy
    import json as _json
    import pathlib as _pathlib

    path = _pathlib.Path(__file__).resolve().parents[1] / "config" / "cba" / "2022.json"
    data = copy.deepcopy(_json.loads(path.read_text()))
    mutate(data)
    return cba.Ruleset(data, "test", path)


def test_the_default_ruleset_reproduces_the_constants_the_engine_shipped_with():
    """
    The whole refactor is only safe if the default ruleset is exactly what was
    hardcoded before. These five numbers are the ones published output depends
    on; if any drifts, every figure on the site moves.
    """
    rules = cba.default()
    check("172 days per credited year", rules.require("service_time.days_per_credited_year") == 172)
    check("6.000 years to free agency", rules.require("free_agency.credited_years_required") == 6.0)
    check("3.000 years to arbitration", rules.require("arbitration.standard_years_required") == 3.0)
    check("Super Two class is 2.000-3.000", rules.require("arbitration.super_two.service_range_years") == [2.0, 3.0])
    check("186-day normal season span", rules.require("service_time.normal_season_span_days") == 186)
    check("2020 is the only prorated season", rules.shortened_seasons() == {2020: 186})
    check("module constants agree with the ruleset", FULL_YEAR_DAYS == 172 and SHORTENED_SEASONS == {2020})


def test_an_engine_function_computes_under_the_ruleset_it_is_given():
    """
    The brief's rule is computeEligibility(player, ruleset), never
    computeEligibility(player). Proof that the parameter is live rather than
    decorative: the same player under a ruleset with a different full-year
    length must produce a different figure.
    """
    season = SeasonWindow(2023, dt.date(2023, 3, 30), dt.date(2023, 10, 1))
    txns = [Transaction(dt.date(2023, 3, 30), "Team selected the contract of X")]

    default_result = compute_service_time(txns, [season])
    check("under the real agreement a full season is 1.000", default_result.formatted == "1.000")

    def shorten_year(d):
        d["service_time"]["days_per_credited_year"] = 100
        d["service_time"]["max_days_creditable_per_season"] = 100

    alt = compute_service_time(txns, [season], ruleset=_alt_ruleset(shorten_year))
    check("a 100-day credited year caps the same season at 100", alt.total_days == 100)
    check("and still formats as one full year", alt.formatted == "1.000")


def test_free_agency_and_arbitration_thresholds_come_from_the_ruleset():
    """A successor agreement moving free agency to 5 years must need no code."""
    seasons = [
        SeasonWindow(y, dt.date(y, 4, 1), dt.date(y, 9, 30)) for y in range(2018, 2023)
    ]
    txns = [Transaction(dt.date(2018, 4, 1), "Team selected the contract of X")]

    five_years = compute_service_time(txns, seasons)
    check("five credited years is not free agency under the 2022 rules", not five_years.is_free_agent_eligible)

    def fa_at_five(d):
        d["free_agency"]["credited_years_required"] = 5.0

    alt = compute_service_time(txns, seasons, ruleset=_alt_ruleset(fa_at_five))
    check("the same player IS a free agent under a 5-year ruleset", alt.is_free_agent_eligible)


def test_super_two_can_be_switched_off_by_a_ruleset():
    """
    Owners have proposed changes to arbitration. A successor agreement that
    abolishes Super Two must be expressible as config, not a code change.
    """
    seasons = [SeasonWindow(y, dt.date(y, 4, 1), dt.date(y, 9, 30)) for y in (2022, 2023)]
    seasons.append(SeasonWindow(2024, dt.date(2024, 4, 1), dt.date(2024, 7, 15)))
    txns = [Transaction(dt.date(2022, 4, 1), "Team selected the contract of X")]

    with_s2 = compute_service_time(txns, seasons)
    check("a 2.106 player is flagged Super Two under the 2022 rules", with_s2.is_super_two_candidate)

    def disable(d):
        d["arbitration"]["super_two"]["enabled"] = False

    alt = compute_service_time(txns, seasons, ruleset=_alt_ruleset(disable))
    check("disabled in config, the same player is not", not alt.is_super_two_candidate)
    check("and he is no longer arbitration eligible either", not alt.is_arbitration_eligible)


def test_the_2027_placeholder_refuses_to_compute():
    """
    A ruleset nobody has negotiated must not quietly credit anyone zero days.
    This is the same instinct as marking a tax state "unverified": a missing
    number is safe, a wrong one is not.
    """
    placeholder = cba.load("2027")
    check("the placeholder loads", placeholder.version == "2027")
    check("but reports itself unusable", not placeholder.usable)

    raised = False
    try:
        placeholder.require("free_agency.credited_years_required")
    except cba.UnusableRuleset:
        raised = True
    check("and raises rather than returning a value", raised)

    raised = False
    try:
        compute_service_time([], [SeasonWindow(2026, dt.date(2026, 4, 1), dt.date(2026, 9, 30))], ruleset=placeholder)
    except cba.UnusableRuleset:
        raised = True
    check("computing under it raises too", raised)


def test_a_value_nobody_has_checked_cannot_be_published_by_accident():
    """
    Option counts are in the ruleset for shape, but no human has read them off
    the CBA text. require_verified() is what stops them reaching a page.
    """
    rules = cba.default()
    check(
        "the five-option-per-season cap is present",
        rules.get("options.max_optional_assignments_per_season") == 5,
    )
    check(
        "and is marked unverified",
        rules.status("options.max_optional_assignments_per_season") == "unverified",
    )

    raised = False
    try:
        rules.require_verified("options.max_optional_assignments_per_season")
    except cba.UnverifiedRulesetValue:
        raised = True
    check("so require_verified refuses it", raised)
    check("while a verified value passes", rules.require_verified("service_time.days_per_credited_year") == 172)
    # The 20-day optional-assignment threshold WAS unverified; it is now the
    # one option value the engine consumes (the <20-day service credit), so it
    # had to be verified against the CBA text before it could drive a figure.
    check(
        "the 20-day threshold the engine consumes is now verified",
        rules.require_verified("options.days_to_burn_option_year") == 20,
    )
    check("only the remaining option shape-values are unverified today", rules.unverified_paths() == [
        "options.fourth_option_conditions",
        "options.max_optional_assignments_per_season",
    ])


def test_every_ruleset_value_carries_a_source():
    """
    The brief asks for a `sources` field. This is what makes it real: a value
    added later without provenance fails the suite rather than shipping
    silently. Structural containers and notes are exempt; leaves are not.
    """
    import json as _json
    import pathlib as _pathlib

    path = _pathlib.Path(__file__).resolve().parents[1] / "config" / "cba" / "2022.json"
    data = _json.loads(path.read_text())
    sources = data.get("sources", {})

    skip_top = {"version", "label", "effective_start", "effective_end", "sources", "usable", "status"}
    missing = []

    def walk(node, prefix):
        for key, value in node.items():
            if key.startswith("_") or key in ("note", "cutoff_note"):
                continue
            path_key = f"{prefix}.{key}" if prefix else key
            if path_key in sources:
                continue  # documented at this level; children are covered
            if isinstance(value, dict):
                walk(value, path_key)
            else:
                missing.append(path_key)

    for key, value in data.items():
        if key in skip_top:
            continue
        if isinstance(value, dict):
            walk(value, key)
        elif key not in sources:
            missing.append(key)

    check(f"every 2022 value has a sources entry (missing: {missing})", not missing)


def test_the_milestone_sentence_is_a_floor_derived_from_the_ruleset():
    """
    Added for the player-page indexing work: a plain-English sentence stating
    where a player stands against the CBA thresholds, so each page says more
    than a bare Y.DDD figure. Every threshold comes from config/cba/ the same
    way `_threshold_sentence()` already does -- this pins that it states a
    FLOOR (the fewest more seasons he could possibly need, at the maximum
    creditable rate) rather than inventing a projection the engine itself does
    not make. See docs/data/accrual_model.json for the real, probability-
    weighted version of this question, which lives only in the browser.
    """
    import write_player_pages as w

    check("0 days needed costs 0 seasons", w._min_seasons_to(0) == 0)
    check("a negative balance (already past) costs 0 seasons", w._min_seasons_to(-5) == 0)
    check("1 day still costs a whole season", w._min_seasons_to(1) == 1)
    check("exactly one season's worth costs 1", w._min_seasons_to(w.FULL_YEAR_DAYS) == 1)
    check("one day over a season rolls to 2", w._min_seasons_to(w.FULL_YEAR_DAYS + 1) == 2)

    fa = {"on_40_man": True, "service_time": "9.000", "service_days_total": 9 * w.FULL_YEAR_DAYS,
          "free_agent_eligible": True}
    check("already free-agency eligible says so and projects nothing",
          "already eligible for free agency" in w._milestone_sentence(fa))

    arb = {"on_40_man": True, "service_time": "3.010", "service_days_total": 3 * w.FULL_YEAR_DAYS + 10,
           "arbitration_eligible": True, "free_agent_eligible": False, "super_two_candidate": False}
    sentence = w._milestone_sentence(arb)
    check("an arbitration-eligible player is told what's left for free agency",
          "arbitration-eligible" in sentence and "free agency" in sentence)
    check("the free agency threshold in the sentence is the ruleset's own",
          w._yrs(w.FREE_AGENCY_YEARS) in sentence)

    s2 = dict(arb, super_two_candidate=True)
    check("a Super Two candidate is named as such",
          "Super Two track" in w._milestone_sentence(s2))

    pre_arb = {"on_40_man": True, "service_time": "1.050", "service_days_total": 1 * w.FULL_YEAR_DAYS + 50,
               "arbitration_eligible": False, "free_agent_eligible": False, "super_two_candidate": False}
    sentence = w._milestone_sentence(pre_arb)
    check("a pre-arbitration player is told what's left for arbitration",
          "arbitration eligibility" in sentence)
    check("the arbitration threshold in the sentence is the ruleset's own",
          w._yrs(w.ARBITRATION_YEARS) in sentence)

    retired_fa = {"on_40_man": False, "service_time": "9.000", "service_days_total": 9 * w.FULL_YEAR_DAYS,
                  "free_agent_eligible": True, "arbitration_eligible": True}
    check("a finished career is described as closed, not projected",
          "His career closed" in w._milestone_sentence(retired_fa))

    never_up = {"on_40_man": True, "service_time": "0.000", "service_days_total": 0}
    check("a player who has never accrued still gets a defensible sentence",
          "arbitration eligibility" in w._milestone_sentence(never_up))


def test_the_milestone_sentence_follows_the_ruleset_not_a_literal():
    """
    Positive control, same shape as test_the_explainer_prose_follows_the_ruleset_not_a_literal():
    a changed agreement must move the thresholds this sentence quotes.
    """
    import importlib
    import json as _json

    config_dir = pathlib.Path(__file__).resolve().parents[1] / "config" / "cba"
    base = _json.loads((config_dir / "2022.json").read_text())
    base["free_agency"]["credited_years_required"] = 5.0
    base["arbitration"]["standard_years_required"] = 2.0
    scratch = config_dir / "9998.json"
    scratch.write_text(_json.dumps(base))

    import write_player_pages

    real_default = cba.DEFAULT_VERSION
    try:
        cba.DEFAULT_VERSION = "9998"
        cba.load.cache_clear()
        importlib.reload(write_player_pages)
        pre_arb = {"on_40_man": True, "service_time": "1.050", "service_days_total": 222,
                   "arbitration_eligible": False, "free_agent_eligible": False,
                   "super_two_candidate": False}
        sentence = write_player_pages._milestone_sentence(pre_arb)
        check("a lowered arbitration threshold reaches the sentence", "2.000" in sentence)
        check("the old 3.000 threshold does not survive", "3.000" not in sentence)
    finally:
        scratch.unlink(missing_ok=True)
        cba.DEFAULT_VERSION = real_default
        cba.load.cache_clear()
        importlib.reload(write_player_pages)


def test_teammates_are_linked_but_never_a_retired_players_stale_club():
    """
    A player's page links sideways to a handful of his current roster mates,
    so a crawler following links has more than one path off each page.

    The dangerous case is an alumnus: his stored `team` is the LAST club he
    was with, which is stale by construction (see `_club_phrase()`). Looking
    up "teammates" by that name against today's roster would list men who
    were never on a roster with him at all -- the same category error already
    fixed once in the table's payload and in `_write_club_pages()`. The
    caller in write_player_pages() is responsible for passing `[]` for a
    non-rostered player; this pins that `_teammates_block()` itself also
    degrades safely if it is ever called some other way.
    """
    import write_player_pages as w

    roster = [
        {"id": 1, "name": "Me", "service_days_total": 100},
        {"id": 2, "name": "Teammate A", "service_days_total": 900},
        {"id": 3, "name": "Teammate B", "service_days_total": 50},
    ]
    block = w._teammates_block(roster[0], roster)
    check("a player never lists himself as his own teammate", ">Me<" not in block)
    check("real teammates are linked by filename, not full page_path",
          'href="2-teammate-a.html"' in block and 'href="3-teammate-b.html"' in block)
    check("the more tenured teammate is listed first",
          block.index("Teammate A") < block.index("Teammate B"))
    check("no service-time figure is printed next to a teammate's name "
          "(it would move every teammate's own lastmod on every accruing day)",
          "900" not in block and "50</" not in block and "50 " not in block)

    check("no teammates at all renders nothing", w._teammates_block(roster[0], []) == "")

    # The real guard against the stale-club trap lives in write_player_pages()
    # itself (teammates = [] unless on_40_man), not in this helper -- so this
    # only documents that a caller handing it zero teammates is always safe.
    alumnus = {"id": 9, "name": "Retired Guy", "on_40_man": False}
    check("an alumnus handed no teammates gets no section",
          w._teammates_block(alumnus, []) == "")


def test_key_players_on_a_club_page_are_grouped_by_threshold_not_by_day_count():
    """
    The club page highlights players by surfaces of ELIGIBILITY -- free
    agency, Super Two, standard arbitration -- rather than by raw day count.
    That is deliberate: a group's membership changes only when a threshold is
    actually crossed, a real and infrequent event, never on an ordinary
    accruing day. Ranking by day count instead would reorder the sentence on
    nearly every accruing day, which is exactly the daily-tick problem
    `_content_key()` exists to keep out of the sitemap -- see the fix to
    `_svc_cell()`'s own hashing in this same change.
    """
    import write_player_pages as w

    fa = {"id": 1, "name": "Zeta FA", "free_agent_eligible": True,
          "arbitration_eligible": True, "super_two_candidate": False}
    s2 = {"id": 2, "name": "Alpha S2", "free_agent_eligible": False,
          "arbitration_eligible": True, "super_two_candidate": True}
    arb = {"id": 3, "name": "Beta Arb", "free_agent_eligible": False,
           "arbitration_eligible": True, "super_two_candidate": False}
    pre = {"id": 4, "name": "Nobody Yet", "free_agent_eligible": False,
           "arbitration_eligible": False, "super_two_candidate": False}

    line = w._key_players_line([fa, s2, arb, pre])
    check("a free-agency-eligible player is named", "Zeta FA" in line)
    check("a Super Two candidate is named under its own heading", "Super Two track" in line)
    check("...and not double-counted under arbitration", line.count("Alpha S2") == 1)
    check("a standard-arbitration player is named", "Beta Arb" in line)
    check("a pre-arbitration player is not mentioned at all", "Nobody Yet" not in line)
    check("within a group, names are alphabetical, not by id or day count",
          line.index("Alpha S2") < line.index("Beta Arb"))

    check("a roster with nobody past pre-arb produces nothing",
          w._key_players_line([pre]) == "")


def test_the_svc_meter_split_figure_is_normalised_like_every_other_figure():
    """
    Found while checking that club/alumni lastmod values are sensible for
    this change: `_svc_cell()` (the meter used only on club and alumni listing
    pages) splits the Y.DDD figure across two <span>s so the years and the
    days can be styled separately -- which means `_FIGURE_RE`'s single
    \\d+\\.\\d{3} token never matches it. A single player's ordinary daily
    accrual therefore changed a CLUB page's content hash every day, which is
    the exact regression the whole `_content_key()` mechanism exists to
    prevent on player pages -- it had just never been re-checked against the
    listing pages that reuse this cell.
    """
    import write_player_pages as w

    p1 = {"id": 1, "name": "A", "position": "P", "service_days_total": 1532,
          "service_time": "8.156", "free_agent_eligible": False,
          "arbitration_eligible": True, "super_two_candidate": False}
    p2 = dict(p1, service_days_total=1533, service_time="8.157")

    club1 = w.render_club("Some Club", [p1], "2026-01-01T00:00:00+00:00")
    club2 = w.render_club("Some Club", [p2], "2026-01-01T00:00:00+00:00")
    check("a club page's hash is unmoved by one player's ordinary +1 day",
          w._content_key(club1) == w._content_key(club2))

    traded = dict(p1); traded["free_agent_eligible"] = True
    check("...but a real status change still moves it",
          w._content_key(club1) != w.render_club("Some Club", [traded], "2026-01-01T00:00:00+00:00")
          and w._content_key(club1) != w._content_key(w.render_club("Some Club", [traded], "2026-01-01T00:00:00+00:00")))


def test_the_homepage_carries_real_links_into_a_handful_of_player_pages():
    """
    docs/index.html renders its player table client-side from index.json, so
    a crawler that does not run JavaScript found no player link anywhere on
    the one page carrying the site's own inbound links -- every player page
    sat three hops from home with exactly one inbound link, from its club
    page. `_update_homepage_key_players()` rewrites one marked region of the
    otherwise hand-maintained homepage with a few real `<a href>` links,
    regenerated every run so they can never go stale between one run and the
    next.
    """
    import re
    import write_player_pages as w

    root = pathlib.Path(__file__).resolve().parents[1]
    index_path = root / "docs" / "index.html"
    original = index_path.read_text(encoding="utf-8")
    check("the homepage carries the generated-region markers",
          w._KEY_PLAYERS_START in original and w._KEY_PLAYERS_END in original)

    try:
        players = [
            {"id": 101, "name": "Famous Vet", "service_days_total": 3000, "on_40_man": True},
            {"id": 102, "name": "Bench Guy", "service_days_total": 5, "on_40_man": True},
        ]
        w._update_homepage_key_players(players)
        updated = index_path.read_text(encoding="utf-8")
        check("both picks appear as real hrefs", 'href="p/101-famous-vet.html"' in updated)
        check("...both of them", 'href="p/102-bench-guy.html"' in updated)
        check("the rest of the file is untouched",
              updated.replace(updated[updated.index(w._KEY_PLAYERS_START):
                                       updated.index(w._KEY_PLAYERS_END) + len(w._KEY_PLAYERS_END)], "X")
              == original.replace(original[original.index(w._KEY_PLAYERS_START):
                                            original.index(w._KEY_PLAYERS_END) + len(w._KEY_PLAYERS_END)], "X"))

        # Re-running must replace, not accumulate -- a second run with a
        # different pick should leave no trace of the first.
        w._update_homepage_key_players([{"id": 999, "name": "Someone Else",
                                          "service_days_total": 1, "on_40_man": True}])
        twice = index_path.read_text(encoding="utf-8")
        check("re-running replaces rather than appending",
              "Famous Vet" not in twice and "Someone Else" in twice)
        check("exactly one pair of markers survives", twice.count(w._KEY_PLAYERS_START) == 1)
    finally:
        index_path.write_text(original, encoding="utf-8")

    check("_pick_key_players only selects currently-rostered players",
          all(p.get("on_40_man") for p in w._pick_key_players(
              [{"id": 1, "name": "On", "service_days_total": 5, "on_40_man": True},
               {"id": 2, "name": "Off", "service_days_total": 9000, "on_40_man": False}])))


# ===========================================================================
# rules version 6: schedule-derived windows, the 20-day option rule, and ROY
# ===========================================================================


def test_a_season_window_starts_at_each_clubs_own_opener():
    """
    Service time is credited from a club's OWN first regular-season game.
    /seasons reports a single regularSeasonStartDate -- the earliest game
    anywhere, i.e. the overseas opener -- and applying it to all 30 clubs
    credited every non-overseas club a week of phantom days. The /schedule feed
    gives each club its own opener instead.
    """
    import fetch_mlb_data as mlb

    # The two Seoul clubs (119, 121) open a week early; four domestic clubs open
    # on the modal date 03-28, so the domestic opener is unambiguous.
    payload = {"dates": [
        {"date": "2024-03-20", "games": [{
            "gameType": "R", "status": {"detailedState": "Final"},
            "teams": {"home": {"team": {"id": 119}}, "away": {"team": {"id": 121}}},
        }]},
        {"date": "2024-03-28", "games": [
            {"gameType": "R", "status": {"detailedState": "Final"},
             "teams": {"home": {"team": {"id": 147}}, "away": {"team": {"id": 111}}}},
            {"gameType": "R", "status": {"detailedState": "Final"},
             "teams": {"home": {"team": {"id": 135}}, "away": {"team": {"id": 158}}}},
        ]},
        {"date": "2024-09-29", "games": [{
            "gameType": "R", "status": {"detailedState": "Final"},
            "teams": {"home": {"team": {"id": 147}}, "away": {"team": {"id": 111}}},
        }]},
    ]}
    sched = mlb._parse_season_schedule(2024, payload)
    check("the overseas club keeps its early opener", sched.club_openers[119] == dt.date(2024, 3, 20))
    check("a domestic club opens on its own day", sched.club_openers[147] == dt.date(2024, 3, 28))
    check(
        "the domestic (modal) opener is the one three clubs share, not the Seoul date",
        sched.domestic_opener == dt.date(2024, 3, 28),
    )

    # And end-to-end through the fetch layer: get_club_season_start reads the
    # club's own opener, the league window defaults to the domestic opener, and
    # an unknown club falls back to it.
    real_get = mlb._get
    mlb.get_season_window.cache_clear()
    mlb._SEASON_SCHEDULE.clear()
    try:
        mlb._get = lambda path, params=None: payload
        check("get_club_season_start reads the overseas club's opener", mlb.get_club_season_start(2024, 119) == dt.date(2024, 3, 20))
        check("...and a domestic club's own opener", mlb.get_club_season_start(2024, 147) == dt.date(2024, 3, 28))
        check("an unknown club falls back to the domestic opener", mlb.get_club_season_start(2024, 999) == dt.date(2024, 3, 28))
        check("the league window is (domestic opener, last game)", mlb.get_season_window(2024) == (dt.date(2024, 3, 28), dt.date(2024, 9, 29)))
    finally:
        mlb._get = real_get
        mlb.get_season_window.cache_clear()
        mlb._SEASON_SCHEDULE.clear()


def test_a_season_ends_on_the_last_game_actually_played():
    """
    The 2023 /seasons end was 2023-10-02 because of a makeup game scheduled
    that day and later cancelled; the last game actually played was 10-01. The
    end is the latest game NOT cancelled/postponed -- which is also why the
    real 2024-09-30 Mets-Braves makeup doubleheader keeps 2024 ending on 09-30,
    with no blanket rule against makeups.
    """
    import fetch_mlb_data as mlb

    completed_early_then_cancelled_makeup = {"dates": [
        {"date": "2023-09-28", "games": [{
            "gameType": "R", "status": {"detailedState": "Completed Early"},
            "teams": {"home": {"team": {"id": 146}}, "away": {"team": {"id": 121}}},
        }]},
        {"date": "2023-10-01", "games": [{
            "gameType": "R", "status": {"detailedState": "Final"},
            "teams": {"home": {"team": {"id": 146}}, "away": {"team": {"id": 121}}},
        }]},
        {"date": "2023-10-02", "games": [{
            "gameType": "R", "status": {"detailedState": "Cancelled"},
            "teams": {"home": {"team": {"id": 146}}, "away": {"team": {"id": 121}}},
        }]},
    ]}
    sched = mlb._parse_season_schedule(2023, completed_early_then_cancelled_makeup)
    check("a cancelled makeup does not extend the season", sched.end == dt.date(2023, 10, 1))

    makeup_doubleheader_played = {"dates": [
        {"date": "2024-06-01", "games": [{
            "gameType": "R", "status": {"detailedState": "Postponed"},
            "teams": {"home": {"team": {"id": 144}}, "away": {"team": {"id": 121}}},
        }]},
        {"date": "2024-09-28", "games": [{
            "gameType": "R", "status": {"detailedState": "Final"},
            "teams": {"home": {"team": {"id": 144}}, "away": {"team": {"id": 121}}},
        }]},
        # The real 2024-09-30 Mets-Braves makeup doubleheader: two games, both
        # actually played, so they count.
        {"date": "2024-09-30", "games": [
            {"gameType": "R", "status": {"detailedState": "Final"},
             "teams": {"home": {"team": {"id": 144}}, "away": {"team": {"id": 121}}}},
            {"gameType": "R", "status": {"detailedState": "Final"},
             "teams": {"home": {"team": {"id": 144}}, "away": {"team": {"id": 121}}}},
        ]},
    ]}
    sched2 = mlb._parse_season_schedule(2024, makeup_doubleheader_played)
    check("a makeup doubleheader that was played does count", sched2.end == dt.date(2024, 9, 30))


def test_a_resumed_game_relisted_under_a_later_date_does_not_extend_the_season():
    """
    LIVE BUG, caught 2026-10-29 running against the real statsapi feed:
    get_season_window(2023) still returned 2023-10-02 after the
    cancelled-makeup fix above. The real cause is a different shape than a
    cancelled makeup: the Marlins@Mets game suspended by rain on 2023-09-28
    ("Completed Early") is RELISTED by the /schedule feed under the date it
    finished resuming, 2023-10-02, with `resumedFrom` pointing back to when it
    actually started. Both rows share the same gamePk and the same result --
    it is one game, not two -- but the first version of this parser keyed the
    played date off the surrounding date BUCKET, so the 10-02 relisting read
    as a real game on 10-02 and pulled the season end a day past the real
    last day played, 2023-10-01.

    These fixtures are trimmed real /schedule payloads (tests/fixtures/), not
    synthetic ones, specifically so this does not get re-broken by a change
    that only synthetic tests would pass. They also carry a genuine
    postponed-and-rescheduled game (716393: a Postponed stub on 2023-09-29,
    replayed Final on 2023-09-30) as a decoy -- that one must still count on
    its real makeup date, which is what tells a resumption apart from an
    ordinary rainout reschedule (see the comment on _parse_season_schedule).
    """
    import fetch_mlb_data as mlb

    fixtures = pathlib.Path(__file__).resolve().parent / "fixtures"
    payload_2023 = json.loads((fixtures / "schedule_2023_end.json").read_text())
    payload_2024 = json.loads((fixtures / "schedule_2024_end.json").read_text())

    sched_2023 = mlb._parse_season_schedule(2023, payload_2023)
    check(
        "the resumed game's duplicate relisting does not push the season "
        f"past its real last day (got {sched_2023.end})",
        sched_2023.end == dt.date(2023, 10, 1),
    )

    sched_2024 = mlb._parse_season_schedule(2024, payload_2024)
    check(
        "2024 still ends on the real Mets-Braves makeup doubleheader date "
        f"(got {sched_2024.end})",
        sched_2024.end == dt.date(2024, 9, 30),
    )


def test_a_short_option_under_twenty_days_counts_as_service():
    """
    The 20-day rule: a player optioned for fewer than 20 days in a season is
    credited those days as Major League service (the Brett Baty case). A 10-day
    option inside an otherwise-full window is credited back in full.
    """
    season = SeasonWindow(2023, dt.date(2023, 4, 1), dt.date(2023, 4, 30))  # 30 days
    txns = [
        Transaction(dt.date(2023, 4, 1), "Team selected the contract of X"),
        Transaction(dt.date(2023, 4, 10), "Team optioned X to Triple-A"),
        Transaction(dt.date(2023, 4, 20), "Team recalled X from Triple-A"),
    ]
    result = compute_service_time(txns, [season], horizon_end=season.end)
    # On-roster days are 9 + 11 = 20; the 10-day option gap is under 20, so it
    # is credited back, giving the full 30-day window.
    check(
        "a 10-day option is credited back as service",
        result.by_season[2023]["credited_days"] == 30,
    )
    check(
        "the credited gap is reflected in the raw day count",
        result.by_season[2023]["raw_active_days"] == 30,
    )


def test_an_option_of_twenty_days_or_more_is_not_credited():
    """The threshold is strict: 20 days or more does not count as service."""
    season = SeasonWindow(2023, dt.date(2023, 4, 1), dt.date(2023, 5, 31))
    txns = [
        Transaction(dt.date(2023, 4, 1), "Team selected the contract of X"),
        Transaction(dt.date(2023, 4, 11), "Team optioned X to Triple-A"),
        Transaction(dt.date(2023, 5, 1), "Team recalled X from Triple-A"),  # 20-day gap
    ]
    result = compute_service_time(txns, [season], horizon_end=season.end)
    # On-roster: Apr 1-10 (10) + May 1-31 (31) = 41. The 20-day gap stays off.
    check(
        "a 20-day option is NOT credited back",
        result.by_season[2023]["credited_days"] == 41,
    )


def test_the_option_credit_is_capped_at_the_full_year():
    """A short option on an otherwise full season cannot push past 172."""
    season = SeasonWindow(2023, dt.date(2023, 3, 30), dt.date(2023, 10, 1))
    window_len = (season.end - season.start).days + 1
    txns = [
        Transaction(dt.date(2023, 3, 30), "Team selected the contract of X"),
        Transaction(dt.date(2023, 6, 1), "Team optioned X to Triple-A"),
        Transaction(dt.date(2023, 6, 10), "Team recalled X from Triple-A"),  # 9-day gap
    ]
    result = compute_service_time(txns, [season], horizon_end=season.end)
    check(
        "the 9-day gap is credited back into the raw days",
        result.by_season[2023]["raw_active_days"] == window_len,
    )
    check(
        "...but the season is still capped at a full year",
        result.by_season[2023]["credited_days"] == 172,
    )


def test_a_spring_option_counts_from_the_clubs_opening_day():
    """
    An option made in spring training (before the opener) counts from Opening
    Day: the player was on the 40-man, optioned, and recalled during the season.
    """
    season = SeasonWindow(2023, dt.date(2023, 4, 1), dt.date(2023, 4, 30))
    txns = [
        Transaction(dt.date(2023, 3, 25), "Team optioned X to Triple-A"),  # spring
        Transaction(dt.date(2023, 4, 11), "Team recalled X from Triple-A"),
    ]
    result = compute_service_time(txns, [season], horizon_end=season.end)
    # On-roster Apr 11-30 = 20 days; the spring option is counted from Opening
    # Day (Apr 1 -> Apr 11 = 10 days), under 20, so credited back -> 30.
    check(
        "a spring option is counted from Opening Day and credited back",
        result.by_season[2023]["credited_days"] == 30,
    )


def test_an_option_never_recalled_counts_through_season_end():
    """An option with no recall that season runs its days to the end of the
    season -- so a late short option still qualifies."""
    season = SeasonWindow(2023, dt.date(2023, 4, 1), dt.date(2023, 4, 30))
    txns = [
        Transaction(dt.date(2023, 4, 1), "Team selected the contract of X"),
        Transaction(dt.date(2023, 4, 20), "Team optioned X to Triple-A"),  # 11 days left
    ]
    result = compute_service_time(txns, [season], horizon_end=season.end)
    # On-roster Apr 1-19 = 19 days; the option runs Apr 20-30 = 11 days, under
    # 20, credited back -> the full 30-day window.
    check(
        "an option never recalled, under 20 days, is credited to season end",
        result.by_season[2023]["credited_days"] == 30,
    )


def test_a_dfa_gap_is_not_an_optional_assignment():
    """Only an option is credited back -- a DFA/release gap is not, even when
    it is short."""
    season = SeasonWindow(2023, dt.date(2023, 4, 1), dt.date(2023, 4, 30))
    txns = [
        Transaction(dt.date(2023, 4, 1), "Team selected the contract of X"),
        Transaction(dt.date(2023, 4, 10), "Team designated X for assignment"),
        Transaction(dt.date(2023, 4, 20), "Team selected the contract of X"),
    ]
    result = compute_service_time(txns, [season], horizon_end=season.end)
    # 9 + 11 = 20 on-roster days; the 10-day DFA gap is NOT an option, so it is
    # not credited.
    check(
        "a short DFA gap is not credited back",
        result.by_season[2023]["credited_days"] == 20,
    )


def test_a_roy_full_year_finish_raises_a_short_season():
    """
    The Prospect Promotion Incentive: a top-two Rookie of the Year finish is
    credited a full year for that season, even on a short day count. The
    full-year value comes from the ruleset, not a literal.
    """
    season = SeasonWindow(2024, dt.date(2024, 7, 1), dt.date(2024, 10, 1))  # ~93 days
    txns = [Transaction(dt.date(2024, 7, 1), "Team selected the contract of X")]

    short = compute_service_time(txns, [season], horizon_end=season.end)
    check("without the award, a July call-up is a partial year", short.by_season[2024]["credited_days"] < 172)

    full = compute_service_time(
        txns, [season], horizon_end=season.end, roy_full_year_seasons={2024}
    )
    check("a ROY full-year finish raises the season to 172", full.by_season[2024]["credited_days"] == 172)
    check("...and the formatted figure is a full year", full.formatted == "1.000")


def test_roy_full_year_only_raises_never_lowers():
    """The award can only raise a figure -- a player who already has a full
    season is unaffected."""
    season = SeasonWindow(2024, dt.date(2024, 3, 28), dt.date(2024, 9, 29))
    txns = [Transaction(dt.date(2024, 3, 28), "Team selected the contract of X")]
    full = compute_service_time(
        txns, [season], horizon_end=season.end, roy_full_year_seasons={2024}
    )
    check("an already-full season stays at the cap", full.by_season[2024]["credited_days"] == 172)


def test_the_roy_loader_rejects_an_unsourced_or_malformed_entry():
    """Every ROY entry must carry a source and a 1-or-2 finish, the same way an
    unverified ruleset value is refused -- a guessed full year is a number
    nobody checked wearing the site's authority."""
    import tempfile

    def write(obj):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(obj, fh)
        fh.close()
        return fh.name

    good = write({"awards": [{
        "mlbam_id": 694973, "name": "Paul Skenes", "season": 2024,
        "league": "NL", "finish": 1, "source": "https://example.com/skenes",
    }]})
    check("a well-formed, sourced entry loads", len(cba.load_roy_full_year_awards(good)) == 1)

    no_source = write({"awards": [{
        "mlbam_id": 1, "name": "X", "season": 2024, "league": "NL", "finish": 1,
    }]})
    raised = False
    try:
        cba.load_roy_full_year_awards(no_source)
    except cba.RoyAwardError:
        raised = True
    check("an entry with no source is refused", raised)

    bad_finish = write({"awards": [{
        "mlbam_id": 1, "name": "X", "season": 2024, "league": "NL",
        "finish": 3, "source": "https://example.com/x",
    }]})
    raised = False
    try:
        cba.load_roy_full_year_awards(bad_finish)
    except cba.RoyAwardError:
        raised = True
    check("a third-place finish does not earn the full year", raised)


def test_the_roy_list_is_not_treated_as_a_ruleset_version():
    """The ROY list lives in config/cba/ beside the rulesets but is not one, so
    ruleset discovery must skip it."""
    check("roy_full_year_awards is not a ruleset version", "roy_full_year_awards" not in cba.available_versions())
    check("the real rulesets are still discovered", "2022" in cba.available_versions())


def test_a_traded_players_start_is_his_opening_day_club_not_the_latest_opener():
    """
    A traded player's season must start on the opener of the club he was with
    at Opening Day -- the earliest club of the season by transaction date --
    not the latest of his clubs' openers. So a player who opened with an
    overseas club and was traded away keeps the early start, while one who
    opened with a domestic club and was traded TO an overseas-opener club does
    not inherit its early date.
    """
    import fetch_mlb_data as mlb
    import update_service_time as u

    # A synthetic season: club 119 opens early (Seoul-style), club 147 opens a
    # week later on the modal date.
    payload = {"dates": [
        {"date": "2024-03-20", "games": [{
            "gameType": "R", "status": {"detailedState": "Final"},
            "teams": {"home": {"team": {"id": 119}}, "away": {"team": {"id": 135}}}}]},
        {"date": "2024-03-28", "games": [{
            "gameType": "R", "status": {"detailedState": "Final"},
            "teams": {"home": {"team": {"id": 147}}, "away": {"team": {"id": 111}}}}]},
        {"date": "2024-03-28", "games": [{
            "gameType": "R", "status": {"detailedState": "Final"},
            "teams": {"home": {"team": {"id": 135}}, "away": {"team": {"id": 111}}}}]},
        {"date": "2024-09-29", "games": [{
            "gameType": "R", "status": {"detailedState": "Final"},
            "teams": {"home": {"team": {"id": 147}}, "away": {"team": {"id": 119}}}}]},
    ]}
    real_get = mlb._get
    mlb.get_season_window.cache_clear()
    mlb._SEASON_SCHEDULE.clear()
    try:
        mlb._get = lambda path, params=None: payload
        mlb_ids = {119, 135, 147, 111}

        # Case 1: opened with the overseas club (119), traded away to 147 in July.
        traded_away = [
            {"date": "2024-05-01", "description": "119 optioned X", "team_id": 119},
            {"date": "2024-07-01", "description": "Club traded X", "from_team_id": 119, "to_team_id": 147},
        ]
        od1 = u._opening_day_clubs(traded_away, mlb_ids)
        check("the Opening Day club is the origin of the earliest transaction", od1.get(2024) == 119)
        start1 = u._opening_day_for(2024, od1.get(2024), [119, 147])
        check("a player who opened overseas keeps the early start", start1 == dt.date(2024, 3, 20))

        # Case 2: opened with a domestic club (147), traded TO the overseas-opener
        # club (119) later -- he never played the overseas series.
        traded_to_overseas = [
            {"date": "2024-05-01", "description": "147 optioned X", "team_id": 147},
            {"date": "2024-07-01", "description": "Club traded X", "from_team_id": 147, "to_team_id": 119},
        ]
        od2 = u._opening_day_clubs(traded_to_overseas, mlb_ids)
        check("his Opening Day club is the domestic one he started with", od2.get(2024) == 147)
        start2 = u._opening_day_for(2024, od2.get(2024), [147, 119])
        check("he does NOT inherit the early overseas date", start2 == dt.date(2024, 3, 28))

        # Fallback: no transactions -> the conservative latest opener.
        start3 = u._opening_day_for(2024, None, [119, 147])
        check("with no Opening Day club known, the latest opener is used", start3 == dt.date(2024, 3, 28))
    finally:
        mlb._get = real_get
        mlb.get_season_window.cache_clear()
        mlb._SEASON_SCHEDULE.clear()


# ---------------------------------------------------------------------------
# Google Analytics, AdSense, the data-source protections, and the two new
# hand-written pages (about.html, privacy.html). See CLAUDE.md -- these ship
# the same two ids (GA_MEASUREMENT_ID, ADSENSE_CLIENT_ID) on every page, so
# the thing worth pinning is "every page carries it exactly once", the same
# shape as test_the_site_header_is_the_same_on_every_page().
# ---------------------------------------------------------------------------

_HAND_WRITTEN_HTML_PAGES = [
    "index.html", "taxes.html", "contract.html", "neutrality.html",
    "about.html", "privacy.html", "lockout.html",
]


def _generated_sample_pages():
    """One rendered string per generated template, same fixtures style as
    test_the_site_header_is_the_same_on_every_page() and
    test_a_debuted_player_is_never_described_as_never_having_been_up()."""
    import write_player_pages as w

    player = {
        "id": 1, "name": "Test Debutant", "mlb_debut": "2026-08-29",
        "on_40_man": True, "service_days_total": 0, "service_time": "0.000",
        "team": "Some Club", "seasons": [], "position": "P",
    }
    pages = {
        "render_club_index": w.render_club_index({"Atlanta Braves": []}, "2026-01-01"),
        "render_club": w.render_club("Atlanta Braves", [], "2026-01-01"),
        "render (player)": w.render(player, {}, "2026-01-01"),
        "_alumni_shell": w._alumni_shell(
            "Title", "Desc", "https://bigleagueservicetime.com/alumni/",
            w._site_header(None), "", "Heading", "Sub", "Body", "2026-01-01", [],
        ),
        "render_explainer": w.render_explainer("2026-01-01"),
        "render_arbitration_eligible": w.render_arbitration_eligible([], "2026-01-01", None, {}),
        "render_super_two": w.render_super_two([], {}, "2026-01-01", None),
    }
    return pages


def test_every_page_carries_exactly_one_ga_tag_and_one_adsense_block():
    """
    Measurement ID G-G04P969TLX and AdSense publisher ca-pub-8748967682918425
    each have to land on every page once -- missing one makes a page's
    traffic or ad delivery invisible, and a duplicate is a page that double-
    fires analytics events. Generated pages share one constant
    (write_player_pages.HEAD_TAGS); the six hand-written pages each carry
    their own literal copy, which is exactly the shape that let the
    Cloudflare token drift once before (see ANALYTICS's docstring) -- so
    every hand-written copy is checked too, not just the generated ones.
    """
    ROOT = pathlib.Path(__file__).resolve().parents[1]
    docs = ROOT / "docs"

    ga_script = "googletagmanager.com/gtag/js?id=G-G04P969TLX"
    ga_config = "gtag('config', 'G-G04P969TLX')"
    adsense_script = "pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-8748967682918425"
    adsense_meta = 'google-adsense-account" content="ca-pub-8748967682918425'

    for name in _HAND_WRITTEN_HTML_PAGES:
        html = (docs / name).read_text(encoding="utf-8")
        check(f"{name}: exactly one GA script tag", html.count(ga_script) == 1)
        check(f"{name}: exactly one GA config call", html.count(ga_config) == 1)
        check(f"{name}: exactly one AdSense script tag", html.count(adsense_script) == 1)
        check(f"{name}: exactly one AdSense meta tag", html.count(adsense_meta) == 1)

    import write_player_pages as w
    w._write_404()
    page404 = (docs / "404.html").read_text(encoding="utf-8")
    generated = _generated_sample_pages()
    generated["404"] = page404

    for name, html in generated.items():
        check(f"{name}: exactly one GA script tag", html.count(ga_script) == 1)
        check(f"{name}: exactly one GA config call", html.count(ga_config) == 1)
        check(f"{name}: exactly one AdSense script tag", html.count(adsense_script) == 1)
        check(f"{name}: exactly one AdSense meta tag", html.count(adsense_meta) == 1)


def test_ads_txt_is_exact():
    """
    ads.txt has one job: tell Google's crawler this site sells inventory
    directly. A typo here silently breaks ad delivery with no error anyone
    would see, so the content is pinned exactly rather than loosely matched.
    """
    ROOT = pathlib.Path(__file__).resolve().parents[1]
    text = (ROOT / "docs" / "ads.txt").read_text(encoding="utf-8")
    check(
        "ads.txt is exactly the one required line",
        text.strip() == "google.com, pub-8748967682918425, DIRECT, f08c47fec0942fa0",
    )
    check("ads.txt is not swallowed by Jekyll", (ROOT / "docs" / ".nojekyll").exists())


def test_every_page_carries_the_required_footer_disclaimer_and_links():
    """
    The data-source review requires this exact sentence (not a paraphrase)
    plus reachable About/Privacy/contact links on every page -- see
    write_player_pages._site_footer() for the generated side.
    """
    ROOT = pathlib.Path(__file__).resolve().parents[1]
    docs = ROOT / "docs"
    required = (
        "Not affiliated with or endorsed by Major League Baseball or any "
        "team. Service time figures are our own calculations from public "
        "data."
    )
    contact = "contact@bigleagueservicetime.com"

    import write_player_pages as w
    w._write_404()
    pages = _generated_sample_pages()
    pages["404"] = (docs / "404.html").read_text(encoding="utf-8")
    for name in _HAND_WRITTEN_HTML_PAGES:
        pages[name] = (docs / name).read_text(encoding="utf-8")

    for name, html in pages.items():
        normalized = " ".join(html.split())
        check(f"{name}: carries the required disclaimer sentence", required in normalized)
        check(f"{name}: links to About", "about.html" in html)
        check(f"{name}: links to Privacy", "privacy.html" in html)
        check(f"{name}: shows the contact address", contact in html)


def test_the_homepage_puts_numbers_before_the_long_explanation():
    """
    Job 12b: a phone visitor used to scroll past three paragraphs before
    reaching a single number. The one-line .home-lede now sits right under
    the shared header, the stat tiles and search come before the long
    explanation, and the long explanation itself is collapsed into a
    <details> so a reader sees the table, not a wall of text, by default.

    Also checks the banned phrase Braxton asked removed is gone from the
    site entirely, not just reworded on this one page.
    """
    ROOT = pathlib.Path(__file__).resolve().parents[1]
    index_html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")

    check("the homepage carries a one-line lede", '<p class="home-lede">' in index_html)
    check("the homepage links to the service-time explainer",
          'href="service-time.html"' in index_html)
    check("the long homepage explainer is gone",
          "intro-details" not in index_html and "More about service time" not in index_html)

    lede_pos = index_html.index('class="home-lede"')
    tiles_pos = index_html.index('class="stat-tiles"')
    controls_pos = index_html.index('class="controls"')
    table_pos = index_html.index('class="table-wrap"')
    check(
        "the lede comes before the tiles, search and table",
        lede_pos < tiles_pos < controls_pos < table_pos,
    )

    banned = "almost nothing to do with how well he plays"
    for path in (ROOT / "docs").rglob("*.html"):
        check(f"{path.relative_to(ROOT)}: never carries the retired phrase",
              banned not in path.read_text(encoding="utf-8"))
    check("app.js never carries the retired phrase either",
          banned not in (ROOT / "docs" / "app.js").read_text(encoding="utf-8"))


def test_the_five_pages_carry_an_original_intro():
    """
    The long "what is service time" essays used to open the homepage, the
    club directory, the arbitration list and the Super Two page. Those
    concepts now live once, on service-time.html. Each other page keeps one
    sentence about what it lists and a link to that explainer.
    """
    ROOT = pathlib.Path(__file__).resolve().parents[1]
    index_html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    check(
        "homepage says what it lists and links to the explainer",
        "Every MLB player's service time" in index_html
        and 'href="service-time.html"' in index_html,
    )
    check(
        "homepage no longer opens with the long essay",
        "clock that governs a player" not in index_html,
    )

    import write_player_pages as w
    club_index = " ".join(w.render_club_index({"Atlanta Braves": []}, "2026-01-01").split())
    check(
        "/t/ says what it lists and links to the explainer",
        "Every 40-man roster" in club_index
        and "service-time.html" in club_index
        and "how well he plays" not in club_index,
    )

    explainer = w.render_explainer("2026-01-01")
    check(
        "service-time.html opens with a short intro that says what it decides",
        "It decides when he reaches" in explainer
        and "milestones below are the ones that matter most" not in explainer,
    )
    check(
        "the explainer has a section for each concept",
        all(anchor in explainer for anchor in (
            'id="what-is-service-time"', 'id="arbitration"',
            'id="free-agency"', 'id="super-two"',
        )),
    )

    arb = " ".join(w.render_arbitration_eligible([], "2026-01-01", None, {}).split())
    check(
        "arbitration-eligible.html lists the players and links to the explainer",
        "arbitration-eligible this winter" in arb
        and "service-time.html#arbitration" in arb
        and "becomes eligible for salary arbitration" not in arb,
    )

    s2 = w.render_super_two([], {}, "2026-01-01", None)
    check(
        "super-two.html lists the class and links to the explainer",
        "Who is in the class right now" in s2
        and "service-time.html#super-two" in s2,
    )


def test_about_page_stays_high_level():
    """
    about.html is deliberately high-level: no formulas, no rule logic, no
    named API endpoints, and "public data" rather than "public MLB data"
    (CLAUDE.md, Hard rule 4's spirit extended to not over-specifying the
    source). privacy.html is allowed to be detailed about cookies, so only
    about.html is checked here.
    """
    import re

    ROOT = pathlib.Path(__file__).resolve().parents[1]
    about = (ROOT / "docs" / "about.html").read_text(encoding="utf-8")

    check("about.html never names the API client", "statsapi" not in about.lower())
    check("about.html never states a day/year threshold as a literal",
          not re.search(r"\b172\b", about))
    check("about.html never says the word API",
          not re.search(r"\bAPI\b", about, re.IGNORECASE))
    check('about.html says "public data", never "public MLB data"',
          "public data" in about and "public mlb data" not in about.lower())
    check("about.html carries the contact address",
          "contact@bigleagueservicetime.com" in about)


def test_no_page_in_docs_links_or_names_the_raw_statsapi_endpoint():
    """
    Hard rule: no page links raw API JSON. statsapi.mlb.com is fine to name
    as prose provenance (README.md, service-time.html's sourcing section),
    but no `<a href>` anywhere in docs/ may point AT it, and no page may
    quote a transaction's raw MLB description text -- see
    write_player_pages._season_rows(), which shows only year/club/days/our
    own source label, never the feed's wording.
    """
    import re

    ROOT = pathlib.Path(__file__).resolve().parents[1]
    docs = ROOT / "docs"

    for path in docs.rglob("*.html"):
        html = path.read_text(encoding="utf-8")
        for href in re.findall(r'href=["\']([^"\']+)["\']', html):
            check(
                f"{path.relative_to(ROOT)}: no link points at statsapi.mlb.com",
                "statsapi.mlb.com" not in href,
            )


def test_fetchers_send_an_honest_user_agent_and_use_the_shared_pause():
    """
    Every MLB Stats API call should identify the site (an honest User-Agent)
    and wait between requests through one shared, configurable helper --
    fetch_schedules.py used to call requests.get() directly, bypassing both.
    """
    import fetch_mlb_data as mlb

    check(
        "the shared session sends the documented User-Agent",
        mlb._session.headers.get("User-Agent")
        == "bigleagueservicetime.com (contact@bigleagueservicetime.com)",
    )
    check("USER_AGENT constant matches what the session actually sends",
          mlb.USER_AGENT == mlb._session.headers.get("User-Agent"))

    old = os.environ.get("MLB_FETCH_PAUSE_SECONDS")
    try:
        os.environ["MLB_FETCH_PAUSE_SECONDS"] = "2.5"
        check("the pause is configurable via the environment",
              mlb.polite_delay_seconds() == 2.5)

        slept = []
        real_sleep = mlb.time.sleep
        mlb.time.sleep = lambda s: slept.append(s)  # a fake opener for time.sleep
        try:
            mlb.polite_pause()
        finally:
            mlb.time.sleep = real_sleep
        check("polite_pause() sleeps exactly the configured amount", slept == [2.5])

        os.environ["MLB_FETCH_PAUSE_SECONDS"] = "not-a-number"
        check("a non-numeric override falls back to the default rather than crashing",
              mlb.polite_delay_seconds() == mlb.DEFAULT_POLITE_DELAY_SECONDS)
    finally:
        if old is None:
            os.environ.pop("MLB_FETCH_PAUSE_SECONDS", None)
        else:
            os.environ["MLB_FETCH_PAUSE_SECONDS"] = old

    import inspect
    import fetch_schedules as fs
    src = inspect.getsource(fs.fetch_season)
    check(
        "fetch_schedules.py's one direct caller goes through the shared "
        "session (and so sends the same User-Agent), not a bare requests.get()",
        "mlb._session.get" in src and "mlb.requests.get" not in src,
    )
    check("fetch_schedules.py uses the shared pause helper",
          "mlb.polite_pause()" in src)


def test_mlb_fetch_enabled_false_skips_the_api_and_still_builds():
    """
    The backup switch. MLB_FETCH_ENABLED=false must skip every MLB Stats API
    call in the daily job and rebuild the published site from the stored
    database only -- the same no-network path --recompute-derived takes,
    reached without a manual flag. See README.md, "Turning off live fetching".
    """
    import update_service_time as u

    old = os.environ.get("MLB_FETCH_ENABLED")
    try:
        for value, enabled in (("false", False), ("0", False), ("no", False),
                                ("true", True), ("", True)):
            os.environ["MLB_FETCH_ENABLED"] = value
            check(f"MLB_FETCH_ENABLED={value!r} reads as enabled={enabled}",
                  u.mlb_fetch_enabled() is enabled)
        os.environ.pop("MLB_FETCH_ENABLED", None)
        check("unset defaults to enabled", u.mlb_fetch_enabled() is True)
    finally:
        if old is None:
            os.environ.pop("MLB_FETCH_ENABLED", None)
        else:
            os.environ["MLB_FETCH_ENABLED"] = old

    fetch_calls = []
    write_calls = []
    real_fetch = u.mlb.get_all_40man_players
    real_write = u._write_outputs
    real_load = u.load_existing_db
    real_argv = sys.argv
    u.mlb.get_all_40man_players = lambda *a, **k: (fetch_calls.append(1), [])[1]
    u._write_outputs = lambda db: write_calls.append(1)
    u.load_existing_db = lambda: {}
    try:
        os.environ["MLB_FETCH_ENABLED"] = "false"
        sys.argv = ["update_service_time.py"]
        u.main()
        check("no roster fetch happens when the switch is off", fetch_calls == [])
        check("the site is still rebuilt from the stored database", write_calls == [1])
    finally:
        u.mlb.get_all_40man_players = real_fetch
        u._write_outputs = real_write
        u.load_existing_db = real_load
        sys.argv = real_argv
        if old is None:
            os.environ.pop("MLB_FETCH_ENABLED", None)
        else:
            os.environ["MLB_FETCH_ENABLED"] = old


# --- rules v7: restricted list, DFA clock, exceptions, stale cache ----------

def _can_reach_statsapi() -> bool:
    """True if this process can open a TCP connection to statsapi.mlb.com:443.

    The player-level integration checks below need the real /schedule feed for
    season windows. CI runs offline, where they would otherwise estimate the
    window and assert against a guess; so they are skipped unless the feed is
    actually reachable (as it is on the box that computed these figures).
    """
    import socket
    try:
        socket.create_connection(("statsapi.mlb.com", 443), timeout=5).close()
        return True
    except OSError:
        return False


def test_a_restricted_list_placement_stops_the_clock_by_default():
    """Rules v7. A restricted-list player is not paid and earns no Major League
    service (MLB Rule 2(c)(6)), so a 'placed ... on the restricted list' row is
    a STOP, and 'activated ... from the restricted list' is a START that reopens
    the clock. Before v7 the restricted list was a non-stopping placement that
    kept accruing -- the Jose Alvarado / 68-player overcount."""
    txns = [
        Transaction(dt.date(2025, 4, 1), "Phillies selected the contract of LHP X from Lehigh Valley."),
        Transaction(dt.date(2025, 5, 18), "Phillies placed LHP X on the restricted list."),
        Transaction(dt.date(2025, 8, 19), "Phillies activated LHP X from the restricted list."),
    ]
    intervals = build_global_active_intervals(txns, dt.date(2025, 9, 28))
    check("the restricted-list placement closes the first interval the day before",
          (dt.date(2025, 4, 1), dt.date(2025, 5, 17)) in intervals)
    check("reinstatement opens a second interval",
          (dt.date(2025, 8, 19), dt.date(2025, 9, 28)) in intervals)
    check("the restricted-list days are not credited", len(intervals) == 2)


def test_a_dfa_in_season_keeps_the_clock_running_until_the_next_move():
    """Rules v7. A player designated for assignment DURING the season keeps
    earning service until the move that resolves the DFA (trade, claim,
    outright/option/assignment, release), capped at the grace window. Before v7
    the clock stopped the day of the DFA."""
    opened = Transaction(dt.date(2024, 5, 1), "Team A selected the contract of RHP X from Triple-A.")
    dfa = Transaction(dt.date(2024, 7, 10), "Team A designated RHP X for assignment.")
    claim = Transaction(dt.date(2024, 7, 12), "Team B claimed RHP X off waivers from Team A.")

    # (a) DFA then claim two days later: the two limbo days are credited.
    iv = build_global_active_intervals([opened, dfa, claim], dt.date(2024, 12, 31), dfa_limbo_days=7)
    check("the DFA-to-claim limbo closes the day before the claim (2 days credited)",
          iv[0] == (dt.date(2024, 5, 1), dt.date(2024, 7, 11)))

    # (b) DFA with nothing after it: capped at the grace window (7 days incl. the DFA day).
    iv2 = build_global_active_intervals([opened, dfa], dt.date(2024, 12, 31), dfa_limbo_days=7)
    check("an unresolved DFA is capped at the grace window",
          iv2[-1][1] == dt.date(2024, 7, 16))
    check("the grace window is exactly 7 days from the DFA",
          (iv2[-1][1] - dt.date(2024, 7, 10)).days + 1 == 7)

    # (c) grace off (the default) reproduces the old behaviour: stop at DFA - 1.
    iv3 = build_global_active_intervals([opened, dfa], dt.date(2024, 12, 31))
    check("with the grace window off the clock still stops the day before the DFA",
          iv3 == [(dt.date(2024, 5, 1), dt.date(2024, 7, 9))])

    # (d) an OFFSEASON DFA adds nothing: the grace days fall outside the season.
    season = SeasonWindow(2024, dt.date(2024, 3, 28), dt.date(2024, 9, 29))
    off_dfa = [
        Transaction(dt.date(2024, 5, 1), "Team A selected the contract of RHP X from Triple-A."),
        Transaction(dt.date(2024, 11, 3), "Team A designated RHP X for assignment."),
    ]
    with_grace = compute_service_time(off_dfa, [season], dfa_limbo_days=7, horizon_end=dt.date(2024, 12, 31))
    without = compute_service_time(off_dfa, [season], horizon_end=dt.date(2024, 12, 31))
    check("an offseason DFA's grace days add nothing (clipped to the season)",
          with_grace.total_days == without.total_days)


def test_a_dfa_resolved_the_same_day_credits_no_limbo():
    """Rules v7. When a DFA's outcome lands on the DFA's own date -- an outright,
    release, option or trade (a claim/activation is already caught) -- the
    outcome is known that day, so there is no grace window to credit. The guard
    used to check only has_start, so a same-day outright/option got a full 7-day
    over-credit (Waguespack 2021-03-10, Rico Garcia 2023-07-07, Juan Brito
    2026-08-03). The clock stops the day before the DFA, exactly as grace-off."""
    opened = Transaction(dt.date(2024, 5, 1), "Team A selected the contract of RHP X from Triple-A.")

    # DFA and an outright on the same day: no limbo.
    same_day_outright = [
        opened,
        Transaction(dt.date(2024, 7, 10), "Team A designated RHP X for assignment."),
        Transaction(dt.date(2024, 7, 10), "Team A sent RHP X outright to Triple-A."),
    ]
    iv = build_global_active_intervals(same_day_outright, dt.date(2024, 12, 31), dfa_limbo_days=7)
    check("a same-day DFA + outright stops the day before the DFA (no limbo)",
          iv == [(dt.date(2024, 5, 1), dt.date(2024, 7, 9))])

    # DFA and an option on the same day: no limbo.
    same_day_option = [
        opened,
        Transaction(dt.date(2024, 7, 10), "Team A designated RHP X for assignment."),
        Transaction(dt.date(2024, 7, 10), "Team A optioned RHP X to Triple-A."),
    ]
    iv2 = build_global_active_intervals(same_day_option, dt.date(2024, 12, 31), dfa_limbo_days=7)
    check("a same-day DFA + option credits no limbo",
          iv2 == [(dt.date(2024, 5, 1), dt.date(2024, 7, 9))])

    # DFA and a trade on the same day: no limbo (the trade names the outcome).
    same_day_trade = [
        opened,
        Transaction(dt.date(2024, 7, 10), "Team A designated RHP X for assignment."),
        Transaction(dt.date(2024, 7, 10), "Team A traded RHP X to Team B."),
    ]
    iv3 = build_global_active_intervals(same_day_trade, dt.date(2024, 12, 31), dfa_limbo_days=7)
    check("a same-day DFA + trade credits no limbo",
          iv3[0] == (dt.date(2024, 5, 1), dt.date(2024, 7, 9)))
    check("a same-day trade puts him on the new roster that day",
          iv3[-1] == (dt.date(2024, 7, 10), dt.date(2024, 12, 31)))

    # Control: a DFA that stands alone still gets its grace window.
    alone = [
        opened,
        Transaction(dt.date(2024, 7, 10), "Team A designated RHP X for assignment."),
    ]
    iv4 = build_global_active_intervals(alone, dt.date(2024, 12, 31), dfa_limbo_days=7)
    check("a DFA that stands alone still earns the grace window",
          iv4[-1][1] == dt.date(2024, 7, 16))


def test_a_spring_dfa_does_not_credit_regular_season_limbo():
    """Rules v7. A player designated for assignment BEFORE Opening Day earns no
    service for that season (OFRU: he was never on its active roster). The grace
    window must not cross an opener that falls after the DFA -- a DFA in the days
    before Opening Day used to spill regular-season limbo days (20 cached cases,
    e.g. 676724 2024-03-27, 596117 2026-03-25)."""
    opener = dt.date(2024, 3, 28)
    txns = [
        Transaction(dt.date(2023, 5, 1), "Team A recalled RHP X from Triple-A."),
        Transaction(dt.date(2024, 3, 25), "Team A designated RHP X for assignment."),
    ]

    # Without the opener, the 7-day window spills 4 days past Opening Day.
    spill = build_global_active_intervals(txns, dt.date(2024, 12, 31), dfa_limbo_days=7)
    check("without the opener the grace window crosses Opening Day",
          spill[-1][1] == dt.date(2024, 3, 31))

    # With the opener, limbo is clipped to the day before it -- nothing lands in
    # the regular season.
    clipped = build_global_active_intervals(
        txns, dt.date(2024, 12, 31), dfa_limbo_days=7, season_starts=[opener]
    )
    check("the spring DFA's limbo stops the day before Opening Day",
          clipped[-1][1] == dt.date(2024, 3, 27))

    # End to end: the player is credited nothing for the season he was DFA'd out
    # of before it began.
    seasons = [
        SeasonWindow(2023, dt.date(2023, 3, 30), dt.date(2023, 10, 1)),
        SeasonWindow(2024, opener, dt.date(2024, 9, 29)),
    ]
    result = compute_service_time(
        txns, seasons, dfa_limbo_days=7, horizon_end=dt.date(2024, 12, 31)
    )
    check("a spring DFA credits no regular-season days",
          result.by_season[2024]["credited_days"] == 0)

    # A DFA during the season keeps its grace window (the opener is behind it).
    in_season = [
        Transaction(dt.date(2024, 5, 1), "Team A recalled RHP X from Triple-A."),
        Transaction(dt.date(2024, 7, 10), "Team A designated RHP X for assignment."),
    ]
    iv = build_global_active_intervals(
        in_season, dt.date(2024, 12, 31), dfa_limbo_days=7, season_starts=[opener]
    )
    check("an in-season DFA still earns its grace window",
          iv[-1][1] == dt.date(2024, 7, 16))


def test_a_service_time_exception_can_restore_or_remove_days():
    """Rules v7. The feed phrases paid administrative leave, a 2020 opt-out and
    a suspension identically ('placed ... on the restricted list'), so the few
    that DO (or do not) earn service are corrected per player from
    data/service_time_exceptions.json: a counts=true period is unioned back in,
    a counts=false period is cut out."""
    season = SeasonWindow(2025, dt.date(2025, 4, 1), dt.date(2025, 9, 28))
    txns = [
        Transaction(dt.date(2025, 4, 1), "Club selected the contract of LHP X from Triple-A."),
        Transaction(dt.date(2025, 5, 18), "Club placed LHP X on the restricted list."),
    ]
    base = compute_service_time(txns, [season], horizon_end=dt.date(2025, 9, 28)).total_days
    restored = compute_service_time(
        txns, [season], horizon_end=dt.date(2025, 9, 28),
        force_active_periods=[(dt.date(2025, 5, 18), dt.date(2025, 9, 28))],
    ).total_days
    check("a counts=true exception restores the stopped days", restored > base)
    check("...and restores them to a full credited year (181 raw, capped at 172)", restored == 172)

    # A counts=false exception removes an otherwise-credited stretch.
    active = [Transaction(dt.date(2025, 4, 1), "Club selected the contract of LHP X from Triple-A.")]
    full = compute_service_time(active, [season], horizon_end=dt.date(2025, 9, 28)).total_days
    cut = compute_service_time(
        active, [season], horizon_end=dt.date(2025, 9, 28),
        force_inactive_periods=[(dt.date(2025, 5, 18), dt.date(2025, 9, 28))],
    ).total_days
    check("a counts=false exception removes the credited days", cut < full)
    check("...leaving only the days before it", cut == 47)


def test_jordan_hicks_2020_opt_out_keeps_his_season():
    """The real-world bug this exception fixes: Hicks's actual 2020
    transactions ('placed ... on the restricted list' 2020-07-13, 'activated
    ... from the restricted list' 2020-10-30) bracket the entire 2020 season,
    so the rules-v7 default restricted-list stop -- correct for an unpaid
    suspension -- would erase his whole prorated 2020 year. The hand-kept
    exception restores it."""
    import service_time_exceptions as ste

    season_2020 = SeasonWindow(2020, dt.date(2020, 7, 23), dt.date(2020, 9, 27))
    txns = [
        Transaction(dt.date(2019, 4, 1), "St. Louis Cardinals selected the contract of RHP Jordan Hicks."),
        Transaction(dt.date(2020, 2, 12), "St. Louis Cardinals placed RHP Jordan Hicks on the 60-day injured list."),
        Transaction(dt.date(2020, 7, 13), "St. Louis Cardinals placed RHP Jordan Hicks on the restricted list."),
        Transaction(dt.date(2020, 10, 30), "St. Louis Cardinals activated RHP Jordan Hicks from the restricted list."),
    ]
    without_exception = compute_service_time(txns, [season_2020], horizon_end=dt.date(2020, 9, 27))
    check("without the exception, the default restricted-list stop erases his 2020 season",
          without_exception.by_season.get(2020, {}).get("credited_days", 0) == 0)

    real = ste.load_exceptions()
    active, inactive = ste.periods_for_player(real, 663855)
    with_exception = compute_service_time(
        txns, [season_2020], horizon_end=dt.date(2020, 9, 27),
        force_active_periods=active, force_inactive_periods=inactive,
    )
    check("the exception restores a full credited 2020 year",
          with_exception.by_season[2020]["credited_days"] == 172)


def test_the_service_time_exceptions_file_loads_and_validates():
    """The hand-kept exceptions file validates as it loads, like the ROY list:
    a malformed or unsourced entry fails loudly in CI, and the shipped file
    carries the Trevor Bauer administrative-leave / suspension pair."""
    import service_time_exceptions as ste

    real = ste.load_exceptions()
    check("the shipped file loads", isinstance(real, dict))
    bauer = real.get(545333, [])
    check("Bauer's two spells are recorded", len(bauer) == 2)
    counts = sorted(e["counts"] for e in bauer)
    check("one counts, one does not", counts == [False, True])
    active, inactive = ste.periods_for_player(real, 545333)
    check("the paid leave is a counts=true period", (dt.date(2021, 7, 2), dt.date(2022, 4, 28)) in active)
    check("the suspension is a counts=false period", (dt.date(2022, 4, 29), dt.date(2022, 12, 31)) in inactive)
    for e in bauer:
        src = e["source"] if isinstance(e["source"], list) else [e["source"]]
        check(f"every source is an https URL ({e['start']})", all(u.startswith("https://") for u in src))

    hicks = real.get(663855, [])
    check("Jordan Hicks's 2020 opt-out spell is recorded", len(hicks) == 1)
    check("it counts as service", hicks[0]["counts"] is True)
    hicks_active, _ = ste.periods_for_player(real, 663855)
    check("the opt-out is a counts=true period covering the restricted-list dates",
          (dt.date(2020, 7, 13), dt.date(2020, 10, 30)) in hicks_active)

    def _rejects(entry, why):
        import tempfile, json as _json
        p = pathlib.Path(tempfile.mkstemp(suffix=".json")[1])
        p.write_text(_json.dumps({"exceptions": [entry]}))
        try:
            ste.load_exceptions(p)
            check(f"rejects {why}", False)
        except ste.ServiceTimeExceptionError:
            check(f"rejects {why}", True)
        finally:
            p.unlink()

    good = {"mlbam_id": 1, "name": "T", "start": "2021-01-01", "end": "2021-02-01",
            "counts": True, "reason": "r", "source": "https://example.com"}
    _rejects({**good, "source": "http://example.com"}, "a non-https source")
    _rejects({**good, "start": "not-a-date"}, "a non-ISO date")
    _rejects({**good, "end": "2020-01-01"}, "an end before the start")
    _rejects({k: v for k, v in good.items() if k != "counts"}, "a missing counts flag")
    _rejects({k: v for k, v in good.items() if k != "source"}, "a missing source")


def test_a_legacy_cache_without_team_ids_is_discarded_or_ignored():
    """Rules v7 stale-cache guard. A transaction cache written before team IDs
    were stored keeps ID-less rows, which _involves_mlb_club cannot judge and so
    keeps -- the Nelson Velazquez bug, where minor-league and Mexican-League
    moves read as Major League ones. A cache with any such row is refetched when
    online, or its ID-less rows are dropped when fetching is off."""
    import update_service_time as u

    legacy_row = {"date": "2019-06-01", "description": "Mexican League move", "team": "X"}
    healthy_row = {"date": "2020-04-01", "description": "Club selected the contract of X.",
                   "team": "Club", "team_id": 147, "from_team_id": None, "to_team_id": 147}
    check("a row missing all three id keys is legacy", u._row_is_legacy_format(legacy_row))
    check("a row with the id keys (even all null) is not legacy",
          not u._row_is_legacy_format({**healthy_row, "team_id": None, "to_team_id": None, "from_team_id": None}))
    check("a cache with one legacy row is legacy", u._cache_is_legacy([healthy_row, legacy_row]))
    check("an all-healthy cache is not legacy", not u._cache_is_legacy([healthy_row]))

    # Offline, _fetch_transactions_incremental drops the ID-less rows rather
    # than letting them read as Major League moves.
    real_load = u._load_cached_transactions
    real_enabled = u.mlb_fetch_enabled
    u._load_cached_transactions = lambda pid: [healthy_row, legacy_row]
    u.mlb_fetch_enabled = lambda: False
    try:
        out = u._fetch_transactions_incremental(99, full_refresh=False, use_cache=True)
        check("offline, the legacy row is dropped", legacy_row not in out)
        check("offline, the healthy row is kept", healthy_row in out)
    finally:
        u._load_cached_transactions = real_load
        u.mlb_fetch_enabled = real_enabled


def test_new_transaction_fetches_store_type_dates_and_id_old_rows_still_read():
    """
    The incremental fetcher used to drop typeCode, typeDesc, effectiveDate,
    resolutionDate and the transaction id. New rows store them, including a
    null the feed actually sent (so a fetched row is distinguishable from an
    older one that lacks the key).

    Older cache rows stay readable: the key is simply absent, and reading it
    is None. That absence is not a legacy cache — the legacy path refetches
    when team ids are missing, which these rows still have — and it is not
    fed to the service-time engine. An incremental fetch does not rewrite
    the old rows; it only appends.
    """
    import update_service_time as u

    raw = {
        "id": 507148,
        "date": "2021-07-25",
        "effectiveDate": "2021-07-22",
        "resolutionDate": None,
        "typeCode": "SC",
        "typeDesc": "Status Change",
        "description": "Los Angeles Dodgers placed RF Mookie Betts on the 10-day injured list.",
        "team": {"id": 119, "name": "Los Angeles Dodgers"},
        "toTeam": {"id": 119, "name": "Los Angeles Dodgers"},
    }
    row = u._cache_transaction(raw)
    check("typeCode is stored", row["typeCode"] == "SC")
    check("typeDesc is stored", row["typeDesc"] == "Status Change")
    check("effectiveDate is stored as the feed gave it, not the row date",
          row["effectiveDate"] == "2021-07-22" and row["date"] == "2021-07-25")
    check("a null resolutionDate is stored as None", row["resolutionDate"] is None)
    check("the key is present even when the value is null", "resolutionDate" in row)
    check("the transaction id is stored", row["id"] == 507148)
    check("team ids are still stored alongside the new fields",
          row["team_id"] == 119 and row["to_team_id"] == 119)

    omitted = u._cache_transaction({
        "date": "2024-05-01",
        "description": "Club selected the contract of X.",
        "team": {"id": 147},
        "toTeam": {"id": 147, "name": "Club"},
    })
    for key in ("typeCode", "typeDesc", "effectiveDate", "resolutionDate", "id"):
        check(f"a feed row that omits {key} still stores the key", key in omitted and omitted[key] is None)
    check("a row with no date is not cached", u._cache_transaction({"description": "no date"}) is None)
    check(
        "a non-scalar id is not stored as an object",
        u._cache_transaction({"date": "2024-01-01", "id": {"unexpected": 1}})["id"] is None,
    )

    old = {
        "date": "2020-04-01",
        "description": "Club selected the contract of X.",
        "team": "Club",
        "team_id": 147,
        "from_team_id": None,
        "to_team_id": 147,
    }
    for key in ("typeCode", "typeDesc", "effectiveDate", "resolutionDate", "id"):
        check(f"an older cache row has no {key} key", key not in old)
        check(f"reading {key} off an older row is None", old.get(key) is None)
    check("missing type fields do not make a row legacy", not u._row_is_legacy_format(old))
    check("a newly fetched row is not legacy either", not u._row_is_legacy_format(row))
    check("an older row still counts as a major-league move", u._involves_mlb_club(old, {147}))
    check("a new row still counts as a major-league move", u._involves_mlb_club(row, {119}))

    def _as_txn(cached_row):
        return Transaction(
            date=dt.date.fromisoformat(cached_row["date"]),
            description=cached_row["description"],
            team=(
                (cached_row.get("team") or {}).get("name")
                if isinstance(cached_row.get("team"), dict)
                else cached_row.get("team")
            ),
        )

    built = [_as_txn(old), _as_txn(row)]
    check("the engine's reader accepts an older row and a new one", len(built) == 2)
    check("the new fields are not copied onto the engine transaction",
          not hasattr(built[1], "typeCode") and not hasattr(built[1], "effectiveDate"))

    # Incremental fetch: the cached row predates the new fields and must come
    # back unchanged. The newly fetched row carries them. Nothing is written
    # to the real cache.
    old_day = (u.TODAY - dt.timedelta(days=30)).isoformat()
    cached_old = dict(old, date=old_day)
    fresh = dict(raw, date=u.TODAY.isoformat())
    saved = {}

    real_load = u._load_cached_transactions
    real_save = u._save_cached_transactions
    real_get = u.mlb.get_player_transactions

    def _capture(_pid, txns):
        saved["txns"] = txns

    u._load_cached_transactions = lambda pid: [dict(cached_old)]
    u._save_cached_transactions = _capture
    u.mlb.get_player_transactions = lambda pid, start, end: [fresh, {"description": "undated"}]
    try:
        out = u._fetch_transactions_incremental(999001, full_refresh=False, use_cache=True)
    finally:
        u._load_cached_transactions = real_load
        u._save_cached_transactions = real_save
        u.mlb.get_player_transactions = real_get

    check("the incremental result has the old row and the new one", len(out) == 2)
    check("the older row was not rewritten with the new keys",
          out[0]["date"] == old_day and "typeCode" not in out[0] and "id" not in out[0])
    check("the new row stored typeCode and the retro effectiveDate",
          out[1]["typeCode"] == "SC" and out[1]["effectiveDate"] == "2021-07-22")
    check("the new row stored the transaction id", out[1]["id"] == 507148)
    check("an undated feed row was not cached", all(t.get("description") != "undated" for t in out))
    check("the save received the same rows", saved.get("txns") == out)


def _cached_roster_transactions(player_id: int) -> list[Transaction]:
    """The rows the publisher would keep, read from the on-disk cache.

    No network. Team ids are the 30 clubs hardcoded for display, the same
    set the publisher fetches.
    """
    import mlb_clubs
    import update_service_time as u

    u._MLB_TEAM_IDS = set(mlb_clubs.MLB_CLUB_IDS)
    from service_time import is_free_agency_election

    raw = json.loads(
        (pathlib.Path(__file__).resolve().parents[1] / "data" / "cache" / "transactions" / f"{player_id}.json").read_text()
    )
    mlb_ids = u.mlb_team_ids()
    kept = []
    for row in raw:
        desc = row.get("description") or ""
        minor = u._is_minor_assignment(row, mlb_ids)
        mlb_asg = u._is_mlb_assignment(row, mlb_ids)
        if not (
            u._involves_mlb_club(row, mlb_ids)
            or minor
            or mlb_asg
            or is_active_stop(desc)
            or is_free_agency_election(desc)
        ):
            continue
        kept.append(
            Transaction(
                date=dt.date.fromisoformat(row["date"]),
                description=desc,
                minor_assignment=minor,
                mlb_assignment=mlb_asg,
            )
        )
    return kept


def _covers(intervals, day: dt.date) -> bool:
    return any(start <= day <= end for start, end in intervals)


def _cached_intervals(player_id: int, debut: str):
    floor = dt.date.fromisoformat(debut)
    return build_global_active_intervals(
        _cached_roster_transactions(player_id),
        dt.date(2026, 9, 27),
        accrual_floor=floor,
        accrual_ceiling=dt.date(2026, 9, 27),
        presume_active_from=floor,
        dfa_limbo_days=7,
    )


def test_free_agency_and_same_day_trades_on_five_cached_careers():
    """The five careers this rule was checked against, from the cached histories.

    Nick Martinez spent 2018 in Japan after electing free agency in December
    2017. Michael Conforto sat out 2022. Nestor Cortes was not on a
    major-league roster in 2026. Jonah Heim was designated and traded on the
    same day in 2026, and the clock has to keep running. Christian
    Bethancourt's 2024 election is in the offseason; it must not leave the
    clock open into December.
    """
    martinez = _cached_intervals(607259, "2014-04-05")
    check("Martinez's 2018 season in Japan is not service time",
          not _covers(martinez, dt.date(2018, 6, 1)))
    check("Martinez was on a roster in September 2017",
          _covers(martinez, dt.date(2017, 9, 1)))
    check("Martinez's clock restarts when he signs in 2022",
          _covers(martinez, dt.date(2022, 6, 1)))

    conforto = _cached_intervals(624424, "2015-07-24")
    check("Conforto does not accrue the 2022 season he sat out",
          not _covers(conforto, dt.date(2022, 6, 1)))
    check("Conforto accrued in 2021", _covers(conforto, dt.date(2021, 6, 1)))
    check("Conforto's clock restarts on the 2023 signing",
          _covers(conforto, dt.date(2023, 6, 1)))

    cortes = _cached_intervals(641482, "2018-03-31")
    check("Cortes does not accrue a 2026 season he was not rostered for",
          not _covers(cortes, dt.date(2026, 6, 1)))
    check("Cortes accrued in 2025", _covers(cortes, dt.date(2025, 6, 1)))
    seasons = [
        SeasonWindow(y, dt.date(y, 3, 26), dt.date(y, 9, 27)) for y in range(2018, 2027)
    ]
    cortes_days = compute_service_time(
        _cached_roster_transactions(641482),
        seasons,
        presume_active_from_debut=True,
        horizon_end=dt.date(2026, 9, 27),
        accrual_floor=dt.date(2018, 3, 31),
        accrual_ceiling=dt.date(2026, 9, 27),
        dfa_limbo_days=7,
    )
    check("Cortes's 2026 credited days are zero",
          cortes_days.by_season[2026]["credited_days"] == 0)

    heim = _cached_intervals(641680, "2020-08-25")
    check("Heim stays on the clock after the same-day DFA and trade",
          _covers(heim, dt.date(2026, 7, 1)))
    check("Heim's trade day itself is on the clock",
          _covers(heim, dt.date(2026, 5, 4)))

    beth = _cached_intervals(542194, "2013-09-29")
    check("Bethancourt's November 2024 election does not run into December",
          not _covers(beth, dt.date(2024, 12, 1)))
    check("Bethancourt's 2025 minor-league year is not service time",
          not _covers(beth, dt.date(2025, 6, 1)))
    check("Bethancourt's September 2026 call-up is on the clock",
          _covers(beth, dt.date(2026, 9, 1)))
    check("Bethancourt's outright a few days later stops it",
          not _covers(beth, dt.date(2026, 9, 15)))


def test_rules_version_is_ten():
    """The bump the backfill reads to know which players to recompute."""
    import update_service_time as u
    check("SERVICE_TIME_RULES_VERSION is 10", u.SERVICE_TIME_RULES_VERSION == 10)


def test_an_mlb_club_assignment_restarts_a_stopped_clock():
    """Zac Gallen and Tyler Kinley came back in 2026 on "assigned to the
    club", not a signing, so the free-agency stop left the clock off until
    an injured-list row. That assignment is the return. A spring-training
    assignment, a same-day minor-league assignment, an option, and an
    outright are not.
    """
    gallen = _cached_intervals(668678, "2019-06-20")
    check("Gallen's clock stays off after the 2025 free-agency election",
          not _covers(gallen, dt.date(2025, 12, 1)))
    check("Gallen is back on the clock in May 2026, from the February assignment",
          _covers(gallen, dt.date(2026, 5, 1)))

    kinley = _cached_intervals(641755, "2018-08-07")
    check("Kinley's clock stays off after the 2025 free-agency election",
          not _covers(kinley, dt.date(2025, 12, 1)))
    check("Kinley is back on the clock in May 2026, from the January assignment",
          _covers(kinley, dt.date(2026, 5, 1)))

    # Otherwise off: optioned, traded, then assigned to the new club. The
    # July 7 assignment is the return; the June option gap is not.
    tellez = _cached_intervals(642133, "2018-08-09")
    check("Tellez is not credited while optioned, before the Brewers assignment",
          not _covers(tellez, dt.date(2021, 7, 1)))
    check("Tellez's assignment to the Brewers restarts the clock",
          _covers(tellez, dt.date(2021, 8, 15)))

    # Spring note, then a contract selection: he was in the minors until the
    # selection. The March assignment must not fill the summer.
    ginkel = _cached_intervals(656464, "2019-08-17")
    check("a spring assignment does not credit the months before a selection",
          not _covers(ginkel, dt.date(2022, 6, 1)))

    # Same-day minor-league assignment wins. Bruihl was in Buffalo until the
    # June selection.
    bruihl = _cached_intervals(677865, "2020-08-22")
    check("a same-day minor-league assignment does not restart the clock",
          not _covers(bruihl, dt.date(2025, 4, 15)))

    import update_service_time as u
    ids = {109}
    check(
        "an assignment to a major-league club is flagged as one",
        u._is_mlb_assignment(
            {"description": "RHP X assigned to Arizona Diamondbacks.", "to_team_id": 109},
            ids,
        ),
    )
    check(
        "a minor-league assignment is not an active-roster return",
        not u._is_mlb_assignment(
            {"description": "RHP X assigned to Reno Aces.", "to_team_id": 2310},
            ids,
        ),
    )
    check(
        "an outright assignment does not restart the clock",
        not u._is_mlb_assignment(
            {"description": "RHP X assigned outright to Reno Aces.", "to_team_id": 2310},
            ids,
        )
        and not u._is_mlb_assignment(
            {"description": "Arizona Diamondbacks assigned RHP X outright to the roster.", "to_team_id": 109},
            ids,
        ),
    )
    check(
        "an optional assignment does not restart the clock",
        not u._is_mlb_assignment(
            {"description": "Arizona Diamondbacks optioned RHP X to Reno Aces.", "to_team_id": 2310},
            ids,
        ),
    )
    check(
        "a rehab assignment does not restart the clock",
        not u._is_mlb_assignment(
            {"description": "Arizona Diamondbacks sent RHP X on a rehab assignment to Reno Aces.", "to_team_id": 2310},
            ids,
        ),
    )

    # Synthetic mirror of Gallen: election, assignment, then the injured list.
    seasons = [SeasonWindow(2026, dt.date(2026, 3, 26), dt.date(2026, 9, 27))]
    back = compute_service_time(
        [
            Transaction(dt.date(2025, 11, 2), "RHP X elected free agency."),
            Transaction(
                dt.date(2026, 2, 15),
                "RHP X assigned to Arizona Diamondbacks.",
                mlb_assignment=True,
            ),
            Transaction(
                dt.date(2026, 7, 12),
                "Arizona Diamondbacks placed RHP X on the 15-day injured list.",
            ),
        ],
        seasons,
        horizon_end=dt.date(2026, 9, 27),
        accrual_floor=dt.date(2019, 6, 20),
        dfa_limbo_days=7,
    )
    check(
        "the assignment credits the season from Opening Day, not from the injured list",
        back.by_season[2026]["credited_days"] == 172,
    )


def test_the_dfa_grace_window_is_a_sourced_ruleset_value():
    """The 7-day grace window is read from config/cba, never a literal, and
    carries a sources entry like every other value the engine consumes."""
    rules = cba.default()
    check("the DFA grace window is 7 days", rules.require("designated_for_assignment.max_service_days") == 7)
    check("it carries a sources status",
          rules.status("designated_for_assignment.max_service_days") is not None)


def test_the_player_level_v7_figures_hold_against_the_real_feed():
    """Integration: the three diagnosed players, computed through the real
    pipeline (cached transactions + the live /schedule windows). Skipped when
    the feed is unreachable (CI), since without it the windows are only an
    estimate. These are the headline figures in the PR."""
    if not _can_reach_statsapi():
        check("statsapi reachable for the player-level v7 checks (skipped offline)", True)
        return
    import update_service_time as u

    def days(pid, name, rostered, dfa=u._DFA_LIMBO_DAYS):
        # Never write the transaction cache: this runs live and some of these
        # players have fresher transactions than the in-repo cache (or none),
        # and a test must not mutate committed data.
        saved_dfa, saved_save = u._DFA_LIMBO_DAYS, u._save_cached_transactions
        u._DFA_LIMBO_DAYS = dfa
        u._save_cached_transactions = lambda _pid, _txns: None
        try:
            r = u.build_player_record({"id": pid, "fullName": name}, full_refresh=False,
                                      use_cache=True, currently_rostered=rostered)
        finally:
            u._DFA_LIMBO_DAYS = saved_dfa
            u._save_cached_transactions = saved_save
        return r

    bauer = days(545333, "Trevor Bauer", False)
    check(f"Bauer is ~1,382 days, not the old 1,533 (got {bauer['service_days_total']})",
          1381 <= bauer["service_days_total"] <= 1384)
    ceiling = bauer["accrual_ceiling"]
    check("Bauer's career does not run past 2022 (no fallback to the 2023 release)",
          ceiling is None or str(ceiling)[:4] <= "2022")

    # Rules v7 regression (QA HOLD on PR #25): a player who spent whole seasons
    # on a major-league roster -- Marsh and Zastryzny on the 60-day IL -- and
    # was THEN outrighted keeps that IL service. The first fix capped them at
    # their last APPEARANCE and cratered each by a year or two. Needs the live
    # feed. Rules v8 additionally closes a stint on a minor-league assignment
    # the old filter dropped, which removes phantom years spent in the minors
    # (Zastryzny 2021-22, Bethancourt 2025). The floors stay clear of the
    # cratered figures; the ceilings stay clear of those phantom years.
    marsh = days(679525, "Alec Marsh", False)
    check(f"Marsh keeps his 2025-26 IL service, not cratered to ~250d (got {marsh['service_days_total']})",
          marsh["service_days_total"] >= 560)
    beth = days(542194, "Christian Bethancourt", False)
    beth_2025 = next((s["d"] for s in beth["seasons"] if s["y"] == 2025), 0)
    beth_2026 = next((s["d"] for s in beth["seasons"] if s["y"] == 2026), 0)
    check(f"Bethancourt keeps his real roster service (got {beth['service_days_total']})",
          800 <= beth["service_days_total"] <= 1000)
    check(f"Bethancourt's minor-league 2025 is not credited (got {beth_2025})", beth_2025 == 0)
    check(f"Bethancourt's 2026 is the September selection, not a full year (got {beth_2026})",
          beth_2026 <= 40)
    zast = days(642239, "Rob Zastryzny", False)
    zast_2021 = next((s["d"] for s in zast["seasons"] if s["y"] == 2021), 0)
    zast_2026 = next((s["d"] for s in zast["seasons"] if s["y"] == 2026), 0)
    check(f"Zastryzny keeps his IL service without the minor-league years (got {zast['service_days_total']})",
          540 <= zast["service_days_total"] <= 700)
    check(f"Zastryzny 2021 in the minors is not a full year (got {zast_2021})", zast_2021 <= 20)
    check(f"Zastryzny's 2026 IL season is credited (got {zast_2026})", zast_2026 >= 160)

    # The opposite guard: Aaron Sanchez elected free agency after his last 2022
    # selection and has only signed minor-league deals since, so he must stay
    # capped near his real ~6.1 years, not balloon to today (the pre-fix 9.109
    # that the stale _still_on_ml_roster guard produced off the 2022 selection).
    sanchez = days(592717, "Aaron Sanchez", False)
    check(f"Sanchez stays capped near his real figure, not ballooning past ~1,200d (got {sanchez['service_days_total']})",
          1100 <= sanchez["service_days_total"] <= 1200)

    alv = days(621237, "Jose Alvarado", False)
    check(f"Alvarado drops the restricted-list days (got {alv['service_days_total']})",
          1555 <= alv["service_days_total"] <= 1559)

    # The stale-cache guard alone keeps Velazquez at 2.042; the DFA grace window
    # then legitimately adds his two 2026 limbo stretches.
    vel_cacheonly = days(676369, "Nelson Velazquez", False, dfa=None)
    check(f"the cache guard keeps Velazquez at 2.042 / 386 days (got {vel_cacheonly['service_days_total']})",
          vel_cacheonly["service_days_total"] == 386)
    vel_full = days(676369, "Nelson Velazquez", False)
    check(f"the in-season DFA grace adds his 2026 limbo days (got {vel_full['service_days_total']})",
          vel_full["service_days_total"] == 391)


# --- rules v7: the season-boundary (off-by-one) fix -------------------------

def _R(home, away, date, **extra):
    """A played regular-season game row for a /schedule fixture."""
    g = {"gameType": "R", "status": {"detailedState": "Final"},
         "teams": {"home": {"team": {"id": home}}, "away": {"team": {"id": away}}}}
    g.update(extra)
    return {"date": date, "games": [g]}


def test_a_tiebreaker_game_does_not_set_the_league_season_end():
    """Rules v7. CBA Art. XXI(A)(1) runs service through the last REGULARLY
    scheduled game 'uniformly to all ... Clubs'. A tiebreaker ('game 163',
    gameType R with tiebreaker 'Y') is not regularly scheduled, so it must not
    set the league end -- the 2013 TEX/TB 09-30 and 2018 MIL/CHC, COL/LAD 10-01
    games used to drag the end a day past the last real game for every club
    (Cole/Arenado/Soto +1). But a club that actually PLAYED the tiebreaker keeps
    that day for its own players."""
    import fetch_mlb_data as mlb

    payload = {"dates": [
        _R(140, 147, "2013-09-29"),  # last regularly scheduled games
        _R(139, 111, "2013-09-29"),
        _R(140, 139, "2013-09-30", tiebreaker="Y"),  # AL tiebreaker, TEX(140) @ TB(139)
    ]}
    sched = mlb._parse_season_schedule(2013, payload)
    check("the league end is the last regular game, not the tiebreaker", sched.end == dt.date(2013, 9, 29))
    check("a club that played the tiebreaker keeps that day", sched.club_ends[140] == dt.date(2013, 9, 30))
    check("...both of them", sched.club_ends[139] == dt.date(2013, 9, 30))
    check("a club that did not keeps the regular end", sched.club_ends[147] == dt.date(2013, 9, 29))

    real_get = mlb._get
    mlb.get_season_window.cache_clear()
    mlb._SEASON_SCHEDULE.clear()
    try:
        mlb._get = lambda path, params=None: payload
        check("get_club_season_end extends a tiebreaker club", mlb.get_club_season_end(2013, 140) == dt.date(2013, 9, 30))
        check("get_club_season_end leaves a non-tiebreaker club at the league end", mlb.get_club_season_end(2013, 147) == dt.date(2013, 9, 29))
        check("an unknown club falls back to the league end", mlb.get_club_season_end(2013, 999) == dt.date(2013, 9, 29))
    finally:
        mlb._get = real_get
        mlb.get_season_window.cache_clear()
        mlb._SEASON_SCHEDULE.clear()


def test_the_2018_tiebreakers_end_the_league_season_on_09_30():
    """The 2018 case specifically: two game-163s on 10-01 (CHC/MIL and COL/LAD).
    The league ends 09-30; the four clubs that played keep 10-01."""
    import fetch_mlb_data as mlb
    payload = {"dates": [
        _R(112, 158, "2018-09-30"),
        _R(115, 119, "2018-09-30"),
        _R(112, 158, "2018-10-01", tiebreaker="Y"),  # CHC(112) v MIL(158)
        _R(119, 115, "2018-10-01", tiebreaker="Y"),  # LAD(119) v COL(115)
    ]}
    sched = mlb._parse_season_schedule(2018, payload)
    check("the 2018 league end is 09-30", sched.end == dt.date(2018, 9, 30))
    for club in (112, 158, 115, 119):
        check(f"club {club} keeps its 10-01 tiebreaker", sched.club_ends[club] == dt.date(2018, 10, 1))


def test_the_cba_uniform_start_applies_to_every_club_except_an_overseas_opener():
    """Rules v7. A staggered DOMESTIC opener must not cost a player a day (CBA
    Art. XXI(A)(1)); every domestic club shares one Opening Day. A club that
    opened OVERSEAS earlier (Art. V(A): outside the 187-day window) keeps its
    own earlier date. 2022 DET opened 04-08 vs the league's 04-07; a Tokyo/Seoul
    club opened a week before everyone else."""
    import fetch_mlb_data as mlb
    import update_service_time as u

    # Overseas club 119 opens 03-20; most domestic clubs (147/111/139/118) open
    # 04-07, the modal date; DET(116) opens 04-08, a day late. The season ends
    # 09-28.
    payload = {"dates": [
        _R(119, 121, "2024-03-20"),   # overseas (Seoul-style) opener
        _R(147, 111, "2024-04-07"),   # league Opening Day
        _R(139, 118, "2024-04-07"),   # most of the league shares it
        _R(116, 135, "2024-04-08"),   # a club that opened a day late
        _R(147, 111, "2024-09-28"),   # last game
    ]}
    sched = mlb._parse_season_schedule(2024, payload)
    check("the uniform start is the modal domestic opener (04-07)", sched.uniform_opener == dt.date(2024, 4, 7))
    check("the overseas club keeps its own early opener", sched.club_openers[119] == dt.date(2024, 3, 20))

    real_get = mlb._get
    mlb.get_season_window.cache_clear()
    mlb._SEASON_SCHEDULE.clear()
    try:
        mlb._get = lambda path, params=None: payload
        check("get_uniform_season_start is 04-07", mlb.get_uniform_season_start(2024) == dt.date(2024, 4, 7))
        check("a late-opening domestic club's player starts on the uniform day",
              u._opening_day_for(2024, 116, [116]) == dt.date(2024, 4, 7))
        check("an on-time domestic club too",
              u._opening_day_for(2024, 147, [147]) == dt.date(2024, 4, 7))
        check("an overseas club keeps its own earlier opener (no regression)",
              u._opening_day_for(2024, 119, [119]) == dt.date(2024, 3, 20))
        check("an unknown opening-day club takes the latest candidate's CBA start",
              u._opening_day_for(2024, None, [119, 147]) == dt.date(2024, 4, 7))
    finally:
        mlb._get = real_get
        mlb.get_season_window.cache_clear()
        mlb._SEASON_SCHEDULE.clear()


def test_an_early_domestic_outlier_does_not_become_the_uniform_start():
    """Rules v7, round 2. The 187-day-window rule for uniform_opener correctly
    excluded a true overseas opener, but an early-domestic outlier (not
    overseas, just a special early game) falls INSIDE that window too, so it
    was wrongly adopted as the shared Opening Day every other club's player
    snapped down to. 2026: the Yankees and Giants opened at Oracle Park on
    03-25; 22 clubs opened 03-26, six opened 03-27 (none of them played that
    day). A DFA in the days before a club's own opener must clip its limbo at
    THAT club's opener, not at 03-25 -- see the real cases this fixes: Stubbs
    (PHI), Wisely (ATL), Rortvedt (NYM), Mead (CWS), Leo Jimenez (TOR) and
    Waters (KC)."""
    import fetch_mlb_data as mlb
    import update_service_time as u

    payload = {"dates": [
        _R(147, 137, "2026-03-25"),   # NYY @ SF, Oracle Park -- 2 clubs
        _R(143, 144, "2026-03-26"),   # PHI/ATL -- the shared Opening Day
        _R(121, 145, "2026-03-26"),   # NYM/CWS, same day
        _R(141, 118, "2026-03-27"),   # TOR/KC open a day later
        _R(147, 137, "2026-09-27"),   # last game, keeps the window realistic
    ]}
    sched = mlb._parse_season_schedule(2026, payload)
    check("the modal opener is 03-26, not the 2-club 03-25 outlier",
          sched.domestic_opener == dt.date(2026, 3, 26))
    check("the uniform start follows the modal opener, not the earliest one",
          sched.uniform_opener == dt.date(2026, 3, 26))
    check("the Oracle Park clubs keep their own earlier opener",
          sched.club_openers[147] == dt.date(2026, 3, 25))

    real_get = mlb._get
    mlb.get_season_window.cache_clear()
    mlb._SEASON_SCHEDULE.clear()
    try:
        mlb._get = lambda path, params=None: payload
        check("a club that opened 03-27 still starts on the 03-26 uniform day, not 03-25",
              u._opening_day_for(2026, 141, [141]) == dt.date(2026, 3, 26))
        check("Oracle Park's own clubs keep their 03-25 start",
              u._opening_day_for(2026, 147, [147]) == dt.date(2026, 3, 25))

        # End to end: a DFA on 03-24 (before PHI's real opener) must not spill
        # any regular-season limbo days just because two other clubs happened
        # to play on 03-25.
        txns = [
            Transaction(dt.date(2025, 5, 1), "Philadelphia Phillies recalled RHP X from Triple-A."),
            Transaction(dt.date(2026, 3, 24), "Philadelphia Phillies designated RHP X for assignment."),
        ]
        opener = u._opening_day_for(2026, 143, [143])
        check("PHI's own opener is 03-26, not the Oracle Park 03-25", opener == dt.date(2026, 3, 26))
        clipped = build_global_active_intervals(
            txns, dt.date(2026, 12, 31), dfa_limbo_days=7, season_starts=[opener]
        )
        check("the spring DFA's limbo stops the day before PHI's own opener",
              clipped[-1][1] == dt.date(2026, 3, 25))
    finally:
        mlb._get = real_get
        mlb.get_season_window.cache_clear()
        mlb._SEASON_SCHEDULE.clear()


def test_a_tiebreaker_clubs_player_window_runs_a_day_longer():
    """End to end through the window builder: a player on the club that played
    the tiebreaker gets the extra day; a player on any other club does not."""
    import fetch_mlb_data as mlb
    import update_service_time as u

    payload = {"dates": [
        _R(140, 147, "2013-09-29"),
        _R(139, 111, "2013-09-29"),
        _R(140, 139, "2013-09-30", tiebreaker="Y"),
    ]}
    real_get = mlb._get
    mlb.get_season_window.cache_clear()
    mlb._SEASON_SCHEDULE.clear()
    try:
        mlb._get = lambda path, params=None: payload
        tb = u._season_windows_for_player(range(2013, 2014), {2013: [140]}, {}, {2013: 140})
        other = u._season_windows_for_player(range(2013, 2014), {2013: [147]}, {}, {2013: 147})
        check("the tiebreaker club's season runs through 09-30", tb[0].end == dt.date(2013, 9, 30))
        check("every other club's season ends on the regular 09-29", other[0].end == dt.date(2013, 9, 29))
    finally:
        mlb._get = real_get
        mlb.get_season_window.cache_clear()
        mlb._SEASON_SCHEDULE.clear()


def test_soto_accepted_delta_reflects_the_boundary_fix():
    """The season-boundary fix removed Soto's '1 of rounding' (the 2018 10-01
    tiebreaker), so his accepted reference delta drops from 6 to 5.

    The hand-entered reference figures live OUTSIDE the repo (owner's rule: no
    third-party reference numbers checked in). Point ST_REFERENCE_PATH at the
    private copy to run this check; without it the test skips cleanly."""
    ref_path = os.environ.get("ST_REFERENCE_PATH")
    if not ref_path or not pathlib.Path(ref_path).exists():
        check("ST_REFERENCE_PATH reference file present (skipped; figures live outside the repo)", True)
        return
    ref = json.loads(pathlib.Path(ref_path).read_text())
    soto = next(r for r in ref if r.get("player_id") == 665742)
    check("Soto's accepted_delta is 5", soto["accepted_delta"] == 5)


def test_a_bare_signing_beside_a_minor_league_deal_does_not_open_a_stint():
    """The feed files two rows on one day: a bare 'signed free agent' and a
    sibling that says 'to a minor league contract' (Snelten, Ravelo). Only
    the sibling is true. The bare row must not open years of service."""
    seasons = [SeasonWindow(y, dt.date(y, 3, 28), dt.date(y, 10, 1)) for y in (2021, 2022)]
    txns = [
        Transaction(dt.date(2020, 11, 16), "Chicago Cubs signed free agent P X."),
        Transaction(dt.date(2020, 11, 16), "Chicago Cubs signed free agent P X to a minor league contract."),
    ]
    result = compute_service_time(txns, seasons, horizon_end=dt.date(2022, 10, 1))
    check("the duplicate minor-league signing accrues nothing", result.total_days == 0)


def test_a_minor_league_assignment_closes_an_open_signing():
    """Kaminsky, 2022-04-06: 'Mariners signed free agent' with no minor-league
    words, and the same day 'assigned to Arkansas Travelers'. The assignment
    is the departure. Nothing after it should accrue."""
    seasons = [SeasonWindow(y, dt.date(y, 3, 30), dt.date(y, 10, 1)) for y in (2020, 2022, 2023, 2024, 2025, 2026)]
    txns = [
        Transaction(dt.date(2020, 8, 15), "Cardinals selected the contract of LHP X."),
        Transaction(dt.date(2020, 9, 18), "Cardinals sent LHP X outright to Memphis."),
        Transaction(dt.date(2022, 4, 6), "Mariners signed free agent LHP X."),
        Transaction(dt.date(2022, 4, 6), "LHP X assigned to Arkansas Travelers.", minor_assignment=True),
    ]
    result = compute_service_time(txns, seasons, horizon_end=dt.date(2026, 10, 1))
    check("2020 still has the real stint", result.by_season[2020]["credited_days"] > 0)
    check("2022 and after stay at zero",
          all(result.by_season[y]["credited_days"] == 0 for y in range(2022, 2027)))


def test_same_day_free_agency_cancels_the_activation():
    """Almonte, 2024-11-04: activated off the 60-day IL and elected free
    agency the same day. The activation must not reopen the clock through
    later seasons."""
    seasons = [SeasonWindow(y, dt.date(y, 3, 28), dt.date(y, 10, 1)) for y in (2024, 2025, 2026)]
    txns = [
        Transaction(dt.date(2024, 5, 11), "Cubs placed RHP X on the 15-day injured list."),
        Transaction(dt.date(2024, 11, 4), "Cubs activated RHP X from the 60-day injured list."),
        Transaction(dt.date(2024, 11, 4), "RHP X elected free agency."),
    ]
    result = compute_service_time(txns, seasons, horizon_end=dt.date(2026, 10, 1))
    check("the 2024 injured-list season still counts", result.by_season[2024]["credited_days"] > 0)
    check("2025 does not open off the election-day activation", result.by_season[2025]["credited_days"] == 0)
    check("2026 does not either", result.by_season[2026]["credited_days"] == 0)
    import update_service_time as u
    check("he is not still on a major-league roster", u._still_on_ml_roster(txns) is False)


def test_reassigned_to_the_minors_is_a_stop():
    from service_time import is_active_stop
    check(
        "reassigned to the minor leagues stops the clock",
        is_active_stop("New York Yankees reassigned RHP X to the minor leagues."),
    )


def test_appearances_fill_a_season_the_transactions_left_closed():
    """Gordon 2017 / Sears 2023: a stop the feed never reversed, then a
    season of games. The span from the first game to the last is credited.
    A stop between two games (the suspension itself) is not filled."""
    season = SeasonWindow(2017, dt.date(2017, 4, 2), dt.date(2017, 10, 1))
    txns = [
        Transaction(dt.date(2016, 4, 29), "Marlins placed 2B X on the restricted list."),
    ]
    # Games across the whole 2017 season, and none of them cross a stop
    # inside the season.
    dates = [dt.date(2017, 4, 3), dt.date(2017, 7, 1), dt.date(2017, 9, 30)]
    result = compute_service_time(
        txns, [season], horizon_end=dt.date(2017, 10, 1), appearance_dates=dates,
    )
    check("the played season is credited", result.by_season[2017]["credited_days"] >= 170)

    # 2016: games before the April 29 suspension and after a July return.
    # The suspension gap must stay empty.
    s2016 = SeasonWindow(2016, dt.date(2016, 4, 3), dt.date(2016, 10, 2))
    dates_2016 = [dt.date(2016, 4, 10), dt.date(2016, 4, 20), dt.date(2016, 8, 1), dt.date(2016, 9, 30)]
    opened = [
        Transaction(dt.date(2016, 4, 3), "Marlins activated 2B X."),
        Transaction(dt.date(2016, 4, 29), "Marlins placed 2B X on the restricted list."),
    ]
    filled = compute_service_time(
        opened, [s2016], horizon_end=dt.date(2016, 10, 2), appearance_dates=dates_2016,
    )
    # April 3-28 from the transactions (~26 days) plus August 1-September 30
    # from the games (~61 days). The May-July suspension is not in either.
    days = filled.by_season[2016]["credited_days"]
    check("the suspension is not filled back in", 70 <= days <= 110)


def test_service_sanity_flags_both_failure_modes():
    import validate_service_sanity as sanity
    players = [
        {
            "id": 1, "name": "Played And Zero", "on_40_man": True,
            "season_stats": {"2025": {"hitting": {"gamesPlayed": 150}}},
            "seasons": [{"y": 2025, "d": 0}],
        },
        {
            "id": 2, "name": "Phantom Years", "on_40_man": False,
            "season_stats": {"2020": {"pitching": {"gamesPlayed": 5}}},
            "seasons": [{"y": 2020, "d": 96}, {"y": 2022, "d": 172}, {"y": 2023, "d": 172, "il": True}],
        },
        {
            "id": 3, "name": "Pre Feed", "on_40_man": False,
            "season_stats": {"2004": {"hitting": {"gamesPlayed": 162}}},
            "seasons": [{"y": 2004, "d": 0}],
        },
    ]
    report = sanity.summarize(players)
    check("the played zero-day season is flagged", report["zero_despite_games"] == 1)
    check("the phantom year is flagged and the IL year is not", report["days_after_last_season"] == 1)
    check("a pre-2009 zero is not this check's problem", report["zero_sample"][0][2] == 2025)


def test_moniak_override_is_an_extension():
    import salary_estimates as se
    overrides = se.load_contract_overrides()
    check("Mickey Moniak is in the hand-kept override file", 666160 in overrides)
    check("the override note says reported extension",
          overrides[666160].get("note") == "reported extension")
    check("has_contract_extension honors the file without a transaction",
          se.has_contract_extension(666160, overrides) is True)


if __name__ == "__main__":
    print("Running service_time.py tests...")
    test_single_full_season()
    test_option_and_recall()
    test_injured_list_still_accrues()
    test_multi_year_accumulation_and_free_agency()
    test_arbitration_boundary()
    test_status_carries_across_seasons_with_no_new_transactions()
    test_trade_does_not_stop_clock()
    test_pre_debut_events_do_not_start_the_clock()
    test_as_of_past_date_ignores_later_transactions()
    test_retired_player_clock_stops_at_final_season()
    test_active_player_is_unaffected_by_free_agency_elections()
    test_horizon_stops_at_today_not_end_of_season()
    test_impossible_total_is_detected()
    test_real_feed_wording_is_matched()
    test_major_league_signing_starts_the_clock_minor_league_does_not()
    test_injured_list_placement_restarts_a_stopped_clock()
    test_missing_seasons_is_measured_per_player_not_assumed_from_a_year()
    test_never_debuted_player_has_no_service_time()
    test_2020_season_is_prorated_to_a_full_year()
    test_2020_partial_season_scales_proportionally()
    test_normal_season_is_not_prorated()
    test_carry_in_credits_a_player_who_simply_never_left_the_roster()
    test_carry_in_is_closed_by_the_first_stop_it_meets()
    test_carry_in_ignores_pre_debut_transactions()
    test_carry_in_still_respects_the_accrual_ceiling()
    test_carry_in_is_inert_without_a_debut_date()
    test_carry_in_must_be_asked_for_at_the_api_level()
    test_pipeline_has_carry_in_on()
    test_season_provenance_is_classified_three_ways()
    test_season_teams_prefer_stats_then_transactions_then_carry_forward()
    test_profile_rows_sum_to_the_published_total()
    test_super_two_cutoff_is_the_top_22_percent_boundary()
    test_super_two_class_excludes_players_who_did_not_play_that_year()
    test_super_two_class_requires_86_days_not_just_any_accrual()
    test_super_two_rounds_the_qualifying_count_rather_than_flooring_it()
    test_super_two_requires_86_days_in_the_preceding_season()
    test_super_two_flags_only_the_two_to_three_year_band()
    test_load_reported_cutoff_falls_back_gracefully()
    test_cutoff_range_orders_low_and_high_and_handles_no_reported_figure()
    test_apply_super_two_sets_the_bubble_flag_from_a_reported_range()
    test_an_official_cutoff_decides_who_is_super_two()
    test_super_two_declines_to_guess_from_a_tiny_population()
    test_validate_super_two_counts_failures_and_would_exit_nonzero()
    test_qualifying_season_follows_the_season_being_played()
    test_qualifying_season_falls_back_early_in_a_season()
    test_latest_complete_season_is_measured_not_assumed()
    test_same_date_start_and_stop_do_not_depend_on_feed_order()
    test_claimed_and_optioned_the_same_day_credits_nothing()
    test_activated_off_the_il_then_optioned_stops_the_clock()
    test_no_interval_ever_ends_before_it_starts()
    test_a_player_who_never_debuted_gets_no_presumed_service()
    test_an_inferred_season_needs_something_to_corroborate_it()
    test_an_appearance_alone_is_enough_to_keep_a_presumed_season()
    test_a_minor_league_signing_does_not_extend_a_career()
    test_a_player_on_the_60_day_il_is_still_on_a_major_league_roster()
    test_the_ceiling_stops_a_player_who_left_for_the_minors()
    test_a_rule_5_return_closes_the_interval()
    test_returned_to_an_affiliate_is_also_a_stop()
    test_returned_to_the_active_roster_is_still_a_start()
    test_recalled_from_an_affiliate_is_not_a_return()
    test_an_empty_roster_fetch_is_refused()
    test_a_roster_that_collapses_overnight_is_refused()
    test_widespread_build_failures_are_refused()
    test_the_guard_is_inert_on_a_first_run()
    test_a_debuted_player_credited_nothing_is_reported()
    test_roster_days_before_the_first_game_are_credited()
    test_a_draft_signing_never_moves_the_floor()
    test_the_pre_debut_window_is_bounded()
    test_a_trade_out_of_a_dfa_reopens_the_clock()
    test_a_trade_out_of_an_option_does_not()
    test_a_slug_transliterates_an_accent_rather_than_dropping_it()
    test_a_player_page_is_only_published_for_a_rostered_player()
    test_the_site_url_follows_docs_cname()
    test_a_club_page_path_is_derived_the_same_way_as_a_player_page()
    test_a_breadcrumb_marks_the_current_page_as_the_last_step()
    test_no_crumb_still_says_all_players()
    test_the_club_directory_is_reachable_in_one_hop()
    test_lastmod_moves_only_when_a_page_actually_changes()
    test_a_missing_lastmod_manifest_degrades_to_the_old_behaviour()
    test_a_disagreement_is_labelled_by_the_moves_around_it()
    test_bereavement_and_paternity_accrue_but_the_restricted_list_does_not()
    test_a_bereavement_placement_does_not_stop_the_clock()
    test_the_roster_sweep_runs_end_to_end_without_touching_the_network()
    test_a_waiver_claim_followed_straight_by_a_recall_is_a_minor_league_stint()
    test_a_claim_with_a_move_in_between_is_not_the_same_shape()
    test_the_broad_version_of_the_recall_rule_stays_rejected()
    test_the_claim_recall_rule_is_wired_into_the_interval_walk()
    test_the_rule_only_trims_the_interval_the_claim_itself_opened()
    test_a_trade_straight_to_a_recall_is_also_a_minor_league_stint()
    test_the_stint_window_does_not_depend_on_row_order()
    test_a_trade_onto_an_active_roster_is_untouched()
    test_the_default_ruleset_reproduces_the_constants_the_engine_shipped_with()
    test_an_engine_function_computes_under_the_ruleset_it_is_given()
    test_free_agency_and_arbitration_thresholds_come_from_the_ruleset()
    test_super_two_can_be_switched_off_by_a_ruleset()
    test_the_2027_placeholder_refuses_to_compute()
    test_a_value_nobody_has_checked_cannot_be_published_by_accident()
    test_every_ruleset_value_carries_a_source()
    test_the_explainer_prose_follows_the_ruleset_not_a_literal()
    test_the_structured_data_and_the_visible_prose_agree_on_a_season()
    test_a_season_window_is_fetched_once_not_once_per_player()
    test_an_estimated_season_window_stops_the_run_from_publishing()
    test_both_published_files_carry_the_cba_rules_block()
    test_nothing_published_claims_a_transaction_coverage_cutoff_year()
    test_a_debuted_player_is_never_described_as_never_having_been_up()
    test_salary_estimate_shown_for_clearly_pre_arb_with_no_extension()
    test_known_extension_suppresses_the_salary_estimate()
    test_a_transaction_mentioning_an_extension_suppresses_the_estimate()
    test_no_salary_section_outside_clearly_pre_arbitration()
    test_salary_block_wording_matches_estimate_vs_extension()
    test_the_profile_overlay_fetches_and_renders_the_same_salary_data()
    test_statlines_are_read_from_the_bio_the_pipeline_already_fetches()
    test_a_malformed_or_empty_bio_yields_no_statline_rather_than_an_error()
    test_a_mid_season_trade_keeps_the_combined_total_and_each_club()
    test_the_stats_tab_states_that_war_and_salary_are_absent()
    test_the_super_two_prose_quotes_published_figures_not_our_own()
    test_an_unpublished_cutoff_is_called_a_prediction_not_a_fact()
    test_the_page_does_not_claim_a_finished_season_is_still_being_played()
    test_arbitration_eligible_selection_boundaries()
    test_super_two_class_selection_boundaries()
    test_super_two_status_labels_ties_and_boundaries()
    test_super_two_status_labels_with_a_real_range()
    test_an_extended_player_leaves_the_arbitration_and_non_tender_lists()
    test_the_super_two_page_shows_the_range_and_three_groups()
    test_arbitration_eligible_badge_distinguishes_the_bubble()
    test_the_offseason_pages_are_added_to_the_sitemap_and_workflows()
    test_non_tender_watch_selection_states_its_rule_and_picks_expected_players()
    test_free_agents_by_year_boundary_at_exactly_six_years()
    test_club_offseason_subpages_have_correct_counts_titles_and_descriptions()
    test_write_club_pages_produces_three_subpages_per_club_with_right_totals()
    test_the_site_header_is_the_same_on_every_page()
    test_the_tab_row_matches_the_approved_phone_and_desktop_sets()
    test_the_sheet_lists_every_required_row_and_never_the_shelved_tools()
    test_every_club_in_the_sheet_links_to_a_real_club_page()
    test_the_header_marks_the_right_tab_current_and_only_one()
    test_no_banned_string_appears_in_the_header_or_sheet()
    test_the_footer_is_the_same_on_every_page()
    test_no_banned_string_appears_in_the_footer()
    test_no_page_in_docs_links_to_the_repo()
    test_the_404_template_carries_the_site_header()
    test_the_daily_service_time_tick_is_not_a_content_change()
    test_only_alumni_with_a_defensible_figure_are_published()
    test_a_published_alumnus_is_reachable_and_off_the_club_pages()
    test_a_player_who_leaves_the_40man_keeps_his_page()
    test_a_previously_published_page_is_never_dropped()
    test_the_homepage_has_no_dead_links()
    test_the_sitemap_is_an_index_split_by_section()
    test_the_milestone_sentence_is_a_floor_derived_from_the_ruleset()
    test_the_milestone_sentence_follows_the_ruleset_not_a_literal()
    test_teammates_are_linked_but_never_a_retired_players_stale_club()
    test_key_players_on_a_club_page_are_grouped_by_threshold_not_by_day_count()
    test_the_svc_meter_split_figure_is_normalised_like_every_other_figure()
    test_the_homepage_carries_real_links_into_a_handful_of_player_pages()
    test_a_season_window_starts_at_each_clubs_own_opener()
    test_a_season_ends_on_the_last_game_actually_played()
    test_a_resumed_game_relisted_under_a_later_date_does_not_extend_the_season()
    test_a_short_option_under_twenty_days_counts_as_service()
    test_an_option_of_twenty_days_or_more_is_not_credited()
    test_the_option_credit_is_capped_at_the_full_year()
    test_a_spring_option_counts_from_the_clubs_opening_day()
    test_an_option_never_recalled_counts_through_season_end()
    test_a_dfa_gap_is_not_an_optional_assignment()
    test_a_roy_full_year_finish_raises_a_short_season()
    test_roy_full_year_only_raises_never_lowers()
    test_the_roy_loader_rejects_an_unsourced_or_malformed_entry()
    test_the_roy_list_is_not_treated_as_a_ruleset_version()
    test_a_traded_players_start_is_his_opening_day_club_not_the_latest_opener()
    test_every_page_carries_exactly_one_ga_tag_and_one_adsense_block()
    test_ads_txt_is_exact()
    test_every_page_carries_the_required_footer_disclaimer_and_links()
    test_the_homepage_puts_numbers_before_the_long_explanation()
    test_the_five_pages_carry_an_original_intro()
    test_about_page_stays_high_level()
    test_no_page_in_docs_links_or_names_the_raw_statsapi_endpoint()
    test_fetchers_send_an_honest_user_agent_and_use_the_shared_pause()
    test_mlb_fetch_enabled_false_skips_the_api_and_still_builds()
    test_a_restricted_list_placement_stops_the_clock_by_default()
    test_a_dfa_in_season_keeps_the_clock_running_until_the_next_move()
    test_a_dfa_resolved_the_same_day_credits_no_limbo()
    test_a_spring_dfa_does_not_credit_regular_season_limbo()
    test_a_service_time_exception_can_restore_or_remove_days()
    test_the_service_time_exceptions_file_loads_and_validates()
    test_a_legacy_cache_without_team_ids_is_discarded_or_ignored()
    test_new_transaction_fetches_store_type_dates_and_id_old_rows_still_read()
    test_a_bare_signing_beside_a_minor_league_deal_does_not_open_a_stint()
    test_a_minor_league_assignment_closes_an_open_signing()
    test_same_day_free_agency_cancels_the_activation()
    test_reassigned_to_the_minors_is_a_stop()
    test_appearances_fill_a_season_the_transactions_left_closed()
    test_service_sanity_flags_both_failure_modes()
    test_moniak_override_is_an_extension()
    test_rules_version_is_ten()
    test_free_agency_and_same_day_trades_on_five_cached_careers()
    test_an_mlb_club_assignment_restarts_a_stopped_clock()
    test_the_dfa_grace_window_is_a_sourced_ruleset_value()
    test_the_player_level_v7_figures_hold_against_the_real_feed()
    test_a_tiebreaker_game_does_not_set_the_league_season_end()
    test_the_2018_tiebreakers_end_the_league_season_on_09_30()
    test_the_cba_uniform_start_applies_to_every_club_except_an_overseas_opener()
    test_an_early_domestic_outlier_does_not_become_the_uniform_start()
    test_a_tiebreaker_clubs_player_window_runs_a_day_longer()
    test_soto_accepted_delta_reflects_the_boundary_fix()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
