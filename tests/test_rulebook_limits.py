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


if __name__ == "__main__":
    test_owner_dfa_decisions()
    test_midseason_entry_and_option_total()
    print(f"\n{t.PASS} passed, {t.FAIL} failed")
    raise SystemExit(1 if t.FAIL else 0)
