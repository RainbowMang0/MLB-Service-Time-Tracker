#!/usr/bin/env python3
"""
Contract years change list membership and the short page line.

Service time is not touched. A row counts only when it has guaranteed
seasons and the club's agreement is confirmed. An unconfirmed row, and a
note that someone was missing from a free-agent snapshot, do not.

Run directly: python3 tests/test_contracts.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import contracts  # noqa: E402
import write_player_pages as wpp  # noqa: E402

PASS = 0
FAIL = 0

# The eight deals the lists were getting wrong. Years are the guarantee,
# not a service-time figure from a research sheet.
MUST_FIX = {
    666160: {"name": "Mickey Moniak", "through": 2028, "label": "Signed through 2028",
             "fa_year": 2028},
    663728: {"name": "Cal Raleigh", "through": 2030,
             "label": "Signed through 2030 · 2031 vesting option"},
    667670: {"name": "Brent Rooker", "through": 2029,
             "label": "Signed through 2029 · 2030 club option"},
    672695: {"name": "Geraldo Perdomo", "through": 2029,
             "label": "Signed through 2029 · 2030 club option"},
    680694: {"name": "Kyle Bradish", "through": 2031, "label": "Signed through 2031"},
    661388: {"name": "William Contreras", "through": 2027,
             "label": "Signed through 2027 · option exercised", "fa_year": 2027},
    670623: {"name": "Isaac Paredes", "through": 2027,
             "label": "Signed through 2027 · option exercised", "fa_year": 2027},
}
# The Nationals have not confirmed this one. The row stays; the lists do not.
UNCONFIRMED = 696285
# 2027 is a vesting option whose physical was not confirmed.
CHAPMAN = 547973
# No guaranteed seasons were recorded. They stay where service time put them.
NO_YEARS = {
    592178: "Kris Bryant",
    656302: "Dylan Cease",
}
# Named gaps: no row at all.
GAPS = (
    "Phil Maton",
    "Shelby Miller",
    "Jose Trevino",
    "Peter Lambert",
    "Yusei Kikuchi",
    "Jesus Luzardo",
    "Kyle Schwarber",
    "Adrian Houser",
    "Brandon Nimmo",
    "Danny Jansen",
)
COMING = 2027


def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def _norm(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name)
    return "".join(ch for ch in folded.lower() if ch.isascii() and ch.isalpha())


def _players():
    data = json.loads((ROOT / "docs" / "data" / "service_time.json").read_text())
    return [p for p in data["players"] if p.get("on_40_man")]


def test_file_shape():
    rows = contracts.load()
    check("the file loaded", len(rows) > 100)
    ids = list(rows)
    check("ids are unique", len(ids) == len(set(ids)))
    check("every row has a first and last season",
          all(row.first <= row.last for row in rows.values()))
    check("a confirmed row is in the file",
          any(row.governs() for row in rows.values()))
    young = rows[UNCONFIRMED]
    check("Jacob Young is stored", young.name == "Jacob Young")
    check("Jacob Young is unconfirmed", young.status == contracts.STATUS_UNCONFIRMED)
    check("an unconfirmed row does not cover 2027", contracts.covers(UNCONFIRMED, 2027) is False)
    check("an unconfirmed row has no page line", contracts.label_for(UNCONFIRMED, 2027) == "")
    check("his note stays in the file", "not confirmed" in young.unconfirmed.lower())
    for pid, name in NO_YEARS.items():
        check(f"{name} has no row", pid not in rows)
    names = {_norm(row.name) for row in rows.values()}
    for name in GAPS:
        check(f"{name} has no row", _norm(name) not in names)
    chapman = rows[CHAPMAN]
    check("Chapman's guarantee stops in 2026", chapman.last == 2026)
    check("2027 is only his vesting option", chapman.option_years == (2027,) and chapman.option_type == "vesting")
    check("Chapman is not covered for 2027", contracts.covers(CHAPMAN, 2027) is False)
    moniak_rows = [row for row in rows.values() if row.id == 666160]
    check("Moniak is one row, not an override plus a contract", len(moniak_rows) == 1)
    for pid, expect in MUST_FIX.items():
        row = rows[pid]
        check(f"{expect['name']} is confirmed through {expect['through']}",
              row.governs() and row.last == expect["through"] and contracts.covers(pid, 2027))
        check(f"{expect['name']} page line", contracts.label_for(pid, 2027) == expect["label"])
        check(f"{expect['name']} line has no source url", "http" not in contracts.label_for(pid, 2027))


def test_expired_contract_excludes_nobody():
    expired = contracts.Contract(
        id=1, name="Expired", club="A", signed="2019-01-01",
        first=2019, last=2024, option_years=(), option_type=None,
        status=contracts.STATUS_SIGNED, source_url="https://example.invalid", unconfirmed="",
    )
    active = contracts.Contract(
        id=2, name="Active", club="A", signed="2026-01-01",
        first=2027, last=2028, option_years=(), option_type=None,
        status=contracts.STATUS_SIGNED, source_url="https://example.invalid", unconfirmed="",
    )
    index = {1: expired, 2: active}
    check("an expired deal does not cover 2027", contracts.covers(1, 2027, index) is False)
    check("an expired deal has no line for 2027", contracts.label_for(1, 2027, index) == "")
    low = {"season_stats": {"2026": {"hitting": {"gamesPlayed": 4}}}}
    players = [
        {"id": 1, "name": "Expired", "team": "A", "on_40_man": True,
         "service_days_total": 4 * wpp.FULL_YEAR_DAYS, "service_time": "4.000",
         "arbitration_eligible": True, "super_two_candidate": False,
         "free_agent_eligible": False, **low},
        {"id": 2, "name": "Active", "team": "A", "on_40_man": True,
         "service_days_total": 4 * wpp.FULL_YEAR_DAYS, "service_time": "4.000",
         "arbitration_eligible": True, "super_two_candidate": False,
         "free_agent_eligible": False, **low},
    ]
    arb = {p["id"] for p in wpp.select_arbitration_eligible(players, COMING, index)}
    check("the expired contract stays arbitration-eligible", arb == {1})
    watch = {p["id"] for p, _r in wpp.select_non_tender_watch(players, COMING, index)}
    check("the expired contract can still be a non-tender candidate", 1 in watch and 2 not in watch)
    by_year = wpp.select_free_agents_by_year(players, COMING, index)
    years = {p["id"]: year for year, group in by_year.items() for p in group}
    # Four credited years, two short of free agency: after 2028, and the
    # 2019-24 deal does not pull that year forward or push it back.
    check("the expired contract keeps his service-time year", years.get(1) == 2028)
    check("the active contract moves to his last guaranteed season", years.get(2) == 2028)


def test_live_lists_use_guarantees_only():
    """Before is an empty contract index: service time alone. After is the file."""
    rostered = _players()
    by_id = {p["id"]: p for p in rostered}
    empty: dict = {}

    def buckets(index):
        now = {p["id"] for p in wpp.select_free_agents_now(rostered, COMING, index)}
        by_year = wpp.select_free_agents_by_year(rostered, COMING, index)
        arb_list = wpp.select_arbitration_eligible(rostered, COMING, index)
        arb = {p["id"] for p in arb_list}
        watch = {p["id"] for p, _r in wpp.select_non_tender_watch(arb_list, COMING, index)}
        return now, by_year, arb, watch

    before_now, before_years, before_arb, before_watch = buckets(empty)
    after_now, after_years, after_arb, after_watch = buckets(None)

    def year_of(groups, pid):
        for year, group in groups.items():
            if any(p["id"] == pid for p in group):
                return year
        return None

    print(
        "  counts before -> after | "
        f"arb {len(before_arb)} -> {len(after_arb)}; "
        f"non-tender {len(before_watch)} -> {len(after_watch)}; "
        f"FA now {len(before_now)} -> {len(after_now)}; "
        + ", ".join(
            f"after {year} {len(before_years.get(year, []))} -> {len(after_years.get(year, []))}"
            for year in range(2027, 2033)
        )
    )

    removed_now = before_now - after_now
    check("every player who left FA-now has a guarantee covering 2027",
          all(contracts.covers(pid, COMING) for pid in removed_now))
    check("nobody was removed from FA-now without a guarantee",
          removed_now <= {pid for pid in removed_now if contracts.covers(pid, COMING)})

    for pid, expect in MUST_FIX.items():
        player = by_id.get(pid)
        check(f"{expect['name']} is on a roster", player is not None)
        if player is None:
            continue
        check(f"{expect['name']} service time is the live figure",
              bool(player.get("service_time")))
        check(f"{expect['name']} is off the arbitration list", pid not in after_arb)
        check(f"{expect['name']} is off the non-tender list", pid not in after_watch)
        if "fa_year" in expect:
            check(f"{expect['name']} is filed after {expect['fa_year']}",
                  year_of(after_years, pid) == expect["fa_year"])
        else:
            check(f"{expect['name']} is not filed after 2027",
                  year_of(after_years, pid) != 2027)
            check(f"{expect['name']} is filed after his last guarantee",
                  year_of(after_years, pid) == expect["through"])
        page = wpp.render(player, {}, "2026-11-09T00:00:00+00:00", [])
        check(f"{expect['name']} page says {expect['label']}", expect["label"] in page)
        check(f"{expect['name']} page does not print the source",
              "http" not in page.split("signed-through")[-1][:80] if "signed-through" in page else False)
        check(f"{expect['name']} page names no salary site",
              not any(word in page for word in ("FanGraphs", "Spotrac", "Baseball-Reference", "Cot's")))

    young = by_id.get(UNCONFIRMED)
    check("Jacob Young is on a roster", young is not None)
    if young is not None:
        check("Jacob Young stays arbitration-eligible", UNCONFIRMED in after_arb)
        if UNCONFIRMED in before_watch:
            check("Jacob Young stays on the non-tender list", UNCONFIRMED in after_watch)
        else:
            check("Jacob Young was not a non-tender candidate", UNCONFIRMED not in after_watch)
        page = wpp.render(young, {}, "2026-11-09T00:00:00+00:00", [])
        check("Jacob Young has no Signed through line", "Signed through" not in page)
        check("his live service time is unchanged on the page",
              young["service_time"] in page)

    moniak = by_id[666160]
    projected = wpp.free_agency_year(moniak, COMING)
    check("Moniak's live service time projects after 2027", projected == 2027)
    check("the guarantee moves him to after 2028", year_of(after_years, 666160) == 2028)
    check("he is not left on after 2027", year_of(after_years, 666160) != 2027)

    contreras = by_id[661388]
    check("Contreras's live service time is 5.108", contreras.get("service_time") == "5.108")
    check("that still projects free agency after 2027",
          wpp.free_agency_year(contreras, COMING) == 2027)

    if CHAPMAN in before_now:
        check("Chapman stays a free agent now", CHAPMAN in after_now)
    for pid, name in NO_YEARS.items():
        if pid in before_now:
            check(f"{name} stays a free agent now", pid in after_now)
        check(f"{name} was not given a Signed through line",
              contracts.label_for(pid, COMING) == "")

    gap_ids = []
    for player in rostered:
        if _norm(player.get("name") or "") in {_norm(n) for n in GAPS}:
            gap_ids.append(player["id"])
    check("the ten named gaps were found on a roster", len(gap_ids) == len(GAPS))
    for pid in gap_ids:
        check(f"{by_id[pid]['name']} was not removed from FA-now",
              (pid in after_now) if pid in before_now else pid not in after_now)
        check(f"{by_id[pid]['name']} has no page line",
              contracts.label_for(pid, COMING) == "")

    # A player leaves a list only when a confirmed guarantee says so.
    for pid in (before_arb - after_arb):
        check(f"id {pid} left arbitration because 2027 is guaranteed",
              contracts.covers(pid, COMING))
    check("the arbitration list only shrank", len(after_arb) < len(before_arb))


def test_pages_do_not_cite_the_research():
    text = (ROOT / "scripts" / "contracts.py").read_text()
    for banned in ("site-conflicts", "FanGraphs", "Spotrac", "Baseball-Reference", "Cot's"):
        check(f"contracts.py does not mention {banned}", banned not in text)
    sample = wpp.render_free_agents([], {}, "2026-11-09T00:00:00+00:00")
    check("the free-agent page carries the projection note",
          contracts.PROJECTION_NOTE in sample)
    club = wpp.render_club_free_agents("Colorado Rockies", [], [], "2026-11-09T00:00:00+00:00", 2027)
    check("a club free-agent page carries the same note", contracts.PROJECTION_NOTE in club)


if __name__ == "__main__":
    test_file_shape()
    test_expired_contract_excludes_nobody()
    test_live_lists_use_guarantees_only()
    test_pages_do_not_cite_the_research()
    print(f"\n{PASS} passed, {FAIL} failed")
    raise SystemExit(1 if FAIL else 0)
