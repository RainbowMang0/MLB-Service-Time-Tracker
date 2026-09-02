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
import hashlib
import html
import json
import pathlib
import re
import unicodedata
import urllib.parse
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cba  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
PAGE_DIR = DOCS / "p"
CLUB_DIR = DOCS / "t"

DEFAULT_SITE_URL = "https://rainbowmang0.github.io/MLB-Service-Time-Tracker"


def _site_url() -> str:
    """Where this site is served from.

    Read from docs/CNAME when it exists, because that is the file GitHub Pages
    itself reads to decide the domain -- so the two can never disagree. Moving
    to a custom domain is then exactly one action, `echo example.com >
    docs/CNAME`, with no source edit to forget: every canonical URL, og:url,
    sitemap entry and JSON-LD url below is derived from this.

    A CNAME holds a bare hostname, never a scheme or a path. GitHub Pages
    serves a custom domain from the root, which is why _base_path() collapses
    to "/" for one and stays "/MLB-Service-Time-Tracker/" without.
    """
    cname = DOCS / "CNAME"
    if cname.exists():
        host = cname.read_text().strip().splitlines()[0].strip().rstrip("/")
        if host:
            return f"https://{host}"
    return DEFAULT_SITE_URL


def _base_path(site_url: str) -> str:
    """The absolute path prefix the site is served under, with a trailing slash.

    404.html needs this: GitHub Pages serves it for unknown paths anywhere on
    the site, including under /p/, so its links cannot be relative. A custom
    domain is served from the root, so this collapses to "/" the moment
    docs/CNAME appears.
    """
    path = urllib.parse.urlsplit(site_url).path.rstrip("/")
    return f"{path}/" if path else "/"


SITE_URL = _site_url()
BASE_PATH = _base_path(SITE_URL)

# From the CBA ruleset, not a literal -- see scripts/cba.py. This file draws
# the same service-time meter as docs/app.js, so the two must agree on what a
# full year is, and the only way to guarantee that is for both to read it from
# config/cba/ rather than each keeping a copy.
_RULES = cba.default()
FULL_YEAR_DAYS = _RULES.require("service_time.days_per_credited_year")
FREE_AGENCY_YEARS = _RULES.require("free_agency.credited_years_required")

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


def _plain_figure(service: str) -> str:
    """"21.075" -> "21 years and 75 days".

    The stored figure is the industry's Y.DDD notation, which is precise and
    which nobody outside baseball reads on sight -- and "service time" pages
    are found by people who have just learned the term. Spelling it out once,
    in the sentence under the heading, is what makes the page answer the
    question it was searched for.
    """
    years, _, days = str(service or "0.000").partition(".")
    try:
        y, d = int(years), int(days or 0)
    except ValueError:
        return f"{service} years"
    yl = f"{y} year{'' if y == 1 else 's'}"
    dl = f"{d} day{'' if d == 1 else 's'}"
    if y and d:
        return f"{yl} and {dl}"
    return dl if not y else yl


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


CLUB_DIR_NAME = "t"


def club_path(club: str) -> str:
    return f"{CLUB_DIR_NAME}/{slug(club)}.html"


def _crumbs(*trail: tuple[str, str | None]) -> str:
    """Breadcrumb markup. Each item is (label, href); href None = current page."""
    parts = []
    for label, href in trail:
        parts.append(f'<a href="{href}">{label}</a>' if href else f"<b>{label}</b>")
    return '<nav class="crumbs">' + '<span>/</span>'.join(parts) + "</nav>"


def _breadcrumb_ld(trail: list[tuple[str, str]]) -> dict:
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name, "item": url}
            for i, (name, url) in enumerate(trail, start=1)
        ],
    }


