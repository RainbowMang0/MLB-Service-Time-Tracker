"""Rule 5 eligibility against the hand-checked players and the edge cases.

No pytest and no network. The dates below are the public signing records
already checked by hand. A debut, a prospect rank, and a minor-league day
count are not inputs.

    python3 tests/test_rule5.py
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from rule5_eligibility import (  # noqa: E402
    ELIGIBLE_AGAIN,
    ELIGIBLE_FIRST,
    NOT_YET,
    REASON_SEVERAL,
    REASON_WINTER,
    UNKNOWN,
    Draft,
    SeasonEnds,
    Signing,
    classify,
    how_text,
    june5_before,
)

PASS = 0
FAIL = 0


def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def _s(iso: str, code: str, description: str = "") -> Signing:
    y, m, d = (int(p) for p in iso.split("-"))
    return Signing(dt.date(y, m, d), code, description)


def _b(iso: str) -> dt.date:
    y, m, d = (int(p) for p in iso.split("-"))
    return dt.date(y, m, d)


# Hand-checked against the public signing feed. Expected status uses the
# June 5 immediately before the earliest professional contract.
HAND_CHECKED = [
    {
        "name": "Carlos Lagrange",
        "birth": "2003-05-25",
        "country": "Dominican Republic",
        "signings": [("2022-02-03", "SFA")],
        "june5": "2021-06-05",
        "age": 18,
        "meetings": (2022, 2023, 2024, 2025, 2026),
        "year": 2026,
        "status": ELIGIBLE_FIRST,
        "estimated": True,
        "winter": True,
    },
    {
        "name": "Henry Lalane",
        "birth": "2004-05-18",
        "country": "Dominican Republic",
        "signings": [("2021-05-04", "SFA")],
        "june5": "2020-06-05",
        "age": 16,
        "meetings": (2021, 2022, 2023, 2024, 2025),
        "year": 2025,
        "status": ELIGIBLE_AGAIN,
        "estimated": True,
        "winter": True,
    },
    {
        "name": "Joe Whitman",
        "birth": "2001-09-17",
        "country": "USA",
        "signings": [("2023-07-17", "SGN")],
        "drafts": [Draft(2023, "2C", 69)],
        "june5": "2023-06-05",
        "age": 21,
        "meetings": (2023, 2024, 2025, 2026),
        "year": 2026,
        "status": ELIGIBLE_FIRST,
        "estimated": False,
        "how": "Round 2C",
    },
    {
        "name": "Luis De La Torre",
        "birth": "2003-09-06",
        "country": "Dominican Republic",
        "signings": [("2023-07-03", "SFA")],
        "june5": "2023-06-05",
        "age": 19,
        "meetings": (2023, 2024, 2025, 2026),
        "year": 2026,
        "status": ELIGIBLE_FIRST,
        "estimated": False,
        "how": "International",
    },
    {
        "name": "Marty Gair",
        "birth": "2003-07-17",
        "country": "USA",
        "signings": [("2023-07-18", "SGN")],
        "drafts": [Draft(2023, "13", 403)],
        "june5": "2023-06-05",
        "age": 19,
        "meetings": (2023, 2024, 2025, 2026),
        "year": 2026,
        "status": ELIGIBLE_FIRST,
        "estimated": False,
        "how": "Round 13",
    },
    {
        "name": "Reggie Crawford",
        "birth": "2000-12-04",
        "country": "USA",
        "signings": [("2022-07-28", "SGN")],
        # Drafted in 2019 and did not sign. That draft is not a contract.
        "drafts": [Draft(2022, "1", 30), Draft(2019, "37", 1099)],
        "june5": "2022-06-05",
        "age": 21,
        "meetings": (2022, 2023, 2024, 2025),
        "year": 2025,
        "status": ELIGIBLE_AGAIN,
        "estimated": False,
        "how": "Round 1",
    },
    {
        "name": "Adrián Sugastey",
        "birth": "2002-10-23",
        "country": "Panama",
        "signings": [("2019-07-02", "SFA")],
        "june5": "2019-06-05",
        "age": 16,
        "meetings": (2019, 2020, 2021, 2022, 2023),
        "year": 2023,
        "status": ELIGIBLE_AGAIN,
        "estimated": False,
        "how": "International",
    },
    {
        "name": "Jancel Villarroel",
        "birth": "2005-01-17",
        "country": "Venezuela",
        "signings": [("2022-08-03", "SFA")],
        "june5": "2022-06-05",
        "age": 17,
        "meetings": (2022, 2023, 2024, 2025, 2026),
        "year": 2026,
        "status": ELIGIBLE_FIRST,
        "estimated": False,
        "how": "International",
    },
    {
        "name": "Sabin Ceballos",
        "birth": "2002-08-17",
        "country": "USA",
        "signings": [("2023-07-12", "SGN"), ("2022-08-02", "SGN")],
        "drafts": [Draft(2022, "14", 418), Draft(2023, "3", 94)],
        "june5": "2022-06-05",
        "age": 19,
        "meetings": (2022, 2023, 2024, 2025),
        "year": 2025,
        "status": ELIGIBLE_AGAIN,
        "estimated": True,
        "several": True,
        "how": "Round 14",
    },
    {
        "name": "Will Bednar",
        "birth": "2000-06-13",
        "country": "USA",
        "signings": [("2021-07-23", "SGN")],
        "drafts": [Draft(2021, "1", 14)],
        "june5": "2021-06-05",
        "age": 20,
        "meetings": (2021, 2022, 2023, 2024),
        "year": 2024,
        "status": ELIGIBLE_AGAIN,
        "estimated": False,
        "how": "Round 1",
    },
    {
        "name": "Felix Arronde",
        "birth": "2003-04-25",
        "country": "Cuba",
        "signings": [("2021-06-28", "SFA")],
        "june5": "2021-06-05",
        "age": 18,
        "meetings": (2021, 2022, 2023, 2024, 2025),
        "year": 2025,
        "status": ELIGIBLE_AGAIN,
        "estimated": False,
        "how": "International",
        "signed_year": 2021,
    },
]


def _decide(row):
    signings = [_s(iso, code) for iso, code in row["signings"]]
    return classify(
        _b(row["birth"]),
        signings,
        birth_country=row.get("country"),
        drafts=row.get("drafts"),
    )


def test_hand_checked_players():
    for row in HAND_CHECKED:
        dec = _decide(row)
        name = row["name"]
        check(f"{name} status is {row['status']}", dec.status == row["status"])
        check(f"{name} June 5 is {row['june5']}", dec.june5 == _b(row["june5"]))
        check(f"{name} age on that June 5 is {row['age']}", dec.age_on_june5 == row["age"])
        check(f"{name} meetings are {row['meetings']}", dec.meeting_years == row["meetings"])
        check(f"{name} first eligible year is {row['year']}", dec.first_eligible_year == row["year"])
        check(f"{name} estimated is {row['estimated']}", dec.estimated is row["estimated"])
        if row.get("how"):
            label = how_text(dec.signing, row.get("drafts"), row.get("country"))
            check(f"{name} signed as {row['how']}", label == row["how"])
        if row.get("winter"):
            check(f"{name} carries the winter/spring note", REASON_WINTER in dec.reasons)
        if row.get("several"):
            check(f"{name} carries the several-signings note", REASON_SEVERAL in dec.reasons)
            check(
                f"{name} uses the earliest contract",
                dec.signing.on == _b(row["signings"][-1][0] if row["name"] == "Sabin Ceballos" else row["signings"][0][0]),
            )


def test_carlos_lagrange_is_first_eligible_in_december_2026():
    dec = classify(
        dt.date(2003, 5, 25),
        [_s("2022-02-03", "SFA")],
        birth_country="Dominican Republic",
    )
    check("Lagrange June 5 is 2021-06-05", dec.june5 == dt.date(2021, 6, 5))
    check("Lagrange was 18 on that date", dec.age_on_june5 == 18)
    check(
        "Lagrange's meetings run December 2022 through December 2026",
        dec.meeting_years == (2022, 2023, 2024, 2025, 2026),
    )
    check("Lagrange is first eligible in 2026", dec.first_eligible_year == 2026)
    check("Lagrange is eligible for the first time", dec.status == ELIGIBLE_FIRST)
    check("Lagrange is not left unknown", dec.status != UNKNOWN)
    check("Lagrange is estimated", dec.estimated is True)


def test_felix_arronde_signed_in_2021_not_2022():
    signing = _s("2021-06-28", "SFA")
    drafts: list[Draft] = []
    dec = classify(dt.date(2003, 4, 25), [signing], birth_country="Cuba", drafts=drafts)
    label = how_text(signing, drafts, "Cuba")
    check("Arronde's contract date is 2021-06-28", dec.signing is not None and dec.signing.on.isoformat() == "2021-06-28")
    check("Arronde's signing year is 2021", dec.signing.on.year == 2021)
    check("Arronde is labeled International", label == "International")
    check("Arronde's label is not a 2022 signing", "2022" not in f"{dec.signing.on.year}, {label}")
    check("Arronde was 18 on June 5, 2021", dec.age_on_june5 == 18 and dec.june5 == dt.date(2021, 6, 5))
    check("Arronde was first eligible in 2025", dec.first_eligible_year == 2025)
    check("Arronde is eligible again", dec.status == ELIGIBLE_AGAIN)
    check("a June 28 signing is not the winter/spring flag", dec.estimated is False)


def test_several_signings_use_the_earliest_contract_and_are_estimated():
    later = _s("2023-07-12", "SGN", "Atlanta Braves signed 3B Sabin Ceballos.")
    earlier = _s("2022-08-02", "SGN", "Contract voided")
    dec = classify(
        dt.date(2002, 8, 17),
        [later, earlier],
        birth_country="USA",
        drafts=[Draft(2023, "3", 94), Draft(2022, "14", 418)],
    )
    check("the earliest contract is the one that is used", dec.signing.on == dt.date(2022, 8, 2))
    check("a void note on that contract does not move the date", dec.signing.on.year == 2022)
    check("several signings are estimated", dec.estimated is True and REASON_SEVERAL in dec.reasons)
    check("Ceballos is eligible again from the 2022 contract", dec.status == ELIGIBLE_AGAIN)
    check("Ceballos's first eligible year is 2025", dec.first_eligible_year == 2025)
    check("the later draft round is not the label", how_text(dec.signing, [Draft(2022, "14")], "USA") == "Round 14")


def test_winter_and_spring_international_signees_are_estimated():
    # February, May, and a signing on June 5 itself. The status is still
    # computed from the June 5 before the contract.
    cases = [
        ("2022-02-03", dt.date(2021, 6, 5)),
        ("2021-05-04", dt.date(2020, 6, 5)),
        ("2022-06-05", dt.date(2021, 6, 5)),
        ("2022-12-15", dt.date(2022, 6, 5)),
    ]
    for iso, june5 in cases:
        dec = classify(_b("2003-05-25"), [_s(iso, "SFA")], birth_country="Cuba")
        check(f"{iso} uses June 5 {june5.isoformat()}", dec.june5 == june5)
        check(f"{iso} is estimated", dec.estimated is True and REASON_WINTER in dec.reasons)
        check(f"{iso} is not left unknown solely for the season", dec.status != UNKNOWN or dec.note != "")


def test_a_summer_international_signing_is_not_flagged_for_the_june5_date():
    dec = classify(
        dt.date(2005, 1, 17),
        [_s("2022-08-03", "SFA")],
        birth_country="Venezuela",
    )
    check("an August international signing is not estimated", dec.estimated is False)
    check("June 6 is the summer side of the cutoff", june5_before(dt.date(2022, 6, 6)) == dt.date(2022, 6, 5))
    summer = classify(
        dt.date(2003, 5, 25),
        [_s("2022-06-06", "SFA")],
        birth_country="Cuba",
    )
    check("June 6 international is not the winter/spring flag", REASON_WINTER not in summer.reasons)


def test_a_domestic_spring_free_agent_is_not_called_international():
    signing = _s("2024-03-01", "SFA")
    check("a U.S. spring free agent is a free agent", how_text(signing, [], "USA") == "Free agent")
    dec = classify(dt.date(2000, 1, 1), [signing], birth_country="USA")
    check("a domestic spring signing is not the international flag", REASON_WINTER not in dec.reasons)


def test_age_18_and_19_on_june_5():
    # Signed in July, so the June 5 of the signing year is the age date.
    younger = classify(dt.date(2003, 6, 6), [_s("2022-07-15", "SGN")], drafts=[Draft(2022, "2")])
    older = classify(dt.date(2003, 6, 5), [_s("2022-07-15", "SGN")], drafts=[Draft(2022, "2")])
    check("born June 6 is still 18 on June 5", younger.age_on_june5 == 18)
    check("age 18 takes five meetings and is first eligible in 2026", younger.meetings == 5 and younger.status == ELIGIBLE_FIRST)
    check("born on June 5 is 19 that day", older.age_on_june5 == 19)
    check("age 19 takes four meetings and was first eligible in 2025", older.meetings == 4 and older.status == ELIGIBLE_AGAIN)


def test_june_5_signing_date_picks_the_previous_june_5():
    on_the_day = classify(dt.date(2003, 5, 25), [_s("2022-06-05", "SFA")], birth_country="Cuba")
    day_after = classify(dt.date(2003, 5, 25), [_s("2022-06-06", "SFA")], birth_country="Cuba")
    check("a June 5 signing uses the previous June 5", on_the_day.june5 == dt.date(2021, 6, 5))
    check("that player is 18", on_the_day.age_on_june5 == 18)
    check("a June 6 signing uses June 5 of the same year", day_after.june5 == dt.date(2022, 6, 5))
    check("that player is 19", day_after.age_on_june5 == 19)
    check("the two dates are not the same age", on_the_day.age_on_june5 != day_after.age_on_june5)


def test_fall_signing_window():
    birth = dt.date(2000, 1, 1)  # 23 on any 2023 June 5, so four meetings
    ends = SeasonEnds(
        all_minor_leagues=dt.date(2023, 9, 28),
        majors_championship=dt.date(2023, 10, 1),
    )
    before = classify(birth, [_s("2023-09-20", "SGN")], drafts=[Draft(2023, "5")], season_ends=ends)
    gap = classify(birth, [_s("2023-09-29", "SGN")], drafts=[Draft(2023, "5")], season_ends=ends)
    after = classify(birth, [_s("2023-10-02", "SGN")], drafts=[Draft(2023, "5")], season_ends=ends)
    december = classify(birth, [_s("2023-12-01", "SFA")], birth_country="USA", season_ends=ends)
    check("a signing before the minor-league year ends is not delayed", before.status == ELIGIBLE_FIRST and before.first_eligible_year == 2026)
    check("a signing in the gap between the two season ends is unknown", gap.status == UNKNOWN)
    check("a signing after both seasons have ended is delayed", after.status == NOT_YET and after.first_eligible_year == 2027)
    check("a December signing skips that December", december.first_eligible_year == 2027 and december.status == NOT_YET)

    no_dates = classify(birth, [_s("2023-10-01", "SGN")], drafts=[Draft(2023, "5")])
    july = classify(birth, [_s("2023-07-20", "SGN")], drafts=[Draft(2023, "5")])
    november = classify(birth, [_s("2023-11-15", "SGN")], drafts=[Draft(2023, "5")])
    check("October is unknown when the season dates are missing", no_dates.status == UNKNOWN)
    check("July is not delayed", july.status == ELIGIBLE_FIRST and july.first_eligible_year == 2026)
    check("November is after the season when dates are missing", november.status == NOT_YET and november.first_eligible_year == 2027)


def test_missing_birth_date_or_signing_is_unknown():
    no_birth = classify(None, [_s("2023-07-17", "SGN")])
    no_sign = classify(dt.date(2001, 9, 17), [])
    check("a missing birth date is unknown", no_birth.status == UNKNOWN and no_birth.estimated is False)
    check("a missing signing is unknown", no_sign.status == UNKNOWN and no_sign.estimated is False)
    check("neither one invents a year", no_birth.first_eligible_year is None and no_sign.first_eligible_year is None)


def test_a_major_league_debut_does_not_start_the_clock():
    args = dict(birth_country="Dominican Republic")
    without = classify(dt.date(2003, 5, 25), [_s("2022-02-03", "SFA")], mlb_debut=None, **args)
    with_debut = classify(
        dt.date(2003, 5, 25),
        [_s("2022-02-03", "SFA")],
        mlb_debut=dt.date(2025, 4, 1),
        **args,
    )
    check("a debut leaves the decision unchanged", without == with_debut)
    check("the signing date is still February 2022", with_debut.signing.on == dt.date(2022, 2, 3))


def test_rendered_row_labels_arronde_2021_and_sorts_hand_checked_first():
    import write_rule5_pages as pages

    arr = {
        "player_id": 702373,
        "name": "Felix Arronde",
        "position": "P",
        "age": 23,
        "signed_year": 2021,
        "how": "International",
        "draft_round": None,
        "draft_sort": [999, ""],
        "level": "Double-A",
        "level_rank": 2,
        "affiliate": "Northwest Arkansas Naturals",
        "status": "Eligible (again)",
        "estimated": False,
        "estimated_reasons": [],
    }
    other = {
        "player_id": 1,
        "name": "Aaa Prospect",
        "position": "P",
        "age": 24,
        "signed_year": 2022,
        "how": "Round 1",
        "draft_round": "1",
        "draft_sort": [1, ""],
        "level": "Triple-A",
        "level_rank": 1,
        "affiliate": "Omaha Storm Chasers",
        "status": "Eligible (first time)",
        "estimated": True,
        "estimated_reasons": ["More than one professional signing; the earliest is used."],
    }
    ordered = pages.sort_players([other, arr])
    check("Arronde stays ahead of a higher level", [p["name"] for p in ordered] == ["Felix Arronde", "Aaa Prospect"])
    club = {
        "club": "Kansas City Royals",
        "counts": {"eligible_first": 1, "eligible_again": 1, "not_yet": 0, "unknown": 0, "estimated": 1},
        "players": [other, arr],
    }
    html_out = pages.render_club_page(club, {})
    row = html_out.split("Felix Arronde")[1].split("</tr>")[0]
    check("Arronde's row says 2021, International", "2021, International" in row)
    check("Arronde's row does not call him a 2022 signing", "2022" not in row)
    check("the other row can still say 2022", "2022, Round 1" in html_out)
    title = html_out.split("<title>")[1].split("</title>")[0]
    check("title aims at Rule 5 eligible Kansas City Royals 2026", "Rule 5 eligible Kansas City Royals 2026" in title)
    check("club title stays within a search snippet", len(title) <= 60)
    check("estimated banner is on the page", pages.ESTIMATE_BANNER in html_out)
    check("FAQ structured data is present", '"@type": "FAQPage"' in html_out)
    check("an estimated row is flagged", ">Estimated<" in html_out)
    for needle in (
        "Baseball-Reference", "Baseball Reference", "pipeline",
        "scripts/", ".py", "Last updated", "mlbplayers.com", "5(c)",
    ):
        check(f"rendered board has no {needle!r}", needle not in html_out)


def test_saved_board_matches_arronde_and_lagrange_when_present():
    path = ROOT / "data" / "rule5" / "rule5_2026.json"
    if not path.exists():
        check("saved board is not required for this run", True)
        return
    data = json.loads(path.read_text())
    found = {}
    for club in data["clubs"]:
        for player in club["players"]:
            if player["player_id"] in (702373, 801739):
                found[player["player_id"]] = player
    arr = found.get(702373)
    lag = found.get(801739)
    check("Arronde is on the saved board", arr is not None)
    check("Lagrange is on the saved board", lag is not None)
    if arr:
        check("saved Arronde signing date is 2021-06-28", arr.get("signing_date") == "2021-06-28")
        check("saved Arronde year is 2021", arr.get("signed_year") == 2021)
        check("saved Arronde how is International", arr.get("how") == "International")
        check("saved Arronde is not labeled 2022", arr.get("signed_year") != 2022)
        check("saved Arronde is eligible again", arr.get("status") == ELIGIBLE_AGAIN)
        check("saved Arronde is not estimated", arr.get("estimated") is False)
    if lag:
        check("saved Lagrange is first-time eligible", lag.get("status") == ELIGIBLE_FIRST)
        check("saved Lagrange June 5 is 2021-06-05", lag.get("june5") == "2021-06-05")
        check("saved Lagrange first year is 2026", lag.get("first_eligible_year") == 2026)
        check("saved Lagrange is estimated", lag.get("estimated") is True)


def test_pages_do_not_carry_banned_words_when_present():
    docs = ROOT / "docs"
    pages = [docs / "rule-5.html"] + list((docs / "t").glob("*-rule-5.html"))
    pages = [p for p in pages if p.exists()]
    if not pages:
        check("rendered pages are not required for this run", True)
        return
    banned = (
        "Baseball-Reference",
        "Baseball Reference",
        "baseball-reference",
        "pipeline",
        "scripts/",
        ".py",
        "Last updated",
        "last updated",
        "mlbplayers.com",
        "5(c)",
    )
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for needle in banned:
            check(f"{page.name} has no {needle!r}", needle not in text)
        check(f"{page.name} says the list is estimated", "Eligibility estimated from public signing data" in text)
        check(f"{page.name} has FAQ structured data", '"@type": "FAQPage"' in text or '"@type":"FAQPage"' in text)


def main():
    test_hand_checked_players()
    test_carlos_lagrange_is_first_eligible_in_december_2026()
    test_felix_arronde_signed_in_2021_not_2022()
    test_several_signings_use_the_earliest_contract_and_are_estimated()
    test_winter_and_spring_international_signees_are_estimated()
    test_a_summer_international_signing_is_not_flagged_for_the_june5_date()
    test_a_domestic_spring_free_agent_is_not_called_international()
    test_age_18_and_19_on_june_5()
    test_june_5_signing_date_picks_the_previous_june_5()
    test_fall_signing_window()
    test_missing_birth_date_or_signing_is_unknown()
    test_a_major_league_debut_does_not_start_the_clock()
    test_rendered_row_labels_arronde_2021_and_sorts_hand_checked_first()
    test_saved_board_matches_arronde_and_lagrange_when_present()
    test_pages_do_not_carry_banned_words_when_present()
    print(f"\n{PASS} passed, {FAIL} failed")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
