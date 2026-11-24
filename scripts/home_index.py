"""The file the homepage paints from.

``index.json`` is the whole table: the current 40-man and every previous
player. The page opens on the 40-man, and the line above the table is a few
counts over that whole list. Those counts plus the rostered rows are enough
to draw the first screen. The previous-player rows stay in ``index.json``,
which the page fetches after the table is up, for that filter and for the
header search.

The census keys match ``renderStatTiles()`` in ``docs/app.js``:

  * ``yet_to_debut`` is players with zero credited days (``hasService``)
  * ``incomplete`` is players whose ``missing_seasons`` is not 0
    (``isComplete`` is false)
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME_FILE = ROOT / "docs" / "data" / "home.json"


def census_for(payload: dict) -> dict:
    """Whole-database counts the homepage shows above a roster-only table."""
    fields = payload["fields"]
    on_ix = fields.index("on_40_man")
    days_ix = fields.index("days")
    missing_ix = fields.index("missing_seasons")
    rows = payload["players"]
    rostered = sum(1 for row in rows if row[on_ix] == 1)
    tracked = len(rows)
    return {
        "tracked": tracked,
        "rostered": rostered,
        "previous": tracked - rostered,
        "yet_to_debut": sum(1 for row in rows if row[days_ix] == 0),
        "incomplete": sum(1 for row in rows if row[missing_ix] != 0),
    }


def home_payload(payload: dict) -> dict:
    """Rostered rows, the same lookups and rules, and the census above."""
    on_ix = payload["fields"].index("on_40_man")
    home = dict(payload)
    home["players"] = [row for row in payload["players"] if row[on_ix] == 1]
    home["player_count"] = len(home["players"])
    home["census"] = census_for(payload)
    return home


def write_home_index(payload: dict, path: Path | None = None) -> None:
    target = path or HOME_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(home_payload(payload), separators=(",", ":")))


def agreement_problems(index: dict, home: dict) -> list[str]:
    """Empty when ``home`` is exactly the roster slice of ``index``."""
    problems: list[str] = []
    if home.get("fields") != index.get("fields"):
        problems.append("home.json fields differ from index.json")
    if home.get("rules") != index.get("rules"):
        problems.append("home.json rules differ from index.json")
    if home.get("teams") != index.get("teams"):
        problems.append("home.json team lookup differs from index.json")
    if home.get("team_ids") != index.get("team_ids"):
        problems.append("home.json team ids differ from index.json")
    for key in ("positions", "disclaimer", "super_two_cutoff", "super_two_published"):
        if home.get(key) != index.get(key):
            problems.append(f"home.json {key} differs from index.json")
    fields = index.get("fields") or []
    if "on_40_man" not in fields:
        problems.append("index.json has no on_40_man column")
        return problems
    on_ix = fields.index("on_40_man")
    roster = [row for row in index.get("players") or [] if row[on_ix] == 1]
    if home.get("players") != roster:
        problems.append("home.json rows are not the rostered rows of index.json")
    if home.get("census") != census_for(index):
        problems.append(
            f"home.json census {home.get('census')} does not match index.json"
        )
    return problems
