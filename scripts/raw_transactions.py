#!/usr/bin/env python3
"""Raw transaction evidence, one file per published player.

The service-time, option, and injured-list figures are still computed from
data/cache/transactions (rostered players) and from the compact alumni
files. This directory is a separate copy of the feed itself: each file is
the transaction rows the public API returned for that player, plus when
the file was written and a checksum of those rows. Nothing here changes a
published figure. The files live under data/, which is not the folder
GitHub Pages serves.

A full pull writes a player's rows from scratch. A later full pull does
the same: the new list replaces the old one. Rows are never merged on
date plus description, which is how the roster cache misses a correction
to a day it already stored.

The daily update asks for the whole feed over the last few days, then
replaces that date range inside each affected player's file. The weekly
update fetches every player again and replaces each file.

    python scripts/raw_transactions.py --status
    python scripts/raw_transactions.py --full          # resumable gap fill
    python scripts/raw_transactions.py --window        # recent days
    python scripts/raw_transactions.py --refresh       # full re-pull
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import fetch_mlb_data as mlb  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
STORE_DIR = ROOT / "data" / "raw_transactions"
PLAYERS_FILE = ROOT / "docs" / "data" / "service_time.json"

# Same floor the roster cache uses. One request from this date through
# today matched a year-by-year pull for the longest history in that cache.
EARLIEST = dt.date(2005, 1, 1)

# Recent days, including today. Long enough that a row posted a day or two
# late still lands; short enough that it is one small request per day.
DEFAULT_WINDOW_DAYS = 3

# A weekly re-pull that stopped early can be continued the same weekend.
# After that, the next --refresh starts a new pass over everyone.
REFRESH_RESUME_DAYS = 2

STATE_NAME = "_state.json"


def utc_now(clock: dt.datetime | None = None) -> dt.datetime:
    if clock is not None:
        if clock.tzinfo is None:
            return clock.replace(tzinfo=dt.timezone.utc)
        return clock.astimezone(dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc)


def format_fetched_at(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_payload(transactions: list) -> str:
    """Stable text of the row list. Key order and row order do not matter."""
    parts = [
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for row in transactions
    ]
    parts.sort()
    return "[" + ",".join(parts) + "]"


def checksum_of(transactions: list) -> str:
    digest = hashlib.sha256(canonical_payload(transactions).encode("utf-8")).hexdigest()
    return "sha256:" + digest


def _iso_date(value) -> dt.date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _team_id(row: dict, key: str) -> int | None:
    obj = row.get(key)
    if not isinstance(obj, dict):
        return None
    value = obj.get("id")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def row_is_incomplete(row) -> bool:
    """A row that cannot be used as evidence of a roster move.

    The public feed's player rows carry a date, a type code, a description,
    a transaction id, and at least one team id (on team, fromTeam, or
    toTeam). A file that is missing any of those was not stored from a
    complete response — the case where a saved row has no team id at all.
    """
    if not isinstance(row, dict):
        return True
    if _iso_date(row.get("date")) is None:
        return True
    type_code = row.get("typeCode")
    if not isinstance(type_code, str) or not type_code.strip():
        return True
    description = row.get("description")
    if not isinstance(description, str) or not description.strip():
        return True
    txn_id = row.get("id")
    if isinstance(txn_id, bool) or not isinstance(txn_id, int):
        return True
    return all(_team_id(row, key) is None for key in ("team", "fromTeam", "toTeam"))


def player_path(store: pathlib.Path, player_id: int) -> pathlib.Path:
    return store / f"{int(player_id)}.json"


def state_path(store: pathlib.Path) -> pathlib.Path:
    return store / STATE_NAME


def load_state(store: pathlib.Path) -> dict:
    path = state_path(store)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(store: pathlib.Path, state: dict) -> None:
    store.mkdir(parents=True, exist_ok=True)
    _write_json(state_path(store), state)


def _write_json(path: pathlib.Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_player(store: pathlib.Path, player_id: int) -> dict | None:
    path = player_path(store, player_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def integrity_problem(doc: dict | None, player_id: int) -> str | None:
    """None when the file is this player's rows and the checksum matches."""
    if not isinstance(doc, dict):
        return "missing"
    if doc.get("player_id") != int(player_id):
        return "player_id"
    rows = doc.get("transactions")
    if not isinstance(rows, list):
        return "transactions"
    if doc.get("checksum") != checksum_of(rows):
        return "checksum"
    fetched = doc.get("fetched_at")
    if not isinstance(fetched, str) or _iso_date(fetched) is None:
        return "fetched_at"
    return None


