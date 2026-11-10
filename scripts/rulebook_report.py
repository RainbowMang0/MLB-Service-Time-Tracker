#!/usr/bin/env python3
"""Compare the rulebook with the current service-time walk.

Writes reports/rulebook/ and one explanation file per batch under
data/rulebook/explanations/. Nothing here is published to the site.

    python3 scripts/rulebook_report.py
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cba  # noqa: E402
import fetch_mlb_data as mlb  # noqa: E402
import rulebook  # noqa: E402
import service_time_exceptions  # noqa: E402
from mlb_clubs import MLB_CLUB_IDS  # noqa: E402
from rulebook import (  # noqa: E402
    RULEBOOK_VERSION,
    Calendar,
    canonical_json,
    evaluate,
    load_calendar,
    open_questions,
)
from service_time import (  # noqa: E402
    SeasonWindow,
    Transaction,
    compute_service_time,
    is_active_stop,
    is_free_agency_election,
)
from update_service_time import (  # noqa: E402
    SERVICE_TIME_RULES_VERSION,
    _cache_transaction,
    _involves_mlb_club,
    _is_mlb_assignment,
    _is_minor_assignment,
    _opening_day_clubs,
    _season_windows_for_player,
)

RAW = ROOT / "data" / "raw_transactions"
PUBLISHED = ROOT / "docs" / "data" / "service_time.json"
WINDOWS = ROOT / "data" / "season_windows.json"
GOLDEN = ROOT / "tests" / "golden" / "players.json"
GOLDEN_TX = ROOT / "tests" / "golden" / "transactions"
REPORTS = ROOT / "reports" / "rulebook"
EXPLANATIONS = ROOT / "data" / "rulebook" / "explanations"
HORIZON = dt.date(2026, 11, 9)
# The last rules change the review named, so this report can be read next to it.
PRIOR_RULES_CHANGE = {"players_changed": 399, "up": 295, "down": 104}

# Hard cases from the review, and which hand-checked player exercises them.
SECTION_B = [
    ("Optional assignment and the 20-day credit", "Alvarado 2022, Cole 2014, Buxton 2015, Beck"),
    ("Option, then hurt in the minors", "No golden player. Covered by a unit test."),
    ("Major-league injured list, including the old disabled-list name", "Cole, Ohtani, Rendon, Burnes, Buxton"),
    ("Rehab assignment", "Cole, Alvarado, Rendon"),
    ("Restricted list does not count", "Alvarado, Bauer"),
    ("On-field suspension counts", "No golden player. Covered by a unit test."),
    ("Bereavement, paternity, family medical", "Alvarado, Ohtani, Rendon, Burnes. Family medical has no golden player; unit test."),
    ("Military list", "No golden player, and almost no such row in the saved feed."),
    ("2020 high-risk opt-out", "No golden player. Jordan Hicks is in the exceptions file."),
    ("2020 proration", "Bauer, Alvarado, Cole, Burnes, Ohtani, Rendon, Buxton"),
    ("Taxi squad / alternate site", "No golden player. The rule is in the rulebook."),
    ("Designation window", "Velázquez 2026"),
    ("Designation and trade the same day", "No golden player. Covered by a unit test (the Heim case)."),
    ("Free agency stops until a new signing", "Ohtani after 2023, Velázquez 2026, Bauer"),
    ("Preseason designation or outright earns no Opening Day", "Velázquez 2025 outright. Preseason designation is a unit test."),
    ("Trades and claims do not open a gap", "Bauer 2019, Baker 2025"),
    ("Selection starts; minor-league signing does not", "Cole, Ohtani, and the unit test"),
    ("League opener, not a later club opener", "Buxton 2016, Beck 2026, Bauer 2022"),
    ("International opener", "Ohtani 2024 and 2025"),
    ("Tiebreaker only for the clubs that played it, through 2021", "Burnes 2018, Cole 2013"),
    ("All-Star and winter league are not demotions", "Cole, Burnes, Ohtani, Buxton, Velázquez"),
    ("Same-day order does not matter", "Unit test. The golden files are not shuffled."),
]


def _install_v10_calendar() -> None:
    """The current walk's openers, from the saved club calendar. No network."""
    raw = json.loads(WINDOWS.read_text())["years"]
    windows = {}
    for year, row in raw.items():
        windows[int(year)] = {
            "start": dt.date.fromisoformat(row["start"]),
            "end": dt.date.fromisoformat(row["end"]),
            "clubs": {int(k): dt.date.fromisoformat(v) for k, v in row["clubs"].items()},
            "ends": {int(k): dt.date.fromisoformat(v) for k, v in row.get("ends", {}).items()},
        }
    reference = load_calendar()

    def season_window(year: int):
        if year in windows:
            row = windows[year]
            return row["start"], row["end"]
        if year in reference.seasons:
            return reference.league_start(year), reference.seasons[year].championship_end
        return dt.date(year, 3, 28), dt.date(year, 10, 1)

    def club_start(year: int, club_id: int | None):
        if year in windows and club_id in windows[year]["clubs"]:
            return windows[year]["clubs"][club_id]
        start, _end = season_window(year)
        return start

    def uniform(year: int):
        return season_window(year)[0]

    def club_end(year: int, club_id: int | None):
        _start, end = season_window(year)
        if year in windows and club_id in windows[year]["ends"]:
            return max(end, windows[year]["ends"][club_id])
        return end

    mlb.get_season_window = season_window
    mlb.get_uniform_season_start = uniform
    mlb.get_club_season_start = club_start
    mlb.get_club_season_end = club_end


