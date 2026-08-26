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
    6.000 reaches free agency. Eligibility counts describe players currently
    on a 40-man roster.
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
</body>
</html>
"""


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
        f"<td class='n'>{html.escape(p.get('service_time') or '0.000')}</td>"
        f"<td class='n'>{p.get('service_days_total', 0)}</td>"
        f"<td>{html.escape(_status(p))}</td></tr>"
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
    <div><span>Free agency eligible</span>{fa}</div>
    <div><span>Arbitration eligible</span>{arb}</div>
    <div><span>Super Two track</span>{s2}</div>
  </div>

  <table><thead><tr><th>Player</th><th>Pos</th><th class="n">Service time</th>
  <th class="n">Days</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>

  <p class="foot">
    172 days credit a full year, so a season adds at most 1.000 no matter how
    long a player is on a roster. 3.000 years reaches salary arbitration and
    6.000 reaches free agency.
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


LASTMOD_PATH = DOCS / "data" / "page_lastmod.json"

# The footer stamp moves every day on every page whether or not anything about
# the player changed, so it has to come out before hashing or every page looks
# modified daily -- which is precisely the lie this is here to stop telling.
_VOLATILE_RE = re.compile(r"Last updated \d{4}-\d{2}-\d{2}\.")


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
.facts div span { display: block; font-size: 0.7rem; text-transform: uppercase;
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
