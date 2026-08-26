"""
Lightweight unit tests for service_time.py. No pytest dependency required --
run directly with `python tests/test_service_time.py`.
"""

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from service_time import (  # noqa: E402
    SeasonWindow,
    is_active_start,
    is_active_stop,
    roster_start_before_debut,
    Transaction,
    compute_service_time,
    days_in_intervals,
    build_global_active_intervals,
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

    A career usually ends with "elected free agency", which is deliberately
    NOT a stop keyword (see the note in service_time.py -- treating it as one
    breaks 272 active players). So the final interval stays open and, without
    a ceiling, runs to horizon_end: Angel Guzman, who last pitched in 2010,
    came out at 20.030 years.
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
        "without a ceiling a retired player accrues absurd service time",
        uncapped.total_years >= 15,
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
    The other half of the same finding: a player who elects free agency and
    re-signs must keep accruing. There is no start keyword for the re-signing
    ("Team signed free agent RHP X" -- "signed" can't be one, minor league
    deals use it too), so if the election ever became a stop keyword this
    player would freeze at his first election and never restart.
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
        "free agency election does not stop an active player's clock",
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
# published 20.002.


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
# "86 days into your third year" as the whole test; 86 days is really a
# secondary condition on top of a top-22% ranking of the league-wide class,
# which is only computable now that the database holds every player.


def _s2_player(pid, name, rows):
    return {"id": pid, "name": name, "seasons": [{"y": y, "d": d} for y, d in rows],
            "service_days_total": sum(d for _, d in rows)}


def _s2_league(n, year=2025):
    """n players spread evenly across the 2.000-3.000 band."""
    db = {}
    for i in range(n):
        # 345..514, so every player accrues at least one day in `year` and is
        # therefore in that year's class.
        days = 2 * 172 + 1 + (i * 169) // max(n - 1, 1)
        db[str(i)] = _s2_player(i, f"P{i:03d}", [(year - 2, 172), (year - 1, 172), (year, days - 344)])
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


def test_super_two_declines_to_guess_from_a_tiny_population():
    """
    A partial dataset would otherwise produce a confident-looking cutoff
    drawn through a handful of players.
    """
    import super_two as S  # noqa: PLC0415

    check("no cutoff from 10 players", S.compute_cutoff(_s2_league(10), 2025) is None)


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
    out = _crumbs(("All players", "../"), ("Club", "../t/club.html"), ("Player", None))
    check("earlier steps are links", '<a href="../">All players</a>' in out)
    check("the current page is not a link", "<b>Player</b>" in out)
    check("and it is the last one", out.rstrip().endswith("<b>Player</b></nav>"))


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
    test_super_two_requires_86_days_in_the_preceding_season()
    test_super_two_flags_only_the_two_to_three_year_band()
    test_super_two_declines_to_guess_from_a_tiny_population()
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
    test_the_club_directory_is_reachable_in_one_hop()
    test_lastmod_moves_only_when_a_page_actually_changes()
    test_a_missing_lastmod_manifest_degrades_to_the_old_behaviour()
    test_a_disagreement_is_labelled_by_the_moves_around_it()
    test_bereavement_and_paternity_accrue_but_the_restricted_list_does_not()
    test_a_bereavement_placement_does_not_stop_the_clock()
    test_the_roster_sweep_runs_end_to_end_without_touching_the_network()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
