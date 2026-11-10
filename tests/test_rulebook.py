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


def _karinchak_2021() -> int:
    """Recall on the 2020 opener must not plant an option that erases 2021."""
    real = rb.load_calendar()
    result = evaluate(
        [
            txn("2020-03-26", "OPT", "Indians optioned X to Columbus.", frm=114, to=445),
            txn("2020-07-23", "CU", "Indians recalled X from Columbus.", to=114),
            txn("2021-08-28", "OPT", "Indians optioned X to Columbus.", frm=114, to=445),
            txn("2021-10-01", "CU", "Indians recalled X from Columbus.", to=114),
        ],
        player_id=675916,
        debut=dt.date(2020, 7, 23),
        horizon=dt.date(2021, 10, 5),
        calendar=real,
        exceptions=[],
        roy_years=set(),
        appearance_years={2020, 2021},
    )
    return days_of(result, 2021)


def _roster_status_does_not_hide() -> bool:
    calendar = cal(season(2024, "2024-03-28", "2024-09-29"))
    result = run(
        [
            txn("2024-03-28", "SE", "Padres selected the contract of X.", to=135),
            txn("2024-06-01", "SC", "Padres roster status changed for X.", to=135),
            txn("2024-06-15", "OPT", "Padres optioned X to El Paso.", frm=135, to=4904),
        ],
        calendar, "2024-03-28", "2024-10-05",
    )
    return days_of(result, 2024) < cba.default().require("service_time.max_days_creditable_per_season")


def _june_option_keeps_opening_day() -> bool:
    """A June option means he was already up. The August recall does not erase Opening Day."""
    calendar = cal(season(2025, "2025-03-27", "2025-09-28"))
    result = run(
        [
            txn("2024-05-31", "OPT", "Athletics optioned X to Las Vegas.", frm=133, to=400),
            txn("2025-06-30", "OPT", "Athletics optioned X to Las Vegas.", frm=133, to=400),
            txn("2025-08-06", "CU", "Athletics recalled X from Las Vegas.", to=133),
        ],
        calendar, "2025-03-27", "2025-10-05",
        appearance_years={2025},
    )
    # March 27 through June 29, plus August 6 through September 28.
    before = (dt.date(2025, 6, 29) - dt.date(2025, 3, 27)).days + 1
    after = (dt.date(2025, 9, 28) - dt.date(2025, 8, 6)).days + 1
    return days_of(result, 2025) == before + after


def _first_game_after_option() -> int:
    calendar = cal(season(2024, "2024-03-28", "2024-09-30"))
    games = [dt.date(2024, 6, 18), dt.date(2024, 9, 30)]
    result = run(
        [
            txn("2024-03-28", "SE", "Athletics selected the contract of X.", to=133),
            txn("2024-05-14", "OPT", "Athletics optioned X to Las Vegas.", frm=133, to=400),
        ],
        calendar, "2024-03-28", "2024-10-05",
        appearance_dates=games,
    )
    return days_of(result, 2024)


def _markakis_raw() -> int:
    real = rb.load_calendar()
    result = evaluate(
        [
            txn("2020-07-06", "SC", "Braves placed X on the restricted list.", to=144),
            txn("2020-08-05", "SC", "Braves activated X from the restricted list.", to=144),
        ],
        player_id=455976,
        debut=dt.date(2006, 4, 3),
        horizon=dt.date(2020, 10, 5),
        calendar=real,
        roy_years=set(),
    )
    for season in result["seasons"]:
        if season["year"] == 2020:
            return season["raw_days"]
    return 0


