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


if __name__ == "__main__":
    print("Running service_time.py tests...")
    test_single_full_season()
    test_option_and_recall()
    test_injured_list_still_accrues()
    test_multi_year_accumulation_and_free_agency()
    test_arbitration_boundary()
    test_status_carries_across_seasons_with_no_new_transactions()
    test_trade_does_not_stop_clock()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