def has_incomplete_row(doc: dict | None) -> bool:
    if not isinstance(doc, dict):
        return False
    rows = doc.get("transactions")
    if not isinstance(rows, list):
        return False
    return any(row_is_incomplete(row) for row in rows)


def fetched_on(doc: dict | None, day: dt.date) -> bool:
    if not isinstance(doc, dict):
        return False
    return _iso_date(doc.get("fetched_at")) == day


def published_player_ids(players_file: pathlib.Path) -> list[int]:
    """Ids in the published player database. That file is the site's record
    of who is tracked; this store follows it and does not add anyone else."""
    data = json.loads(players_file.read_text(encoding="utf-8"))
    players = data.get("players") if isinstance(data, dict) else None
    if not isinstance(players, list):
        raise RuntimeError(f"{players_file} has no players list")
    ids: list[int] = []
    seen: set[int] = set()
    for player in players:
        if not isinstance(player, dict):
            continue
        pid = player.get("id")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid in seen:
            continue
        seen.add(pid)
        ids.append(pid)
    ids.sort()
    return ids


def replace_player(
    store: pathlib.Path,
    player_id: int,
    transactions: list,
    *,
    fetched_at: str,
) -> dict:
    """Write this player's file from the given rows. The previous list is
    not read and not merged."""
    if not isinstance(transactions, list):
        raise TypeError("transactions must be a list")
    payload = {
        "player_id": int(player_id),
        "fetched_at": fetched_at,
        "checksum": checksum_of(transactions),
        "transactions": transactions,
    }
    _write_json(player_path(store, player_id), payload)
    return payload


def replace_date_range(
    existing: list,
    incoming: list,
    start: dt.date,
    end: dt.date,
) -> list:
    """Drop stored rows inside the window and append the window's rows.

    Matching is the date range, not date plus description. A corrected
    sentence replaces the old row; a row the feed no longer lists for
    those days is dropped. Rows outside the window stay as they were.
    """
    kept = []
    for row in existing:
        day = _iso_date(row.get("date")) if isinstance(row, dict) else None
        if day is not None and start <= day <= end:
            continue
        kept.append(row)
    for row in incoming:
        day = _iso_date(row.get("date")) if isinstance(row, dict) else None
        if day is not None and start <= day <= end:
            kept.append(row)
    return kept


def fetch_player_transactions(
    player_id: int,
    start: dt.date,
    end: dt.date,
) -> list:
    rows = mlb.get_player_transactions(player_id, start, end)
    if not isinstance(rows, list):
        raise RuntimeError(f"transactions for {player_id} were not a list")
    return rows


def fetch_league_transactions(start: dt.date, end: dt.date) -> list:
    rows = mlb.get_league_transactions(start, end)
    if not isinstance(rows, list):
        raise RuntimeError("league transactions were not a list")
    return rows


def _person_id(row: dict) -> int | None:
    person = row.get("person")
    if not isinstance(person, dict):
        return None
    pid = person.get("id")
    if isinstance(pid, bool) or not isinstance(pid, int):
        return None
    return pid


def group_by_player(rows: list) -> dict[int, list]:
    grouped: dict[int, list] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = _person_id(row)
        if pid is None:
            continue
        grouped.setdefault(pid, []).append(row)
    return grouped


def window_bounds(today: dt.date, days: int) -> tuple[dt.date, dt.date]:
    span = max(1, int(days))
    return today - dt.timedelta(days=span - 1), today


def _refresh_block(state: dict) -> dict:
    block = state.get("refresh")
    return block if isinstance(block, dict) else {}


def refresh_is_resumable(state: dict, now: dt.datetime) -> bool:
    block = _refresh_block(state)
    if not block.get("in_progress"):
        return False
    started = _iso_date(block.get("started_at"))
    if started is None:
        return False
    age = now.date() - started
    return dt.timedelta(0) <= age <= dt.timedelta(days=REFRESH_RESUME_DAYS)


def _begin_refresh(state: dict, now: dt.datetime) -> dict:
    if refresh_is_resumable(state, now):
        return state
    state = dict(state)
    state["refresh"] = {
        "in_progress": True,
        "started_at": format_fetched_at(now),
        "done_ids": [],
    }
    return state


