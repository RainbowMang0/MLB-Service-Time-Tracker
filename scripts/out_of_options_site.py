"""Links, sitemap rows, and file retention for the out-of-options pages.

The pages themselves are written by out_of_options_pages.py. This module
only keeps those files in a roster rebuild and points existing pages at them.
"""

from __future__ import annotations

import html
from pathlib import Path

LEAGUE = "out-of-options.html"


def club_filename(slug: str) -> str:
    return f"{slug}-out-of-options.html"


def prepare(players, today):
    import out_of_options
    return out_of_options.prepare(players, today)


def clear() -> None:
    import out_of_options
    out_of_options.clear()


def count_for(players, option_counts) -> int:
    import out_of_options
    return out_of_options.count_for(players, option_counts)


def roster_link(club: str, slug: str, base: str, year: int | None) -> str:
    """The club-roster foot-note. The count above it links the club page."""
    when = f" {year}" if year else ""
    label = html.escape(f"{club} out of options{when}")
    href = html.escape(club_filename(slug), quote=True)
    league = html.escape(f"{base}{LEAGUE}", quote=True)
    return (
        "\n  <h3>Out of options</h3>\n"
        f'  <p class="foot-note"><a href="{href}">{label}</a>\n'
        f'  &middot; <a href="{league}">league-wide</a></p>'
    )


def keep(club_dir: Path, kept: set[Path]) -> None:
    if club_dir.is_dir():
        kept.update(path.resolve() for path in club_dir.glob("*-out-of-options.html"))


def add_sitemap(docs: Path, site_url: str, lastmod, urls: list[str], club_urls: list[str]) -> None:
    league = docs / LEAGUE
    if league.is_file():
        rel = LEAGUE
        lastmod.record(rel, league.read_text(encoding="utf-8"))
        urls.append(
            f"  <url><loc>{site_url}/{rel}</loc><lastmod>{lastmod.of(rel)}</lastmod>"
            f"<priority>0.9</priority></url>"
        )
    folder = docs / "t"
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("*-out-of-options.html")):
        rel = f"t/{path.name}"
        lastmod.record(rel, path.read_text(encoding="utf-8"))
        club_urls.append(
            f"  <url><loc>{site_url}/{rel}</loc><lastmod>{lastmod.of(rel)}</lastmod>"
            f"<priority>0.7</priority></url>"
        )
