"""Career-move lines for a player page, in our own words.

Each line is built from a date, a kind, and club ids. The transaction
sentence is used only to decide the kind. It is never copied.

Option and recall trips collapse once a season has at least three
options. Number changes, minor-league assignments, invites, roster-status
notes, personal lists, and injured-list rows are left out. The injured-list
table already carries those.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from mlb_clubs import MLB_CLUB_IDS, _int_id, involves_mlb_club, the_club

_SIGNED_RE = re.compile(r"\bsigned\b", re.IGNORECASE)
_MINOR_RE = re.compile(r"minor league (?:contract|deal)", re.IGNORECASE)
_SELECTED_RE = re.compile(
    r"selected the contract|contract selected|purchased the contract|purchased contract of",
    re.IGNORECASE,
)
_RULE5_RE = re.compile(r"rule 5", re.IGNORECASE)
_RECALL_RE = re.compile(r"\brecalled\b", re.IGNORECASE)
_OPTION_RE = re.compile(r"\boptioned\b", re.IGNORECASE)
_DFA_RE = re.compile(r"designated .{0,60}? for assignment", re.IGNORECASE)
_OUTRIGHT_RE = re.compile(r"\boutrighted\b|sent .{0,60}? outright", re.IGNORECASE)
_CLAIM_RE = re.compile(r"claimed .{0,60}? off waivers", re.IGNORECASE)
_TRADE_RE = re.compile(r"\btraded\b", re.IGNORECASE)
_RELEASE_RE = re.compile(r"\breleased\b", re.IGNORECASE)
_FA_RE = re.compile(r"elected free agency|declared free agency", re.IGNORECASE)
_RETURN_RE = re.compile(r"returned to .{0,80}? \bfrom\b", re.IGNORECASE)
_RETIRED_RE = re.compile(r"\bretired\b", re.IGNORECASE)

# Dropped on purpose. Checked after the moves above so "designated for
# assignment" is not thrown out by the word "assigned".
_NOISE_RE = re.compile(
    r"uniform number|changed number|number to"
    r"|non-roster"
    r"|roster status"
    r"|temporarily inactive"
    r"|reserve list|development list"
    r"|paternity|bereavement|family medical|restricted list|\bsuspended\b"
    r"|injured list|disabled list|rehab assignment"
    r"|\breassigned\b|\bassigned\b"
    r"|\bactivated\b|\breinstated\b"
    r"|\bdrafted\b",
    re.IGNORECASE,
)

# Offseason contract selections read as roster protection, not a call-up.
_OFFSEASON_MONTHS = (11, 12, 1, 2)

# A season's shuttling collapses at this many times sent down.
_SHUTTLE_OPTIONS = 3


@dataclass(frozen=True)
class Move:
    date: dt.date
    text: str
    kind: str


def _as_date(value) -> dt.date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _club(row: dict, *keys: str) -> int | None:
    for key in keys:
        club = _int_id(row.get(key))
        if club is not None and club in MLB_CLUB_IDS:
            return club
    return None


def _both_mlb(row: dict) -> tuple[int, int] | None:
    src = _int_id(row.get("from_team_id"))
    dst = _int_id(row.get("to_team_id"))
    if src in MLB_CLUB_IDS and dst in MLB_CLUB_IDS:
        return src, dst
    return None


def classify_move(row: dict) -> str | None:
    """The timeline kind, or None when the row is noise.

    Free-agency elections are kept even when the only club id on the row
    is not a major league club. Every other row has to name one.
    """
    desc = row.get("description") or ""
    if not desc:
        return None
    free_agent = _FA_RE.search(desc) is not None
    if not free_agent and not involves_mlb_club(row):
        return None
    if _TRADE_RE.search(desc):
        return "trade"
    if _CLAIM_RE.search(desc):
        return "claim"
    if _RULE5_RE.search(desc) and _SELECTED_RE.search(desc):
        return "rule5_pick"
    if _RETURN_RE.search(desc) and _both_mlb(row):
        return "rule5_return"
    if _DFA_RE.search(desc):
        return "dfa"
    if _OUTRIGHT_RE.search(desc):
        return "outright"
    if _RELEASE_RE.search(desc):
        return "release"
    if free_agent:
        return "free_agency"
    if _OPTION_RE.search(desc):
        return "option"
    if _RECALL_RE.search(desc):
        return "recall"
    if _SELECTED_RE.search(desc):
        row_date = _as_date(row.get("date"))
        if row_date is not None and row_date.month in _OFFSEASON_MONTHS:
            return "selected_offseason"
        return "selected"
    if _SIGNED_RE.search(desc):
        if _MINOR_RE.search(desc):
            return "signed_minor"
        return "signed"
    if _RETIRED_RE.search(desc):
        return "retired"
    if _NOISE_RE.search(desc):
        return None
    return None


def _phrase(kind: str, row: dict, names: dict | None, *, came_off_il: bool) -> str | None:
    club = _club(row, "to_team_id", "team_id", "from_team_id")
    src = _club(row, "from_team_id")
    dst = _club(row, "to_team_id")
    if kind == "trade":
        # Direction is the ids on this player's row, never the sentence.
        if src and dst:
            return f"Traded from {the_club(src, names)} to {the_club(dst, names)}"
        if dst:
            return f"Traded to {the_club(dst, names)}"
        if src:
            return f"Traded from {the_club(src, names)}"
        return None
    if kind == "claim":
        if src and dst:
            return (
                f"Claimed off waivers by {the_club(dst, names)} "
                f"from {the_club(src, names)}"
            )
        if dst or club:
            return f"Claimed off waivers by {the_club(dst or club, names)}"
        return None
    if kind == "option":
        who = src or club
        if who is None:
            return None
        if came_off_il:
            return (
                f"Came off the injured list and was sent down to the minors "
                f"by {the_club(who, names)}"
            )
        return f"Sent down to the minors by {the_club(who, names)}"
    if kind == "recall":
        who = dst or club
        if who is None:
            return None
        return f"Called back up by {the_club(who, names)}"
    if kind == "selected":
        who = dst or club
        if who is None:
            return None
        return f"Called up and added to {the_club(who, names)} 40-man roster"
    if kind == "selected_offseason":
        who = dst or club
        if who is None:
            return None
        return f"Added to {the_club(who, names)} 40-man roster"
    if kind == "signed":
        who = dst or club
        if who is None:
            return None
        return f"Signed with {the_club(who, names)}"
    if kind == "signed_minor":
        who = dst or club
        if who is None:
            return None
        return f"Signed a minor league deal with {the_club(who, names)}"
    if kind == "dfa":
        who = src or club
        if who is None:
            return None
        return f"Designated for assignment by {the_club(who, names)}"
    if kind == "outright":
        who = src or club
        if who is None:
            return None
        return f"Removed from {the_club(who, names)} 40-man roster and kept in the minors"
    if kind == "release":
        who = dst or src or club
        if who is None:
            return None
        return f"Released by {the_club(who, names)}"
    if kind == "free_agency":
        return "Became a free agent"
    if kind == "rule5_pick":
        who = dst or club
        if who is None:
            return None
        return f"Picked by {the_club(who, names)} in the Rule 5 draft"
    if kind == "rule5_return":
        pair = _both_mlb(row)
        if pair is None:
            return None
        return f"Returned to {the_club(pair[1], names)} under Rule 5 rules"
    if kind == "retired":
        return "Retired"
    return None


def _collapse_shuttles(moves: list[Move]) -> list[Move]:
    """Replace a season of three or more options, and its recalls, with one line."""
    by_year: dict[int, list[Move]] = {}
    for move in moves:
        if move.kind in ("option", "recall"):
            by_year.setdefault(move.date.year, []).append(move)
    drop_ids: set[int] = set()
    summaries: list[Move] = []
    for year, group in by_year.items():
        options = [m for m in group if m.kind == "option"]
        if len(options) < _SHUTTLE_OPTIONS:
            continue
        for move in group:
            drop_ids.add(id(move))
        first = min(options, key=lambda m: m.date)
        summaries.append(
            Move(
                date=first.date,
                text=f"Moved between the majors and minors {len(options)} times in {year}",
                kind="shuttle",
            )
        )
    if not drop_ids:
        return moves
    kept = [m for m in moves if id(m) not in drop_ids]
    kept.extend(summaries)
    kept.sort(key=lambda m: (m.date, m.kind))
    return kept


def build_moves(
    rows: list[dict],
    *,
    debut: dt.date | None = None,
    debut_club_id: int | None = None,
    names: dict | None = None,
    il_close_dates: set[dt.date] | None = None,
) -> list[Move]:
    """Timeline entries, oldest first.

    `il_close_dates` are days an injured-list stay ended. An option on one
    of those days is a single line about coming off the list and being
    sent down, rather than two lines.
    """
    closes = il_close_dates or set()
    moves: list[Move] = []
    seen: set[tuple] = set()
    ordered = sorted(
        enumerate(rows or []),
        key=lambda item: ((item[1].get("date") or ""), item[0]),
    )
    for _, row in ordered:
        if not isinstance(row, dict):
            continue
        kind = classify_move(row)
        if kind is None:
            continue
        row_date = _as_date(row.get("date"))
        if row_date is None:
            continue
        text = _phrase(kind, row, names, came_off_il=row_date in closes)
        if not text:
            continue
        key = (row_date, kind, text)
        if key in seen:
            continue
        seen.add(key)
        moves.append(Move(row_date, text, kind))

    if debut is not None:
        if debut_club_id is not None:
            text = f"Made his major league debut with {the_club(debut_club_id, names)}"
        else:
            text = "Made his major league debut"
        moves.append(Move(debut, text, "debut"))

    moves = _collapse_shuttles(moves)
    moves.sort(key=lambda m: (m.date, _rank(m.kind)))
    return moves


def _rank(kind: str) -> int:
    order = {
        "signed": 0,
        "signed_minor": 0,
        "rule5_pick": 1,
        "selected_offseason": 2,
        "selected": 2,
        "debut": 3,
        "recall": 4,
        "trade": 5,
        "claim": 5,
        "option": 6,
        "shuttle": 6,
        "dfa": 7,
        "outright": 8,
        "release": 9,
        "free_agency": 10,
        "rule5_return": 11,
        "retired": 12,
    }
    return order.get(kind, 20)
