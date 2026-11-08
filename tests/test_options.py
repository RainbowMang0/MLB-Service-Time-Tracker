"""Option years from saved transaction histories.

No pytest. The day totals below are checked against the cached rows for the
players worked by hand. A season is used at the ruleset threshold. An option
after the regular season does not count. The fourth-option exception is not
applied, and a career that starts before the saved record is unknown.

    python3 tests/test_options.py
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import write_player_pages as pages  # noqa: E402
from il_stints import SeasonWindows  # noqa: E402
from options import (  # noqa: E402
    count_option_years,
    player_line,
    record_is_incomplete,
    summarize,
)

PASS = 0
FAIL = 0
TODAY = dt.date(2026, 11, 8)
CACHE = ROOT / "data" / "cache" / "transactions"
WINDOWS = SeasonWindows.load()

# Worked from the cached rows. Years, not a day-for-day copy: a one-day
# difference in a season window must not flip a year that is nowhere near
# the threshold, and these are not.
EXPECTED = {
    641329: {"name": "Bryan Baker", "used": [2021, 2023, 2024], "saved": [], "out": True, "remaining": 0},
    663698: {"name": "Joey Bart", "used": [2021, 2022, 2023], "saved": [], "out": True, "remaining": 0},
    683146: {"name": "Brett Baty", "used": [2023, 2024], "saved": [2025], "out": False, "remaining": 1},
    661563: {"name": "Luis Gil", "used": [2020, 2021, 2022, 2026], "saved": [], "out": True, "remaining": 0},
    671056: {"name": "Iván Herrera", "used": [2021, 2022, 2023, 2024], "saved": [], "out": True, "remaining": 0},
    650911: {"name": "Cristopher Sánchez", "used": [2020, 2021, 2022, 2023], "saved": [], "out": True, "remaining": 0},
    665645: {"name": "Kervin Castro", "used": [2021, 2022, 2026], "saved": [], "out": True, "remaining": 0},
    650960: {"name": "Daniel Duarte", "used": [2023, 2026], "saved": [2022], "out": False, "remaining": 1},
}


def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def _player(pid):
    """Just this player's stored record. The database is not printed."""
    data = json.loads((ROOT / "docs" / "data" / "service_time.json").read_text())
    for player in data["players"]:
        if player.get("id") == pid:
            return player
    raise AssertionError(f"player {pid} is not in the database")


def _txns(pid):
    return json.loads((CACHE / f"{pid}.json").read_text())


def test_hand_worked_players():
    for pid, expect in EXPECTED.items():
        player = _player(pid)
        count = summarize(player, _txns(pid), WINDOWS, TODAY)
        label = expect["name"]
        check(f"{label} is not marked incomplete", not count.incomplete)
        check(f"{label} used {expect['used']}", count.used_years == expect["used"])
        check(f"{label} saved {expect['saved']}", count.saved_years == expect["saved"])
        check(f"{label} remaining {expect['remaining']}", count.remaining == expect["remaining"])
        check(f"{label} out of options is {expect['out']}", count.out_of_options is expect["out"])
        line = player_line(count)
        years = ", ".join(str(year) for year in expect["used"])
        check(f"{label} line names the seasons", years in line)
        check(f"{label} line names the remaining count", f"Remaining: {expect['remaining']}" in line)
        if expect["out"]:
            check(f"{label} line says out of options", "Out of options." in line)
        else:
            check(f"{label} line does not say out of options", "Out of options" not in line)
        check(f"{label} line does not mention a fourth option", "4th" not in line and "fourth" not in line.lower())


def test_baker_page_line():
    player = _player(641329)
    count = summarize(player, _txns(641329), WINDOWS, TODAY)
    html = pages.render(player, {}, "2026-11-08T00:00:00+00:00", [], _txns(641329), None, count)
    check(
        "Baker's page says 2021, 2023, 2024 and out of options",
        "Option years used: 3 (2021, 2023, 2024). Remaining: 0. Out of options." in html,
    )
    check("Baker's page does not mention a fourth option", "4th" not in html and "fourth option" not in html.lower())


