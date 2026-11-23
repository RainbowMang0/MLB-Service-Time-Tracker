"""Rookie eligibility from fixtures. No live roster.

    python3 tests/test_rookie_status.py
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rookie_status as rook  # noqa: E402
import rookie_status_pages as pages  # noqa: E402
import table_html  # noqa: E402
from il_stints import SeasonWindows  # noqa: E402

PASS = 0
FAIL = 0
TODAY = dt.date(2026, 11, 23)
CLUB = 147


def check(label, ok):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL  {label}")


def _bounds(start, end):
    return {
        "start": dt.date.fromisoformat(start),
        "end": dt.date.fromisoformat(end),
        "clubs": {},
        "ends": {},
    }


def windows():
    return SeasonWindows({
        2019: _bounds("2019-03-28", "2019-09-29"),
        2020: _bounds("2020-07-24", "2020-09-27"),
        2024: _bounds("2024-03-28", "2024-09-29"),
        2025: _bounds("2025-03-27", "2025-09-28"),
        2026: _bounds("2026-03-26", "2026-09-27"),
    })


def person(**kwargs):
    base = {
        "id": 101,
        "name": "Pat Fixture",
        "team": "New York Yankees",
        "on_40_man": True,
        "position": "OF",
        "mlb_debut": None,
        "season_stats": {},
        "seasons": [],
        "service_days_total": 0,
    }
    base.update(kwargs)
    return base


def txn(day, desc):
    return {
        "date": day,
        "description": desc,
        "team_id": CLUB,
        "from_team_id": CLUB,
        "to_team_id": CLUB,
    }


def judge(player, rows):
    return rook.evaluate(player, rows, TODAY, windows())


def clock(player, rows, through=2026):
    return rook.active_clock(player, rows, TODAY, windows(), through)


def _cup():
    return [
        txn("2026-09-01", "New York Yankees recalled OF Pat Fixture from Scranton."),
        txn("2026-09-05", "New York Yankees optioned OF Pat Fixture to Scranton."),
    ]


def test_thresholds_are_exceeded_not_reached():
    at_limit = person(
        mlb_debut="2026-09-01",
        season_stats={"2026": {"hitting": {"atBats": 130, "gamesPlayed": 40}}},
        seasons=[{"y": 2026, "d": 4, "t": [CLUB]}],
    )
    over = person(
        mlb_debut="2026-09-01",
        season_stats={"2026": {"hitting": {"atBats": 131, "gamesPlayed": 40}}},
        seasons=[{"y": 2026, "d": 4, "t": [CLUB]}],
    )
    check("130 at-bats is still a rookie", judge(at_limit, _cup()).state == "eligible")
    check("131 at-bats uses up eligibility in 2026", judge(over, _cup()).state == "graduated")
    check("the at-bat limit is what crossed", judge(over, _cup()).crossed == ("at_bats",))
    ip_even = person(
        mlb_debut="2026-09-01", position="P",
        season_stats={"2026": {"pitching": {"inningsPitched": "50.0", "gamesPlayed": 10}}},
        seasons=[{"y": 2026, "d": 4, "t": [CLUB]}],
    )
    ip_over = person(
        mlb_debut="2026-09-01", position="P",
        season_stats={"2026": {"pitching": {"inningsPitched": "50.1", "gamesPlayed": 10}}},
        seasons=[{"y": 2026, "d": 4, "t": [CLUB]}],
    )
    check("50.0 innings is still a rookie", judge(ip_even, _cup()).state == "eligible")
    check("50.1 innings uses up eligibility", judge(ip_over, _cup()).state == "graduated")
    check("innings are what crossed", "innings" in judge(ip_over, _cup()).crossed)


def test_active_days_exclude_the_injured_list_and_stop_at_45():
    under = [
        txn("2026-08-01", "New York Yankees recalled OF Pat Fixture from Scranton."),
        txn("2026-09-15", "New York Yankees optioned OF Pat Fixture to Scranton."),
    ]
    over = [
        txn("2026-08-01", "New York Yankees recalled OF Pat Fixture from Scranton."),
        txn("2026-09-16", "New York Yankees optioned OF Pat Fixture to Scranton."),
    ]
    player = person(
        mlb_debut="2026-08-01",
        season_stats={"2026": {"hitting": {"atBats": 20, "gamesPlayed": 15}}},
        seasons=[{"y": 2026, "d": 50, "t": [CLUB]}],
    )
    stayed = judge(player, under)
    lost = judge(player, over)
    check("45 active days is still a rookie", stayed.state == "eligible" and stayed.days_exact == 45)
    check("46 active days uses up eligibility", lost.state == "graduated" and lost.days_exact == 46)
    check("active days are what crossed", lost.crossed == ("days",))
    # Service time would count the injured-list days in the middle. This clock must not.
    split = [
        txn("2026-04-01", "New York Yankees recalled OF Pat Fixture from Scranton."),
        txn("2026-04-10", "New York Yankees placed OF Pat Fixture on the 10-day injured list."),
        txn("2026-04-20", "New York Yankees activated OF Pat Fixture from the 10-day injured list."),
        txn("2026-04-30", "New York Yankees optioned OF Pat Fixture to Scranton."),
    ]
    il_player = person(
        mlb_debut="2026-04-01",
        service_days_total=29,
        season_stats={"2026": {"hitting": {"atBats": 8, "gamesPlayed": 8}}},
        seasons=[{"y": 2026, "d": 29, "t": [CLUB]}],
    )
    status = judge(il_player, split)
    check("injured-list days are not active days", status.days_exact == 19)
    check("a large service-time total does not decide it", status.state == "eligible")


def test_september_counts_from_2020_on():
    rows = [
        txn("2019-08-20", "New York Yankees recalled OF Pat Fixture from Scranton."),
        txn("2019-09-10", "New York Yankees optioned OF Pat Fixture to Scranton."),
    ]
    player = person(
        mlb_debut="2019-08-20",
        season_stats={"2019": {"hitting": {"atBats": 10, "gamesPlayed": 8}}},
        seasons=[{"y": 2019, "d": 22, "t": [CLUB]}],
    )
    counted = clock(player, rows)
    check("September 2019 does not count", counted.low() == 12 and counted.high() == 12)
    long_2020 = [
        txn("2020-07-24", "New York Yankees recalled OF Pat Fixture from Scranton."),
        txn("2020-09-10", "New York Yankees optioned OF Pat Fixture to Scranton."),
    ]
    rookie_2020 = person(
        mlb_debut="2020-07-24",
        season_stats={"2020": {"hitting": {"atBats": 40, "gamesPlayed": 30}, "pitching": {"inningsPitched": "10.0"}}},
        seasons=[{"y": 2020, "d": 48, "t": [CLUB]}],
    )
    status = judge(rookie_2020, long_2020)
    bare = rook.active_clock(rookie_2020, long_2020, TODAY, windows(), 2026, count_2020_september=False)
    check("September 2020 does count", status.days_exact == 48)
    check("without September 2020 the same stint is under 45", bare.low() == 39)
    check("2020 at-bats and innings are not prorated", status.state == "veteran")
    check("September 2020 is what changes the result", status.september_2020)


def test_pre_debut_roster_days_count_and_a_gap_is_not_guessed():
    rows = [
        txn("2026-04-01", "New York Yankees selected the contract of OF Pat Fixture from Scranton."),
        txn("2026-04-20", "New York Yankees optioned OF Pat Fixture to Scranton."),
    ]
    player = person(mlb_debut="2026-04-10", seasons=[{"y": 2026, "d": 10, "t": [CLUB]}])
    status = judge(player, rows)
    check("days before the debut still count", status.days_exact == 19)
    missing = person(mlb_debut="2026-04-10", seasons=[{"y": 2026, "d": 10, "t": [CLUB]}])
    unknown = judge(missing, None)
    check("no transaction record is unconfirmed", unknown.state == "unconfirmed")
    blank = person()
    check("a player with no major league time is a rookie", judge(blank, None).state == "eligible")
    check("that rookie has no counting stats", judge(blank, None).days_exact == 0)


def test_retroactive_il_and_paternity_and_same_day_option():
    retro = [
        txn("2026-04-01", "New York Yankees recalled OF Pat Fixture from Scranton."),
        txn(
            "2026-04-10",
            "New York Yankees placed OF Pat Fixture on the 10-day injured list retroactive to April 7, 2026.",
        ),
        txn("2026-04-20", "New York Yankees optioned OF Pat Fixture to Scranton."),
    ]
    player = person(
        mlb_debut="2026-04-01",
        season_stats={"2026": {"hitting": {"atBats": 4, "gamesPlayed": 4}}},
        seasons=[{"y": 2026, "d": 20, "t": [CLUB]}],
    )
    check("a retroactive injured-list date is not active", judge(player, retro).days_exact == 6)
    leave = [
        txn("2026-04-01", "New York Yankees recalled OF Pat Fixture from Scranton."),
        txn("2026-04-10", "New York Yankees placed OF Pat Fixture on the paternity list."),
        txn("2026-04-13", "New York Yankees reinstated OF Pat Fixture from the paternity list."),
        txn("2026-04-20", "New York Yankees optioned OF Pat Fixture to Scranton."),
    ]
    check("paternity days are not active days", judge(player, leave).days_exact == 16)
    quiet = person(
        mlb_debut="2026-04-01",
        season_stats={"2026": {"hitting": {"atBats": 0, "gamesPlayed": 0}}},
        seasons=[{"y": 2026, "d": 0, "t": [CLUB]}],
    )
    same = [
        txn("2026-04-01", "New York Yankees recalled OF Pat Fixture from Scranton."),
        txn("2026-04-01", "New York Yankees optioned OF Pat Fixture to Scranton."),
    ]
    check("a same-day option does not count a day", judge(quiet, same).days_exact == 0)


def test_a_service_year_without_a_stat_line_is_zero_when_he_was_not_active():
    player = person(
        mlb_debut="2024-05-01",
        season_stats={
            "2024": {"hitting": {"atBats": 40, "gamesPlayed": 20}},
            "2026": {"hitting": {"atBats": 20, "gamesPlayed": 10}},
        },
        seasons=[
            {"y": 2024, "d": 20, "t": [CLUB]},
            {"y": 2025, "d": 1, "t": [CLUB]},
            {"y": 2026, "d": 10, "t": [CLUB]},
        ],
    )
    rows = [
        txn("2024-05-01", "New York Yankees recalled OF Pat Fixture from Scranton."),
        txn("2024-05-10", "New York Yankees optioned OF Pat Fixture to Scranton."),
        txn("2026-08-01", "New York Yankees recalled OF Pat Fixture from Scranton."),
        txn("2026-08-08", "New York Yankees optioned OF Pat Fixture to Scranton."),
    ]
    status = judge(player, rows)
    check("a gap year with no active days does not hide the other seasons", status.at_bats == 60)
    check("he stays a rookie", status.state == "eligible")


def test_graduated_means_the_latest_season_and_only_that_season():
    earlier = person(
        mlb_debut="2024-04-01",
        season_stats={"2024": {"hitting": {"atBats": 140, "gamesPlayed": 40}}},
        seasons=[{"y": 2024, "d": 80, "t": [CLUB]}],
    )
    this_year = person(
        mlb_debut="2025-09-01",
        season_stats={
            "2025": {"hitting": {"atBats": 40, "gamesPlayed": 20}},
            "2026": {"hitting": {"atBats": 100, "gamesPlayed": 40}},
        },
        seasons=[{"y": 2025, "d": 20, "t": [CLUB]}, {"y": 2026, "d": 40, "t": [CLUB]}],
    )
    stint = [
        txn("2025-09-01", "New York Yankees recalled OF Pat Fixture from Scranton."),
        txn("2025-09-10", "New York Yankees optioned OF Pat Fixture to Scranton."),
    ]
    check("an earlier limit is not 'used up in 2026'", judge(earlier, []).state == "veteran")
    crossed = judge(this_year, stint)
    check("2026 is the season that crosses 130", crossed.state == "graduated" and crossed.at_bats == 140)


def test_a_short_uncertain_tail_stays_under_and_a_long_one_does_not():
    short = [
        txn("2026-09-01", "New York Yankees recalled OF Pat Fixture from Scranton."),
        txn("2026-09-20", "New York Yankees placed OF Pat Fixture on the 10-day injured list."),
    ]
    player = person(
        mlb_debut="2026-09-01",
        season_stats={"2026": {"hitting": {"atBats": 12, "gamesPlayed": 10}}},
        seasons=[{"y": 2026, "d": 27, "t": [CLUB]}],
    )
    status = judge(player, short)
    check("a tail that cannot reach 46 is still a rookie", status.state == "eligible")
    check("the day text keeps the range", pages.days_text(status) == "19-26")
    long = [
        txn("2026-04-01", "New York Yankees recalled OF Pat Fixture from Scranton."),
        txn("2026-04-10", "New York Yankees placed OF Pat Fixture on the 10-day injured list."),
    ]
    ambiguous = person(
        mlb_debut="2026-04-01",
        season_stats={"2026": {"hitting": {"atBats": 8, "gamesPlayed": 8}}},
        seasons=[{"y": 2026, "d": 100, "t": [CLUB]}],
    )
    check("a tail that can cross 45 is unconfirmed", judge(ambiguous, long).state == "unconfirmed")


def test_the_player_line_and_the_page_use_fixtures():
    rookie = person(
        id=101, mlb_debut="2026-08-01",
        season_stats={"2026": {"hitting": {"atBats": 12, "gamesPlayed": 10}}},
        seasons=[{"y": 2026, "d": 20, "t": [CLUB]}],
    )
    rows = [
        txn("2026-08-01", "New York Yankees recalled OF Pat Fixture from Scranton."),
        txn("2026-08-20", "New York Yankees optioned OF Pat Fixture to Scranton."),
    ]
    line = pages.player_line(rookie, rows, TODAY)
    check("a supported rookie gets one line", "Rookie eligible: 12 AB, 0.0 IP, 19 active days." in line)
    check("the line links the league page", 'href="/rookie-status.html"' in line)
    veteran = person(
        id=102, name="Vin Veteran", mlb_debut="2018-04-01",
        season_stats={"2018": {"hitting": {"atBats": 400, "gamesPlayed": 140}}},
        seasons=[{"y": 2018, "d": 150, "t": [CLUB]}],
    )
    check("a player past the limits gets no line", pages.player_line(veteran, [], TODAY) == "")
    unknown = person(id=103, name="Uma Unknown", mlb_debut="2026-05-01", on_40_man=True)
    check("an unconfirmed 40-man player says so", "unconfirmed" in pages.player_line(unknown, None, TODAY))
    unknown["on_40_man"] = False
    check("an unconfirmed former player gets no line", pages.player_line(unknown, None, TODAY) == "")

    eligible = judge(rookie, rows)
    graduated = judge(person(
        id=104, name="Gina Graduate", mlb_debut="2026-04-01", position="P",
        season_stats={"2026": {"pitching": {"inningsPitched": "51.0", "gamesPlayed": 12}}},
        seasons=[{"y": 2026, "d": 60, "t": [CLUB]}],
    ), [])
    pending = judge(unknown, None)
    grouped = {
        "eligible": [(rookie, eligible)],
        "graduated": [(person(
            id=104, name="Gina Graduate", team="New York Yankees", position="P", on_40_man=True,
        ), graduated)],
        "unconfirmed": [(unknown, pending)],
        "affected": [],
    }
    raw = pages.render(grouped, "2026-11-23T00:00:00+00:00")
    final = table_html.wrap_bare_tables(raw)
    check("one h1", raw.count("<h1>") == 1)
    check("the intro is one sentence", raw.split('<p class="subtitle">')[1].split("</p>")[0].count(".") == 1)
    check("the player link is present", 'href="/p/101-pat-fixture.html"' in final)
    check("the club link is present", 'href="/t/new-york-yankees.html"' in final)
    check("wide tables scroll", 'class="tbl-scroll"' in final and "Swipe sideways" in final)
    check("no last-updated line", "last updated" not in final.lower())
    lowered = final.lower()
    for banned in ("baseball-reference", "fangraphs", "spotrac", "mlb trade rumors", "cot's"):
        check(f"no {banned}", banned not in lowered)
    check("says public data", "public data" in lowered)
    check("not an official status", "not an official mlb status" in lowered)
    check("links the service-time explainer", 'href="/service-time.html"' in final)
    check("has the 2026 section", "Used up in 2026" in final)
    check("has the unconfirmed section", ">Unconfirmed<" in final)
    check("graduated innings show", "51.0" in final)


def test_titles_fit_and_the_pipeline_names_the_page():
    import page_titles
    title = page_titles.rookie_title()
    meta = page_titles.rookie_meta(120, 30, 2026)
    check("title fits", len(title) <= page_titles.TITLE_MAX and title.startswith("MLB rookie eligibility"))
    check("meta fits", len(meta) <= page_titles.META_MAX and "public data" in meta)
    text = (ROOT / "scripts" / "write_player_pages.py").read_text(encoding="utf-8")
    check("the roster rebuild writes the page", "rookie_status_pages.write" in text)
    check("the sitemap names the page", "rookie-status.html" in text)
    for name in ("update-service-time.yml", "backfill-history.yml"):
        body = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        check(f"{name} commits the page", "docs/rookie-status.html" in body)
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    check("ci runs this suite", "tests/test_rookie_status.py" in ci)
    hub = (ROOT / "scripts" / "build_offseason_page.py").read_text(encoding="utf-8")
    check("the offseason hub can link the page", "rookie-status.html" in hub)
    lists = (ROOT / "scripts" / "list_links.py").read_text(encoding="utf-8")
    check("league pages link it", "rookie-status.html" in lists)


def main():
    test_thresholds_are_exceeded_not_reached()
    test_active_days_exclude_the_injured_list_and_stop_at_45()
    test_september_counts_from_2020_on()
    test_pre_debut_roster_days_count_and_a_gap_is_not_guessed()
    test_retroactive_il_and_paternity_and_same_day_option()
    test_a_service_year_without_a_stat_line_is_zero_when_he_was_not_active()
    test_graduated_means_the_latest_season_and_only_that_season()
    test_a_short_uncertain_tail_stays_under_and_a_long_one_does_not()
    test_the_player_line_and_the_page_use_fixtures()
    test_titles_fit_and_the_pipeline_names_the_page()
    print(f"{PASS} passed, {FAIL} failed")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
