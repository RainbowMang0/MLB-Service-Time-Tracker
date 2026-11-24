"""2026 debuts page from fixtures. No live roster.

    python3 tests/test_debuts.py
"""

from __future__ import annotations

import datetime as dt
import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import debuts_pages as debuts  # noqa: E402
import list_links  # noqa: E402
import page_titles  # noqa: E402
import table_html  # noqa: E402
import write_player_pages as wpp  # noqa: E402
from il_stints import SeasonWindows  # noqa: E402
from mlb_clubs import CLUB_NAMES  # noqa: E402

PASS = 0
FAIL = 0
TODAY = dt.date(2026, 11, 23)
YANKEES = 147
RED_SOX = 111
WHITE_SOX = 145


def check(label, ok):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL  {label}")


def _bounds(start, end, clubs=None):
    return {
        "start": dt.date.fromisoformat(start),
        "end": dt.date.fromisoformat(end),
        "clubs": clubs or {},
        "ends": {},
    }


def windows():
    return SeasonWindows({
        2026: _bounds("2026-03-26", "2026-09-27", {WHITE_SOX: dt.date(2026, 3, 25)}),
        2027: _bounds("2027-03-25", "2027-10-01"),
    })


def person(**kwargs):
    base = {
        "id": 101,
        "name": "Pat Fixture",
        "team": CLUB_NAMES[YANKEES],
        "team_id": YANKEES,
        "on_40_man": True,
        "position": "CF",
        "mlb_debut": "2026-08-01",
        "season_stats": {
            "2026": {"hitting": {"gamesPlayed": 10, "atBats": 12, "team_id": YANKEES}},
        },
        "seasons": [{"y": 2026, "d": 20, "t": [YANKEES]}],
    }
    base.update(kwargs)
    return base


def txn(day, desc):
    return {
        "date": day,
        "description": desc,
        "team_id": YANKEES,
        "from_team_id": YANKEES,
        "to_team_id": YANKEES,
    }


def test_the_season_window_includes_an_early_opener():
    bounds = windows()
    check("November is still the season that just ended", debuts.season_year(TODAY, bounds) == 2026)
    check(
        "before the next opener the previous season stays",
        debuts.season_year(dt.date(2027, 3, 1), bounds) == 2026,
    )
    check(
        "after the next opener the new season starts",
        debuts.season_year(dt.date(2027, 4, 2), bounds) == 2027,
    )
    check("a club opener before the league date counts", debuts.in_season(dt.date(2026, 3, 25), 2026, bounds))
    check("a date after the season does not count", not debuts.in_season(dt.date(2026, 11, 1), 2026, bounds))
    check("a prior season does not count", not debuts.in_season(dt.date(2025, 8, 1), 2026, bounds))


def test_totals_use_the_first_club_and_the_right_line():
    hitter = person()
    text, club = debuts.season_summary(hitter, 2026)
    check("a hitter's line is games and at-bats", text == "10 G, 12 AB")
    check("a one-club season uses that club", club == YANKEES)
    traded = person(
        id=202,
        name="Ada Arm",
        position="P",
        team=CLUB_NAMES[RED_SOX],
        team_id=RED_SOX,
        mlb_debut="2026-05-18",
        season_stats={
            "2026": {
                "pitching": {
                    "gamesPlayed": 30,
                    "inningsPitched": "31.0",
                    "numTeams": 2,
                    "splits": [
                        {"team_id": YANKEES, "gamesPlayed": 22, "inningsPitched": "22.0"},
                        {"team_id": RED_SOX, "gamesPlayed": 8, "inningsPitched": "9.0"},
                    ],
                },
            },
        },
    )
    text, club = debuts.season_summary(traded, 2026)
    check("a pitcher line is games and innings", text == "30 G, 31.0 IP")
    check("the first club he pitched for is the debut club", club == YANKEES)
    check("the current club is not treated as the debut club", club != RED_SOX)
    bare = person(
        id=303,
        name="No Line",
        season_stats={},
        seasons=[{"y": 2026, "d": 4, "t": [RED_SOX]}],
        team_id=YANKEES,
        team=CLUB_NAMES[YANKEES],
    )
    text, club = debuts.season_summary(bare, 2026)
    check("a missing stat line stays blank", text == "")
    check("one stored club is the fallback", club == RED_SOX)


