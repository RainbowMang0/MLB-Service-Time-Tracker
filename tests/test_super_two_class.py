#!/usr/bin/env python3
"""
The Super Two class is ranked only after the prior-season day floor.

A player qualifies for the class with at least two and under three years
AND the prior-season day floor. The top share of that class, ties included,
is Super Two. Ranking the wider two-to-three-year group first and applying
the day floor afterward is a different cutoff.

Run directly: python3 tests/test_super_two_class.py
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import super_two as S  # noqa: E402

PASS = 0
FAIL = 0
YEAR = 2026


def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def _player(pid, name, total, days_in_year):
    prior = total - days_in_year
    return {
        "id": pid,
        "name": name,
        "seasons": [{"y": YEAR - 1, "d": prior}, {"y": YEAR, "d": days_in_year}],
        "service_days_total": total,
    }


def _league(n, high, days_in_year, name):
    """n players, one day apart, descending from `high`, all in the band."""
    db = {}
    full = S.FULL_YEAR_DAYS
    for i in range(n):
        total = high - i
        assert full * 2 <= total < full * 3
        db[str(i)] = _player(i, f"{name}{i:02d}", total, days_in_year)
    return db


def test_day_floor_defines_the_class_before_the_rank():
    """Ten players sit above the real class but miss the day floor.

    They would occupy the top of a ranking of the whole band. They are not
    in the class, so they do not set the cutoff.
    """
    full = S.FULL_YEAR_DAYS
    floor = S.SUPER_TWO_MIN_PRECEDING_SEASON_DAYS
    db = _league(50, full * 2 + full - 1, floor + 14, "In")
    for i in range(10):
        total = full * 3 - 1 - i
        db[f"out{i}"] = _player(1000 + i, f"Short{i:02d}", total, floor - 1)

    eligible = S.build_class(db, YEAR)
    wide = S.build_class(db, YEAR, minimum_days=1)
    cutoff = S.compute_cutoff(db, YEAR)
    wide_count = S._round_half_up(len(wide) * S.SUPER_TWO_TOP_FRACTION)
    check("the day floor keeps the short stints out of the class", len(eligible) == 50)
    check("the wider band still counts them", len(wide) == 60)
    check("22% of the eligible class rounds to 11", cutoff["qualifying_count"] == 11)
    check("the cutoff is the 11th eligible player",
          cutoff["cutoff_days"] == eligible[10][0])
    check("ranking the wider band first would take a different player",
          wide[wide_count - 1][0] != cutoff["cutoff_days"])
    check("that cutoff sits higher than the day-floor class",
          wide[wide_count - 1][0] > cutoff["cutoff_days"])


def test_a_tie_at_the_slot_is_the_cutoff_and_a_day_under_is_not():
    """Ties share the cutoff. A player one day under the tie does not raise it.

    This is the shape of a class whose published line sits on a tie and
    whose own 22% slot is the next player down: the computed cutoff is his
    service, not the tie above him.
    """
    full = S.FULL_YEAR_DAYS
    floor = S.SUPER_TWO_MIN_PRECEDING_SEASON_DAYS
    # 50 players, 22% rounds to 11. Ranks 9 and 10 (1-indexed 9-10) tied,
    # rank 11 one day under that tie, alone.
    high = full * 2 + 160
    db = {}
    for i in range(8):
        db[str(i)] = _player(i, f"Top{i:02d}", high - i, floor + 20)
    tie = high - 8
    db["tie-a"] = _player(20, "Tie A", tie, floor + 20)
    db["tie-b"] = _player(21, "Tie B", tie, floor + 20)
    slot = tie - 1
    db["slot"] = _player(22, "Slot", slot, floor + 20)
    for i in range(39):
        db[f"rest{i}"] = _player(100 + i, f"Rest{i:02d}", slot - 1 - i, floor + 20)

    ranked = S.build_class(db, YEAR)
    cutoff = S.compute_cutoff(db, YEAR)
    check("fifty players form the class", cutoff["class_size"] == 50)
    check("the qualifying count is 11", cutoff["qualifying_count"] == 11)
    check("the 11th player is alone one day under the tie", ranked[10][0] == slot)
    check("the two players just above him are tied",
          ranked[8][0] == ranked[9][0] == tie)
    check("the cutoff is the slot, not the tie above it", cutoff["cutoff_days"] == slot)

    applied = S.apply_super_two(db, YEAR)
    check("apply uses that same cutoff when no official line replaces it",
          applied["cutoff_days"] == slot)
    tied = [p for p in db.values() if p["service_days_total"] == tie]
    check("both tied players above the slot are Super Two",
          len(tied) == 2 and all(p["super_two_candidate"] for p in tied))
    check("the player on the slot is Super Two", db["slot"]["super_two_candidate"] is True)
    below = [p for p in db.values() if p["service_days_total"] == slot - 1]
    check("a player one day under the slot is not Super Two",
          below and all(not p["super_two_candidate"] for p in below))


if __name__ == "__main__":
    test_day_floor_defines_the_class_before_the_rank()
    test_a_tie_at_the_slot_is_the_cutoff_and_a_day_under_is_not()
    print(f"\n{PASS} passed, {FAIL} failed")
    raise SystemExit(1 if FAIL else 0)
