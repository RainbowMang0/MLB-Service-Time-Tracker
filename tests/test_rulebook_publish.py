"""Rollback restores the previous published file byte for byte.

    python3 tests/test_rulebook_publish.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rulebook_publish as pub  # noqa: E402

PASS = 0
FAIL = 0


def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def test_rollback_restores_the_previous_file(tmp: Path):
    tmp.mkdir(parents=True, exist_ok=True)
    published = tmp / "service_time.json"
    snapshot = tmp / "published_snapshot.json"
    original = '{"players":[{"id":1,"service_time":"1.000"}]}\n'
    published.write_text(original, encoding="utf-8")
    snapshot.write_bytes(published.read_bytes())
    published.write_text('{"players":[{"id":1,"service_time":"9.999"}]}\n', encoding="utf-8")
    pub.rollback(published, snapshot)
    check("rollback restores the previous bytes", published.read_bytes() == original.encode("utf-8"))
    check("the restored file still parses", json.loads(published.read_text())["players"][0]["service_time"] == "1.000")


def test_a_lower_season_is_held():
    player = {
        "id": 1,
        "name": "Example",
        "on_40_man": True,
        "service_time": "1.000",
        "service_days_total": 172,
        "seasons": [{"y": 2024, "d": 152, "raw": 152, "pro": 152, "t": [133], "src": "read"}],
        "rules_version": 10,
    }
    result = {
        "seasons": [{"year": 2024, "credited_days": 47, "raw_days": 47}],
        "review_flags": [],
        "ambiguous": [],
    }
    fields, holds, _skipped = pub.apply_player(player, result, {}, 172)
    check("the published season is kept", fields["seasons"][0]["d"] == 152)
    check("the hold records both numbers", holds[0]["rulebook_days"] == 47 and holds[0]["published_days"] == 152)
    check("the hold is not cleared", holds[0]["cleared"] is False)
    cleared = dict(holds[0])
    cleared["cleared"] = True
    fields, again, _skipped = pub.apply_player(player, result, {"1:2024": cleared}, 172)
    check("a cleared hold publishes the lower figure", fields["seasons"][0]["d"] == 47 and again == [])


def main():
    print("Rollback")
    test_rollback_restores_the_previous_file(Path("/tmp/rulebook-publish-test"))
    print("Holds")
    test_a_lower_season_is_held()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
