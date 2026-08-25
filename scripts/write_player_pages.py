#!/usr/bin/env python3
"""
write_player_pages.py
---------------------
A real, crawlable HTML page per player.

WHY THIS EXISTS
===============
The site is one page with hash routing: a profile lives at
`/#player/592450`. Search engines do not index hash fragments as separate
pages, so nobody could ever reach a player by searching "Aaron Judge service
time" -- which is exactly how someone would look for this. Every visitor had
to arrive at the table and know to search it.

These pages fix that, and they are not a second implementation of the site:
the content is rendered from the same records, at build time, into static
files that need no JavaScript at all. A crawler sees the figure and the
season table in the HTML.

The usual single-page trick -- path routing with a 404.html shim -- does NOT
work on GitHub Pages, which returns a real 404 status for unknown paths.
Search engines will not index a 404. Static files are the only thing that
actually works here.

ROSTERED PLAYERS ONLY, for now. 1,355 pages is about 12 MB committed; all
5,570 would be nearer 45 MB, on top of a 17 MB transaction cache and an
8.8 MB database. Retired players are also far less searched. Widening it
later is a one-line change to `_should_publish`.

Each page carries a canonical URL, an og: card, and a link back to the full
table. The overlay in app.js still works for anyone browsing the table, so
these are an addition rather than a replacement.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import pathlib
import re
import unicodedata
import shutil

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
PAGE_DIR = DOCS / "p"
SITE_URL = "https://rainbowmang0.github.io/MLB-Service-Time-Tracker"

FULL_YEAR_DAYS = 172

SOURCE_LABEL = {
    "read": "From transactions",
    "carry": "Carried forward",
    "presumed": "Presumed from debut",
}


# Characters NFKD does not decompose into letter + combining mark. Without
# these, "Jose Berrios" is fine but a slug for a player whose name carries one
# would drop the letter entirely rather than transliterate it.
_TRANSLITERATE = str.maketrans({
    "\u00f8": "o", "\u00d8": "O",   # o-slash
    "\u0142": "l", "\u0141": "L",   # l-stroke
    "\u00e6": "ae", "\u00c6": "AE",
    "\u00df": "ss",
    "\u0111": "d", "\u0110": "D",
    "\u00fe": "th", "\u00de": "Th",
})


def slug(name: str) -> str:
    """A stable, readable URL fragment. The id is what identifies a player.

    Accents are transliterated rather than dropped: a quarter of this league
    has one, and "adolis-garc-a" is a worse URL than "adolis-garcia" for both
    a reader and a search engine. NFKD splits an accented letter into letter
    plus combining mark, so discarding the marks leaves the ASCII letter
    behind; _TRANSLITERATE covers the handful NFKD will not split.
    """
    folded = unicodedata.normalize("NFKD", (name or "").translate(_TRANSLITERATE))
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")
    return s or "player"


def page_path(player: dict) -> str:
    return f"p/{player['id']}-{slug(player.get('name'))}.html"


def _should_publish(player: dict) -> bool:
    # Rostered players only -- see the module docstring.
    return bool(player.get("on_40_man"))


def _fmt(days: int) -> str:
    return f"{days // FULL_YEAR_DAYS}.{days % FULL_YEAR_DAYS:03d}"


def _status(player: dict) -> str:
    if not player.get("on_40_man"):
        return "No longer on a 40-man roster"
    if not player.get("service_days_total"):
        return "Yet to accrue a day of major league service"
    if player.get("free_agent_eligible"):
        return "Free agency eligible"
    if player.get("super_two_candidate"):
        return "On the Super Two track"
    if player.get("arbitration_eligible"):
        return "Arbitration eligible"
    return "Pre-arbitration"


def _description(player: dict) -> str:
    name = player.get("name") or "This player"
    service = player.get("service_time") or "0.000"
    club = player.get("team") or "his club"
    return (
        f"{name} has an estimated {service} years of major league service time "
        f"with {club} — {player.get('service_days_total', 0)} days credited. "
        f"{_status(player)}. Reconstructed from public roster transactions; "
        "not an official MLB/MLBPA figure."
    )


def _season_rows(player: dict, team_names: dict[int, str]) -> str:
    rows = []
    running = 0
    for season in player.get("seasons") or []:
        days = int(season.get("d") or 0)
        running += days
        if days == 0 and not season.get("t"):
            continue  # evidence-free padding; see trimLeadingEmpty in app.js
        clubs = ", ".join(
            html.escape(team_names.get(int(t), f"Club {t}")) for t in (season.get("t") or [])
        )
        rows.append(
            f"<tr><td>{season['y']}</td><td>{clubs or '—'}</td>"
            f"<td class='n'>{days}</td><td class='n'>{_fmt(running)}</td>"
            f"<td>{SOURCE_LABEL.get(season.get('src'), 'From transactions')}</td></tr>"
        )
    if not rows:
        return "<p class='empty'>No credited seasons on record.</p>"
    return (
        "<table><thead><tr><th>Season</th><th>Club</th><th>Days</th>"
        "<th>Running total</th><th>How this season is known</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _jsonld(player: dict, name: str, url: str, service: str) -> str:
    """Structured data for the page.

    Deliberately conservative. schema.org has no vocabulary for service time,
    so it goes in as a named `PropertyValue` rather than being forced into a
    field that means something else -- a search engine that does not
    understand it ignores it, which is the correct outcome, and one that does
    is not misled about what it is.

    The `description` repeats the estimate caveat for the same reason the
    visible page does: this markup can be surfaced on its own.
    """
    props = [{
        "@type": "PropertyValue",
        "name": "Estimated MLB service time",
        "value": service,
        "description": (
            f"{player.get('service_days_total', 0)} days credited, estimated from "
            "public roster transactions. Not an official MLB/MLBPA figure."
        ),
    }]
    data = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": player.get("name") or "Player",
        "url": url,
        "additionalProperty": props,
    }
    if player.get("team"):
        data["affiliation"] = {"@type": "SportsTeam", "name": player["team"]}
    if player.get("position"):
        data["jobTitle"] = player["position"]
    # </script> inside a JSON string would end the block early; escape the slash.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/ld+json">{payload}</script>'


def render(player: dict, team_names: dict[int, str], generated_at: str) -> str:
    name = html.escape(player.get("name") or "Player")
    service = html.escape(player.get("service_time") or "0.000")
    url = f"{SITE_URL}/{page_path(player)}"
    desc = html.escape(_description(player))
    club = html.escape(player.get("team") or "")
    position = html.escape(player.get("position") or "")
    debut = html.escape(player.get("mlb_debut") or "—")
    missing = int(player.get("missing_seasons") or 0)

    jsonld = _jsonld(player, name, url, service)

    caveat = ""
    if missing:
        caveat = (
            f"<p class='caveat'>{missing} season{'s' if missing != 1 else ''} of his career "
            "are presumed from his debut date rather than read from transactions, so this "
            "figure is less certain than most.</p>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{name} — service time | Big League Service Time Tracker</title>
<meta name="description" content="{desc}" />
<link rel="canonical" href="{url}" />
<meta property="og:type" content="profile" />
<meta property="og:title" content="{name} — estimated service time {service}" />
<meta property="og:description" content="{desc}" />
<meta property="og:url" content="{url}" />
<meta name="twitter:card" content="summary" />
{jsonld}
<link rel="stylesheet" href="../styles.css" />
<script>
  /* Carry the theme the visitor chose on the main table. In <head> and inline
     so it runs before first paint -- deferred, it would flash the wrong theme.
     styles.css already honours prefers-color-scheme on its own; this is only
     for an explicit override. */
  try {{
    var t = localStorage.getItem("mlb-service-time-theme");
    if (t) document.documentElement.setAttribute("data-theme", t);
  }} catch (e) {{}}
</script>
<style>
  .wrap {{ max-width: 60rem; margin: 0 auto; padding: 24px clamp(16px,4vw,40px) 48px; }}
  .big {{ font-size: 2.6rem; font-weight: 700; letter-spacing: -0.02em; line-height: 1; }}
  .facts {{ display: flex; flex-wrap: wrap; gap: 1.75rem; margin: 1rem 0 1.4rem;
           padding: 0.9rem 0; border-top: 1px solid var(--gridline);
           border-bottom: 1px solid var(--gridline); }}
  .facts div span {{ display: block; font-size: 0.7rem; text-transform: uppercase;
                    letter-spacing: 0.05em; color: var(--text-muted); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid var(--gridline); }}
  td.n, th.n {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .caveat {{ background: var(--status-warning-wash); color: var(--text-secondary);
            padding: 0.7rem 0.85rem; border-radius: 6px; font-size: 0.85rem; }}
  .back {{ display: inline-block; margin-bottom: 1rem; color: var(--accent);
          text-decoration: none; }}
  .foot {{ margin-top: 1.5rem; font-size: 0.8rem; color: var(--text-muted); line-height: 1.6; }}
</style>
</head>
<body>
<div class="viz-root"><div class="wrap">
  <a class="back" href="../index.html">← All players</a>
  <h1>{name}</h1>
  <p class="subtitle">{club}{' · ' if club and position else ''}{position} · {html.escape(_status(player))}</p>

  <div class="facts">
    <div><span>Service time</span><b class="big">{service}</b></div>
    <div><span>Days credited</span>{player.get('service_days_total', 0)}</div>
    <div><span>MLB debut</span>{debut}</div>
  </div>

  {caveat}
  <h2>Season by season</h2>
  {_season_rows(player, team_names)}

  <p class="foot">
    172 days credit a full year, so a season adds at most 1.000 no matter how
    long a player is on a roster. 3.000 years reaches salary arbitration and
    6.000 reaches free agency.
    <br /><br />
    This figure is an <strong>estimate</strong> reconstructed from public roster
    transaction records. It is not an official MLB or MLBPA figure — those are
    not published. See the
    <a href="https://github.com/RainbowMang0/MLB-Service-Time-Tracker#readme">methodology</a>.
    Last updated {html.escape(generated_at[:10])}.
    <br /><br />
    Independent project, not affiliated with or endorsed by Major League
    Baseball or the MLBPA.
  </p>
</div></div>
</body>
</html>
"""