def _done_ids(state: dict) -> set[int]:
    raw = _refresh_block(state).get("done_ids")
    if not isinstance(raw, list):
        return set()
    return {int(pid) for pid in raw if isinstance(pid, int) and not isinstance(pid, bool)}


def _mark_done(state: dict, player_id: int) -> None:
    block = state.setdefault("refresh", {})
    done = block.get("done_ids")
    if not isinstance(done, list):
        done = []
        block["done_ids"] = done
    if player_id not in done:
        done.append(int(player_id))


def _record_failure(state: dict, player_id: int, exc: Exception) -> None:
    failed = state.get("failed")
    if not isinstance(failed, dict):
        failed = {}
        state["failed"] = failed
    failed[str(int(player_id))] = str(exc)[:300]


def _clear_failure(state: dict, player_id: int) -> None:
    failed = state.get("failed")
    if isinstance(failed, dict):
        failed.pop(str(int(player_id)), None)


def _store_fetched(
    store: pathlib.Path,
    player_id: int,
    rows: list,
    fetched_at: str,
) -> bool:
    """Replace the file when the payload changed. Returns True if written."""
    fresh = checksum_of(rows)
    current = load_player(store, player_id)
    if (
        integrity_problem(current, player_id) is None
        and current.get("checksum") == fresh
        and not has_incomplete_row(current)
    ):
        return False
    # An identical incomplete payload fetched again still gets a new
    # fetched_at, so the same broken response is not requested in a loop
    # for the rest of the day.
    if (
        integrity_problem(current, player_id) is None
        and current.get("checksum") == fresh
        and fetched_on(current, _iso_date(fetched_at) or dt.date.min)
    ):
        return False
    replace_player(store, player_id, rows, fetched_at=fetched_at)
    return True


def _fetch_and_store(
    store: pathlib.Path,
    player_id: int,
    *,
    today: dt.date,
    fetched_at: str,
    state: dict,
) -> bool:
    try:
        rows = fetch_player_transactions(player_id, EARLIEST, today)
    except Exception as exc:  # network, or a payload that is not a list
        _record_failure(state, player_id, exc)
        print(f"failed {player_id}: {exc}", flush=True)
        return False
    wrote = _store_fetched(store, player_id, rows, fetched_at)
    _clear_failure(state, player_id)
    print(
        f"fetched {player_id} rows={len(rows)} wrote={str(wrote).lower()}",
        flush=True,
    )
    return True


def _needs_gap_fill(store: pathlib.Path, player_id: int, today: dt.date) -> bool:
    """Missing, unreadable, or incomplete and not already refetched today."""
    doc = load_player(store, player_id)
    if integrity_problem(doc, player_id) is not None:
        return True
    if has_incomplete_row(doc) and not fetched_on(doc, today):
        return True
    return False


def _needs_repair(store: pathlib.Path, player_id: int, today: dt.date) -> bool:
    """A file that is on disk but not trustworthy.

    A player with no file yet is not repaired here. The full pull fills
    those in; the daily window only fetches a missing player when that
    player actually appears in the recent feed.
    """
    path = player_path(store, player_id)
    if not path.exists():
        return False
    return _needs_gap_fill(store, player_id, today)


def run_full(
    store: pathlib.Path,
    player_ids: list[int],
    *,
    today: dt.date,
    now: dt.datetime,
    limit: int = 0,
) -> dict:
    """Fetch every published player who has no good file yet.

    A file already on disk with a matching checksum is left alone, so a
    stopped run continues at the next missing player. A file with an
    incomplete row is fetched again, unless that fetch already happened
    today.
    """
    state = load_state(store)
    fetched_at = format_fetched_at(now)
    budget = None if limit <= 0 else limit
    used = 0
    attempts = 0
    failures = 0
    for player_id in player_ids:
        if budget is not None and used >= budget:
            break
        if not _needs_gap_fill(store, player_id, today):
            continue
        used += 1
        attempts += 1
        ok = _fetch_and_store(
            store, player_id, today=today, fetched_at=fetched_at, state=state
        )
        if not ok:
            failures += 1
        if used % 25 == 0:
            save_state(store, state)
    save_state(store, state)
    summary = summarize(store, player_ids, today=today)
    summary["fetch_attempts"] = attempts
    summary["fetch_failures"] = failures
    return summary


