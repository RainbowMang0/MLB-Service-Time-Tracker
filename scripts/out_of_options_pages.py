#!/usr/bin/env python3
"""Write the out-of-options pages from stored transactions.

No API calls. The roster rebuild calls write(). This script can also
print the counts, check the files on disk, or write them on their own.

    python3 scripts/out_of_options_pages.py --report
    python3 scripts/out_of_options_pages.py --check
    python3 scripts/out_of_options_pages.py
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import out_of_options  # noqa: E402
import page_titles  # noqa: E402
import site_tags  # noqa: E402
import table_html  # noqa: E402
import write_player_pages as wpp  # noqa: E402
from out_of_options import Listed, RosterOptions  # noqa: E402

LEAGUE = "out-of-options.html"


def season_year(generated_at: str | None) -> int:
    """The season a roster search is about.

    From October on, that is next year. January through March still belong
    to the season about to start. April through September are the current year.
    """
    day = out_of_options.as_date(generated_at)
    if day.month >= 10:
        return day.year + 1
    return day.year


def _intro_league(count: int, year: int) -> str:
    word = "player" if count == 1 else "players"
    verb = "is" if count == 1 else "are"
    return (
        f"{count} {word} on a 40-man roster {verb} out of minor-league "
        f"options for {year}."
    )


def _intro_club(club: str, count: int, year: int) -> str:
    if count == 0:
        return (
            f"No {club} player on the 40-man roster is out of minor-league "
            f"options for {year}."
        )
    word = "player" if count == 1 else "players"
    verb = "is" if count == 1 else "are"
    return (
        f"{count} {club} {word} on the 40-man roster {verb} out of "
        f"minor-league options for {year}."
    )


def _groups(rows: list[Listed]) -> list[tuple[str, list[Listed]]]:
    by: dict[str, list[Listed]] = {}
    for row in rows:
        by.setdefault(row.player.get("team") or "No club", []).append(row)
    for group in by.values():
        group.sort(key=lambda row: (row.player.get("name") or "").casefold())
    order = out_of_options.club_names()
    names = [team for team in order if team in by]
    names += sorted(team for team in by if team not in names)
    return [(team, by[team]) for team in names]


def _row(row: Listed, base: str) -> str:
    player = row.player
    name = html.escape(player.get("name") or "")
    href = html.escape(f"{base}{wpp.page_path(player)}", quote=True)
    pos = html.escape(player.get("position") or "—")
    seasons = ", ".join(str(year) for year in row.years) or "—"
    service = html.escape(str(player.get("service_time") or "—"))
    return (
        "<tr>"
        f'<td><a href="{href}">{name}</a></td>'
        f"<td>{pos}</td>"
        f'<td class="n">{len(row.years)}</td>'
        f"<td>{html.escape(seasons)}</td>"
        f'<td class="n">{service}</td>'
        "</tr>"
    )


def _may_fourth_note(rows: list[Listed], base: str) -> str:
    """Names are listed only when this page has someone in that spot."""
    if not rows:
        return ""
    links = []
    for row in rows:
        player = row.player
        href = html.escape(f"{base}{wpp.page_path(player)}", quote=True)
        name = html.escape(player.get("name") or "")
        team = player.get("team") or ""
        label = f"{name} ({html.escape(team)})" if team else name
        links.append(f'<a href="{href}">{label}</a>')
    return (
        "<h2>May have a fourth option</h2>"
        "<p>These players used three option years. A fourth year is allowed "
        "when a player has fewer than five full professional seasons, and "
        "the record cannot settle that, so they are not listed as out of options.</p>"
        "<p>" + ", ".join(links) + ".</p>"
    )


def _one_left_note(rows: list[Listed]) -> str:
    if not any(len(row.years) >= 3 for row in rows):
        return ""
    return (
        '<p class="foot-note">Three seasons in this list means one option year '
        "is left. Five full professional seasons were not on the record.</p>"
    )


def _table(rows: list[Listed], base: str) -> str:
    if not rows:
        return "<p>None.</p>"
    body = "".join(_row(row, base) for row in rows)
    return (
        "<table><thead><tr>"
        "<th>Player</th><th>Pos</th>"
        '<th class="n">Years used</th><th>Seasons</th>'
        '<th class="n">Service time</th>'
        f"</tr></thead><tbody>{body}</tbody></table>"
    )


def _note(base: str) -> str:
    href = html.escape(f"{base}service-time.html", quote=True)
    return f'<p class="foot-note"><a href="{href}">How service time is counted</a>.</p>'


def _shell(title: str, desc: str, url: str, crumbs: str, heading: str,
           subtitle: str, body: str, graph: list[dict], depth: str) -> str:
    base = wpp.BASE_PATH
    foot = (
        "Option years and service time are estimates from public data, "
        "not official MLB numbers."
    )
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
<meta property="og:title" content="{html.escape(page_titles.og_title(heading))}" />
<meta property="og:description" content="{html.escape(desc)}" />
<meta property="og:url" content="{html.escape(url)}" />
{wpp._ld_script(graph)}
<link rel="icon" href="{base}favicon.svg" type="image/svg+xml" />
<link rel="stylesheet" href="{depth}styles.css" />
<link rel="stylesheet" href="{depth}page.css" />
<script>
  try {{
    var t = localStorage.getItem("mlb-service-time-theme");
    if (t) document.documentElement.setAttribute("data-theme", t);
  }} catch (e) {{}}
</script>
</head>
<body>
<div class="viz-root">{wpp._site_header(None)}<div class="wrap">
  {crumbs}
  <h1>{html.escape(heading)}</h1>
  <p class="subtitle">{html.escape(subtitle)}</p>
  {_note(base)}
  {body}
  <p class="foot">{html.escape(foot)}</p>
</div>{wpp._site_footer()}{wpp._search_script_tag()}</div>
{wpp.ANALYTICS}
</body>
</html>
"""


