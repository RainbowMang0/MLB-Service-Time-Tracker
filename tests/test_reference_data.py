#!/usr/bin/env python3
"""
Tests for the reference files the service-time rewrite will read.

data/reference/season_calendar.json and
data/reference/service_time_exceptions.json are not wired into the
calculator. These checks only validate the files.

Run directly: python3 tests/test_reference_data.py
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CALENDAR_PATH = ROOT / "data" / "reference" / "season_calendar.json"
EXCEPTIONS_PATH = ROOT / "data" / "reference" / "service_time_exceptions.json"
ENGINE_EXCEPTIONS_PATH = ROOT / "data" / "service_time_exceptions.json"
PLAYERS_PATH = ROOT / "docs" / "data" / "service_time.json"

# An ordinary championship season, with no stoppage and no opener outside
# the United States and Canada long enough to stretch the calendar.
ORDINARY_MIN_DAYS = 181
ORDINARY_MAX_DAYS = 187

COUNTS_TRUE = {"paid_administrative_leave", "covid_high_risk_opt_out", "commissioner_suspension"}
COUNTS_FALSE = {"unpaid_suspension", "unpaid_restricted_list", "covid_opt_out"}
KINDS = COUNTS_TRUE | COUNTS_FALSE

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


def load(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_iso(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def urls_of(source) -> list[str]:
    if isinstance(source, str):
        return [source]
    if isinstance(source, list):
        return [u for u in source if isinstance(u, str)]
    return []


def players_by_id() -> dict[int, dict]:
    data = load(PLAYERS_PATH)
    rows = data["players"]
    return {int(p["id"]): p for p in rows}


def test_calendar_covers_every_stored_season():
    calendar = load(CALENDAR_PATH)
    seasons = calendar["seasons"]
    years = [row["season"] for row in seasons]
    check("the calendar has a schema note", bool(calendar.get("_schema")))
    check("seasons are a non-empty list", bool(seasons))
    check("seasons are sorted and unique", years == sorted(set(years)))

    players = players_by_id()
    debuts = [p.get("mlb_debut") for p in players.values() if p.get("mlb_debut")]
    first_year = min(int(d[:4]) for d in debuts)
    last_year = max(int(d[:4]) for d in debuts)
    check("stored debuts reach back to 1986", first_year == 1986)
    check(
        "every season from the earliest debut through the latest is present",
        years == list(range(first_year, last_year + 1)),
    )


def test_calendar_dates_and_day_counts():
    calendar = load(CALENDAR_PATH)
    for row in calendar["seasons"]:
        year = row["season"]
        start = parse_iso(row["championship_start"])
        end = parse_iso(row["championship_end"])
        domestic = parse_iso(row["first_domestic_game"])
        check(f"{year} start is an ISO date", start is not None and start.year == year)
        check(f"{year} end is an ISO date on or after the start", end is not None and start is not None and end >= start)
        check(f"{year} domestic opener is on or after the league start", domestic is not None and start is not None and domestic >= start)
        if start is None or end is None or domestic is None:
            continue
        span = (end - start).days + 1
        days = row["championship_days"]
        special = row["special"] is True
        note = (row["notes"] or "").strip()
        if special:
            check(f"{year} is a documented special year", bool(note))
            # A special year may store a day count other than the inclusive
            # span. When it does, the note has to say why. The inclusive span
            # is always accepted.
            if days != span:
                check(f"{year} explains a day count that is not the span", str(days) in note)
            else:
                check(f"{year} special-year day count matches its dates", True)
        else:
            check(f"{year} day count matches its dates", days == span)
            check(
                f"{year} is an ordinary {ORDINARY_MIN_DAYS}-{ORDINARY_MAX_DAYS} day season",
                ORDINARY_MIN_DAYS <= days <= ORDINARY_MAX_DAYS,
            )
        source = row["source"]
        check(
            f"{year} cites the MLB schedule feed",
            isinstance(source, str)
            and source.startswith("https://statsapi.mlb.com/api/v1/schedule?")
            and f"season={year}" in source
            and "gameType=R" in source,
        )

        outside = row["games_outside_us_and_canada"]
        if start < domestic:
            dates = [parse_iso(g["date"]) for g in outside]
            check(f"{year} records the games before the domestic opener", bool(dates) and None not in dates)
            if dates and None not in dates:
                check(f"{year} league start is the first of those games", min(dates) == start)
                check(f"{year} those games are all before the domestic opener", all(d < domestic for d in dates))
        else:
            check(f"{year} has no pre-domestic games when the openers match", outside == [])

        for game in row["tiebreakers_excluded"]:
            played = parse_iso(game["date"])
            check(
                f"{year} excluded tiebreaker {game['date']} is after the championship end",
                played is not None and played > end,
            )

        openers = row["club_openers"]
        opener_dates = [parse_iso(v) for v in openers.values()]
        check(f"{year} has a club opener on the league start", start in opener_dates)
        check(
            f"{year} club openers fall inside the championship season",
            all(d is not None and start <= d <= end for d in opener_dates),
        )
        expected_clubs = 26 if year <= 1992 else 28 if year <= 1997 else 30
        check(f"{year} has {expected_clubs} club openers", len(openers) == expected_clubs)


def test_hand_checked_seasons():
    """The dates the off-by-one review already pinned down.

    Ohtani 2025 opened in Tokyo, before the domestic schedule. Buxton's Twins
    opened the day after the 2016 league start. Beck's Rockies opened two days
    after the first 2026 game, which was itself a domestic game.
    """
    by_year = {row["season"]: row for row in load(CALENDAR_PATH)["seasons"]}

    y2025 = by_year[2025]
    check("2025 starts on the Tokyo opener", y2025["championship_start"] == "2025-03-18")
    check("2025 domestic games start later", y2025["first_domestic_game"] == "2025-03-27")
    check("the Dodgers opened in Tokyo", y2025["club_openers"]["119"] == "2025-03-18")
    check("the Cubs opened in Tokyo", y2025["club_openers"]["112"] == "2025-03-18")
    countries = {g["country"] for g in y2025["games_outside_us_and_canada"]}
    check("2025's early games were in Japan", countries == {"Japan"})

    y2016 = by_year[2016]
    check("2016 league start is April 3", y2016["championship_start"] == "2016-04-03")
    check("2016 domestic start is the league start", y2016["first_domestic_game"] == "2016-04-03")
    check("the Twins opened on April 4", y2016["club_openers"]["142"] == "2016-04-04")

    y2026 = by_year[2026]
    check("2026 league start is March 25", y2026["championship_start"] == "2026-03-25")
    check("2026's first game is a domestic game", y2026["first_domestic_game"] == "2026-03-25")
    check("the Yankees played on the first day", y2026["club_openers"]["147"] == "2026-03-25")
    check("the Giants played on the first day", y2026["club_openers"]["137"] == "2026-03-25")
    check("the Rockies opened on March 27", y2026["club_openers"]["115"] == "2026-03-27")

    y2013 = by_year[2013]
    check("2013 ends the day before the tiebreaker", y2013["championship_end"] == "2013-09-29")
    check(
        "2013 excludes the September 30 tiebreaker",
        [g["date"] for g in y2013["tiebreakers_excluded"]] == ["2013-09-30"],
    )

    y2018 = by_year[2018]
    check("2018 ends the day before the tiebreakers", y2018["championship_end"] == "2018-09-30")
    check(
        "2018 excludes both October 1 tiebreakers",
        sorted(g["date"] for g in y2018["tiebreakers_excluded"]) == ["2018-10-01", "2018-10-01"],
    )

    y2022 = by_year[2022]
    check("2022's delayed start is April 7", y2022["championship_start"] == "2022-04-07")
    check("2022 is marked special", y2022["special"] is True)
    check("2022's note names the lockout", "lockout" in y2022["notes"].lower())

    y2020 = by_year[2020]
    check("2020 runs July 23 through September 27", y2020["championship_start"] == "2020-07-23" and y2020["championship_end"] == "2020-09-27")
    check("2020 is 67 championship-season days", y2020["championship_days"] == 67)
    check("2020 is marked special and notes the 60-game season", y2020["special"] is True and "60" in y2020["notes"])

    check("1994 is the strike year", by_year[1994]["special"] is True and by_year[1994]["championship_end"] == "1994-08-11")
    check("1995 ends before its tiebreaker", by_year[1995]["championship_end"] == "1995-10-01" and by_year[1995]["special"] is True)
    check("2023 ends October 1", by_year[2023]["championship_end"] == "2023-10-01")
    check("2024 keeps the September 30 makeups", by_year[2024]["championship_end"] == "2024-09-30")


def test_exceptions_are_sourced_and_ordered():
    data = load(EXCEPTIONS_PATH)
    check("the exceptions file has a schema note", bool(data.get("_schema")))
    entries = data["exceptions"]
    check("there is at least one exception", bool(entries))
    players = players_by_id()
    seen_spans: dict[int, list[tuple[dt.date, dt.date]]] = {}

    for i, entry in enumerate(entries):
        name = entry.get("name")
        start = parse_iso(entry.get("start"))
        end = parse_iso(entry.get("end"))
        check(f"exception {i} ({name}) has a start and an end in order", start is not None and end is not None and end >= start)
        counts = entry.get("counts")
        kind = entry.get("kind")
        check(f"exception {i} ({name}) counts is true or false", isinstance(counts, bool))
        check(f"exception {i} ({name}) has a known kind", kind in KINDS)
        if isinstance(counts, bool) and kind in KINDS:
            expected = kind in COUNTS_TRUE
            check(f"exception {i} ({name}) kind agrees with counts", counts is expected)
        check(f"exception {i} ({name}) has a reason", isinstance(entry.get("reason"), str) and entry["reason"].strip())
        urls = urls_of(entry.get("source"))
        check(
            f"exception {i} ({name}) has an https source",
            bool(urls) and all(u.startswith("https://") for u in urls),
        )
        try:
            mlbam_id = int(entry["mlbam_id"])
        except (KeyError, TypeError, ValueError):
            check(f"exception {i} ({name}) has an integer player id", False)
            continue
        player = players.get(mlbam_id)
        check(f"exception {i} id {mlbam_id} is a stored player", player is not None)
        if player is not None:
            check(f"exception {i} name matches the stored player", player.get("name") == name)
        if start is not None and end is not None:
            for prev_start, prev_end in seen_spans.get(mlbam_id, []):
                overlaps = start <= prev_end and prev_start <= end
                check(f"exception {i} ({name}) does not overlap another spell", not overlaps)
            seen_spans.setdefault(mlbam_id, []).append((start, end))


def test_existing_exceptions_are_folded_in():
    """The calculator's list is a subset of this reference list."""
    engine = load(ENGINE_EXCEPTIONS_PATH)["exceptions"]
    reference = load(EXCEPTIONS_PATH)["exceptions"]
    ref_keys = {
        (int(e["mlbam_id"]), e["start"], e["end"], e["counts"])
        for e in reference
    }
    for entry in engine:
        key = (int(entry["mlbam_id"]), entry["start"], entry["end"], entry["counts"])
        check(f"folded in {entry['name']} {entry['start']} counts={entry['counts']}", key in ref_keys)

    by_id: dict[int, list[dict]] = {}
    for entry in reference:
        by_id.setdefault(int(entry["mlbam_id"]), []).append(entry)
    bauer = by_id[545333]
    check("Bauer has a paid leave and an unpaid suspension", sorted(e["counts"] for e in bauer) == [False, True])
    hicks = by_id[663855]
    check("Hicks's 2020 opt-out counts as service", len(hicks) == 1 and hicks[0]["counts"] is True and hicks[0]["kind"] == "covid_high_risk_opt_out")
    alvarado = [e for e in by_id[621237] if e["start"].startswith("2025-")]
    check("Alvarado's 2025 suspension does not count", len(alvarado) == 1 and alvarado[0]["counts"] is False)


def test_sources_are_not_baseball_reference():
    blob = (CALENDAR_PATH.read_text(encoding="utf-8") + EXCEPTIONS_PATH.read_text(encoding="utf-8")).lower()
    check("neither file mentions baseball-reference", "baseball-reference" not in blob and "baseball reference" not in blob)


def main():
    test_calendar_covers_every_stored_season()
    test_calendar_dates_and_day_counts()
    test_hand_checked_seasons()
    test_exceptions_are_sourced_and_ordered()
    test_existing_exceptions_are_folded_in()
    test_sources_are_not_baseball_reference()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
