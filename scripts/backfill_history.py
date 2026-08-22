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
    write_index,
    write_profiles,
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
            # currently_rostered=False: and because they ARE done playing,
            # their service clock has to be capped at their final season
            # rather than running to today. Omitting this credited half of the
            # first backfill batch with 15+ years of phantom service time.
            record = build_player_record(
                entry, full_refresh=True, use_cache=False, currently_rostered=False
            )
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

    report_implausible(db)
    report_impossible_totals(db)

    remaining = len(todo) - len(batch)
    print(f"\nAdded {added} players. {remaining} still remaining.")
    if remaining:
        print("Run this again (or re-trigger the workflow) to continue.")
    else:
        print("Backfill complete.")


# Only 33 players in MLB history have reached 20 years of service, and none of
# them are reconstructible from a feed that starts in 2009. Anything at or above
# this is a bug, not a career.
IMPLAUSIBLE_SERVICE_YEARS = 20


def report_implausible(db: dict[str, dict]) -> None:
    """
    Shout if the database contains service times that cannot be real.

    The first backfill batch credited 246 of 500 retired players with 15+
    years because their clock never stopped, and that was only caught by
    eyeballing the last five lines of a 500-line log. A systemic error should
    announce itself.
    """
    bad = sorted(
        (p for p in db.values() if p.get("service_days_total", 0) >= IMPLAUSIBLE_SERVICE_YEARS * 172),
        key=lambda p: -p["service_days_total"],
    )
    if not bad:
        return
    print(
        f"\n!! WARNING: {len(bad)} player(s) at or above {IMPLAUSIBLE_SERVICE_YEARS}.000 "
        "years -- almost certainly a bug, not a career:"
    )
    for p in bad[:10]:
        print(f"     {p.get('name')}: {p.get('service_time')} (debut {p.get('mlb_debut')})")
    if len(bad) > 10:
        print(f"     ... and {len(bad) - 10} more")


def max_creditable_days(player: dict) -> int | None:
    """
    The most service time this player's record could legitimately show.

    CAUTION -- this bound is only as good as TRANSACTION_COVERAGE_START_YEAR,
    and that premise is in doubt. It assumes nothing accrues before 2009
    because nothing is reported before 2009. The cache appears to agree (3
    rows out of 64,643 predate it) but that sample is biased: almost every
    cached player is currently rostered and so debuted well after 2009, and
    had no professional transactions at all before then.

    Against the backfill the bound produces what look like false positives.
    Lew Ford is credited 1,085 days against a 2009-2012 window that this
    caps at 688 -- but 1,085 days is 6.31 years, close to his real career
    total, which is only reachable by crediting his 2003-2007 Minnesota
    seasons. Angel Guzman's 568 days is exactly 172*3 + 52, i.e. four
    seasons starting in 2006. Both suggest the feed does carry pre-2009
    history for players who were active then, and that the bound is wrong
    rather than the records.

    Resolving it needs one live query: fetch a pre-2009 player's
    /transactions and see whether major-league rows come back for those
    years. Until then treat a hit here as "worth a look", not as proof.

    Returns None when the record predates this field or is still accruing.
    """
    ceiling = player.get("accrual_ceiling")
    if not ceiling:
        return None
    debut = player.get("mlb_debut")
    first = max(
        int(debut[:4]) if debut else TRANSACTION_COVERAGE_START_YEAR,
        TRANSACTION_COVERAGE_START_YEAR,
    )
    seasons = int(ceiling[:4]) - first + 1
    return max(seasons, 0) * 172


def report_impossible_totals(db: dict[str, dict]) -> None:
    """
    Flag records crediting more service time than their own season window allows.

    This catches the class of bug where an interval is left open across years
    the player spent outside MLB -- the clock keeps running because the move
    that ended his tenure ("elected free agency", a jump to an independent or
    foreign league) is not a recognized stop. Lew Ford surfaced it: 1,085 days
    credited against a 2009-2013 window that caps at 860.

    Unlike the >=20-year heuristic this is an invariant, not a guess, so a hit
    here is always a real defect.
    """
    bad = []
    for p in db.values():
        cap = max_creditable_days(p)
        if cap is not None and p.get("service_days_total", 0) > cap:
            bad.append((p, cap))
    if not bad:
        return
    bad.sort(key=lambda x: x[1] - x[0]["service_days_total"])
    print(
        f"\n!! WARNING: {len(bad)} record(s) credit more service time than their "
        "season window allows -- the clock ran through years spent outside MLB:"
    )
    for p, cap in bad[:10]:
        excess = p["service_days_total"] - cap
        print(
            f"     {p.get('name')}: {p.get('service_time')} "
            f"({p['service_days_total']}d vs {cap}d max, +{excess}d) "
            f"debut={p.get('mlb_debut')} last_played={p.get('last_played')}"
        )
    if len(bad) > 10:
        print(f"     ... and {len(bad) - 10} more")


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
    write_index(db)
    write_profiles(db)


if __name__ == "__main__":
    main()
