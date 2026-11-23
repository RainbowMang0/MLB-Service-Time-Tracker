#!/usr/bin/env python3
"""Write the debuts page from stored debut dates and season totals.

No API calls. The roster rebuild calls write(). This script can also
print the counts or check the file on disk.

    python3 scripts/debuts_pages.py --report
    python3 scripts/debuts_pages.py --check
    python3 scripts/debuts_pages.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import list_links  # noqa: E402
import page_titles  # noqa: E402
import player_sections  # noqa: E402
import rookie_status as rook  # noqa: E402
import site_tags  # noqa: E402
import table_html  # noqa: E402
import write_player_pages as wpp  # noqa: E402
from il_stints import SeasonWindows  # noqa: E402
from mlb_clubs import CLUB_ABBR, MLB_CLUB_IDS, club_name  # noqa: E402
from season_stat_tables import PITCHER_POSITIONS, ip_to_outs, outs_to_ip  # noqa: E402

PATH = "debuts.html"
_CLUBS = {name for name, _abbr in CLUB_ABBR}
_MONTHS = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


@dataclass
class Debut:
    player_id: int
    name: str
    debut: dt.date
    club: str
    position: str
    totals: str
    rookie: str
    href: str


def _windows() -> SeasonWindows:
    return player_sections._windows()


def _bounds(windows: SeasonWindows, year: int) -> tuple[dt.date, dt.date] | None:
    row = windows.years.get(year)
    if not row:
        return None
    start = row["start"]
    end = row["end"]
    clubs = row.get("clubs") or {}
    ends = row.get("ends") or {}
    if clubs:
        start = min(start, min(clubs.values()))
    if ends:
        end = max(end, max(ends.values()))
    return start, end


def season_year(today: dt.date, windows: SeasonWindows | None = None) -> int:
    """The season underway, or the one that just ended."""
    windows = windows or _windows()
    bounds = _bounds(windows, today.year)
    if bounds is not None and today >= bounds[0]:
        return today.year
    if today.year - 1 in windows.years:
        return today.year - 1
    return today.year


def in_season(day: dt.date, year: int, windows: SeasonWindows | None = None) -> bool:
    windows = windows or _windows()
    bounds = _bounds(windows, year)
    if bounds is None:
        return day.year == year
    return bounds[0] <= day <= bounds[1]


def _as_date(value) -> dt.date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _today(generated_at: str | None) -> dt.date:
    try:
        return dt.date.fromisoformat((generated_at or "")[:10])
    except ValueError:
        return dt.date.today()


def long_date(day: dt.date) -> str:
    return f"{_MONTHS[day.month]} {day.day}, {day.year}"


def _int_field(row: dict, key: str) -> int | None:
    value = row.get(key)
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _splits(group: dict) -> list[dict]:
    return [row for row in (group.get("splits") or []) if isinstance(row, dict)]


def _sum_field(group: dict, key: str) -> int | None:
    total = _int_field(group, key)
    if total is not None:
        return total
    nums = [_int_field(row, key) for row in _splits(group)]
    found = [n for n in nums if n is not None]
    if not found:
        return None
    return sum(found)


def _innings(group: dict) -> str | None:
    raw = group.get("inningsPitched")
    if raw not in (None, ""):
        text = str(raw).strip()
        outs = ip_to_outs(text)
        if outs is not None:
            return text if "." in text else outs_to_ip(outs)
    outs = 0
    saw = False
    for row in _splits(group):
        part = ip_to_outs(row.get("inningsPitched"))
        if part is None:
            continue
        saw = True
        outs += part
    if not saw:
        return None
    return outs_to_ip(outs)


def _mlb_id(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value not in MLB_CLUB_IDS:
        return None
    return value


def _first_club(group: dict) -> int | None:
    for row in _splits(group):
        tid = _mlb_id(row.get("team_id"))
        if tid is not None:
            return tid
    return _mlb_id(group.get("team_id"))


def _year_groups(player: dict, year: int) -> tuple[dict, dict]:
    stats = player.get("season_stats") if isinstance(player.get("season_stats"), dict) else {}
    rec = stats.get(str(year))
    if rec is None:
        rec = stats.get(year)
    if not isinstance(rec, dict):
        rec = {}
    hit = rec.get("hitting") if isinstance(rec.get("hitting"), dict) else {}
    pit = rec.get("pitching") if isinstance(rec.get("pitching"), dict) else {}
    return hit, pit


def _fallback_club(player: dict, year: int) -> int | None:
    teams: list[int] = []
    for season in player.get("seasons") or []:
        if not isinstance(season, dict):
            continue
        try:
            season_year_i = int(season.get("y"))
        except (TypeError, ValueError):
            continue
        if season_year_i != year:
            continue
        for tid in season.get("t") or []:
            club = _mlb_id(tid)
            if club is not None and club not in teams:
                teams.append(club)
    if len(teams) == 1:
        return teams[0]
    current = _mlb_id(player.get("team_id"))
    if current is not None and (not teams or current in teams):
        return current
    return None


def season_summary(player: dict, year: int) -> tuple[str, int | None]:
    """'12 G, 40 AB' or '8 G, 12.1 IP', and the first club he played for."""
    hit, pit = _year_groups(player, year)
    pos = (player.get("position") or "").strip().upper()
    innings = _innings(pit)
    at_bats = _sum_field(hit, "atBats")
    pitcher = pos in PITCHER_POSITIONS
    if pitcher and (innings is not None or _sum_field(pit, "gamesPlayed") is not None):
        group, kind = pit, "ip"
    elif not pitcher and (at_bats is not None or _sum_field(hit, "gamesPlayed") is not None):
        group, kind = hit, "ab"
    elif innings is not None:
        group, kind = pit, "ip"
    elif at_bats is not None:
        group, kind = hit, "ab"
    else:
        return "", _fallback_club(player, year)
    bits = []
    games = _sum_field(group, "gamesPlayed")
    if games is not None:
        bits.append(f"{games} G")
    if kind == "ip" and innings is not None:
        bits.append(f"{innings} IP")
    elif kind == "ab" and at_bats is not None:
        bits.append(f"{at_bats} AB")
    club = _first_club(group) or _fallback_club(player, year)
    return ", ".join(bits), club


def rookie_phrase(player: dict, rows: list | None, today: dt.date) -> str:
    """The status the rookie page would show, or nothing when it would not."""
    status = rook.evaluate(player, rows, today)
    show = status.state in ("eligible", "graduated") or (
        status.state == "unconfirmed" and player.get("on_40_man")
    )
    if not show:
        return ""
    if status.state == "unconfirmed":
        return "Rookie status unconfirmed"
    if status.state == "graduated":
        return "Rookie eligibility used up"
    return "Rookie eligible"


def _tx_for(player: dict):
    try:
        return player_sections.load_cached_transactions(player.get("id"))
    except Exception:
        return None


def _href(player: dict, docs: Path) -> str:
    try:
        rel = wpp.page_path(player)
    except Exception:
        return ""
    if (docs / rel).is_file():
        return f"{wpp.BASE_PATH}{rel}"
    return ""


def _club_label(club_id: int | None, player: dict) -> str:
    if club_id is not None:
        return club_name(club_id)
    stored = " ".join(str(player.get("team") or "").split())
    if stored in _CLUBS:
        return stored
    return ""


def collect(
    players: list[dict],
    today: dt.date,
    tx_for=None,
    windows: SeasonWindows | None = None,
    docs: Path | None = None,
) -> list[Debut]:
    windows = windows or _windows()
    docs = docs or wpp.DOCS
    tx_for = tx_for or _tx_for
    year = season_year(today, windows)
    rows: list[Debut] = []
    for player in players:
        if not isinstance(player, dict):
            continue
        debut = _as_date(player.get("mlb_debut"))
        if debut is None or not in_season(debut, year, windows):
            continue
        try:
            pid = int(player.get("id"))
        except (TypeError, ValueError):
            continue
        totals, club_id = season_summary(player, year)
        try:
            phrase = rookie_phrase(player, tx_for(player), today)
        except Exception:
            phrase = ""
        rows.append(Debut(
            player_id=pid,
            name=" ".join(str(player.get("name") or "Player").split()) or "Player",
            debut=debut,
            club=_club_label(club_id, player),
            position=str(player.get("position") or "").strip(),
            totals=totals,
            rookie=phrase,
            href=_href(player, docs),
        ))
    rows.sort(key=lambda row: (-row.debut.toordinal(), row.name.casefold(), row.player_id))
    return rows


def player_line(player: dict, today: dt.date, windows: SeasonWindows | None = None) -> str:
    """One line on a player page when this page lists his debut."""
    windows = windows or _windows()
    debut = _as_date(player.get("mlb_debut"))
    if debut is None:
        return ""
    year = season_year(today, windows)
    if not in_season(debut, year, windows):
        return ""
    href = html.escape(f"{wpp.BASE_PATH}{PATH}", quote=True)
    label = html.escape(long_date(debut))
    return f'\n  <p class="list-links">Debuted <a href="{href}">{label}</a>.</p>'


def _intro(count: int, year: int) -> str:
    if count == 0:
        return f"No player made his major league debut in the {year} season."
    if count == 1:
        return f"1 player made his major league debut in the {year} season."
    return f"{count} players made their major league debut in the {year} season."


def _note(year: int, base: str) -> str:
    href = html.escape(f"{base}rookie-status.html", quote=True)
    return (
        f'<p class="foot-note">Newest first. The club is the one he first played for in {year}. '
        "The line under each name is that season's games and at-bats, or innings for a pitcher. "
        f'<a href="{href}">Rookie eligibility</a> is shown when it can be reconstructed.</p>'
    )


def _counts(rows: list[Debut]) -> list[tuple[str, int]]:
    order = [name for name, _abbr in CLUB_ABBR]
    buckets: dict[str, int] = {}
    for row in rows:
        if not row.club:
            continue
        buckets[row.club] = buckets.get(row.club, 0) + 1
    names = [name for name in order if name in buckets]
    names += sorted(name for name in buckets if name not in names)
    return [(name, buckets[name]) for name in names]


def _club_anchor(name: str, label: str, base: str) -> str:
    text = html.escape(label)
    if name in _CLUBS:
        href = html.escape(f"{base}{wpp.club_path(name)}", quote=True)
        return f'<a href="{href}">{text}</a>'
    return text


def _count_nav(rows: list[Debut], base: str) -> str:
    counts = _counts(rows)
    if not counts:
        return ""
    links = [_club_anchor(name, f"{name} ({count})", base) for name, count in counts]
    return (
        '<nav class="list-links debut-clubs" aria-label="Debuts by club">'
        + " · ".join(links)
        + "</nav>"
    )


def _detail(row: Debut, base: str) -> str:
    parts = []
    if row.totals:
        parts.append(html.escape(row.totals) + ".")
    if row.rookie:
        href = html.escape(f"{base}rookie-status.html", quote=True)
        parts.append(f'<a href="{href}">{html.escape(row.rookie)}</a>.')
    return " ".join(parts)


def _player_cell(row: Debut, base: str) -> str:
    name = html.escape(row.name)
    if row.href:
        href = html.escape(row.href, quote=True)
        who = f'<a href="{href}">{name}</a>'
    else:
        who = name
    detail = _detail(row, base)
    if detail:
        who += f'<span class="debut-line">{detail}</span>'
    return f"<td>{who}</td>"


def _table(rows: list[Debut], base: str) -> str:
    if not rows:
        return "<p>None.</p>"
    body = []
    for row in rows:
        club = _club_anchor(row.club, row.club, base) if row.club else "—"
        body.append(
            "<tr>"
            f"{_player_cell(row, base)}"
            f'<td class="when"><time datetime="{row.debut.isoformat()}">{html.escape(long_date(row.debut))}</time></td>'
            f'<td class="club">{club}</td>'
            f"<td>{html.escape(row.position or '—')}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>Player</th><th>Debut</th><th>Club</th><th>Pos</th>"
        f"</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def render(rows: list[Debut], year: int) -> str:
    base = wpp.BASE_PATH
    count = len(rows)
    intro = _intro(count, year)
    title = page_titles.debuts_title(year)
    desc = page_titles.debuts_meta(count, year)
    url = f"{wpp.SITE_URL}/{PATH}"
    graph = [
        {"@type": "CollectionPage", "name": page_titles.debuts_core(year), "url": url, "description": desc},
        wpp._breadcrumb_ld([
            ("Big League Service Time Tracker", f"{wpp.SITE_URL}/"),
            (f"{year} debuts", url),
        ]),
    ]
    also = list_links.related_league_line(PATH, base)
    body = "\n".join([
        _note(year, base),
        also,
        _count_nav(rows, base),
        _table(rows, base),
    ])
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
<meta property="og:title" content="{html.escape(page_titles.og_title(page_titles.debuts_core(year)))}" />
<meta property="og:description" content="{html.escape(desc)}" />
<meta property="og:url" content="{html.escape(url)}" />
{wpp._ld_script(graph)}
<link rel="icon" href="{base}favicon.svg" type="image/svg+xml" />
<link rel="stylesheet" href="{base}styles.css" />
<link rel="stylesheet" href="{base}page.css" />
<link rel="stylesheet" href="{base}debuts.css" />
<script>
  try {{
    var t = localStorage.getItem("mlb-service-time-theme");
    if (t) document.documentElement.setAttribute("data-theme", t);
  }} catch (e) {{}}
</script>
</head>
<body>
<div class="viz-root">{wpp._site_header(None)}<div class="wrap debuts">
  {wpp._crumbs(("Players", base or "/"), (f"{year} debuts", None))}
  <h1>{year} debuts</h1>
  <p class="subtitle">{html.escape(intro)}</p>
  {body}
  <p class="foot">Debut dates, clubs, and season totals are from public data. Rookie eligibility is not an official MLB status.</p>
</div>{wpp._site_footer()}{wpp._search_script_tag()}</div>
{wpp.ANALYTICS}
</body>
</html>
"""