def _ld_script(graph: list[dict]) -> str:
    payload = json.dumps(
        {"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False
    ).replace("</", "<\\/")  # </script> in a string would end the block early
    return f'<script type="application/ld+json">{payload}</script>'


def render_club_index(by_club: dict[str, list[dict]], generated_at: str) -> str:
    """The club directory at /t/.

    This exists so the club hubs are reachable from the homepage in ONE hop.
    They were only reachable through a player page, which meant a crawler
    landing on the index had to go index -> player -> club to find them --
    exactly backwards for pages meant to be section hubs.

    It is also the one link index.html has to carry by hand. A generated
    directory page means that link is "t/" and never changes; hand-writing 30
    club links there would put 30 slugs in a hand-maintained file, and a club
    rename would silently 404 one of them.
    """
    url = f"{SITE_URL}/{CLUB_DIR_NAME}/"
    total = sum(len(v) for v in by_club.values())
    desc = (
        f"Estimated major league service time for all {total} players on a 40-man "
        f"roster, by club — who reaches free agency, who reaches arbitration, and "
        "who is on the Super Two track. Reconstructed from public roster "
        "transactions; not an official MLB/MLBPA figure."
    )

    rows = "".join(
        f"<tr><td><a href=\"{slug(club)}.html\">{html.escape(club)}</a></td>"
        f"<td class='n'>{len(players)}</td>"
        f"<td class='n'>{sum(1 for p in players if p.get('free_agent_eligible'))}</td>"
        f"<td class='n'>{sum(1 for p in players if p.get('arbitration_eligible'))}</td>"
        f"<td class='n'>{sum(1 for p in players if p.get('super_two_candidate'))}</td></tr>"
        for club, players in sorted(by_club.items())
    )

    graph = [
        {"@type": "CollectionPage", "name": "Service time by club", "url": url,
         "description": desc},
        _breadcrumb_ld([
            ("Big League Service Time Tracker", f"{SITE_URL}/"),
            ("By club", url),
        ]),
    ]

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Service time by club — all 30 teams | Big League Service Time Tracker</title>
<meta name="description" content="{html.escape(desc)}" />
<link rel="canonical" href="{url}" />
<meta property="og:type" content="website" />
<meta property="og:title" content="Service time by club — all 30 teams" />
<meta property="og:description" content="{html.escape(desc)}" />
<meta property="og:url" content="{url}" />
<meta name="twitter:card" content="summary" />
{_ld_script(graph)}
<link rel="icon" href="{BASE_PATH}favicon.svg" type="image/svg+xml" />
<link rel="stylesheet" href="../styles.css" />
<link rel="stylesheet" href="../page.css" />
<script>
  try {{
    var t = localStorage.getItem("mlb-service-time-theme");
    if (t) document.documentElement.setAttribute("data-theme", t);
  }} catch (e) {{}}
</script>
</head>
<body>
<div class="viz-root"><div class="wrap">
  {_crumbs(("All players", "../"), ("By club", None))}
  <h1>Service time by club</h1>
  <p class="subtitle">Every 40-man roster, and who on it reaches arbitration and free agency.</p>

  <table><thead><tr><th>Club</th><th class="n">Players</th>
  <th class="n">Free agency</th><th class="n">Arbitration</th>
  <th class="n">Super Two</th></tr></thead><tbody>{rows}</tbody></table>

  <p class="foot">
    172 days credit a full year, so a season adds at most 1.000 no matter how
    long a player is on a roster. 3.000 years reaches salary arbitration and
    6.000 reaches free agency —
    <a href="{BASE_PATH}service-time.html">what every threshold unlocks</a>.
    Eligibility counts describe players currently on a 40-man roster.
    <br /><br />
    These figures are <strong>estimates</strong> reconstructed from public
    roster transaction records. They are not official MLB or MLBPA figures —
    those are not published. See the
    <a href="https://github.com/RainbowMang0/MLB-Service-Time-Tracker#readme">methodology</a>.
    Last updated {html.escape(generated_at[:10])}.
    <br /><br />
    Independent project, not affiliated with or endorsed by Major League
    Baseball or the MLBPA.
  </p>
</div></div>
{ANALYTICS}
</body>
</html>
"""


# The club pages were a plain table while the main table had a meter and
# coloured status pills, so the two read as different sites -- and the club
# pages are the ones search traffic lands on ("phillies arbitration
# eligible" beats any single player's name). These reproduce app.js's
# conventions exactly, against the same styles.css the pages already load,
# so the bar and the badge cannot disagree across the two surfaces.

def _svc_cell(player: dict) -> str:
    """The service-time meter, scaled 0 -> 6.000 as on the main table.

    6.000 is the scale ceiling because that is where the clock stops
    mattering, and the fill takes its colour from the same status the badge
    shows one column over.
    """
    days = int(player.get("service_days_total") or 0)
    pct = max(0.0, min(1.0, days / (FREE_AGENCY_YEARS * FULL_YEAR_DAYS))) * 100
    if player.get("free_agent_eligible"):
        fill = "f-good"
    elif player.get("super_two_candidate"):
        fill = "f-serious"
    elif player.get("arbitration_eligible"):
        fill = "f-warning"
    else:
        fill = ""
    years, _, dd = str(player.get("service_time") or "0.000").partition(".")
    return (
        "<span class='svc'>"
        f"<span class='svc-num'><span class='svc-years'>{html.escape(years)}</span>"
        f"<span class='svc-days'>.{html.escape(dd)}</span></span>"
        f"<span class='svc-track' style='--pct:{pct:.1f}'>"
        f"<span class='svc-fill {fill}'></span></span>"
        "</span>"
    )


def _status_badge(player: dict) -> str:
    """Same wording and same badge class as app.js's statusOf()."""
    if not player.get("service_days_total"):
        label, cls = "Yet to debut", "badge-neutral"
    elif player.get("free_agent_eligible"):
        label, cls = "Free Agent Eligible", "badge-good"
    elif player.get("super_two_candidate"):
        label, cls = "Super Two Track", "badge-serious"
    elif player.get("arbitration_eligible"):
        label, cls = "Arbitration Eligible", "badge-warning"
    else:
        label, cls = "Pre-Arbitration", "badge-neutral"
    return f"<span class='badge {cls}'>{label}</span>"


def render_club(club: str, players: list[dict], generated_at: str) -> str:
    """A club's 40-man roster, by service time.

    These exist so the player pages are not crawl leaves. Before them, every
    page was reachable only from the index and linked only back to it, which
    is the flattest possible structure and gives a crawler no reason to treat
    any of it as a coherent section. It is also the query people actually
    type -- "phillies arbitration eligible" is far commoner than any single
    player's name.
    """
    name = html.escape(club)
    url = f"{SITE_URL}/{club_path(club)}"
    total = len(players)
    fa = sum(1 for p in players if p.get("free_agent_eligible"))
    arb = sum(1 for p in players if p.get("arbitration_eligible"))
    s2 = sum(1 for p in players if p.get("super_two_candidate"))

    # Not "all N players on the 40-man": a club's 40-man can hold more than 40,
    # because players on the 60-day IL stay on it without counting against the
    # limit. The count is real -- it is what MLB's own roster endpoint returns
    # -- but phrased as a total it reads like a contradiction.
    desc = (
        f"Estimated major league service time for the {club} 40-man roster — "
        f"{total} players, {fa} free agency eligible, {arb} arbitration eligible. "
        "Reconstructed from public roster transactions; not an official "
        "MLB/MLBPA figure."
    )

    rows = "".join(
        f"<tr><td><a href=\"../{page_path(p)}\">{html.escape(p.get('name') or '')}</a></td>"
        f"<td>{html.escape(p.get('position') or '—')}</td>"
        f"<td class='n svc-col'>{_svc_cell(p)}</td>"
        f"<td class='n'>{p.get('service_days_total', 0)}</td>"
        f"<td>{_status_badge(p)}</td></tr>"
        for p in sorted(
            players, key=lambda p: (-int(p.get("service_days_total") or 0), p.get("name") or "")
        )
    )

    graph = [
        {
            "@type": "SportsTeam",
            "name": club,
            "url": url,
            "description": desc,
        },
        _breadcrumb_ld([
            ("Big League Service Time Tracker", f"{SITE_URL}/"),
            (club, url),
        ]),
    ]

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{name} service time — 40-man roster | Big League Service Time Tracker</title>
<meta name="description" content="{html.escape(desc)}" />
<link rel="canonical" href="{url}" />
<meta property="og:type" content="website" />
<meta property="og:title" content="{name} — service time, 40-man roster" />
<meta property="og:description" content="{html.escape(desc)}" />
<meta property="og:url" content="{url}" />
<meta name="twitter:card" content="summary" />
{_ld_script(graph)}
<link rel="icon" href="{BASE_PATH}favicon.svg" type="image/svg+xml" />
<link rel="stylesheet" href="../styles.css" />
<link rel="stylesheet" href="../page.css" />
<script>
  try {{
    var t = localStorage.getItem("mlb-service-time-theme");
    if (t) document.documentElement.setAttribute("data-theme", t);
  }} catch (e) {{}}
</script>
</head>
<body>
<div class="viz-root"><div class="wrap">
  {_crumbs(("All players", "../"), (name, None))}
  <h1>{name} — service time</h1>
  <p class="subtitle">Every player on the 40-man roster, most service time first.</p>

  <div class="facts">
    <div><span>Players tracked</span><b class="big">{total}</b></div>
    <div><span>Free agency eligible</span><b class="big">{fa}</b></div>
    <div><span>Arbitration eligible</span><b class="big">{arb}</b></div>
    <div><span>Super Two track</span><b class="big">{s2}</b></div>
  </div>

  <table><thead><tr><th>Player</th><th>Pos</th><th class="n">Service time</th>
  <th class="n">Days</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>

  <p class="foot">
    172 days credit a full year, so a season adds at most 1.000 no matter how
    long a player is on a roster. 3.000 years reaches salary arbitration and
    6.000 reaches free agency —
    <a href="{BASE_PATH}service-time.html">what every threshold unlocks</a>.
    <br /><br />
    These figures are <strong>estimates</strong> reconstructed from public
    roster transaction records. They are not official MLB or MLBPA figures —
    those are not published. See the
    <a href="https://github.com/RainbowMang0/MLB-Service-Time-Tracker#readme">methodology</a>.
    Last updated {html.escape(generated_at[:10])}.
    <br /><br />
    Independent project, not affiliated with or endorsed by Major League
    Baseball or the MLBPA.
  </p>
</div></div>
{ANALYTICS}
</body>
</html>
"""


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
    person = {
        "@type": "Person",
        "name": player.get("name") or "Player",
        "url": url,
        "additionalProperty": props,
    }
    trail = [("Big League Service Time Tracker", f"{SITE_URL}/")]
    club = player.get("team")
    if club and player.get("on_40_man"):
        person["affiliation"] = {
            "@type": "SportsTeam",
            "name": club,
            "url": f"{SITE_URL}/{club_path(club)}",
        }
        trail.append((club, f"{SITE_URL}/{club_path(club)}"))
    elif club:
        person["affiliation"] = {"@type": "SportsTeam", "name": club}
    if player.get("position"):
        person["jobTitle"] = player["position"]
    trail.append((player.get("name") or "Player", url))
    return _ld_script([person, _breadcrumb_ld(trail)])


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

    # The figure as a sentence, directly under the heading. A crawler's snippet
    # and a first-time reader both take the first paragraph; the facts grid
    # below states 21.075 without saying what that notation means.
    total_days = int(player.get("service_days_total") or 0)
    if total_days:
        lede = (
            f"{name} has an estimated <b>{_plain_figure(service)}</b> of major league "
            f"service time — {service} in the notation clubs use, from {total_days} "
            f"day{'' if total_days == 1 else 's'} credited on a major league roster."
        )
    else:
        # "0 days ... from 0 days credited" reads like a broken template. These
        # are prospects added to a 40-man to protect them from the Rule 5 draft.
        # The second sentence is conditional because _should_publish() may later
        # widen to non-rostered players, for whom it would simply be false.
        roster_note = (
            " He is on a 40-man roster but has not been on a major league active "
            "roster or injured list."
            if player.get("on_40_man")
            else ""
        )
        lede = (
            f"{name} has <b>yet to accrue a day</b> of major league service "
            f"time.{roster_note}"
        )

    trail: list[tuple[str, str | None]] = [("All players", "../")]
    if club and player.get("on_40_man"):
        trail.append((club, f"../{club_path(player['team'])}"))
    trail.append((name, None))
    crumbs = _crumbs(*trail)

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
<link rel="icon" href="{BASE_PATH}favicon.svg" type="image/svg+xml" />
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
<link rel="stylesheet" href="../page.css" />
</head>
<body>
<div class="viz-root"><div class="wrap">
  {crumbs}
  <h1>{name} service time</h1>
  <p class="subtitle">{club}{' · ' if club and position else ''}{position} · {html.escape(_status(player))}</p>
  <p class="lede">{lede}</p>

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
    6.000 reaches free agency —
    <a href="{BASE_PATH}service-time.html">what every threshold unlocks</a>.
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
{ANALYTICS}
</body>
</html>
"""


LASTMOD_PATH = DOCS / "data" / "page_lastmod.json"

# The footer stamp moves every day on every page whether or not anything about
# the player changed, so it has to come out before hashing or every page looks
# modified daily -- which is precisely the lie this is here to stop telling.
#
# Two wordings, not one. The generated pages say "Last updated <date>"; the
# hand-written explainer (docs/service-time.html) says "Updated <date>", and
# the original pattern did not match it -- so that one page reported a content
# change every single day and carried a fresh sitemap lastmod for it. A
# sitemap that cries wolf on one URL is the same failure as one that cries
# wolf on all of them, just quieter. Anchoring on the optional "Last " prefix
# covers both.
#
# The replacement string stays exactly "Last updated." -- not something
# neutral like "updated." -- because the stable text is what gets hashed, so
# changing it would rehash all 1,396 pages and bump every sitemap lastmod to
# today for no reason. Keeping it means this fix moves the one page that was
# actually broken and leaves the other 1,395 alone.
_VOLATILE_RE = re.compile(r"(?:Last u|U)pdated \d{4}-\d{2}-\d{2}\.")


def _content_key(page_html: str) -> str:
    stable = _VOLATILE_RE.sub("Last updated.", page_html)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]


