"""What happened after a player was designated for assignment.

Rows come from the roster transaction cache and the raw feed the daily
job already stores. The sentence is used only to name the kind. The club
is the id on the row. Service time is the figure already stored for that
player; this module does not recompute it.

A trade or a waiver claim puts him on a new club, so that is the outcome
when one falls inside the resolution window. An election of free agency
is the result when he is also outrighted or released in that same window:
the outright is the offer, and the election is what he did. A release
with no election is next. An outright he accepted is last. Anything else
is still pending while the ruleset's window is open, and unresolved once
that window has closed.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import cba
import career_moves
import change_log
from mlb_clubs import CLUB_ABBR, CLUB_NAMES, club_name

# How far back the page looks. This is the page's own span, not a CBA
# threshold. The designation window itself is read from the ruleset.
SPAN_DAYS = 60

OUTCOMES = ("trade", "claim", "outright", "release", "free_agency")

# Lower sorts first. Trade and claim move him. Free agency beats an
# outright or a release in the same window. See the module note.
_RANK = {
    "trade": 0,
    "claim": 1,
    "free_agency": 2,
    "release": 3,
    "outright": 4,
}

_LABELS = {
    "trade": "Traded",
    "claim": "Claimed off waivers",
    "outright": "Outrighted to the minors",
    "release": "Released",
    "free_agency": "Elected free agency",
    "pending": "Pending",
    "unresolved": "No outcome recorded",
}

_SHEET = {}
for _index, (_name, _abbr) in enumerate(CLUB_ABBR):
    for _tid, _stored in CLUB_NAMES.items():
        if _stored == _name:
            _SHEET[_tid] = _index
            break


def window_days(rules: cba.Ruleset | None = None) -> int:
    """How many days a designation may run, from the ruleset in force."""
    rules = cba.default() if rules is None else rules
    return int(rules.require("designated_for_assignment.max_service_days"))


def outcome_label(kind: str, days_left: int | None = None) -> str:
    """The words the page shows for one outcome."""
    if kind == "pending" and isinstance(days_left, int) and days_left > 0:
        unit = "day" if days_left == 1 else "days"
        return f"Pending, {days_left} {unit} left"
    return _LABELS.get(kind, "No outcome recorded")


def designating_club(row: dict) -> int | None:
    """The major league club that designated him.

    The feed usually puts that club on `toTeam` and leaves `fromTeam`
    empty. A flat cache row does the same with `to_team_id`.
    """
    return (
        change_log._club(row, "from_team_id")
        or change_log._club(row, "team_id")
        or change_log._club(row, "to_team_id")
    )


def destination_club(row: dict) -> int | None:
    """The major league club he joined, when the move names one."""
    src = change_log._club(row, "from_team_id")
    dst = change_log._club(row, "to_team_id")
    team = change_log._club(row, "team_id")
    if dst is not None and dst != src:
        return dst
    if team is not None and team != src:
        return team
    return None


def _row_date(row: dict) -> dt.date | None:
    return change_log._row_date(row)


def _txn_id(row: dict) -> int:
    value = row.get("id")
    if isinstance(value, bool) or not isinstance(value, int):
        return -1
    return value


def classify_followups(
    dfa_date: dt.date,
    followups: list[tuple[dt.date, str | None, int | None]],
    *,
    today: dt.date,
    window_days: int,
) -> tuple[str, dt.date | None, int | None, int | None]:
    """Outcome kind, outcome date, new club id, and days left.

    `followups` are `(date, kind, destination club)` on or after the
    designation. `days left` is set only while the outcome is still pending.
    The window is inclusive of the designation day, matching the service-time
    grace window: the last open day is `dfa_date + window_days - 1`.
    A resolving move dated one day past that still counts, so a row posted
    at the deadline is not left as "no outcome".
    """
    if window_days < 1:
        raise ValueError("the designation window must be at least one day")
    last_open = dfa_date + dt.timedelta(days=window_days - 1)
    horizon = dfa_date + dt.timedelta(days=window_days)
    best: tuple[tuple[int, dt.date, int], str, int | None] | None = None
    for day, kind, dest in followups:
        if kind not in _RANK or day < dfa_date or day > horizon or day > today:
            continue
        club = dest if kind in {"trade", "claim"} else None
        # The third key prefers a row that names the new club. It is 0 or 1,
        # never the club id, so two ids are never compared.
        key = (_RANK[kind], day, 0 if club is not None else 1)
        if best is None or key < best[0]:
            best = (key, kind, club)
    if best is not None:
        _key, kind, club = best
        return kind, _key[1], club, None
    if today <= last_open:
        return "pending", None, None, (last_open - today).days + 1
    return "unresolved", None, None, None


def _club_score(row: dict) -> int:
    return sum(
        change_log._club(row, key) is not None
        for key in ("from_team_id", "to_team_id", "team_id")
    )


def _better(new: dict, old: dict) -> bool:
    """Prefer the copy that names a club, then the copy with a transaction id."""
    new_score = _club_score(new)
    old_score = _club_score(old)
    if new_score != old_score:
        return new_score > old_score
    return _txn_id(new) > _txn_id(old)


def merge_rows(rows: list[dict]) -> list[dict]:
    """One row per date and description.

    The cache and the raw feed both store the same move. One copy often
    has the club id and the other has the transaction id.
    """
    best: dict[tuple, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = (row.get("date"), row.get("description"))
        current = best.get(key)
        if current is None or _better(row, current):
            best[key] = row
    return list(best.values())


def _person_name(rows: list[dict]) -> str:
    for row in rows:
        person = row.get("person") if isinstance(row, dict) else None
        if not isinstance(person, dict):
            continue
        name = person.get("fullName")
        if isinstance(name, str) and name.strip():
            return " ".join(name.split())
    return ""


def _service(value) -> str:
    if isinstance(value, str):
        text = value.strip()
        if len(text) >= 5 and text[0].isdigit() and "." in text:
            return text
    return ""


def events_for_player(
    player_id: int,
    rows: list[dict],
    *,
    today: dt.date,
    window_days: int,
    span_days: int,
    player: dict | None,
) -> list[dict]:
    """Every designation for one player inside the span."""
    merged = merge_rows(rows)
    merged.sort(key=lambda row: (_row_date(row) or dt.date.min, row.get("description") or ""))
    info = player or {}
    name = " ".join(str(info.get("name") or "").split()) or _person_name(merged)
    if not name:
        return []
    cutoff = today - dt.timedelta(days=span_days)
    service = _service(info.get("service_time"))
    href = info.get("href") if isinstance(info.get("href"), str) else ""
    found = []
    for row in merged:
        if career_moves.classify_move(row) != "dfa":
            continue
        day = _row_date(row)
        if day is None or day < cutoff or day > today:
            continue
        followups = []
        for other in merged:
            if other is row:
                continue
            other_day = _row_date(other)
            if other_day is None:
                continue
            kind = career_moves.classify_move(other)
            dest = destination_club(other) if kind in {"trade", "claim"} else None
            followups.append((other_day, kind, dest))
        kind, outcome_date, new_club, days_left = classify_followups(
            day, followups, today=today, window_days=window_days
        )
        club_id = designating_club(row)
        found.append(
            {
                "id": int(player_id),
                "name": name,
                "href": href,
                "club_id": club_id,
                "club": club_name(club_id) if club_id is not None else "",
                "dfa_date": day.isoformat(),
                "outcome": kind,
                "outcome_date": outcome_date.isoformat() if outcome_date else "",
                "new_club_id": new_club,
                "new_club": club_name(new_club) if new_club is not None else "",
                "days_left": days_left,
                "service_time": service,
            }
        )
    return found


def collect(
    grouped: dict[int, list[dict]],
    players: dict[int, dict],
    *,
    today: dt.date,
    window_days: int,
    span_days: int = SPAN_DAYS,
) -> list[dict]:
    """Newest designation first. `grouped` maps a player id to his rows."""
    rows = []
    for pid, raw_rows in grouped.items():
        try:
            player_id = int(pid)
        except (TypeError, ValueError):
            continue
        rows.extend(
            events_for_player(
                player_id,
                raw_rows,
                today=today,
                window_days=window_days,
                span_days=span_days,
                player=players.get(player_id),
            )
        )
    rows.sort(key=lambda row: (-_date_key(row["dfa_date"]), row["name"].casefold(), row["id"]))
    return rows


def _date_key(iso: str) -> int:
    try:
        day = dt.date.fromisoformat(iso)
    except ValueError:
        return 0
    return day.toordinal()


def group_cache(cache_dir: Path) -> dict[int, list[dict]]:
    """Player id to rows, from the roster cache and the raw feed beside it."""
    grouped: dict[int, list[dict]] = {}
    for pid, row in change_log.iter_cache(cache_dir):
        grouped.setdefault(pid, []).append(row)
    return grouped


def sheet_index(club_id: int | None) -> int:
    """Where this club sits in the site's club list. Unknown clubs sort last."""
    if club_id is None:
        return 10_000
    return _SHEET.get(club_id, 9_000)
