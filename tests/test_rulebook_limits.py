"""Owner decisions and the opener-restart limits.

    python3 tests/test_rulebook_limits.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_rulebook as t  # noqa: E402


def test_owner_dfa_decisions():
    calendar = t.cal(t.season(2024, "2024-03-28", "2024-04-10"))
    kept = t.run(
        [
            t.txn("2023-06-01", "SE", "Yankees selected the contract of X.", to=t.NYY),
            t.txn("2024-03-20", "DES", "Yankees designated X for assignment.", frm=t.NYY),
            t.txn("2024-03-25", "SC", "Yankees placed X on the 10-day injured list.", to=t.NYY),
        ],
        calendar, "2023-06-01", "2024-04-12",
    )
    t.check("a non-optioned spring designation on the injured list earns Opening Day", t.days_of(kept, 2024) > 0)
    claimed = t.run(
        [
            t.txn("2023-06-01", "SE", "Yankees selected the contract of X.", to=t.NYY),
            t.txn("2024-03-20", "DES", "Yankees designated X for assignment.", frm=t.NYY),
            t.txn("2024-03-22", "CLW", "Dodgers claimed X off waivers from the Yankees.", frm=t.NYY, to=t.LAD),
        ],
        calendar, "2023-06-01", "2024-04-12",
    )
    t.check("a spring designation that is claimed earns Opening Day", t.days_of(claimed, 2024) > 0)
    optioned = t.run(
        [
            t.txn("2024-03-01", "OPT", "Yankees optioned X to Scranton.", frm=t.NYY, to=531),
            t.txn("2024-03-20", "DES", "Yankees designated X for assignment.", frm=t.NYY),
        ],
        calendar, "2023-06-01", "2024-04-12",
    )
    t.check("a spring designation of an optioned player does not earn Opening Day", t.days_of(optioned, 2024) == 0)
    old = t.run(
        [
            t.txn("2010-04-05", "SE", "Yankees selected the contract of X.", to=t.NYY),
            t.txn("2010-06-01", "DES", "Yankees designated X for assignment.", frm=t.NYY),
        ],
        t.cal(t.season(2010, "2010-04-05", "2010-10-03")),
        "2010-04-05",
        "2010-10-05",
    )
    t.check(
        "a pre-2012 designation is flagged because the window length is unknown",
        any(flag["year"] == 2010 for flag in old["review_flags"]),
    )
    cup = t.run(
        [
            t.txn("2023-06-01", "SE", "Yankees selected the contract of X.", to=t.NYY),
            t.txn("2023-07-01", "OPT", "Yankees optioned X to Scranton.", frm=t.NYY, to=531),
        ],
        t.cal(t.season(2023, "2023-03-30", "2023-10-01"), t.season(2024, "2024-03-28", "2024-09-29")),
        "2023-06-01", "2024-10-05",
        appearance_years={2024},
        appearance_counts={2024: 1},
    )
    t.check("one game does not open the next season at the league opener", t.days_of(cup, 2024) == 0)
    regular = t.run(
        [
            t.txn("2024-08-02", "OPT", "Athletics optioned X to Las Vegas.", frm=133, to=400),
            t.txn("2025-08-01", "SC", "Padres activated X.", to=135),
            t.txn("2025-08-05", "OPT", "Padres optioned X to El Paso.", frm=135, to=4904),
        ],
        t.cal(t.season(2024, "2024-03-28", "2024-09-29"), t.season(2025, "2025-03-27", "2025-09-28")),
        "2024-03-28", "2025-10-05",
        appearance_years={2024, 2025},
        appearance_counts={2024: 120, 2025: 27},
    )
    t.check("a regular's later activation does not erase the opener", t.days_of(regular, 2025) > 100)
    signed = t.run(
        [
            t.txn("2021-02-12", "SFA", "Padres signed free agent X."),
            t.txn("2021-02-12", "SFA", "Padres signed free agent X to a minor league contract and invited him to spring training."),
            t.txn("2021-04-30", "SE", "Padres selected the contract of X.", to=135),
            t.txn("2021-05-04", "OPT", "Padres optioned X.", frm=135, to=4904),
        ],
        t.cal(t.season(2021, "2021-04-01", "2021-10-03")),
        "2021-04-30", "2021-10-05",
    )
    t.check("a spring invite does not count the days before the selection", t.days_of(signed, 2021) <= 4)
    suspended = t.run(
        [
            t.txn("2025-09-29", "CU", "Phillies recalled X.", to=t.PHI),
            t.txn("2026-06-25", "SC", "Phillies activated X from the restricted list.", to=t.PHI),
            t.txn("2026-06-25", "SC", "Phillies placed X on the 60-day injured list.", to=t.PHI),
        ],
        t.cal(t.season(2025, "2025-03-27", "2025-09-28"), t.season(2026, "2026-03-25", "2026-09-27")),
        "2025-09-29", "2026-10-05",
    )
    t.check("a missing restricted-list placement does not count before the activation", t.days_of(suspended, 2026) < 120)


def test_midseason_entry_and_option_total():
    import datetime as dt
    import rulebook_limits
    calendar = t.cal(t.season(2026, "2026-03-25", "2026-09-27"))
    signed = t.run(
        [
            t.txn("2025-11-06", "SFA", "Phillies signed free agent X to a minor league contract.", to=t.PHI),
            t.txn("2025-11-06", "ASG", "X assigned to Lehigh Valley IronPigs.", to=531),
            t.txn("2026-07-05", "SFA", "Philadelphia Phillies signed free agent X.", to=t.PHI),
            t.txn("2026-07-05", "OPT", "Philadelphia Phillies optioned X to Lehigh Valley IronPigs.", frm=t.PHI, to=531),
            t.txn("2026-07-21", "CU", "Philadelphia Phillies recalled X from Lehigh Valley IronPigs.", to=t.PHI),
        ],
        calendar, "2026-07-21", "2026-10-01",
        appearance_years={2026},
        appearance_counts={2026: 34},
    )
    t.check("a minor-league assignment does not reopen at the opener", t.days_of(signed, 2026) < 120)
    t.check("a July signing starts the clock", t.days_of(signed, 2026) >= 69)
    claimed = t.run(
        [
            t.txn("2011-03-13", "OPT", "Toronto Blue Jays optioned X to Las Vegas.", frm=141, to=400),
            t.txn("2011-06-02", "CLW", "Colorado Rockies claimed X off waivers from Toronto Blue Jays.", frm=141, to=115),
            t.txn("2011-06-02", "OPT", "Colorado Rockies optioned X to Colorado Springs.", frm=115, to=400),
            t.txn("2011-08-07", "CU", "Colorado Rockies recalled X from Colorado Springs.", to=115),
        ],
        t.cal(t.season(2011, "2011-03-31", "2011-09-28")),
        "2011-08-07", "2011-10-05",
        appearance_years={2011},
        appearance_counts={2011: 19},
    )
    t.check("a claim does not leave Opening Day credit in place", t.days_of(claimed, 2011) < 80)
    traded = t.run(
        [
            t.txn("2026-02-18", "ASG", "X assigned to Indianapolis Indians.", to=531),
            t.txn("2026-05-16", "TR", "Athletics traded a player to Pittsburgh for X.", frm=134, to=133),
            t.txn("2026-06-21", "OPT", "Athletics optioned X to Las Vegas.", frm=133, to=400),
        ],
        calendar, "2026-05-16", "2026-10-01",
        appearance_years={2026},
        appearance_counts={2026: 40},
    )
    traded_days = t.days_of(traded, 2026)
    t.check("a trade starts the clock on the trade date", 30 <= traded_days <= 50)
    regular = t.run(
        [
            t.txn("2022-08-02", "OPT", "Athletics optioned X to Las Vegas.", frm=133, to=400),
            t.txn("2025-07-31", "TR", "Athletics traded X to the Padres.", frm=133, to=135),
            t.txn("2025-08-05", "OPT", "Padres optioned X to El Paso.", frm=135, to=4904),
        ],
        t.cal(
            t.season(2022, "2022-04-07", "2022-10-05"),
            t.season(2023, "2023-03-30", "2023-10-01"),
            t.season(2024, "2024-03-28", "2024-09-29"),
            t.season(2025, "2025-03-27", "2025-09-28"),
        ),
        "2022-08-02", "2025-10-05",
        appearance_years={2023, 2024, 2025},
        appearance_counts={2023: 32, 2024: 32, 2025: 27},
    )
    t.check("a trade does not erase the opener for someone already up", t.days_of(regular, 2025) > 100)
    paper = t.run(
        [
            t.txn("2022-04-13", "SE", "Yankees selected the contract of X.", to=t.NYY),
            t.txn("2022-04-17", "OPT", "Yankees optioned X to Scranton.", frm=t.NYY, to=531),
            t.txn("2022-08-01", "TR", "Yankees traded X to the Athletics.", frm=t.NYY, to=133),
            t.txn("2022-08-02", "OPT", "Athletics optioned X to Las Vegas.", frm=133, to=400),
        ],
        t.cal(t.season(2022, "2022-04-07", "2022-10-05")),
        "2022-04-13", "2022-11-09",
        appearance_dates=[dt.date(2022, 8, 10), dt.date(2022, 10, 1)],
    )
    t.check("a trade optioned the next day does not count the trade date", t.days_of(paper, 2022) == 61)
    pieces = [
        (dt.date(2025, 8, 5), dt.date(2025, 8, 19)),
        (dt.date(2025, 8, 26), dt.date(2025, 9, 11)),
        (dt.date(2025, 9, 13), dt.date(2025, 9, 27)),
    ]
    credited = rulebook_limits.option_spells_to_credit(47, pieces, [], 20, 131)
    t.check("a season of options at or over 20 days is not credited", credited == [])
    spans = rulebook_limits.extend_open_spans(
        [(dt.date(2022, 8, 10), dt.date(2022, 10, 1))],
        [],
        t.cal(t.season(2022, "2022-04-07", "2022-10-05")),
        dt.date(2022, 11, 9),
    )
    t.check("a stint with no later stop runs through the championship end", spans[0][1] == dt.date(2022, 10, 5))


def test_spring_option_assignment_and_opening_week():
    import datetime as dt
    import rulebook_guard
    cal_2010 = t.cal(t.season(2010, "2010-04-04", "2010-10-03"))
    bowden = t.run(
        [
            t.txn("2009-09-05", "CU", "Yankees recalled X from Scranton.", to=t.NYY, frm=531),
            t.txn("2010-03-23", "OPT", "Yankees optioned X to Scranton.", frm=t.NYY, to=531),
            t.txn("2010-07-18", "ASG", "X assigned to Yankees.", to=t.NYY),
            t.txn("2010-07-23", "OPT", "Yankees optioned X to Scranton.", frm=t.NYY, to=531),
            t.txn("2010-08-14", "CU", "Yankees recalled X from Scranton.", to=t.NYY, frm=531),
            t.txn("2010-08-28", "OPT", "Yankees optioned X to Scranton.", frm=t.NYY, to=531),
            t.txn("2010-09-07", "CU", "Yankees recalled X from Scranton.", to=t.NYY, frm=531),
        ],
        cal_2010, "2008-08-30", "2010-10-05",
        appearance_years={2010},
        appearance_counts={2010: 14},
    )
    t.check("a spring option does not restart the season at the opener", t.days_of(bowden, 2010) == 46)
    sears = t.run(
        [t.txn("2022-04-17", "OPT", "Yankees optioned X to Scranton.", frm=t.NYY, to=531)],
        t.cal(t.season(2022, "2022-04-07", "2022-10-05")),
        "2022-04-13", "2022-11-09",
        appearance_dates=[dt.date(2022, 4, 13)],
    )
    t.check("a debut in the opening week starts at the opener", t.days_of(sears, 2022) == 10)
    called = t.run(
        [
            t.txn("2022-04-13", "SE", "Yankees selected the contract of X.", to=t.NYY),
            t.txn("2022-04-17", "OPT", "Yankees optioned X to Scranton.", frm=t.NYY, to=531),
        ],
        t.cal(t.season(2022, "2022-04-07", "2022-10-05")),
        "2022-04-13", "2022-11-09",
    )
    t.check("a selection on the debut stays on that date", t.days_of(called, 2022) == 4)
    season_2019 = t.season(2019, "2019-03-20", "2019-09-29", domestic="2019-03-28")
    wain = t.run(
        [
            t.txn("2018-10-29", "DFA", "X elected free agency."),
            t.txn("2018-10-30", "SFA", "Cardinals signed free agent X.", to=138),
            t.txn("2019-03-25", "ASG", "X assigned to Memphis.", to=235),
            t.txn("2019-06-10", "SC", "Cardinals placed X on the 10-day injured list.", to=138),
            t.txn("2019-06-20", "SC", "Cardinals activated X from the 10-day injured list.", to=138),
        ],
        t.cal(season_2019), "2005-09-11", "2019-10-05",
    )
    t.check("a spring assignment does not stop a major-league contract", t.days_of(wain, 2019) == 172)
    sent = t.run(
        [
            t.txn("2019-04-01", "SE", "Cardinals selected the contract of X.", to=138),
            t.txn("2019-06-01", "ASG", "X assigned to Memphis.", to=235),
        ],
        t.cal(season_2019), "2019-04-01", "2019-10-05",
    )
    t.check("an in-season assignment to an affiliate still stops service", t.days_of(sent, 2019) < 80)
    minor = t.run(
        [
            t.txn("2019-01-02", "SFA", "Cardinals signed free agent X to a minor league contract.", to=138),
            t.txn("2019-03-25", "ASG", "X assigned to Memphis.", to=235),
            t.txn("2019-06-01", "SE", "Cardinals selected the contract of X.", to=138),
        ],
        t.cal(season_2019), "2019-06-01", "2019-10-05",
    )
    t.check("a minor-league contract does not earn the days before the selection", t.days_of(minor, 2019) < 130)
    estes = t.run(
        [t.txn("2024-03-10", "OPT", "Athletics optioned X to Las Vegas.", frm=133, to=400)],
        t.cal(t.season(2024, "2024-03-28", "2024-09-30")),
        "2023-09-20", "2024-10-05",
        appearance_years={2024},
        appearance_counts={2024: 25},
        appearance_dates=[dt.date(2024, 5, 11), dt.date(2024, 9, 28)],
        appearance_checked={2024},
    )
    t.check("game dates after a spring option run from the first game", t.days_of(estes, 2024) == 143)
    labeled = {
        "rules": ["accrual.dfa-window-7", "count.dfa", "neutral.trade", "stop.released"],
        "ranges": [{
            "start": "2024-03-28",
            "end": "2024-06-19",
            "rules": ["accrual.dfa-window-7", "count.dfa", "neutral.trade", "stop.released"],
        }],
        "events": [
            {"date": "2024-06-14", "rule": "count.dfa"},
            {"date": "2024-06-20", "rule": "stop.released"},
        ],
    }
    t.check(
        "a season opened by a trade is not labeled as the designation window",
        rulebook_guard.explain_rule(labeled, 84) == "accrual.trade-entry",
    )
    t.check(
        "a measured season at least as high as the prior site can replace a later over-credit",
        rulebook_guard.measured_replaces(46, 41) and not rulebook_guard.measured_replaces(0, 172),
    )
    bridged = t.run(
        [t.txn("2022-04-20", "OPT", "Yankees optioned X.", frm=t.NYY, to=531)],
        t.cal(
            t.season(2022, "2022-04-07", "2022-10-05"),
            t.season(2023, "2023-03-30", "2023-10-01"),
            t.season(2024, "2024-03-28", "2024-09-29"),
        ),
        "2022-04-13", "2024-10-05",
        appearance_years={2022, 2024},
        appearance_counts={2022: 20, 2024: 20},
        appearance_dates=[dt.date(2022, 8, 10), dt.date(2022, 9, 1), dt.date(2024, 6, 1), dt.date(2024, 9, 1)],
        appearance_checked={2022, 2023, 2024},
    )
    t.check("game dates do not fill a season that has no games", t.days_of(bridged, 2023) == 0)


def test_same_day_option_ends_the_stint():
    import datetime as dt
    import rulebook_limits
    played = t.run(
        [
            t.txn("2024-03-28", "SE", "Yankees selected the contract of X.", to=t.NYY),
            t.txn("2024-05-01", "OPT", "Yankees optioned X to Scranton.", frm=t.NYY, to=531),
            t.txn("2024-09-30", "CU", "Yankees recalled X from Scranton.", to=t.NYY),
        ],
        t.cal(t.season(2024, "2024-03-28", "2024-09-30")),
        "2024-03-28", "2024-10-05",
        appearance_dates=[dt.date(2024, 4, 10), dt.date(2024, 5, 1)],
    )
    t.check("an option on the last game date does not fill the rest of the season", t.days_of(played, 2024) < 50)
    released = t.run(
        [
            t.txn("2024-04-01", "SE", "Yankees selected the contract of X.", to=t.NYY),
            t.txn("2024-06-01", "REL", "Yankees released X.", frm=t.NYY),
        ],
        t.cal(t.season(2024, "2024-03-28", "2024-09-30")),
        "2024-04-01", "2024-10-05",
        appearance_dates=[dt.date(2024, 6, 1), dt.date(2024, 8, 1)],
    )
    t.check("a release on a game date does not credit the next game", t.days_of(released, 2024) < 80)
    t.check(
        "a 30-day gap with no transactions is not a season to credit",
        rulebook_limits.untracked_gap([], [dt.date(2007, 6, 3), dt.date(2007, 7, 28)], 2007),
    )
    t.check(
        "a short gap with no transactions can still be credited",
        not rulebook_limits.untracked_gap([], [dt.date(2007, 6, 3), dt.date(2007, 6, 20)], 2007),
    )
    t.check(
        "a long gap with a transaction that year is not the empty-feed case",
        not rulebook_limits.untracked_gap(
            [(dt.date(2007, 6, 1), {}, None)],
            [dt.date(2007, 6, 3), dt.date(2007, 7, 28)],
            2007,
        ),
    )


def test_no_credited_day_carries_past_a_roster_stop():
    """Every saved player. A stint that began on or before an option, outright, or release cannot continue past it."""
    import datetime as dt
    import json
    import rulebook
    import rulebook_limits
    import rulebook_publish as pub
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    db = json.loads((root / "docs" / "data" / "service_time.json").read_text(encoding="utf-8"))
    horizon = dt.date.fromisoformat((db.get("generated_at") or "2026-11-16")[:10])
    calendar = rulebook.load_calendar()
    found = []
    for player in db.get("players") or []:
        result = pub.replay_one(player, horizon)
        path = root / "data" / "raw_transactions" / f"{player['id']}.json"
        rows = []
        if path.exists():
            for raw in json.loads(path.read_text(encoding="utf-8")).get("transactions") or []:
                day = rulebook._date(raw.get("date"))
                hit = rulebook.classify_row(raw, calendar) if day else None
                if day is not None:
                    rows.append((day, raw, hit))
        for season in result["seasons"]:
            ranges = [
                (dt.date.fromisoformat(item["start"]), dt.date.fromisoformat(item["end"]), item.get("rules") or [])
                for item in season["ranges"]
            ]
            extra = rulebook_limits.days_carried_past_stop(ranges, rows)
            if extra:
                found.append(f"{player.get('name')} {season['year']} {extra}")
                if len(found) >= 8:
                    break
        if len(found) >= 8:
            break
    t.check("no stint continues past an option, outright, or release " + ("; ".join(found) if found else ""), not found)


if __name__ == "__main__":
    test_owner_dfa_decisions()
    test_midseason_entry_and_option_total()
    test_spring_option_assignment_and_opening_week()
    test_same_day_option_ends_the_stint()
    test_no_credited_day_carries_past_a_roster_stop()
    print(f"\n{t.PASS} passed, {t.FAIL} failed")
    raise SystemExit(1 if t.FAIL else 0)