class _LastMod:
    """Per-URL <lastmod> that reflects when a page's CONTENT last changed.

    Every URL previously claimed today's date, every day. In season roughly
    half the roster genuinely does change daily -- an accruing player gains a
    day -- but the other half does not, and between the World Series and
    Opening Day *nothing* does. A sitemap that claims 1,390 daily changes
    through a five-month offseason is the textbook case of the unreliable
    lastmod that Google responds to by ignoring lastmod for the whole site.

    So: hash each rendered page with the daily footer stamp removed, and only
    move the date when the hash actually moves. Hashing the rendered HTML
    rather than the record behind it means a template change counts too, which
    it should -- the page really did change.

    A missing or unreadable manifest degrades to "everything changed today",
    which is exactly the old behaviour and never wrong in a harmful direction.
    """

    def __init__(self, today: str) -> None:
        self.today = today
        try:
            self.previous = json.loads(LASTMOD_PATH.read_text())
        except (OSError, ValueError):
            self.previous = {}
        self.current: dict[str, dict[str, str]] = {}

    def record(self, rel_path: str, page_html: str) -> str:
        key = _content_key(page_html)
        was = self.previous.get(rel_path)
        day = was["lastmod"] if was and was.get("hash") == key else self.today
        self.current[rel_path] = {"hash": key, "lastmod": day}
        return day

    def of(self, rel_path: str) -> str:
        return self.current.get(rel_path, {}).get("lastmod", self.today)

    def save(self) -> None:
        # Only what is still published: a player who drops off a 40-man loses
        # his page, and his entry here would otherwise accumulate forever.
        LASTMOD_PATH.parent.mkdir(parents=True, exist_ok=True)
        LASTMOD_PATH.write_text(json.dumps(self.current, indent=0, sort_keys=True))

    def changed(self) -> int:
        return sum(1 for v in self.current.values() if v["lastmod"] == self.today)


