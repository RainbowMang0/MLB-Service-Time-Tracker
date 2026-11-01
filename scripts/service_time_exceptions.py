"""
service_time_exceptions.py
--------------------------
Loads and validates the hand-kept service-time exceptions in
data/service_time_exceptions.json.

WHY THIS EXISTS
===============
Rules v7 treats a restricted-list placement as a STOP: a restricted-list
player is not paid and earns no Major League service. A few restricted-list
spells are the exception -- paid domestic-violence administrative leave and the
2020 high-risk COVID opt-outs DO earn service, and a suspension the feed shows
only as a restricted-list placement earns none. The transaction feed phrases
all of them identically, so they cannot be told apart from the wording. This
module is the one reader of the hand-kept list that records them, and -- like
cba.load_roy_full_year_awards -- it validates as it loads, so a malformed entry
fails loudly (in CI, via tests/test_service_time.py) rather than silently
crediting or dropping a player.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXCEPTIONS_PATH = ROOT / "data" / "service_time_exceptions.json"

# Every entry must carry all of these. `counts` is checked separately because a
# valid value of False is falsy and would be mis-read as "missing".
_REQUIRED_STR_OR_NUM = ("mlbam_id", "name", "start", "end", "reason", "source")


class ServiceTimeExceptionError(Exception):
    """A service-time exception entry is malformed, unsourced or out of order."""


def _parse_iso(value, field: str, i: int, path: pathlib.Path) -> dt.date:
    if not isinstance(value, str):
        raise ServiceTimeExceptionError(
            f"exception #{i} in {path}: {field} must be an ISO date string, got {value!r}"
        )
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise ServiceTimeExceptionError(
            f"exception #{i} in {path}: {field} {value!r} is not an ISO date (YYYY-MM-DD)"
        )


def _check_source(value, i: int, path: pathlib.Path) -> None:
    urls = value if isinstance(value, list) else [value]
    if not urls:
        raise ServiceTimeExceptionError(f"exception #{i} in {path}: source is empty")
    for url in urls:
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ServiceTimeExceptionError(
                f"exception #{i} in {path}: every source must be an https:// URL, got {url!r}"
            )


def load_exceptions(path: str | pathlib.Path | None = None) -> dict[int, list[dict]]:
    """{mlbam_id -> [exception, ...]} validated, or {} if the file is absent.

    Each returned exception is a dict with parsed `start`/`end` dates, a bool
    `counts`, and the original name/reason/source fields. Raises
    ServiceTimeExceptionError on any malformed entry.
    """
    p = pathlib.Path(path) if path else EXCEPTIONS_PATH
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    entries = data.get("exceptions", [])
    if not isinstance(entries, list):
        raise ServiceTimeExceptionError(f"'exceptions' in {p} must be a list")

    out: dict[int, list[dict]] = {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ServiceTimeExceptionError(f"exception #{i} in {p} is not an object")
        missing = [
            f
            for f in _REQUIRED_STR_OR_NUM
            if entry.get(f) is None
            or (isinstance(entry.get(f), str) and not entry.get(f).strip())
        ]
        if missing:
            raise ServiceTimeExceptionError(
                f"exception #{i} in {p} (name {entry.get('name')!r}) is missing "
                f"required field(s): {', '.join(missing)}"
            )
        if "counts" not in entry or not isinstance(entry["counts"], bool):
            raise ServiceTimeExceptionError(
                f"exception #{i} in {p}: counts must be true or false"
            )
        try:
            mlbam_id = int(entry["mlbam_id"])
        except (TypeError, ValueError):
            raise ServiceTimeExceptionError(
                f"exception #{i} in {p}: mlbam_id {entry['mlbam_id']!r} is not an integer"
            )
        start = _parse_iso(entry["start"], "start", i, p)
        end = _parse_iso(entry["end"], "end", i, p)
        if end < start:
            raise ServiceTimeExceptionError(
                f"exception #{i} in {p}: end {end} is before start {start}"
            )
        _check_source(entry["source"], i, p)
        out.setdefault(mlbam_id, []).append(
            {
                "name": entry["name"],
                "start": start,
                "end": end,
                "counts": entry["counts"],
                "reason": entry["reason"],
                "source": entry["source"],
            }
        )
    return out


def periods_for_player(
    exceptions: dict[int, list[dict]], player_id: int
) -> tuple[list[tuple[dt.date, dt.date]], list[tuple[dt.date, dt.date]]]:
    """(force_active_periods, force_inactive_periods) for one player.

    `force_active_periods` are the counts=true spells (credited service) and
    `force_inactive_periods` the counts=false ones (no service), in the shape
    build_global_active_intervals / compute_service_time expect.
    """
    entries = exceptions.get(player_id, [])
    active = [(e["start"], e["end"]) for e in entries if e["counts"]]
    inactive = [(e["start"], e["end"]) for e in entries if not e["counts"]]
    return active, inactive
