"""The three extra blocks on a player page: injured list, season stats, career moves.

Rendered from cached transactions and stored season lines. No network,
and nothing here is a service-time figure. Wording is ours. Transaction
sentences and injury descriptions are not copied.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import pathlib

from career_moves import Move, build_moves
import injury_labels
from il_stints import SeasonWindows, Stint, build_stints, list_label
from mlb_clubs import MLB_CLUB_IDS, club_name
from season_stat_tables import (
    HITTING_COLUMNS,
    PITCHING_COLUMNS,
    career_hitting,
    career_pitching,
    cell,
    season_lines,
    tables_to_show,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
TXN_DIR = ROOT / "data" / "cache" / "transactions"

_MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

_WINDOWS: SeasonWindows | None = None


def load_cached_transactions(player_id) -> list[dict] | None:
    """The on-disk transaction cache, or None when this player has no file.

    None means the page has nothing to render for moves or the injured
    list. An empty list means the file exists and recorded no rows.
    """
    if isinstance(player_id, bool) or not isinstance(player_id, int):
        try:
            player_id = int(player_id)
        except (TypeError, ValueError):
            return None
    path = TXN_DIR / f"{player_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


def _windows() -> SeasonWindows:
    global _WINDOWS
    if _WINDOWS is None:
        _WINDOWS = SeasonWindows.load()
    return _WINDOWS


def _md(day: dt.date) -> str:
    return f"{_MON[day.month - 1]} {day.day}"


def _mdy(day: dt.date) -> str:
    return f"{_MON[day.month - 1]} {day.day}, {day.year}"


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _debut(player: dict, rows: list[dict] | None) -> tuple[dt.date | None, int | None]:
    raw = player.get("mlb_debut")
    if not raw or raw == "—":
        return None, None
    try:
        day = dt.date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None, None
    for season in player.get("seasons") or []:
        try:
            year = int(season.get("y"))
        except (TypeError, ValueError):
            continue
        clubs = [c for c in (season.get("t") or []) if isinstance(c, int) and not isinstance(c, bool)]
        if year == day.year and clubs:
            return day, clubs[0]
    found = None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        raw_date = row.get("date") or ""
        if raw_date[:10] > day.isoformat():
            continue
        for key in ("to_team_id", "team_id", "from_team_id"):
            club = row.get(key)
            if isinstance(club, int) and not isinstance(club, bool) and club in MLB_CLUB_IDS:
                found = club
                break
    return day, found


def _season_clubs(player: dict) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for season in player.get("seasons") or []:
        try:
            year = int(season.get("y"))
        except (TypeError, ValueError):
            continue
        clubs = [c for c in (season.get("t") or []) if isinstance(c, int) and not isinstance(c, bool)]
        if clubs:
            out[year] = clubs
    return out


def _list_cell(stint: Stint, names: dict) -> str:
    label = f"{list_label(stint)} · {injury_labels.display(stint.label)}"
    if len(stint.clubs) > 1:
        arrow = " → ".join(club_name(club, names, short=True) for club in stint.clubs)
        label = f"{label} ({arrow})"
    return label


def _dates_cell(stint: Stint) -> str:
    if stint.kind == "open":
        return f"since {_md(stint.start)}"
    if stint.kind == "lower":
        if stint.days == 0:
            return _md(stint.start)
        return f"{_md(stint.start)} – not in record"
    if stint.end is None:
        return _md(stint.start)
    if stint.end.year != stint.start.year:
        return f"{_mdy(stint.start)} – {_mdy(stint.end)}"
    return f"{_md(stint.start)} – {_md(stint.end)}"


def _days_cell(stint: Stint) -> str:
    if stint.kind == "open":
        # No day count. The number would change every day of the season
        # and move the page's sitemap date with it.
        return '<td class="num pp-open">current</td>'
    if stint.kind == "lower" and stint.days == 0:
        return '<td class="num">—</td>'
    if stint.kind == "lower":
        return f'<td class="num">at least {stint.days}</td>'
    return f'<td class="num">{stint.days}</td>'


def _summary(stints: list[Stint]) -> str:
    noun = "stint" if len(stints) == 1 else "stints"
    known = sum(s.days for s in stints if s.kind != "open")
    has_lower = any(s.kind == "lower" for s in stints)
    has_open = any(s.kind == "open" for s in stints)
    parts = [f"{len(stints)} {noun}"]
    if has_open and not known and not has_lower:
        parts.append("current")
    elif has_lower and known:
        word = "day" if known == 1 else "days"
        parts.append(f"at least {known} {word}")
    elif known and not has_lower:
        word = "day" if known == 1 else "days"
        parts.append(f"{known} {word}")
    if has_open and (known or has_lower):
        parts.append("plus a current stint")
    return " · ".join(parts)


def _record_sentence(record_year: int | None) -> str:
    """Shown when the public feed starts after the career did.

    A zero in that gap would read as a full career with no injured-list
    time. The year is the first year the feed actually has for this player.
    """
    if not record_year:
        return ""
    return f"IL history in the public record begins in {int(record_year)}."


def _injured_html(stints: list[Stint], names: dict, record_year: int | None = None) -> str:
    if not stints:
        sentence = _record_sentence(record_year) or (
            "No major league injured-list time in the public record."
        )
        return (
            '<section class="pp-section" id="injuries">'
            "<h2>Injured list</h2>"
            f'<p class="pp-note">{sentence}</p>'
            "</section>"
        )
    body = []
    for stint in stints:
        body.append(
            "<tr>"
            f"<td>{stint.start.year}</td>"
            f"<td>{_esc(_list_cell(stint, names))}</td>"
            f"<td>{_esc(_dates_cell(stint))}</td>"
            f"{_days_cell(stint)}"
            "</tr>"
        )
    return (
        '<section class="pp-section" id="injuries">'
        "<details open>"
        "<summary><h2>Injured list</h2> "
        f'<span class="pp-sum">{_esc(_summary(stints))}</span></summary>'
        '<div class="tbl-wrap"><table class="pp-tbl">'
        "<thead><tr><th>Year</th><th>List</th><th>Dates</th>"
        '<th class="num">Days</th></tr></thead>'
        f"<tbody>{''.join(body)}</tbody>"
        "</table></div>"
        '<p class="pp-note">Regular-season days only. Days on the injured list '
        "still count as major league service time. At least means the return "
        "date is not in the public record. Injury area, where publicly "
        "reported. Some stints have no reported reason."
        f"{(' ' + _record_sentence(record_year)) if record_year else ''}</p>"
        "</details></section>"
    )


def _team_text(label: str, year: int, season_clubs: dict[int, list[int]], names: dict) -> str:
    if label.endswith(" Tms"):
        return label
    if label != "—":
        try:
            return club_name(int(label), names, short=True)
        except ValueError:
            return label
    clubs = season_clubs.get(year) or []
    if len(clubs) == 1:
        return club_name(clubs[0], names, short=True)
    if len(clubs) > 1:
        return f"{len(clubs)} Tms"
    return "—"


def _stat_table(title: str, columns: tuple, groups: list[tuple[int, dict]], career: dict, season_clubs, names) -> str:
    head = "".join(f"<th>{_esc(label)}</th>" for _, label in columns)
    body = []
    for year, group in groups:
        for label, row in season_lines(group):
            shown = dict(row)
            shown["year"] = str(year)
            shown["team"] = _team_text(label, year, season_clubs, names)
            cells = "".join(
                f'<td class="num">{_esc(cell(shown, key))}</td>' if key not in ("year", "team")
                else f"<td>{_esc(cell(shown, key))}</td>"
                for key, _label in columns
            )
            body.append(f"<tr>{cells}</tr>")
    career_row = dict(career)
    career_row["year"] = "Career"
    career_row["team"] = ""
    foot_cells = []
    for key, _label in columns:
        if key == "year":
            foot_cells.append("<td>Career</td>")
        elif key == "team":
            foot_cells.append("<td></td>")
        else:
            foot_cells.append(f'<td class="num">{_esc(cell(career_row, key))}</td>')
    return (
        f"<h3>{_esc(title)}</h3>"
        '<div class="tbl-wrap"><table class="pp-tbl">'
        f"<thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody>"
        f"<tfoot><tr>{''.join(foot_cells)}</tr></tfoot>"
        "</table></div>"
    )


def _stats_html(player: dict, names: dict) -> str:
    season_stats = player.get("season_stats") or {}
    if not isinstance(season_stats, dict) or not season_stats:
        return ""
    show_batting, show_pitching = tables_to_show(player.get("position"), season_stats)
    if not show_batting and not show_pitching:
        return ""
    seasons = _season_clubs(player)
    blocks = []
    years = []
    for key in season_stats:
        try:
            years.append(int(key))
        except (TypeError, ValueError):
            continue
    years.sort()
    if show_batting:
        groups = []
        for year in years:
            rec = season_stats.get(str(year)) or {}
            group = rec.get("hitting") if isinstance(rec, dict) else None
            if isinstance(group, dict):
                groups.append((year, group))
        if groups:
            blocks.append(_stat_table(
                "Batting", HITTING_COLUMNS, groups, career_hitting(season_stats), seasons, names
            ))
    if show_pitching:
        groups = []
        for year in years:
            rec = season_stats.get(str(year)) or {}
            group = rec.get("pitching") if isinstance(rec, dict) else None
            if isinstance(group, dict):
                groups.append((year, group))
        if groups:
            blocks.append(_stat_table(
                "Pitching", PITCHING_COLUMNS, groups, career_pitching(season_stats), seasons, names
            ))
    if not blocks:
        return ""
    return (
        '<section class="pp-section" id="stats">'
        "<details open>"
        "<summary><h2>Season stats</h2></summary>"
        + "".join(blocks)
        + '<p class="pp-note">Season totals from public data.</p>'
        "</details></section>"
    )


def _moves_html(moves) -> str:
    if not moves:
        return ""
    items = []
    for move in moves:
        items.append(
            "<li>"
            f'<time datetime="{move.date.isoformat()}">{_esc(_mdy(move.date))}</time> '
            f"{_esc(move.text)}"
            "</li>"
        )
    opened = " open" if len(moves) <= 8 else ""
    noun = "entry" if len(moves) == 1 else "entries"
    return (
        '<section class="pp-section" id="moves">'
        f"<details{opened}>"
        "<summary><h2>Career moves</h2> "
        f'<span class="pp-sum">{len(moves)} {noun}</span></summary>'
        f'<ol class="pp-tl">{"".join(items)}</ol>'
        "</details></section>"
    )


def _stint_from_compact(row: dict) -> Stint | None:
    """One stored injured-list stay. The compact file has no feed sentence."""
    if not isinstance(row, dict):
        return None
    start = row.get("start")
    try:
        start_day = dt.date.fromisoformat(str(start)[:10])
    except (TypeError, ValueError):
        return None
    end_raw = row.get("end")
    end_day = None
    if end_raw:
        try:
            end_day = dt.date.fromisoformat(str(end_raw)[:10])
        except ValueError:
            end_day = None
    lists = []
    for item in row.get("lists") or []:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        try:
            lists.append((int(item[0]), dt.date.fromisoformat(str(item[1])[:10]), str(item[2])))
        except (TypeError, ValueError):
            continue
    clubs = []
    for club in row.get("clubs") or []:
        if isinstance(club, int) and not isinstance(club, bool):
            clubs.append(club)
    try:
        days = int(row.get("days") or 0)
    except (TypeError, ValueError):
        days = 0
    label = row.get("label")
    return Stint(
        start=start_day,
        end=end_day,
        lists=lists,
        clubs=clubs,
        days=days,
        kind=str(row.get("kind") or "exact"),
        close=str(row.get("close") or ""),
        last_evidence=end_day or start_day,
        # No reason text is ever stored, so there is nothing to re-derive
        # from here -- the label itself was computed and stored at fetch
        # time, before the feed text was discarded. A record saved before
        # labels existed simply has none, and displays as not disclosed.
        label=label if isinstance(label, str) else None,
        label_source=row.get("label_source") if isinstance(row.get("label_source"), str) else None,
        label_conflict=bool(row.get("label_conflict")),
        multi=bool(row.get("multi")),
    )


def _move_from_compact(row: dict) -> Move | None:
    if not isinstance(row, dict):
        return None
    try:
        day = dt.date.fromisoformat(str(row.get("date") or "")[:10])
    except ValueError:
        return None
    text = row.get("text")
    if not isinstance(text, str) or not text:
        return None
    return Move(day, text, str(row.get("kind") or ""))


def _player_for_stats(player: dict, compact: dict | None) -> dict:
    """Season lines from the compact file when it has any, else the record."""
    if not compact:
        return player
    stats = compact.get("season_stats")
    if not isinstance(stats, dict) or not stats:
        return player
    overlaid = dict(player)
    overlaid["season_stats"] = stats
    return overlaid


def _empty_section(section_id: str, title: str, sentence: str) -> str:
    return (
        f'<section class="pp-section" id="{section_id}">'
        f"<h2>{title}</h2>"
        f'<p class="pp-note">{sentence}</p>'
        "</section>"
    )


def _sections_from_compact(
    player: dict,
    compact: dict,
    names: dict,
) -> str:
    """The three blocks from derived page data, not from a transaction file."""
    record_year = compact.get("il_record_year")
    try:
        record_year = int(record_year) if record_year else None
    except (TypeError, ValueError):
        record_year = None
    stints = []
    for row in compact.get("stints") or []:
        stint = _stint_from_compact(row)
        if stint is not None:
            stints.append(stint)
    parts = [_injured_html(stints, names, record_year)]
    stats = _stats_html(_player_for_stats(player, compact), names)
    parts.append(stats or _empty_section(
        "stats", "Season stats", "No major league season totals in the public record."
    ))
    moves = []
    for row in compact.get("moves") or []:
        move = _move_from_compact(row)
        if move is not None:
            moves.append(move)
    parts.append(_moves_html(moves) or _empty_section(
        "moves", "Career moves", "No career moves in the public record."
    ))
    return "\n".join(parts)


def render_player_sections(
    player: dict,
    transactions: list[dict] | None,
    team_names: dict | None,
    today: dt.date,
    windows: SeasonWindows | None = None,
    compact: dict | None = None,
) -> str:
    """HTML for the blocks under the service-time table. Empty if nothing to show.

    `transactions` None means this player has no cached moves. A `compact`
    record is the derived injured-list, move, and season-line data for a
    player whose raw transactions were not stored. Season stats render
    from the player record when neither source has anything else.
    """
    names = team_names or {}
    parts: list[str] = []
    if transactions is not None:
        bounds = windows or _windows()
        stints = build_stints(transactions, bounds, today)
        injury_labels.apply_labels(stints)
        parts.append(_injured_html(stints, names))
        debut, debut_club = _debut(player, transactions)
        closes = {s.end for s in stints if s.kind == "exact" and s.end is not None}
        moves = build_moves(
            transactions,
            debut=debut,
            debut_club_id=debut_club,
            names=names,
            il_close_dates=closes,
        )
        moves_html = _moves_html(moves)
        if moves_html:
            parts.append(moves_html)
    elif compact:
        return _sections_from_compact(player, compact, names)
    stats = _stats_html(player, names)
    if stats:
        # Stats sit between the injured list and the moves.
        if len(parts) == 2:
            parts.insert(1, stats)
        else:
            parts.append(stats)
    return "\n".join(parts)