def write_player_pages(
    db: dict[str, dict],
    generated_at: str | None = None,
    super_two_cutoff: dict | None = None,
) -> list[dict]:
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
    for directory in (PAGE_DIR, CLUB_DIR):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)

    lastmod = _LastMod(generated_at[:10])

    for player in published:
        page = render(player, team_names, generated_at)
        rel = page_path(player)
        (DOCS / rel).write_text(page)
        lastmod.record(rel, page)

    clubs = _write_club_pages(published, generated_at, lastmod)

    _write_page_css()
    explainer = render_explainer(generated_at, super_two_cutoff)
    (DOCS / EXPLAINER_PATH).write_text(explainer)
    lastmod.record(EXPLAINER_PATH, explainer)
    _write_sitemap(published, clubs, generated_at, lastmod)
    lastmod.save()
    _write_robots()
    _write_404()
    total_kb = sum((DOCS / page_path(p)).stat().st_size for p in published) / 1024
    print(
        f"Wrote {len(published)} player pages ({total_kb / 1024:.1f} MB) to {PAGE_DIR} "
        f"and {len(clubs)} club pages to {CLUB_DIR}; "
        f"{lastmod.changed()} of {len(lastmod.current)} changed content today"
    )
    return published


def _write_club_pages(
    published: list[dict], generated_at: str, lastmod: "_LastMod"
) -> list[str]:
    """One page per club with someone on its 40-man. Returns the club names."""
    by_club: dict[str, list[dict]] = {}
    for player in published:
        club = player.get("team")
        if club:
            by_club.setdefault(club, []).append(player)

    for club, players in by_club.items():
        page = render_club(club, players, generated_at)
        rel = club_path(club)
        (DOCS / rel).write_text(page)
        lastmod.record(rel, page)

    directory = render_club_index(by_club, generated_at)
    (CLUB_DIR / "index.html").write_text(directory)
    lastmod.record(f"{CLUB_DIR_NAME}/index.html", directory)
    return sorted(by_club)


