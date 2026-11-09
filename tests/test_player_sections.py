"""Player-page sections: injured list, career moves, season stats.

No pytest. The injured-list counts are display-only and are checked
against the cached transactions for a handful of players. A day on the
injured list is not removed from service time anywhere in here.

    python3 tests/test_player_sections.py
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from career_moves import build_moves, classify_move  # noqa: E402
from il_stints import SeasonWindows, build_stints, list_label, placement_start  # noqa: E402
from mlb_clubs import CLUB_NAMES  # noqa: E402
from player_sections import render_player_sections  # noqa: E402
from season_stat_tables import (  # noqa: E402
    HITTING_COLUMNS,
    PITCHING_COLUMNS,
    ip_to_outs,
    outs_to_ip,
    tables_to_show,
)

PASS = 0
FAIL = 0
TODAY = dt.date(2026, 11, 6)
CACHE = ROOT / "data" / "cache" / "transactions"
WINDOWS = SeasonWindows.load()


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def rows(player_id: int) -> list[dict]:
    return json.loads((CACHE / f"{player_id}.json").read_text())


def stints(player_id: int, today: dt.date = TODAY):
    return build_stints(rows(player_id), WINDOWS, today)


def moves(player_id: int, debut: dt.date | None = None, club: int | None = None):
    data = rows(player_id)
    built = build_stints(data, WINDOWS, TODAY)
    closes = {s.end for s in built if s.kind == "exact" and s.end is not None}
    return build_moves(
        data, debut=debut, debut_club_id=club, names=CLUB_NAMES, il_close_dates=closes
    )


def by(items, start: str):
    day = dt.date.fromisoformat(start)
    found = [s for s in items if s.start == day]
    return found[0] if found else None


def test_baker_has_no_major_league_injured_list_time():
    built = stints(641329)
    check("Baker has no major league injured-list stay", built == [])
    html = render_player_sections(
        {"id": 641329, "name": "Bryan Baker", "position": "P", "season_stats": {}},
        rows(641329), CLUB_NAMES, TODAY, WINDOWS,
    )
    check(
        "Baker's page says the public record has no major league injured-list time",
        "No major league injured-list time in the public record." in html,
    )
    check("a minor-league stay is not given a table", 'id="injuries"' in html and "<tbody>" not in html.split('id="stats"')[0])


def test_betts_stints_match_the_cached_feed():
    built = stints(605141)
    check("Betts has 7 injured-list stays", len(built) == 7)
    expect = [
        ("2015-07-29", "2015-08-11", 13, "exact", "7-day"),
        ("2018-05-29", "2018-06-11", 13, "exact", "10-day"),
        ("2021-07-25", "2021-08-01", 7, "exact", "10-day"),
        ("2021-08-11", "2021-08-26", 15, "exact", "10-day"),
        ("2022-06-19", "2022-07-03", 14, "exact", "10-day"),
        ("2024-06-17", "2024-08-12", 56, "exact", "10-day"),
        ("2026-04-05", "2026-05-11", 36, "exact", "10-day"),
    ]
    for start, end, days, kind, label in expect:
        stay = by(built, start)
        check(
            f"Betts {start} is {days} days through {end}",
            stay is not None
            and stay.end == dt.date.fromisoformat(end)
            and stay.days == days
            and stay.kind == kind
            and list_label(stay) == label,
        )
    reserve = by(built, "2024-06-17")
    check(
        "Betts 2024 return is the reserve-list activation, not a guessed date",
        reserve is not None and reserve.close == "activation" and reserve.days == 56,
    )
    check(
        "Betts career injured-list days are 154 without an effectiveDate on the 2021 row",
        sum(s.days for s in built) == 154,
    )


def test_effective_date_moves_the_start_when_the_text_does_not():
    """The 2021 Betts row has no retroactive clause. The feed's effective
    date, once stored, is three days earlier. Older cache rows omit the key."""
    bare = {
        "date": "2021-07-25",
        "description": "Los Angeles Dodgers placed RF Mookie Betts on the 10-day injured list.",
        "to_team_id": 119,
    }
    check(
        "a missing effectiveDate is not required",
        placement_start(dt.date(2021, 7, 25), bare["description"], bare.get("effectiveDate"))
        == dt.date(2021, 7, 25),
    )
    dated = dict(bare, effectiveDate="2021-07-22")
    back = {
        "date": "2021-08-01",
        "description": "Los Angeles Dodgers activated RF Mookie Betts from the 10-day injured list.",
        "to_team_id": 119,
    }
    built = build_stints([dated, back], WINDOWS, TODAY)
    check("effectiveDate counts the three days the text omits", len(built) == 1 and built[0].days == 10)
    check("the stay starts on the effective date", built[0].start == dt.date(2021, 7, 22))


def test_adames_missing_return_is_a_lower_bound():
    built = stints(642715)
    check("Adames has 4 injured-list stays", len(built) == 4)
    gap = by(built, "2023-05-27")
    check(
        "Adames 2023 return is not invented; the floor is 9 days through the last row",
        gap is not None and gap.kind == "lower" and gap.days == 9 and gap.end is None,
    )
    check("Adames 2021 stay is 15 days", by(built, "2021-09-05").days == 15)
    check("Adames 2022 stay is 23 days and a plain activation closes it", by(built, "2022-05-16").days == 23)
    check(
        "Adames 2026 stay runs to the season's last day, 35 days",
        by(built, "2026-08-24").days == 35 and by(built, "2026-08-24").kind == "exact",
    )
    known = sum(s.days for s in built if s.kind != "open")
    check("Adames known injured-list days are at least 82", known == 82 and any(s.kind == "lower" for s in built))


def test_arrighetti_trade_does_not_close_the_stay():
    built = stints(681293)
    check("Arrighetti has 3 injured-list stays", len(built) == 3)
    check("2025 transfer stays one stay of 121 days", by(built, "2025-04-07").days == 121)
    check("2025 second stay is 29 days to the season's last day", by(built, "2025-08-31").days == 29)
    traded = by(built, "2026-07-25")
    check(
        "a trade in the middle keeps one stay and both clubs",
        traded is not None
        and traded.days == 27
        and traded.clubs == [117, 141]
        and traded.kind == "exact"
        and list_label(traded) == "15-day",
    )
    html = render_player_sections(
        {"id": 681293, "name": "Spencer Arrighetti", "position": "P"},
        rows(681293), CLUB_NAMES, TODAY, WINDOWS,
    )
    check(
        "the traded stay names both clubs in our words",
        "15-day · Foot (Astros → Blue Jays)" in html,
    )
    check("the other player's name is not copied onto the page", "Varsho" not in html and "cash" not in html.lower())


def test_ohtani_same_day_lists_are_one_stay():
    built = stints(660271)
    check("Ohtani has 4 injured-list stays and 97 days", len(built) == 4 and sum(s.days for s in built) == 97)
    expect = [("2018-06-07", 26), ("2019-03-25", 40), ("2023-09-16", 16), ("2026-09-08", 15)]
    for start, days in expect:
        stay = by(built, start)
        check(f"Ohtani {start} is {days} days", stay is not None and stay.days == days and stay.kind == "exact")
    check("two lists on one day are one stay", list_label(by(built, "2023-09-16")) == "15-day / 10-day")


def test_buxton_option_missed_return_and_conversion():
    built = stints(621439)
    optioned = by(built, "2015-06-25")
    check(
        "Buxton 2015 option closes the stay at 46 days",
        optioned is not None and optioned.close == "option" and optioned.days == 46,
    )
    missed = by(built, "2018-04-15")
    check(
        "Buxton 2018 re-placement closes the earlier stay as at least 8 days",
        missed is not None and missed.kind == "lower" and missed.days == 8,
    )
    check("the stay that follows is closed by an option at 53 days", by(built, "2018-05-10").days == 53)
    check(
        "a 7-day list moving to the 10-day list is one stay of 11 days",
        list_label(by(built, "2019-07-14")) == "7-day → 10-day" and by(built, "2019-07-14").days == 11,
    )
    check("a November activation counts only through the season's last day", by(built, "2019-09-10").days == 20)
    check("a next-day duplicate placement is the same stay", by(built, "2026-07-06").days == 14)
    line = [
        m.text for m in moves(621439, dt.date(2015, 6, 14), 142)
        if m.date == dt.date(2015, 8, 10)
    ]
    check(
        "coming off the list and being optioned the same day is one line",
        line == ["Came off the injured list and was sent down to the minors by the Minnesota Twins"],
    )


def test_rendon_long_history_and_the_february_placement():
    built = stints(543685)
    check("Rendon has 18 injured-list stays", len(built) == 18)
    # The hand note counted this from April 5. His club's first game was April 6.
    check(
        "Rendon 2015 opens on his club's first game day, 59 regular-season days",
        by(built, "2015-03-27").days == 59,
    )
    check("Rendon 2021 transfer to the 60-day list stays one stay of 90 days", by(built, "2021-07-06").days == 90)
    messy = by(built, "2022-06-15")
    check(
        "Rendon 2022 transfer with no placement row is one stay of 110 days",
        messy is not None and messy.days == 110 and messy.opened_without_placement
        and list_label(messy) == "10-day → 60-day",
    )
    spring = by(built, "2024-02-01")
    check(
        "a February placement with no return counts no regular-season days and invents none",
        spring is not None and spring.kind == "lower" and spring.days == 0 and spring.end is None,
    )
    current = built[-1]
    check(
        "the 2026 stay is still open and its day count is not a closed return",
        current.kind == "open" and current.start == dt.date(2026, 3, 24) and current.end is None,
    )
    html = render_player_sections(
        {"id": 543685, "name": "Anthony Rendon", "position": "3B"},
        rows(543685), CLUB_NAMES, TODAY, WINDOWS,
    )
    check("an open stay is labeled current, not a daily number", 'class="num pp-open">current' in html)
    check("the open stay says since the placement date", "since Mar 24" in html)
    check(
        "the note does not say injured-list time reduces service time",
        "still count as major league service time" in html,
    )


def test_november_placement_is_not_a_stay():
    built = build_stints(
        [{
            "date": "2019-12-09",
            "description": "Philadelphia Phillies placed RHP Example on the 10-day injured list.",
            "to_team_id": 143,
        }],
        WINDOWS, TODAY,
    )
    check("a December placement is not an injured-list stay", built == [])


def test_timeline_uses_ids_and_drops_noise():
    baker = moves(641329, dt.date(2021, 9, 5), 141)
    texts = [m.text for m in baker]
    check("Baker signed with the Rockies", "Signed with the Colorado Rockies" in texts)
    check(
        "Baker's trade follows the ids, Rockies to Blue Jays",
        "Traded from the Colorado Rockies to the Toronto Blue Jays" in texts,
    )
    check(
        "Baker 2024 shuttle collapses five trips down",
        "Moved between the majors and minors 5 times in 2024" in texts,
    )
    check("Baker 2023's two trips stay as separate lines", sum(1 for t in texts if "Sent down" in t) == 3)
    check("number changes are dropped", all("number" not in t.lower() for t in texts))
    check("an invite is dropped", all("invite" not in t.lower() and "spring" not in t.lower() for t in texts))
    joined = " ".join(texts)
    check("minor-league assignment cities are dropped", "Asheville" not in joined and "Norfolk" not in joined)

    betts = moves(605141, dt.date(2014, 6, 29), 111)
    trade = [m.text for m in betts if m.kind == "trade"]
    check(
        "Betts' trade is Red Sox to Dodgers even though the sentence starts with the other club",
        trade == ["Traded from the Boston Red Sox to the Los Angeles Dodgers"],
    )
    check("the other names in that trade are not listed", "Verdugo" not in " ".join(m.text for m in betts))

    adames_rows = rows(642715)
    trade_row = next(r for r in adames_rows if r["date"] == "2014-07-31" and "raded" in r["description"])
    check("the messy trade sentence still classifies as a trade", classify_move(trade_row) == "trade")
    adames = moves(642715, dt.date(2018, 5, 22), 139)
    adames_text = [m.text for m in adames]
    check(
        "Adames' trade uses the ids: Tigers to Rays",
        "Traded from the Detroit Tigers to the Tampa Bay Rays" in adames_text,
    )
    check("no other player or cash from that sentence is copied", "Smyly" not in " ".join(adames_text) and "Price" not in " ".join(adames_text))
    check(
        "a November contract selection uses the offseason wording",
        "Added to the Tampa Bay Rays 40-man roster" in adames_text,
    )
    check("Adames became a free agent and later signed", "Became a free agent" in adames_text and "Signed with the San Francisco Giants" in adames_text)
    check(
        "the minor-league signing is kept as its own line",
        "Signed a minor league deal with the Detroit Tigers" in adames_text,
    )

    noise = {"date": "2024-04-15", "description": "RHP Bryan Baker changed number to 42.", "to_team_id": 110}
    check("a number change classifies as nothing", classify_move(noise) is None)
    status = {"date": "2023-10-02", "description": "RHP Bryan Baker roster status changed by Baltimore Orioles.", "to_team_id": 110}
    check("roster status changed is dropped", classify_move(status) is None)


def test_which_stat_tables_show():
    pitching = {"2024": {"pitching": {"gamesPlayed": 10, "inningsPitched": "40.0", "wins": 1, "losses": 2, "earnedRuns": 10, "hits": 30, "baseOnBalls": 10, "strikeOuts": 40}}}
    hitting = {"2024": {"hitting": {"gamesPlayed": 10, "atBats": 0, "hits": 0}}}
    both_zero = {"2024": {**pitching["2024"], **hitting["2024"]}}
    check("a pitcher does not get a batting table", tables_to_show("P", both_zero) == (False, True))
    check("RP and SP are pitchers", tables_to_show("RP", both_zero)[0] is False and tables_to_show("SP", both_zero)[0] is False)

    cameo = {"2024": {"hitting": {"atBats": 400, "hits": 100, "gamesPlayed": 140}, "pitching": {"inningsPitched": "3.0", "gamesPlayed": 1, "earnedRuns": 1}}}
    check("a position player's short pitching outing stays off the page", tables_to_show("SS", cameo) == (True, False))
    real_pitching = {"2024": {"hitting": {"atBats": 400, "hits": 100}, "pitching": {"inningsPitched": "20.0", "gamesPlayed": 5, "earnedRuns": 8, "hits": 18, "baseOnBalls": 6}}}
    check("20 innings gives a position player a pitching table", tables_to_show("CF", real_pitching) == (True, True))
    check("TWP gets both tables", tables_to_show("TWP", real_pitching) == (True, True))
    two_way_by_usage = {"2024": {"hitting": {"atBats": 50, "hits": 10}, "pitching": {"inningsPitched": "20.0", "earnedRuns": 6, "hits": 15, "baseOnBalls": 5}}}
    check(
        "a pitcher's hitting line stays hidden even at 50 at-bats",
        tables_to_show("P", two_way_by_usage) == (False, True),
    )
    check(
        "a non-pitcher with 50 at-bats and 20 innings gets both tables",
        tables_to_show("CF", two_way_by_usage) == (True, True),
    )

    check("1.2 plus 1.2 innings is 3.1, not a decimal sum", outs_to_ip(ip_to_outs("1.2") + ip_to_outs("1.2")) == "3.1")
    check("0.1 innings is one out", ip_to_outs("0.1") == 1)
    check("a value that is not baseball innings is refused", ip_to_outs("85.20") is None)

    traded = {
        "2026": {
            "pitching": {
                "numTeams": 2,
                "gamesPlayed": 24,
                "inningsPitched": "110.2",
                "wins": 8,
                "losses": 6,
                "earnedRuns": 40,
                "hits": 90,
                "baseOnBalls": 30,
                "strikeOuts": 110,
                "splits": [
                    {"team_id": 117, "gamesPlayed": 17, "inningsPitched": "88.0", "wins": 6, "losses": 5, "earnedRuns": 30, "hits": 70, "baseOnBalls": 22, "strikeOuts": 80},
                    {"team_id": 141, "gamesPlayed": 7, "inningsPitched": "22.2", "wins": 2, "losses": 1, "earnedRuns": 10, "hits": 20, "baseOnBalls": 8, "strikeOuts": 30},
                ],
            }
        }
    }
    html = render_player_sections(
        {"id": 1, "name": "Traded Arm", "position": "P", "season_stats": traded},
        None, CLUB_NAMES, TODAY, WINDOWS,
    )
    check("a traded season leads with the 2 Tms total", ">2 Tms<" in html)
    check("then each club", ">Astros<" in html and ">Blue Jays<" in html)
    check("no batting table for the pitcher", "<h3>Batting</h3>" not in html and "<h3>Pitching</h3>" in html)
    check("the page says public data and not public MLB data", "public data" in html and "public MLB" not in html)


def test_stored_profiles_pick_the_right_tables():
    def profile(player_id: int) -> dict:
        shard = ROOT / "docs" / "data" / "profiles" / f"{player_id % 64:02d}.json"
        return json.loads(shard.read_text())["players"][str(player_id)]

    baker = profile(641329)
    html = render_player_sections(baker, None, CLUB_NAMES, TODAY, WINDOWS)
    check("Baker's stored line hides batting", "<h3>Batting</h3>" not in html)
    check("Baker's stored line shows pitching", "<h3>Pitching</h3>" in html and ">62.1<" in html)
    # The 2026-11-06 daily update stored Baker's 2025 traded year (Orioles and
    # Rays). The page shows that stored line. A year the profile does not have
    # is still not invented.
    pitching = html.split("<h3>Pitching</h3>", 1)[-1]
    check(
        "the stored 2025 traded season is shown",
        ">2025<" in pitching and ">2 Tms<" in pitching and ">Orioles<" in pitching and ">Rays<" in pitching,
    )
    check("a year the profile does not have is not invented", ">2020<" not in pitching)

    ohtani = profile(660271)
    page = render_player_sections(ohtani, None, CLUB_NAMES, TODAY, WINDOWS)
    check("Ohtani gets batting and pitching", "<h3>Batting</h3>" in page and "<h3>Pitching</h3>" in page)
    batting, pitching = page.split("<h3>Pitching</h3>", 1)
    check("Ohtani 2018 batting is on the page", ">2018<" in batting and ">22<" in batting)
    check("a year he did not pitch has no pitching row", ">2019<" not in pitching and ">2024<" not in pitching)


def test_open_stay_does_not_change_the_page_hash_during_the_season():
    import write_player_pages as pages

    player = {
        "id": 999001,
        "name": "Open Stay",
        "service_time": "1.000",
        "service_days_total": 172,
        "position": "SS",
        "team": "Miami Marlins",
        "on_40_man": True,
        "mlb_debut": "2024-04-01",
        "seasons": [{"y": 2024, "d": 100, "t": [146]}],
    }
    txns = [{
        "date": "2026-06-01",
        "description": "Miami Marlins placed SS Open Stay on the 10-day injured list. Left elbow sprain.",
        "to_team_id": 146,
    }]
    early = pages.render(player, {146: "Miami Marlins"}, "2026-06-01T00:00:00+00:00", [], txns)
    later = pages.render(player, {146: "Miami Marlins"}, "2026-06-15T00:00:00+00:00", [], txns)
    check("two days of an open stay hash the same", pages._content_key(early) == pages._content_key(later))
    check("the open stay is on the page", "since Jun 1" in early and 'pp-open">current' in early)
    check("the severity word is not copied", "sprain" not in early.lower())
    check("the feed sentence is not copied", "retroactive" not in early.lower())
    check("the coarse body-part label shows in our own words, not the feed's", "Elbow" in early)


class _StatTables(HTMLParser):
    """Header cells and the career row of each season-stat table."""

    def __init__(self):
        super().__init__()
        self.tables = {}
        self._title = ""
        self._in_h3 = False
        self._section = None
        self._row = None
        self._cell = ""
        self._in_cell = False
        self._header = None
        self._career = None

    def handle_starttag(self, tag, attrs):
        if tag == "h3":
            self._in_h3 = True
            self._title = ""
            self._header = None
            self._career = None
        elif tag in ("thead", "tbody", "tfoot"):
            self._section = tag
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._in_cell = True
            self._cell = ""

    def handle_endtag(self, tag):
        if tag == "h3":
            self._in_h3 = False
        elif tag in ("td", "th") and self._in_cell:
            self._row.append(self._cell.strip())
            self._in_cell = False
        elif tag == "tr" and self._row is not None:
            if self._section == "thead":
                self._header = self._row
            elif self._section == "tfoot":
                self._career = self._row
            self._row = None
        elif tag == "table" and self._title:
            self.tables[self._title] = (self._header, self._career)
        elif tag in ("thead", "tbody", "tfoot"):
            self._section = None

    def handle_data(self, data):
        if self._in_h3:
            self._title += data
        elif self._in_cell:
            self._cell += data


def _stat_tables(html: str) -> dict:
    parser = _StatTables()
    parser.feed(html)
    return parser.tables


def _phone_column_hides(css: str) -> list[str]:
    """th/td :nth-child rules set to display:none inside a 640px block.

    A selector counted here is one that is not limited to the main players
    table, so it also hits a season-stat table.
    """
    found = []
    start = 0
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    needle = "@media (max-width: 640px)"
    while True:
        at = css.find(needle, start)
        if at < 0:
            break
        brace = css.find("{", at)
        depth = 0
        end = None
        for i in range(brace, len(css)):
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        block = css[brace + 1:end]
        start = end + 1
        for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", block):
            body = re.sub(r"\s+", "", match.group(2))
            if "display:none" not in body:
                continue
            for raw in match.group(1).split(","):
                selector = " ".join(raw.split())
                if ":nth-child(" not in selector:
                    continue
                if not re.search(r"\b(th|td)\b", selector):
                    continue
                if selector.startswith("#players-table"):
                    continue
                found.append(selector)
    return found


def test_career_row_matches_the_header():
    """Career totals use the same columns, in the same order, as the header.

    Clayton Beeter's pitching line is the case that showed the bug: on a
    phone the shared stylesheet hid column 4 of every header and body (meant
    for Pos on the main players table). Pitching column 4 is GS and batting
    column 4 is AB. The career row lives in the footer, which that rule
    skipped, so 6-8 (W-L) sat under SV.
    """
    pitching = {
        "2024": {"pitching": {
            "gamesPlayed": 5, "gamesStarted": 1, "wins": 1, "losses": 2,
            "saves": 3, "inningsPitched": "10.0", "earnedRuns": 3,
            "strikeOuts": 4, "hits": 8, "baseOnBalls": 2, "homeRuns": 1,
        }},
        "2025": {"pitching": {
            "gamesPlayed": 74, "gamesStarted": 0, "wins": 5, "losses": 6,
            "saves": 10, "inningsPitched": "67.0", "earnedRuns": 35,
            "strikeOuts": 81, "hits": 45, "baseOnBalls": 48, "homeRuns": 10,
        }},
    }
    pitching_html = render_player_sections(
        {"id": 1, "name": "Career Arm", "position": "P", "season_stats": pitching},
        None, CLUB_NAMES, TODAY, WINDOWS,
    )
    headers, career = _stat_tables(pitching_html)["Pitching"]
    check(
        "pitching header is the pitching column list",
        headers == [label for _key, label in PITCHING_COLUMNS],
    )
    check("pitching career row has one cell per header", career is not None and len(career) == len(headers))
    pitched = dict(zip(headers, career))
    check("pitching Year cell is Career", pitched["Year"] == "Career")
    check("pitching team cell on the career row is blank", pitched["Tm"] == "")
    check("pitching GS stays in the GS column", pitched["GS"] == "1")
    check("pitching W-L stays in the W-L column", pitched["W-L"] == "6-8")
    check("pitching SV stays in the SV column", pitched["SV"] == "13")
    check("pitching IP stays in the IP column", pitched["IP"] == "77.0")
    check("pitching ERA stays in the ERA column", pitched["ERA"] == "4.44")
    check("pitching SO stays in the SO column", pitched["SO"] == "85")

    hitting = {
        "2024": {"hitting": {
            "gamesPlayed": 40, "atBats": 100, "hits": 25, "homeRuns": 4,
            "rbi": 10, "stolenBases": 2, "runs": 20, "doubles": 5, "triples": 1,
            "baseOnBalls": 10, "strikeOuts": 30,
        }},
        "2025": {"hitting": {
            "gamesPlayed": 60, "atBats": 200, "hits": 50, "homeRuns": 8,
            "rbi": 20, "stolenBases": 3, "runs": 30, "doubles": 10, "triples": 2,
            "baseOnBalls": 15, "strikeOuts": 40,
        }},
    }
    hitting_html = render_player_sections(
        {"id": 2, "name": "Career Bat", "position": "CF", "season_stats": hitting},
        None, CLUB_NAMES, TODAY, WINDOWS,
    )
    headers, career = _stat_tables(hitting_html)["Batting"]
    check(
        "batting header is the batting column list",
        headers == [label for _key, label in HITTING_COLUMNS],
    )
    check("batting career row has one cell per header", career is not None and len(career) == len(headers))
    batted = dict(zip(headers, career))
    check("batting Year cell is Career", batted["Year"] == "Career")
    check("batting team cell on the career row is blank", batted["Tm"] == "")
    check("batting AB stays in the AB column", batted["AB"] == "300")
    check("batting H stays in the H column", batted["H"] == "75")
    check("batting HR stays in the HR column", batted["HR"] == "12")
    check("batting AVG stays in the AVG column", batted["AVG"] == ".250")
    check("batting OPS stays in the OPS column", batted["OPS"] == "–")

    css = (ROOT / "docs" / "styles.css").read_text()
    leaked = _phone_column_hides(css)
    check(
        "a phone does not hide a stat column the career row still shows",
        leaked == [],
    )
    check(
        "Pos stays hidden on the players table",
        "#players-table thead th:nth-child(4)" in css
        and "#players-table tbody td:nth-child(4)" in css,
    )


def test_sections_appear_only_when_there_is_data_and_collapse_rules_hold():
    baker_html = render_player_sections(
        {"id": 641329, "name": "Bryan Baker", "position": "P", "mlb_debut": "2021-09-05",
         "seasons": [{"y": 2021, "d": 10, "t": [141]}]},
        rows(641329), CLUB_NAMES, TODAY, WINDOWS,
    )
    moves_block = baker_html.split('id="moves"', 1)[1].split("</section>", 1)[0]
    check("more than 8 career moves start collapsed", "<details open>" not in moves_block)
    check("the collapsed moves are still in the html", "Signed with the Colorado Rockies" in moves_block)

    betts_html = render_player_sections(
        {"id": 605141, "name": "Mookie Betts", "position": "SS", "mlb_debut": "2014-06-29",
         "seasons": [{"y": 2014, "d": 50, "t": [111]}]},
        rows(605141), CLUB_NAMES, TODAY, WINDOWS,
    )
    betts_moves = betts_html.split('id="moves"', 1)[1].split("</section>", 1)[0]
    check("8 career moves start open", "<details open>" in betts_moves)
    check("Betts page does not copy an injury word from the feed", "concussion" not in betts_html.lower() and "fracture" not in betts_html.lower() and "oblique" not in betts_html.lower())

    bare = render_player_sections(
        {"id": 42, "name": "No Cache", "position": "SS"},
        None, CLUB_NAMES, TODAY, WINDOWS,
    )
    check("no cache and no stats renders nothing", bare == "")


def main() -> None:
    test_baker_has_no_major_league_injured_list_time()
    test_betts_stints_match_the_cached_feed()
    test_effective_date_moves_the_start_when_the_text_does_not()
    test_adames_missing_return_is_a_lower_bound()
    test_arrighetti_trade_does_not_close_the_stay()
    test_ohtani_same_day_lists_are_one_stay()
    test_buxton_option_missed_return_and_conversion()
    test_rendon_long_history_and_the_february_placement()
    test_november_placement_is_not_a_stay()
    test_timeline_uses_ids_and_drops_noise()
    test_which_stat_tables_show()
    test_career_row_matches_the_header()
    test_stored_profiles_pick_the_right_tables()
    test_open_stay_does_not_change_the_page_hash_during_the_season()
    test_sections_appear_only_when_there_is_data_and_collapse_rules_hold()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
