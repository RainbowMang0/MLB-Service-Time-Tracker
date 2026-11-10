"""Golden service-time cases.

Ten players counted by hand from MLB Stats API transaction dates and the
championship-season calendar. The expected totals live in
tests/golden/players.json. The transaction rows those counts used live in
tests/golden/transactions/ and are what this test hands to the service-time
walk, so the check does not call the network and does not depend on the
transaction cache.

The walk is the rules. The published site can sit above the walk for a
season in which the player appeared in far more games than the walk
credited, because those game dates get added back when the site is built.
That step needs the game log, which this test does not fetch. Cole, Burnes,
and Ohtani are the three where the site and the walk therefore disagree
with each other as well as with the hand count.

A player whose engine result does not match the hand count is listed with
an ``xfail`` reason. That mismatch is reported and does not fail the run.
When a later rules change makes the engine match, the same row fails on
purpose: delete the xfail reason in that change so the case becomes a
normal check.

Corbin Burnes's 2019 season is an open case of one day. A one-day
difference on his total is reported and does not fail. Anything larger is
the xfail above.

    python3 tests/test_golden_players.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cba  # noqa: E402
import fetch_mlb_data as mlb  # noqa: E402
import service_time_exceptions  # noqa: E402
from il_stints import SeasonWindows  # noqa: E402
from mlb_clubs import MLB_CLUB_IDS  # noqa: E402
from options import count_option_years  # noqa: E402
from service_time import (  # noqa: E402
    Transaction,
    compute_service_time,
    is_active_stop,
    is_free_agency_election,
    roster_start_before_debut,
)
from super_two import format_service, load_reported_cutoff, parse_service  # noqa: E402
from update_service_time import (  # noqa: E402
    _involves_mlb_club,
    _is_mlb_assignment,
    _is_minor_assignment,
    _opening_day_clubs,
    _season_windows_for_player,
)

GOLDEN = ROOT / "tests" / "golden" / "players.json"
TRANSACTIONS = ROOT / "tests" / "golden" / "transactions"
SITE = ROOT / "docs" / "data" / "service_time.json"
WINDOWS_FILE = ROOT / "data" / "season_windows.json"
HORIZON = dt.date(2026, 11, 9)

PASS = 0
FAIL = 0
XFAIL = 0


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def _windows_by_year() -> dict[int, dict]:
    raw = json.loads(WINDOWS_FILE.read_text())["years"]
    out = {}
    for year, row in raw.items():
        out[int(year)] = {
            "end": dt.date.fromisoformat(row["end"]),
            "openers": {int(k): dt.date.fromisoformat(v) for k, v in row["clubs"].items()},
            "ends": {int(k): dt.date.fromisoformat(v) for k, v in row.get("ends", {}).items()},
        }
    return out


_CALENDAR = _windows_by_year()


def _uniform(year: int) -> dt.date:
    """The opener the current engine uses: the most common in-window date."""
    row = _CALENDAR[year]
    earliest = row["end"] - dt.timedelta(days=186)
    within = [d for d in row["openers"].values() if d >= earliest]
    return Counter(within).most_common(1)[0][0]


def _install_calendar() -> None:
    """Point the season-window lookups at the stored calendar. No network."""

    def season_window(year: int):
        row = _CALENDAR[year]
        return _uniform(year), row["end"]

    def club_start(year: int, club_id: int | None):
        row = _CALENDAR[year]
        if club_id in row["openers"]:
            return row["openers"][club_id]
        return _uniform(year)

    def club_end(year: int, club_id: int | None):
        row = _CALENDAR[year]
        if club_id in row["ends"]:
            return max(row["end"], row["ends"][club_id])
        return row["end"]

    mlb.get_season_window = season_window
    mlb.get_uniform_season_start = _uniform
    mlb.get_club_season_start = club_start
    mlb.get_club_season_end = club_end


def _transactions(pid: int) -> tuple[list[dict], list[Transaction]]:
    rows = json.loads((TRANSACTIONS / f"{pid}.json").read_text())
    wrapped = []
    for row in rows:
        desc = row.get("description") or ""
        minor = _is_minor_assignment(row, MLB_CLUB_IDS)
        assignment = _is_mlb_assignment(row, MLB_CLUB_IDS)
        if not (
            _involves_mlb_club(row, MLB_CLUB_IDS)
            or minor
            or assignment
            or is_active_stop(desc)
            or is_free_agency_election(desc)
        ):
            continue
        wrapped.append(
            Transaction(
                date=dt.date.fromisoformat(row["date"]),
                description=desc,
                team=row.get("team") if isinstance(row.get("team"), str) else None,
                minor_assignment=minor,
                mlb_assignment=assignment,
            )
        )
    return rows, wrapped


def _team_ids(rows: list[dict]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for row in rows:
        if not _involves_mlb_club(row, MLB_CLUB_IDS):
            continue
        for key in ("to_team_id", "team_id", "from_team_id"):
            team_id = row.get(key)
            if team_id in MLB_CLUB_IDS:
                year = int(row["date"][:4])
                bucket = out.setdefault(year, [])
                if team_id not in bucket:
                    bucket.append(team_id)
                break
    return out


def _run_engine(player: dict, debut: dt.date, exceptions, season_teams: dict[int, list[int]]) -> dict:
    rows, wrapped = _transactions(player["mlbam_id"])
    floor = roster_start_before_debut(wrapped, debut) or debut
    first_year = min(season["year"] for season in player["seasons"])
    first_year = min(first_year, debut.year)
    # Club by season comes from the published record. Without it a traded
    # player's window falls back to every club he ever touched, and an
    # overseas opener gets dropped.
    seasons = _season_windows_for_player(
        range(first_year, 2027),
        season_teams,
        _team_ids(rows),
        _opening_day_clubs(rows, MLB_CLUB_IDS),
    )
    active, inactive = service_time_exceptions.periods_for_player(
        exceptions, player["mlbam_id"]
    )
    rules = cba.default()
    result = compute_service_time(
        wrapped,
        seasons,
        presume_active_from_debut=True,
        horizon_end=HORIZON,
        accrual_floor=floor,
        dfa_limbo_days=rules.require("designated_for_assignment.max_service_days"),
        force_active_periods=active,
        force_inactive_periods=inactive,
    )
    return {
        "formatted": result.formatted,
        "days": result.total_days,
        "by_season": {
            year: detail["credited_days"] for year, detail in result.by_season.items()
        },
        "arbitration": result.is_arbitration_eligible,
        "free_agency": result.is_free_agent_eligible,
    }


def _site_rows() -> dict[int, dict]:
    data = json.loads(SITE.read_text())
    return {row["id"]: row for row in data["players"]}


def _season_teams(row: dict) -> dict[int, list[int]]:
    teams = {}
    for season in row.get("seasons") or []:
        ids = [team_id for team_id in season.get("t") or [] if team_id in MLB_CLUB_IDS]
        if ids:
            teams[season["y"]] = ids
    return teams


def main() -> int:
    global XFAIL
    _install_calendar()
    fixture = json.loads(GOLDEN.read_text())
    rules = cba.default()
    full_year = rules.require("service_time.days_per_credited_year")
    prior_minimum = rules.require("arbitration.super_two.minimum_prior_season_days")
    reported = load_reported_cutoff()
    cutoff_days = parse_service(reported["cutoff"]) if reported else None
    check("the 2026 Super Two cutoff is on file", cutoff_days is not None)
    if cutoff_days is None:
        print(f"\n{PASS} passed, {FAIL} failed, {XFAIL} expected mismatches")
        return 1

    exceptions = service_time_exceptions.load_exceptions()
    option_windows = SeasonWindows.load()
    site_rows = _site_rows()
    report = []

    print("Fixture checks")
    for player in fixture["players"]:
        prior = next(
            (season["credited_days"] for season in player["seasons"] if season["year"] == 2026),
            0,
        )
        # Recompute the Super Two test with the ruleset's prior-season minimum
        # rather than a literal, then compare to the flag stored on the row.
        days = player["service_days"]
        years = days / full_year
        super_two = (
            2 <= years < 3 and days >= cutoff_days and prior >= prior_minimum
        )
        check(
            f"{player['name']} seasons add up",
            sum(season["credited_days"] for season in player["seasons"]) == days,
        )
        check(
            f"{player['name']} {player['service_time']} is {days} days",
            format_service(days) == player["service_time"],
        )
        check(
            f"{player['name']} Super Two is {player['super_two_2026']}",
            player["super_two_2026"] is super_two,
        )
        check(
            f"{player['name']} arbitration is {player['arbitration']}",
            player["arbitration"] is (years >= 3 or player["super_two_2026"]),
        )
        check(
            f"{player['name']} free agency is {player['free_agency']}",
            player["free_agency"] is (years >= 6),
        )
        crossed = []
        if years >= 3:
            crossed.append("3 years")
        if years >= 6:
            crossed.append("6 years")
        if player["super_two_2026"]:
            crossed.append("Super Two")
        check(f"{player['name']} crossed lines are {crossed or 'none'}", player["crossed"] == crossed)
        option_years = set(player["option_years_used"]) | set(player["option_years_saved"])
        check(
            f"{player['name']} option seasons are the used and saved years",
            set(int(year) for year in player["option_days"]) == option_years,
        )

    print("\nEngine")
    for player in fixture["players"]:
        name = player["name"]
        pid = player["mlbam_id"]
        site_row = site_rows.get(pid) or {}
        debut_text = site_row.get("mlb_debut")
        check(f"{name} has a debut date on the site", bool(debut_text))
        if not debut_text:
            continue
        engine = _run_engine(
            player, dt.date.fromisoformat(debut_text), exceptions, _season_teams(site_row)
        )
        rows, _wrapped = _transactions(pid)
        option_days, _inferred = count_option_years(rows, option_windows, HORIZON)
        used = [year for year, total in sorted(option_days.items()) if total >= rules.require_verified("options.days_to_burn_option_year")]
        saved = [
            year
            for year, total in sorted(option_days.items())
            if 0 < total < rules.require_verified("options.days_to_burn_option_year")
        ]
        check(f"{name} option years used {player['option_years_used']}", used == player["option_years_used"])
        check(f"{name} option years saved {player['option_years_saved']}", saved == player["option_years_saved"])
        expected_days = {int(year): total for year, total in player["option_days"].items()}
        got_days = {year: option_days.get(year, 0) for year in expected_days}
        check(f"{name} option day totals", got_days == expected_days)

        expected_seasons = {season["year"]: season["credited_days"] for season in player["seasons"]}
        diffs = []
        for year, expected in sorted(expected_seasons.items()):
            got = engine["by_season"].get(year, 0)
            if got != expected:
                diffs.append(f"{year}: engine {got}, expected {expected}")
        for year, got in sorted(engine["by_season"].items()):
            if year not in expected_seasons and got:
                diffs.append(f"{year}: engine {got}, expected 0")

        site_total = site_row.get("service_time") or "(not on the site)"
        open_case = player.get("open_case")
        day_gap = engine["days"] - player["service_days"]
        if not diffs:
            if player.get("xfail"):
                check(
                    f"{name} still mismatches, so the xfail is still needed",
                    False,
                )
                print(f"         engine now matches {player['service_time']}; remove the xfail reason")
            else:
                check(f"{name} {player['service_time']}", True)
            status = "match"
            why = "The engine matches the hand count."
        elif open_case and abs(day_gap) <= 1 and not player.get("xfail"):
            check(f"{name} is within the unresolved day", True)
            print(f"         open case: {open_case}")
            status = "open"
            why = open_case
        elif player.get("xfail"):
            XFAIL += 1
            print(f"  xfail - {name} engine {engine['formatted']}, expected {player['service_time']}")
            print(f"         {player['xfail']}")
            for line in diffs:
                print(f"         {line}")
            status = "xfail"
            why = player["xfail"]
        else:
            check(f"{name} {player['service_time']}", False)
            print(f"         engine {engine['formatted']}")
            for line in diffs:
                print(f"         {line}")
            status = "fail"
            why = "; ".join(diffs)

        report.append((name, player["service_time"], site_total, engine["formatted"], status, why))

    print("\nComparison")
    print(f"{'Player':<22} {'Expected':<10} {'Site':<10} {'Engine':<10} Status")
    for name, expected, site_total, engine_total, status, _why in report:
        print(f"{name:<22} {expected:<10} {site_total:<10} {engine_total:<10} {status}")

    print(f"\n{PASS} passed, {FAIL} failed, {XFAIL} expected mismatches")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
