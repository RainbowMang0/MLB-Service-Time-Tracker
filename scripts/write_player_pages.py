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

# --------------------------------------------------------------------------
# Site navigation.
#
# One definition, used by all four generated templates. The five hand-written
# pages carry the same markup with relative hrefs, and
# test_the_site_nav_is_the_same_on_every_page() asserts the destination sets
# match -- this is the same "lands in N places and the Nth is the trap" shape
# as the analytics token, which sat wrong in docs/index.html precisely because
# a hand-maintained file cannot read a Python constant.
#
# `tag` marks a section a visitor should know about before clicking. The duty
# day and contract tools work, but no tax professional has reviewed the
# duty-day methodology and the contract tool is mid-build, so they say so.
# Owner's decision, 2026-09-11.
# --------------------------------------------------------------------------

NAV_SECTIONS = [
    ("", "Service time", None),
    ("t/", "By club", None),
    ("alumni/", "Previous players", None),
    ("service-time.html", "What is service time?", None),
    ("contract.html", "Contract clock", "in development"),
    ("taxes.html", "Duty days", "in development"),
    ("neutrality.html", "Neutrality", None),
]


def _site_nav(current: str | None = None) -> str:
    """
    The navigation strip.

    `current` is a key from NAV_SECTIONS (the href fragment), and marks the
    link with aria-current. The CSS keys off aria-current rather than a hand
    set class, so the accessible state and the visible state cannot disagree.

    Hrefs are BASE_PATH-absolute, not relative: these pages are served from
    /p/, /t/ and /alumni/ as well as the root, so a relative href would
    resolve differently per directory. BASE_PATH is derived from docs/CNAME,
    so a domain move carries the nav with it.
    """
    items = []
    for href, label, tag in NAV_SECTIONS:
        mark = ' aria-current="page"' if current == href else ""
        badge = f' <span class="nav-tag">{tag}</span>' if tag else ""
        items.append(f'<a href="{BASE_PATH}{href}"{mark}>{label}{badge}</a>')
    return (
        '<nav class="site-nav" aria-label="Site sections">'
        + "".join(items)
        + "</nav>"
    )


# From the CBA ruleset, not a literal -- see scripts/cba.py. This file draws
# the same service-time meter as docs/app.js, so the two must agree on what a
# full year is, and the only way to guarantee that is for both to read it from
# config/cba/ rather than each keeping a copy.
#
# ⚠️ THE ARITHMETIC WAS NEVER THE PROBLEM. Until this pass the pipeline computed
# from the ruleset while every sentence a reader actually sees said "172" as a
# literal -- roughly twenty of them across the page footers, the explainer's
# threshold table, its meta description and its FAQ structured data. The
# project's headline claim is that filling in config/cba/2027.json makes the
# whole site current the same day; that was true of the numbers and false of
# the prose, which is the half a reader believes. Everything below is derived
# so the two cannot drift apart.
_RULES = cba.default()
FULL_YEAR_DAYS = _RULES.require("service_time.days_per_credited_year")
FREE_AGENCY_YEARS = _RULES.require("free_agency.credited_years_required")
ARBITRATION_YEARS = _RULES.require("arbitration.standard_years_required")
SEASON_SPAN_DAYS = _RULES.require("service_time.normal_season_span_days")
SUPER_TWO_TOP_PERCENTILE = _RULES.require("arbitration.super_two.top_percentile")
SUPER_TWO_MIN_PRIOR_DAYS = _RULES.require(
    "arbitration.super_two.minimum_prior_season_days"
)


def _yrs(years: float) -> str:
    """6.0 -> "6.000". The Y.DDD notation for a whole number of credited years."""
    return f"{int(years)}.000"


def _days_for(years: float) -> int:
    """Credited years -> days, under the agreement in force."""
    return int(round(years * FULL_YEAR_DAYS))


def _threshold_sentence() -> str:
    """The two-sentence rule-of-the-road that closes every generated page.

    One string rather than three copies: it appeared verbatim at the foot of
    the player pages, the club pages and the club directory, and each copy
    hardcoded 172, 3.000 and 6.000 separately.
    """
    return (
        f"{FULL_YEAR_DAYS} days credit a full year, so a season adds at most "
        f"1.000 no matter how long a player is on a roster. {_yrs(ARBITRATION_YEARS)} "
        f"years reaches salary arbitration and {_yrs(FREE_AGENCY_YEARS)} reaches "
        "free agency —"
    )

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


