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
    test_never_debuted_player_has_no_service_time()
    test_2020_season_is_prorated_to_a_full_year()
    test_2020_partial_season_scales_proportionally()
    test_normal_season_is_not_prorated()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