def _write_sitemap(
    published: list[dict], clubs: list[str], generated_at: str, lastmod: "_LastMod"
) -> None:
    day = generated_at[:10]
    urls = [f"  <url><loc>{SITE_URL}/</loc><lastmod>{day}</lastmod><priority>1.0</priority></url>"]
    # Clubs above players: they are the hubs, and a crawler that samples the
    # sitemap rather than reading all of it should see them first.
    urls.append(
        f"  <url><loc>{SITE_URL}/{CLUB_DIR_NAME}/</loc>"
        f"<lastmod>{lastmod.of(f'{CLUB_DIR_NAME}/index.html')}</lastmod>"
        f"<priority>0.9</priority></url>"
    )
    # The explainer is a hub too: it is what "what is mlb service time"
    # should land on, and every player page links into it.
    urls.append(
        f"  <url><loc>{SITE_URL}/{EXPLAINER_PATH}</loc>"
        f"<lastmod>{lastmod.of(EXPLAINER_PATH)}</lastmod>"
        f"<priority>0.9</priority></url>"
    )
    # Hand-written pages. They are not generated, so nothing here can render
    # them -- but they still need an honest lastmod, which means hashing what
    # is actually on disk. A page missing from docs/ is skipped rather than
    # published as a URL that 404s.
    for path, priority in HAND_WRITTEN_PAGES:
        source = DOCS / path
        if not source.exists():
            print(f"  !! {path} is listed in the sitemap but not present; skipped")
            continue
        lastmod.record(path, source.read_text(encoding="utf-8"))
        urls.append(
            f"  <url><loc>{SITE_URL}/{path}</loc>"
            f"<lastmod>{lastmod.of(path)}</lastmod>"
            f"<priority>{priority}</priority></url>"
        )
    urls += [
        f"  <url><loc>{SITE_URL}/{club_path(c)}</loc>"
        f"<lastmod>{lastmod.of(club_path(c))}</lastmod>"
        f"<priority>0.8</priority></url>"
        for c in clubs
    ]
    urls += [
        f"  <url><loc>{SITE_URL}/{page_path(p)}</loc>"
        f"<lastmod>{lastmod.of(page_path(p))}</lastmod></url>"
        for p in published
    ]
    (DOCS / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )


# Cloudflare Web Analytics. Cookieless, so no consent banner, and the token is
# public by design -- it identifies the site to the beacon, it does not
# authorise anything. Kept as one constant because it has to appear on every
# generated page: miss one and that page's traffic is simply invisible.
#
# docs/index.html carries its own copy. It is hand-maintained and NOT generated
# by this script, so it cannot read this constant -- if the token ever changes,
# both places need it.
ANALYTICS = (
    "<!-- Cloudflare Web Analytics -->"
    "<script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' "
    "data-cf-beacon='{\"token\": \"b1a0b70a9b8944a9892a99bc56e22e15\"}'></script>"
    "<!-- End Cloudflare Web Analytics -->"
)

PAGE_CSS = """/* GENERATED by scripts/write_player_pages.py -- edit that, not this.

Shared by every static page (player, club, 404). It used to be inlined into
each of the 1,358 player pages, which cost ~1.4 KB apiece and, worse, could
not be cached: clicking from one player to another re-downloaded the same
rules every time. As a real file the browser fetches it once. */

.wrap { max-width: 60rem; margin: 0 auto; padding: 24px clamp(16px,4vw,40px) 48px; }
.wrap--narrow { max-width: 40rem; padding-top: 15vh; }
.big { font-size: 2.6rem; font-weight: 700; letter-spacing: -0.02em; line-height: 1; }
.facts { display: flex; flex-wrap: wrap; gap: 1.75rem; margin: 1rem 0 1.4rem;
         padding: 0.9rem 0; border-top: 1px solid var(--gridline);
         border-bottom: 1px solid var(--gridline); }
.facts div span { display: block; font-size: 0.66rem; text-transform: uppercase;
                  font-family: var(--mono);
                  letter-spacing: 0.05em; color: var(--text-muted); }
.crumbs { font-size: 0.8rem; color: var(--text-muted); margin-bottom: 1rem; }
.crumbs a { color: var(--accent); text-decoration: none; }
.crumbs a:hover { text-decoration: underline; }
.crumbs span { padding: 0 0.35rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid var(--gridline); }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
td a { color: var(--accent); text-decoration: none; }
td a:hover { text-decoration: underline; }
/* The explainer's threshold table. Wider rows than the season tables --
   the third column is prose, not a figure -- and the service-time column
   holds the number the whole site is about, so it leads. */
.thresholds { margin: 1.2rem 0 0.4rem; }
/* styles.css makes every thead sticky, which is right for a 5,578-row table
   and wrong here: on a prose page the header detaches and floats over the
   paragraphs below the table. */
.thresholds thead th { position: static; }
.thresholds td { vertical-align: top; line-height: 1.55; }
.thresholds td:first-child { white-space: nowrap; font-size: 1.05rem;
                             font-family: var(--mono); letter-spacing: -0.02em; }
.thresholds td:nth-child(2) { white-space: nowrap; color: var(--text-muted);
                              font-family: var(--mono); font-size: 0.85rem; }
.thresholds td:last-child { font-size: 0.9rem; }
.thr-note { display: inline-block; font-size: 0.72rem; color: var(--text-muted);
            white-space: normal; font-weight: 400; }
.sources { font-size: 0.82rem; color: var(--text-muted); line-height: 1.6;
           padding-left: 1.1rem; margin: 0.5rem 0 0.8rem; }
.sources li { margin-bottom: 0.35rem; }
.sources a { color: var(--accent); }
.foot-note { font-size: 0.82rem; color: var(--text-muted); line-height: 1.6;
             margin: 0.2rem 0 0; }
.foot-note a { color: var(--accent); }
.wrap h2 { font-size: 1.15rem; margin: 1.8rem 0 0.5rem; letter-spacing: -0.01em; }
.wrap p { line-height: 1.7; }
@media (max-width: 640px) {
  .thresholds td:nth-child(2) { display: none; }
  .thresholds thead th:nth-child(2) { display: none; }
}
.lede { font-size: 1.05rem; line-height: 1.6; color: var(--text-secondary);
        margin: 0.9rem 0 0; max-width: 62ch; }
.lede b { color: var(--text-primary); font-weight: 650; }
.caveat { background: var(--status-warning-wash); color: var(--text-secondary);
          padding: 0.7rem 0.85rem; border-radius: 6px; font-size: 0.85rem; }
.back { display: inline-block; margin-bottom: 1rem; color: var(--accent);
        text-decoration: none; }
.foot { margin-top: 1.5rem; font-size: 0.8rem; color: var(--text-muted); line-height: 1.6; }
h1 { letter-spacing: -0.02em; }
.wrap--narrow h1 { font-size: 2.2rem; margin: 0 0 0.6rem; }
.wrap--narrow p { color: var(--text-secondary); line-height: 1.7; }
.wrap--narrow a { color: var(--accent); }
.actions { margin-top: 1.6rem; }
"""