def _refresh_completed_on(state: dict, day: dt.date) -> bool:
    block = _refresh_block(state)
    if block.get("in_progress"):
        return False
    return _iso_date(block.get("completed_at")) == day


def run_refresh(
    store: pathlib.Path,
    player_ids: list[int],
    *,
    today: dt.date,
    now: dt.datetime,
    limit: int = 0,
) -> dict:
    """Replace every published player's rows. Resumes a pass started recently.

    A pass that already finished today is left alone. The next day starts
    over, which is what the weekly run wants.
    """
    state = load_state(store)
    if _refresh_completed_on(state, today):
        print("refresh already completed today", flush=True)
        summary = summarize(store, player_ids, today=today)
        summary["fetch_attempts"] = 0
        summary["fetch_failures"] = 0
        return summary
    state = _begin_refresh(state, now)
    save_state(store, state)
    done = _done_ids(state)
    fetched_at = format_fetched_at(now)
    budget = None if limit <= 0 else limit
    used = 0
    attempts = 0
    failures = 0
    for player_id in player_ids:
        if player_id in done:
            continue
        if budget is not None and used >= budget:
            break
        used += 1
        attempts += 1
        ok = _fetch_and_store(
            store, player_id, today=today, fetched_at=fetched_at, state=state
        )
        if ok:
            _mark_done(state, player_id)
        else:
            failures += 1
        if used % 25 == 0:
            save_state(store, state)
    done = _done_ids(state)
    if all(pid in done for pid in player_ids):
        block = _refresh_block(state)
        block["in_progress"] = False
        block["completed_at"] = format_fetched_at(now)
        state["refresh"] = block
    save_state(store, state)
    summary = summarize(store, player_ids, today=today)
    summary["fetch_attempts"] = attempts
    summary["fetch_failures"] = failures
    return summary


def _league_rows_for_window(start: dt.date, end: dt.date) -> list:
    """One request for the whole lookback. A busy few days is still a
    single response, and the daily job is supposed to ask once."""
    return list(fetch_league_transactions(start, end))


def run_window(
    store: pathlib.Path,
    player_ids: list[int],
    *,
    today: dt.date,
    now: dt.datetime,
    days: int = DEFAULT_WINDOW_DAYS,
    limit: int = 0,
) -> dict:
    """Refetch incomplete files, then replace the recent date range for
    every published player who appears in the league-wide feed."""
    state = load_state(store)
    fetched_at = format_fetched_at(now)
    published = set(player_ids)
    budget = None if limit <= 0 else limit
    used = 0
    attempts = 0
    failures = 0

    def allow() -> bool:
        return budget is None or used < budget

    def spend(player_id: int) -> bool:
        nonlocal used, attempts, failures
        if not allow():
            return False
        used += 1
        attempts += 1
        ok = _fetch_and_store(
            store, player_id, today=today, fetched_at=fetched_at, state=state
        )
        if not ok:
            failures += 1
        return ok

    for player_id in player_ids:
        if not allow():
            break
        if _needs_repair(store, player_id, today):
            spend(player_id)

    def finish(aborted: bool = False) -> dict:
        save_state(store, state)
        summary = summarize(store, player_ids, today=today)
        summary["fetch_attempts"] = attempts
        summary["fetch_failures"] = failures
        summary["aborted"] = aborted
        return summary

    if state.get("last_window_utc_date") == today.isoformat():
        print(f"window {today.isoformat()} already applied", flush=True)
        return finish()

    start, end = window_bounds(today, days)
    try:
        league_rows = _league_rows_for_window(start, end)
    except Exception as exc:
        print(f"window fetch failed: {exc}", flush=True)
        return finish(aborted=True)
    grouped = group_by_player(league_rows)
    outside = [pid for pid in grouped if pid not in published]
    print(
        f"window {start.isoformat()}..{end.isoformat()} "
        f"rows={len(league_rows)} players={len(grouped)} "
        f"not_published={len(outside)}",
        flush=True,
    )

    for player_id, incoming in sorted(grouped.items()):
        if player_id not in published:
            continue
        doc = load_player(store, player_id)
        incoming_ok = all(not row_is_incomplete(row) for row in incoming)
        stored_ok = (
            integrity_problem(doc, player_id) is None
            and not has_incomplete_row(doc)
        )
        # A good file plus a good window: replace just those days. Anything
        # else needs the player's whole history, and a file already fetched
        # today is left alone so a broken response is not requested again
        # until tomorrow.
        if stored_ok and incoming_ok:
            updated = replace_date_range(
                doc["transactions"], incoming, start, end
            )
        elif fetched_on(doc, today) and integrity_problem(doc, player_id) is None:
            continue
        elif allow():
            spend(player_id)
            continue
        else:
            continue
        if checksum_of(updated) == doc.get("checksum"):
            continue
        replace_player(store, player_id, updated, fetched_at=fetched_at)
        print(
            f"window-updated {player_id} rows={len(updated)}",
            flush=True,
        )

    state["last_window_utc_date"] = today.isoformat()
    return finish()


