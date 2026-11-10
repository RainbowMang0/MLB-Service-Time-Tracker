"""Keep the yearly Rule 5 pages wired into a roster rebuild.

The board is written by write_rule5_pages.py. These helpers only preserve
the link, the files, and the sitemap rows. No eligibility math.
"""

from __future__ import annotations

import html
from pathlib import Path

from mlb_clubs import CLUB_NAMES, CLUB_SHORT


def _short(club: str) -> str:
    for team_id, name in CLUB_NAMES.items():
        if name == club:
            short = CLUB_SHORT.get(team_id, club)
            return "Diamondbacks" if short == "D-backs" else short
    return club


def offseason_link(club: str, slug: str, base: str, club_dir: Path) -> str:
    """A foot-note on the club roster page, once that club's board exists."""
    if not (club_dir / f"{slug}-rule-5.html").exists():
        return ""
    label = html.escape(f"Rule 5 eligible {_short(club)} 2026")
    return (
        f'\n  <h3>Rule 5</h3>\n'
        f'  <p class="foot-note"><a href="{slug}-rule-5.html">{label}</a>\n'
        f'  &middot; <a href="{base}rule-5.html">league-wide</a></p>'
    )


def keep(club_dir: Path, kept: set[Path]) -> None:
    if club_dir.is_dir():
        kept.update(path.resolve() for path in club_dir.glob("*-rule-5.html"))


def add_sitemap(docs: Path, site_url: str, clubs: list[str], slug_of, lastmod, urls: list[str], club_urls: list[str]) -> None:
    league = docs / "rule-5.html"
    if league.is_file():
        rel = "rule-5.html"
        lastmod.record(rel, league.read_text(encoding="utf-8"))
        urls.append(
            f"  <url><loc>{site_url}/{rel}</loc><lastmod>{lastmod.of(rel)}</lastmod>"
            f"<priority>0.9</priority></url>"
        )
    for club in clubs:
        rel = f"t/{slug_of(club)}-rule-5.html"
        path = docs / rel
        if not path.is_file():
            continue
        lastmod.record(rel, path.read_text(encoding="utf-8"))
        club_urls.append(
            f"  <url><loc>{site_url}/{rel}</loc><lastmod>{lastmod.of(rel)}</lastmod>"
            f"<priority>0.7</priority></url>"
        )