def _appearance_years(player: dict) -> set[int]:
    years = set()
    for year, stats in (player.get("season_stats") or {}).items():
        if not isinstance(stats, dict):
            continue
        for group in stats.values():
            if isinstance(group, dict) and (group.get("gamesPlayed") or 0) > 0:
                years.add(int(year))
                break
    return years


def _v10(raw_rows: list[dict], player: dict, exceptions) -> dict:
    cached = []
    for raw in raw_rows:
        row = _cache_transaction(raw)
        if row is not None:
            cached.append(row)
    wrapped = []
    for row in cached:
        desc = row.get("description") or ""
        minor = _is_minor_assignment(row, MLB_CLUB_IDS)
        assignment = _is_mlb_assignment(row, MLB_CLUB_IDS)
        if not (
            _involves_mlb_club(row, MLB_CLUB_IDS)
            or minor
            or assignment
            or is_active_stop(desc)
            or is_free_agency_election(desc)
        ):
            continue
        wrapped.append(Transaction(
            date=dt.date.fromisoformat(row["date"][:10]),
            description=desc,
            team=row.get("team") if isinstance(row.get("team"), str) else None,
            minor_assignment=minor,
            mlb_assignment=assignment,
        ))
    debut_text = player.get("mlb_debut")
    debut = dt.date.fromisoformat(debut_text) if debut_text else None
    rules = cba.default()
    active, inactive = service_time_exceptions.periods_for_player(exceptions, int(player["id"]))
    floor = None
    if debut is not None:
        from service_time import roster_start_before_debut
        floor = roster_start_before_debut(wrapped, debut) or debut
    last_played = player.get("last_played")
    career_end = dt.date.fromisoformat(last_played) if last_played else None
    on_40 = bool(player.get("on_40_man"))
    if career_end is not None and not on_40:
        last_year = career_end.year
    else:
        last_year = HORIZON.year
    first_year = debut.year if debut else (int(cached[0]["date"][:4]) if cached else last_year)
    if cached:
        first_year = min(first_year, int(min(row["date"][:4] for row in cached)))
    season_teams = {}
    for season in player.get("seasons") or []:
        ids = [team for team in season.get("t") or [] if team in MLB_CLUB_IDS]
        if ids:
            season_teams[int(season["y"])] = ids
    txn_teams: dict[int, list[int]] = {}
    for row in cached:
        if not _involves_mlb_club(row, MLB_CLUB_IDS):
            continue
        year = int(row["date"][:4])
        for key in ("from_team_id", "team_id", "to_team_id"):
            team_id = row.get(key)
            if team_id in MLB_CLUB_IDS:
                bucket = txn_teams.setdefault(year, [])
                if team_id not in bucket:
                    bucket.append(team_id)
                break
    seasons = _season_windows_for_player(
        range(first_year, last_year + 1),
        season_teams,
        txn_teams,
        _opening_day_clubs(cached, MLB_CLUB_IDS),
    )
    if not seasons:
        seasons = [SeasonWindow(last_year, dt.date(last_year, 3, 28), dt.date(last_year, 10, 1))]
    ceiling = seasons[-1].end if (career_end is not None and not on_40 and seasons) else None
    result = compute_service_time(
        wrapped,
        seasons,
        presume_active_from_debut=debut is not None,
        horizon_end=HORIZON,
        accrual_floor=floor,
        accrual_ceiling=ceiling,
        seasons_with_appearances=_appearance_years(player) if player.get("season_stats") is not None else None,
        dfa_limbo_days=rules.require("designated_for_assignment.max_service_days"),
        force_active_periods=active,
        force_inactive_periods=inactive,
        roy_full_year_seasons=cba.roy_full_year_seasons_by_player().get(int(player["id"]), set()),
    )
    return {
        "service_time": result.formatted,
        "service_days": result.total_days,
        "by_season": {year: detail["credited_days"] for year, detail in result.by_season.items() if detail["credited_days"]},
    }


