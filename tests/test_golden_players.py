"""Golden service-time cases.

Ten players counted by hand from MLB Stats API transaction dates and the
championship-season calendar. The expected totals live in
tests/golden/players.json. The transaction rows those counts used live in
tests/golden/transactions/ and are what this test hands to the service-time
walk, so the check does not call the network and does not depend on the
transaction cache.

The check runs the rulebook in scripts/rulebook.py over the saved rows.
The published site can still sit above that count for a season, because
the site is built by the current service-time walk plus game dates this
test does not fetch.

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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cba  # noqa: E402
from rulebook import evaluate  # noqa: E402
from super_two import format_service, load_reported_cutoff, parse_service  # noqa: E402

GOLDEN = ROOT / "tests" / "golden" / "players.json"
TRANSACTIONS = ROOT / "tests" / "golden" / "transactions"
SITE = ROOT / "docs" / "data" / "service_time.json"
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


def _run_engine(player: dict, debut: dt.date) -> dict:
    rows = json.loads((TRANSACTIONS / f"{player['mlbam_id']}.json").read_text())
    result = evaluate(
        rows,
        player_id=player["mlbam_id"],
        name=player["name"],
        debut=debut,
        horizon=HORIZON,
    )
    return {
        "formatted": result["service_time"],
        "days": result["service_days"],
        "by_season": {season["year"]: season["credited_days"] for season in result["seasons"]},
        "option_days": {int(year): days for year, days in result["option_days"].items()},
        "option_years_used": result["option_years_used"],
        "option_years_saved": result["option_years_saved"],
        "sanity": result["sanity"],
    }


def _site_rows() -> dict[int, dict]:
    data = json.loads(SITE.read_text())
    return {row["id"]: row for row in data["players"]}


def main() -> int:
    global XFAIL
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
        engine = _run_engine(player, dt.date.fromisoformat(debut_text))
        check(f"{name} accounting holds", engine["sanity"] == [])
        check(f"{name} option years used {player['option_years_used']}", engine["option_years_used"] == player["option_years_used"])
        check(f"{name} option years saved {player['option_years_saved']}", engine["option_years_saved"] == player["option_years_saved"])
        expected_days = {int(year): total for year, total in player["option_days"].items()}
        got_days = {year: engine["option_days"].get(year, 0) for year in expected_days}
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
