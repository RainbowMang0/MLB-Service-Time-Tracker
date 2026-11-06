"""Which season stat tables a player page shows, and the rows in them.

Pitching innings stay in baseball notation ("85.2" is 85 and 2 outs,
not 85.2). Rate stats on a single season are shown as stored. A career
rate is recomputed from the counting stats. On-base and slugging
percentages are not recomputed: the stored lines do not carry the
fields that would make those exact.

A traded season is a total row ("2 Tms") plus one row per club when the
stored line has that shape. Older lines are a single row.
"""

from __future__ import annotations

PITCHER_POSITIONS = frozenset({"P", "SP", "RP", "LHP", "RHP", "CL", "CP"})

HITTING_COUNTING = (
    "gamesPlayed", "atBats", "runs", "hits", "doubles", "triples",
    "homeRuns", "rbi", "baseOnBalls", "strikeOuts", "stolenBases",
)
PITCHING_COUNTING = (
    "gamesPlayed", "gamesStarted", "wins", "losses", "saves",
    "strikeOuts", "baseOnBalls", "hits", "earnedRuns", "homeRuns",
)

# Mobile-first column order. The rest sit further right, in the scroll.
HITTING_COLUMNS = (
    ("year", "Year"),
    ("team", "Tm"),
    ("gamesPlayed", "G"),
    ("atBats", "AB"),
    ("hits", "H"),
    ("homeRuns", "HR"),
    ("rbi", "RBI"),
    ("stolenBases", "SB"),
    ("avg", "AVG"),
    ("ops", "OPS"),
    ("runs", "R"),
    ("doubles", "2B"),
    ("triples", "3B"),
    ("baseOnBalls", "BB"),
    ("strikeOuts", "SO"),
    ("obp", "OBP"),
    ("slg", "SLG"),
)
PITCHING_COLUMNS = (
    ("year", "Year"),
    ("team", "Tm"),
    ("gamesPlayed", "G"),
    ("gamesStarted", "GS"),
    ("wl", "W-L"),
    ("saves", "SV"),
    ("inningsPitched", "IP"),
    ("era", "ERA"),
    ("strikeOuts", "SO"),
    ("whip", "WHIP"),
    ("hits", "H"),
    ("earnedRuns", "ER"),
    ("homeRuns", "HR"),
    ("baseOnBalls", "BB"),
)


