#!/usr/bin/env python3
"""
salary_estimates.py
--------------------
A league-minimum salary ESTIMATE for clearly pre-arbitration players.

WHY THIS EXISTS
===============
"<player> salary" is a huge search query this project answers nowhere. For a
player who is clearly pre-arbitration -- not Super Two, not arbitration
eligible, not free-agency eligible, by the exact same booleans the rest of
the site already computes -- the CBA's minimum salary is a defensible
estimate, and it is already sitting in config/cba/ (`salary.mlb_minimum`),
unused by any engine code until now.

THE HARD CONSTRAINT, AND WHY IT IS HARD
========================================
Some pre-arbitration players sign multi-year contract extensions and do NOT
earn the minimum. Showing the minimum next to a player who is actually
making eight figures would be the single most embarrassing number this site
could publish -- worse than showing nothing, because a reader has no way to
tell a labelled estimate from a confident mistake.

The brief's proposed detector was MLB Stats API transaction text ("signed
... to a N-year contract", "extension"). Measured 2026-10-29 against every
file in data/cache/transactions/ -- the same cache the service-time engine
already reads, so this cost no new API call -- that signal is essentially
never present: MLB's own transaction ledger tracks ROSTER STATUS (recalled,
optioned, placed on the IL), not money, and an extension changes no roster
status. Michael Harris II signed an 8-year extension in August 2022; his
cached transaction history has zero trace of it. Zero rows across the whole
rostered population contain the word "extension" or a contract-year phrase.

So two signals are combined, neither sufficient alone:

1. `_transaction_suggests_extension()` -- scans the already-cached
   transactions for extension-like wording. Measured to fire on nothing
   today; kept because it is free and would catch a case where MLB's feed
   ever does carry one, or a future transaction type this project has not
   seen yet.
2. `data/known_extensions.json` -- a hand-maintained list, entered from
   public reporting, in the same spirit as data/reference_super_two.json and
   data/reference_service_time.json. See that file's own header for why a
   hand list is the right tool here and what its limits are.

Either signal flips a player from "estimate" to "extension", and the page
says which. **Neither signal found anything is treated as "no extension
FOUND", never as "no extension EXISTS."** The page's own wording says so --
see write_player_pages.py and docs/app.js, which render this module's output
rather than re-deriving it.

WHAT IS DELIBERATELY NOT DONE
==============================
* No dollar figure for any specific player's actual salary -- that remains
  blocked for the reason docs/app.js already states in the stats tab (no
  permitted source). This module estimates only the LEAGUE MINIMUM for a
  category of player, which is a public CBA figure, not anyone's earnings.
* No network call. Both signals read files already on disk -- the cached
  transactions written by update_service_time.py's own daily job, and the
  hand-maintained extension list. This can run as `--recompute-derived`
  does: reload what is already stored, no API calls, no rate limit to mind.
* No re-derivation of "clearly pre-arbitration". That definition already
  lives in three places (service_time.py's booleans, write_player_pages.py's
  `_status()`, app.js's `classify()`); this module reuses the stored flags
  rather than adding a fourth copy that could drift from the other three.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cba  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache" / "transactions"
KNOWN_EXTENSIONS_PATH = ROOT / "data" / "known_extensions.json"
OUT_PATH = ROOT / "docs" / "data" / "salary_estimates.json"

STATUS_ESTIMATE = "estimate"
STATUS_EXTENSION = "extension"

# Defensive only -- see the module docstring. Measured 2026-10-29: zero hits
# across the whole cached rostered population. An ordinary free-agent or
# minor-league signing names a contract without implying a long-term major
# league deal, so those are excluded first rather than left to coincidence.
_ORDINARY_SIGNING_RE = re.compile(
    r"signed (?:free agent|non-roster|to a minor league)", re.IGNORECASE
)
_EXTENSION_RE = re.compile(
    r"\bextension\b|\b\d{1,2}-year contract\b|\bmulti-year\b|\bto a \$",
    re.IGNORECASE,
)


def _transaction_suggests_extension(player_id: int) -> bool:
    """Scan a player's ALREADY-CACHED transactions for extension wording.

    Reads data/cache/transactions/<id>.json -- the cache update_service_time.py
    already populates for the service-time engine. No network call, and no
    error for a missing or unreadable file: that is read as "no signal", the
    same safe default as everything else in this module.
    """
    path = CACHE_DIR / f"{player_id}.json"
    if not path.exists():
        return False
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    for row in rows:
        desc = row.get("description") or ""
        if _ORDINARY_SIGNING_RE.search(desc):
            continue
        if _EXTENSION_RE.search(desc):
            return True
    return False


def load_known_extensions(path: pathlib.Path = KNOWN_EXTENSIONS_PATH) -> dict[int, dict]:
    """{player id -> row} from data/known_extensions.json.

    The file's first element is a `_README` block, not a player row; skipped
    by virtue of having no `id` key rather than by position, so reordering
    the file cannot silently break this.
    """
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[int, dict] = {}
    for row in rows:
        if not isinstance(row, dict) or "id" not in row:
            continue
        try:
            out[int(row["id"])] = row
        except (TypeError, ValueError):
            continue
    return out


def is_clearly_pre_arbitration(player: dict) -> bool:
    """The same "Pre-arbitration" bucket write_player_pages.py's `_status()`
    and docs/app.js's `classify()` already compute -- reused, not re-derived.

    Requires at least one credited day: a 40-man player who has never
    debuted has not reached the majors at all, so "pre-arbitration" (which
    implies a clock that has started) would misdescribe him the same way
    classify() already refuses to call him anything but "Yet to debut".
    """
    return bool(
        player.get("on_40_man")
        and player.get("service_days_total")
        and not player.get("free_agent_eligible")
        and not player.get("super_two_candidate")
        and not player.get("arbitration_eligible")
    )


def classify(
    player: dict,
    known_extensions: dict[int, dict],
    ruleset: cba.Ruleset | None = None,
) -> dict | None:
    """One player's publishable salary record, or None if nothing should be
    shown at all -- the rule only applies to clearly pre-arbitration players,
    so anyone else (including "unsure") gets no salary section whatsoever.
    """
    if not is_clearly_pre_arbitration(player):
        return None

    ruleset = ruleset or cba.default()
    player_id = player.get("id")
    known_row = known_extensions.get(player_id)
    if known_row is not None:
        return {
            "status": STATUS_EXTENSION,
            "note": (
                known_row.get("note")
                or "A contract extension is on file for this player; the "
                "league-minimum estimate does not apply to him."
            ),
        }

    if player_id is not None and _transaction_suggests_extension(player_id):
        return {
            "status": STATUS_EXTENSION,
            "note": (
                "A roster transaction on file for this player mentions a "
                "multi-year contract or extension, so the league-minimum "
                "estimate does not apply to him."
            ),
        }

    return {
        "status": STATUS_ESTIMATE,
        "minimum": ruleset.require("salary.mlb_minimum"),
        "minimum_year": ruleset.require("salary.mlb_minimum_year"),
    }


def build(db: dict[str, dict], ruleset: cba.Ruleset | None = None) -> dict[int, dict]:
    """{player id -> salary record} for every player worth publishing one
    for. Sparse on purpose: a player who is not clearly pre-arbitration has
    no key at all, so the renderer's default ("nothing found -> show
    nothing") falls out of a plain dict lookup rather than a branch.
    """
    ruleset = ruleset or cba.default()
    known = load_known_extensions()
    out: dict[int, dict] = {}
    for player in db.values():
        record = classify(player, known, ruleset)
        if record is not None:
            out[player.get("id")] = record
    return out


def publish_payload(db: dict[str, dict], ruleset: cba.Ruleset | None = None) -> dict:
    """The shape written to docs/data/salary_estimates.json.

    `build()`'s per-player dict repeats the minimum and its year on every
    "estimate" row -- fine for one Python call, wasteful for 700+ players in
    a file the browser downloads. The minimum is the same number for every
    one of them (it is the one figure the whole feature is built on), so it
    is hoisted to the top and the per-player rows shrink to an id list for
    the common case and a short note for the rare one.
    """
    ruleset = ruleset or cba.default()
    records = build(db, ruleset)
    estimate_ids = sorted(
        pid for pid, r in records.items() if r["status"] == STATUS_ESTIMATE
    )
    extensions = {
        str(pid): {"note": r["note"]}
        for pid, r in records.items()
        if r["status"] == STATUS_EXTENSION
    }
    return {
        "minimum": ruleset.require("salary.mlb_minimum"),
        "minimum_year": ruleset.require("salary.mlb_minimum_year"),
        "estimate_ids": estimate_ids,
        "extensions": extensions,
    }


def write(db: dict[str, dict], ruleset: cba.Ruleset | None = None) -> pathlib.Path:
    payload = publish_payload(db, ruleset)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    return OUT_PATH


if __name__ == "__main__":
    # Standalone, same no-API-call guarantee as --recompute-derived: reload
    # the published database, which is already correct, and rewrite this one
    # derived file from it.
    db_path = ROOT / "docs" / "data" / "service_time.json"
    data = json.loads(db_path.read_text(encoding="utf-8"))
    players = {str(p["id"]): p for p in data["players"]}
    out_path = write(players)
    written = json.loads(out_path.read_text(encoding="utf-8"))
    print(
        f"Wrote salary estimates to {out_path}: "
        f"{len(written['estimate_ids'])} estimate, "
        f"{len(written['extensions'])} extension-flagged "
        f"(minimum ${written['minimum']:,} for {written['minimum_year']})"
    )