def _write_page_css() -> None:
    (DOCS / "page.css").write_text(PAGE_CSS)


EXPLAINER_PATH = "service-time.html"

# Pages that are hand-written rather than generated, but still belong in the
# sitemap. Listed by hand for the same reason the workflows list their paths
# by hand: sweeping docs/ wholesale would publish anything that happened to be
# sitting there. Each is content-hashed from disk so its lastmod is as honest
# as a generated page's.
HAND_WRITTEN_PAGES = [
    ("taxes.html", "0.9"),
    ("neutrality.html", "0.5"),
]


def render_explainer(generated_at: str, super_two_cutoff: dict | None = None) -> str:
    """The page that explains what the number on every other page means.

    Every other page here PUBLISHES a service-time figure and assumes the
    reader knows what one is. This is the page that says so, and it is a
    landing page in its own right -- "what is MLB service time" is a real
    search, and a far commoner one than any single player's name.

    ON SOURCING. The thresholds in the table are the durable, CBA-derived
    facts, and the four this project actually computes (172, Super Two,
    3.000, 6.000) are the same constants the pipeline uses -- so the page
    cannot drift from the site's own arithmetic without the arithmetic
    changing too.

    Deliberately NO DOLLAR FIGURES. Pension amounts are renegotiated and
    reported differently by different sources, and this page's prose is not
    regenerated when the data is -- so a number typed here would rot quietly
    while the figures beside it stayed current. The page states the
    THRESHOLD, which is the stable part, describes the benefit
    qualitatively, and points outward for current amounts.
    """
    url = f"{SITE_URL}/{EXPLAINER_PATH}"

    # The Super Two line quotes THIS SITE's computed cutoff rather than a
    # number typed into the prose. The threshold is not fixed -- it falls
    # where the class falls, and super_two.py has measured it between 2.112
    # and 2.137 across five seasons -- so a hardcoded figure would drift away
    # from the badges on the pages beside it. With no cutoff to hand the row
    # describes the rule and claims no number, rather than guessing one.
    if super_two_cutoff and super_two_cutoff.get("cutoff"):
        s2_fig = html.escape(str(super_two_cutoff["cutoff"]))
        s2_days = f"{int(super_two_cutoff.get('cutoff_days') or 0):,}"
        s2_season = super_two_cutoff.get("season")
        s2_note = (
            f" After the {s2_season} season the line fell at <b>{s2_fig}</b>, "
            "which is what this site currently projects against."
            if s2_season else ""
        )
    else:
        s2_fig, s2_days, s2_note = "varies", "—", ""
    desc = (
        "What major league service time is, how a day is earned, and every "
        "threshold it unlocks — 172 days to a year, arbitration at 3.000, "
        "free agency at 6.000, the gold card at 8.000 and a full pension at "
        "10.000."
    )

    # FAQPage rather than Article: these are the questions people actually
    # type, and the markup can surface the answer directly in a result.
    faq = {
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": "What is MLB service time?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        "Major league service time counts the days a player "
                        "spends on a major league active roster or injured "
                        "list. It is roster time, not playing time — a player "
                        "who never leaves the bench earns the same day as the "
                        "one who pitches a complete game. 172 days make one "
                        "credited year."
                    ),
                },
            },
            {
                "@type": "Question",
                "name": "Why is a service-time year 172 days and not a full season?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        "A major league season runs about 186 days, but the "
                        "Basic Agreement sets a credited year at 172. A player "
                        "on a roster all season is credited 1.000 and no more, "
                        "so the extra days give a little slack for a short "
                        "trip to the minors."
                    ),
                },
            },
            {
                "@type": "Question",
                "name": "What does a figure like 6.031 mean?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        "It is years and days, not a decimal. 6.031 is six "
                        "credited years and 31 days — 6 × 172 + 31 = 1,063 "
                        "days. Because a year is 172 days, the part after the "
                        "point never reaches 172."
                    ),
                },
            },
            {
                "@type": "Question",
                "name": "When does a player reach free agency?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        "At six credited years — 6.000, or 1,032 days on a "
                        "major league roster. Arbitration eligibility "
                        "generally arrives at 3.000, and a Super Two player "
                        "reaches it a year early."
                    ),
                },
            },
        ],
    }

    graph = [
        {
            "@type": "WebPage",
            "name": "What is MLB service time?",
            "url": url,
            "description": desc,
        },
        faq,
        _breadcrumb_ld([
            ("Big League Service Time Tracker", f"{SITE_URL}/"),
            ("What is service time?", url),
        ]),
    ]

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>What is MLB service time? Every threshold explained | Big League Service Time Tracker</title>
<meta name="description" content="{html.escape(desc)}" />
<link rel="canonical" href="{url}" />
<meta property="og:type" content="article" />
<meta property="og:title" content="What is MLB service time? Every threshold explained" />
<meta property="og:description" content="{html.escape(desc)}" />
<meta property="og:url" content="{url}" />
<meta name="twitter:card" content="summary" />
{_ld_script(graph)}
<link rel="icon" href="{BASE_PATH}favicon.svg" type="image/svg+xml" />
<link rel="stylesheet" href="{BASE_PATH}styles.css" />
<link rel="stylesheet" href="{BASE_PATH}page.css" />
<script>
  try {{
    var t = localStorage.getItem("mlb-service-time-theme");
    if (t) document.documentElement.setAttribute("data-theme", t);
  }} catch (e) {{}}