def test_the_list_is_newest_first_and_links_real_pages_only():
    bounds = windows()
    early = person(id=1, name="Early Fixture", mlb_debut="2026-04-02")
    late = person(id=2, name="Late Fixture", mlb_debut="2026-09-20", position="P", season_stats={
        "2026": {"pitching": {"gamesPlayed": 3, "inningsPitched": "4.1", "team_id": YANKEES}},
    })
    outside = person(id=3, name="Winter Fixture", mlb_debut="2026-11-01")
    old = person(id=4, name="Old Fixture", mlb_debut="2019-04-01")
    docs = ROOT / "docs"
    rows = debuts.collect(
        [early, late, outside, old],
        TODAY,
        tx_for=lambda _player: None,
        windows=bounds,
        docs=docs,
    )
    names = [row.name for row in rows]
    check("only in-season debuts are listed", names == ["Late Fixture", "Early Fixture"])
    check("the later debut is first", rows[0].debut.isoformat() == "2026-09-20")
    check("a pitcher total is innings", rows[0].totals == "3 G, 4.1 IP")
    check("a fixture has no live player page", all(not row.href for row in rows))

    folder = ROOT / "tmp-debuts-pages"
    folder.mkdir(exist_ok=True)
    try:
        rel = wpp.page_path(late)
        dest = folder / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("x", encoding="utf-8")
        linked = debuts.collect([late], TODAY, tx_for=lambda _player: None, windows=bounds, docs=folder)
    finally:
        for path in folder.rglob("*"):
            if path.is_file():
                path.unlink()
        for path in sorted(folder.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
        folder.rmdir()
    check("a page on disk is linked", linked and linked[0].href.endswith(rel))


def test_rookie_status_is_shown_only_when_the_rookie_page_would():
    eligible = person(
        season_stats={"2026": {"hitting": {"atBats": 12, "gamesPlayed": 10, "team_id": YANKEES}}},
    )
    rows = [
        txn("2026-08-01", "New York Yankees recalled OF Pat Fixture from Scranton."),
        txn("2026-08-20", "New York Yankees optioned OF Pat Fixture to Scranton."),
    ]
    check(
        "a supported rookie is named",
        debuts.rookie_phrase(eligible, rows, TODAY) == "Rookie eligible",
    )
    graduated = person(
        id=104,
        name="Gina Graduate",
        position="P",
        mlb_debut="2026-04-01",
        season_stats={"2026": {"pitching": {"inningsPitched": "51.0", "gamesPlayed": 12, "team_id": YANKEES}}},
    )
    check(
        "a limit crossed this season says so",
        debuts.rookie_phrase(graduated, [], TODAY) == "Rookie eligibility used up",
    )
    unknown = person(id=105, name="Uma Unknown", on_40_man=True, season_stats={})
    check(
        "an unconfirmed 40-man player says so",
        debuts.rookie_phrase(unknown, None, TODAY) == "Rookie status unconfirmed",
    )
    unknown["on_40_man"] = False
    check("an unconfirmed former player is left off", debuts.rookie_phrase(unknown, None, TODAY) == "")


def test_the_page_and_the_player_line_use_fixtures():
    bounds = windows()
    late = person(id=2, name="Late Fixture", mlb_debut="2026-09-20")
    traded = person(
        id=202,
        name="Ada Arm",
        position="P",
        team=CLUB_NAMES[RED_SOX],
        team_id=RED_SOX,
        mlb_debut="2026-05-18",
        on_40_man=False,
        season_stats={
            "2026": {
                "pitching": {
                    "gamesPlayed": 30,
                    "inningsPitched": "31.0",
                    "numTeams": 2,
                    "splits": [
                        {"team_id": YANKEES, "gamesPlayed": 22, "inningsPitched": "22.0"},
                        {"team_id": RED_SOX, "gamesPlayed": 8, "inningsPitched": "9.0"},
                    ],
                },
            },
        },
    )
    rows = debuts.collect(
        [late, traded],
        TODAY,
        tx_for=lambda player: [] if player.get("id") == 2 else None,
        windows=bounds,
        docs=ROOT / "docs",
    )
    raw = debuts.render(rows, 2026)
    final = table_html.wrap_bare_tables(raw)
    intro = raw.split('<p class="subtitle">')[1].split("</p>")[0]
    check("one h1", raw.count("<h1>") == 1)
    check("the intro is one sentence", intro.count(".") == 1 and intro.endswith("."))
    check("newest debut is first", final.index("Late Fixture") < final.index("Ada Arm"))
    check("the debut date is written out", "September 20, 2026" in final and 'datetime="2026-09-20"' in final)
    check("the hitter line is at-bats", "10 G, 12 AB" in final)
    check("the pitcher line is innings", "30 G, 31.0 IP" in final)
    yankees = wpp.club_path(CLUB_NAMES[YANKEES])
    red_sox = wpp.club_path(CLUB_NAMES[RED_SOX])
    table = final.split("<table>", 1)[1].split("</table>", 1)[0]
    nav = final.split('aria-label="Debuts by club"', 1)[1].split("</nav>", 1)[0]
    check("the debut club is linked", f'href="/{yankees}"' in table)
    check("the later club is not the debut club", f'href="/{red_sox}"' not in table and f'href="/{red_sox}"' not in nav)
    check("the count names the club", f"{CLUB_NAMES[YANKEES]} (2)" in final)
    check("rookie status links the rookie page", 'href="/rookie-status.html"' in final)
    check("a fixture name is not a player link", 'href="/p/2-late-fixture.html"' not in final)
    check("the page stylesheet is its own file", 'href="/debuts.css"' in raw)
    check("wide tables scroll", 'class="tbl-scroll"' in final and "Swipe sideways" in final)
    check("no last-updated line", "last updated" not in final.lower())
    lowered = final.lower()
    for banned in ("baseball-reference", "fangraphs", "spotrac", "mlb trade rumors", "cot's"):
        check(f"no {banned}", banned not in lowered)
    check("says public data", "public data" in lowered)
    line = debuts.player_line(late, TODAY, bounds)
    check("a listed debut gets a debuted line", line.startswith("\n  <p class=\"list-links\">Debuted ") and "September 20, 2026" in line and line.endswith(".</p>"))
    check("the line links this page", 'href="/debuts.html"' in line)
    check("an older debut gets no line", debuts.player_line(person(mlb_debut="2019-04-01"), TODAY, bounds) == "")
    check(
        "a debut after the season gets no line",
        debuts.player_line(person(mlb_debut="2026-11-01"), TODAY, bounds) == "",
    )


def test_titles_fit_and_the_pipeline_names_the_page():
    title = page_titles.debuts_title(2026)
    meta = page_titles.debuts_meta(12, 2026)
    check("title fits", len(title) <= page_titles.TITLE_MAX and title.startswith("MLB debuts in 2026"))
    check("meta fits", len(meta) <= page_titles.META_MAX and "public data" in meta and "12" in meta)
    text = (ROOT / "scripts" / "write_player_pages.py").read_text(encoding="utf-8")
    check("the roster rebuild writes the page", "debuts_pages.write" in text)
    check("the sitemap names the page", '"debuts.html"' in text)
    check("a player page can say he debuted", "debuts_pages" in text and "player_line" in text)
    for name in ("update-service-time.yml", "backfill-history.yml"):
        body = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        check(f"{name} commits the page", "docs/debuts.html" in body)
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    check("ci runs this suite", "tests/test_debuts.py" in ci)
    check("ci checks the built page", "scripts/debuts_pages.py --check" in ci)
    hub = (ROOT / "scripts" / "build_offseason_page.py").read_text(encoding="utf-8")
    check("the offseason hub can link the page", "debuts.html" in hub)
    lists = (ROOT / "scripts" / "list_links.py").read_text(encoding="utf-8")
    check("league pages link it", "debuts.html" in lists)
    tags = (ROOT / "scripts" / "site_tags.py").read_text(encoding="utf-8")
    check("the list ad slot includes it", "debuts.html" in tags)
    published = (ROOT / "scripts" / "validate_published.py").read_text(encoding="utf-8")
    check("published-link check includes it", "debuts.html" in published)
    core = (ROOT / "docs" / "sitemap-core.xml").read_text(encoding="utf-8")
    check("sitemap lists the page", "/debuts.html</loc>" in core)
    css = (ROOT / "docs" / "page.css").read_text(encoding="utf-8")
    check("page.css matches the generator", css == wpp.PAGE_CSS)
    check(
        "the dfa club rules are one per line",
        ".list-links a:hover { text-decoration: underline; }\n"
        ".dfa-clubs { max-width: none; }\n"
        ".dfa-clubs a { white-space: nowrap; }" in css,
    )
    check("page.css does not put two rules on one line", "} ." not in css)
    taxes = (ROOT / "docs" / "taxes.html").read_bytes()
    check(
        "taxes.html is unchanged",
        hashlib.sha256(taxes).hexdigest() == "ef78b2f8160046145ce58382a0da8a55dc5ab3b98130fab271db847e1a45fe62",
    )


def main():
    test_the_season_window_includes_an_early_opener()
    test_totals_use_the_first_club_and_the_right_line()
    test_the_list_is_newest_first_and_links_real_pages_only()
    test_rookie_status_is_shown_only_when_the_rookie_page_would()
    test_the_page_and_the_player_line_use_fixtures()
    test_titles_fit_and_the_pipeline_names_the_page()
    print(f"{PASS} passed, {FAIL} failed")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