def _lines(old_days: int, new_days: int, full_year: int) -> list[str]:
    """Eligibility lines a player crosses between the two totals."""
    old_years, old_rest = divmod(old_days, full_year)
    new_years, new_rest = divmod(new_days, full_year)
    crossed = []

    def side(days, years):
        return years + (days % full_year) / full_year

    old_frac = side(old_days, old_years)
    new_frac = side(new_days, new_years)
    for label, mark in (("3 years", 3), ("6 years", 6)):
        if (old_frac < mark) != (new_frac < mark):
            crossed.append(label)
    # Super Two is the official 2026 line, 2.133, for players near two years.
    cutoff = 2 + 133 / full_year
    old_s2 = 2 <= old_frac < 3 and old_frac >= cutoff
    new_s2 = 2 <= new_frac < 3 and new_frac >= cutoff
    if old_s2 != new_s2:
        crossed.append("Super Two")
    old_arb = old_frac >= 3 or old_s2
    new_arb = new_frac >= 3 or new_s2
    if old_arb != new_arb and "3 years" not in crossed and "Super Two" not in crossed:
        crossed.append("arbitration")
    if (old_frac >= 6) != (new_frac >= 6) and "6 years" not in crossed:
        crossed.append("free agency")
    return crossed


def _primary_rule(result: dict, old_seasons: dict, new_seasons: dict) -> tuple[str, str]:
    """The rule that accounts for the largest season gap, and a one-line reason."""
    gaps = []
    for year in sorted(set(old_seasons) | set(new_seasons)):
        delta = new_seasons.get(year, 0) - old_seasons.get(year, 0)
        if delta:
            gaps.append((abs(delta), delta, year))
    if not gaps:
        return "unchanged", "The two counts match."
    gaps.sort(reverse=True)
    _abs, delta, year = gaps[0]
    season = next((row for row in result["seasons"] if row["year"] == year), None)
    rules = list(season["rules"]) if season else []
    notable = set(result.get("notable_rules") or [])
    if season is not None and season.get("credited_days") == 0 and delta < 0:
        if season.get("option_days", 0) > 0:
            return "stop.option", (
                f"{year} is {abs(delta)} days lower because he was on optional assignment "
                "and the old walk still credited the year."
            )
        return "accrual.presume-from-debut", (
            f"{year} is {abs(delta)} days lower because the rulebook does not keep a roster "
            "the old walk still had open."
        )
    if season is None and delta < 0:
        prior = [item for item in result["ranges"] if item["end"] < f"{year:04d}-01-01"]
        prior.sort(key=lambda item: item["end"])
        stops = [rule for rule in (prior[-1]["rules"] if prior else []) if rule.startswith("stop.")]
        chosen = stops[-1] if stops else "stop.reassigned-minors"
        reason = (
            f"{year} is {abs(delta)} days lower because {chosen} had already stopped the clock, "
            "and the old walk kept that year."
        )
        return chosen, reason
    direction = "more" if delta > 0 else "fewer"
    exception = next((rule for rule in rules if rule.startswith("exception.")), None)
    if exception is not None and abs(delta) > 5:
        chosen = exception
    elif abs(delta) <= 5 and "accrual.international-opener" in rules:
        chosen = "accrual.international-opener"
    elif abs(delta) <= 5 and "accrual.tiebreaker-through-2021" in rules:
        chosen = "accrual.tiebreaker-through-2021"
    elif abs(delta) <= 5:
        chosen = "accrual.league-opener"
    elif delta > 0 and "neutral.all-star" in notable and abs(delta) >= 15:
        chosen = "neutral.all-star"
    elif delta > 0 and "neutral.winter-league" in notable and abs(delta) >= 15:
        chosen = "neutral.winter-league"
    else:
        preferred = [
            "accrual.trade-resolves-dfa",
            "count.dfa",
            "stop.free-agency",
            "stop.restricted",
            "count.suspension",
            "stop.assigned-minors",
            "accrual.shortened-2020",
            "accrual.option-under-20",
            "stop.taxi-squad",
        ]
        chosen = next((rule for rule in preferred if rule in rules), None)
        if chosen is None:
            skip = {"accrual.presume-from-debut", "accrual.league-opener", "accrual.international-opener", "accrual.pre-debut-roster"}
            chosen = next((rule for rule in rules if rule not in skip), None) or "unattributed"
    if abs(delta) >= 25:
        reason = _long_reason(chosen, year, delta, result)
    else:
        reason = f"{year} is {abs(delta)} days {direction} under {chosen}."
    return chosen, reason


