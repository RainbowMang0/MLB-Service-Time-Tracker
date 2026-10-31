#!/usr/bin/env python3
"""
query_data.py
-------------
A small, stdlib-only CLI for answering targeted questions against
docs/data/service_time.json (and, for --super-two, data/super_two_reported.json
and docs/data/salary_estimates.json) without reading those files whole.

WHY THIS EXISTS
===============
docs/data/service_time.json holds 5,600+ players and is multiple megabytes;
the per-player profile shards and transaction caches are worse. Most real
questions ("what's Jordan Beck's service time", "who's near the Super Two
cutoff on the Rockies") only need a handful of rows and a handful of fields.
This prints just those, so a coding session (or this project's own
assistant) never has to load the whole database into context for a small
question.

WHAT THIS DOES NOT DO
======================
No service-time math is reimplemented here. years.days parsing/formatting
comes from super_two.parse_service()/format_service() (which reads the
days-per-credited-year figure from config/cba/, never a literal); the Super
Two cutoff and reported range reuse the already-computed `super_two_cutoff`
block in service_time.json plus super_two.load_reported_cutoff() and
super_two.cutoff_range(). This script is a reader, not an engine -- it does
not recompute the Super Two class or touch the network.

DATA MODEL NOTES (read before trusting a filter)
==================================================
* "current" vs "previous": the only such flag this project stores is
  on_40_man (today's 40-man roster, not just "has ever appeared in the
  majors"). Default is current players only; --include-previous adds
  everyone else in the database.
* --team matches either an exact MLB team id, or a case-insensitive
  substring of the team's full name (e.g. "Rockies", "Colorado"). There is
  no separate abbreviation table in this data, so a 3-letter code like
  "COL" will not match anything -- use (part of) the name instead.
* Selectable --fields are the scalar top-level keys on a player record,
  plus two computed ones: `status` (mirrors docs/app.js's classify(), so
  this tool and the site never disagree on wording) and `salary_estimate`
  (reuses docs/data/salary_estimates.json; blank unless the player is
  clearly pre-arbitration with no extension on file, per CLAUDE.md rule 4).
  `seasons` and `season_stats` are nested per-year detail and are
  deliberately not selectable -- for those, read
  docs/data/profiles/NN.json (sharded by id % 64) directly, or a short
  `python3 -c` one-liner, rather than adding a big nested blob to this
  tool's output.

EXAMPLES
========
    python3 scripts/query_data.py --player "jordan beck"
    python3 scripts/query_data.py --player 592091 --fields id,name,service_time,status
    python3 scripts/query_data.py --range 2.000-3.000 --team "Colorado Rockies"
    python3 scripts/query_data.py --team 115 --count
    python3 scripts/query_data.py --super-two
    python3 scripts/query_data.py --range 2.000-3.000 --include-previous --json --limit 0
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import super_two  # noqa: E402

DEFAULT_DATA_FILE = REPO_ROOT / "docs" / "data" / "service_time.json"
DEFAULT_REPORTED_FILE = REPO_ROOT / "data" / "super_two_reported.json"
DEFAULT_SALARY_FILE = REPO_ROOT / "docs" / "data" / "salary_estimates.json"

DEFAULT_FIELDS = ["id", "name", "team", "service_time", "status"]
DEFAULT_LIMIT = 25

# A display heuristic only -- how close to the cutoff counts as "near" for
# --super-two. Not a CBA threshold, so it is not read from config/cba/.
SUPER_TWO_NEAR_WINDOW_DAYS = 15

# Scalar top-level fields a player record carries. `seasons` and
# `season_stats` are nested per-year detail, deliberately excluded -- see
# the module docstring.
_SIMPLE_FIELDS = [
    "id",
    "name",
    "team",
    "team_id",
    "position",
    "mlb_debut",
    "birth_date",
    "last_played",
    "accrual_ceiling",
    "first_transaction",
    "last_updated",
    "rules_version",
    "service_time",
    "service_days_total",
    "missing_seasons",
    "on_40_man",
    "free_agent_eligible",
    "arbitration_eligible",
    "super_two_candidate",
    "super_two_bubble",
    "history_complete",
]
AVAILABLE_FIELDS = _SIMPLE_FIELDS + ["status", "salary_estimate"]


class QueryError(Exception):
    """A bad input the user needs to fix -- caught in main() and reported
    with a clean message and a nonzero exit, no traceback."""


def load_json(path: pathlib.Path, *, what: str) -> dict:
    if not path.exists():
        raise QueryError(f"no {what} file at {path} -- has it been generated?")
    try:
        return json.loads(path.read_text())
    except ValueError as e:
        raise QueryError(f"{path} is not valid JSON: {e}")


def load_optional_json(path: pathlib.Path) -> dict | None:
    """Like load_json, but a missing or malformed file is a graceful
    "nothing available" rather than an error -- used for the salary
    estimates file, which is genuinely optional context."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        return None