def _credited_seasons(player: dict) -> int:
    return sum(1 for s in (player.get("seasons") or []) if s.get("d"))


def _is_alumnus(player: dict) -> bool:
    """A previously-rostered player whose figure is as solid as a current one.

    Widened 2026-09-08 from "rostered players only", but NOT to everybody. The
    non-rostered population is 4,222 players and its data is measurably weaker
    than the 40-man's: 27% declare missing seasons and 16% of their credited
    seasons are `presumed` from the debut rather than read from transactions.

    Two conditions, and both are about whether the page can stand on its own:

      * `missing_seasons == 0` -- the feed can see the FRONT of his career, so
        the total is a real estimate rather than a floor.
      * more than one credited season -- a single-season page is a handful of
        numbers wrapped in boilerplate, and a median player page already
        carries only ~28 distinct words of its own.

    That is 2,466 of the 4,222. The remaining 1,756 lean on presumed data and
    stay unpublished until there is evidence these index at all -- which is
    what sitemap-alumni.xml exists to measure.
    """
    return (
        not player.get("on_40_man")
        and int(player.get("missing_seasons") or 0) == 0
        and _credited_seasons(player) > 1
    )


def _should_publish(player: dict) -> bool:
    return bool(player.get("on_40_man")) or _is_alumnus(player)


def _fmt(days: int) -> str:
    return f"{days // FULL_YEAR_DAYS}.{days % FULL_YEAR_DAYS:03d}"


def _debuted_but_empty(player: dict) -> bool:
    """He reached the majors, and we credit him nothing.

    A player who appears in a major league game is on the active roster that
    day, so his debut date is proof of at least one day he is owed. When the
    figure is nevertheless zero, the transaction record failed to reconstruct
    a stint that certainly happened -- and the page must not describe him the
    way it describes a prospect who has never been up.

    Live example: Seth Lonsway, debut 2026-08-29, whose contract was selected
    and who was optioned back on that same date. Stop-wins (finding #10)
    correctly leaves no interval to credit, and finding #15 cannot reach him
    because the selection is ON his debut rather than before it. The
    arithmetic is defensible; a sentence claiming he "has not been on a major
    league active roster" is not.
    """
    return bool(player.get("mlb_debut")) and not player.get("service_days_total")


def _status(player: dict) -> str:
    if not player.get("on_40_man"):
        return "No longer on a 40-man roster"
    if _debuted_but_empty(player):
        return "Debuted, but no service time can be reconstructed"
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


def _club_phrase(player: dict) -> str:
    """"with the Braves" for a current player, "last with" for a former one.

    A non-rostered player's stored `team` is the last club we saw him with, and
    it is stale by construction -- the table's payload blanks it for exactly
    that reason. A page cannot blank it (his club IS most of what identifies
    him), so it says plainly which kind of fact it is. Without this, widening
    publication to 2,466 former players would have put "Player X ... with the
    Atlanta Braves" on every one of them, asserting a roster spot none of them
    holds.
    """
    club = player.get("team")
    if not club:
        return ""
    return f"with {club}" if player.get("on_40_man") else f"last with {club}"