def test_fix_round_cases():
    """Winter clubs by id, a postseason demotion, a major-league re-signing,
    the strike settlement, and the rows the cross-check classified."""
    cap = cba.default().require("service_time.max_days_creditable_per_season")
    calendar = cal(
        season(2024, "2024-03-28", "2024-09-29"),
        season(2025, "2025-03-27", "2025-09-28"),
    )
    winter = run(
        [
            txn("2024-03-28", "SE", "Diamondbacks selected the contract of X.", to=109),
            txn("2024-12-07", "ASG", "X assigned to Aguilas Cibaenas.", to=667, name="Aguilas Cibaenas"),
            txn("2025-01-10", "ASG", "X assigned to Estrellas Orientales.", to=669, name="Estrellas Orientales"),
        ],
        calendar, "2024-03-28", "2025-10-05",
    )
    check("Aguilas Cibaenas does not stop the next season", days_of(winter, 2025) == cap)
    check("Estrellas Orientales is a winter club", any(
        hit is not None and hit.id == "neutral.winter-league"
        for hit in [rb.classify_row(txn("2025-01-10", "ASG", "X assigned to Estrellas Orientales.", to=669, name="Estrellas Orientales"))]
    ))
    spelled = rb.classify_row(txn("2024-12-01", "ASG", "X assigned to Aguilas Cibaenas.", name="Aguilas Cibaenas"))
    check("the winter rule does not need the letters de or del", spelled is not None and spelled.id == "neutral.winter-league")

    reassigned = run(
        [
            txn("2024-03-28", "SE", "Astros selected the contract of X.", to=117),
            txn("2024-09-29", "OPT", "Astros optioned X to Sugar Land.", frm=117, to=5434),
            txn("2024-10-10", "CU", "Astros recalled X from Sugar Land.", to=117),
            txn("2024-10-11", "SC", "Astros reassigned X to the minor leagues.", to=117),
        ],
        calendar, "2024-03-28", "2025-10-05",
    )
    check("a postseason reassignment does not stop the next season", days_of(reassigned, 2025) == cap)

    missing_recall = run(
        [
            txn("2024-03-28", "SE", "Athletics selected the contract of X.", to=133),
            txn("2024-05-14", "OPT", "Athletics optioned X to Las Vegas.", frm=133, to=400),
        ],
        calendar, "2024-03-28", "2025-10-05",
        appearance_years={2024, 2025},
    )
    check("an option with no recall does not wipe a later season he played", days_of(missing_recall, 2025) == cap)
    check("the option still stops the season it happened in", days_of(missing_recall, 2024) < cap)
    check("the restarted season names the appearance rule", "accrual.appearance-restart" in (missing_recall["seasons"][-1]["rules"]))

    signed = run(
        [
            txn("2024-03-28", "SE", "Reds selected the contract of X.", to=113),
            txn("2024-11-01", "DFA", "X elected free agency."),
            txn("2024-11-19", "SGN", "Cincinnati Reds signed RHP X.", to=113),
            txn("2025-11-02", "DFA", "X elected free agency."),
        ],
        calendar, "2024-03-28", "2025-11-05",
    )
    check("a major-league signing after free agency restarts service", days_of(signed, 2025) == cap)
    check("the free-agency gap has no sanity error", signed["sanity"] == [])
    amateur = run(
        [
            txn("2024-03-28", "SE", "Yankees selected the contract of X.", to=NYY),
            txn("2024-06-01", "REL", "Yankees released X.", frm=NYY),
            txn("2024-06-15", "SGN", "Yankees signed X to a minor league contract.", to=NYY),
        ],
        calendar, "2024-03-28", "2025-10-05",
    )
    check("a minor-league signing does not restart service", days_of(amateur, 2025) == 0)
    check("a release leaves no credited day after it", amateur["sanity"] == [])

    drug = run(
        [
            txn("2024-03-28", "SE", "Yankees selected the contract of X.", to=NYY),
            txn("2024-06-01", "SU", "X suspended 80 games under the Joint Drug Agreement for a performance-enhancing drug.", to=NYY),
            txn("2024-08-20", "SC", "Yankees activated X.", to=NYY),
        ],
        calendar, "2024-03-28", "2024-10-05",
    )
    check("a drug suspension is not service", days_of(drug, 2024) < cap)
    check("the drug row is the drug rule", rb.classify_row(txn(
        "2024-06-01", "SU", "X suspended 80 games under the Joint Drug Agreement for a performance-enhancing drug.", to=NYY
    )).id == "stop.ped-suspension")

    real = rb.load_calendar()
    struck = evaluate(
        [txn("1994-04-03", "SE", "Yankees selected the contract of X.", to=NYY)],
        player_id=2, debut=dt.date(1994, 4, 3), horizon=dt.date(1995, 10, 5),
        calendar=real, exceptions=[], roy_years=set(),
    )
    check("1994 plus the 52-day assumption is capped at one year", days_of(struck, 1994) == cap)
    check("1995 plus the 23-day assumption is capped at one year", days_of(struck, 1995) == cap)
    check("the strike credit is one labeled rule", all(
        "accrual.strike-service-1996" in row["rules"] for row in struck["seasons"] if row["year"] in (1994, 1995)
    ))
    partial_strike = evaluate(
        [
            txn("1994-06-01", "SE", "Yankees selected the contract of X.", to=NYY),
            txn("1994-06-15", "REL", "Yankees released X.", frm=NYY),
        ],
        player_id=3, debut=dt.date(1994, 6, 1), horizon=dt.date(1994, 10, 5),
        calendar=real, exceptions=[], roy_years=set(),
    )
    check("a player who was gone before the strike does not get the 52 days", days_of(partial_strike, 1994) == 14)
    check("the strike assumption has no sanity error", struck["sanity"] == [] and partial_strike["sanity"] == [])

    tokyo = evaluate(
        [
            txn("2019-03-01", "SE", "Mariners selected the contract of X.", to=136),
            txn("2019-03-19", "SC", "Mariners placed X on the temporarily inactive list.", to=136),
            txn("2019-04-02", "REL", "Mariners released X.", frm=136),
        ],
        player_id=4, debut=dt.date(2019, 3, 20), horizon=dt.date(2019, 5, 1),
        calendar=real, exceptions=[], roy_years=set(),
    )
    # Domestic opener is March 28. March 28 through April 1 count. April 2 is the release.
    check("Tokyo days do not count and service resumes at the domestic opener", days_of(tokyo, 2019) == 5)
    check("the Tokyo row is not an unknown", tokyo["unknowns"] == [])

    lyles = evaluate(
        [
            txn("2024-03-28", "SE", "Royals selected the contract of X.", to=118),
            txn("2024-04-19", "SC", "Royals placed X on the temporarily inactive list.", to=118),
            txn("2024-04-20", "SC", "Royals placed X on the restricted list.", to=118),
        ],
        player_id=5, debut=dt.date(2024, 3, 28), horizon=dt.date(2024, 10, 5),
        calendar=real, exceptions=[], roy_years=set(),
    )
    # March 28 through April 18.
    check("an in-season temporarily inactive day does not count", days_of(lyles, 2024) == 22)

    quiet = evaluate(
        [
            txn("2010-04-04", "SE", "Mets selected the contract of X.", to=121),
            txn("2010-11-01", "SC", "Mets placed X on the temporarily inactive list.", to=121),
        ],
        player_id=6, debut=dt.date(2010, 4, 4), horizon=dt.date(2010, 11, 5),
        calendar=real, exceptions=[], roy_years=set(),
    )
    check("a postseason temporarily inactive row does not change the year", days_of(quiet, 2010) == cap)
    foley = evaluate(
        [
            txn("2020-07-23", "SE", "Blue Jays selected the contract of X.", to=141),
            txn("2020-09-29", "SC", "Blue Jays placed X on the reserve list.", to=141),
        ],
        player_id=7, debut=dt.date(2020, 7, 23), horizon=dt.date(2020, 10, 5),
        calendar=real, exceptions=[], roy_years=set(),
    )
    check("a reserve-list row after 2020 ended does not change the year", days_of(foley, 2020) == cap)

    blank = evaluate(
        [
            txn("2023-07-20", "DES", "Pirates designated X for assignment.", frm=PIT),
            txn("2023-07-24", "SC", "", to=PIT),
            txn("2023-07-25", "TR", "Pirates traded X to the Yankees.", frm=PIT, to=NYY),
        ],
        player_id=8, debut=dt.date(2023, 3, 30), horizon=dt.date(2023, 10, 5),
        calendar=real, exceptions=[], roy_years=set(),
    )
    check("a blank status row is not an unknown", blank["unknowns"] == [])
    check("a blank status row does not stop a designation", days_of(blank, 2023) == cap)

    heim = evaluate(
        [
            txn("2024-08-01", "DES", "Rays designated X for assignment.", frm=139),
            txn("2024-08-01", "TR", "Rays traded X to the Yankees.", frm=139, to=NYY),
        ],
        player_id=641680, debut=dt.date(2024, 3, 28), horizon=dt.date(2024, 10, 5),
        calendar=real, exceptions=[], roy_years=set(),
    )
    check("Heim's same-day designation and trade keeps the season", days_of(heim, 2024) == cap)

    military = run(
        [
            txn("2024-03-28", "SE", "Yankees selected the contract of X.", to=NYY),
            txn("2024-06-01", "SC", "Yankees placed X on the military list.", to=NYY),
        ],
        calendar, "2024-03-28", "2024-10-05",
    )
    check("military-list time counts", days_of(military, 2024) == cap)

    taxi = run(
        [
            txn("2020-07-23", "SE", "Yankees selected the contract of X.", to=NYY),
            txn("2020-08-01", "ASG", "X assigned to the alternate training site.", to=3266),
        ],
        cal(season(2020, "2020-07-23", "2020-09-27")), "2020-07-23", "2020-10-05",
    )
    check("a taxi-squad assignment stops service", days_of(taxi, 2020) < cap)

    opt_calendar = cal(season(2020, "2020-07-23", "2020-09-27"))
    opt_rows = [
        txn("2020-07-23", "SE", "Cardinals selected the contract of X.", to=138),
        txn("2020-07-23", "SC", "Cardinals placed X on the restricted list.", to=138),
    ]
    opted = run(opt_rows, opt_calendar, "2020-07-23", "2020-10-05", exceptions=[{
        "start": dt.date(2020, 7, 23), "end": dt.date(2020, 9, 27),
        "counts": True, "kind": "covid_high_risk_opt_out",
    }])
    not_opted = run(opt_rows, opt_calendar, "2020-07-23", "2020-10-05")
    check("a sourced 2020 high-risk opt-out counts as a full year", days_of(opted, 2020) == cap)
    check("an unsourced 2020 opt-out does not", days_of(not_opted, 2020) < cap)

    tatis_rows = [
        txn("2022-04-07", "SE", "Padres selected the contract of X.", to=135),
        txn("2022-08-12", "SC", "Padres roster status changed for X.", to=135),
        txn("2022-08-20", "REL", "Padres released X.", frm=135),
    ]
    without = [row for row in tatis_rows if "roster status" not in row["description"]]
    with_row = evaluate(tatis_rows, player_id=665487, debut=dt.date(2022, 4, 7), horizon=dt.date(2022, 10, 5), calendar=real, exceptions=[], roy_years=set())
    no_row = evaluate(without, player_id=665487, debut=dt.date(2022, 4, 7), horizon=dt.date(2022, 10, 5), calendar=real, exceptions=[], roy_years=set())
    check("a roster-status row does not move the clock", days_of(with_row, 2022) == days_of(no_row, 2022))
    check("the roster-status row is neutral", rb.classify_row(tatis_rows[1], real).id == "neutral.roster-status")

    # Optioned with no recall, plays the next year, then a later year has its own call-up.
    carried = cal(
        season(2023, "2023-03-30", "2023-10-01"),
        season(2024, "2024-03-28", "2024-09-29"),
        season(2025, "2025-03-27", "2025-09-28"),
    )
    sears = run(
        [
            txn("2023-03-30", "SE", "Athletics selected the contract of X.", to=133),
            txn("2023-08-02", "OPT", "Athletics optioned X to Las Vegas.", frm=133, to=400),
            txn("2025-08-01", "SC", "Padres activated X.", to=135),
            txn("2025-08-05", "OPT", "Padres optioned X to El Paso.", frm=135, to=4904),
            txn("2025-08-20", "CU", "Padres recalled X from El Paso.", to=135),
            txn("2025-08-26", "REL", "Padres released X.", frm=135),
        ],
        carried, "2023-03-30", "2025-10-05",
        appearance_years={2023, 2024, 2025},
    )
    check("a year with games and no recall is restored", days_of(sears, 2024) == cap)
    check("a later call-up does not inherit that restored season", days_of(sears, 2025) < 30)

    parker = evaluate(
        [
            txn("2026-03-14", "OPT", "Nationals optioned X to Rochester.", frm=120, to=534),
            txn("2026-04-14", "CU", "Nationals recalled X from Rochester.", to=120),
        ],
        player_id=680730, debut=dt.date(2025, 3, 27), horizon=dt.date(2026, 10, 5),
        calendar=real, exceptions=[], roy_years=set(), appearance_years={2025, 2026},
    )
    check("Karinchak's Opening Day recall is not replaced by a fake option", _karinchak_2021() > 100)
    check("a roster-status row does not hide the next list change", _roster_status_does_not_hide())
    check("Mexico Rojo is a national-team squad", rb.classify_row(
        txn("2024-02-01", "ASG", "X assigned to Mexico.", to=1192, name="Mexico Rojo")
    ).id == "neutral.national-team")
    check("Diablos Rojos del Mexico is not that squad", rb.classify_row(
        txn("2024-05-01", "ASG", "X assigned to Diablos Rojos del Mexico.", to=532, name="Diablos Rojos del Mexico")
    ).id != "neutral.national-team")
    check("an unsourced winter-looking club is not guessed", rb.classify_row(
        txn("2024-11-01", "ASG", "X assigned to Auckland Tuatara.", to=5405, name="Auckland Tuatara")
    ).id != "neutral.winter-league")
    check("a June option proves he was up, so Opening Day is not blocked by the August recall", _june_option_keeps_opening_day())
    check("games after a missing recall restart at the first game", _first_game_after_option() >= 105)
    check("Markakis counts from the day he opted back in", _markakis_raw() > 54)

    check("Parker's spring option is 20 days from the league opener", parker["option_days"].get("2026") == 20)
    check("those 20 days are not credited", days_of(parker, 2026) == 167)

    broken = {
        "service_days": 10,
        "seasons": [{
            "year": 2024,
            "credited_days": 10,
            "raw_days": 10,
            "prorated_days": 10,
            "start": "2024-03-28",
            "end": "2024-09-29",
            "rules": [],
            "ranges": [{"start": "2024-06-02", "end": "2024-06-11", "days": 10, "rules": []}],
        }],
        "boundaries": [{"date": "2024-06-01", "kind": "block", "rule": "stop.free-agency"}],
    }
    check("sanity catches service after free agency", any("free agency" in item for item in rb.sanity_errors(broken)))


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
    print("Fix round")
    test_fix_round_cases()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