def _club_href(team: str, base: str) -> str:
    return html.escape(f"{base}t/{wpp.slug(team)}-out-of-options.html", quote=True)


def render_league(result: RosterOptions, year: int) -> str:
    base = wpp.BASE_PATH
    groups = _groups(result.out)
    parts = []
    for team, rows in groups:
        parts.append(
            f'<h2><a href="{_club_href(team, base)}">{html.escape(team)}</a> '
            f"({len(rows)})</h2>"
        )
        parts.append(_table(rows, base))
    parts.append(_may_fourth_note(result.may_fourth, base))
    items = []
    for team in out_of_options.club_names():
        items.append(f'<li><a href="{_club_href(team, base)}">{html.escape(team)}</a></li>')
    parts.append("<h2>Clubs</h2><ul class=\"sources\">" + "".join(items) + "</ul>")
    url = f"{wpp.SITE_URL}/{LEAGUE}"
    core = page_titles.out_of_options_core(year)
    desc = page_titles.out_of_options_meta(len(result.out), year)
    graph = [
        {"@type": "CollectionPage", "name": core, "url": url, "description": desc},
        wpp._breadcrumb_ld([
            ("Big League Service Time Tracker", f"{wpp.SITE_URL}/"),
            ("Out of options", url),
        ]),
    ]
    return _shell(
        page_titles.out_of_options_title(year),
        desc,
        url,
        wpp._crumbs(("Players", base), ("Out of options", None)),
        f"Players out of options, {year}",
        _intro_league(len(result.out), year),
        "\n".join(parts),
        graph,
        base,
    )