def _description(player: dict) -> str:
    name = player.get("name") or "This player"
    service = player.get("service_time") or "0.000"
    where = _club_phrase(player) or "in the major leagues"
    return (
        f"{name} has an estimated {service} years of major league service time "
        f"{where} — {player.get('service_days_total', 0)} days credited. "
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
  {_site_nav("t/")}
  {_crumbs(("All players", "../"), ("By club", None))}
  <h1>Service time by club</h1>
  <p class="subtitle">Every 40-man roster, and who on it reaches arbitration and free agency.</p>

  <table><thead><tr><th>Club</th><th class="n">Players</th>
  <th class="n">Free agency</th><th class="n">Arbitration</th>
  <th class="n">Super Two</th></tr></thead><tbody>{rows}</tbody></table>

  <p class="foot">
    {_threshold_sentence()}
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
    """Same wording and badge class as classify() in docs/app.js.

    Club pages only ever list rostered players, so classify()'s "Not on a
    roster" and "Unknown" branches have no counterpart here -- the branches
    that DO exist must match it word for word, because a visitor moving between
    the club page and the main table is looking at the same player.
    """
    if not player.get("service_days_total"):
        label, cls = "Yet to debut", "badge-neutral"
    elif player.get("free_agent_eligible"):
        label, cls = "Free Agent Eligible", "badge-good"
    elif player.get("super_two_candidate"):
        # Lower-case "track", matching classify(). It read "Track" here, which
        # is the sort of drift two independent copies of a label always
        # produce -- see the test that now pins them together.
        label, cls = "Super Two track", "badge-serious"
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
  {_site_nav("t/")}
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
    {_threshold_sentence()}
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
    # Past tense for a former player -- see _club_phrase().
    club_label = club if not club else (
        club if player.get("on_40_man") else f"Last with {club}"
    )
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
    elif _debuted_but_empty(player):
        # He REACHED the majors and we credit him nothing, which is a different
        # fact from never having been up and must not be described as one.
        #
        # The page used to say "he is on a 40-man roster but has not been on a
        # major league active roster or injured list" -- flatly false for a man
        # with a debut date, since appearing in a game requires being on the
        # active roster. It asserted more than the arithmetic knows, which is
        # the one thing a page publishing an estimate must never do.
        lede = (
            f"{name} made his major league debut on {debut}, but <b>no service "
            "time can be reconstructed</b> for him from the public transaction "
            "record. He is owed at least the day he appeared; the roster moves "
            "that would prove it are not in the feed. This figure is a floor, "
            "not a measurement."
        )
    else:
        # "0 days ... from 0 days credited" reads like a broken template. These
        # are prospects added to a 40-man to protect them from the Rule 5 draft
        # -- no debut date, so nothing here contradicts the record.
        # The second sentence is conditional because _should_publish() may later
        # widen to non-rostered players, for whom it would simply be false.
        roster_note = (
            " He is on a 40-man roster but has not yet been on a major league "
            "active roster or injured list."
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
    elif not player.get("on_40_man"):
        # A published alumnus is not on any club page -- his stored club is
        # stale -- so the alumni directory is his one route back up, and the
        # one place that links down to him. Without this he is an orphan.
        trail.append(("Previous players", f"../{ALUMNI_DIR_NAME}/"))
    trail.append((name, None))
    crumbs = _site_nav() + _crumbs(*trail)

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
     Dark is the site's identity, not something inherited from the OS, so
     styles.css contains no prefers-color-scheme rules at all and this is the
     only thing that can produce a light page. */
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
  <p class="subtitle">{club_label}{' · ' if club_label and position else ''}{position} · {html.escape(_status(player))}</p>
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
    {_threshold_sentence()}
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


# --- The daily tick is not a content change ---------------------------------
# Measured 2026-09-08, after Search Console reported 782 pages "Discovered --
# currently not indexed". Across five consecutive daily commits the sitemap
# claimed 1,138-1,144 of ~1,405 URLs (81%) had changed THAT DAY, every day.
#
# It was telling the truth, and that is the problem. An accruing player gains a
# day every day the season is on, so his figure really does move. Diffed at N
# vs N+1 days, exactly sixteen lines change on a player page and every one of
# them is the same two numbers -- the Y.DDD figure and the day count:
#
#     8.156 -> 8.157        1532 -> 1533
#
# Nothing structural moves: same club, same status, same season rows, same
# words. Google asks that lastmod carry the date of the last *significant*
# modification, and a counter ticking by one is not that. Meanwhile an
# eight-day-old domain has a small crawl budget, and we were spending it asking
# Google to re-fetch 81% of the site daily while 782 pages had never been
# crawled once.
#
# This is the same judgement `_VOLATILE_RE` already makes about the footer date
# -- that stamp also genuinely changes daily and is also normalised out --
# extended to the two figures that behave the same way.
#
# WHAT STILL MOVES A DATE, deliberately: a status change (the badge text
# differs), a trade (the club name differs), a new season row (the YEAR is not
# normalised -- only `<td class='n'>` day cells are), and any template edit
# (the surrounding markup and words are hashed as before). The four cases are
# pinned by a test.
_FIGURE_RE = re.compile(r"\b\d+\.\d{3}\b")               # 8.156, 21.075, 1.000
_DAYS_PHRASE_RE = re.compile(r"[\d,]+ days credited")     # meta, JSON-LD, lede
_DAYS_FACT_RE = re.compile(r"(<span>Days credited</span>)\d+")
_DAYS_CELL_RE = re.compile(r"(<td class='n'>)\d+(</td>)")  # season + club tables
_PCT_RE = re.compile(r"--pct:[\d.]+")                      # the meter fill
# The same figure spelled out by _plain_figure() in the lede: "8 years and 156
# days". Found by diffing the NORMALISED text at N vs N+1 days rather than by
# reading the template -- the first pass at this list missed it, and the diff
# is what said so.
_PLAIN_FIGURE_RE = re.compile(r"<b>\d+ (?:year|day)s?(?: and \d+ days?)?</b>")


def _content_key(page_html: str) -> str:
    stable = _VOLATILE_RE.sub("Last updated.", page_html)
    stable = _PLAIN_FIGURE_RE.sub("<b><PLAIN></b>", stable)
    stable = _FIGURE_RE.sub("<SVC>", stable)
    stable = _DAYS_PHRASE_RE.sub("<DAYS> days credited", stable)
    stable = _DAYS_FACT_RE.sub(r"\1<DAYS>", stable)
    stable = _DAYS_CELL_RE.sub(r"\1<DAYS>\2", stable)
    stable = _PCT_RE.sub("--pct:<PCT>", stable)
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
    alumni = [p for p in published if not p.get("on_40_man")]
    alumni_pages = _write_alumni_pages(alumni, generated_at, lastmod) if alumni else []

    _write_page_css()
    explainer = render_explainer(generated_at, super_two_cutoff)
    (DOCS / EXPLAINER_PATH).write_text(explainer)
    lastmod.record(EXPLAINER_PATH, explainer)
    _write_sitemap(published, clubs, alumni_pages, generated_at, lastmod)
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
    """One page per club with someone on its 40-man. Returns the club names.

    ⚠️ ROSTERED PLAYERS ONLY, and the filter is explicit rather than implied by
    the caller. `published` used to be exactly the 40-man, so grouping it by
    club was the same thing; since _should_publish() widened to alumni it is
    not. A retired player's stored `team` is the last club we saw him with,
    which is stale by construction -- listing him on that club's 40-man page
    would assert a roster spot he does not hold, which is the same category
    error already fixed once in the table's payload.
    """
    by_club: dict[str, list[dict]] = {}
    for player in published:
        club = player.get("team")
        if club and player.get("on_40_man"):
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


ALUMNI_DIR_NAME = "alumni"
ALUMNI_DIR = DOCS / ALUMNI_DIR_NAME

_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}


def _surname_initial(name: str) -> str:
    """First letter of the surname, for an A-Z directory.

    Suffixes are skipped so "Ken Griffey Jr." files under G, not J. Anything
    that does not fold to a letter goes under '#' rather than being dropped --
    a player who cannot be filed is a player nobody can reach.
    """
    parts = [p for p in slug(name).split("-") if p and p not in _SUFFIXES]
    if not parts:
        return "#"
    first = parts[-1][:1].upper()
    return first if first.isalpha() else "#"


def _alumni_letters(alumni: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for p in alumni:
        out.setdefault(_surname_initial(p.get("name") or ""), []).append(p)
    for players in out.values():
        players.sort(key=lambda p: (_surname_initial(p.get("name") or ""),
                                    (p.get("name") or "").split()[-1:],
                                    p.get("name") or ""))
    return dict(sorted(out.items()))


def _alumni_shell(title: str, desc: str, url: str, crumbs: str,
                  heading: str, sub: str, body: str, generated_at: str,
                  graph: list[dict], depth: str = "../") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}" />
<link rel="canonical" href="{url}" />
<meta property="og:type" content="website" />
<meta property="og:title" content="{html.escape(heading)}" />
<meta property="og:description" content="{html.escape(desc)}" />
<meta property="og:url" content="{url}" />
<meta name="twitter:card" content="summary" />
{_ld_script(graph)}
<link rel="icon" href="{BASE_PATH}favicon.svg" type="image/svg+xml" />
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
<div class="viz-root"><div class="wrap">
  {crumbs}
  <h1>{html.escape(heading)}</h1>
  <p class="subtitle">{sub}</p>
  {body}
  <p class="foot">
    {_threshold_sentence()}
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


def _write_alumni_pages(
    alumni: list[dict], generated_at: str, lastmod: "_LastMod"
) -> list[str]:
    """An A-Z directory for players no longer on a 40-man. Returns rel paths.

    WHY THIS HAS TO EXIST. Club pages list the 40-man only, so a published
    alumnus has no inbound internal link at all -- a pure orphan reachable
    only from the sitemap, which is the worst possible starting position for a
    page whose whole purpose is to be found. Search Console already reports 782
    pages "Discovered - currently not indexed"; adding 2,466 orphans to that
    queue would prove nothing about whether these pages are worth indexing.

    So: home -> alumni/ -> letter page -> player, the same depth the 40-man
    pages sit at through the club directory.
    """
    by_letter = _alumni_letters(alumni)
    if ALUMNI_DIR.exists():
        shutil.rmtree(ALUMNI_DIR)
    ALUMNI_DIR.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    total = len(alumni)

    for letter, players in by_letter.items():
        fname = ("num" if letter == "#" else letter.lower()) + ".html"
        rel = f"{ALUMNI_DIR_NAME}/{fname}"
        url = f"{SITE_URL}/{rel}"
        desc = (
            f"Estimated major league service time for {len(players)} players "
            f"whose surname begins with {letter} and who are no longer on a "
            "40-man roster. Reconstructed from public roster transactions; not "
            "an official MLB/MLBPA figure."
        )
        rows = "".join(
            f"<tr><td><a href=\"../{page_path(p)}\">{html.escape(p.get('name') or '')}</a></td>"
            f"<td class='n svc-col'>{_svc_cell(p)}</td>"
            f"<td class='n'>{p.get('service_days_total', 0)}</td>"
            f"<td>{html.escape((p.get('mlb_debut') or '—')[:4])}</td>"
            f"<td>{html.escape((p.get('last_played') or '—')[:4])}</td></tr>"
            for p in players
        )
        nav = " ".join(
            f'<a href="{("num" if l == "#" else l.lower())}.html">{l}</a>'
            if l != letter else f"<b>{l}</b>"
            for l in by_letter
        )
        body = (
            f'<p class="crumbs">{nav}</p>'
            "<table><thead><tr><th>Player</th><th class=\"n\">Service time</th>"
            "<th class=\"n\">Days</th><th>Debut</th><th>Last played</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )
        graph = [
            {"@type": "CollectionPage", "name": f"Previous players — {letter}",
             "url": url, "description": desc},
            _breadcrumb_ld([
                ("Big League Service Time Tracker", f"{SITE_URL}/"),
                ("Previous players", f"{SITE_URL}/{ALUMNI_DIR_NAME}/"),
                (letter, url),
            ]),
        ]
        page = _alumni_shell(
            f"Previous players — {letter} | Big League Service Time Tracker",
            desc, url,
            _site_nav("alumni/") + _crumbs(("All players", "../"), ("Previous players", "./"), (letter, None)),
            f"Previous players — {letter}",
            f"{len(players)} players no longer on a 40-man roster, by surname.",
            body, generated_at, graph,
        )
        (DOCS / rel).write_text(page, encoding="utf-8")
        lastmod.record(rel, page)
        written.append(rel)

    # The directory itself.
    rel = f"{ALUMNI_DIR_NAME}/index.html"
    url = f"{SITE_URL}/{ALUMNI_DIR_NAME}/"
    desc = (
        f"Estimated major league service time for {total:,} players who have "
        "come off a 40-man roster, A to Z. Reconstructed from public roster "
        "transactions; not an official MLB/MLBPA figure."
    )
    cards = "".join(
        f"<tr><td><a href=\"{('num' if l == '#' else l.lower())}.html\">{l}</a></td>"
        f"<td class='n'>{len(ps)}</td></tr>"
        for l, ps in by_letter.items()
    )
    body = (
        "<table><thead><tr><th>Surname</th><th class=\"n\">Players</th></tr>"
        f"</thead><tbody>{cards}</tbody></table>"
    )
    graph = [
        {"@type": "CollectionPage", "name": "Previous players", "url": url,
         "description": desc},
        _breadcrumb_ld([
            ("Big League Service Time Tracker", f"{SITE_URL}/"),
            ("Previous players", url),
        ]),
    ]
    page = _alumni_shell(
        "Previous players — service time A to Z | Big League Service Time Tracker",
        desc, url,
        _site_nav("alumni/") + _crumbs(("All players", "../"), ("Previous players", None)),
        "Previous players",
        f"{total:,} players who have come off a 40-man roster, by surname. "
        "Their service time is final — it stopped when they did.",
        body, generated_at, graph,
    )
    (ALUMNI_DIR / "index.html").write_text(page, encoding="utf-8")
    lastmod.record(rel, page)
    written.append(rel)
    return written


def _write_sitemap(
    published: list[dict], clubs: list[str], alumni_pages: list[str],
    generated_at: str, lastmod: "_LastMod"
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
    club_urls = [
        f"  <url><loc>{SITE_URL}/{club_path(c)}</loc>"
        f"<lastmod>{lastmod.of(club_path(c))}</lastmod>"
        f"<priority>0.8</priority></url>"
        for c in clubs
    ]
    def _url(rel, prio=None):
        pr = f"<priority>{prio}</priority>" if prio else ""
        return (f"  <url><loc>{SITE_URL}/{rel}</loc>"
                f"<lastmod>{lastmod.of(rel)}</lastmod>{pr}</url>")

    player_urls = [_url(page_path(p)) for p in published if p.get("on_40_man")]
    # Alumni get their OWN sitemap, which is the whole point of publishing them
    # as a measured batch: Search Console reports indexed-vs-submitted per
    # sitemap, so in a fortnight this file answers "do retired-player pages
    # index at all?" without confounding it with the 40-man pages.
    alumni_urls = (
        [_url(rel, "0.7") for rel in alumni_pages if rel.endswith("index.html")]
        + [_url(rel, "0.6") for rel in alumni_pages if not rel.endswith("index.html")]
        + [_url(page_path(p)) for p in published if not p.get("on_40_man")]
    )

    # SPLIT BY SECTION, behind a sitemap index at the same URL.
    #
    # Search Console reports indexed-vs-submitted PER SITEMAP, and with one flat
    # file of 1,406 URLs "782 discovered, not indexed" says nothing about WHICH
    # 782. Split, the next report answers it directly: if the 30 club pages
    # index and the 1,370 player pages do not, that is a statement about thin
    # templated pages; if both lag equally it is a statement about site age.
    #
    # sitemap.xml stays the entry point and becomes the index, so the URL
    # already submitted to Search Console keeps working and Google discovers
    # the children itself -- nothing has to be re-submitted by hand.
    _write_urlset("sitemap-core.xml", urls)
    _write_urlset("sitemap-clubs.xml", club_urls)
    _write_urlset("sitemap-players.xml", player_urls)
    if alumni_urls:
        _write_urlset("sitemap-alumni.xml", alumni_urls)

    newest = max([lastmod.today] + [lastmod.of(page_path(p)) for p in published])
    children = "\n".join(
        f"  <sitemap><loc>{SITE_URL}/{name}</loc><lastmod>{when}</lastmod></sitemap>"
        for name, when in (
            ("sitemap-core.xml", lastmod.today),
            ("sitemap-clubs.xml", lastmod.today),
            ("sitemap-players.xml", newest),
        ) + ((("sitemap-alumni.xml", lastmod.today),) if alumni_urls else ())
    )
    (DOCS / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + children
        + "\n</sitemapindex>\n",
        encoding="utf-8",
    )


def _write_urlset(name: str, urls: list[str]) -> None:
    (DOCS / name).write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n",
        encoding="utf-8",
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
    ("contract.html", "0.9"),
    ("neutrality.html", "0.5"),
]


def render_explainer(generated_at: str, super_two_cutoff: dict | None = None) -> str:
    """The page that explains what the number on every other page means.

    Every other page here PUBLISHES a service-time figure and assumes the
    reader knows what one is. This is the page that says so, and it is a
    landing page in its own right -- "what is MLB service time" is a real
    search, and a far commoner one than any single player's name.

    ON SOURCING. The thresholds in the table are the durable, CBA-derived
    facts, and the four this project actually computes (the credited year,
    Super Two, arbitration, free agency) are read from config/cba/ -- the same
    ruleset the pipeline computes against -- so the page cannot drift from the
    site's own arithmetic without the arithmetic changing too. The gold card
    and the pension maximum are NOT in the ruleset, because this project does
    not compute them and the loader refuses to publish a value nobody has
    verified; they are stated as page constants and sourced in the list below,
    with only their day counts derived.

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

    # Thresholds this project does NOT compute, so they are not in the CBA
    # ruleset and must not be invented into it -- the loader's own rule is that
    # a value nobody has verified may not be published. They are sourced in the
    # page's own list (Sportico/InsideHook for the gold card, the MLBPA for the
    # pension). Their DAY counts are still derived, because those follow from
    # the credited-year length whatever the year threshold turns out to be.
    GOLD_CARD_YEARS = 8
    MAX_PENSION_YEARS = 10

    desc = (
        "What major league service time is, how a day is earned, and every "
        f"threshold it unlocks — {FULL_YEAR_DAYS} days to a year, arbitration "
        f"at {_yrs(ARBITRATION_YEARS)}, free agency at {_yrs(FREE_AGENCY_YEARS)}, "
        f"the gold card at {_yrs(GOLD_CARD_YEARS)} and a full pension at "
        f"{_yrs(MAX_PENSION_YEARS)}."
    )
    # One worked example, computed rather than typed, so "6 x 172 + 31 = 1,063"
    # cannot survive a change to what a credited year is worth.
    eg_years, eg_days = int(FREE_AGENCY_YEARS), 31
    eg_total = _days_for(eg_years) + eg_days
    eg_fig = f"{eg_years}.{eg_days:03d}"
    # A pension quarter is a quarter of a credited year, so it follows the
    # credited year rather than being the fixed 43 it happens to be today.
    quarter_days = FULL_YEAR_DAYS // 4

    # FAQPage rather than Article: these are the questions people actually
    # type, and the markup can surface the answer directly in a result.
    #
    # Every figure here is derived, for a reason beyond the usual one: this
    # block and the visible prose below it used to be written independently and
    # had already drifted apart -- the structured data said a season runs "about
    # 186 days" while the paragraph on the same page said 187, and 186 is the
    # project's own measured value (it is what reproduces Aaron Judge's figure
    # through the 2020 proration, and it is what config/cba/2022.json holds).
    # A search engine reads both. Deriving both from the ruleset is what makes
    # a contradiction impossible rather than merely fixed.
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
                        f"one who pitches a complete game. {FULL_YEAR_DAYS} days "
                        "make one credited year."
                    ),
                },
            },
            {
                "@type": "Question",
                "name": (
                    f"Why is a service-time year {FULL_YEAR_DAYS} days and not "
                    "a full season?"
                ),
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        f"A major league season runs about {SEASON_SPAN_DAYS} "
                        "days, but the Basic Agreement sets a credited year at "
                        f"{FULL_YEAR_DAYS}. A player on a roster all season is "
                        "credited 1.000 and no more, so the extra days give a "
                        "little slack for a short trip to the minors."
                    ),
                },
            },
            {
                "@type": "Question",
                "name": f"What does a figure like {eg_fig} mean?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        f"It is years and days, not a decimal. {eg_fig} is "
                        f"{eg_years} credited years and {eg_days} days — "
                        f"{eg_years} × {FULL_YEAR_DAYS} + {eg_days} = "
                        f"{eg_total:,} days. Because a year is {FULL_YEAR_DAYS} "
                        "days, the part after the point never reaches "
                        f"{FULL_YEAR_DAYS}."
                    ),
                },
            },
            {
                "@type": "Question",
                "name": "When does a player reach free agency?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": (
                        f"At {int(FREE_AGENCY_YEARS)} credited years — "
                        f"{_yrs(FREE_AGENCY_YEARS)}, or "
                        f"{_days_for(FREE_AGENCY_YEARS):,} days on a major "
                        "league roster. Arbitration eligibility generally "
                        f"arrives at {_yrs(ARBITRATION_YEARS)}, and a Super Two "
                        "player reaches it a year early."
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
  {_site_nav("service-time.html")}
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

  <h2>{FULL_YEAR_DAYS} days make a year</h2>
  <p>A season runs about {SEASON_SPAN_DAYS} days, but the Basic Agreement sets
  a credited year at <b>{FULL_YEAR_DAYS}</b>. A player on a roster from Opening
  Day to the end of the season is credited <b>1.000</b> and no more, so those
  spare days leave a little room for a short trip to the minors without costing
  him the year.</p>
  <p>That is also why figures here look like <b>{eg_fig}</b> rather than
  {eg_years + eg_days / FULL_YEAR_DAYS:.2f}. It is <b>years and days</b>, not a
  decimal: {eg_fig} means {eg_years} credited years and {eg_days} more days, or
  {eg_total:,} days in total. The part after the point never reaches
  {FULL_YEAR_DAYS}.</p>

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
        <td class="n"><b>0.{quarter_days:03d}</b></td><td class="n">{quarter_days}</td>
        <td>One quarter of a year, and the first step toward a pension. Each
        further {quarter_days} days adds to what a player will eventually
        draw.</td>
      </tr>
      <tr>
        <td class="n"><b>{s2_fig}</b></td><td class="n">{s2_days}</td>
        <td><b>Super Two.</b> A player between two and three years who ranks
        in the top {SUPER_TWO_TOP_PERCENTILE}% of that class, with
        {SUPER_TWO_MIN_PRIOR_DAYS}+ days in the preceding season,
        reaches salary arbitration <b>a year early</b> — four trips through it
        instead of three. <b>The cutoff is not fixed</b>: it falls wherever
        the class falls that year.{s2_note}</td>
      </tr>
      <tr>
        <td class="n"><b>{_yrs(ARBITRATION_YEARS)}</b></td>
        <td class="n">{_days_for(ARBITRATION_YEARS):,}</td>
        <td><b>Salary arbitration.</b> Until now the club has set his salary
        near the league minimum. From here he can argue for a raise before an
        arbitration panel, and his pay starts to track his performance.</td>
      </tr>
      <tr>
        <td class="n"><b>{_yrs(FREE_AGENCY_YEARS)}</b></td>
        <td class="n">{_days_for(FREE_AGENCY_YEARS):,}</td>
        <td><b>Free agency.</b> The big one. He can sign with any club that
        wants him, for the first time in his career.</td>
      </tr>
      <tr>
        <td class="n"><b>{_yrs(GOLD_CARD_YEARS)}</b></td>
        <td class="n">{_days_for(GOLD_CARD_YEARS):,}</td>
        <td><b>The gold card.</b> A lifetime pass admitting the holder and a
        guest to any regular-season major league game, at any ballpark.
        Postseason games are excluded.</td>
      </tr>
      <tr>
        <td class="n"><b>{_yrs(MAX_PENSION_YEARS)}</b></td>
        <td class="n">{_days_for(MAX_PENSION_YEARS):,}</td>
        <td><b>The maximum pension.</b> Ten years reaches the top of the
        scale. Fewer than one player in ten ever gets there.</td>
      </tr>
      <tr>
        <td class="n"><b>{_yrs(MAX_PENSION_YEARS)}</b><br /><span class="thr-note">+ 5 straight
        with one club</span></td><td class="n">{_days_for(MAX_PENSION_YEARS):,}</td>
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
      glossary — Service time</a>: {FULL_YEAR_DAYS} days to a credited year,
      and the length of a season.</li>
    <li><a href="https://www.mlb.com/glossary/transactions/super-two">MLB
      glossary — Super Two</a>: two-to-three years,
      {SUPER_TWO_MIN_PRIOR_DAYS}+ days, top {SUPER_TWO_TOP_PERCENTILE}%.</li>
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
  <p>Because {FULL_YEAR_DAYS} days make a year and a season is longer, a club
  that keeps a player in the minors for the first couple of weeks of his rookie
  season leaves him at 0.{FULL_YEAR_DAYS - 1:03d} rather than 1.000 — and pushes
  his free agency back by a full year. The practice is called <b>service-time manipulation</b>, it is
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
