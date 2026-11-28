"""2020 opens on 24 July for 26 clubs, and on 23 July for four.

    python3 tests/test_2020_opener.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cba  # noqa: E402
import rulebook_publish as pub  # noqa: E402
import season_openers  # noqa: E402
from rulebook import Calendar, SeasonCal, evaluate  # noqa: E402

PASS = 0
FAIL = 0

NYY = 147
WSH = 120
LAD = 119
SF = 137
STL = 138


def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def season(year, start, end):
    return {
        "season": year,
        "championship_start": start,
        "championship_end": end,
        "first_domestic_game": start,
        "games_outside_us_and_canada": [],
        "tiebreakers_excluded": [],
    }


def txn(day, code, description, to=None, frm=None):
    row = {
        "date": day,
        "typeCode": code,
        "typeDesc": code,
        "description": description,
        "id": abs(hash((day, code, description))) % 100000,
    }
    if to is not None:
        row["to_team_id"] = to
    if frm is not None:
        row["from_team_id"] = frm
    return row


def credited(rows, debut, horizon, clubs=None):
    result = evaluate(
        rows,
        player_id=1,
        debut=dt.date.fromisoformat(debut),
        horizon=dt.date.fromisoformat(horizon),
        calendar=Calendar([SeasonCal(season(2020, "2020-07-23", "2020-09-27"))]),
        exceptions=[],
        roy_years=set(),
        season_clubs=clubs,
    )
    for row in result["seasons"]:
        if row["year"] == 2020:
            return row["credited_days"], row["raw_days"], row["scale_start"]
    return 0, 0, None


def test_the_four_clubs_keep_23_july():
    check("Yankees open 23 July", season_openers.proration_start(2020, season_openers.GENERAL_OPENER, NYY) == dt.date(2020, 7, 23))
    check("Nationals open 23 July", season_openers.proration_start(2020, season_openers.GENERAL_OPENER, WSH) == dt.date(2020, 7, 23))
    check("Dodgers open 23 July", season_openers.proration_start(2020, season_openers.GENERAL_OPENER, LAD) == dt.date(2020, 7, 23))
    check("Giants open 23 July", season_openers.proration_start(2020, season_openers.GENERAL_OPENER, SF) == dt.date(2020, 7, 23))
    check("another club opens 24 July", season_openers.proration_start(2020, dt.date(2020, 7, 23), STL) == dt.date(2020, 7, 24))
    check("an unknown club opens 24 July", season_openers.proration_start(2020, dt.date(2020, 7, 23), None) == dt.date(2020, 7, 24))
    check("2021 keeps its own opener", season_openers.proration_start(2021, dt.date(2021, 4, 1), NYY) == dt.date(2021, 4, 1))


def test_a_24_july_club_scales_on_that_length():
    rules = cba.default()
    cap = rules.require("service_time.max_days_creditable_per_season")
    scale = rules.require("service_time.shortened_seasons.2020.scale_to_span_days")
    days, raw, scale_start = credited(
        [
            txn("2020-07-24", "SE", "Cardinals selected the contract of X.", to=STL),
            txn("2020-08-01", "REL", "Cardinals released X.", frm=STL),
        ],
        "2020-07-24",
        "2020-10-05",
    )
    # 24 July through 31 July. The scale runs 24 July through 27 September.
    span = (dt.date(2020, 9, 27) - dt.date(2020, 7, 24)).days + 1
    expected = min(cap, int(round(raw * scale / span)))
    check("a Cardinals partial season uses the 24 July length", days == expected and scale_start == "2020-07-24")
    check("that length is shorter than the first-game length", span == 66)
    full, _raw, _start = credited(
        [txn("2020-07-24", "SE", "Cardinals selected the contract of X.", to=STL)],
        "2020-07-24",
        "2020-10-05",
    )
    check("a full season on a 24 July club is one credited year", full == cap)


def test_a_yankees_partial_season_keeps_23_july():
    rules = cba.default()
    cap = rules.require("service_time.max_days_creditable_per_season")
    scale = rules.require("service_time.shortened_seasons.2020.scale_to_span_days")
    days, raw, scale_start = credited(
        [
            txn("2020-07-23", "SE", "Yankees selected the contract of X.", to=NYY),
            txn("2020-08-01", "REL", "Yankees released X.", frm=NYY),
        ],
        "2020-07-23",
        "2020-10-05",
    )
    span = (dt.date(2020, 9, 27) - dt.date(2020, 7, 23)).days + 1
    expected = min(cap, int(round(raw * scale / span)))
    check("a Yankees partial season uses 23 July", days == expected and scale_start == "2020-07-23" and span == 67)


def test_the_scale_does_not_replace_a_published_total():
    """A total already on the site at the 23 July length stays there."""
    rules = cba.default()
    normal = rules.require("service_time.shortened_seasons.2020.scale_to_span_days")
    cap = rules.require("service_time.max_days_creditable_per_season")
    raw = 34
    early = (dt.date(2020, 9, 27) - dt.date(2020, 7, 23)).days + 1
    general = (dt.date(2020, 9, 27) - dt.date(2020, 7, 24)).days + 1
    published = min(cap, int(round(raw * normal / early)))
    newer = min(cap, int(round(raw * normal / general)))
    player = {
        "id": 7,
        "name": "Example",
        "on_40_man": False,
        "service_time": "0.000",
        "service_days_total": published,
        "seasons": [{"y": 2020, "d": published, "raw": raw, "pro": raw, "t": [STL], "src": "read"}],
        "rules_version": 10,
    }
    result = {"seasons": [{"year": 2020, "credited_days": newer, "raw_days": raw}], "review_flags": [], "ambiguous": []}
    fields, holds, _skipped = pub.apply_player(player, result, {}, cap)
    check("the published 2020 total is not raised", fields["seasons"][0]["d"] == published and not holds)
    check("the two lengths differ", newer != published)


def _published_player(player_id):
    data = json.loads((ROOT / "docs" / "data" / "service_time.json").read_text(encoding="utf-8"))
    player = next(row for row in data["players"] if int(row["id"]) == player_id)
    season = next(row for row in player["seasons"] if row.get("y") == 2020)
    horizon = dt.date.fromisoformat(data["generated_at"][:10])
    result = pub.replay_one(player, horizon)
    got = next(row["credited_days"] for row in result["seasons"] if row["year"] == 2020)
    return player["name"], int(season["d"]), got


def test_two_published_seasons_now_match():
    """Aaron Fletcher and Aaron Slegers were held on the 24 July length."""
    for player_id, label in ((667465, "Fletcher"), (642083, "Slegers")):
        name, published, got = _published_player(player_id)
        check(f"{name} 2020 matches the published {published} days", got == published)


def test_a_23_july_club_stays_on_that_length():
    name, published, got = _published_player(502578)
    check(f"{name} keeps the published {published} days", got == published)


def main():
    print("2020 opener")
    test_the_four_clubs_keep_23_july()
    test_a_24_july_club_scales_on_that_length()
    test_a_yankees_partial_season_keeps_23_july()
    test_the_scale_does_not_replace_a_published_total()
    test_two_published_seasons_now_match()
    test_a_23_july_club_stays_on_that_length()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