def render_club(
    club: str,
    out_rows: list[Listed],
    one_rows: list[Listed],
    year: int,
    may_rows: list[Listed] | None = None,
) -> str:
    base = wpp.BASE_PATH
    slug = wpp.slug(club)
    out_rows = sorted(out_rows, key=lambda row: (row.player.get("name") or "").casefold())
    one_rows = sorted(one_rows, key=lambda row: (row.player.get("name") or "").casefold())
    body = (
        "<h2>Out of options</h2>"
        + _table(out_rows, base)
        + "<h2>One option year left</h2>"
        + _one_left_note(one_rows)
        + _table(one_rows, base)
        + _may_fourth_note(may_rows or [], base)
        + (
            f'<p class="actions"><a href="{html.escape(slug)}.html">&larr; '
            f"{html.escape(club)} roster</a>"
            f' &middot; <a href="{html.escape(base + LEAGUE, quote=True)}">all clubs</a></p>'
        )
    )
    url = f"{wpp.SITE_URL}/t/{slug}-out-of-options.html"
    core = page_titles.club_out_of_options_core(club, year)
    desc = page_titles.club_out_of_options_meta(club, len(out_rows), len(one_rows), year)
    graph = [
        {"@type": "CollectionPage", "name": core, "url": url, "description": desc},
        wpp._breadcrumb_ld([
            ("Big League Service Time Tracker", f"{wpp.SITE_URL}/"),
            (club, f"{wpp.SITE_URL}/{wpp.club_path(club)}"),
            (f"Out of options, {year}", url),
        ]),
    ]
    return _shell(
        page_titles.club_out_of_options_title(club, year),
        desc,
        url,
        wpp._crumbs(("Players", base), (club, f"{slug}.html"), ("Out of options", None)),
        f"{club} out of options, {year}",
        _intro_club(club, len(out_rows), year),
        body,
        graph,
        "../",
    )


def _for_club(rows: list[Listed], club: str) -> list[Listed]:
    return [row for row in rows if (row.player.get("team") or "") == club]


def render_all(result: RosterOptions, generated_at: str | None) -> dict[str, str]:
    """Raw HTML, before the shared table wrapper and ad slot."""
    year = season_year(generated_at)
    pages = {LEAGUE: render_league(result, year)}
    names = list(out_of_options.club_names())
    extra = sorted({
        (row.player.get("team") or "")
        for row in result.out + result.one
        if (row.player.get("team") or "") not in names
    })
    for club in names + [name for name in extra if name]:
        rel = f"t/{wpp.slug(club)}-out-of-options.html"
        pages[rel] = render_club(
            club,
            _for_club(result.out, club),
            _for_club(result.one, club),
            year,
            _for_club(result.may_fourth, club),
        )
    return pages


def finalize(html_text: str, rel: str) -> str:
    return table_html.wrap_bare_tables(site_tags.place_ads(html_text, rel))


def write(result: RosterOptions, generated_at: str | None, lastmod, publish_page, docs: Path) -> None:
    for rel, page in render_all(result, generated_at).items():
        publish_page(docs / rel, page, lastmod, rel)
    print(out_of_options.report(result), end="")


def _load() -> tuple[list[dict], str]:
    data = json.loads((wpp.DOCS / "data" / "service_time.json").read_text(encoding="utf-8"))
    players = [p for p in data.get("players") or [] if isinstance(p, dict) and p.get("on_40_man")]
    return players, data.get("generated_at") or ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare the pages on disk")
    parser.add_argument("--report", action="store_true", help="print counts and write nothing")
    args = parser.parse_args(argv)
    players, generated_at = _load()
    result = out_of_options.select(players, out_of_options.as_date(generated_at))
    if args.report:
        print(out_of_options.report(result), end="")
        return 0
    pages = render_all(result, generated_at)
    if args.check:
        bad = []
        for rel, page in pages.items():
            path = wpp.DOCS / rel
            disk = path.read_text(encoding="utf-8") if path.is_file() else None
            if disk != finalize(page, rel):
                bad.append(rel)
        if bad:
            print("out-of-options pages are stale:", file=sys.stderr)
            for rel in bad[:12]:
                print(f"  - {rel}", file=sys.stderr)
            if len(bad) > 12:
                print(f"  - and {len(bad) - 12} more", file=sys.stderr)
            return 1
        print("out-of-options pages: ok")
        return 0
    for rel, page in pages.items():
        path = wpp.DOCS / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(finalize(page, rel), encoding="utf-8")
    print(out_of_options.report(result), end="")
    print(f"wrote {len(pages)} out-of-options pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