def test_a_short_stay_is_not_a_used_year_and_a_postseason_option_does_not_count():
    windows = SeasonWindows.load()
    # 2024 window opens 2024-03-28 and closes 2024-09-30.
    rows = [
        {
            "date": "2024-04-01",
            "description": "Baltimore Orioles optioned RHP Test Player to Norfolk Tides.",
            "from_team_id": 110,
            "to_team_id": 568,
        },
        {
            "date": "2024-04-16",
            "description": "Baltimore Orioles recalled RHP Test Player from Norfolk Tides.",
            "from_team_id": 568,
            "to_team_id": 110,
        },
        {
            "date": "2024-10-02",
            "description": "Baltimore Orioles optioned RHP Test Player to Norfolk Tides.",
            "from_team_id": 110,
            "to_team_id": 568,
        },
    ]
    days, _inferred = count_option_years(rows, windows, TODAY)
    check("fifteen championship days are counted", days.get(2024) == 15)
    check("the October option adds no season", 2025 not in days)
    player = {
        "id": 1, "name": "Test Player", "mlb_debut": "2024-04-10",
        "history_complete": True, "missing_seasons": 0,
    }
    count = summarize(player, rows, windows, TODAY)
    check("fifteen days does not use the year", count.used_years == [])
    check("the year is saved", count.saved_years == [2024])
    check("three remain", count.remaining == 3 and count.out_of_options is False)

    rows[1]["date"] = "2024-04-21"
    count = summarize(player, rows, windows, TODAY)
    check("twenty days uses the year", count.used_years == [2024] and count.remaining == 2)


def test_a_spring_option_starts_on_opening_day_and_opening_day_recall_is_free():
    rows = [
        {
            "date": "2024-03-03",
            "description": "New York Yankees optioned RHP Test Player to Scranton.",
            "from_team_id": 147,
            "to_team_id": 531,
        },
        {
            "date": "2024-03-28",
            "description": "New York Yankees recalled RHP Test Player from Scranton.",
            "from_team_id": 531,
            "to_team_id": 147,
        },
    ]
    days, _inferred = count_option_years(rows, WINDOWS, TODAY)
    check("recalled on Opening Day uses no days", days.get(2024, 0) == 0)


def test_a_career_that_starts_before_the_record_is_unknown():
    player = {
        "id": 2, "name": "Early Debut", "mlb_debut": "2012-04-06",
        "history_complete": False, "missing_seasons": 4,
    }
    rows = [{
        "date": "2016-04-10",
        "description": "New York Yankees optioned OF Early Debut to Scranton.",
        "from_team_id": 147,
    }]
    check("the record is incomplete", record_is_incomplete(player, rows))
    count = summarize(player, rows, WINDOWS, TODAY)
    check("remaining is not guessed", count.incomplete and count.remaining is None and count.out_of_options is None)
    check("the line says unknown", player_line(count) == "Option years: unknown.")
    check("no cache is unknown", record_is_incomplete(player, None))
    page = pages.render(player, {}, "2026-11-08T00:00:00+00:00", [], None)
    check("a page with no record says unknown", "Option years: unknown." in page)


def test_the_options_page_lists_the_two_groups_and_hides_the_machinery():
    baker = _player(641329)
    baty = _player(683146)
    early = {
        "id": 2, "name": "Early Debut", "team": "New York Yankees", "position": "OF",
        "on_40_man": True, "mlb_debut": "2012-04-06", "history_complete": False,
        "missing_seasons": 4, "service_time": "8.000", "service_days_total": 1376,
    }
    counts = {
        baker["id"]: summarize(baker, _txns(baker["id"]), WINDOWS, TODAY),
        baty["id"]: summarize(baty, _txns(baty["id"]), WINDOWS, TODAY),
        early["id"]: summarize(early, _txns(641329), WINDOWS, TODAY),
    }
    # Early Debut's own rows are not what makes him unknown; the stored flags are.
    html = pages.render_options([baker, baty, early], counts, "2026-11-08T00:00:00+00:00")
    check("the page names the search", "out of options" in html.lower())
    check("Baker is listed", "Bryan Baker" in html and "2021, 2023, 2024" in html)
    check("Baty is listed with one left", "Brett Baty" in html and 'data-status="one"' in html)
    check("Baker is out of options on the page", 'data-status="out"' in html)
    check("an incomplete player is not listed", "Early Debut" not in html)
    check("the incomplete count is on the page", ">1<" in html or ">1</b>" in html)
    for banned in (
        "4th option", "fourth option", "may have a 4th", "scripts/", ".py",
        "Stats API", "transaction feed",
    ):
        check(f"options page does not say {banned!r}", banned not in html)
    check("the page says public data", "public data" in html)
    check("filters and sort headers are present", 'id="opt-controls"' in html and 'data-key="team"' in html)
    check("the longer explanation starts collapsed", "<details" in html and "open" not in html.split("<details", 1)[1].split(">", 1)[0])


def main():
    test_hand_worked_players()
    test_baker_page_line()
    test_a_short_stay_is_not_a_used_year_and_a_postseason_option_does_not_count()
    test_a_spring_option_starts_on_opening_day_and_opening_day_recall_is_free()
    test_a_career_that_starts_before_the_record_is_unknown()
    test_the_options_page_lists_the_two_groups_and_hides_the_machinery()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