def _long_reason(rule: str, year: int, delta: int, result: dict) -> str:
    direction = "higher" if delta > 0 else "lower"
    gloss = {
        "neutral.all-star": "an All-Star assignment no longer stops the clock",
        "neutral.winter-league": "a winter-league assignment no longer ends the major-league roster",
        "accrual.league-opener": "the season starts on the league's first game",
        "accrual.international-opener": "an overseas opener is included for that club",
        "accrual.tiebreaker-through-2021": "a tiebreaker day is included only for the clubs that played it",
        "accrual.trade-resolves-dfa": "a trade or claim ends a designation and keeps service going",
        "count.dfa": "designation days count until the outright, release, or the end of the window",
        "stop.free-agency": "electing free agency stops service until the next major-league deal",
        "stop.assigned-minors": "an assignment to an affiliate stops service",
        "count.suspension": "an on-field suspension counts",
        "stop.restricted": "the restricted list does not count unless an exception says it does",
        "accrual.shortened-2020": "2020 is scaled to a full year for a player who was rostered the whole season",
        "accrual.option-under-20": "fewer than 20 option days are credited as service",
    }
    if rule.startswith("exception."):
        plain = "a sourced exception overrides the restricted-list wording (" + rule.removeprefix("exception.").replace("_", " ") + ")"
    else:
        plain = gloss.get(rule, rule.replace("-", " "))
    return f"{year} is {abs(delta)} days {direction} because {plain}."


def _threshold_rank(crossed: list[str], on_40: bool) -> tuple:
    return (0 if crossed else 1, 0 if on_40 else 1, -len(crossed))


