#!/usr/bin/env python3
"""Daily diff of the published service-time and roster state.

A compact snapshot (id, service days, roster flag, club id, Super Two
flag) is diffed against the database just written. Roster moves are
classified from the transaction cache; the sentence is never stored.
A rules-version change is one up/down count, not a player list. Empty
days are omitted. The log keeps 60 days. With no snapshot yet, the
newest day of moves already in the cache is the first entry. Failures
are reported and do not propagate.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import traceback
from pathlib import Path

import cba
import career_moves
from il_stints import _IL_ACT_RE, _PLACE_RE, _TRANSFER_RE
from mlb_clubs import MLB_CLUB_IDS, club_name, name_for_record

ROOT = Path(__file__).resolve().parents[1]
WINDOW_DAYS = 60
YEAR_MARKS = (1, 2, 3, 4, 5, 6)

# career_moves kinds that are roster news on this page. Waiver claims,
# free-agency elections, and retirements are not in this list.
_KIND_FROM_CAREER = {
    "recall": "call-up",
    "selected": "call-up",
    "selected_offseason": "call-up",
    "option": "option",
    "dfa": "dfa",
    "outright": "outright",
    "release": "release",
    "signed": "signing",
    "signed_minor": "signing",
    "trade": "trade",
}

_OFFSEASON_IL_MONTHS = (11, 12, 1)


def days_per_year() -> int:
    """Credited days in a full year, from the agreement, not a literal."""
    return int(cba.default().require("service_time.days_per_credited_year"))


def _pid(record: dict, key) -> int | None:
    raw = record.get("id") if isinstance(record, dict) and record.get("id") is not None else key
    if isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def rules_version(db: dict) -> int | None:
    """The version stamped on the most players. None when nobody has one."""
    counts: dict[int, int] = {}
    for record in db.values():
        if not isinstance(record, dict):
            continue
        value = record.get("rules_version")
        number = None
        if isinstance(value, int) and not isinstance(value, bool):
            number = value
        elif isinstance(value, str) and value.isdigit():
            number = int(value)
        if number is not None:
            counts[number] = counts.get(number, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def _club(row: dict, key: str) -> int | None:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value not in MLB_CLUB_IDS:
        return None
    return value


def _row_date(row: dict) -> dt.date | None:
    raw = row.get("date")
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return dt.date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _names_mlb_club(row: dict) -> bool:
    return any(_club(row, key) for key in ("from_team_id", "to_team_id", "team_id"))


def is_il_move(row: dict) -> bool:
    """An injured-list placement, transfer, or return that names a major league club.

    November through January placements are offseason bookkeeping, same as
    the injured-list table. A return from the list still counts.
    """
    desc = row.get("description") or ""
    if not isinstance(desc, str) or not desc or not _names_mlb_club(row):
        return False
    day = _row_date(row)
    if day is None:
        return False
    if _TRANSFER_RE.search(desc):
        return True
    if _PLACE_RE.search(desc):
        return day.month not in _OFFSEASON_IL_MONTHS
    return _IL_ACT_RE.search(desc) is not None


def classify_move(row: dict) -> str | None:
    """The change-log kind, or None when the row is not one of the listed moves."""
    if not isinstance(row, dict):
        return None
    if is_il_move(row):
        return "il"
    kind = career_moves.classify_move(row)
    if kind is None:
        return None
    return _KIND_FROM_CAREER.get(kind)


def team_label(kind: str, row: dict) -> str:
    """Club text for one move. A trade names both clubs when both are known."""
    src = _club(row, "from_team_id")
    dst = _club(row, "to_team_id")
    club = _club(row, "team_id")
    if kind == "trade" and src and dst and src != dst:
        return f"{club_name(src)} to {club_name(dst)}"
    if kind in {"option", "dfa", "outright", "release"}:
        chosen = src or club or dst
    else:
        chosen = dst or club or src
    if chosen is None:
        return ""
    return club_name(chosen)


def snapshot_from_db(db: dict, today: dt.date) -> dict:
    """Compact rows. No names, seasons, or transaction text."""
    rows = []
    for key, record in db.items():
        if not isinstance(record, dict):
            continue
        pid = _pid(record, key)
        if pid is None:
            continue
        try:
            days = int(record.get("service_days_total") or 0)
        except (TypeError, ValueError):
            days = 0
        roster = 1 if record.get("on_40_man") else 0
        team = record.get("team_id")
        try:
            team_id = int(team) if team is not None and not isinstance(team, bool) else 0
        except (TypeError, ValueError):
            team_id = 0
        super_two = 1 if record.get("super_two_candidate") or record.get("super_two_bubble") else 0
        rows.append([pid, days, roster, team_id, super_two])
    rows.sort()
    return {
        "as_of": today.isoformat(),
        "rules_version": rules_version(db),
        "players": rows,
    }


def indexes(db: dict) -> tuple[dict[int, str], dict[int, str]]:
    names: dict[int, str] = {}
    teams: dict[int, str] = {}
    for key, record in db.items():
        if not isinstance(record, dict):
            continue
        pid = _pid(record, key)
        if pid is None:
            continue
        name = " ".join(str(record.get("name") or "").split())
        if name:
            names[pid] = name
        teams[pid] = name_for_record(record.get("team_id"), record.get("team") or "")
    return names, teams


def _index_rows(snapshot: dict | None) -> dict[int, tuple[int, int, int, int]]:
    found: dict[int, tuple[int, int, int, int]] = {}
    if not snapshot:
        return found
    for row in snapshot.get("players") or []:
        if not isinstance(row, list) or len(row) < 5:
            continue
        try:
            pid, days, roster, team, flag = (int(row[0]), int(row[1]), int(row[2]), int(row[3]), int(row[4]))
        except (TypeError, ValueError):
            continue
        found[pid] = (days, roster, team, flag)
    return found


def _person(pid: int, names: dict[int, str], teams: dict[int, str], extra: dict) -> dict | None:
    name = names.get(pid)
    if not name:
        return None
    item = {"id": pid, "name": name}
    team = teams.get(pid) or ""
    if team:
        item["team"] = team
    item.update(extra)
    return item


def service_changes(
    previous: dict | None,
    current: dict,
    *,
    per_year: int,
    names: dict[int, str],
    teams: dict[int, str],
) -> dict:
    """Threshold crossings, Super Two entries, and a rule-change summary.

    No previous snapshot means there is nothing to compare. A rules-version
    change replaces the player lists with up/down counts.
    """
    empty = {"thresholds": [], "super_two": [], "corrections": None}
    if not previous or per_year <= 0:
        return empty
    prev_rows = _index_rows(previous)
    curr_rows = _index_rows(current)
    prev_version = previous.get("rules_version")
    curr_version = current.get("rules_version")
    rules_changed = (
        isinstance(prev_version, int)
        and not isinstance(prev_version, bool)
        and isinstance(curr_version, int)
        and not isinstance(curr_version, bool)
        and prev_version != curr_version
    )
    if rules_changed:
        up = down = 0
        for pid, (days, _roster, _team, _flag) in curr_rows.items():
            old = prev_rows.get(pid)
            if old is None:
                continue
            if days > old[0]:
                up += 1
            elif days < old[0]:
                down += 1
        corrections = {"up": up, "down": down} if up or down else None
        return {"thresholds": [], "super_two": [], "corrections": corrections}

    thresholds = []
    super_two = []
    for pid, (days, _roster, _team, flag) in curr_rows.items():
        old = prev_rows.get(pid)
        if old is None:
            continue
        old_years = old[0] // per_year
        new_years = days // per_year
        for mark in YEAR_MARKS:
            if old_years < mark <= new_years:
                item = _person(pid, names, teams, {"years": mark})
                if item:
                    thresholds.append(item)
        if not old[3] and flag:
            item = _person(pid, names, teams, {})
            if item:
                super_two.append(item)
    return {"thresholds": thresholds, "super_two": super_two, "corrections": None}


def iter_cache(cache_dir: Path):
    """(player id, row) from the transaction cache. A bad file is skipped."""
    if not cache_dir.is_dir():
        return
    for path in cache_dir.glob("*.json"):
        try:
            pid = int(path.stem)
        except ValueError:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = payload if isinstance(payload, list) else []
        if isinstance(payload, dict) and isinstance(payload.get("transactions"), list):
            rows = payload["transactions"]
        for row in rows:
            if isinstance(row, dict):
                yield pid, row


def collect_moves(
    cache_dir: Path,
    *,
    start: dt.date | None,
    end: dt.date,
    names: dict[int, str],
) -> dict[str, list[dict]]:
    """Moves by ISO date.

    `start` is inclusive. None keeps only the newest day that has a move
    on or before `end` — the opening entry, when no snapshot exists yet.
    """
    grouped: dict[str, list[dict]] = {}
    newest = ""
    for pid, row in iter_cache(cache_dir):
        kind = classify_move(row)
        day = _row_date(row)
        name = names.get(pid)
        if kind is None or day is None or not name or day > end:
            continue
        if start is not None and day < start:
            continue
        iso = day.isoformat()
        item = {"id": pid, "name": name, "kind": kind}
        team = team_label(kind, row)
        if team:
            item["team"] = team
        if start is None:
            if iso > newest:
                newest = iso
                grouped = {iso: [item]}
            elif iso == newest:
                grouped.setdefault(iso, []).append(item)
            continue
        grouped.setdefault(iso, []).append(item)
    for iso, items in grouped.items():
        grouped[iso] = _union([], items, _move_key)
    return grouped


def _move_key(item: dict) -> tuple:
    return (item.get("id"), item.get("kind"), item.get("team") or "")


def _threshold_key(item: dict) -> tuple:
    return (item.get("id"), item.get("years"))


def _super_key(item: dict) -> tuple:
    return (item.get("id"),)


def _union(old, new, key) -> list[dict]:
    merged = []
    seen = set()
    for item in list(old or []) + list(new or []):
        marker = key(item)
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(item)
    merged.sort(key=lambda item: (
        (item.get("name") or "").casefold(),
        item.get("id") or 0,
        item.get("years") or 0,
        item.get("kind") or "",
    ))
    return merged


def strip_entry(entry: dict) -> dict | None:
    """Drop empty groups. None when the day has nothing to show."""
    out = {"date": entry["date"]}
    for field in ("thresholds", "super_two", "moves"):
        items = entry.get(field) or []
        if items:
            out[field] = items
    corrections = entry.get("corrections")
    if isinstance(corrections, dict) and (corrections.get("up") or corrections.get("down")):
        out["corrections"] = {
            "up": int(corrections.get("up") or 0),
            "down": int(corrections.get("down") or 0),
        }
    if len(out) == 1:
        return None
    return out


def combine(old: dict | None, new: dict) -> dict | None:
    merged = {
        "date": new["date"],
        "thresholds": _union(
            (old or {}).get("thresholds"), new.get("thresholds"), _threshold_key
        ),
        "super_two": _union(
            (old or {}).get("super_two"), new.get("super_two"), _super_key
        ),
        "moves": _union((old or {}).get("moves"), new.get("moves"), _move_key),
    }
    if new.get("corrections"):
        merged["corrections"] = new["corrections"]
    elif old and old.get("corrections"):
        merged["corrections"] = old["corrections"]
    return strip_entry(merged)


def trim(days: list[dict], today: dt.date, window: int = WINDOW_DAYS) -> list[dict]:
    cutoff = today - dt.timedelta(days=window)
    kept = []
    for entry in days:
        try:
            day = dt.date.fromisoformat(entry["date"])
        except (KeyError, TypeError, ValueError):
            continue
        if day >= cutoff:
            kept.append(entry)
    kept.sort(key=lambda entry: entry["date"], reverse=True)
    return kept


def load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def build_log(
    previous: dict | None,
    current: dict,
    moves_by_day: dict[str, list[dict]],
    *,
    today: dt.date,
    per_year: int,
    names: dict[int, str],
    teams: dict[int, str],
    existing: list[dict] | None = None,
) -> list[dict]:
    """Merge today's diff into the stored days. Empty days are not added."""
    by_date = {}
    for entry in existing or []:
        if isinstance(entry, dict) and isinstance(entry.get("date"), str):
            by_date[entry["date"]] = entry

    service = service_changes(
        previous, current, per_year=per_year, names=names, teams=teams
    )
    dates = set(moves_by_day)
    if service["thresholds"] or service["super_two"] or service["corrections"]:
        dates.add(today.isoformat())
    for iso in dates:
        fresh = {
            "date": iso,
            "moves": moves_by_day.get(iso) or [],
            "thresholds": [],
            "super_two": [],
            "corrections": None,
        }
        if iso == today.isoformat():
            fresh["thresholds"] = service["thresholds"]
            fresh["super_two"] = service["super_two"]
            fresh["corrections"] = service["corrections"]
        combined = combine(by_date.get(iso), fresh)
        if combined:
            by_date[iso] = combined
    return trim(list(by_date.values()), today)