</script>
</head>
<body>
<div class="viz-root"><div class="wrap">
  {_crumbs(("All players", BASE_PATH), ("What is service time?", None))}

  <h1>What is MLB service time?</h1>
  <p class="lede">Major league service time is the currency of a baseball
  career. It decides when a player can negotiate his salary, when he can
  choose his employer, and what he is owed after he stops playing — and
  almost none of it depends on how well he plays.</p>

  <h2>It is roster time, not playing time</h2>
  <p>A player earns a day of service for each day he spends on a major
  league <b>active roster or injured list</b>. He does not have to appear in
  the game. A reliever who never warms up and the starter who throws a
  shutout earn exactly the same day.</p>
  <p>Days on the injured list count, which surprises people — the reasoning
  is that an injured major leaguer is still a major leaguer. Days on the
  <b>bereavement, family medical emergency and paternity lists</b> count too.
  Days spent optioned to the minor leagues do not.</p>

  <h2>172 days make a year</h2>
  <p>A season runs about 187 days, but the Basic Agreement sets a credited
  year at <b>172</b>. A player on a roster from Opening Day to the end of the
  season is credited <b>1.000</b> and no more, so those spare days leave a
  little room for a short trip to the minors without costing him the year.</p>
  <p>That is also why figures here look like <b>6.031</b> rather than 6.18.
  It is <b>years and days</b>, not a decimal: 6.031 means six credited years
  and 31 more days, or 1,063 days in total. The part after the point never
  reaches 172.</p>

  <h2>Every threshold, and what it unlocks</h2>
  <p>Service time is a ratchet: it only goes up, and each of these is
  permanent once reached.</p>

  <table class="thresholds">
    <thead><tr><th>Service time</th><th>Days</th><th>What it unlocks</th></tr></thead>
    <tbody>
      <tr>
        <td class="n"><b>0.001</b></td><td class="n">1</td>
        <td>Access to the players' benefit plan. One day on a major league
        roster is the entry point.</td>
      </tr>
      <tr>
        <td class="n"><b>0.043</b></td><td class="n">43</td>
        <td>One quarter of a year, and the first step toward a pension. Each
        further 43 days adds to what a player will eventually draw.</td>
      </tr>
      <tr>
        <td class="n"><b>{s2_fig}</b></td><td class="n">{s2_days}</td>
        <td><b>Super Two.</b> A player between two and three years who ranks
        in the top 22% of that class, with 86+ days in the preceding season,
        reaches salary arbitration <b>a year early</b> — four trips through it
        instead of three. <b>The cutoff is not fixed</b>: it falls wherever
        the class falls that year.{s2_note}</td>
      </tr>
      <tr>
        <td class="n"><b>3.000</b></td><td class="n">516</td>
        <td><b>Salary arbitration.</b> Until now the club has set his salary
        near the league minimum. From here he can argue for a raise before an
        arbitration panel, and his pay starts to track his performance.</td>
      </tr>
      <tr>
        <td class="n"><b>6.000</b></td><td class="n">1,032</td>
        <td><b>Free agency.</b> The big one. He can sign with any club that
        wants him, for the first time in his career.</td>
      </tr>
      <tr>
        <td class="n"><b>8.000</b></td><td class="n">1,376</td>
        <td><b>The gold card.</b> A lifetime pass admitting the holder and a
        guest to any regular-season major league game, at any ballpark.
        Postseason games are excluded.</td>
      </tr>
      <tr>
        <td class="n"><b>10.000</b></td><td class="n">1,720</td>
        <td><b>The maximum pension.</b> Ten years reaches the top of the
        scale. Fewer than one player in ten ever gets there.</td>
      </tr>
      <tr>
        <td class="n"><b>10.000</b><br /><span class="thr-note">+ 5 straight
        with one club</span></td><td class="n">1,720</td>
        <td><b>10-and-5 rights.</b> Ten years of service with the last five
        consecutive at his current club, and he can <b>veto any trade</b>. It
        arrives automatically — it does not have to be negotiated into a
        contract. Leave the club and come back, and the five-year clock
        restarts.</td>
      </tr>
    </tbody>
  </table>

  <p class="foot-note">Pension and benefit amounts are renegotiated between
  the league and the players' association and are reported differently by
  different sources, so <b>no dollar figures are quoted here</b> — the
  thresholds above are the durable part.</p>

  <h2>Where these come from</h2>
  <p class="foot-note">This site publishes its own reconstruction of service
  time, so it owes you the provenance of the rules it reconstructs against.
  The first four thresholds are the ones the pipeline itself computes; the
  rest are not, and are sourced here.</p>
  <ul class="sources">
    <li><a href="https://www.mlb.com/glossary/transactions/service-time">MLB
      glossary — Service time</a>: 172 days to a credited year, and the
      length of a season.</li>
    <li><a href="https://www.mlb.com/glossary/transactions/super-two">MLB
      glossary — Super Two</a>: two-to-three years, 86+ days, top 22%.</li>
    <li><a href="https://www.mlb.com/glossary/transactions/salary-arbitration">MLB
      glossary — Salary arbitration</a> and
      <a href="https://www.mlb.com/glossary/transactions/free-agency">Free
      agency</a>: the 3.000 and 6.000 thresholds.</li>
    <li><a href="https://www.mlb.com/glossary/transactions/10-and-5-rights">MLB
      glossary — 10-and-5 rights</a>: ten years, five consecutive with the
      current club, full trade veto.</li>
    <li><a href="https://www.sportico.com/leagues/baseball/2025/mlb-lifetime-pass-golden-ticket-reward-program-service-1234854369/">Sportico</a>
      and <a href="https://www.insidehook.com/sports/mlb-gold-card-free-baseball-lifetime-pass">InsideHook</a>
      on the gold card: eight years, awarded by the Commissioner's Office,
      regular-season admission for the holder and a guest.</li>
    <li><a href="https://www.mlbplayers.com/">MLB Players Association</a> for
      pension and benefit terms, including the 43-day quarter and the
      ten-year maximum.</li>
  </ul>
  <p class="foot-note">Where sources disagreed, this page states the weaker
  claim. One day of service is described as buying <i>access to the benefit
  plan</i> because accounts differ on whether it confers coverage or the
  right to buy in.</p>

  <h2>Why clubs pay attention to the calendar</h2>
  <p>Because 172 days make a year and a season is longer, a club that keeps a
  player in the minors for the first couple of weeks of his rookie season
  leaves him at 0.171 rather than 1.000 — and pushes his free agency back by
  a full year. The practice is called <b>service-time manipulation</b>, it is
  legal, it is contested, and it is the reason a prospect's call-up date is
  news.</p>

  <h2>What this site publishes</h2>
  <p>MLB and the players' association keep the official ledger and <b>do not
  publish it</b>. Every figure on this site is reconstructed from public
  roster transactions and is an <b>estimate</b>, not an official
  MLB/MLBPA figure. Where the record cannot see the start of a career, the
  player's page says so and the number is a floor rather than an estimate.</p>

  <p class="actions"><a href="{BASE_PATH}">Look up a player &rarr;</a></p>

  <p class="foot">
    Updated {generated_at[:10]}. Not affiliated with or endorsed by Major
    League Baseball or the MLBPA.
  </p>
