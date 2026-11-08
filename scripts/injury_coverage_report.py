#!/usr/bin/env python3
"""
injury_coverage_report.py
--------------------------
Coverage summary for the injury body-part labels (scripts/injury_labels.py):
how many injured-list stints got a label vs. Undisclosed, the label
distribution, and how many Undisclosed stints had no reason text at all
versus text the mapper could not map.

No network. Reads only what is already on disk:
  - data/cache/transactions/*.json  (rostered/cached players; builds
    stints and labels fresh, since the reason text is never stored
    separately for them)
  - data/alumni_sections/*.json     (alumni; the label was already
    computed and stored at fetch time, before the reason text was
    discarded -- this script reads the stored label, it does not and
    cannot re-derive it without the text)

Prints counts only. Never prints a reason text or a player's name next
to an unmapped case -- see scripts/injury_labels.py and the
injury-body-part groundwork for why.

    python3 scripts/injury_coverage_report.py
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import injury_labels as il  # noqa: E402
from il_stints import SeasonWindows, build_stints  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache" / "transactions"
ALUMNI_DIR = ROOT / "data" / "alumni_sections"


def _cached_player_stints(today: dt.date) -> list:
    windows = SeasonWindows.load()
    out = []
    for path in sorted(CACHE_DIR.glob("*.json")):
        try:
            rows = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(rows, list):
            continue
        stints = build_stints(rows, windows, today)
        if not stints:
            continue
        il.apply_labels(stints)
        out.append(stints)
    return out


def _alumni_stints() -> list:
    """Already-labeled stints from the compact store -- no text to re-derive from."""
    out = []
    if not ALUMNI_DIR.exists():
        return out
    for path in sorted(ALUMNI_DIR.glob("[0-9][0-9].json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        players = data.get("players") if isinstance(data, dict) else None
        if not isinstance(players, dict):
            continue
        for record in players.values():
            stints = record.get("stints") if isinstance(record, dict) else None
            if stints:
                out.append(stints)
    return out


def main() -> int:
    today = dt.date.today()
    cached = _cached_player_stints(today)
    alumni = _alumni_stints()

    label_counts: collections.Counter = collections.Counter()
    no_text = 0
    unmapped_count = 0
    only_undisclosed_players = 0
    total_players = 0
    total_stints = 0

    for stints in cached:
        total_players += 1
        only_undisclosed = True
        for stint in stints:
            total_stints += 1
            label_counts[stint.label or il.UNDISCLOSED] += 1
            if stint.label and stint.label != il.UNDISCLOSED:
                only_undisclosed = False
            else:
                has_text = any(il._reason_text(text).strip() for _when, text, _how in stint.reasons)
                if has_text:
                    unmapped_count += 1
                else:
                    no_text += 1
        if only_undisclosed:
            only_undisclosed_players += 1

    # Alumni compact records: stored as dicts, already labeled (or not,
    # if the record predates this feature). There is no reason text left
    # to inspect, so "no text" and "unmapped" are not distinguishable --
    # both show as Undisclosed to a reader and both are counted the same
    # way here, as "no derived label yet".
    alumni_no_label = 0
    for stints in alumni:
        total_players += 1
        only_undisclosed = True
        for row in stints:
            total_stints += 1
            label = row.get("label") if isinstance(row, dict) else None
            label_counts[label or il.UNDISCLOSED] += 1
            if label and label != il.UNDISCLOSED:
                only_undisclosed = False
            else:
                alumni_no_label += 1
        if only_undisclosed:
            only_undisclosed_players += 1

    print(f"Players with >=1 injured-list stint: {total_players}")
    print(f"Total stints: {total_stints}")
    print()
    print("Label distribution:")
    for label, count in label_counts.most_common():
        pct = count / total_stints * 100 if total_stints else 0.0
        print(f"  {label:12s} {count:5d} {pct:5.1f}%")
    print()
    undisclosed = label_counts.get(il.UNDISCLOSED, 0)
    print(f"Undisclosed: {undisclosed} ({undisclosed / total_stints * 100:.1f}%)" if total_stints else "Undisclosed: 0")
    print(f"  cached players, no reason text on any row: {no_text}")
    print(f"  cached players, text present but unmapped: {unmapped_count}")
    print(f"  alumni compact records with no label stored (no text to derive from, or predates this feature): {alumni_no_label}")
    print(f"Players with only Undisclosed stints: {only_undisclosed_players}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
