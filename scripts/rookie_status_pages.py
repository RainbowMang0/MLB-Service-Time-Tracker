#!/usr/bin/env python3
"""Write the rookie-eligibility page from stored season totals and transactions.

No API calls. The roster rebuild calls write(). This script can also
print the counts or check the file on disk.

    python3 scripts/rookie_status_pages.py --report
    python3 scripts/rookie_status_pages.py --check
    python3 scripts/rookie_status_pages.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import page_titles  # noqa: E402
import player_sections  # noqa: E402
import rookie_status as rook  # noqa: E402
import write_player_pages as wpp  # noqa: E402
from mlb_clubs import CLUB_ABBR, CLUB_NAMES  # noqa: E402
from rookie_status import RookieStatus  # noqa: E402
from season_stat_tables import outs_to_ip  # noqa: E402

PATH = "rookie-status.html"
_CLUBS = {name for name, _abbr in CLUB_ABBR}
_CROSSED = {"at_bats": "At-bats", "innings": "Innings", "days": "Active days"}


def _num(value: int | None) -> str:
    return "—" if value is None else str(value)


def _ip(outs: int | None) -> str:
    return "—" if outs is None else outs_to_ip(outs)


def days_text(status: RookieStatus) -> str:
    if status.state == "unconfirmed":
        return "Unconfirmed"
    if status.days_exact is not None:
        return str(status.days_exact)
    if status.days_low is not None and status.days_low > rook.DAY_LIMIT:
        return f"{status.days_low}+"
    if (
        status.days_low is not None
        and status.days_high is not None
        and status.days_high <= rook.DAY_LIMIT
    ):
        return f"{status.days_low}-{status.days_high}"
    return "—"


def _mark(status: RookieStatus) -> str:
    if not status.september_2020:
        return ""
    return ' <span class="thr-note">2020</span>'


def player_line(player: dict, transactions: list | None, today) -> str:
    """One line on a player page, or empty when the status is not supported there."""
    status = rook.evaluate(player, transactions, today)
    show = status.state in ("eligible", "graduated") or (
        status.state == "unconfirmed" and player.get("on_40_man")
    )
    if not show:
        return ""
    href = html.escape(f"{wpp.BASE_PATH}{PATH}", quote=True)
    link = f'<a href="{href}">Rookie eligibility</a>'
    if status.state == "unconfirmed":
        text = f"Rookie status: unconfirmed. {link}."
    elif status.state == "graduated":
        text = (
            f"Rookie eligibility used up in {status.grad_year}: "
            f"{_num(status.at_bats)} AB, {_ip(status.innings_outs)} IP, "
            f"{days_text(status)} active days. {link}."
        )
    else:
        text = (
            f"Rookie eligible: {_num(status.at_bats)} AB, {_ip(status.innings_outs)} IP, "
            f"{days_text(status)} active days. {link}."
        )
    return f'\n  <p class="list-links">{text}</p>'


def _rows(players: list[dict], today, tx_for) -> dict[str, list[tuple[dict, RookieStatus]]]:
    grouped = {"eligible": [], "graduated": [], "unconfirmed": [], "affected": []}
    for player in players:
        if not player.get("on_40_man"):
            continue
        try:
            rows = tx_for(player)
        except Exception:
            rows = None
        status = rook.evaluate(player, rows, today)
        if status.state in grouped:
            grouped[status.state].append((player, status))
        elif status.september_2020:
            grouped["affected"].append((player, status))
    for name in grouped:
        grouped[name].sort(key=lambda item: (
            item[0].get("team") or "",
            (item[0].get("name") or "").casefold(),
        ))
    return grouped


def _player_cell(player: dict, base: str) -> str:
    name = html.escape(player.get("name") or "")
    href = html.escape(f"{base}{wpp.page_path(player)}", quote=True)
    return f'<td><a href="{href}">{name}</a></td>'


def _club_cell(team: str, base: str) -> str:
    if team in _CLUBS:
        href = html.escape(f"{base}{wpp.club_path(team)}", quote=True)
        return f'<td><a href="{href}">{html.escape(team)}</a></td>'
    return f"<td>{html.escape(team) if team else '—'}</td>"


def _eligible_table(rows: list[tuple[dict, RookieStatus]], base: str) -> str:
    body = []
    for player, status in rows:
        note = _mark(status)
        body.append(
            "<tr>"
            f"{_player_cell(player, base)}"
            f"<td>{html.escape(player.get('position') or '—')}</td>"
            f'<td class="n">{_num(status.at_bats)}</td>'
            f'<td class="n">{_ip(status.innings_outs)}</td>'
            f'<td class="n">{html.escape(days_text(status))}{note}</td>'
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>Player</th><th>Pos</th>"
        '<th class="n">AB</th><th class="n">IP</th><th class="n">Active days</th>'
        f"</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def _by_club(rows: list[tuple[dict, RookieStatus]]) -> list[tuple[str, list]]:
    order = [name for name, _abbr in CLUB_ABBR]
    buckets: dict[str, list] = {}
    for item in rows:
        buckets.setdefault(item[0].get("team") or "—", []).append(item)
    names = [team for team in order if team in buckets]
    names += sorted(team for team in buckets if team not in names)
    return [(team, buckets[team]) for team in names]


def _eligible_html(rows: list[tuple[dict, RookieStatus]], base: str) -> str:
    if not rows:
        return "<p>None.</p>"
    parts = []
    for team, group in _by_club(rows):
        if team in _CLUBS:
            href = html.escape(f"{base}{wpp.club_path(team)}", quote=True)
            heading = f'<a href="{href}">{html.escape(team)}</a>'
        else:
            heading = html.escape(team)
        parts.append(f"<h2>{heading} ({len(group)})</h2>")
        parts.append(_eligible_table(group, base))
    return "\n".join(parts)


def _other_table(rows: list[tuple[dict, RookieStatus]], base: str, *, crossed: bool) -> str:
    if not rows:
        return "<p>None.</p>"
    head = (
        "<th>Player</th><th>Club</th><th>Pos</th>"
        '<th class="n">AB</th><th class="n">IP</th><th class="n">Active days</th>'
    )
    if crossed:
        head += "<th>Crossed</th>"
    body = []
    for player, status in rows:
        team = player.get("team") or ""
        limit = ", ".join(_CROSSED[name] for name in status.crossed if name in _CROSSED) or "—"
        cells = (
            "<tr>"
            f"{_player_cell(player, base)}"
            f"{_club_cell(team, base)}"
            f"<td>{html.escape(player.get('position') or '—')}</td>"
            f'<td class="n">{_num(status.at_bats)}</td>'
            f'<td class="n">{_ip(status.innings_outs)}</td>'
            f'<td class="n">{html.escape(days_text(status))}{_mark(status)}</td>'
        )
        if crossed:
            cells += f"<td>{html.escape(limit)}</td>"
        body.append(cells + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _intro(count: int) -> str:
    if count == 0:
        return "No player on a 40-man roster still has rookie eligibility."
    word = "player" if count == 1 else "players"
    verb = "has" if count == 1 else "have"
    return f"{count} {word} on a 40-man roster still {verb} rookie eligibility."


def _note(base: str, affected: list[tuple[dict, RookieStatus]]) -> str:
    service = html.escape(f"{base}service-time.html", quote=True)
    text = (
        "<p class=\"foot-note\">A player remains a rookie until previous seasons exceed "
        "130 at-bats, 50 innings pitched, or 45 days on an active roster, not counting "
        "the injured list. September days count from 2020 on; before 2020 they do not. "
        "A 2020 mark means September 2020 is what pushes the active-day total over 45. "
        f'<a href="{service}">Service time</a> counts injured-list days. '
        "These figures are reconstructed from public data, not an official MLB status.</p>"
    )
    if not affected:
        return text
    links = []
    for player, _status in affected:
        href = html.escape(f"{base}{wpp.page_path(player)}", quote=True)
        team = player.get("team") or ""
        label = html.escape(player.get("name") or "")
        if team:
            label = f"{label} ({html.escape(team)})"
        links.append(f'<a href="{href}">{label}</a>')
    text += (
        "<p class=\"foot-note\">Marked 2020, or listed here, when September 2020 "
        "active days are what puts the player over 45: "
        + ", ".join(links)
        + ".</p>"
    )
    return text


def _today(generated_at: str | None) -> dt.date:
    try:
        return dt.date.fromisoformat((generated_at or "")[:10])
    except ValueError:
        return dt.date.today()


def render(grouped: dict[str, list], generated_at: str | None) -> str:
    base = wpp.BASE_PATH
    windows = player_sections._windows()
    year = rook.rookie_season(_today(generated_at), windows) - 1
    eligible = grouped["eligible"]
    graduated = grouped["graduated"]
    unconfirmed = grouped["unconfirmed"]
    # A row in a table keeps its 2020 mark. The note lists everyone else
    # whose result changes when September 2020 is left out.
    shown = {id(item) for item in eligible + graduated + unconfirmed}
    only_note = [item for item in grouped["affected"] if id(item) not in shown]
    intro = _intro(len(eligible))
    title = page_titles.rookie_title()
    desc = page_titles.rookie_meta(len(eligible), len(graduated), year)
    url = f"{wpp.SITE_URL}/{PATH}"
    graph = [
        {"@type": "CollectionPage", "name": page_titles.rookie_core(), "url": url, "description": desc},
        wpp._breadcrumb_ld([
            ("Big League Service Time Tracker", f"{wpp.SITE_URL}/"),
            ("Rookie eligibility", url),
        ]),
    ]
    grad_word = "player" if len(graduated) == 1 else "players"
    unc_word = "player" if len(unconfirmed) == 1 else "players"
    body = "\n".join([
        _eligible_html(eligible, base),
        f"<h2>Used up in {year}</h2>",
        f"<p>{len(graduated)} {grad_word} on a 40-man roster crossed a rookie limit in {year}.</p>",
        _other_table(graduated, base, crossed=True),
        "<h2>Unconfirmed</h2>",
        (
            f"<p>{len(unconfirmed)} {unc_word} on a 40-man roster whose active-roster days "
            "cannot be reconstructed.</p>"
        ),
        _other_table(unconfirmed, base, crossed=False),
    ])
    also = list_links_line(base)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
{wpp.HEAD_TAGS}
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}" />
<link rel="canonical" href="{html.escape(url)}" />
<meta property="og:type" content="website" />
<meta property="og:title" content="{html.escape(page_titles.og_title(page_titles.rookie_core()))}" />
<meta property="og:description" content="{html.escape(desc)}" />
<meta property="og:url" content="{html.escape(url)}" />
{wpp._ld_script(graph)}
<link rel="icon" href="{base}favicon.svg" type="image/svg+xml" />
<link rel="stylesheet" href="{base}styles.css" />
<link rel="stylesheet" href="{base}page.css" />
<script>
  try {{
    var t = localStorage.getItem("mlb-service-time-theme");
    if (t) document.documentElement.setAttribute("data-theme", t);
  }} catch (e) {{}}
</script>
</head>
<body>
<div class="viz-root">{wpp._site_header(None)}<div class="wrap">
  {wpp._crumbs(("Players", base or "/"), ("Rookie eligibility", None))}
  <h1>Rookie eligibility</h1>
  <p class="subtitle">{html.escape(intro)}</p>
  {_note(base, only_note)}
  {also}
  {body}
  <p class="foot">Rookie eligibility here is reconstructed from public data. It is not an official MLB status.</p>
</div>{wpp._site_footer()}{wpp._search_script_tag()}</div>
{wpp.ANALYTICS}
</body>
</html>
"""