def ip_to_outs(value) -> int | None:
    """Baseball innings as outs. '85.2' is 257 outs. A decimal is not innings."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    if "." in text:
        whole, frac = text.split(".", 1)
        if not _is_int(whole) or len(frac) != 1 or frac not in "012":
            return None
        return int(whole) * 3 + int(frac)
    if _is_int(text):
        return int(text) * 3
    return None


def outs_to_ip(outs: int) -> str:
    return f"{outs // 3}.{outs % 3}"


def _is_int(text: str) -> bool:
    if not text:
        return False
    return text.isdigit() or (text[0] == "-" and text[1:].isdigit())


def _years(season_stats: dict) -> list[int]:
    out = []
    for key in season_stats or {}:
        try:
            out.append(int(key))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def _group(season_stats: dict, year: int, name: str) -> dict | None:
    rec = season_stats.get(str(year))
    if rec is None:
        rec = season_stats.get(year)
    if not isinstance(rec, dict):
        return None
    group = rec.get(name)
    return group if isinstance(group, dict) else None


def _number(row: dict, key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _has_number(row: dict, key: str) -> bool:
    value = row.get(key)
    return not isinstance(value, bool) and value not in (None, "")


def total_at_bats(season_stats: dict) -> int:
    total = 0
    for year in _years(season_stats):
        group = _group(season_stats, year, "hitting")
        if not group:
            continue
        if _has_number(group, "atBats"):
            total += _number(group, "atBats")
            continue
        for split in group.get("splits") or []:
            if isinstance(split, dict):
                total += _number(split, "atBats")
    return total


def total_ip_outs(season_stats: dict) -> int:
    total = 0
    for year in _years(season_stats):
        group = _group(season_stats, year, "pitching")
        if not group:
            continue
        outs = ip_to_outs(group.get("inningsPitched"))
        if outs is not None:
            total += outs
            continue
        for split in group.get("splits") or []:
            if isinstance(split, dict):
                part = ip_to_outs(split.get("inningsPitched"))
                if part is not None:
                    total += part
    return total


def tables_to_show(position: str | None, season_stats: dict) -> tuple[bool, bool]:
    """(batting, pitching). Pitchers do not get a batting table.

    A position player gets a pitching table only at 20 innings. Two-way
    is a TWP position, or 50 at-bats and 20 innings together, and gets both.
    """
    pos = (position or "").strip().upper()
    at_bats = total_at_bats(season_stats)
    innings = total_ip_outs(season_stats) / 3
    has_hitting = any(_group(season_stats, year, "hitting") for year in _years(season_stats))
    has_pitching = any(_group(season_stats, year, "pitching") for year in _years(season_stats))
    pitcher = pos in PITCHER_POSITIONS
    # No listed position: a long pitching line and little hitting reads as a pitcher.
    if not pos and innings >= 20 and at_bats < 50:
        pitcher = True
    # Pitchers do not get a batting table. The at-bat threshold is for a
    # player who is not listed as a pitcher (or is listed TWP).
    two_way = pos == "TWP" or (not pitcher and at_bats >= 50 and innings >= 20)
    show_batting = has_hitting and (pos == "TWP" or not pitcher)
    show_pitching = has_pitching and (two_way or pitcher or innings >= 20)
    return show_batting, show_pitching


def season_lines(group: dict) -> list[tuple[str, dict]]:
    """('2 Tms', total) then ('club id or label', split), or one club row.

    The first item's label is the team cell. A combined season uses
    'N Tms'. Club rows carry team_id on the dict; the label is the id
    as a string when present, else '—'.
    """
    splits = group.get("splits")
    if isinstance(splits, list) and len(splits) >= 2:
        count = group.get("numTeams")
        try:
            n_teams = int(count) if count not in (None, "") else len(splits)
        except (TypeError, ValueError):
            n_teams = len(splits)
        return [(f"{n_teams} Tms", group)] + [
            (_team_label(split), split) for split in splits if isinstance(split, dict)
        ]
    if isinstance(splits, list) and len(splits) == 1 and isinstance(splits[0], dict):
        return [(_team_label(splits[0]), splits[0])]
    return [(_team_label(group), group)]


def _team_label(row: dict) -> str:
    team_id = row.get("team_id")
    if isinstance(team_id, bool) or not isinstance(team_id, int):
        return "—"
    return str(team_id)


def _sum_counting(rows: list[dict], keys: tuple[str, ...]) -> dict:
    totals = {key: 0 for key in keys}
    seen = {key: False for key in keys}
    for row in rows:
        source = row
        if row.get("_sum_splits"):
            source = None
        if source is not None and any(_has_number(source, key) for key in keys):
            for key in keys:
                if _has_number(source, key):
                    totals[key] += _number(source, key)
                    seen[key] = True
            continue
        for split in (row.get("splits") or []):
            if not isinstance(split, dict):
                continue
            for key in keys:
                if _has_number(split, key):
                    totals[key] += _number(split, key)
                    seen[key] = True
    return {key: totals[key] for key in keys if seen[key]}


def career_hitting(season_stats: dict) -> dict:
    rows = []
    for year in _years(season_stats):
        group = _group(season_stats, year, "hitting")
        if group:
            rows.append(group)
    totals = _sum_counting(rows, HITTING_COUNTING)
    at_bats = totals.get("atBats") or 0
    hits = totals.get("hits") or 0
    if at_bats:
        totals["avg"] = _fmt_avg(hits, at_bats)
    return totals


def career_pitching(season_stats: dict) -> dict:
    rows = []
    outs = 0
    have_ip = False
    for year in _years(season_stats):
        group = _group(season_stats, year, "pitching")
        if not group:
            continue
        rows.append(group)
        part = ip_to_outs(group.get("inningsPitched"))
        if part is None:
            for split in group.get("splits") or []:
                if isinstance(split, dict):
                    bit = ip_to_outs(split.get("inningsPitched"))
                    if bit is not None:
                        part = (part or 0) + bit
        if part is not None:
            outs += part
            have_ip = True
    totals = _sum_counting(rows, PITCHING_COUNTING)
    if have_ip:
        totals["inningsPitched"] = outs_to_ip(outs)
        earned = totals.get("earnedRuns")
        if earned is not None and outs:
            totals["era"] = f"{(27 * earned / outs):.2f}"
        walks = totals.get("baseOnBalls")
        hits = totals.get("hits")
        if walks is not None and hits is not None and outs:
            totals["whip"] = f"{(3 * (walks + hits) / outs):.2f}"
        if "wins" in totals or "losses" in totals:
            totals["wl"] = f"{totals.get('wins', 0)}-{totals.get('losses', 0)}"
    elif "wins" in totals or "losses" in totals:
        totals["wl"] = f"{totals.get('wins', 0)}-{totals.get('losses', 0)}"
    return totals


def _fmt_avg(hits: int, at_bats: int) -> str:
    raw = f"{hits / at_bats:.3f}"
    if raw.startswith("0"):
        return raw[1:]
    return raw


def cell(row: dict, key: str) -> str:
    """One table cell. Missing is an em dash. Innings are not rewritten."""
    if key == "wl":
        if "wl" in row and row["wl"] not in (None, ""):
            return str(row["wl"])
        if not _has_number(row, "wins") and not _has_number(row, "losses"):
            return "–"
        return f"{_number(row, 'wins')}-{_number(row, 'losses')}"
    if key in ("avg", "obp", "slg", "ops", "era", "whip", "inningsPitched"):
        value = row.get(key)
        if value in (None, ""):
            return "–"
        return str(value)
    if key in ("year", "team"):
        value = row.get(key)
        return "–" if value in (None, "") else str(value)
    if not _has_number(row, key):
        return "–"
    return str(_number(row, key))
