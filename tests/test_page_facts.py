"""Club-hub counts match the names on the page, and last club follows the latest stint.

Run directly: python3 tests/test_page_facts.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import club_groups  # noqa: E402
import player_facts  # noqa: E402
import write_player_pages as pages  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
PASS = 0
FAIL = 0


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def _player(**extra) -> dict:
    base = {
        "id": extra.get("id", 1),
        "name": extra.get("name", "Player"),
        "on_40_man": True,
        "team": "New York Yankees",
        "position": "P",
        "service_time": "1.000",
        "service_days_total": pages.FULL_YEAR_DAYS,
        "free_agent_eligible": False,
        "arbitration_eligible": False,
        "super_two_candidate": False,
        "seasons": [],
    }
    base.update(extra)
    return base


def test_groups_match_the_name_list_not_the_raw_flag() -> None:
    """A free agent keeps arbitration_eligible. The name list does not count him there."""
    players = [
        _player(id=1, name="Zeta FA", free_agent_eligible=True, arbitration_eligible=True,
                service_time="9.000", service_days_total=9 * pages.FULL_YEAR_DAYS),
        _player(id=2, name="Alpha S2", super_two_candidate=True, arbitration_eligible=True,
                service_time="2.100", service_days_total=2 * pages.FULL_YEAR_DAYS + 100),
        _player(id=3, name="Beta Arb", arbitration_eligible=True,
                service_time="4.000", service_days_total=4 * pages.FULL_YEAR_DAYS),
        _player(id=4, name="Nobody Yet"),
    ]
    groups = club_groups.roster_groups(players)
    check("free agents are their own list", [p["name"] for p in groups["fa"]] == ["Zeta FA"])
    check("Super Two is its own list", [p["name"] for p in groups["s2"]] == ["Alpha S2"])
    check("arbitration list drops free agents and Super Two",
          [p["name"] for p in groups["arb"]] == ["Beta Arb"])

    html = pages.render_club("New York Yankees", players, "2026-11-10")
    facts = dict(re.findall(
        r"<span>([^<]+)</span><b class=\"big\">(?:<a[^>]*>)?(\d+)", html
    ))
    check("arbitration card is the name-list count", facts["Arbitration eligible"] == "1")
    check("Super Two card is the name-list count", facts["Super Two track"] == "1")
    check("free-agency card is the name-list count", facts["Free agency eligible"] == "1")
    check("meta description uses that arbitration count",
          "1 arbitration eligible" in html)
    check("meta description does not use the raw flag count",
          "3 arbitration eligible" not in html)
    desc = re.search(r'"description": "([^"]+)"', html)
    check("JSON-LD repeats the same arbitration count",
          desc is not None and "1 arbitration eligible" in desc.group(1))


def _named(html: str, label: str) -> int:
    para = re.search(r"<p class='key-players'>(.*?)</p>", html)
    if not para:
        return 0
    parts = re.split(r"<b>(.*?)</b>", para.group(1))
    for index in range(1, len(parts), 2):
        if parts[index].rstrip(":") == label:
            return parts[index + 1].count("<a ")
    return 0


def test_every_club_hub_card_matches_its_rendered_names() -> None:
    data = json.loads((ROOT / "docs" / "data" / "service_time.json").read_text())
    rostered = [p for p in data["players"] if p.get("on_40_man") and p.get("team")]
    by_club: dict[str, list[dict]] = {}
    for player in rostered:
        by_club.setdefault(player["team"], []).append(player)
    check("the directory covers all 30 clubs", len(by_club) == 30)

    mismatches = []
    for club, players in sorted(by_club.items()):
        groups = club_groups.roster_groups(players)
        html = pages.render_club(club, players, "2026-11-10")
        facts = dict(re.findall(
            r"<span>([^<]+)</span><b class=\"big\">(?:<a[^>]*>)?(\d+)", html
        ))
        arb = str(len(groups["arb"]))
        described = re.search(r"(?<!\d)" + arb + r" arbitration eligible", html) is not None
        if (
            facts.get("Arbitration eligible") != arb
            or facts.get("Super Two track") != str(len(groups["s2"]))
            or facts.get("Free agency eligible") != str(len(groups["fa"]))
            or _named(html, "Arbitration eligible") != len(groups["arb"])
            or _named(html, "Super Two track") != len(groups["s2"])
            or not described
        ):
            mismatches.append(club)
    check("all 30 hubs: cards, names, meta, and JSON-LD share one count", not mismatches)
    if mismatches:
        print("   off:", ", ".join(mismatches))


def test_last_club_is_the_latest_major_league_stint() -> None:
    """The stored team is stale, and the season-row club list is not in stint order.

    A minor-league signing names the parent club and must not count. The
    later selection onto that club's major league roster does.
    """
    names = {145: "Chicago White Sox", 144: "Atlanta Braves", 118: "Kansas City Royals"}
    rows = [
        {"id": 1, "date": "2026-05-19",
         "description": "Atlanta Braves released LHP Aaron Bummer.",
         "toTeam": {"id": 144}},
        {"id": 2, "date": "2026-07-21",
         "description": "Kansas City Royals signed free agent LHP Aaron Bummer to a minor league contract.",
         "toTeam": {"id": 118}},
        {"id": 3, "date": "2026-08-02",
         "description": "Kansas City Royals selected the contract of LHP Aaron Bummer from Omaha Storm Chasers.",
         "fromTeam": {"id": 541}, "toTeam": {"id": 118}},
        {"id": 4, "date": "2026-08-07",
         "description": "LHP Aaron Bummer elected free agency.",
         "toTeam": {"id": 541}},
        {"id": 5, "date": "2026-08-07",
         "description": "Kansas City Royals optioned LHP Aaron Bummer to Omaha Storm Chasers.",
         "fromTeam": {"id": 118}, "toTeam": {"id": 541}},
    ]
    check("the minor-league signing does not beat the later major league selection",
          player_facts.last_mlb_club_id(rows) == 118)
    real = player_facts.load_transaction_rows(607481)
    check("Aaron Bummer's saved feed ends on the Royals",
          player_facts.last_mlb_club_id(real) == 118)

    player = _player(
        id=607481, name="Aaron Bummer", on_40_man=False, team="Chicago White Sox",
        team_id=145, position="P", service_time="8.037", service_days_total=1413,
        free_agent_eligible=True, arbitration_eligible=True,
        seasons=[
            {"y": 2023, "d": 172, "t": [145], "src": "read"},
            {"y": 2026, "d": 59, "t": [118, 144], "src": "read"},
        ],
    )
    page = pages.render(player, names, "2026-11-10T00:00:00+00:00", transactions=rows)
    check("subtitle links Kansas City",
          'Last with <a href="/t/kansas-city-royals.html">Kansas City Royals</a>' in page)
    check("subtitle, meta, and JSON-LD do not keep the White Sox or treat Atlanta as last",
          "last with Chicago White Sox" not in page
          and "Last with Chicago White Sox" not in page
          and "Last with Atlanta Braves" not in page
          and "last with Atlanta Braves" not in page)
    check("JSON-LD affiliation is the Royals",
          '"name": "Kansas City Royals"' in page)
    check("the season table still shows both 2026 clubs",
          "Kansas City Royals, Atlanta Braves" in page or "Atlanta Braves" in page)
    check("method labels are not rendered",
          "How this season is known" not in page and "From transactions" not in page
          and "Carried forward" not in page)
    check("the source code stays on the season record", player["seasons"][0]["src"] == "read")


def test_career_closed_follows_the_active_flag_and_a_retirement() -> None:
    played = _player(
        id=900100, on_40_man=False, active=True, service_time="8.037",
        service_days_total=1413, free_agent_eligible=True, arbitration_eligible=True,
        seasons=[{"y": 2026, "d": 59, "t": [118]}],
    )
    inactive = _player(
        id=900101, on_40_man=False, active=False, service_time="8.037",
        service_days_total=1413, free_agent_eligible=True, arbitration_eligible=True,
        seasons=[{"y": 2026, "d": 111, "t": [116]}],
    )
    quiet = _player(
        id=900102, on_40_man=False, active=True, service_time="0.005",
        service_days_total=5, seasons=[{"y": 2024, "d": 5, "t": [141]}],
    )
    rostered = _player(
        on_40_man=True, active=True, free_agent_eligible=True, service_time="9.000",
        service_days_total=9 * pages.FULL_YEAR_DAYS,
    )
    retirement = [
        {"id": 1, "date": "2026-06-08",
         "description": "Los Angeles Angels selected the contract of 1B Example from Salt Lake Bees.",
         "fromTeam": {"id": 559}, "toTeam": {"id": 108}},
        {"id": 2, "date": "2026-09-19", "typeCode": "RET",
         "description": "1B Example retired.", "toTeam": {"id": 110}},
    ]
    retired = _player(
        id=900103, on_40_man=False, active=True, service_time="6.000",
        service_days_total=6 * pages.FULL_YEAR_DAYS, free_agent_eligible=True,
        seasons=[{"y": 2026, "d": 13, "t": [108]}],
    )
    recent = pages._milestone_sentence(played, 2026, [])
    finished = pages._milestone_sentence(inactive, 2026, [])
    still_active = pages._milestone_sentence(quiet, 2026, [])
    on_roster = pages._milestone_sentence(rostered, 2026, [])
    closed_by_row = pages._milestone_sentence(retired, 2026, retirement)
    check("an active player with days this season is not closed",
          "career closed" not in recent.lower() and "not on a 40-man roster" in recent)
    check("an inactive player is closed even with days this season",
          finished.startswith("His career closed"))
    check("an active player with no recent days is not closed",
          "career closed" not in still_active.lower())
    check("a 40-man spot is not a closed career", "career closed" not in on_roster.lower())
    check("a retirement row closes the career even when the flag is still active",
          closed_by_row.startswith("His career closed"))
    check("a retirement row does not supply the last club",
          player_facts.last_mlb_club_id(retirement) == 108)


def test_named_players_match_the_feed() -> None:
    """The four cases from the fact-check, read from the saved feed and flags."""
    data = json.loads((ROOT / "docs" / "data" / "service_time.json").read_text())
    by_id = {p["id"]: p for p in data["players"]}

    def sentence(pid: int) -> str:
        player = by_id[pid]
        rows = player_facts.load_transaction_rows(pid)
        return pages._milestone_sentence(player, 2026, rows)

    bummer = by_id[607481]
    bummer_rows = player_facts.load_transaction_rows(607481)
    check("Bummer's last club is Kansas City",
          player_facts.last_mlb_club_id(bummer_rows) == 118)
    check("Bummer is active and not closed",
          player_facts.active_flag(bummer) is True and "career closed" not in sentence(607481).lower())

    mancini_rows = player_facts.load_transaction_rows(641820)
    check("Mancini's last club is the Angels, not the retirement row",
          player_facts.last_mlb_club_id(mancini_rows) == 108)
    check("Mancini's retirement closes the career",
          sentence(641820).startswith("His career closed"))

    check("Verlander is inactive, so the career is closed",
          player_facts.active_flag(by_id[434378]) is False
          and sentence(434378).startswith("His career closed"))
    check("Orelvis Martinez is active and not closed",
          player_facts.active_flag(by_id[682619]) is True
          and "career closed" not in sentence(682619).lower())


def main() -> None:
    test_groups_match_the_name_list_not_the_raw_flag()
    test_every_club_hub_card_matches_its_rendered_names()
    test_last_club_is_the_latest_major_league_stint()
    test_career_closed_follows_the_active_flag_and_a_retirement()
    test_named_players_match_the_feed()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