def list_links_line(base: str) -> str:
    import list_links
    return list_links.related_league_line(PATH, base)


def _load() -> tuple[list[dict], str]:
    data = json.loads((wpp.DOCS / "data" / "service_time.json").read_text(encoding="utf-8"))
    players = [p for p in data.get("players") or [] if isinstance(p, dict)]
    return players, data.get("generated_at") or ""


def _tx_for(player: dict):
    return player_sections.load_cached_transactions(player.get("id"))


def grouped_for(players: list[dict], generated_at: str | None, tx_for=None):
    tx_for = tx_for or _tx_for
    return _rows(players, _today(generated_at), tx_for)


def build(players: list[dict], generated_at: str | None, tx_for=None) -> str:
    return render(grouped_for(players, generated_at, tx_for), generated_at)


def _print_grouped(grouped, generated_at: str | None) -> None:
    today = _today(generated_at)
    year = rook.rookie_season(today, player_sections._windows()) - 1
    print(
        f"Rookie eligibility: {len(grouped['eligible'])} eligible, "
        f"{len(grouped['graduated'])} used up in {year}, "
        f"{len(grouped['unconfirmed'])} unconfirmed, "
        f"{len(grouped['affected'])} affected by September 2020"
    )


def write(players, generated_at, lastmod, publish_page, docs: Path) -> None:
    grouped = grouped_for(players, generated_at)
    publish_page(docs / PATH, render(grouped, generated_at), lastmod, PATH)
    _print_grouped(grouped, generated_at)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)
    players, generated_at = _load()
    grouped = grouped_for(players, generated_at)
    if args.report:
        _print_grouped(grouped, generated_at)
        return 0
    import site_tags
    import table_html
    final = table_html.wrap_bare_tables(site_tags.place_ads(render(grouped, generated_at), PATH))
    path = wpp.DOCS / PATH
    if args.check:
        disk = path.read_text(encoding="utf-8") if path.is_file() else ""
        if disk != final:
            print("rookie-status.html is stale", file=sys.stderr)
            return 1
        print("rookie-status.html: ok")
        return 0
    path.write_text(final, encoding="utf-8")
    _print_grouped(grouped, generated_at)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