def status_label(player: dict, super_two_cutoff: dict | None) -> str:
    """Mirrors docs/app.js's classify() label logic, so this CLI and the
    live site never describe the same player differently."""
    if not player.get("on_40_man"):
        return "Not on a roster"
    total = player.get("service_days_total") or 0
    if total == 0 and player.get("history_complete") is False:
        return "Unknown"
    if total == 0:
        return "Yet to debut"
    if player.get("free_agent_eligible"):
        return "Free Agent Eligible"
    if player.get("super_two_candidate"):
        return "Super Two track" if super_two_cutoff else "Possible Super Two"
    if player.get("arbitration_eligible"):
        return "Arbitration Eligible"
    return "Pre-Arbitration"


def salary_estimate_label(player: dict, salary_data: dict | None) -> str:
    """CBA rule #4 (CLAUDE.md): a league-minimum estimate only for a player
    docs/data/salary_estimates.json already classified as clearly
    pre-arbitration with no extension on file; blank otherwise, including
    when the file is missing -- never invent a figure here."""
    if not salary_data:
        return ""
    player_id = player.get("id")
    extensions = salary_data.get("extensions") or {}
    if str(player_id) in extensions:
        return "extension on file (no minimum-salary estimate)"
    estimate_ids = salary_data.get("estimate_ids") or []
    if player_id in estimate_ids:
        minimum = salary_data.get("minimum")
        year = salary_data.get("minimum_year")
        if minimum:
            return f"${minimum:,} ({year} est., league minimum)"
    return ""


def field_value(player: dict, field: str, *, super_two_cutoff=None, salary_data=None):
    if field == "status":
        return status_label(player, super_two_cutoff)
    if field == "salary_estimate":
        return salary_estimate_label(player, salary_data)
    return player.get(field)


def resolve_fields(spec: str | None) -> list[str]:
    fields = [f.strip() for f in spec.split(",")] if spec else list(DEFAULT_FIELDS)
    fields = [f for f in fields if f]
    if not fields:
        raise QueryError("--fields was given but named no fields")
    unknown = [f for f in fields if f not in AVAILABLE_FIELDS]
    if unknown:
        raise QueryError(
            f"unknown field(s): {', '.join(unknown)}. "
            f"Available fields: {', '.join(AVAILABLE_FIELDS)}"
        )
    return fields


def parse_range(value: str) -> tuple[int, int]:
    """"2.000-3.000" -> (low_days, high_days), inclusive on both ends, using
    the configured days-per-credited-year (never a literal 172 -- see
    super_two.parse_service)."""
    parts = value.split("-")
    if len(parts) != 2:
        raise QueryError(
            "--range must be two years.days figures separated by '-', "
            f"e.g. 2.000-3.000 (got {value!r})"
        )
    low_str, high_str = (p.strip() for p in parts)
    try:
        low = super_two.parse_service(low_str)
        high = super_two.parse_service(high_str)
    except ValueError as e:
        raise QueryError(f"--range: {e}")
    if low > high:
        raise QueryError(
            f"--range: low end {low_str!r} is after high end {high_str!r}"
        )
    return low, high