def _golden_coverage(book_rules: list[str]) -> dict:
    fired = set()
    codes = set()
    for path in sorted(GOLDEN_TX.glob("*.json")):
        rows = json.loads(path.read_text())
        for row in rows:
            code = row.get("typeCode") or ""
            if code:
                codes.add(code)
            hit = rulebook.classify_row(row)
            if hit is not None:
                fired.add(hit.id)
    return {
        "rules_not_exercised_by_a_golden_player": sorted(set(book_rules) - fired),
        "type_codes_in_the_golden_files": sorted(codes),
        "section_b": [{"case": case, "where": where} for case, where in SECTION_B],
    }


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    _install_v10_calendar()
    published = json.loads(PUBLISHED.read_text())
    players = {int(row["id"]): row for row in published["players"]}
    exceptions = service_time_exceptions.load_exceptions()
    ruleset = cba.default()
    full_year = int(ruleset.require("service_time.days_per_credited_year"))
    book = rulebook.load_rulebook()
    rule_ids = [rule.id for rule in book["rules"]]

    paths = sorted(p for p in RAW.glob("*.json") if not p.name.startswith("_"))
    explanations = []
    differences = []
    unknown_rows = []
    ambiguous_rows = []
    sanity_failures = []
    compared = 0
    missing_published = 0

    def one_pass(store: bool) -> str:
        nonlocal compared, missing_published
        chunks = []
        compared = 0
        for index, path in enumerate(paths, start=1):
            doc = json.loads(path.read_text())
            player_id = int(doc["player_id"])
            player = players.get(player_id)
            if player is None:
                missing_published += 1
                continue
            debut_text = player.get("mlb_debut")
            debut = dt.date.fromisoformat(debut_text) if debut_text else None
            last_played = player.get("last_played")
            career_end = dt.date.fromisoformat(last_played) if last_played and not player.get("on_40_man") else None
            result = evaluate(
                doc.get("transactions") or [],
                player_id=player_id,
                name=player.get("name"),
                debut=debut,
                on_40_man=bool(player.get("on_40_man")),
                horizon=HORIZON,
                career_end=career_end,
                appearance_years=_appearance_years(player),
            )
            chunks.append(canonical_json(result))
            if not store:
                continue
            old = _v10(doc.get("transactions") or [], player, exceptions)
            if store:
                explanations.append({
                    "player_id": player_id,
                    "name": player.get("name"),
                    "on_40_man": bool(player.get("on_40_man")),
                    "rules_version": result["rules_version"],
                    "service_time": result["service_time"],
                    "service_days": result["service_days"],
                    "option_years_used": result["option_years_used"],
                    "option_years_saved": result["option_years_saved"],
                    "option_days": result["option_days"],
                    "seasons": [
                        {"year": row["year"], "days": row["credited_days"], "rules": row["rules"]}
                        for row in result["seasons"]
                    ],
                    "ranges": result["ranges"],
                })
                new_seasons = {row["year"]: row["credited_days"] for row in result["seasons"]}
                if result["service_days"] != old["service_days"] or result["service_time"] != old["service_time"]:
                    rule, reason = _primary_rule(result, old["by_season"], new_seasons)
                    differences.append({
                        "player_id": player_id,
                        "name": player.get("name"),
                        "on_40_man": bool(player.get("on_40_man")),
                        "old_service_time": old["service_time"],
                        "new_service_time": result["service_time"],
                        "old_days": old["service_days"],
                        "new_days": result["service_days"],
                        "delta_days": result["service_days"] - old["service_days"],
                        "crossed": _lines(old["service_days"], result["service_days"], full_year),
                        "rule_id": rule,
                        "rule_ids": sorted({
                            rule_id
                            for row in result["seasons"]
                            for rule_id in row["rules"]
                        }),
                        "reason": reason,
                    })
                unknown_rows.extend(result["unknowns"])
                ambiguous_rows.extend(result["ambiguous"])
                if result["sanity"]:
                    sanity_failures.append({
                        "player_id": player_id,
                        "name": player.get("name"),
                        "sanity": result["sanity"],
                    })
            if index % 500 == 0:
                print(f"  {index} of {len(paths)}", file=sys.stderr)
        chunks.sort()
        return _hash("\n".join(chunks))

    print("first pass", file=sys.stderr)
    first_hash = one_pass(store=False)
    # Reset collectors in case store was false. one_pass(store=False) does not append.
    print("second pass", file=sys.stderr)
    second_hash = one_pass(store=True)
    identical = first_hash == second_hash

    differences.sort(key=lambda row: (
        _threshold_rank(row["crossed"], row["on_40_man"])[0],
        _threshold_rank(row["crossed"], row["on_40_man"])[1],
        -abs(row["delta_days"]),
        row["player_id"],
    ))
    up = sum(1 for row in differences if row["delta_days"] > 0)
    down = sum(1 for row in differences if row["delta_days"] < 0)
    net = sum(row["delta_days"] for row in differences)
    by_rule = Counter(row["rule_id"] for row in differences)
    on40_diff = [row for row in differences if row["on_40_man"]]
    alumni_diff = [row for row in differences if not row["on_40_man"]]
    large = [row for row in differences if abs(row["delta_days"]) >= 25]
    unknown_counts = Counter(
        (row.get("type_code") or "", (row.get("description") or "")[:120])
        for row in unknown_rows
    )
    unknown_40 = sum(1 for row in unknown_rows if row.get("on_40_man"))
    grouped_rules = [
        {"rule_id": rule, "players": count}
        for rule, count in by_rule.most_common()
    ]

    summary = {
        "rules_version": RULEBOOK_VERSION,
        "compared_with": f"service-time walk version {SERVICE_TIME_RULES_VERSION} on the same saved transactions",
        "counted_through": HORIZON.isoformat(),
        "players_compared": compared if compared else len(explanations),
        "match": len(explanations) - len(differences),
        "differ": len(differences),
        "up": up,
        "down": down,
        "net_days": net,
        "prior_rules_change": PRIOR_RULES_CHANGE,
        "by_rule": grouped_rules,
        "on_40_man_differ": len(on40_diff),
        "alumni_differ": len(alumni_diff),
        "differences_of_25_days_or_more": len(large),
        "threshold_crossers": sum(1 for row in differences if row["crossed"]),
        "unknown_transactions": len(unknown_rows),
        "unknown_on_40_man": unknown_40,
        "ambiguous_restricted_list": len(ambiguous_rows),
        "sanity_failures": len(sanity_failures),
        "determinism": {
            "first_hash": first_hash,
            "second_hash": second_hash,
            "identical": identical,
        },
    }
    # explanations were appended once, during the second pass. compared was
    # reset. Use the explanation count.
    summary["players_compared"] = len(explanations)
    summary["match"] = len(explanations) - len(differences)

    EXPLANATIONS.mkdir(parents=True, exist_ok=True)
    explanation_path = EXPLANATIONS / "players.jsonl"
    with explanation_path.open("w", encoding="utf-8") as handle:
        for row in sorted(explanations, key=lambda item: item["player_id"]):
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    _write(REPORTS / "summary.json", summary)
    _write(REPORTS / "differences.json", differences)
    _write(REPORTS / "unknowns.json", {
        "transactions": len(unknown_rows),
        "on_40_man": unknown_40,
        "alumni": len(unknown_rows) - unknown_40,
        "by_code_and_description": [
            {"count": count, "type_code": code, "description": desc}
            for (code, desc), count in unknown_counts.most_common()
        ],
        "rows": unknown_rows,
    })
    _write(REPORTS / "ambiguous.json", {
        "note": "Restricted-list rows with no sourced exception. Classified as no service and flagged rather than guessed.",
        "count": len(ambiguous_rows),
        "rows": ambiguous_rows,
    })
    _write(REPORTS / "open_questions.json", open_questions())
    _write(REPORTS / "golden_coverage.json", _golden_coverage(rule_ids))
    _write(REPORTS / "determinism.json", summary["determinism"])
    _write(REPORTS / "sanity.json", {"failures": sanity_failures})
    _write(REPORTS / "large_differences.json", large)

    lines = [
        f"Compared {summary['players_compared']} players with the current walk (version {SERVICE_TIME_RULES_VERSION}).",
        f"Match: {summary['match']}. Differ: {summary['differ']} ({up} up, {down} down, net {net} days).",
        f"The previous rules change moved {PRIOR_RULES_CHANGE['players_changed']} players ({PRIOR_RULES_CHANGE['up']} up, {PRIOR_RULES_CHANGE['down']} down).",
        f"40-man differences: {len(on40_diff)}. Alumni differences: {len(alumni_diff)}.",
        f"Crossed 3 years, 6 years, Super Two, or an eligibility line: {summary['threshold_crossers']}.",
        f"Differences of 25 days or more: {len(large)}.",
        f"Unknown transactions: {len(unknown_rows)} ({unknown_40} on a current 40-man player).",
        f"Ambiguous restricted-list rows: {len(ambiguous_rows)}.",
        f"Sanity failures: {len(sanity_failures)}.",
        f"Two full replays {'matched' if identical else 'DID NOT MATCH'} ({first_hash[:12]}).",
        "",
        "By rule:",
    ]
    for row in grouped_rules:
        lines.append(f"- {row['rule_id']}: {row['players']}")
    (REPORTS / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if identical and not sanity_failures and unknown_40 == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