def _write_json(path: Path, payload: dict, *, compact: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(payload, separators=(",", ":")) + "\n"
    else:
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def publish(
    db: dict,
    *,
    today: dt.date | None = None,
    root: Path | None = None,
    cache_dir: Path | None = None,
    per_year: int | None = None,
) -> dict:
    """Diff, append, write the snapshot, and rebuild the changes page.

    Raises on a real failure. Call publish_safely from the daily job.
    """
    if not isinstance(db, dict):
        raise TypeError("the published database must be a dict of players")
    today = today or dt.date.today()
    root = root or ROOT
    cache_dir = cache_dir or (root / "data" / "cache" / "transactions")
    per_year = days_per_year() if per_year is None else per_year
    snapshot_path = root / "data" / "changes_snapshot.json"
    log_path = root / "data" / "changes.json"
    docs = root / "docs"

    current = snapshot_from_db(db, today)
    previous = load_json(snapshot_path)
    if previous is not None and not isinstance(previous.get("players"), list):
        previous = None
    names, teams = indexes(db)
    if previous is None:
        moves = collect_moves(cache_dir, start=None, end=today, names=names)
    else:
        try:
            start = dt.date.fromisoformat(str(previous.get("as_of")))
        except ValueError:
            start = today
        if start > today:
            start = today
        # Same calendar day: reread that day so a second publish can add a
        # move, without pulling the previous day in again.
        if start == today:
            window_start = today
        else:
            window_start = start + dt.timedelta(days=1)
        moves = collect_moves(cache_dir, start=window_start, end=today, names=names)

    stored = load_json(log_path) or {}
    existing = stored.get("days") if isinstance(stored.get("days"), list) else []
    days = build_log(
        previous,
        current,
        moves,
        today=today,
        per_year=per_year,
        names=names,
        teams=teams,
        existing=existing,
    )
    log = {"days": days}

    import build_changes_page as page

    html = page.render(log, docs)
    bad = page.problems(html, docs, log)
    if bad:
        raise RuntimeError("changes page failed checks: " + "; ".join(bad))

    _write_json(log_path, log, compact=False)
    page.write_text(docs / "changes.html", html)
    _write_json(snapshot_path, current, compact=True)
    print(
        f"Change log: {len(days)} day(s) kept, snapshot {today.isoformat()}, "
        f"{len(current['players'])} players."
    )
    return log


def publish_safely(db: dict, **kwargs) -> bool:
    """Run publish. A failure is printed and does not propagate."""
    try:
        publish(db, **kwargs)
    except Exception as exc:
        print(f"Change log was not updated: {exc}", file=sys.stderr)
        traceback.print_exc()
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    """Rebuild the log from the database already on disk. No API calls."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="diff the stored database and rewrite the change log and page",
    )
    args = parser.parse_args(argv)
    if not args.write:
        parser.print_help()
        return 2
    import update_service_time as job

    db = job.load_existing_db()
    ok = publish_safely(db)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
