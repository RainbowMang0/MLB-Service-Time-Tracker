"""Out-of-options pages: agreed seasons only, then the static pages.

    python3 tests/test_out_of_options_pages.py
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import options as option_years  # noqa: E402
import out_of_options  # noqa: E402
import out_of_options_pages as pages  # noqa: E402
import page_titles  # noqa: E402
import write_player_pages as wpp  # noqa: E402
from mlb_clubs import CLUB_ABBR  # noqa: E402
from out_of_options import (  # noqa: E402
    REASON_DISAGREE,
    REASON_INCOMPLETE,
    REASON_NO_CACHE,
    REASON_NO_RAW,
    Listed,
    RosterOptions,
)

PASS = 0
FAIL = 0
TODAY = dt.date(2026, 11, 16)
BANNED = (
    "baseball-reference",
    "baseball reference",
    "fangraphs",
    "spotrac",
    "cot's",
    "mlb trade rumors",
    "trade rumors",
)


def check(label, ok):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def _player(pid):
    data = json.loads((ROOT / "docs" / "data" / "service_time.json").read_text())
    for player in data["players"]:
        if player.get("id") == pid:
            return player
    raise AssertionError(f"player {pid} is not in the database")


def _count(years, *, incomplete=False):
    return option_years.OptionCount(
        used_years=list(years),
        remaining=None if incomplete else max(0, 3 - len(years)),
        out_of_options=None if incomplete else len(years) >= 3,
        incomplete=incomplete,
    )


def _select(players, *, raw=True, cache=True, years=(2022, 2023, 2024), replay_years=None, standard=None):
    replay_years = years if replay_years is None else replay_years

    def counter(player, rows, today):
        return _count(player.get("_years", years), incomplete=player.get("_incomplete", False))

    return out_of_options.select(
        players,
        TODAY,
        cache_for=lambda player: [] if cache else None,
        raw_for=lambda player: [] if raw else None,
        counter=counter,
        replay=lambda player, today: {"option_years_used": list(player.get("_replay", replay_years))},
        standard=standard,
        apply_fourth=False,
    )


def _roster(name, team="New York Yankees", **extra):
    player = {
        "id": extra.pop("id", 50),
        "name": name,
        "team": team,
        "on_40_man": True,
        "position": extra.pop("position", "P"),
        "service_time": extra.pop("service_time", "1.040"),
        "service_days_total": extra.pop("service_days_total", 212),
    }
    player.update(extra)
    return player


def test_a_player_is_listed_only_when_the_seasons_agree():
    agreed = _roster("Agreed")
    split = _roster("Split", id=51, _replay=(2022, 2023))
    early = _roster("Early", id=52, _incomplete=True)
    result = _select([agreed, split, early])
    check("three agreed seasons are out of options", [row.player["name"] for row in result.out] == ["Agreed"])
    check("a different season list is left off", any(row.name == "Split" and row.reason == REASON_DISAGREE for row in result.omitted))
    check("an incomplete record is left off", any(row.name == "Early" and row.reason == REASON_INCOMPLETE for row in result.omitted))
    check("a listed player keeps the agreed seasons", result.out[0].years == (2022, 2023, 2024))

    one = _select([_roster("One Left", _years=(2023, 2024), _replay=(2023, 2024))], years=(2023, 2024))
    check("two agreed seasons leave one year", [row.player["name"] for row in one.one] == ["One Left"])
    check("two agreed seasons are not out", one.out == [])

    missing_raw = _select([_roster("No Raw")], raw=False)
    check("no raw file is left off", missing_raw.omitted[0].reason == REASON_NO_RAW)
    missing_cache = _select([_roster("No Cache")], cache=False)
    check("no transaction cache is left off", missing_cache.omitted[0].reason == REASON_NO_CACHE)

    four = _select([_roster("Four")], standard=4)
    check("the standard count is not hard-coded at three", len(four.out) == 0 and len(four.one) == 1)


def test_the_pages_go_straight_to_the_numbers():
    yankees = Listed(_roster("Aaron Out", id=1, service_time="9.999"), (2021, 2022, 2023, 2024))
    angels = Listed(_roster("Betty Out", id=2, team="Los Angeles Angels", service_time="2.010"), (2020, 2022, 2023))
    one = Listed(_roster("Cara Left", id=3, service_time="1.020"), (2024, 2025))
    result = RosterOptions(out=[yankees, angels], one=[one], standard=3)
    league = pages.render_league(result, 2027)
    club = pages.render_club("New York Yankees", [yankees], [one], 2027)
    for label, page in (("league", league), ("club", club)):
        subtitle = re.search(r'<p class="subtitle">(.*?)</p>', page, re.S).group(1)
        check(f"{label} intro is one sentence", subtitle.count(".") == 1 and subtitle.endswith("."))
        check(f"{label} links the service-time page", 'href="/service-time.html"' in page)
        check(f"{label} names the columns", all(
            heading in page for heading in ("Player", "Pos", "Years used", "Seasons", "Service time")
        ))
        check(f"{label} has no last-updated line", "last updated" not in page.lower())
        check(f"{label} does not mention an extra option year", "fourth" not in page.lower() and "4th" not in page.lower())
        check(f"{label} does not mention an outright", "outright" not in page.lower())
        check(f"{label} says public data", "public data" in page)
        for banned in BANNED:
            check(f"{label} does not name {banned}", banned not in page.lower())
    angels = league.split("Los Angeles Angels", 1)[1].split("<h2>", 1)[0]
    yankees = league.split("New York Yankees", 1)[1].split("<h2>", 1)[0]
    check("the Angels are listed before the Yankees", league.find("Los Angeles Angels") < league.find("New York Yankees"))
    check("Betty is in the Angels group", "Betty Out" in angels and "Aaron Out" not in angels)
    check("Aaron is in the Yankees group", "Aaron Out" in yankees and "Betty Out" not in yankees)
    check("the stored service time is shown", "9.999" in league.split("Aaron Out")[1].split("</tr>")[0])
    check("four used seasons stay a count of four", '<td class="n">4</td>' in league.split("Aaron Out")[1].split("</tr>")[0])
    out_part, one_part = club.split("<h2>One option year left</h2>", 1)
    check("the club page keeps one year left in the second section", "Cara Left" in one_part and "Cara Left" not in out_part)
    check("the club page links the league list", 'href="/out-of-options.html"' in club)
    check("the title is the 2027 search", "<title>" in league and "out of options 2027" in league)


def test_titles_match_the_search():
    check("November 2026 is the 2027 season", pages.season_year("2026-11-16T23:15:47+00:00") == 2027)
    check("July 2027 stays 2027", pages.season_year("2027-07-04") == 2027)
    check("January 2027 stays 2027", pages.season_year("2027-01-15") == 2027)
    check("October 2027 is the 2028 season", pages.season_year("2027-10-02") == 2028)
    league = page_titles.out_of_options_title(2027)
    check("league title fits and names the search", len(league) <= 60 and "out of options 2027" in league)
    meta = page_titles.out_of_options_meta(190, 2027)
    check("league description fits", len(meta) <= 155 and "public data" in meta and "2027" in meta)
    for club, _abbr in CLUB_ABBR:
        title = page_titles.club_out_of_options_title(club, 2027)
        desc = page_titles.club_out_of_options_meta(club, 12, 4, 2027)
        check(
            f"{club} title is the 2027 search",
            len(title) <= 60 and title.startswith(club) and "out of options 2027" in title,
        )
        check(
            f"{club} description names the search",
            len(desc) <= 155 and f"{club} out of options 2027" in desc and "public data" in desc,
        )


def test_real_players_match_both_counts():
    wanted = {
        641329: ("out", (2021, 2023, 2024)),  # Bryan Baker
        656529: ("out", (2019, 2020, 2021)),  # Sam Hentges
        671056: ("out", (2021, 2022, 2023, 2024)),  # Iván Herrera
        650911: ("out", (2020, 2021, 2022, 2023)),  # Cristopher Sánchez
        683146: ("one", (2023, 2024)),  # Brett Baty
    }
    left_off = {
        665645: (2021, 2022, 2026),  # Kervin Castro, rulebook drops 2021
        572233: (2015, 2016, 2017, 2018),  # Christian Walker, rulebook drops 2017
        669020: (2023, 2025),  # Ryan Rolison, the two counts split on 2026
    }
    players = [_player(pid) for pid in list(wanted) + list(left_off)]
    result = out_of_options.select(players, TODAY)
    by_out = {row.player["id"]: row for row in result.out}
    by_one = {row.player["id"]: row for row in result.one}
    by_off = {row.player_id: row for row in result.omitted}
    for pid, (kind, years) in wanted.items():
        found = by_out.get(pid) if kind == "out" else by_one.get(pid)
        name = _player(pid)["name"]
        check(f"{name} is {kind} with {years}", found is not None and found.years == years)
        check(f"{name} is not left off", pid not in by_off)
    for pid, cache_years in left_off.items():
        name = _player(pid)["name"]
        row = by_off.get(pid)
        check(
            f"{name} is left off because the seasons disagree",
            row is not None and row.reason == REASON_DISAGREE and row.cache_years == cache_years,
        )
        check(f"{name} is not listed", pid not in by_out and pid not in by_one)
    league = pages.render_league(result, 2027)
    baker = _player(641329)
    baker_row = league.split(baker["name"])[1].split("</tr>")[0]
    check("Baker's row names his seasons", "2021, 2023, 2024" in baker_row)
    check("Baker's row uses the stored service time", baker["service_time"] in baker_row)
    check("Castro is not on the page", "Kervin Castro" not in league)
    check("Rolison is not on the page", "Ryan Rolison" not in league)


def test_a_fourth_option_moves_three_years_and_keeps_four():
    from fourth_option import NO, UNKNOWN, YES, decide
    from il_stints import SeasonWindows

    windows = SeasonWindows.load()
    recent = [
        {"date": "2023-07-15", "typeCode": "SGN", "description": "Houston Astros signed RHP Jake Bloss."},
        {"date": "2024-04-02", "typeCode": "ASG", "description": "RHP Jake Bloss assigned to Asheville Tourists."},
    ]
    decision = decide(recent, [2024, 2025, 2026], TODAY, windows)
    check("a July 2023 signing still has a fourth option", decision.decision == YES and decision.possible < 5)

    veteran = [{"date": "2010-06-01", "typeCode": "SGN", "description": "Club signed RHP Old Player."}]
    for year in range(2010, 2016):
        veteran.append({
            "date": f"{year}-04-10",
            "typeCode": "ASG",
            "description": "RHP Old Player assigned to a minor league club.",
        })
    decision = decide(veteran, [2022, 2023, 2024], TODAY, windows)
    check("five full seasons leave him out", decision.decision == NO and decision.full >= 5)
    check("no signing date is unsettled", decide([], [2024, 2025, 2026], TODAY, windows).decision == UNKNOWN)

    four = _roster("Four Used", id=77)
    result = out_of_options.select(
        [four],
        TODAY,
        cache_for=lambda player: [],
        raw_for=lambda player: recent,
        counter=lambda player, rows, today: _count((2023, 2024, 2025, 2026)),
        replay=lambda player, today: {"option_years_used": [2023, 2024, 2025, 2026]},
        apply_fourth=True,
    )
    check("four used years stay out", [row.player["name"] for row in result.out] == ["Four Used"])
    check("four used years are not moved to one left", result.one == [] and result.may_fourth == [])


def test_an_injury_year_counts_and_a_lone_transfer_does_not():
    from fourth_option import NO, UNKNOWN, YES, decide
    from il_stints import SeasonWindows

    windows = SeasonWindows.load()

    def assigned(year, month_day="04-20"):
        return {
            "date": f"{year}-{month_day}",
            "typeCode": "ASG",
            "description": "RHP Pat Example assigned to a minor league club.",
        }

    injury = [{"date": "2014-04-20", "typeCode": "SGN", "description": "Club signed RHP Pat Example."}]
    for year in (2014, 2015, 2016, 2017):
        injury.append(assigned(year))
    injury += [
        assigned(2018),
        {"date": "2018-05-25", "typeCode": "SC", "description": "Club placed RHP Pat Example on the 7-day injured list."},
        {"date": "2018-09-01", "typeCode": "SC", "description": "Club activated RHP Pat Example from the 7-day injured list."},
    ]
    decision = decide(injury, [2016, 2017, 2018], TODAY, windows)
    check("30 active days plus injured-list time is a full season", decision.decision == NO and decision.full >= 5)

    brief = [{"date": "2014-04-20", "typeCode": "SGN", "description": "Club signed RHP Pat Example."}]
    for year in (2014, 2015, 2016, 2017):
        brief.append(assigned(year))
    brief += [
        assigned(2018, "04-10"),
        {"date": "2018-04-20", "typeCode": "SC", "description": "Club placed RHP Pat Example on the 7-day injured list."},
    ]
    decision = decide(brief, [2016, 2017, 2018], TODAY, windows)
    check("fewer than 30 active days is not a full injury season", decision.decision == YES)

    gap = [{"date": "2014-04-20", "typeCode": "SGN", "description": "Club signed RHP Pat Example."}]
    for year in (2014, 2015, 2016, 2017):
        gap.append(assigned(year))
    gap.append({
        "date": "2018-08-28",
        "typeCode": "ASG",
        "description": "RHP Pat Example assigned to Mahoning Valley Scrappers from AZL Indians.",
    })
    decision = decide(gap, [2016, 2017, 2018], TODAY, windows)
    check("a lone mid-season transfer is unknown, not a short season", decision.decision == UNKNOWN)


def test_fixtures_cover_four_years_a_fourth_option_and_missing_data():
    """These rules used to be pinned to Luis Gil and Jake Cousins.

    Both left the 40-man roster, so a test that reads today's roster can no
    longer see them. The placement did not change: four used years stay out,
    three years with a record that cannot settle a fourth year are held, and
    a missing transaction file is not called out.
    """
    four = _roster("Four Seasons", id=101, service_time="1.040")
    held = _roster("Maybe Fourth", id=102, team="Chicago White Sox", service_time="1.020")
    missing = _roster("No Record", id=103, service_time="0.040")

    def years_for(player):
        if player["id"] == 101:
            return (2020, 2021, 2022, 2026)
        return (2022, 2023, 2024)

    def raw_for(player):
        if player["id"] == 103:
            return None
        return []

    result = out_of_options.select(
        [four, held, missing],
        TODAY,
        cache_for=lambda player: [],
        raw_for=raw_for,
        counter=lambda player, rows, today: _count(years_for(player)),
        replay=lambda player, today: {"option_years_used": list(years_for(player))},
        apply_fourth=True,
    )
    by_out = {row.player["id"]: row for row in result.out}
    by_held = {row.player["id"]: row for row in result.may_fourth}
    by_off = {row.player_id: row for row in result.omitted}
    check(
        "four agreed seasons stay out",
        by_out[101].years == (2020, 2021, 2022, 2026) and 101 not in by_held,
    )
    check(
        "three seasons and no signing date may have a fourth option",
        102 in by_held and by_held[102].years == (2022, 2023, 2024) and 102 not in by_out,
    )
    check("a missing transaction file is left off", by_off[103].reason == REASON_NO_RAW)
    check("missing data is not listed as out", 103 not in by_out and 103 not in by_held)
    league = pages.render_league(result, 2027)
    out_half, held_half = league.split("May have a fourth option", 1)
    check("the rendered page keeps four seasons out", "Four Seasons" in out_half and "Four Seasons" not in held_half)
    check("the rendered page holds the unsettled fourth option", "Maybe Fourth" in held_half)
    check("the rendered page omits the missing record", "No Record" not in league)
    from fourth_option import UNKNOWN, decide
    from il_stints import SeasonWindows
    decision = decide([], [2022, 2023, 2024], TODAY, SeasonWindows.load())
    check("missing signing data is unknown", decision.decision == UNKNOWN)


def test_real_fourth_option_cases():
    wanted = {
        814005: "one",   # Jake Bloss
        694462: "one",   # Hurston Waldrep
        682729: "out",   # Jonatan Clase, four years
        663893: "one",   # Brendon Little, two years on the record
        671936: "fourth",  # Adam Macko, no signing date
        641329: "out",   # Bryan Baker, five full seasons
        656529: "out",   # Sam Hentges, injury years make five full seasons
        687093: "out",   # Vaughn Grissom
        682982: "fourth",  # Daniel Espino, reviewed hold
        689017: "fourth",  # Landon Knack, reviewed hold
        663436: "fourth",  # Davis Martin
        694362: "fourth",  # Blake Dunn
        685133: "one",   # Wade Meckler, signed too late for five full seasons
    }
    players = [_player(pid) for pid in wanted]
    result = out_of_options.select(players, TODAY)
    by_out = {row.player["id"]: row for row in result.out}
    by_one = {row.player["id"]: row for row in result.one}
    by_held = {row.player["id"]: row for row in result.may_fourth}
    check("Bloss is one left and still shows 2024-2026", by_one[814005].years == (2024, 2025, 2026) and 814005 not in by_out)
    check("Waldrep is one left and still shows 2024-2026", by_one[694462].years == (2024, 2025, 2026))
    check("Clase stays out with four seasons", by_out[682729].years == (2023, 2024, 2025, 2026))
    check("Little stays at the two seasons on the record", by_one[663893].years == (2024, 2026) and 663893 not in by_out)
    check("Macko is held, not listed as out", 671936 in by_held and 671936 not in by_out)
    check("Baker stays out", 641329 in by_out and by_out[641329].years == (2021, 2023, 2024))
    check("Hentges is out and keeps 2019-2021", by_out[656529].years == (2019, 2020, 2021))
    check("Grissom is out", 687093 in by_out and 687093 not in by_one)
    check("Meckler stays one left", 685133 in by_one and 685133 not in by_out)
    check("Espino may have a fourth option", 682982 in by_held and 682982 not in by_out)
    check("Knack may have a fourth option", 689017 in by_held and 689017 not in by_out)
    check("Martin may have a fourth option", 663436 in by_held and 663436 not in by_out)
    check("Dunn may have a fourth option", 694362 in by_held and 694362 not in by_out)
    league = pages.render_league(result, 2027)
    check("the league page names a held player", "May have a fourth option" in league and "Adam Macko" in league)
    check("a player with a fourth year is not in the held note", "Jake Bloss" not in league)
    jays_out = [row for row in result.out if row.player.get("team") == "Toronto Blue Jays"]
    jays_one = [row for row in result.one if row.player.get("team") == "Toronto Blue Jays"]
    page = pages.render_club("Toronto Blue Jays", jays_out, jays_one, 2027)
    out_part, one_part = page.split("<h2>One option year left</h2>", 1)
    check("the Jays page puts Bloss in one left", "Jake Bloss" in one_part and "Jake Bloss" not in out_part)
    check("the Jays page keeps Clase out", "Jonatan Clase" in out_part)
    check("three seasons are explained as one year left", "Three seasons in this list means one option year is left" in one_part)
    check("the one-left section does not call it a fourth option", "fourth" not in one_part.lower())
    for banned in BANNED:
        check(f"Jays page does not name {banned}", banned not in page.lower())


def test_the_roster_page_links_the_list():
    class Count:
        out_of_options = True

    player = _roster("Counted", id=999001)
    prepared = out_of_options.prepare(
        [player],
        TODAY,
    )
    # prepare() reads the real files. This id has none, so he is left off.
    check("an unknown id is left off", prepared.omitted and prepared.omitted[0].reason == REASON_NO_RAW)
    check(
        "the club count uses that decision",
        out_of_options.count_for([player], {999001: Count()}) == 0,
    )
    out_of_options.clear()
    check(
        "without a prepared roster the transaction count is used",
        out_of_options.count_for([player], {999001: Count()}) == 1,
    )
    html = wpp.render_club("New York Yankees", [player], "2026-11-16T00:00:00+00:00", None, None, None, 2027)
    check("the roster count links the club page", 'href="new-york-yankees-out-of-options.html"' in html)
    check("the roster page links the league list", 'href="/out-of-options.html"' in html)
    check("the roster link names the 2027 search", "New York Yankees out of options 2027" in html)


def test_published_files_and_the_guards():
    docs = ROOT / "docs"
    league = (docs / "out-of-options.html").read_text(encoding="utf-8")
    check("the league page is on disk", "Players out of options, 2027" in league)
    check("the league page has no last-updated line", "last updated" not in league.lower())
    check("the league page holds an unsettled fourth option", "May have a fourth option" in league and "Adam Macko" in league)
    check("Jake Bloss is not listed as out", "Jake Bloss" not in league)
    jays = (docs / "t" / "toronto-blue-jays-out-of-options.html").read_text(encoding="utf-8")
    braves = (docs / "t" / "atlanta-braves-out-of-options.html").read_text(encoding="utf-8")
    out_part, one_part = jays.split("<h2>One option year left</h2>", 1)
    check("the built Jays page puts Bloss in one left", "Jake Bloss" in one_part and "Jake Bloss" not in out_part)
    check("the built Jays page keeps Little on 2024 and 2026", "Brendon Little" in one_part and "2024, 2026" in one_part)
    check("the built Braves page puts Waldrep in one left", "Hurston Waldrep" in braves.split("<h2>One option year left</h2>", 1)[1])
    out_half, held_half = league.split("May have a fourth option", 1)
    check("the built league page lists Hentges out", "Sam Hentges" in out_half)
    check("the built league page lists Grissom out", "Vaughn Grissom" in out_half)
    check("the built league page holds Espino", "Daniel Espino" in held_half)
    for banned in BANNED:
        check(f"built league page does not name {banned}", banned not in league.lower())
    core = (docs / "sitemap-core.xml").read_text(encoding="utf-8")
    clubs = (docs / "sitemap-clubs.xml").read_text(encoding="utf-8")
    check("sitemap-core lists the league page", "/out-of-options.html</loc>" in core)
    check("sitemap-clubs lists a club page", "/t/new-york-yankees-out-of-options.html</loc>" in clubs)
    check("every club page is in the sitemap", all(
        f"/t/{wpp.slug(name)}-out-of-options.html</loc>" in clubs for name, _abbr in CLUB_ABBR
    ))
    offseason = (docs / "offseason.html").read_text(encoding="utf-8")
    guide = (docs / "send-a-player-down.html").read_text(encoding="utf-8")
    yankees = (docs / "t" / "new-york-yankees.html").read_text(encoding="utf-8")
    check("the offseason hub links the league page", 'href="/out-of-options.html"' in offseason)
    check("the send-down guide links the league page", 'href="/out-of-options.html"' in guide)
    check("the Yankees roster links the league page", 'href="/out-of-options.html"' in yankees)
    check("the Yankees roster links its own page", "new-york-yankees-out-of-options.html" in yankees)
    for name in ("update-service-time.yml", "backfill-history.yml"):
        body = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        check(f"{name} commits the league page", "docs/out-of-options.html" in body)
    raw_job = (ROOT / ".github" / "workflows" / "update-service-time.yml").read_text(encoding="utf-8").split("raw-transactions:", 1)[1]
    check("the raw job commits the league page with the store", "docs/out-of-options.html" in raw_job)
    check("the raw job commits the club pages with the store", "docs/t/*-out-of-options.html" in raw_job)
    rebuild = (ROOT / "scripts" / "rebuild_transaction_pages.py").read_text(encoding="utf-8")
    check("a transaction commit rebuilds these pages", "scripts/out_of_options_pages.py" in rebuild)
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    check("ci names this suite", "tests/test_out_of_options_pages.py" in ci)
    check("ci checks the built pages", "scripts/out_of_options_pages.py --check" in ci)
    taxes = (docs / "taxes.html").read_bytes()
    check(
        "taxes.html is unchanged",
        hashlib.sha256(taxes).hexdigest()
        == "ef78b2f8160046145ce58382a0da8a55dc5ab3b98130fab271db847e1a45fe62",
    )
    for rel in (
        "scripts/out_of_options.py",
        "scripts/out_of_options_pages.py",
        "scripts/out_of_options_site.py",
        "scripts/fourth_option.py",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8").lower()
        for banned in BANNED:
            check(f"{rel} does not name {banned}", banned not in text)


def main():
    test_a_player_is_listed_only_when_the_seasons_agree()
    test_the_pages_go_straight_to_the_numbers()
    test_titles_match_the_search()
    test_real_players_match_both_counts()
    test_a_fourth_option_moves_three_years_and_keeps_four()
    test_an_injury_year_counts_and_a_lone_transfer_does_not()
    test_fixtures_cover_four_years_a_fourth_option_and_missing_data()
    test_real_fourth_option_cases()
    test_the_roster_page_links_the_list()
    test_published_files_and_the_guards()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
