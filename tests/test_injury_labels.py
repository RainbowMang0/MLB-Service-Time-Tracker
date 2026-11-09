"""Body-part labels for injured-list stints (scripts/injury_labels.py).

No pytest, no network. Covers the 30 sample mappings and the six test
players from the injury-body-part groundwork (Braxton's decisions,
Nov 2026). Real cached data moves on, so the stint-level checks assert
against today's cache, not a frozen historical count -- see the
coverage report printed by scripts/injury_coverage_report.py for any
drift from the groundwork's own numbers.

    python3 tests/test_injury_labels.py
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import injury_labels as il  # noqa: E402
from il_stints import SeasonWindows, build_stints  # noqa: E402

PASS = 0
FAIL = 0
CACHE = ROOT / "data" / "cache" / "transactions"
WINDOWS = SeasonWindows.load()
TODAY = dt.date(2026, 11, 8)


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def _row(reason: str, list_sentence: str = "on the 10-day injured list") -> str:
    return f"Some Club placed Some Player {list_sentence}. {reason}"


# Section 4 sample mappings, paraphrased in our own words (as the
# groundwork itself paraphrases MLB's wording). Each is a single row's
# reason text and its expected first label.
SAMPLES: list[tuple[str, str]] = [
    ("elbow ligament injury", "Elbow"),
    ("recovery from elbow-ligament reconstruction", "Elbow"),
    ("broken thumb", "Thumb"),
    ("hip flexor strain", "Hip"),
    ("elbow flexor strain", "Elbow"),
    ("lat strain", "Back"),
    ("shoulder lat strain", "Shoulder"),
    ("torn labrum, hip", "Hip"),
    ("labrum tear, shoulder", "Shoulder"),
    ("thoracic outlet symptoms", "Shoulder"),
    ("oblique", "Ribs/Side"),
    ("biceps inflammation", "Arm"),
    ("arm fatigue plus neck spasms", "Arm"),
    ("thumb ligament sprain", "Thumb"),
    ("blister on heel", "Foot"),
    ("infection, index finger", "Finger"),
    ("hand-foot-mouth disease", "Illness"),
    ("migraines", "Illness"),
    ("concussion-like symptoms", "Head"),
    ("typo hanstring strain", "Hamstring"),
    ("upper body injury", "Undisclosed"),
    ("irregular heartbeat", "Illness"),
    ("testicular injury", "Illness"),
    ("personal reasons", "Personal"),
    ("vascular condition with a shoulder word", "Illness"),
    ("blood clot in the upper arm", "Illness"),
]


def test_section_4_samples() -> None:
    for reason, expect in SAMPLES:
        labels = il.row_labels(_row(reason))
        got = labels[0] if labels else il.UNDISCLOSED
        check(f"{reason!r} -> {expect}", got == expect)


def test_no_reason_is_always_undisclosed() -> None:
    check("a bare placement sentence with nothing after it is Undisclosed", il.row_labels(_row("")) == [])
    check(
        "a 7-day list with no reason is Undisclosed, not Head",
        il.label_for_stint([(dt.date(2025, 5, 16), "Twins placed Player on the 7-day injured list.", "placement")])["label"]
        == il.UNDISCLOSED,
    )


def test_multi_mention_sets_multi_and_keeps_the_first() -> None:
    labels = il.row_labels(_row("elbow flexor strain"))
    check("elbow flexor strain names two parts, Elbow first", labels == ["Elbow", "Forearm"])
    result = il.label_for_stint([(dt.date(2024, 1, 1), _row("elbow flexor strain"), "placement")])
    check("the stint-level multi flag is set", result["multi"] is True and result["label"] == "Elbow")


def test_arm_is_dropped_for_a_specific_part() -> None:
    check(
        "biceps alone is Arm",
        il.row_labels(_row("biceps strain")) == ["Arm"],
    )
    check(
        "arm plus a named elbow drops the vague Arm label",
        il.row_labels(_row("arm and elbow soreness")) == ["Elbow"],
    )


def test_placement_wins_over_a_later_different_part() -> None:
    reasons = [
        (dt.date(2024, 6, 1), _row("oblique strain"), "placement"),
        (dt.date(2024, 6, 20), "Club transferred Player to the 60-day injured list. Elbow soreness.", "transfer"),
    ]
    result = il.label_for_stint(reasons)
    check(
        "the placement's label wins and the conflict is flagged internally",
        result["label"] == "Ribs/Side" and result["label_conflict"] is True,
    )


def test_later_row_fills_in_an_undisclosed_placement() -> None:
    reasons = [
        (dt.date(2022, 6, 15), "Club transferred Player from the 10-day injured list to the 60-day injured list.", "transfer"),
        (dt.date(2022, 6, 17), _row("wrist surgery"), "placement"),
    ]
    result = il.label_for_stint(reasons)
    check(
        "the later row's label fills in an Undisclosed placement",
        result["label"] == "Wrist" and result["label_source"] == "placement",
    )


def test_sensitive_rows_override_every_other_row() -> None:
    reasons = [
        (dt.date(2024, 6, 1), _row("shoulder soreness"), "placement"),
        (dt.date(2024, 6, 20), "Club transferred Player to the 60-day injured list. Blood clot.", "transfer"),
    ]
    result = il.label_for_stint(reasons)
    check(
        "a sensitive row anywhere in the stint wins, whatever the other rows say",
        result["label"] == "Illness" and result["label_source"] == "sensitive" and result["label_conflict"] is False,
    )
    personal = il.label_for_stint([(dt.date(2024, 1, 1), _row("personal reasons"), "placement")])
    check("a personal reason outranks an illness reason", personal["label"] == "Personal")


def test_ordinary_illness_keyword_is_not_tagged_sensitive() -> None:
    """Migraines/flu/infection map to Illness through the ordinary keyword
    table, not the Personal/medical override -- label_source should say
    so, even though the final label is the same word."""
    result = il.label_for_stint([(dt.date(2024, 1, 1), _row("migraines"), "placement")])
    check(
        "an everyday illness keyword is sourced as a placement label, not sensitive",
        result["label"] == "Illness" and result["label_source"] == "placement",
    )


def test_reason_extraction_handles_a_transfer_sentence() -> None:
    """A transfer names the injured list twice ('from the 15-day ... to
    the 60-day ...'); the reason must come from after the second, not
    leak 'to the 60-day injured list.' as if it were the reason."""
    text = "Astros transferred Player from the 15-day injured list to the 60-day injured list. Broken right thumb."
    check("the reason is read after the list is named a second time", il.row_labels(text) == ["Thumb"])
    bare = "Astros transferred Player from the 15-day injured list to the 60-day injured list."
    check("a transfer naming no reason is Undisclosed, not a leftover list phrase", il.row_labels(bare) == [])


def test_display_never_guesses() -> None:
    check("Undisclosed displays as Not disclosed", il.display(il.UNDISCLOSED) == "Not disclosed")
    check("a missing label (no stored text to derive from) also reads Not disclosed", il.display(None) == "Not disclosed")
    check("Illness and Personal show as-is", il.display("Illness") == "Illness" and il.display("Personal") == "Personal")
    check("an ordinary body part shows as-is", il.display("Elbow") == "Elbow")


# Test players: ids whose cached transactions are checked against the
# groundwork's reported per-stint labels (section 4). Any mismatch is
# printed, not hidden, since the cache has moved on since the
# groundwork was written and a small drift is expected.
TEST_PLAYERS: dict[int, list[tuple[int, str]]] = {
    660271: [(2018, "Elbow"), (2019, "Elbow"), (2023, "Ribs/Side"), (2026, "Arm")],
    543685: [
        (2015, "Knee"), (2015, "Quad"), (2018, "Toe"), (2019, "Elbow"),
        (2021, "Groin"), (2021, "Knee"), (2021, "Hamstring"), (2022, "Wrist"),
        (2022, "Wrist"), (2023, "Groin"), (2023, "Wrist"), (2023, "Lower leg"),
        (2024, "Hip"), (2024, "Hamstring"), (2024, "Back"), (2024, "Ribs/Side"),
        (2025, "Hip"), (2026, "Hip"),
    ],
    605141: [
        (2015, "Head"), (2018, "Abdomen"), (2021, "Hip"), (2021, "Hip"),
        (2022, "Ribs/Side"), (2024, "Hand"), (2026, "Ribs/Side"),
    ],
    642715: [(2021, "Quad"), (2022, "Ankle"), (2023, "Head"), (2026, "Elbow")],
    681293: [(2025, "Thumb"), (2025, "Elbow"), (2026, "Foot")],
    621439: [
        (2015, "Thumb"), (2017, "Groin"), (2018, "Illness"), (2018, "Toe"),
        (2019, "Wrist"), (2019, "Head"), (2019, "Shoulder"), (2019, "Shoulder"),
        (2020, "Shoulder"), (2021, "Hip"), (2021, "Hand"), (2022, "Hip"),
        (2023, "Ribs/Side"), (2023, "Hamstring"), (2024, "Knee"), (2024, "Hip"),
        (2025, "Undisclosed"), (2025, "Ribs/Side"), (2026, "Hip"), (2026, "Hip"),
        (2026, "Hip"),
    ],
}


def test_the_six_groundwork_players() -> None:
    for player_id, expected in TEST_PLAYERS.items():
        path = CACHE / f"{player_id}.json"
        if not path.exists():
            check(f"player {player_id} has a transaction cache", False)
            continue
        rows = json.loads(path.read_text())
        stints = build_stints(rows, WINDOWS, TODAY)
        il.apply_labels(stints)
        got = [(s.start.year, s.label) for s in stints]
        if got != expected:
            print(f"  note - player {player_id}: groundwork expected {expected}")
            print(f"  note - player {player_id}: cache today gives    {got}")
        check(f"player {player_id} matches the groundwork's {len(expected)} stints", got == expected)


def main() -> None:
    test_section_4_samples()
    test_no_reason_is_always_undisclosed()
    test_multi_mention_sets_multi_and_keeps_the_first()
    test_arm_is_dropped_for_a_specific_part()
    test_placement_wins_over_a_later_different_part()
    test_later_row_fills_in_an_undisclosed_placement()
    test_sensitive_rows_override_every_other_row()
    test_ordinary_illness_keyword_is_not_tagged_sensitive()
    test_reason_extraction_handles_a_transfer_sentence()
    test_display_never_guesses()
    test_the_six_groundwork_players()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
