#!/usr/bin/env python3
"""Major-league game dates for the rulebook replay.

One file per player under data/cache/appearances/. Each file is the
regular-season dates the public stats feed returned, the seasons that
were requested, when the file was written, and a checksum of those
dates. A later fetch that returns the same dates leaves the file alone.

The replay reads this cache. It does not call the feed itself.

    python3 scripts/appearance_cache.py --status
    python3 scripts/appearance_cache.py --fetch
    python3 scripts/appearance_cache.py --fetch --player 676664
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "data" / "cache" / "appearances"
PLAYERS = ROOT / "docs" / "data" / "service_time.json"
# The feed's game log is requested through the current season.
LAST_SEASON = 2026
# One request covers this many seasons. Longer lists time out.
CHUNK = 12


def checksum_of(body: dict) -> str:
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def player_path(player_id: int) -> Path:
    return STORE / f"{int(player_id)}.json"


def _body(seasons: dict[str, list[str]], requested: list[int], groups: list[str]) -> dict:
    return {
        "groups": sorted(groups),
        "seasons_requested": [int(year) for year in sorted(requested)],
        "seasons": {str(year): list(seasons[str(year)]) for year in sorted(seasons, key=lambda item: int(item))},
    }


def integrity_problem(doc: dict | None, player_id: int) -> str | None:
    if not isinstance(doc, dict):
        return "missing"
    if doc.get("player_id") != int(player_id):
        return "player_id"
    seasons = doc.get("seasons")
    requested = doc.get("seasons_requested")
    groups = doc.get("groups")
    if not isinstance(seasons, dict) or not isinstance(requested, list) or not isinstance(groups, list):
        return "shape"
    body = _body(seasons, [int(year) for year in requested], [str(group) for group in groups])
    if doc.get("checksum") != checksum_of(body):
        return "checksum"
    return None


def load_player(player_id: int) -> tuple[list[dt.date], set[int]] | None:
    """(game dates, seasons that were requested). None when there is no good file."""
    path = player_path(player_id)
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if integrity_problem(doc, player_id):
        return None
    dates = []
    for days in (doc.get("seasons") or {}).values():
        for day in days:
            dates.append(dt.date.fromisoformat(day))
    checked = {int(year) for year in doc.get("seasons_requested") or []}
    return dates, checked


def _write(player_id: int, seasons: dict[str, list[str]], requested: list[int], groups: list[str]) -> bool:
    """Write the file. Return False when the dates were already stored."""
    body = _body(seasons, requested, groups)
    digest = checksum_of(body)
    path = player_path(player_id)
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = None
        if isinstance(current, dict) and current.get("checksum") == digest and current.get("player_id") == int(player_id):
            return False
    STORE.mkdir(parents=True, exist_ok=True)
    doc = {
        "player_id": int(player_id),
        "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "checksum": digest,
        **body,
    }
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return True


def _dates_from_payload(data: dict) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for block in data.get("stats") or []:
        for split in block.get("splits") or []:
            if split.get("gameType") not in (None, "R"):
                continue
            sport = split.get("sport") or {}
            if isinstance(sport, dict) and sport.get("id") not in (None, 1):
                continue
            raw = split.get("date")
            if not isinstance(raw, str) or len(raw) < 10:
                continue
            year = str(split.get("season") or raw[:4])
            found.setdefault(year, set()).add(raw[:10])
    return found


def _chunks(years: list[int]) -> list[list[int]]:
    ordered = sorted(set(years))
    return [ordered[index:index + CHUNK] for index in range(0, len(ordered), CHUNK)] or [[]]


_GATE = threading.Lock()
_NEXT = 0.0


def _wait_turn(gap: float) -> None:
    global _NEXT
    with _GATE:
        now = time.monotonic()
        if now < _NEXT:
            time.sleep(_NEXT - now)
        _NEXT = time.monotonic() + gap


def fetch_one(player_id: int, years: list[int], groups: list[str], gap: float) -> dict[str, list[str]]:
    """Game dates for one player. Raises if the feed does not answer."""
    import fetch_mlb_data as mlb
    found: dict[str, set[str]] = {}
    for group in groups:
        for chunk in _chunks(years):
            if not chunk:
                continue
            _wait_turn(gap)
            data = mlb._get(
                f"/people/{int(player_id)}/stats",
                {
                    "stats": "gameLog",
                    "group": group,
                    "sportId": 1,
                    "gameType": "R",
                    "seasons": ",".join(str(year) for year in chunk),
                },
            )
            for year, days in _dates_from_payload(data).items():
                found.setdefault(year, set()).update(days)
    return {year: sorted(days) for year, days in found.items()}


def _player_plan(player: dict) -> tuple[int, list[int], list[str]] | None:
    player_id = int(player["id"])
    stats = player.get("season_stats") or {}
    debut = player.get("mlb_debut") or ""
    start = int(debut[:4]) if isinstance(debut, str) and len(debut) >= 4 else None
    stat_years = []
    groups = []
    for year, by_group in stats.items():
        if not isinstance(by_group, dict):
            continue
        try:
            stat_years.append(int(year))
        except (TypeError, ValueError):
            continue
        for group in ("hitting", "pitching"):
            block = by_group.get(group)
            played = 0
            if isinstance(block, dict):
                try:
                    played = int(block.get("gamesPlayed") or 0)
                except (TypeError, ValueError):
                    played = 0
            if played and group not in groups:
                groups.append(group)
    if not groups:
        groups = ["hitting", "pitching"]
    if start is None and stat_years:
        start = min(stat_years)
    if start is None:
        start = LAST_SEASON - 2
    end = LAST_SEASON
    last = player.get("last_played") or ""
    if isinstance(last, str) and len(last) >= 4 and not player.get("on_40_man"):
        try:
            end = min(LAST_SEASON, max(int(last[:4]), start))
        except ValueError:
            end = LAST_SEASON
    years = list(range(start, end + 1))
    return player_id, years, groups


def _needs_fetch(player_id: int, years: list[int], groups: list[str]) -> bool:
    path = player_path(player_id)
    if not path.exists():
        return True
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    if integrity_problem(doc, player_id):
        return True
    have = {int(year) for year in doc.get("seasons_requested") or []}
    have_groups = set(doc.get("groups") or [])
    return not set(years).issubset(have) or not set(groups).issubset(have_groups)


def _store_fetched(player_id: int, years: list[int], groups: list[str], seasons: dict[str, list[str]]) -> bool:
    # Keep only requested seasons. A stray row from another year is not coverage.
    wanted = {str(year) for year in years}
    kept = {year: days for year, days in seasons.items() if year in wanted}
    return _write(player_id, kept, years, groups)


def fetch_players(plans: list[tuple[int, list[int], list[str]]], workers: int, gap: float) -> tuple[int, int, list[str]]:
    os.environ["MLB_FETCH_PAUSE_SECONDS"] = "0"
    wrote = 0
    failed: list[str] = []
    done = 0

    def work(plan):
        player_id, years, groups = plan
        seasons = fetch_one(player_id, years, groups, gap)
        changed = _store_fetched(player_id, years, groups, seasons)
        return player_id, changed

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(work, plan): plan[0] for plan in plans}
        for future in as_completed(futures):
            player_id = futures[future]
            done += 1
            try:
                _pid, changed = future.result()
            except Exception as exc:
                failed.append(f"{player_id}: {exc}")
            else:
                if changed:
                    wrote += 1
            if done % 200 == 0 or done == len(plans):
                print(f"  {done}/{len(plans)} stored {wrote} failed {len(failed)}", flush=True)
    return wrote, len(plans) - len(failed), failed


def status() -> None:
    files = list(STORE.glob("*.json")) if STORE.exists() else []
    bad = 0
    dates = 0
    for path in files:
        try:
            player_id = int(path.stem)
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            bad += 1
            continue
        if integrity_problem(doc, player_id):
            bad += 1
            continue
        for days in (doc.get("seasons") or {}).values():
            dates += len(days)
    print(f"{len(files)} files, {bad} unreadable, {dates} game dates")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cache major-league game dates")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--player", type=int, action="append", default=[])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--gap", type=float, default=0.05)
    args = parser.parse_args(argv)
    if args.status or not args.fetch:
        status()
        if not args.fetch:
            return 0
    sys.path.insert(0, str(ROOT / "scripts"))
    doc = json.loads(PLAYERS.read_text(encoding="utf-8"))
    plans = []
    wanted = set(args.player)
    for player in doc.get("players") or []:
        plan = _player_plan(player)
        if plan is None:
            continue
        if wanted and plan[0] not in wanted:
            continue
        if _needs_fetch(*plan):
            plans.append(plan)
    print(f"Fetching {len(plans)} players", flush=True)
    _wrote, _ok, failed = fetch_players(plans, args.workers, args.gap)
    if failed:
        print(f"{len(failed)} players failed. Retrying once.", flush=True)
        retry = [plan for plan in plans if plan[0] in {int(row.split(':', 1)[0]) for row in failed}]
        _wrote2, _ok2, failed = fetch_players(retry, max(1, args.workers // 2), args.gap * 2)
    status()
    if failed:
        print("\n".join(failed[:20]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