def write_player_pages(db: dict[str, dict], generated_at: str | None = None) -> list[dict]:
    """Regenerate docs/p/ from scratch and return the players published."""
    generated_at = generated_at or dt.datetime.now(dt.timezone.utc).isoformat()

    team_names: dict[int, str] = {}
    for player in db.values():
        if player.get("team_id") and player.get("team"):
            team_names[int(player["team_id"])] = player["team"]

    published = sorted(
        (p for p in db.values() if _should_publish(p)),
        key=lambda p: p.get("name") or "",
    )

    # Rebuilt wholesale: a player who drops off a 40-man must lose his page,
    # not keep serving a stale one.
    if PAGE_DIR.exists():
        shutil.rmtree(PAGE_DIR)
    PAGE_DIR.mkdir(parents=True, exist_ok=True)

    for player in published:
        (DOCS / page_path(player)).write_text(render(player, team_names, generated_at))

    _write_sitemap(published, generated_at)
    _write_robots()
    total_kb = sum((DOCS / page_path(p)).stat().st_size for p in published) / 1024
    print(
        f"Wrote {len(published)} player pages ({total_kb / 1024:.1f} MB) to {PAGE_DIR}"
    )
    return published


def _write_sitemap(published: list[dict], generated_at: str) -> None:
    day = generated_at[:10]
    urls = [f"  <url><loc>{SITE_URL}/</loc><lastmod>{day}</lastmod><priority>1.0</priority></url>"]
    urls += [
        f"  <url><loc>{SITE_URL}/{page_path(p)}</loc><lastmod>{day}</lastmod></url>"
        for p in published
    ]
    (DOCS / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )


def _write_robots() -> None:
    (DOCS / "robots.txt").write_text(
        "User-agent: *\n"
        "Allow: /\n"
        "# The data files are for the site's own use; there is nothing to index in them.\n"
        "Disallow: /data/\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n"
    )


if __name__ == "__main__":
    data = json.loads((DOCS / "data" / "service_time.json").read_text())
    write_player_pages(
        {str(p["id"]): p for p in data["players"]}, data.get("generated_at")
    )
