#!/usr/bin/env python3
"""
probe_headshots.py
------------------
Does MLB publish a headshot for the players in this database, and for how
many of them?

WHY MEASURE RATHER THAN ASSUME
===============================
Adding a photo to each profile is cheap only if the photos exist. The
database is 5,569 players and four fifths of them are retired -- if MLB's
CDN only serves current players, most profiles would show a grey silhouette,
and a feature that renders a placeholder four times out of five is worse
than no feature.

The CDN answers with a generic silhouette rather than a 404 when it has no
photo (that is what the `d_people:generic:headshot` default in the URL
does), so a status code alone proves nothing. This compares each response
against the known silhouette instead: fetch it once for an id that cannot
have a photo, then treat any response with the same bytes as "no photo".

Samples rather than sweeping all 5,569 -- that would be 5,569 requests at
MLB's expense to answer a yes/no design question.

    python scripts/probe_headshots.py --sample 60

Needs network, so run it from Actions:
    Actions -> "Validate Service Time" -> check: headshots
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB_FILE = ROOT / "docs" / "data" / "service_time.json"

# The size the profile would actually request. `d_people:generic:...` is the
# CDN's own default-image directive: no photo yields the silhouette, not an
# error.
URL = (
    "https://img.mlbstatic.com/mlb-photos/image/upload/"
    "d_people:generic:headshot:67:current.png/w_213,q_auto:best/"
    "v1/people/{id}/headshot/67/current"
)

# An id in the valid range that no real player holds, used to learn what the
# silhouette looks like.
SENTINEL_ID = 1


def fetch(pid: int, timeout: int = 20) -> tuple[int, bytes]:
    req = urllib.request.Request(
        URL.format(id=pid), headers={"User-Agent": "mlb-service-time-tracker/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", 0)
        return int(code or 0), b""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=60, help="players per group")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    players = json.loads(DB_FILE.read_text())["players"]
    rostered = [p for p in players if p.get("on_40_man")]
    former = [p for p in players if not p.get("on_40_man")]
    debuted = [p for p in former if p.get("mlb_debut")]

    print(f"database: {len(players)} players, {len(rostered)} rostered, {len(former)} not\n")

    status, body = fetch(SENTINEL_ID)
    if not body:
        print(f"Could not reach the CDN at all (status {status}). Nothing measured.")
        sys.exit(1)
    silhouette = hashlib.sha256(body).hexdigest()
    print(f"silhouette reference: status {status}, {len(body)} bytes, sha {silhouette[:12]}\n")

    rnd = random.Random(args.seed)
    groups = {
        "on a 40-man": rnd.sample(rostered, min(args.sample, len(rostered))),
        "former, debuted": rnd.sample(debuted, min(args.sample, len(debuted))),
    }

    overall = {}
    for label, sample in groups.items():
        real = generic = failed = 0
        misses = []
        for p in sample:
            code, data = fetch(int(p["id"]))
            if not data:
                failed += 1
                continue
            if hashlib.sha256(data).hexdigest() == silhouette:
                generic += 1
                misses.append(p["name"])
            else:
                real += 1
            time.sleep(0.12)  # polite: this is MLB's bandwidth, not ours
        n = len(sample)
        overall[label] = (real, n)
        print(f"{label}: {n} sampled")
        print(f"    real photo   {real:3d}  ({real / n:.0%})")
        print(f"    silhouette   {generic:3d}  ({generic / n:.0%})")
        print(f"    request failed {failed:3d}")
        if misses:
            print(f"    without a photo: {', '.join(misses[:8])}"
                  + (" ..." if len(misses) > 8 else ""))
        print()

    print("VERDICT")
    for label, (real, n) in overall.items():
        share = real / n
        verdict = (
            "worth showing" if share >= 0.9
            else "worth showing, with a visible fallback" if share >= 0.5
            else "mostly placeholders -- not worth showing"
        )
        print(f"  {label:<18} {share:.0%} -> {verdict}")


if __name__ == "__main__":
    main()