def matches_player_query(player: dict, query: str) -> bool:
    query = query.strip()
    if query.isdigit():
        return player.get("id") == int(query)
    name = (player.get("name") or "").lower()
    return query.lower() in name


def matches_team_query(player: dict, query: str) -> bool:
    query = query.strip()
    if query.isdigit():
        return player.get("team_id") == int(query)
    team = (player.get("team") or "").lower()
    return query.lower() in team


def filter_players(
    players,
    *,
    player_query: str | None = None,
    service_range: tuple[int, int] | None = None,
    team_query: str | None = None,
    include_previous: bool = False,
):
    out = []
    for p in players:
        if not include_previous and not p.get("on_40_man"):
            continue
        if player_query is not None and not matches_player_query(p, player_query):
            continue
        if service_range is not None:
            low, high = service_range
            total = p.get("service_days_total") or 0
            if not (low <= total <= high):
                continue
        if team_query is not None and not matches_team_query(p, team_query):
            continue
        out.append(p)
    return out


def build_rows(players, fields, *, super_two_cutoff=None, salary_data=None):
    return [
        {
            f: field_value(p, f, super_two_cutoff=super_two_cutoff, salary_data=salary_data)
            for f in fields
        }
        for p in players
    ]


def render_rows(rows: list[dict], fields: list[str]) -> str:
    if not rows:
        return "(no matches)"

    def cell(value) -> str:
        return "" if value is None else str(value)

    widths = [
        max(len(f), *(len(cell(r.get(f))) for r in rows)) if rows else len(f)
        for f in fields
    ]
    lines = [
        "  ".join(f.ljust(w) for f, w in zip(fields, widths)),
        "  ".join("-" * w for w in widths),
    ]
    for r in rows:
        lines.append("  ".join(cell(r.get(f)).ljust(w) for f, w in zip(fields, widths)))
    return "\n".join(lines)


def run_super_two(data: dict, args: argparse.Namespace) -> int:
    cutoff = data.get("super_two_cutoff")
    if not cutoff:
        print("No Super Two cutoff is on file in this data file.")
        return 0

    reported = super_two.load_reported_cutoff(
        path=args.reported_file, projected_season=cutoff.get("season")
    )
    rng = super_two.cutoff_range(cutoff, reported)

    full_year = super_two.FULL_YEAR_DAYS
    low_band, high_band = 2 * full_year, 3 * full_year
    cutoff_days = cutoff.get("cutoff_days") or 0

    candidates = filter_players(
        data.get("players") or [],
        team_query=args.team,
        include_previous=args.include_previous,
    )
    matches = [
        p
        for p in candidates
        if low_band <= (p.get("service_days_total") or 0) < high_band
        and (
            p.get("super_two_candidate")
            or (p.get("service_days_total") or 0) >= cutoff_days - SUPER_TWO_NEAR_WINDOW_DAYS
        )
    ]
    matches.sort(key=lambda p: -(p.get("service_days_total") or 0))

    if args.count:
        print(len(matches))
        return 0

    fields = resolve_fields(args.fields)
    limited = matches if args.limit == 0 else matches[: args.limit]
    salary_data = (
        load_optional_json(args.salary_file) if "salary_estimate" in fields else None
    )
    rows = build_rows(limited, fields, super_two_cutoff=cutoff, salary_data=salary_data)

    if args.json:
        print(json.dumps({"cutoff": cutoff, "range": rng, "players": rows}, separators=(",", ":")))
        return 0

    print(
        f"Super Two cutoff (season {cutoff.get('season')}): {cutoff.get('cutoff')} "
        f"({cutoff_days} days) -- class of {cutoff.get('class_size')}, "
        f"top {cutoff.get('qualifying_count')} qualify"
    )
    if rng and rng.get("is_range"):
        source = (rng.get("reported") or {}).get("source") or "unofficial"
        print(f"Reported range: {rng['low']}-{rng['high']} ({source})")
    else:
        print("No usable reported figure on file -- showing the computed cutoff only.")
    print()
    print(render_rows(rows, fields))
    if len(matches) > len(limited):
        print(f"\n... {len(matches) - len(limited)} more match(es) not shown (raise --limit, or use --count).")
    return 0


