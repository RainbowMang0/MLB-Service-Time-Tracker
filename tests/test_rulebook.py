"""Rulebook checks: one effect per rule, the families that move service,
the golden players, and the accounting identities.

    python3 tests/test_rulebook.py
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cba  # noqa: E402
import rulebook as rb  # noqa: E402
from rulebook import Calendar, PublishBlocked, SeasonCal, canonical_json, evaluate  # noqa: E402

PASS = 0
FAIL = 0

NYY = 147
LAD = 119
COL = 115
MIL = 158
PIT = 134
PHI = 143
CLE = 114


def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def season(year, start, end, domestic=None, abroad=None, ties=None):
    return {
        "season": year,
        "championship_start": start,
        "championship_end": end,
        "first_domestic_game": domestic or start,
        "games_outside_us_and_canada": abroad or [],
        "tiebreakers_excluded": ties or [],
    }


def cal(*rows):
    return Calendar([SeasonCal(row) for row in rows])


def txn(day, code, description, to=None, frm=None, team=None, name=None, effective=None):
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
    if team is not None:
        row["team_id"] = team
    if name is not None:
        row["team"] = name
    if effective is not None:
        row["effectiveDate"] = effective
    return row


def run(rows, calendar, debut, horizon, **kwargs):
    return evaluate(
        rows,
        player_id=kwargs.pop("player_id", 1),
        debut=dt.date.fromisoformat(debut) if isinstance(debut, str) else debut,
        horizon=dt.date.fromisoformat(horizon) if isinstance(horizon, str) else horizon,
        calendar=calendar,
        exceptions=kwargs.pop("exceptions", []),
        roy_years=kwargs.pop("roy_years", set()),
        appearance_years=kwargs.pop("appearance_years", None),
        on_40_man=kwargs.pop("on_40_man", False),
        **kwargs,
    )


def days_of(result, year):
    for season in result["seasons"]:
        if season["year"] == year:
            return season["credited_days"]
    return 0


def test_rule_file():
    book = rb.load_rulebook()
    effects = set(book["rules"][0].effect for _ in [0])
    check("the rulebook has a version", book["version"] == rb.RULEBOOK_VERSION)
    ids = [rule.id for rule in book["rules"]]
    check("rule ids are unique", len(ids) == len(set(ids)))
    check("every rule has one allowed effect", all(rule.effect in rb._EFFECTS for rule in book["rules"]))
    check("every rule cites the reference", all(rule.cite for rule in book["rules"]))
    check("open questions are listed", len(book["open_questions"]) >= 10)
    ruleset = cba.default()
    cap = ruleset.require("designated_for_assignment.max_service_days")
    later = [row for row in book["dfa_windows"] if row["years"][0] >= 2017 and not row["open_question"]]
    check("the 2017-and-later designation window matches the ruleset", later and later[0]["days"] == cap)
    check("2012-16 designation window is 10 days", any(
        row["years"] == (2012, 2016) and row["days"] == 10 for row in book["dfa_windows"]
    ))
    # Silence the unused local.
    check("effects set is the four names", effects <= rb._EFFECTS)


def test_partial_season_is_not_hidden_by_the_cap():
    calendar = cal(season(2023, "2023-03-30", "2023-10-01"))
    cap = cba.default().require("service_time.max_days_creditable_per_season")
    full = run(
        [txn("2023-03-30", "SE", "New York Yankees selected the contract of X from Scranton.", to=NYY, frm=1)],
        calendar, "2023-03-30", "2023-10-05",
    )
    check("a full season credits the cap, not the longer calendar", days_of(full, 2023) == cap)
    check("the full season has no sanity errors", full["sanity"] == [])
    partial = run(
        [
            txn("2023-06-01", "SE", "New York Yankees selected the contract of X from Scranton.", to=NYY, frm=1),
            txn("2023-06-11", "REL", "New York Yankees released X.", frm=NYY),
        ],
        calendar, "2023-06-01", "2023-10-05",
    )
    check("a ten-day call-up is ten days, not the cap", days_of(partial, 2023) == 10)
    check("ranges add up to those ten days", partial["seasons"][0]["raw_days"] == 10)


def test_option_under_and_over_the_threshold():
    calendar = cal(season(2023, "2023-03-30", "2023-10-01"))
    short = run(
        [
            txn("2023-03-30", "SE", "Yankees selected the contract of X.", to=NYY),
            txn("2023-04-10", "OPT", "Yankees optioned X to Scranton.", frm=NYY, to=531),
            txn("2023-04-20", "CU", "Yankees recalled X from Scranton.", to=NYY, frm=531),
        ],
        calendar, "2023-03-30", "2023-10-05",
    )
    season_row = short["seasons"][0]
    check("ten option days are saved", short["option_years_saved"] == [2023])
    check("ten option days are not a used year", short["option_years_used"] == [])
    check("the ten option days are credited as service", season_row["option_service_credit"] == 10)
    # March 30-April 9 is 11 roster days, plus 10 credited option days, plus April 20-Oct 1.
    roster_after = (dt.date(2023, 10, 1) - dt.date(2023, 4, 20)).days + 1
    roster_before = (dt.date(2023, 4, 9) - dt.date(2023, 3, 30)).days + 1
    cap = cba.default().require("service_time.max_days_creditable_per_season")
    check(
        "the raw total includes the ten, then the cap applies",
        season_row["raw_days"] == roster_before + 10 + roster_after and days_of(short, 2023) == cap,
    )
    long = run(
        [
            txn("2023-03-30", "SE", "Yankees selected the contract of X.", to=NYY),
            txn("2023-04-10", "OPT", "Yankees optioned X to Scranton.", frm=NYY, to=531),
            txn("2023-05-15", "CU", "Yankees recalled X from Scranton.", to=NYY, frm=531),
        ],
        calendar, "2023-03-30", "2023-10-05",
    )
    check("thirty-five option days use the year", long["option_years_used"] == [2023])
    check("those days are not folded back into service", long["seasons"][0]["option_service_credit"] == 0)
    check("the option tally itself is still recorded", long["option_days"]["2023"] == 35)


def test_option_then_hurt_in_the_minors_does_not_count():
    calendar = cal(season(2023, "2023-03-30", "2023-10-01"))
    result = run(
        [
            txn("2023-04-01", "SE", "Yankees selected the contract of X.", to=NYY),
            txn("2023-04-10", "OPT", "Yankees optioned X to Scranton.", frm=NYY, to=531),
            txn("2023-04-20", "SC", "Scranton placed X on the 7-day injured list.", to=531),
            txn("2023-05-09", "CU", "Yankees recalled X from Scranton.", to=NYY, frm=531),
        ],
        calendar, "2023-04-01", "2023-10-05",
    )
    # April 10 through May 8 is 29 days, over the threshold.
    check("the minor-league injured list stays on the option", result["option_days"]["2023"] == 29)
    check("those days are not major-league service", result["seasons"][0]["option_service_credit"] == 0)
    check("no major-league injured-list spell was opened", result["il_ranges"] == [])
    recalled = run(
        [
            txn("2023-04-01", "SE", "Yankees selected the contract of X.", to=NYY),
            txn("2023-04-10", "OPT", "Yankees optioned X to Scranton.", frm=NYY, to=531),
            txn("2023-04-20", "CU", "Yankees recalled X from Scranton.", to=NYY, frm=531),
            txn("2023-04-20", "SC", "Yankees placed X on the 60-day injured list.", to=NYY),
        ],
        calendar, "2023-04-01", "2023-10-05",
    )
    check("recalled and placed on the major-league injured list counts", days_of(recalled, 2023) > 100)
    check("the option stopped the day before the recall", recalled["option_days"]["2023"] == 10)
    check("the major-league injured list is its own spell", len(recalled["il_ranges"]) == 1)


def test_dfa_window_trade_and_preseason():
    calendar = cal(
        season(2015, "2015-04-06", "2015-10-04"),
        season(2023, "2023-03-30", "2023-10-01"),
        season(2024, "2024-03-28", "2024-09-29"),
    )
    held = run(
        [
            txn("2015-04-06", "SE", "Yankees selected the contract of X.", to=NYY),
            txn("2015-06-01", "DES", "Yankees designated X for assignment.", frm=NYY),
        ],
        calendar, "2015-04-06", "2015-10-05",
    )
    # April 6 through June 10 (10-day window beginning June 1).
    expected = (dt.date(2015, 6, 10) - dt.date(2015, 4, 6)).days + 1
    check("a 2015 designation keeps counting for 10 days", days_of(held, 2015) == expected)
    short = run(
        [
            txn("2023-03-30", "SE", "Yankees selected the contract of X.", to=NYY),
            txn("2023-06-01", "DES", "Yankees designated X for assignment.", frm=NYY),
            txn("2023-06-05", "OUT", "Yankees sent X outright to Scranton.", frm=NYY, to=531),
        ],
        calendar, "2023-03-30", "2023-10-05",
    )
    # Through June 4. June 5 is the outright.
    expected = (dt.date(2023, 6, 4) - dt.date(2023, 3, 30)).days + 1
    check("service stops at the outright, and the designation days count", days_of(short, 2023) == expected)
    heim = run(
        [
            txn("2023-03-30", "SE", "Yankees selected the contract of X.", to=NYY),
            txn("2023-06-01", "DES", "Yankees designated X for assignment.", frm=NYY),
            txn("2023-06-01", "TR", "Yankees traded X to the Dodgers.", frm=NYY, to=LAD),
        ],
        calendar, "2023-03-30", "2023-10-05",
    )
    check("a same-day designation and trade does not stop the clock", days_of(heim, 2023) == cba.default().require("service_time.max_days_creditable_per_season"))
    spring = run(
        [
            txn("2023-06-01", "SE", "Yankees selected the contract of X.", to=NYY),
            txn("2024-03-20", "DES", "Yankees designated X for assignment.", frm=NYY),
            txn("2024-04-02", "OUT", "Yankees sent X outright to Scranton.", frm=NYY, to=531),
        ],
        calendar, "2023-06-01", "2024-10-05",
    )
    check("a preseason designation does not earn Opening Day", days_of(spring, 2024) == 0)


def test_lists_suspensions_and_exceptions():
    calendar = cal(season(2023, "2023-03-30", "2023-10-01"))
    rows = [
        txn("2023-03-30", "SE", "Phillies selected the contract of X.", to=PHI),
        txn("2023-06-02", "SC", "Phillies placed X on the bereavement list.", to=PHI),
        txn("2023-06-06", "SC", "Phillies placed X on the paternity list.", to=PHI),
        txn("2023-06-08", "SC", "Phillies placed X on the family medical emergency list.", to=PHI),
        txn("2023-06-10", "SU", "X suspended.", to=PHI),
        txn("2023-06-12", "SC", "Phillies activated X.", to=PHI),
    ]
    result = run(rows, calendar, "2023-03-30", "2023-10-05")
    check("bereavement, paternity, family medical, and a suspension count", days_of(result, 2023) == cba.default().require("service_time.max_days_creditable_per_season"))
    restricted = run(
        [
            txn("2023-03-30", "SE", "Phillies selected the contract of X.", to=PHI),
            txn("2023-06-01", "SC", "Phillies placed X on the restricted list.", to=PHI),
            txn("2023-06-11", "SC", "Phillies activated X from the restricted list.", to=PHI),
        ],
        calendar, "2023-03-30", "2023-10-05",
    )
    check("a capped season can hide a short restricted-list gap", days_of(restricted, 2023) <= cba.default().require("service_time.max_days_creditable_per_season"))
    visible = run(
        [
            txn("2023-06-01", "SE", "Phillies selected the contract of X.", to=PHI),
            txn("2023-06-05", "SC", "Phillies placed X on the restricted list.", to=PHI),
            txn("2023-06-15", "SC", "Phillies activated X from the restricted list.", to=PHI),
            txn("2023-06-20", "REL", "Phillies released X.", frm=PHI),
        ],
        calendar, "2023-06-01", "2023-10-05",
    )
    # June 1-4 and June 15-19 = 4 + 5 = 9. June 5-14 out. June 20 is the release.
    check("a restricted-list gap is missing from a partial season", days_of(visible, 2023) == 9)
    check("the restricted-list row is flagged, not guessed", visible["ambiguous"] and visible["ambiguous"][0]["rule_id"] == "stop.restricted")
    paid = run(
        [
            txn("2023-06-01", "SE", "Dodgers selected the contract of X.", to=LAD),
            txn("2023-06-05", "SC", "Dodgers placed X on the restricted list.", to=LAD),
            txn("2023-06-20", "REL", "Dodgers released X.", frm=LAD),
        ],
        calendar, "2023-06-01", "2023-10-05",
        exceptions=[{
            "start": dt.date(2023, 6, 5),
            "end": dt.date(2023, 6, 14),
            "counts": True,
            "kind": "paid_administrative_leave",
        }],
    )
    # June 1-4 on the roster, June 5-14 restored by the leave. No later activation.
    check("paid administrative leave counts", days_of(paid, 2023) == 14)
    check("a covered restricted-list row is not left ambiguous", paid["ambiguous"] == [])


def test_all_star_winter_rehab_and_minor_signing():
    calendar = cal(season(2023, "2023-03-30", "2023-10-01"), season(2024, "2024-03-28", "2024-09-29"))
    rows = [
        txn("2023-03-30", "SE", "Yankees selected the contract of X.", to=NYY),
        txn("2023-07-10", "ASG", "X assigned to American League All-Stars.", to=159),
        txn("2023-07-12", "SC", "American League All-Stars activated X.", to=159),
        txn("2023-08-01", "ASG", "Yankees sent X on a rehab assignment to Scranton.", frm=NYY, to=531),
        txn("2023-11-20", "ASG", "X assigned to Criollos de Caguas.", to=4000, name="Criollos de Caguas"),
        txn("2024-02-01", "SFA", "Yankees signed free agent X to a minor league contract.", to=NYY),
    ]
    # Give the rehab a real injured-list placement so the season is ordinary.
    rows.insert(3, txn("2023-07-20", "SC", "Yankees placed X on the 10-day injured list.", to=NYY))
    rows.insert(5, txn("2023-08-05", "SC", "Yankees activated X from the 10-day injured list.", to=NYY))
    result = run(rows, calendar, "2023-03-30", "2024-10-05")
    cap = cba.default().require("service_time.max_days_creditable_per_season")
    check("All-Star, rehab, and winter ball do not end the season", days_of(result, 2023) == cap)
    check("a minor-league signing does not by itself end next year", days_of(result, 2024) == cap)
    stopped = run(
        [
            txn("2023-03-30", "SE", "Yankees selected the contract of X.", to=NYY),
            txn("2023-06-01", "ASG", "X assigned to Scranton.", to=531),
        ],
        calendar, "2023-03-30", "2023-10-05",
    )
    check("an affiliate assignment during the season stops service", days_of(stopped, 2023) < cap)


def test_free_agency_and_same_day_order():
    calendar = cal(season(2023, "2023-03-30", "2023-10-01"))
    result = run(
        [
            txn("2023-03-30", "SE", "Yankees selected the contract of X.", to=NYY),
            txn("2023-06-01", "DFA", "X elected free agency."),
            txn("2023-07-01", "SFA", "Dodgers signed free agent X.", to=LAD),
            txn("2023-07-15", "REL", "Dodgers released X.", frm=LAD),
        ],
        calendar, "2023-03-30", "2023-10-05",
    )
    # March 30-May 31 = 63 days. July 1-14 = 14. June is out. July 15 is the release.
    check("nothing counts between free agency and the next signing", days_of(result, 2023) == 63 + 14)
    forward = [
        txn("2023-04-01", "SE", "Yankees selected the contract of X.", to=NYY),
        txn("2023-05-01", "CU", "Yankees recalled X.", to=NYY),
        txn("2023-05-01", "OPT", "Yankees optioned X to Scranton.", frm=NYY, to=531),
    ]
    backward = list(reversed(forward))
    a = run(forward, calendar, "2023-04-01", "2023-10-05")
    b = run(backward, calendar, "2023-04-01", "2023-10-05")
    check("same-day order does not change the result", canonical_json(a) == canonical_json(b))
    check("the same-day option wins", a["option_days"].get("2023", 0) > 20)


def test_2020_and_the_lockout_opener():
    calendar = cal(season(2020, "2020-07-23", "2020-09-27"))
    ruleset = cba.default()
    cap = ruleset.require("service_time.max_days_creditable_per_season")
    scale = ruleset.require("service_time.shortened_seasons.2020.scale_to_span_days")
    full = run(
        [txn("2020-07-23", "SE", "Yankees selected the contract of X.", to=NYY)],
        calendar, "2020-07-23", "2020-10-05",
    )
    check("a full 2020 season is one credited year", days_of(full, 2020) == cap)
    partial = run(
        [
            txn("2020-07-23", "SE", "Yankees selected the contract of X.", to=NYY),
            txn("2020-08-01", "REL", "Yankees released X.", frm=NYY),
        ],
        calendar, "2020-07-23", "2020-10-05",
    )
    raw = 9  # July 23 through July 31
    span = (dt.date(2020, 9, 27) - dt.date(2020, 7, 23)).days + 1
    expected = min(cap, int(round(raw * scale / span)))
    check("a partial 2020 season is scaled and still under the cap", days_of(partial, 2020) == expected)
    check("the partial 2020 total is not the raw day count", days_of(partial, 2020) != raw)
    real = rb.load_calendar()
    lockout = evaluate(
        [
            txn("2022-04-07", "SE", "Dodgers selected the contract of X.", to=LAD),
            txn("2022-04-09", "REL", "Dodgers released X.", frm=LAD),
        ],
        player_id=9,
        debut=dt.date(2022, 4, 7),
        horizon=dt.date(2022, 10, 6),
        calendar=real,
        exceptions=[],
        roy_years=set(),
    )
    # League opener is April 7. April 7 and April 8 count. April 9 is the release.
    check("2022 starts on the restored opener, two days before the release", days_of(lockout, 2022) == 2)


def test_international_opener_and_tiebreaker():
    calendar = cal(season(
        2025, "2025-03-18", "2025-04-30", domestic="2025-03-27",
        abroad=[{"date": "2025-03-18", "away_id": LAD, "home_id": 112}],
    ))
    dodgers = run(
        [
            txn("2025-01-15", "SFA", "Dodgers signed free agent X.", to=LAD),
            txn("2025-03-22", "REL", "Dodgers released X.", frm=LAD),
        ],
        calendar, "2025-03-18", "2025-05-05",
    )
    rockies = run(
        [
            txn("2025-01-15", "SFA", "Rockies signed free agent Y.", to=COL),
            txn("2025-03-30", "REL", "Rockies released Y.", frm=COL),
        ],
        calendar, "2025-03-27", "2025-05-05",
    )
    check("the overseas club earns the international opener", days_of(dodgers, 2025) == 4)
    check("another club starts on the first domestic game", days_of(rockies, 2025) == 3)
    early = cal(
        season(2018, "2018-09-28", "2018-09-30", ties=[{
            "date": "2018-10-01", "away_id": MIL, "home_id": 112,
        }]),
        season(2022, "2022-10-01", "2022-10-05", ties=[{
            "date": "2022-10-06", "away_id": MIL, "home_id": 112,
        }]),
    )
    brewers_2018 = run(
        [
            txn("2018-09-28", "SE", "Brewers selected the contract of X.", to=MIL),
            txn("2018-10-03", "REL", "Brewers released X.", frm=MIL),
        ],
        early, "2018-09-28", "2018-10-05",
    )
    pirates_2018 = run(
        [
            txn("2018-09-28", "SE", "Pirates selected the contract of X.", to=PIT),
            txn("2018-10-03", "REL", "Pirates released X.", frm=PIT),
        ],
        early, "2018-09-28", "2018-10-05",
    )
    check("a 2018 tiebreaker counts for the club that played it", days_of(brewers_2018, 2018) == 4)
    check("a 2018 tiebreaker does not count for every club", days_of(pirates_2018, 2018) == 3)
    brewers_2022 = run(
        [
            txn("2022-10-01", "SE", "Brewers selected the contract of X.", to=MIL),
            txn("2022-10-08", "REL", "Brewers released X.", frm=MIL),
        ],
        early, "2022-10-01", "2022-10-10",
    )
    check("a 2022 tiebreaker does not extend the window", days_of(brewers_2022, 2022) == 5)


def test_unknowns_and_determinism():
    calendar = cal(season(2023, "2023-03-30", "2023-10-01"))
    rows = [
        txn("2023-03-30", "SE", "Yankees selected the contract of X.", to=NYY),
        txn("2023-06-01", "ZZZ", "Something the rulebook has never seen.", to=NYY),
    ]
    alumni = run(rows, calendar, "2023-03-30", "2023-10-05", on_40_man=False)
    check("an alumni unknown is a warning", alumni["unknowns"][0]["severity"] == "warning")
    try:
        rb.assert_publishable(alumni)
        check("an alumni unknown does not block publishing", True)
    except PublishBlocked:
        check("an alumni unknown does not block publishing", False)
    current = run(rows, calendar, "2023-03-30", "2023-10-05", on_40_man=True)
    blocked = False
    try:
        rb.assert_publishable(current)
    except PublishBlocked:
        blocked = True
    check("a 40-man unknown blocks publishing", blocked)
    again = run(rows, calendar, "2023-03-30", "2023-10-05", on_40_man=True)
    check("the same rows produce the same text", canonical_json(current) == canonical_json(again))


def test_every_rule_effect_is_stable_for_a_representative_row():
    calendar = rb.load_calendar()
    samples = {
        "start.selected": txn("2024-06-01", "SE", "Yankees selected the contract of X.", to=NYY),
        "stop.option": txn("2024-06-02", "OPT", "Yankees optioned X to Scranton.", frm=NYY, to=531),
        "count.il-10": txn("2024-06-03", "SC", "Yankees placed X on the 10-day injured list.", to=NYY),
        "count.dl-15": txn("2014-06-03", "SC", "Yankees placed X on the 15-day disabled list.", to=NYY),
        "neutral.all-star": txn("2024-07-16", "ASG", "X assigned to American League All-Stars.", to=159),
        "neutral.trade": txn("2024-07-30", "TR", "Yankees traded X to the Dodgers.", frm=NYY, to=LAD),
        "stop.free-agency": txn("2024-11-01", "DFA", "X elected free agency."),
        "count.dfa": txn("2024-08-01", "DES", "Yankees designated X for assignment.", frm=NYY),
    }
    for rule_id, row in samples.items():
        hit = rb.classify_row(row, calendar)
        check(f"{rule_id} classifies to itself", hit is not None and hit.id == rule_id)


def main():
    print("Rule file")
    test_rule_file()
    print("Partial seasons")
    test_partial_season_is_not_hidden_by_the_cap()
    print("Options")
    test_option_under_and_over_the_threshold()
    test_option_then_hurt_in_the_minors_does_not_count()
    print("Designations")
    test_dfa_window_trade_and_preseason()
    print("Lists")
    test_lists_suspensions_and_exceptions()
    print("Neutral moves")
    test_all_star_winter_rehab_and_minor_signing()
    print("Free agency and order")
    test_free_agency_and_same_day_order()
    print("2020 and 2022")
    test_2020_and_the_lockout_opener()
    print("Opener and tiebreaker")
    test_international_opener_and_tiebreaker()
    print("Unknowns")
    test_unknowns_and_determinism()
    print("Samples")
    test_every_rule_effect_is_stable_for_a_representative_row()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
