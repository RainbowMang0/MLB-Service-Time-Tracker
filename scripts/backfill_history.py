#!/usr/bin/env python3
"""
backfill_history.py
-------------------
One-time (well, several-times) backfill of players who are NOT on a 40-man
roster today, so the site logs the whole modern era rather than only whoever
happens to be rostered right now.

Why this is a separate script from update_service_time.py
---------------------------------------------------------
The daily job is small and fast: ~1,400 rostered players, incrementally
cached. This one walks ~5,300 unique players from 2009 onward and is a
fundamentally different shape of job:

  * It is RESUMABLE. State lives in data/backfill_state.json, so each run
    chews through a batch and records what it finished. A crash, a timeout,
    or a cancelled workflow costs you one batch instead of the whole run --
    which matters because GitHub Actions kills a job at six hours.
  * It does NOT cache transaction history. A retired player's service time
    never changes, so there is nothing to incrementally update, and caching
    thousands of transaction files would add tens of megabytes to the repo
    for no benefit.

Why 2009 and not earlier
------------------------
The /transactions endpoint has a hard floor. Sampling six players per season
and counting transactions involving a major league club:

    2005: 0   2006: 0   2007: 0   2008: 0   2009: 17   2010: 17   2011: 20

Nothing before 2009 is recoverable at any effort level. Players whose careers
started earlier are still included when they turn up, but flagged
history_complete=false so the site can say the number is a floor rather than
an estimate.

Usage
-----
    python scripts/backfill_history.py --batch 500      # one batch
    python scripts/backfill_history.py --status         # how much is left
    python scripts/backfill_history.py --batch 0        # everything, no limit

Run it repeatedly (locally or via the backfill-history workflow) until
--status reports zero remaining.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import fetch_mlb_data as mlb  # noqa: E402
from service_time import TRANSACTION_COVERAGE_START_YEAR  # noqa: E402
from update_service_time import (  # noqa: E402
    DATA_DIR,
    OUTPUT_FILE,
    TODAY,
    build_player_record,
    load_existing_db,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / "data" / "backfill_state.json"

DEFAULT_BATCH = 500


# ---------------------------------------------------------------------------
# Resumable state
# ---------------------------------------------------------------------------


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            print("State file was corrupt; starting from scratch.", file=sys.stderr)
    return {"processed_ids": [], "failed_ids": [], "last_run": None}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Player enumeration
# ---------------------------------------------------------------------------


def enumerate_players(start_year: int, end_year: int) -> dict[int, dict]:
    """
    Every player who appeared on an MLB roster in any season in the range.

    One API call per season (~18 total), which is cheap compared with the
    per-player work that follows.
    """
    players: dict[int, dict] = {}
    for year in range(start_year, end_year + 1):
        try:
            people = mlb._get("/sports/1/players", {"season": year}).get("people", [])
        except Exception as exc:  # a single bad season shouldn't kill the run
            print(f"  {year}: FAILED to enumerate ({exc})", file=sys.stderr)
            continue
        for person in people:
            pid = person.get("id")
            if pid is None or pid in players:
                continue
            players[pid] = {
                "id": pid,
                "fullName": person.get("fullName"),
                "team": (person.get("currentTeam") or {}).get("name"),
                "teamId": (person.get("currentTeam") or {}).get("id"),
                "position": (person.get("primaryPosition") or {}).get("abbreviation"),
            }
        print(f"  {year}: {len(people):>5} players (unique so far: {len(players)})")
    return players


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--batch",
        type=int,
        default=DEFAULT_BATCH,
        help="How many players to process this run (0 = no limit).",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=TRANSACTION_COVERAGE_START_YEAR,
        help="Earliest season to enumerate players from.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Report remaining work and exit without processing anything.",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Put previously failed players back in the queue.",
    )
    args = parser.parse_args()

    print(f"[{dt.datetime.now().isoformat(timespec='seconds')}] Backfill starting...")

    db = load_existing_db()
    state = load_state()
    processed = set(state.get("processed_ids", []))
    failed = set(state.get("failed_ids", []))
    if args.retry_failed:
        failed = set()

    print(f"Database currently holds {len(db)} players.")
    print(f"Enumerating players {args.start_year}-{TODAY.year}...")
    everyone = enumerate_players(args.start_year, TODAY.year)

    # Skip anyone we already have a record for, already processed, or gave up
    # on. The daily job owns rostered players; this one fills in the rest.
    todo = [
        pid
        for pid in everyone
        if str(pid) not in db and pid not in processed and pid not in failed
    ]
    todo.sort()

    print(
        f"\n{len(everyone)} players in range | {len(db)} already recorded | "
        f"{len(processed)} previously backfilled | {len(failed)} failed | "
        f"{len(todo)} remaining"
    )

    if args.status:
        return

    if not todo:
        print("Nothing left to do -- backfill is complete.")
        return

    batch = todo if args.batch == 0 else todo[: args.batch]
    print(f"Processing {len(batch)} players this run.\n")

    added = 0
    for i, pid in enumerate(batch, 1):
        entry = everyone[pid]
        try:
            # use_cache=False: these players are done playing, so there is no
            # incremental update to make and nothing worth storing on disk.
            record = build_player_record(entry, full_refresh=True, use_cache=False)
            record["on_40_man"] = False
            db[str(pid)] = record
            processed.add(pid)
            added += 1
            flag = "" if record.get("history_complete", True) else "  [partial history]"
            print(f"  [{i}/{len(batch)}] {record['name']}: {record['service_time']}{flag}")
        except Exception as exc:  # keep going; one bad player shouldn't stop the run
            failed.add(pid)
            print(
                f"  [{i}/{len(batch)}] FAILED for {entry.get('fullName')} ({pid}): {exc}",
                file=sys.stderr,
            )

    # Persist both the data and the progress marker together, so a crash
    # between the two can't make us re-do or skip work.
    write_db(db)
    state["processed_ids"] = sorted(processed)
    state["failed_ids"] = sorted(failed)
    save_state(state)

    remaining = len(todo) - len(batch)
    print(f"\nAdded {added} players. {remaining} still remaining.")
    if remaining:
        print("Run this again (or re-trigger the workflow) to continue.")
    else:
        print("Backfill complete.")


def write_db(db: dict[str, dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    incomplete = sum(1 for p in db.values() if not p.get("history_complete", True))
    output = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "MLB Stats API (statsapi.mlb.com, unofficial public endpoint)",
        "disclaimer": (
            "Service time figures are ESTIMATES computed from public roster "
            "transaction records, not official MLB/MLBPA figures. Transaction "
            f"coverage begins in {TRANSACTION_COVERAGE_START_YEAR}; players who "
            "debuted earlier are marked as having incomplete history and their "
            "figures are a floor, not an estimate."
        ),
        "coverage_start_year": TRANSACTION_COVERAGE_START_YEAR,
        "player_count": len(db),
        "incomplete_history_count": incomplete,
        "players": sorted(db.values(), key=lambda p: p.get("name") or ""),
    }
    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"Wrote {len(db)} player records to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