def run_query(data: dict, args: argparse.Namespace) -> int:
    service_range = parse_range(args.range) if args.range else None
    matches = filter_players(
        data.get("players") or [],
        player_query=args.player,
        service_range=service_range,
        team_query=args.team,
        include_previous=args.include_previous,
    )

    if args.count:
        print(len(matches))
        return 0

    fields = resolve_fields(args.fields)
    limited = matches if args.limit == 0 else matches[: args.limit]
    super_two_cutoff = data.get("super_two_cutoff") if "status" in fields else None
    salary_data = (
        load_optional_json(args.salary_file) if "salary_estimate" in fields else None
    )
    rows = build_rows(
        limited, fields, super_two_cutoff=super_two_cutoff, salary_data=salary_data
    )

    if args.json:
        print(json.dumps(rows, separators=(",", ":")))
        return 0

    print(render_rows(rows, fields))
    if len(matches) > len(limited):
        print(f"\n... {len(matches) - len(limited)} more match(es) not shown (raise --limit, or use --count).")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="query_data.py",
        description=(
            "Answer targeted questions against docs/data/service_time.json "
            "without reading the whole file. Filters combine with AND."
        ),
        epilog=__doc__.split("EXAMPLES\n========\n", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--player", metavar="ID_OR_NAME",
        help="Exact MLB id, or a case-insensitive substring of the player's name.",
    )
    parser.add_argument(
        "--range", metavar="LOW-HIGH",
        help="Inclusive years.days service-time range, e.g. 2.000-3.000.",
    )
    parser.add_argument(
        "--team", metavar="ID_OR_NAME",
        help=(
            "Exact MLB team id, or a case-insensitive substring of the "
            "team's full name (this data has no abbreviation table, so a "
            "3-letter code will not match)."
        ),
    )
    parser.add_argument(
        "--fields",
        help=(
            "Comma-separated output fields (default: " + ",".join(DEFAULT_FIELDS) + "). "
            "Available: " + ", ".join(AVAILABLE_FIELDS)
        ),
    )
    parser.add_argument(
        "--super-two", action="store_true",
        help="Show the computed Super Two cutoff/reported range and the players in or near it.",
    )
    parser.add_argument("--count", action="store_true", help="Print only the number of matching rows.")
    parser.add_argument("--json", action="store_true", help="Compact JSON output (default: aligned plain text).")
    parser.add_argument(
        "--include-previous", action="store_true",
        help="Include players no longer on a 40-man roster (default: current roster only).",
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT,
        help=f"Cap the number of rows printed (default {DEFAULT_LIMIT}; 0 = unlimited).",
    )
    parser.add_argument(
        "--data-file", type=pathlib.Path, default=DEFAULT_DATA_FILE,
        help="Path to the service-time database (default: docs/data/service_time.json).",
    )
    parser.add_argument(
        "--reported-file", type=pathlib.Path, default=DEFAULT_REPORTED_FILE,
        help="Path to the reported Super Two figure, used only with --super-two "
             "(default: data/super_two_reported.json).",
    )
    parser.add_argument(
        "--salary-file", type=pathlib.Path, default=DEFAULT_SALARY_FILE,
        help="Path to the salary estimates file, used only by --fields salary_estimate "
             "(default: docs/data/salary_estimates.json).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        if args.limit < 0:
            raise QueryError("--limit must be 0 or a positive integer")
        data = load_json(args.data_file, what="data")
        if args.super_two:
            return run_super_two(data, args)
        return run_query(data, args)
    except QueryError as e:
        print(f"query_data.py: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
