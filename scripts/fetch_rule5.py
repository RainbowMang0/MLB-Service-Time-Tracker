#!/usr/bin/env python3
"""One-off pull of 2026 Rule 5 eligibility for all 30 clubs.

Not part of the daily update. The public stats API is called with at
least a one-second pause between requests. Writes
data/rule5/rule5_2026.json. A cache under data/rule5/cache/ lets a
stopped run resume; that cache is not published.

    python3 scripts/fetch_rule5.py
    python3 scripts/fetch_rule5.py --club 137

No prospect ranks and no minor-league stat lines are requested. Those
are not eligibility inputs. Players with more than one professional
signing are classified from the earliest one.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).parent))

def _at_least_one_second() -> None:
    raw = os.environ.get("MLB_FETCH_PAUSE_SECONDS")
    try:
        current = float(raw) if raw else 1.0
    except ValueError:
        current = 1.0
    if current < 1.0:
        os.environ["MLB_FETCH_PAUSE_SECONDS"] = "1"


_at_least_one_second()

import fetch_mlb_data as api  # noqa: E402
from mlb_clubs import CLUB_NAMES, MLB_CLUB_IDS  # noqa: E402
from rule5_eligibility import (  # noqa: E402
    RULE5_YEAR,
    Draft,
    SeasonEnds,
    Signing,
    classify,
    how_text,
    round_sort_key,
)

SEASON = 2026
AS_OF = dt.date(2026, 11, 17)
OUT = ROOT / "data" / "rule5" / "rule5_2026.json"
CACHE = ROOT / "data" / "rule5" / "cache"
PEOPLE = CACHE / "people"
MINOR_SPORTS = (11, 12, 13, 14, 16)


def _get(path: str, params: dict | None = None):
    _at_least_one_second()
    return api._get(path, params)


def _read(path: pathlib.Path):
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _write(path: pathlib.Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def level_of(team_name: str, sport_id) -> tuple[str, int]:
    upper = (team_name or "").upper()
    if "DSL" in upper:
        return "DSL", 6
    return {
        11: ("Triple-A", 1),
        12: ("Double-A", 2),
        13: ("High-A", 3),
        14: ("Single-A", 4),
        16: ("Rookie", 5),
    }.get(sport_id, ("Other", 7))


def _date(value: str | None) -> dt.date | None:
    if not value or len(value) < 10:
        return None
    try:
        y, m, d = (int(p) for p in value[:10].split("-"))
        return dt.date(y, m, d)
    except ValueError:
        return None


def _age(birth: dt.date | None) -> int | None:
    if birth is None:
        return None
    years = AS_OF.year - birth.year
    if (AS_OF.month, AS_OF.day) < (birth.month, birth.day):
        years -= 1
    return years


def harvest_club(club_id: int) -> dict:
    cached = CACHE / f"club_{club_id}.json"
    found = _read(cached)
    if found:
        return found
    forty = _get(f"/teams/{club_id}/roster", {"rosterType": "40Man"})
    aff = _get(f"/teams/{club_id}/affiliates", {"season": SEASON})
    players: dict[int, dict] = {}
    for team in aff.get("teams") or []:
        team_id = team.get("id")
        if not team_id:
            continue
        roster = _get(
            f"/teams/{team_id}/roster",
            {"rosterType": "fullSeason", "season": SEASON},
        )
        sport = (team.get("sport") or {}).get("id")
        name = team.get("name") or ""
        label, rank = level_of(name, sport)
        for entry in roster.get("roster") or []:
            person = entry.get("person") or {}
            pid = person.get("id")
            if not isinstance(pid, int):
                continue
            row = {
                "id": pid,
                "name": person.get("fullName") or "",
                "position": (entry.get("position") or {}).get("abbreviation") or "",
                "level": label,
                "level_rank": rank,
                "affiliate": name,
            }
            prev = players.get(pid)
            if prev is None or rank < prev["level_rank"]:
                players[pid] = row
    forty_ids = []
    for entry in forty.get("roster") or []:
        pid = (entry.get("person") or {}).get("id")
        if isinstance(pid, int):
            forty_ids.append(pid)
    payload = {"club_id": club_id, "forty": forty_ids, "players": list(players.values())}
    _write(cached, payload)
    print(
        f"  {CLUB_NAMES.get(club_id, club_id)}: "
        f"{len(forty_ids)} on the 40-man, {len(players)} on affiliate rosters",
        flush=True,
    )
    return payload


def _slim(person: dict) -> dict:
    signings = []
    seen = set()
    for txn in person.get("transactions") or []:
        if not isinstance(txn, dict):
            continue
        code = txn.get("typeCode")
        if code not in {"SGN", "SFA"}:
            continue
        day = (txn.get("date") or "")[:10]
        if len(day) < 10 or (day, code) in seen:
            continue
        seen.add((day, code))
        signings.append({"date": day, "type": code})
    drafts = []
    for draft in person.get("drafts") or []:
        if not isinstance(draft, dict):
            continue
        try:
            year = int(draft.get("year"))
        except (TypeError, ValueError):
            continue
        pick = draft.get("pickNumber")
        try:
            pick = int(pick) if pick is not None else None
        except (TypeError, ValueError):
            pick = None
        drafts.append({
            "year": year,
            "round": draft.get("pickRound") or None,
            "pick": pick,
        })
    return {
        "id": person.get("id"),
        "name": person.get("fullName") or "",
        "birth_date": (person.get("birthDate") or "")[:10] or None,
        "birth_country": person.get("birthCountry") or None,
        "position": (person.get("primaryPosition") or {}).get("abbreviation") or "",
        "signings": signings,
        "drafts": drafts,
    }


def _fetch_batch(ids: list[int]) -> None:
    if not ids:
        return
    try:
        data = _get(
            "/people",
            {"personIds": ",".join(str(i) for i in ids), "hydrate": "draft,transactions"},
        )
    except Exception as exc:
        if len(ids) == 1:
            print(f"  !! person {ids[0]} failed: {exc}", flush=True)
            _write(PEOPLE / f"{ids[0]}.json", {"id": ids[0], "error": True})
            return
        mid = len(ids) // 2
        _fetch_batch(ids[:mid])
        _fetch_batch(ids[mid:])
        return
    got = set()
    for person in data.get("people") or []:
        slim = _slim(person)
        pid = slim.get("id")
        if not isinstance(pid, int):
            continue
        got.add(pid)
        _write(PEOPLE / f"{pid}.json", slim)
    missing = [i for i in ids if i not in got]
    for pid in missing:
        if len(ids) == 1:
            _write(PEOPLE / f"{pid}.json", {"id": pid, "error": True})
        else:
            _fetch_batch([pid])


def fetch_people(ids: list[int]) -> None:
    missing = [i for i in ids if not (PEOPLE / f"{i}.json").exists()]
    print(f"bios to fetch: {len(missing)} of {len(ids)}", flush=True)
    step = 15
    for start in range(0, len(missing), step):
        batch = missing[start:start + step]
        _fetch_batch(batch)
        done = min(start + step, len(missing))
        if done % 150 == 0 or done == len(missing):
            print(f"  bios {done}/{len(missing)}", flush=True)


def _season_payload(year: int) -> dict:
    path = CACHE / "seasons.json"
    store = _read(path) or {}
    key = str(year)
    if key in store:
        return store[key]
    minor_ends = []
    majors = None
    for sport in (1,) + MINOR_SPORTS:
        data = _get("/seasons", {"sportId": sport, "season": year})
        seasons = data.get("seasons") if isinstance(data, dict) else None
        row = seasons[0] if seasons else {}
        if sport == 1:
            majors = row.get("regularSeasonEndDate")
        else:
            minor_ends.append(row.get("postSeasonEndDate") or row.get("seasonEndDate"))
    minor = [d for d in minor_ends if d]
    payload = {
        "all_minor_leagues": max(minor) if minor else None,
        "majors_championship": majors,
    }
    store[key] = payload
    _write(path, store)
    return payload


def season_ends_for(years: set[int]) -> dict[int, SeasonEnds]:
    out = {}
    for year in sorted(years):
        row = _season_payload(year)
        out[year] = SeasonEnds(
            all_minor_leagues=_date(row.get("all_minor_leagues")),
            majors_championship=_date(row.get("majors_championship")),
        )
    return out


def _drafts(rows: list[dict]) -> list[Draft]:
    return [
        Draft(row["year"], row.get("round"), row.get("pick"))
        for row in rows
        if isinstance(row.get("year"), int)
    ]


def _signings(rows: list[dict]) -> list[Signing]:
    out = []
    for row in rows:
        day = _date(row.get("date"))
        code = row.get("type")
        if day and code in {"SGN", "SFA"}:
            out.append(Signing(day, code))
    return out


def build_player(roster: dict, bio: dict, ends: dict[int, SeasonEnds]) -> dict:
    signings = _signings(bio.get("signings") or [])
    drafts = _drafts(bio.get("drafts") or [])
    birth = _date(bio.get("birth_date"))
    country = bio.get("birth_country")
    used_ends = None
    if signings:
        first = min(signings, key=lambda s: (s.on, s.type_code))
        if first.on.month >= 9:
            used_ends = ends.get(first.on.year)
    decision = classify(
        birth,
        signings,
        birth_country=country,
        drafts=drafts,
        season_ends=used_ends,
    )
    signing = decision.signing
    how = how_text(signing, drafts, country) if signing else None
    draft_round = None
    if signing and signing.type_code == "SGN":
        for draft in drafts:
            if draft.year == signing.on.year and draft.round:
                draft_round = draft.round
                break
    name = bio.get("name") or roster.get("name") or ""
    position = roster.get("position") or bio.get("position") or ""
    return {
        "player_id": roster["id"],
        "name": name,
        "position": position,
        "birth_date": birth.isoformat() if birth else None,
        "age": _age(birth),
        "birth_country": country,
        "signing_date": signing.on.isoformat() if signing else None,
        "signing_type": signing.type_code if signing else None,
        "signing_count": len({(s.on, s.type_code) for s in signings}),
        "signed_year": signing.on.year if signing else None,
        "how": how,
        "draft_round": draft_round,
        "draft_sort": list(round_sort_key(draft_round)),
        "level": roster.get("level") or "Other",
        "level_rank": roster.get("level_rank") or 7,
        "affiliate": roster.get("affiliate") or "",
        "status": decision.status,
        "estimated": decision.estimated,
        "estimated_reasons": list(decision.reasons),
        "first_eligible_year": decision.first_eligible_year,
        "age_on_june5": decision.age_on_june5,
        "june5": decision.june5.isoformat() if decision.june5 else None,
        "meetings": decision.meetings,
        "note": decision.note,
    }


def build(club_ids: list[int]) -> dict:
    print("rosters", flush=True)
    harvested = [harvest_club(cid) for cid in club_ids]
    protected = {pid for club in harvested for pid in club["forty"]}
    by_player: dict[int, tuple[int, dict]] = {}
    for club in harvested:
        for row in club["players"]:
            if row["id"] in protected:
                continue
            prev = by_player.get(row["id"])
            if prev is None or row["level_rank"] < prev[1]["level_rank"]:
                by_player[row["id"]] = (club["club_id"], row)
    print(f"non-40-man players: {len(by_player)}", flush=True)
    fetch_people(sorted(by_player))
    fall_years = set()
    bios = {}
    for pid in by_player:
        bio = _read(PEOPLE / f"{pid}.json") or {"id": pid, "error": True}
        bios[pid] = bio
        for row in bio.get("signings") or []:
            day = _date(row.get("date"))
            if day and day.month >= 9:
                fall_years.add(day.year)
    print(f"season dates for {len(fall_years)} years", flush=True)
    ends = season_ends_for(fall_years) if fall_years else {}
    clubs = []
    for club_id in club_ids:
        rows = []
        for pid, (owner, roster) in by_player.items():
            if owner != club_id:
                continue
            rows.append(build_player(roster, bios.get(pid) or {}, ends))
        rows.sort(key=lambda r: (r["name"], r["player_id"]))
        counts = {"eligible_first": 0, "eligible_again": 0, "not_yet": 0, "unknown": 0, "estimated": 0}
        for row in rows:
            key = {
                "Eligible (first time)": "eligible_first",
                "Eligible (again)": "eligible_again",
                "Not yet": "not_yet",
                "Unknown": "unknown",
            }[row["status"]]
            counts[key] += 1
            if row["estimated"]:
                counts["estimated"] += 1
        clubs.append({
            "club_id": club_id,
            "club": CLUB_NAMES[club_id],
            "forty_man": len(next(c["forty"] for c in harvested if c["club_id"] == club_id)),
            "counts": counts,
            "players": rows,
        })
        c = counts
        print(
            f"  {CLUB_NAMES[club_id]}: first {c['eligible_first']}, "
            f"again {c['eligible_again']}, not yet {c['not_yet']}, "
            f"unknown {c['unknown']}, estimated {c['estimated']}",
            flush=True,
        )
    clubs.sort(key=lambda c: c["club"])
    totals = {k: sum(c["counts"][k] for c in clubs) for k in clubs[0]["counts"]} if clubs else {}
    return {
        "rule5_year": RULE5_YEAR,
        "protection_deadline": "2026-11-17",
        "draft_date": "2026-12-09",
        "counts": totals,
        "clubs": clubs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull the 2026 Rule 5 board.")
    parser.add_argument("--club", type=int, action="append", default=None)
    args = parser.parse_args()
    ids = args.club or sorted(MLB_CLUB_IDS)
    unknown = [i for i in ids if i not in MLB_CLUB_IDS]
    if unknown:
        raise SystemExit(f"not a major league club id: {unknown}")
    _at_least_one_second()
    payload = build(ids)
    if args.club and OUT.exists():
        current = json.loads(OUT.read_text())
        by_id = {c["club_id"]: c for c in current.get("clubs") or []}
        for club in payload["clubs"]:
            by_id[club["club_id"]] = club
        payload["clubs"] = sorted(by_id.values(), key=lambda c: c["club"])
        payload["counts"] = {
            k: sum(c["counts"][k] for c in payload["clubs"])
            for k in ("eligible_first", "eligible_again", "not_yet", "unknown", "estimated")
        }
    _write(OUT, payload)
    print(f"wrote {OUT}", flush=True)
    print(json.dumps(payload["counts"]), flush=True)


if __name__ == "__main__":
    main()