</div></div>
{ANALYTICS}
</body>
</html>
"""

def _write_404() -> None:
    """The page GitHub Pages serves for any unknown path.

    Generated rather than hand-written for one reason: it is served for paths
    under /p/ as well as at the root, so its links must be site-absolute, and a
    hand-maintained absolute path is exactly the thing that silently breaks on
    a domain move. Derived from BASE_PATH, it cannot.
    """
    (DOCS / "404.html").write_text(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Page not found | Big League Service Time Tracker</title>
<meta name="robots" content="noindex" />
<!-- GENERATED by scripts/write_player_pages.py -- edit that, not this.
     Every URL here is site-absolute on purpose: this file is served for bad
     paths under /p/ too, where a relative "styles.css" would resolve against
     /p/ and 404 in turn, leaving an unstyled error page. -->
<link rel="icon" href="{BASE_PATH}favicon.svg" type="image/svg+xml" />
<link rel="stylesheet" href="{BASE_PATH}styles.css" />
<link rel="stylesheet" href="{BASE_PATH}page.css" />
<script>
  /* Same theme carry as the player pages; see the note there. */
  try {{
    var t = localStorage.getItem("mlb-service-time-theme");
    if (t) document.documentElement.setAttribute("data-theme", t);
  }} catch (e) {{}}
</script>
</head>
<body>
<div class="viz-root"><div class="wrap wrap--narrow">
  <h1>That page isn't here.</h1>
  <p>
    The link may be old, or it may point to a player who has since come off a
    40-man roster &mdash; pages are published for current players and are
    removed when a player drops off.
  </p>
  <p>
    Every player this project tracks, current or not, is in the searchable
    table on the main page.
  </p>
  <p class="actions"><a href="{BASE_PATH}">&larr; Search all players</a></p>
</div></div>
{ANALYTICS}
</body>
</html>
""")


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
