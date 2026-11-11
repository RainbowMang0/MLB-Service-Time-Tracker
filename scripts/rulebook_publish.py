"""Replay saved players through the rulebook and prepare the published records.

A season below the figure already on the site is kept in data/rulebook/holds.json
until a person sets cleared to true. Higher seasons publish. The daily job calls
apply_to_database before it writes; the morning gate is unchanged.

    python scripts/rulebook_publish.py --rollback
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import sys
import unicodedata
from pathlib import Path

import cba
import rulebook
from rulebook import evaluate

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = ROOT / "docs" / "data" / "service_time.json"
SNAPSHOT = ROOT / "data" / "rulebook" / "published_snapshot.json"
HOLDS_PATH = ROOT / "data" / "rulebook" / "holds.json"
RAW = ROOT / "data" / "raw_transactions"
RULES = ROOT / "data" / "rulebook" / "rules.json"
RESTRICTED = ROOT / "data" / "reference" / "restricted_list_classification.csv"
REPORTS = ROOT / "reports" / "replay"
# Seasons the owner asked to see by name. A match that is not lower has no hold.
NAMED_SEASONS = {671732: 2024, 641857: 2018, 676664: 2025}


class PublishRefused(RuntimeError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _games(player: dict) -> dict[int, int]:
    out: dict[int, int] = {}
    for year, stats in (player.get("season_stats") or {}).items():
        if not isinstance(stats, dict):
            continue
        best = 0
        for group in stats.values():
            if isinstance(group, dict):
                try:
                    best = max(best, int(group.get("gamesPlayed") or 0))
                except (TypeError, ValueError):
                    pass
        if best:
            out[int(year)] = best
    return out


def _published_days(player: dict) -> dict[int, int]:
    out = {}
    for row in player.get("seasons") or []:
        if isinstance(row, dict) and "y" in row:
            out[int(row["y"])] = int(row.get("d") or 0)
    return out


def _hold_key(player_id: int, year: int) -> str:
    return f"{player_id}:{year}"


def load_holds(path: Path = HOLDS_PATH) -> dict[str, dict]:
    if not path.exists():
        return {}
    doc = _load_json(path)
    return {_hold_key(int(row["player_id"]), int(row["season"])): row for row in doc.get("holds") or []}


def _reason(year: int, rulebook_days: int, published_days: int, result: dict, games: int) -> str:
    bits = [
        f"{year} is {published_days - rulebook_days} days below the published season "
        f"({rulebook_days} from the rulebook, {published_days} published)."
    ]
    if games > rulebook_days:
        bits.append(
            f"He played {games} major-league games, more than the rulebook credited, "
            "so a roster row may be missing. The published days stay until that is cleared."
        )
    if any(flag.get("year") == year for flag in result.get("review_flags") or []):
        bits.append("The total depends on the pre-2012 designation window, and that length is unknown.")
    if any(
        row.get("date", "").startswith(str(year))
        for row in result.get("ambiguous") or []
    ):
        bits.append("An unclassified restricted-list row falls in this season, so those days are not credited yet.")
    return " ".join(bits)


def _cached_appearances(player_id: int) -> list[dt.date] | None:
    path = ROOT / "data" / "rulebook" / "appearance_spans.json"
    if not path.exists():
        return None
    raw = (_load_json(path).get(str(player_id)) or {})
    dates = []
    for days in raw.values():
        for day in days:
            dates.append(dt.date.fromisoformat(day))
    return dates or None


def replay_one(player: dict, horizon: dt.date) -> dict:
    player_id = int(player["id"])
    path = RAW / f"{player_id}.json"
    rows = []
    if path.exists():
        rows = _load_json(path).get("transactions") or []
    debut = player.get("mlb_debut")
    last = player.get("last_played")
    return evaluate(
        rows,
        player_id=player_id,
        name=player.get("name"),
        debut=dt.date.fromisoformat(debut) if debut else None,
        on_40_man=bool(player.get("on_40_man")),
        horizon=horizon,
        career_end=dt.date.fromisoformat(last) if last and not player.get("on_40_man") else None,
        appearance_years=set(_games(player)),
        appearance_counts=_games(player),
        appearance_dates=_cached_appearances(player_id),
    )


def _season_row(year: int, days: int, raw: int, teams: list, src: str, previous: dict | None) -> dict:
    row = dict(previous or {})
    row["y"] = year
    row["d"] = days
    row["raw"] = raw
    row["pro"] = raw
    row["t"] = teams or row.get("t") or []
    row["src"] = src
    return row


def apply_player(player: dict, result: dict, prior_holds: dict[str, dict], full_year: int) -> tuple[dict, list[dict], list[dict]]:
    """Return updated fields and the hold rows for this player."""
    published = _published_days(player)
    by_year = {row["year"]: row for row in result["seasons"]}
    previous = {int(row["y"]): row for row in player.get("seasons") or [] if isinstance(row, dict) and "y" in row}
    games = _games(player)
    holds = []
    skipped = []
    seasons = []
    total = 0
    last_game = max(games) if games else None
    for year in sorted(set(published) | set(by_year)):
        rule_days = int(by_year.get(year, {}).get("credited_days") or 0)
        published_days = int(published.get(year, 0))
        if (
            not player.get("on_40_man")
            and last_game is not None
            and year > last_game
            and not (previous.get(year) or {}).get("il")
        ):
            if published_days <= 0:
                skipped.append({
                    "player_id": int(player["id"]),
                    "name": player.get("name"),
                    "season": year,
                    "rulebook_days": rule_days,
                    "reason": "This season is after his last season with a major-league game, and he is not on a roster. It is not published.",
                })
                continue
            rule_days = min(rule_days, published_days)
        raw = int(by_year.get(year, {}).get("raw_days") or rule_days)
        teams = (previous.get(year) or {}).get("t") or []
        key = _hold_key(int(player["id"]), year)
        old = prior_holds.get(key)
        import rulebook_guard
        accepted = bool(old and old.get("cleared")) or rulebook_guard.released(int(player["id"]), year)
        played = games.get(year, 0)
        if accepted and rule_days < published_days:
            days, src = rule_days, "read"
        elif rule_days < published_days:
            days, src = published_days, "held"
            holds.append({
                "player_id": int(player["id"]),
                "name": player.get("name"),
                "season": year,
                "rulebook_days": rule_days,
                "published_days": published_days,
                "reason": _reason(year, rule_days, published_days, result, games.get(year, 0)),
                "cleared": False,
                "on_40_man": bool(player.get("on_40_man")),
            })
        elif rulebook_guard.short_increase(rule_days, published_days, played):
            days, src = published_days, "held"
            holds.append({
                "player_id": int(player["id"]),
                "name": player.get("name"),
                "season": year,
                "rulebook_days": rule_days,
                "published_days": published_days,
                "reason": (
                    f"{year} would add {rule_days - published_days} days in a season of "
                    f"{played} games. The published days stay until that is reviewed."
                ),
                "cleared": False,
                "on_40_man": bool(player.get("on_40_man")),
            })
            if published_days <= 0 and year not in published:
                continue
        else:
            days, src = rule_days, "read"
            raw = int(by_year.get(year, {}).get("raw_days") or days)
        if days == 0 and year not in by_year and year not in published:
            continue
        seasons.append(_season_row(year, days, raw if src == "read" else int((previous.get(year) or {}).get("raw") or days), teams, src, previous.get(year)))
        total += days
    fields = {
        "seasons": seasons,
        "service_days_total": total,
        "service_time": rulebook._format(total, full_year),
        "free_agent_eligible": total >= 6 * full_year,
        "arbitration_eligible": total >= 3 * full_year,
        "rules_version": 10,
    }
    return fields, holds, skipped


def sanity_problems(players: list[dict], results: dict[int, dict], full_year: int) -> list[str]:
    problems = []
    cap = full_year
    for player in players:
        if player.get("rules_version") != 10:
            problems.append(f"{player.get('name')} rules_version is not 10")
        days = [int(row.get("d") or 0) for row in player.get("seasons") or []]
        if any(day < 0 for day in days) or int(player.get("service_days_total") or 0) < 0:
            problems.append(f"{player.get('name')} has a negative total")
        if any(day > cap for day in days):
            problems.append(f"{player.get('name')} has a season over {cap}")
        if sum(days) != int(player.get("service_days_total") or 0):
            problems.append(f"{player.get('name')} season days do not add up")
        result = results.get(int(player["id"]))
        if result:
            for item in result.get("sanity") or []:
                problems.append(f"{player.get('name')}: {item}")
                break
    if len(problems) > 30:
        return problems[:30] + [f"{len(problems) - 30} more"]
    return problems


def golden_problems() -> list[str]:
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "test_golden_players.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-5:]
        return ["hand-checked players failed: " + " ".join(tail)]
    return []


def _lines(old_days: int, new_days: int, full_year: int) -> list[str]:
    def side(days: int) -> float:
        return days / full_year
    old, new = side(old_days), side(new_days)
    crossed = []
    for label, mark in (("3 years", 3), ("6 years", 6)):
        if (old < mark) != (new < mark):
            crossed.append(label)
    cutoff = 2 + 133 / full_year
    old_s2 = 2 <= old < 3 and old >= cutoff
    new_s2 = 2 <= new < 3 and new >= cutoff
    if old_s2 != new_s2:
        crossed.append("Super Two")
    if (old >= 3 or old_s2) != (new >= 3 or new_s2) and "3 years" not in crossed and "Super Two" not in crossed:
        crossed.append("arbitration")
    if (old >= 6) != (new >= 6) and "6 years" not in crossed:
        crossed.append("free agency")
    return crossed


def _fold_name(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    return " ".join(text.lower().split())


def _restricted_index() -> dict[tuple[str, str], dict]:
    found = {}
    if not RESTRICTED.exists():
        return found
    with RESTRICTED.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            for day in (row.get("dates") or "").split(";"):
                day = day.strip()
                if day:
                    found[(_fold_name(row.get("player") or ""), day[:10])] = row
    return found


def _unclassified(row: dict, index: dict[tuple[str, str], dict]) -> dict | None:
    """A current-roster restricted row nobody has classified as counting or not."""
    if not row.get("on_40_man"):
        return None
    match = index.get((_fold_name(row.get("name") or ""), (row.get("date") or "")[:10]))
    counts = ((match or {}).get("counts_service") or "unknown").strip().lower()
    if counts in ("yes", "no"):
        return None
    item = dict(row)
    item["likely_type"] = ((match or {}).get("likely_type") or "unknown").strip() or "unknown"
    return item


def _first_sentence(note: str) -> str:
    text = " ".join(note.split())
    start = 0
    while True:
        cut = text.find(". ", start)
        if cut < 0:
            return text.rstrip(".")
        word = text[:cut].rsplit(" ", 1)[-1]
        if word in {"Art", "Mr", "No"} or len(word) <= 1:
            start = cut + 2
            continue
        return text[:cut]


def _rule_sentence(rule_id: str) -> str:
    if not hasattr(_rule_sentence, "notes"):
        notes = {}
        doc = _load_json(RULES)
        for key in ("rules", "accrual", "dfa_windows"):
            for rule in doc.get(key) or []:
                notes[rule["id"]] = _first_sentence(rule.get("note") or "")
        _rule_sentence.notes = notes
    return _rule_sentence.notes.get(rule_id, "")


def apply_to_database(db: dict[str, dict], published_path: Path = PUBLISHED, holds_path: Path = HOLDS_PATH, snapshot_path: Path = SNAPSHOT) -> None:
    """Mutate db to the figures that should be published. Refuse on a broken check."""
    full_year = int(cba.default().require("service_time.days_per_credited_year"))
    disk_players = {}
    horizon = dt.date(2026, 11, 9)
    if published_path.exists():
        doc = _load_json(published_path)
        disk_players = {int(row["id"]): row for row in doc.get("players") or []}
        raw_day = (doc.get("generated_at") or "")[:10]
        try:
            horizon = dt.date.fromisoformat(raw_day)
        except ValueError:
            pass
    prior = load_holds(holds_path)
    results: dict[int, dict] = {}
    new_holds: list[dict] = []
    skipped_after_last: list[dict] = []
    changes: list[dict] = []
    restricted: list[dict] = []
    flags: list[dict] = []
    named: list[dict] = []
    updated: dict[int, dict] = {}
    restricted_index = _restricted_index()
    for key, record in db.items():
        baseline = disk_players.get(int(record["id"]), record)
        result = replay_one(baseline, horizon)
        results[int(record["id"])] = result
        fields, holds, skipped = apply_player(baseline, result, prior, full_year)
        updated[int(record["id"])] = fields
        new_holds.extend(holds)
        skipped_after_last.extend(skipped)
        if result.get("on_40_man"):
            for row in result.get("ambiguous") or []:
                waiting = _unclassified(row, restricted_index)
                if waiting:
                    restricted.append(waiting)
        watched = NAMED_SEASONS.get(int(record["id"]))
        if watched is not None:
            hold = next((row for row in holds if row["season"] == watched), None)
            published_days = int(_published_days(baseline).get(watched, 0))
            new_season = next((row["d"] for row in fields["seasons"] if row["y"] == watched), 0)
            named.append({
                "player_id": int(record["id"]),
                "name": record.get("name"),
                "season": watched,
                "held": hold is not None,
                "published_days": hold["published_days"] if hold else published_days,
                "new_days": new_season,
                "rulebook_days": hold["rulebook_days"] if hold else new_season,
                "games": _games(baseline).get(watched, 0),
            })
        for flag in result.get("review_flags") or []:
            flags.append({"name": record.get("name"), "year": flag["year"], "player_id": int(record["id"])})
        old_days = int(baseline.get("service_days_total") or 0)
        new_days = fields["service_days_total"]
        if new_days != old_days or fields["service_time"] != baseline.get("service_time"):
            changes.append({
                "player_id": int(record["id"]),
                "name": record.get("name"),
                "on_40_man": bool(record.get("on_40_man")),
                "old_service_time": baseline.get("service_time"),
                "new_service_time": fields["service_time"],
                "old_days": old_days,
                "new_days": new_days,
                "delta_days": new_days - old_days,
                "crossed": _lines(old_days, new_days, full_year),
                "rule_id": _primary_rule_id(baseline, result, fields),
                "reason": _change_reason(baseline, result, fields, full_year),
            })
    # Sanity is on the records we are about to store.
    preview = []
    for record in db.values():
        merged = dict(record)
        merged.update(updated[int(record["id"])])
        preview.append(merged)
    import rulebook_guard
    problems = sanity_problems(preview, results, full_year) + golden_problems() + rulebook_guard.verified_problems(preview)
    if problems:
        raise PublishRefused(problems)
    if published_path.exists():
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_bytes(published_path.read_bytes())
    for record in db.values():
        record.update(updated[int(record["id"])])
    holds_path.parent.mkdir(parents=True, exist_ok=True)
    holds_path.write_text(json.dumps({"holds": new_holds}, indent=2) + "\n", encoding="utf-8")
    import rulebook_guard
    rulebook_guard.write_reports(REPORTS, changes, new_holds, restricted, flags, named, list(NAMED_SEASONS))
    if skipped_after_last:
        (REPORTS / "after_last_season.json").write_text(
            json.dumps(skipped_after_last, indent=2) + "\n", encoding="utf-8"
        )


def _primary_rule_id(baseline: dict, result: dict, fields: dict) -> str:
    old = _published_days(baseline)
    new = {int(row["y"]): int(row["d"]) for row in fields["seasons"]}
    by_year = {row["year"]: row for row in result["seasons"]}
    best_rule = "rulebook"
    best_abs = -1
    for year in set(old) | set(new):
        delta = new.get(year, 0) - old.get(year, 0)
        if abs(delta) <= best_abs:
            continue
        import rulebook_guard
        chosen = rulebook_guard.explain_rule(by_year.get(year) or {}, delta)
        best_rule, best_abs = chosen, abs(delta)
    return best_rule


def _change_reason(baseline: dict, result: dict, fields: dict, full_year: int) -> str:
    old = _published_days(baseline)
    new = {int(row["y"]): int(row["d"]) for row in fields["seasons"]}
    parts = []
    for year in sorted(set(old) | set(new)):
        delta = new.get(year, 0) - old.get(year, 0)
        if not delta:
            continue
        direction = "higher" if delta > 0 else "lower"
        parts.append(f"{year} is {abs(delta)} days {direction}.")
    if not parts:
        return "The total moved."
    moved = abs(fields["service_days_total"] - int(baseline.get("service_days_total") or 0))
    if moved < 25:
        return parts[0]
    biggest = max(
        ((year, new.get(year, 0) - old.get(year, 0)) for year in set(old) | set(new)),
        key=lambda item: abs(item[1]),
    )
    year, delta = biggest
    direction = "higher" if delta > 0 else "lower"
    rule = _primary_rule_id(baseline, result, fields)
    note = _rule_sentence(rule)
    because = f" because {note[0].lower() + note[1:]}" if note else ""
    if len(parts) == 1:
        return f"{year} is {abs(delta)} days {direction}{because} ({rule})."
    return (
        f"{len(parts)} seasons move, and the largest is {year} by {abs(delta)} days {direction}"
        f"{because} ({rule})."
    )


def rollback(published_path: Path = PUBLISHED, snapshot_path: Path = SNAPSHOT) -> None:
    """Put the previous published file back, byte for byte."""
    if not snapshot_path.exists():
        raise PublishRefused([f"No snapshot at {snapshot_path.name}."])
    published_path.write_bytes(snapshot_path.read_bytes())


def publish_or_exit(db) -> None:
    """Apply the rulebook or stop the publish. The morning gate is unchanged."""
    try:
        apply_to_database(db)
    except PublishRefused as refused:
        print("\n!! REFUSING TO PUBLISH THIS RUN:", file=sys.stderr)
        for problem in refused.problems:
            print(f"     - {problem}", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    if "--rollback" in sys.argv:
        rollback()
        print(f"Restored {PUBLISHED.name} from the snapshot.")
        return 0
    import update_service_time as publisher
    db = publisher.load_existing_db()
    try:
        publisher._write_outputs(db)
    except PublishRefused as exc:
        print("Refusing to publish:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