def store_bytes(store: pathlib.Path) -> int:
    if not store.exists():
        return 0
    total = 0
    for path in store.iterdir():
        if path.is_file():
            total += path.stat().st_size
    return total


def summarize(
    store: pathlib.Path,
    player_ids: list[int],
    *,
    today: dt.date,
) -> dict:
    stored = 0
    incomplete = 0
    for player_id in player_ids:
        doc = load_player(store, player_id)
        if integrity_problem(doc, player_id) is not None:
            continue
        stored += 1
        if has_incomplete_row(doc):
            incomplete += 1
    state = load_state(store)
    in_progress = refresh_is_resumable(state, dt.datetime.combine(today, dt.time(), dt.timezone.utc))
    # resumable check uses the state's started_at against `today`, which is
    # what the commands pass in. A clock from tests is that same day.
    done = _done_ids(state) if in_progress else set()
    refresh_remaining = (
        sum(1 for pid in player_ids if pid not in done) if in_progress else 0
    )
    return {
        "stored": stored,
        "remaining": len(player_ids) - stored,
        "incomplete": incomplete,
        "bytes": store_bytes(store),
        "refresh_in_progress": in_progress,
        "refresh_remaining": refresh_remaining,
        "published": len(player_ids),
        "today": today.isoformat(),
    }


def format_summary(summary: dict) -> str:
    flag = "true" if summary["refresh_in_progress"] else "false"
    return (
        f"stored: {summary['stored']}\n"
        f"remaining: {summary['remaining']}\n"
        f"incomplete: {summary['incomplete']}\n"
        f"bytes: {summary['bytes']}\n"
        f"refresh_in_progress: {flag}\n"
        f"refresh_remaining: {summary['refresh_remaining']}\n"
        f"published: {summary['published']}\n"
    )


def _parse_today(text: str | None) -> dt.date:
    if not text:
        return dt.datetime.now(dt.timezone.utc).date()
    return dt.date.fromisoformat(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Raw MLB transaction evidence store")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--full", action="store_true", help="fetch players who have no good file yet")
    mode.add_argument("--refresh", action="store_true", help="re-pull every player, replacing each file")
    mode.add_argument("--window", action="store_true", help="update players who moved in the recent window")
    mode.add_argument("--status", action="store_true", help="print counts and the size of the store")
    parser.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument("--limit", type=int, default=0, help="max player fetches this run (0 = no cap)")
    parser.add_argument("--data-dir", type=pathlib.Path, default=STORE_DIR)
    parser.add_argument("--players-file", type=pathlib.Path, default=PLAYERS_FILE)
    parser.add_argument("--today", default=None, help="YYYY-MM-DD, UTC, for a fixed run date")
    args = parser.parse_args(argv)

    today = _parse_today(args.today)
    now = dt.datetime.combine(today, dt.datetime.now(dt.timezone.utc).time(), dt.timezone.utc)
    if args.today:
        now = dt.datetime.combine(today, dt.time(12, 0), dt.timezone.utc)

    try:
        player_ids = published_player_ids(args.players_file)
    except (OSError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"could not read players: {exc}", file=sys.stderr)
        return 1

    store = args.data_dir
    try:
        if args.status:
            summary = summarize(store, player_ids, today=today)
        elif args.full:
            summary = run_full(store, player_ids, today=today, now=now, limit=args.limit)
        elif args.refresh:
            summary = run_refresh(store, player_ids, today=today, now=now, limit=args.limit)
        else:
            summary = run_window(
                store,
                player_ids,
                today=today,
                now=now,
                days=args.days,
                limit=args.limit,
            )
    except Exception as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 1

    print(format_summary(summary), end="")
    attempts = summary.get("fetch_attempts") or 0
    failures = summary.get("fetch_failures") or 0
    if summary.get("aborted") or (attempts and failures == attempts):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
