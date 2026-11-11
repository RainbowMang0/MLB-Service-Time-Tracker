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


if __name__ == "__main__":
    test_owner_dfa_decisions()
    print(f"\n{t.PASS} passed, {t.FAIL} failed")
    raise SystemExit(1 if t.FAIL else 0)
