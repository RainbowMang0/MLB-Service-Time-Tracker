#!/usr/bin/env python3
"""
validate_published.py
---------------------
Do the three published files agree with each other?

WHY THIS EXISTS
===============
The site publishes the same figures three times, in three shapes:

    docs/data/service_time.json   the database -- every field, one object
                                  per player, and the pipeline's own source
                                  of truth on the next run
    docs/data/index.json          the compact table payload the browser
                                  actually downloads
    docs/data/profiles/NN.json    per-player season detail, sharded by id % 64

Only the last two are ever read by a visitor, and they are DERIVED. So a
defect in the derivation publishes wrong numbers while the database sitting
next to them is perfectly correct, and every other check in this project --
which all read the database -- passes.

That is not hypothetical. Both workflows once ran `git add` on
service_time.json alone, so index.json was frozen at whatever had last been
committed by hand while the database updated daily underneath it. The site
served stale figures and nothing noticed, because nothing compared them.

WHAT IS CHECKED
===============
  * every database player appears in the index, and vice versa
  * day counts, roster flags and Super Two flags match
  * a club is published for rostered players only, and blanked otherwise
    (a non-rostered player's stored club is stale by construction, so
    printing it would assert a roster spot he does not hold)
  * every player has a profile, in the shard his id actually maps to
  * each profile's total matches the database
  * each profile's season rows sum to that total

Offline: reads only what is on disk, so it validates exactly what would be
served. Exits non-zero on any disagreement.

    python scripts/validate_published.py
"""

from __future__ import annotations

import glob
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB_FILE = ROOT / "docs" / "data" / "service_time.json"
INDEX_FILE = ROOT / "docs" / "data" / "index.json"
PROFILE_DIR = ROOT / "docs" / "data" / "profiles"
PROFILE_SHARDS = 64


def check_published() -> list[str]:
    problems: list[str] = []

    db = {p["id"]: p for p in json.loads(DB_FILE.read_text())["players"]}
    idx = json.loads(INDEX_FILE.read_text())
    teams = idx["teams"]

    seen = set()
    for row in idx["players"]:
        pid, name, team_ix, _pos_ix, days, on40, _missing, super_two = row
        seen.add(pid)
        rec = db.get(pid)
        if rec is None:
            problems.append(f"index carries {name} ({pid}), absent from the database")
            continue
        if days != rec["service_days_total"]:
            problems.append(
                f"{name}: index says {days} days, database says {rec['service_days_total']}"
            )
        if bool(on40) != bool(rec.get("on_40_man")):
            problems.append(f"{name}: index and database disagree on roster status")
        if bool(super_two) != bool(rec.get("super_two_candidate")):
            problems.append(f"{name}: index and database disagree on the Super Two flag")

        published_club = teams[team_ix]
        if on40 and published_club != (rec.get("team") or ""):
            problems.append(
                f"{name}: index club {published_club!r}, database {rec.get('team')!r}"
            )
        if not on40 and published_club:
            problems.append(
                f"{name}: a club ({published_club!r}) is published for a player who is "
                "not on a 40-man; his stored club is stale by construction"
            )

    for pid in set(db) - seen:
        problems.append(f"{db[pid].get('name')} ({pid}) is in the database but not the index")

    profiled = 0
    for path in sorted(glob.glob(str(PROFILE_DIR / "*.json"))):
        bucket = int(pathlib.Path(path).stem)
        shard = json.loads(pathlib.Path(path).read_text())
        for key, profile in shard.get("players", {}).items():
            profiled += 1
            pid = int(key)
            if pid % PROFILE_SHARDS != bucket:
                problems.append(
                    f"{profile.get('name')} sits in shard {bucket:02d} but his id maps "
                    f"to {pid % PROFILE_SHARDS:02d}, so the browser will not find him"
                )
            rec = db.get(pid)
            if rec is None:
                problems.append(f"profile {pid} has no database record")
                continue
            if profile.get("days") != rec["service_days_total"]:
                problems.append(
                    f"{profile.get('name')}: profile says {profile.get('days')} days, "
                    f"database says {rec['service_days_total']}"
                )
            rows = sum(int(s.get("d") or 0) for s in profile.get("seasons") or [])
            if rows != profile.get("days"):
                problems.append(
                    f"{profile.get('name')}: season rows sum to {rows}, profile total "
                    f"is {profile.get('days')} -- the profile contradicts its own table"
                )

    for pid in set(db) - {int(k) for p in glob.glob(str(PROFILE_DIR / "*.json"))
                          for k in json.loads(pathlib.Path(p).read_text()).get("players", {})}:
        problems.append(f"{db[pid].get('name')} ({pid}) has no profile")

    print(f"database {len(db)} | index {len(idx['players'])} | profiles {profiled}")
    return problems


def main() -> None:
    problems = check_published()
    if not problems:
        print("\nAll three published files agree.")
        return
    print(f"\n{len(problems)} disagreement(s):")
    for problem in problems[:40]:
        print(f"    {problem}")
    if len(problems) > 40:
        print(f"    ... and {len(problems) - 40} more")
    sys.exit(1)


if __name__ == "__main__":
    main()
