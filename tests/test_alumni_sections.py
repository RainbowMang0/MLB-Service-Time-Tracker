"""Alumni page sections: who is fetched, what is stored, what the page shows.

No network. The fetch itself stays in scripts/alumni_sections.py and is
not part of this run.

    python3 tests/test_alumni_sections.py
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import alumni_sections as alumni  # noqa: E402
from il_stints import SeasonWindows  # noqa: E402
from player_sections import render_player_sections  # noqa: E402

PASS = 0
FAIL = 0
TODAY = dt.date(2026, 11, 6)
WINDOWS = SeasonWindows.load()
NAMES = {120: "Washington Nationals", 146: "Miami Marlins"}


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def _player(**extra) -> dict:
    record = {
        "id": 900001,
        "name": "Fixture Alum",
        "position": "3B",
        "team": "Washington Nationals",
        "team_id": 120,
        "on_40_man": False,
        "mlb_debut": "2013-04-21",
        "service_time": "4.000",
        "service_days_total": 600,
        "missing_seasons": 0,
        "seasons": [
            {"y": 2013, "d": 100, "t": [120]},
            {"y": 2016, "d": 120, "t": [120]},
        ],
    }
    record.update(extra)
    return record


def _rows() -> list[dict]:
    return [
        {
            "date": "2011-08-15",
            "description": "Washington Nationals signed 3B Fixture Alum. Right oblique strain.",
            "to_team_id": 120,
            "team_id": 120,
            "typeCode": "SGN",
            "typeDesc": "Signed",
            "effectiveDate": "2011-08-15",
            "resolutionDate": None,
            "id": 55,
        },
        {
            "date": "2016-04-19",
            "description": (
                "Washington Nationals placed 3B Fixture Alum on the 10-day disabled list. "
                "Left hamstring strain."
            ),
            "to_team_id": 120,
            "team_id": 120,
            "typeCode": "SC",
            "typeDesc": "Status Change",
            "effectiveDate": "2016-04-19",
            "id": 56,
        },
        {
            "date": "2016-05-05",
            "description": "Washington Nationals activated 3B Fixture Alum from the 10-day disabled list.",
            "to_team_id": 120,
            "team_id": 120,
            "typeCode": "SC",
            "effectiveDate": "2016-05-05",
            "id": 57,
        },
    ]


def _stats() -> dict:
    return {
        2016: {
            "hitting": {
                "gamesPlayed": 80,
                "atBats": 300,
                "runs": 40,
                "hits": 90,
                "doubles": 18,
                "triples": 1,
                "homeRuns": 12,
                "rbi": 48,
                "baseOnBalls": 30,
                "strikeOuts": 70,
                "stolenBases": 4,
                "avg": ".300",
                "obp": ".370",
                "slg": ".480",
                "ops": ".850",
                "team_id": 120,
            }
        }
    }


def test_discovery_skips_cached_and_rostered() -> None:
    page_ids = {1, 2, 3, 4}
    cached = {2}
    players = {
        1: {"id": 1, "on_40_man": False},
        2: {"id": 2, "on_40_man": False},
        3: {"id": 3, "on_40_man": True},
    }
    found = alumni.discover_alumni_ids(page_ids, cached, players)
    check("cached alumni are skipped", found == [1])
    check("a stored id is not fetched again", alumni.ids_to_fetch([1, 5], {1}, refresh=False) == [5])
    check("a refresh fetches stored ids", alumni.ids_to_fetch([1], {1}, refresh=True) == [1])

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        pages = root / "p"
        cache = root / "cache"
        pages.mkdir()
        cache.mkdir()
        (pages / "11-cached-alum.html").write_text("<html></html>")
        (pages / "22-open-alum.html").write_text("<html></html>")
        (pages / "index.html").write_text("<html></html>")
        (cache / "11.json").write_text("[]")
        check("page ids come from the filename", alumni.published_page_ids(pages) == {11, 22})
        check("cache ids are the stems", alumni.cached_transaction_ids(cache) == {11})
        disk = alumni.discover_alumni_ids(
            alumni.published_page_ids(pages),
            alumni.cached_transaction_ids(cache),
            {11: {"on_40_man": False}, 22: {"on_40_man": False}},
        )
        check("the on-disk cached page is skipped", disk == [22])


def test_compact_record_drops_the_feed_sentence() -> None:
    compact = alumni.build_compact(_player(), _rows(), _stats(), NAMES, TODAY, WINDOWS)
    raw = json.dumps(compact)
    check("no feed field is stored", not alumni.contains_feed_key(compact))
    check("the injury sentence is not stored", "oblique" not in raw and "hamstring" not in raw and "strain" not in raw)
    check("the stay is kept", len(compact["stints"]) == 1 and compact["stints"][0]["kind"] == "exact")
    check("the list length is kept", compact["stints"][0]["lists"][0][0] == 10)
    check("the move is our sentence", any(move["text"] == "Signed with the Washington Nationals" for move in compact["moves"]))
    check("debut is a move", any(move["kind"] == "debut" for move in compact["moves"]))
    check("season lines are kept", compact["season_stats"]["2016"]["hitting"]["homeRuns"] == 12)
    check("feed covers this debut, so no thin year", compact["il_record_year"] is None)
    check("allowed top-level keys only", set(compact) == {
        "feed_start", "il_record_year", "stints", "moves", "season_stats",
    })

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        alumni.save_record(900001, compact, root)
        loaded = alumni.load_store(root)
        check("a shard round-trip keeps the record", loaded[900001]["moves"][0]["text"].startswith("Signed"))
        check("the shard has no feed field", not alumni.contains_feed_key(loaded))
        check("one shard file, not a raw dump", len(list(root.glob("*.json"))) == 1)


def test_page_gains_the_three_sections() -> None:
    import write_player_pages as pages

    player = _player()
    before = pages.render(player, NAMES, "2026-11-06T00:00:00+00:00", [], None, None)
    check("no compact means no injured-list block", 'id="injuries"' not in before)
    check("no compact means no career moves", 'id="moves"' not in before)

    compact = alumni.build_compact(player, _rows(), _stats(), NAMES, TODAY, WINDOWS)
    after = pages.render(player, NAMES, "2026-11-06T00:00:00+00:00", [], None, compact)
    check("the injured list is on the page", 'id="injuries"' in after and ">10-day<" in after)
    check("season stats are on the page", 'id="stats"' in after and ">12<" in after and "public data" in after)
    check("career moves are on the page", 'id="moves"' in after and "Signed with the Washington Nationals" in after)
    check("the debut line is on the page", "Made his major league debut with the Washington Nationals" in after)
    check("the feed sentence is not on the page", "hamstring" not in after.lower() and "oblique" not in after.lower())
    check("the service-time figure is untouched", ">4.000<" in after)


def test_thin_history_wording() -> None:
    check("a covered debut is not thin", alumni.il_record_year(2016, 2011) is None)
    check("rows that start late name that year", alumni.il_record_year(2008, 2016) == 2016)
    check("an empty pre-2015 feed names 2015", alumni.il_record_year(2010, None) == 2015)
    check("an empty modern feed is a real zero", alumni.il_record_year(2018, None) is None)
    check("no debut has nothing to compare", alumni.il_record_year(None, None) is None)

    thin_rows = [{
        "date": "2016-04-19",
        "description": "Washington Nationals placed 3B Thin Feed on the 10-day injured list. Oblique strain.",
        "to_team_id": 120,
        "effectiveDate": "2016-04-19",
    }, {
        "date": "2016-05-01",
        "description": "Washington Nationals activated 3B Thin Feed from the 10-day injured list.",
        "to_team_id": 120,
    }]
    player = _player(mlb_debut="2008-04-02", name="Thin Feed")
    compact = alumni.build_compact(player, thin_rows, {}, NAMES, TODAY, WINDOWS)
    check("the stored year is the first row", compact["il_record_year"] == 2016)
    html = render_player_sections(player, None, NAMES, TODAY, WINDOWS, compact)
    check("the table and the thin note both show", ">10-day<" in html and "IL history in the public record begins in 2016." in html)
    check("a thin record is not called a zero", "No major league injured-list time" not in html)

    empty = alumni.build_compact(
        _player(mlb_debut="2007-06-01", name="No Rows"), [], {}, NAMES, TODAY, WINDOWS,
    )
    empty_html = render_player_sections(
        _player(mlb_debut="2007-06-01"), None, NAMES, TODAY, WINDOWS, empty,
    )
    check("no rows and an early debut use the thin line", "IL history in the public record begins in 2015." in empty_html)
    check("that page does not claim a zero", "No major league injured-list time" not in empty_html)

    modern = alumni.build_compact(
        _player(mlb_debut="2019-04-01", name="Modern Zero"), [], {}, NAMES, TODAY, WINDOWS,
    )
    modern_html = render_player_sections(
        _player(mlb_debut="2019-04-01"), None, NAMES, TODAY, WINDOWS, modern,
    )
    check("a covered empty feed keeps the zero line", "No major league injured-list time in the public record." in modern_html)


def main() -> None:
    test_discovery_skips_cached_and_rostered()
    test_compact_record_drops_the_feed_sentence()
    test_page_gains_the_three_sections()
    test_thin_history_wording()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