def final_html(rows: list[Debut], year: int) -> str:
    return table_html.wrap_bare_tables(site_tags.place_ads(render(rows, year), PATH))


def _load() -> tuple[list[dict], str]:
    data = json.loads((wpp.DOCS / "data" / "service_time.json").read_text(encoding="utf-8"))
    players = [p for p in data.get("players") or [] if isinstance(p, dict)]
    return players, data.get("generated_at") or ""


def _print_report(rows: list[Debut], year: int) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        if row.club:
            counts[row.club] = counts.get(row.club, 0) + 1
    print(f"Debuts in {year}: {len(rows)}")
    if counts:
        low = min(counts.values())
        high = max(counts.values())
        low_names = ", ".join(sorted(name for name, n in counts.items() if n == low))
        high_names = ", ".join(sorted(name for name, n in counts.items() if n == high))
        print(f"By club: {low} ({low_names}) to {high} ({high_names}), {len(counts)} clubs")
    missing_page = sum(1 for row in rows if not row.href)
    missing_rookie = sum(1 for row in rows if not row.rookie)
    missing_totals = sum(1 for row in rows if not row.totals)
    print(
        f"Without a player page: {missing_page}. "
        f"Without a rookie status: {missing_rookie}. "
        f"Without season totals: {missing_totals}."
    )
    if not rows:
        return
    picks = [rows[0], rows[len(rows) // 2], rows[-1]]
    seen: set[int] = set()
    print("Examples:")
    for row in picks:
        if row.player_id in seen:
            continue
        seen.add(row.player_id)
        print(f"  {row.name} — {row.debut.isoformat()} — {row.club or '—'}")


def write(players, generated_at, lastmod, publish_page, docs: Path) -> None:
    today = _today(generated_at)
    windows = _windows()
    year = season_year(today, windows)
    rows = collect(players, today, windows=windows, docs=docs)
    publish_page(docs / PATH, render(rows, year), lastmod, PATH)
    _print_report(rows, year)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)
    players, generated_at = _load()
    today = _today(generated_at)
    year = season_year(today)
    rows = collect(players, today)
    if args.report:
        _print_report(rows, year)
        return 0
    final = final_html(rows, year)
    path = wpp.DOCS / PATH
    if args.check:
        disk = path.read_text(encoding="utf-8") if path.is_file() else ""
        if disk != final:
            print("debuts.html is stale", file=sys.stderr)
            return 1
        print("debuts.html: ok")
        return 0
    path.write_text(final, encoding="utf-8")
    _print_report(rows, year)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
