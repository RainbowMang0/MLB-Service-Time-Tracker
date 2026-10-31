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
import salary_estimates  # noqa: E402
import super_two  # noqa: E402
import validate_super_two  # noqa: E402

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
    ("arbitration-eligible.html", "Arbitration eligible", None),
    ("super-two.html", "Super Two", None),
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

# Loaded once, not per page: 1,300+ pages re-reading and re-parsing the same
# small hand-maintained file would be pure waste. See salary_estimates.py for
# what this does and does not know.
_KNOWN_EXTENSIONS = salary_estimates.load_known_extensions()


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


def _player_filename(player: dict) -> str:
    """The filename alone, with no directory -- shared by page_path() below
    and by the teammate links in render(), which live in the same directory
    as the page linking to them and so must never carry the "p/" prefix.
    """
    return f"{player['id']}-{slug(player.get('name'))}.html"


def page_path(player: dict) -> str:
    return f"p/{_player_filename(player)}"


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


PUBLISHED_IDS_PATH = ROOT / "data" / "published_ids.json"


def load_ever_published() -> set[int]:
    """Every player id that has ever had a published page, from `data/published_ids.json`.

    OWNER'S RULE (Braxton, 2026-10-30): a page is never removed because a
    player left a 40-man roster or stopped meeting `_is_alumnus()`. The 2026-
    10-30 daily update dropped five pages -- Verlander, Scherzer and three
    others -- the moment they came off a roster and failed the alumnus test,
    which also broke the two homepage links pointing at them (caught by
    validate_published.py's dead-link check).

    This file is the fix: it only ever grows (see save_ever_published()), so
    an id that lands in it stays published for good, independent of whatever
    `_should_publish()` would say about the player on a given day. The rmtree
    in write_player_pages() still wipes PAGE_DIR/CLUB_DIR/ALUMNI_DIR, but
    `published` is recomputed to include every id here, so the directory is
    rebuilt WITH these pages rather than without them.

    A missing or unreadable file degrades to "nothing is sticky yet", which
    is always safe -- it just means `_should_publish()`'s fresh criteria
    decide, same as before this existed.
    """
    try:
        return {int(x) for x in json.loads(PUBLISHED_IDS_PATH.read_text())}
    except (OSError, ValueError):
        return set()


def save_ever_published(ids: set[int]) -> None:
    """Persist the (only ever growing) set of ids that have had a page."""
    PUBLISHED_IDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PUBLISHED_IDS_PATH.write_text(json.dumps(sorted(ids)) + "\n")


def _should_publish(player: dict, ever_published: set[int] | None = None) -> bool:
    """Does this player get a page?

    `ever_published` is the sticky set from load_ever_published(). Passing it
    makes publication a ratchet: an id already in it stays published even if
    the player has since come off a 40-man roster and no longer passes
    `_is_alumnus()` (see that function's own caveat about which non-rostered
    players stand on their own). Callers that only care about the fresh
    criteria -- the tests, mainly -- can omit it.
    """
    return (
        bool(player.get("on_40_man"))
        or _is_alumnus(player)
        or bool(ever_published and player.get("id") in ever_published)
    )


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


def _min_seasons_to(days_needed: int) -> int:
    """The fewest additional seasons that could possibly cover `days_needed`
    more days, at the maximum creditable rate of FULL_YEAR_DAYS per season --
    i.e. a player who spends the entire time on an active roster or injured
    list from here on. Ceiling division: 1 day needed still costs a season.
    """
    if days_needed <= 0:
        return 0
    return -(-days_needed // FULL_YEAR_DAYS)


def _milestone_sentence(player: dict) -> str:
    """A plain-English statement of standing relative to the CBA thresholds.

    Every number here is read off the ruleset the same way `_threshold_sentence()`
    and the explainer are -- FULL_YEAR_DAYS, ARBITRATION_YEARS, FREE_AGENCY_YEARS
    and _days_for() all come from config/cba/, so a successor agreement moves
    this sentence exactly the way it moves the rest of the page.

    For a player short of a threshold this states a FLOOR, not a projection:
    the fewest more days -- and so the fewest more seasons -- he could reach it
    in, given perfect roster health from today. That is a guarantee about the
    arithmetic ("he cannot possibly get there faster than this"), never a guess
    about his performance or his health. A probability-weighted projection
    exists for this question (docs/data/accrual_model.json, used by
    contract.js) but it is read only in the browser; reproducing it here would
    be a second implementation of the same number, and this generator has no
    reason to guess at a date the engine itself refuses to predict.
    """
    service = player.get("service_time") or "0.000"
    days_total = int(player.get("service_days_total") or 0)

    if not player.get("on_40_man"):
        # Final. There is nothing left to project for a career that is over.
        if player.get("free_agent_eligible"):
            return f"His career closed at {service} years of service, enough to reach free agency."
        if player.get("arbitration_eligible"):
            return (
                f"His career closed at {service} years of service, enough to reach "
                "arbitration eligibility but short of free agency."
            )
        if days_total:
            return f"His career closed at {service} years of service, short of arbitration eligibility."
        return "He never accrued a day of major league service time."

    if player.get("free_agent_eligible"):
        return f"{service} years of service — already eligible for free agency."

    fa_days = _days_for(FREE_AGENCY_YEARS)
    if player.get("arbitration_eligible"):
        seasons = _min_seasons_to(fa_days - days_total)
        track = " on the Super Two track" if player.get("super_two_candidate") else ""
        return (
            f"{service} years of service, arbitration-eligible{track} — at least "
            f"{seasons} more full season{'' if seasons == 1 else 's'} on an active "
            f"roster or injured list to reach free agency ({_yrs(FREE_AGENCY_YEARS)})."
        )

    arb_days = _days_for(ARBITRATION_YEARS)
    seasons = _min_seasons_to(arb_days - days_total)
    return (
        f"{service} years of service — at least {seasons} more full "
        f"season{'' if seasons == 1 else 's'} on an active roster or injured list "
        f"to reach standard arbitration eligibility ({_yrs(ARBITRATION_YEARS)}); "
        "the Super Two cutoff can move him there a year early."
    )


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


def _salary_block(player: dict) -> str:
    """A labelled league-minimum salary ESTIMATE, pre-arbitration players only.

    Delegates the whole decision to salary_estimates.classify() -- this
    function only renders what that returns, so the gating logic (clearly
    pre-arb, no known or detected extension) lives in exactly one place and
    cannot drift from what app.js's lazily-fetched salary_estimates.json
    shows for the same player in the table overlay.

    Returns "" (no block at all) for anyone who is not clearly
    pre-arbitration, which is the correct rendering of "the rule only
    applies to clearly pre-arb players" -- not a missing feature, a player
    for whom the question does not apply the same way.
    """
    record = salary_estimates.classify(player, _KNOWN_EXTENSIONS, _RULES)
    if record is None:
        return ""
    name = html.escape(player.get("name") or "He")
    if record["status"] == salary_estimates.STATUS_EXTENSION:
        note = html.escape(record.get("note") or "")
        return (
            "<div class='salary-note salary-note-extension'>"
            f"<p><strong>Salary:</strong> {name} has a contract or extension on file "
            "beyond the league minimum, so no salary estimate is shown for him. "
            f"{note}</p></div>"
        )
    minimum = record["minimum"]
    minimum_year = record["minimum_year"]
    return (
        "<div class='salary-note salary-note-estimate'>"
        f"<p><strong>Salary (estimate):</strong> {name} is clearly pre-arbitration "
        "(below Super Two, arbitration and free-agency eligibility) and no "
        "contract extension is on file for him, so his pay is most likely the "
        f"{minimum_year} MLB minimum of <b>${minimum:,}</b>. This is the league "
        "minimum from the Basic Agreement, not a confirmed figure for this "
        "player — this project has no permitted source for actual salaries, "
        "and a handful of pre-arbitration players sign extensions that this "
        "page could not detect. If one exists for him, his real pay is higher."
        "</p></div>"
    )


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


def _key_players_line(players: list[dict]) -> str:
    """Name-only links to the players on this roster already at a threshold.

    "Phillies arbitration eligible" is a far commoner search than any single
    player's name (see CLAUDE.md), and the full roster table below already
    answers it -- but burying the answer in a 40-row table gives a crawler no
    reason to treat these names as more relevant than the rest. This surfaces
    them by name, right under the facts grid.

    Grouped by ELIGIBILITY, not by day count, on purpose: a group's membership
    changes only when a threshold is actually crossed, which is a real content
    change and should move the page's lastmod. Sorting or displaying by raw
    day count would instead make the sentence reorder on nearly every
    accruing day, which is exactly the daily-tick problem `_content_key()`
    exists to keep out of the sitemap.
    """
    def _links(group: list[dict]) -> str:
        return ", ".join(
            f"<a href=\"../{page_path(p)}\">{html.escape(p.get('name') or '')}</a>"
            for p in sorted(group, key=lambda p: p.get("name") or "")
        )

    fa = [p for p in players if p.get("free_agent_eligible")]
    s2 = [p for p in players if p.get("super_two_candidate")]
    already_shown = {p["id"] for p in fa} | {p["id"] for p in s2}
    arb = [
        p for p in players
        if p.get("arbitration_eligible") and p["id"] not in already_shown
    ]

    parts = []
    if fa:
        parts.append(f"<b>Free agency eligible:</b> {_links(fa)}.")
    if s2:
        parts.append(f"<b>Super Two track:</b> {_links(s2)}.")
    if arb:
        parts.append(f"<b>Arbitration eligible:</b> {_links(arb)}.")
    return f"<p class='key-players'>{' '.join(parts)}</p>" if parts else ""


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

  {_key_players_line(players)}

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


def _teammates_block(player: dict, teammates: list[dict]) -> str:
    """Links to a handful of this player's current roster mates.

    Capped at 8 rather than the whole roster: the club page already carries
    every name, and copying all 40-45 of them onto every one of its players'
    pages would multiply boilerplate without giving a crawler a reason to
    prefer any one page over another. Picked by current service time so the
    players most likely to be searched surface first; NAMES ONLY, never a
    figure -- a teammate's own day count ticking up must not change the
    rendered HTML of everyone else on his roster, which is exactly the
    daily-tick problem `_content_key()` exists to keep out of the sitemap.

    Callers must pass `[]` for anyone not currently on a 40-man: a retired
    player's stored club is stale (see `_club_phrase()`), so looking up
    "teammates" by that name would list men on today's roster who were never
    on a roster with him at all.
    """
    others = sorted(
        (t for t in teammates if t.get("id") != player.get("id")),
        key=lambda t: (-int(t.get("service_days_total") or 0), t.get("name") or ""),
    )[:8]
    if not others:
        return ""
    items = "".join(
        f"<li><a href=\"{_player_filename(t)}\">{html.escape(t.get('name') or '')}</a></li>"
        for t in others
    )
    return f"<h2>Teammates</h2><ul class='teammates'>{items}</ul>"


def render(
    player: dict,
    team_names: dict[int, str],
    generated_at: str,
    teammates: list[dict] | None = None,
) -> str:
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

  <p class="milestone">{html.escape(_milestone_sentence(player))}</p>

  {caveat}
  {_salary_block(player)}
  <h2>Season by season</h2>
  {_season_rows(player, team_names)}

  {_teammates_block(player, teammates or [])}

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
# _svc_cell() -- the meter used on club and alumni listing pages -- splits the
# Y.DDD figure across two <span>s so the years and the days can be styled
# differently, which means _FIGURE_RE's single \d+\.\d{3} token never matches
# it. Found the same way as _PLAIN_FIGURE_RE: rendering a club page for the
# same player a day apart and diffing the normalised output showed every club
# page moving on nearly every accruing day -- the exact regression this whole
# mechanism exists to prevent, just on a page nobody had re-checked.
_SVC_SPLIT_RE = re.compile(
    r"<span class='svc-years'>\d+</span><span class='svc-days'>\.\d{3}</span>"
)


def _content_key(page_html: str) -> str:
    stable = _VOLATILE_RE.sub("Last updated.", page_html)
    stable = _PLAIN_FIGURE_RE.sub("<b><PLAIN></b>", stable)
    stable = _SVC_SPLIT_RE.sub(
        "<span class='svc-years'><SVC></span><span class='svc-days'><SVC></span>", stable
    )
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
    ever_published: set[int] | None = None,
) -> list[dict]:
    """Regenerate docs/p/ from scratch and return the players published.

    `ever_published` should be the caller's already-loaded sticky set (see
    load_ever_published()) so that it and write_index()'s `has_page` column
    agree on the same snapshot; if omitted this loads it itself. Either way,
    whatever is actually published here is folded back in and saved before
    returning, so an id can only ever be added to the sticky set, never
    removed -- that is the owner's rule that a player's page outlives his
    roster spot.
    """
    generated_at = generated_at or dt.datetime.now(dt.timezone.utc).isoformat()
    if ever_published is None:
        ever_published = load_ever_published()

    team_names: dict[int, str] = {}
    for player in db.values():
        if player.get("team_id") and player.get("team"):
            team_names[int(player["team_id"])] = player["team"]

    published = sorted(
        (p for p in db.values() if _should_publish(p, ever_published)),
        key=lambda p: p.get("name") or "",
    )

    # Rebuilt wholesale, but NOT from scratch in the sense of "only today's
    # criteria": `published` above already folds in every sticky id, so a
    # player who drops off a 40-man and fails `_is_alumnus()` still comes
    # back out of this rmtree+rebuild with his page intact.
    for directory in (PAGE_DIR, CLUB_DIR):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)

    lastmod = _LastMod(generated_at[:10])
    by_club = _group_rostered_by_club(published)

    for player in published:
        # Only a currently-rostered player gets real teammates -- see the
        # warning in _group_rostered_by_club() and _teammates_block().
        teammates = by_club.get(player.get("team"), []) if player.get("on_40_man") else []
        page = render(player, team_names, generated_at, teammates)
        rel = page_path(player)
        (DOCS / rel).write_text(page)
        lastmod.record(rel, page)

    clubs = _write_club_pages(by_club, generated_at, lastmod)
    alumni = [p for p in published if not p.get("on_40_man")]
    alumni_pages = _write_alumni_pages(alumni, generated_at, lastmod) if alumni else []

    _write_page_css()
    explainer = render_explainer(generated_at, super_two_cutoff)
    (DOCS / EXPLAINER_PATH).write_text(explainer)
    lastmod.record(EXPLAINER_PATH, explainer)

    # Offseason pages: who is arbitration-eligible this winter, and where the
    # Super Two line is projected to fall. `published` (not `db`) feeds the
    # selectors so every linked player is guaranteed to have a page -- any
    # on_40_man player is always in `published`, see _should_publish().
    arb_eligible = select_arbitration_eligible(published)
    arb_html = render_arbitration_eligible(
        arb_eligible, generated_at, super_two_cutoff, _KNOWN_EXTENSIONS
    )
    (DOCS / ARBITRATION_ELIGIBLE_PATH).write_text(arb_html, encoding="utf-8")
    lastmod.record(ARBITRATION_ELIGIBLE_PATH, arb_html)

    s2_class = select_super_two_class(published)
    s2_html = render_super_two(s2_class, db, generated_at, super_two_cutoff)
    (DOCS / SUPER_TWO_PAGE_PATH).write_text(s2_html, encoding="utf-8")
    lastmod.record(SUPER_TWO_PAGE_PATH, s2_html)

    _write_sitemap(published, clubs, alumni_pages, generated_at, lastmod)
    lastmod.save()
    _write_robots()
    _write_404()
    _update_homepage_key_players(_pick_key_players(published))
    save_ever_published(ever_published | {p["id"] for p in published if p.get("id") is not None})
    total_kb = sum((DOCS / page_path(p)).stat().st_size for p in published) / 1024
    print(
        f"Wrote {len(published)} player pages ({total_kb / 1024:.1f} MB) to {PAGE_DIR} "
        f"and {len(clubs)} club pages to {CLUB_DIR}; "
        f"{lastmod.changed()} of {len(lastmod.current)} changed content today"
    )
    return published


def _pick_key_players(published: list[dict], limit: int = 8) -> list[dict]:
    """A handful of currently-rostered players worth linking from the homepage.

    Picked by total days credited -- the same field the main table defaults
    to sorting by -- so the names a crawler reaches from a bare `<a href>`
    are the same ones a JavaScript visitor sees first. Not a figure anyone
    reads here, only a sort key, so this does not reintroduce the daily-tick
    problem _content_key() exists to prevent.
    """
    rostered = [p for p in published if p.get("on_40_man")]
    rostered.sort(key=lambda p: (-int(p.get("service_days_total") or 0), p.get("name") or ""))
    return rostered[:limit]


_KEY_PLAYERS_START = "<!-- KEY_PLAYERS_START -->"
_KEY_PLAYERS_END = "<!-- KEY_PLAYERS_END -->"


def _update_homepage_key_players(key_players: list[dict]) -> None:
    """Rewrite the one generated region of the otherwise hand-maintained homepage.

    docs/index.html renders its player table client-side from index.json, so
    a crawler that does not run JavaScript finds no player link anywhere on
    the one page that carries the site's own inbound links -- every player
    page sits three hops from home with exactly one inbound internal link,
    from its club page. This gives a crawler a handful of direct paths in.

    Injected between two HTML comment markers rather than templated whole:
    the rest of index.html is genuinely hand-maintained (the nav, the
    filters, the table shell), and a roster-dependent list of names would go
    stale the moment a player included here dropped off a 40-man -- unlike
    the nav or the "t/" link, which point at URLs that never change. Marking
    out only this one region keeps everything else exactly as a person last
    edited it, and running this script again always makes it current.

    ⚠️ "Always makes it current" describes what this function does to the
    WORKING TREE, not what is live. Both workflows deliberately never `git
    add docs/index.html`, because the rest of the file is hand-edited and
    sweeping it into an automated commit is exactly the risk that exclusion
    exists to avoid -- see the commit_data.sh paths in both workflow YAMLs.
    So today this region is refreshed only when a human runs the pipeline
    and commits the result (as this change does), the same cadence as any
    other index.html edit, NOT every night with the daily job. Players are
    picked by tenure specifically because that population churns slowly, so
    the staleness window this leaves is small; `validate_published.py`'s
    link check (run on every push and PR, see ci.yml) still catches it if a
    picked player ever does drop off a 40-man between updates, the same
    safety net the og:url/canonical check already relies on for this file.
    Wiring this into the daily workflow too is a reasonable follow-up, but it
    is a deliberate change to that long-standing exclusion and belongs in its
    own decision, not bundled into this one.

    If the markers are missing (a hand edit removed them, or this runs
    against a docs/index.html that predates this feature), this is a no-op
    rather than a crash or a silently corrupted file.
    """
    index_path = DOCS / "index.html"
    if not index_path.exists():
        return
    text = index_path.read_text(encoding="utf-8")
    if _KEY_PLAYERS_START not in text or _KEY_PLAYERS_END not in text:
        return
    links = ", ".join(
        f'<a href="{page_path(p)}">{html.escape(p.get("name") or "")}</a>'
        for p in key_players
    )
    replacement = (
        f"{_KEY_PLAYERS_START}\n"
        f"      <p>Notable players on a 40-man roster right now: {links}.</p>\n"
        f"      {_KEY_PLAYERS_END}"
    )
    pattern = re.compile(re.escape(_KEY_PLAYERS_START) + r".*?" + re.escape(_KEY_PLAYERS_END), re.S)
    index_path.write_text(pattern.sub(replacement, text, count=1), encoding="utf-8")


def _group_rostered_by_club(published: list[dict]) -> dict[str, list[dict]]:
    """Every published, CURRENTLY rostered player, grouped by his 40-man club.

    ⚠️ ROSTERED PLAYERS ONLY, and the filter is explicit rather than implied by
    the caller. `published` used to be exactly the 40-man, so grouping it by
    club was the same thing; since _should_publish() widened to alumni it is
    not. A retired player's stored `team` is the last club we saw him with,
    which is stale by construction -- listing him on that club's 40-man page
    (or as a "teammate" of a current player who happens to share that name)
    would assert a roster spot he does not hold, which is the same category
    error already fixed once in the table's payload.

    Shared by the club pages and the per-player teammate links so the two
    groupings cannot drift apart: a player shown as someone's "teammate" but
    missing from that club's own roster page would be exactly the kind of
    silent disagreement this project tests against elsewhere.
    """
    by_club: dict[str, list[dict]] = {}
    for player in published:
        club = player.get("team")
        if club and player.get("on_40_man"):
            by_club.setdefault(club, []).append(player)
    return by_club


def _write_club_pages(
    by_club: dict[str, list[dict]], generated_at: str, lastmod: "_LastMod"
) -> list[str]:
    """One page per club with someone on its 40-man. Returns the club names."""
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
    # The two offseason pages, same tier as the explainer: both are hub-like
    # (link out to many player/club pages) and target their own searches
    # ("arbitration eligible players", "super two cutoff") rather than any
    # one player's name.
    for path in (ARBITRATION_ELIGIBLE_PATH, SUPER_TWO_PAGE_PATH):
        urls.append(
            f"  <url><loc>{SITE_URL}/{path}</loc>"
            f"<lastmod>{lastmod.of(path)}</lastmod>"
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
.milestone { font-size: 0.9rem; color: var(--text-secondary); margin: 0.6rem 0 0;
             max-width: 62ch; border-left: 2px solid var(--accent); padding-left: 0.7rem; }
.milestone:empty { display: none; }
.caveat { background: var(--status-warning-wash); color: var(--text-secondary);
          padding: 0.7rem 0.85rem; border-radius: 6px; font-size: 0.85rem; }
.teammates { list-style: none; display: flex; flex-wrap: wrap; gap: 0.5rem 1rem;
             padding: 0; margin: 0.4rem 0 0; font-size: 0.9rem; }
.teammates a, .key-players a { color: var(--accent); text-decoration: none; }
.teammates a:hover, .key-players a:hover { text-decoration: underline; }
.key-players { font-size: 0.88rem; color: var(--text-secondary); margin: 0.8rem 0 0; }
.key-players b { color: var(--text-primary); }
.back { display: inline-block; margin-bottom: 1rem; color: var(--accent);
        text-decoration: none; }
.foot { margin-top: 1.5rem; font-size: 0.8rem; color: var(--text-muted); line-height: 1.6; }
h1 { letter-spacing: -0.02em; }
.wrap--narrow h1 { font-size: 2.2rem; margin: 0 0 0.6rem; }
.wrap--narrow p { color: var(--text-secondary); line-height: 1.7; }
.wrap--narrow a { color: var(--accent); }
.actions { margin-top: 1.6rem; }
/* The pre-arbitration salary estimate -- see salary_estimates.py. Same
   "note" treatment as .caveat, in the accent wash for an informational
   estimate and the warning wash for "this does not apply to him", so the
   two read as different levels of the same idea rather than unrelated UI. */
.salary-note { margin: 0.9rem 0; padding: 0.7rem 0.85rem; border-radius: 6px;
               font-size: 0.85rem; line-height: 1.6; }
.salary-note p { margin: 0; }
.salary-note-estimate { background: var(--accent-wash); color: var(--text-secondary); }
.salary-note-extension { background: var(--status-warning-wash); color: var(--text-secondary); }
/* The club separator row on the arbitration-eligible page -- a <tr><td
   colspan> rather than a second table per club, so the page stays one
   sortable-by-eye list instead of 30 disconnected tables. */
.arb-club-row td { background: var(--accent-wash); font-weight: 600;
                    padding: 0.5rem 0.6rem; }
.arb-club-row a { color: var(--text-primary); }
.arb-table .badge[title] { cursor: help; }
"""


def _write_page_css() -> None:
    (DOCS / "page.css").write_text(PAGE_CSS)


EXPLAINER_PATH = "service-time.html"
ARBITRATION_ELIGIBLE_PATH = "arbitration-eligible.html"
SUPER_TWO_PAGE_PATH = "super-two.html"

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


# ---------------------------------------------------------------------------
# Offseason pages: who is arbitration-eligible this winter, and where the
# Super Two line is projected to fall. Both are generated from the same
# records as every other page here -- no literals, no re-derivation of a
# rule that already lives in super_two.py or the CBA ruleset.
# ---------------------------------------------------------------------------


def select_arbitration_eligible(players: list[dict]) -> list[dict]:
    """Rostered players arbitration-eligible this coming offseason.

    That is 3+ credited years but short of free agency, OR a Super Two
    candidate (who is by definition under 3.000 and so under free agency
    too). Free-agency-eligible players are excluded even though
    `arbitration_eligible` stays true for them once they cross 3.000 --
    see service_time.py's compute_service_time -- because a 6+ year player
    heads to free agency, not an arbitration hearing.

    Restricted to `on_40_man`: a departed player's `arbitration_eligible`
    flag is recomputed daily by super_two.apply_super_two() against his
    FROZEN total (see that module), so a retired player can carry the flag
    forever. He is not going to a hearing this winter, so he does not
    belong on this page -- the owner's rule that a departed player is never
    described as a current prospect for anything.
    """
    arb_days = _days_for(ARBITRATION_YEARS)
    fa_days = _days_for(FREE_AGENCY_YEARS)
    out = []
    for p in players:
        if not p.get("on_40_man"):
            continue
        days = int(p.get("service_days_total") or 0)
        if days >= fa_days:
            continue
        if days >= arb_days or p.get("super_two_candidate"):
            out.append(p)
    return out


def select_super_two_class(players: list[dict]) -> list[dict]:
    """Rostered players in the LIVE 2.000-3.000 year band, for the Super Two
    page's "who is in the class right now" table.

    Same half-open band super_two.build_class() uses ([2.000, 3.000)), so a
    player sitting at exactly 3.000 is correctly excluded here the same way
    he is excluded from the class the cutoff itself is drawn through.
    Restricted to `on_40_man` for the same reason select_arbitration_eligible
    is: a departed player's clock is frozen and he is not a candidate for
    anything this winter.
    """
    low = 2 * FULL_YEAR_DAYS
    high = 3 * FULL_YEAR_DAYS
    return [
        p for p in players
        if p.get("on_40_man") and low <= int(p.get("service_days_total") or 0) < high
    ]


def _arb_status_badge(player: dict) -> str:
    if player.get("super_two_candidate"):
        return "<span class='badge badge-serious'>Super Two</span>"
    return "<span class='badge badge-warning'>Arbitration eligible</span>"


def _arb_extension_note(player: dict, known_extensions: dict[int, dict]) -> str:
    """A short, clearly-labelled note for a player with a known contract or
    extension on file -- see data/known_extensions.json.

    This page does NOT remove these players from the list: the engine's
    arbitration_eligible flag is about service time alone, and is correct
    regardless of a contract. Hiding them risks a reader concluding a
    rostered, service-time-eligible player simply isn't listed by mistake.
    Labelling instead keeps the list complete and honest about which names
    on it are unlikely to actually see a hearing this winter.
    """
    row = known_extensions.get(player.get("id"))
    if not row:
        return ""
    note = html.escape(row.get("note") or "A contract or extension is on file for him.")
    return (
        "<span class='badge badge-neutral' title='"
        f"{note}'>Contract/extension on file</span>"
    )


def _group_by_club(players: list[dict]) -> dict[str, list[dict]]:
    by_club: dict[str, list[dict]] = {}
    for p in players:
        club = p.get("team")
        if club:
            by_club.setdefault(club, []).append(p)
    return by_club


def render_arbitration_eligible(
    eligible: list[dict],
    generated_at: str,
    super_two_cutoff: dict | None,
    known_extensions: dict[int, dict],
) -> str:
    """"Arbitration-eligible this winter" -- every rostered player with 3+
    but under 6 years of service, plus the Super Two group, grouped by club.

    See select_arbitration_eligible() for exactly who qualifies and why a
    departed player never appears here even if his stale flag says so.
    """
    url = f"{SITE_URL}/{ARBITRATION_ELIGIBLE_PATH}"
    gen_year = int((generated_at or "")[:4]) if generated_at else None
    next_season = (
        (super_two_cutoff.get("season") + 1)
        if super_two_cutoff and super_two_cutoff.get("season")
        else (gen_year + 1 if gen_year else None)
    )
    season_phrase = f" heading into {next_season}" if next_season else ""

    s2_count = sum(1 for p in eligible if p.get("super_two_candidate"))
    standard_count = len(eligible) - s2_count
    club_count = len({p.get("team") for p in eligible if p.get("team")})
    extension_count = sum(1 for p in eligible if p.get("id") in known_extensions)
    extension_sentence = (
        f" {extension_count} of them already have a contract or extension "
        "on file (marked below), so an actual arbitration hearing is "
        "unlikely even though their service time qualifies."
        if extension_count else ""
    )

    desc = (
        f"{len(eligible)} players on a 40-man roster are projected "
        f"arbitration-eligible this winter{season_phrase} -- {standard_count} by "
        f"standard service time ({_yrs(ARBITRATION_YEARS)}+) and {s2_count} as "
        "Super Two. Estimated from this project's own service-time data, not "
        "an official MLB/MLBPA figure."
    )

    rows = []
    for club, players in sorted(_group_by_club(eligible).items()):
        rows.append(
            f"<tr class='arb-club-row'><td colspan='3'><a href=\"{BASE_PATH}{club_path(club)}\">"
            f"{html.escape(club)}</a> <span class='n'>({len(players)})</span></td></tr>"
        )
        for p in sorted(
            players, key=lambda p: (-int(p.get("service_days_total") or 0), p.get("name") or "")
        ):
            note = _arb_extension_note(p, known_extensions)
            rows.append(
                f"<tr><td><a href=\"{BASE_PATH}{page_path(p)}\">"
                f"{html.escape(p.get('name') or '')}</a></td>"
                f"<td class='n'>{html.escape(p.get('service_time') or '0.000')}</td>"
                f"<td>{_arb_status_badge(p)} {note}</td></tr>"
            )

    graph = [
        {"@type": "CollectionPage", "name": "Arbitration-eligible players this winter",
         "url": url, "description": desc},
        _breadcrumb_ld([
            ("Big League Service Time Tracker", f"{SITE_URL}/"),
            ("Arbitration eligible", url),
        ]),
    ]

    title_year = f" ({next_season})" if next_season else ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Arbitration-eligible players this winter{title_year} | Big League Service Time Tracker</title>
<meta name="description" content="{html.escape(desc)}" />
<link rel="canonical" href="{url}" />
<meta property="og:type" content="website" />
<meta property="og:title" content="Arbitration-eligible players this winter{title_year}" />
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
  {_site_nav("arbitration-eligible.html")}
  {_crumbs(("All players", BASE_PATH), ("Arbitration eligible", None))}

  <h1>Arbitration-eligible players this winter{title_year}</h1>
  <p class="subtitle">{len(eligible)} players across {club_count} clubs, by club and
  service time.</p>

  <p class="lede">A player becomes eligible for salary arbitration once he
  reaches {_yrs(ARBITRATION_YEARS)} years of major league service time, and
  stays eligible until he reaches free agency at {_yrs(FREE_AGENCY_YEARS)}.
  A smaller group reaches it a year early under the <b>Super Two</b> rule:
  players with 2.000-3.000 years of service who rank in the top
  {SUPER_TWO_TOP_PERCENTILE}% of that class, with at least
  {SUPER_TWO_MIN_PRIOR_DAYS} days of service in the preceding season. See
  <a href="{BASE_PATH}{SUPER_TWO_PAGE_PATH}">the Super Two page</a> for how
  that line is projected this year.</p>

  <p class="lede">The {len(eligible)} players below are rostered players
  whose service time estimate meets one of those two tests today. Teams
  still choose whether to tender a contract to each of them; this page
  reports eligibility, not any club's decision.{extension_sentence}</p>

  <table class="arb-table"><thead><tr><th>Player</th><th class="n">Service time</th>
  <th>Status</th></tr></thead><tbody>{''.join(rows)}</tbody></table>

  <p class="foot">
    {_threshold_sentence()}
    <a href="{BASE_PATH}service-time.html">what every threshold unlocks</a>.
    <br /><br />
    These figures are <strong>estimates</strong> reconstructed from public
    roster transaction records. They are not official MLB or MLBPA figures —
    those are not published, and this page does not predict which players a
    club actually tenders. See the
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


def _s2_status(player: dict, cutoff_days: int | None) -> str:
    """Plain-English standing of one player relative to the projected line."""
    if player.get("super_two_candidate"):
        return "Projected Super Two"
    if cutoff_days is None:
        return "Cutoff not yet measurable"
    days = int(player.get("service_days_total") or 0)
    if days == cutoff_days:
        return "Right at the projected line"
    if days > cutoff_days:
        return "Above the line, short of the season's day floor"
    return "Below the projected line"


def _s2_history_rows(db: dict[str, dict]) -> list[dict]:
    """This project's computed cutoff vs. the published figure, season by
    season, straight from data/reference_super_two.json -- the only
    independent check super_two.py has. Reuses compute_cutoff() and the
    validator's own tolerance and parser rather than re-deriving either.
    """
    path = ROOT / "data" / "reference_super_two.json"
    try:
        rows = [r for r in json.loads(path.read_text()) if isinstance(r, dict) and r.get("season")]
    except (OSError, ValueError):
        return []
    out = []
    for row in sorted(rows, key=lambda r: -r["season"]):
        season = row["season"]
        cutoff = super_two.compute_cutoff(db, season)
        published = row.get("published")
        delta = ok = None
        if cutoff and published:
            delta = cutoff["cutoff_days"] - validate_super_two._parse(published)
            ok = abs(delta) <= validate_super_two.DEFAULT_TOLERANCE_DAYS
        out.append({
            "season": season,
            "ours": cutoff["cutoff"] if cutoff else None,
            "published": published,
            "delta": delta,
            "ok": ok,
        })
    return out


def render_super_two(
    class_players: list[dict],
    db: dict[str, dict],
    generated_at: str,
    super_two_cutoff: dict | None,
) -> str:
    """The Super Two rule explained, this winter's projected cutoff, who is
    in the live 2-3 year class relative to it, and how this project's
    computed cutoffs have checked out against published figures in the past.

    Every number is read off super_two.py / the CBA ruleset, never
    re-derived: the cutoff and the per-player super_two_candidate flag are
    both produced by scripts/super_two.py and handed in rather than
    recomputed here.
    """
    url = f"{SITE_URL}/{SUPER_TWO_PAGE_PATH}"
    cutoff_days = super_two_cutoff.get("cutoff_days") if super_two_cutoff else None
    season_measured = super_two_cutoff.get("season") if super_two_cutoff else None

    projected_count = sum(1 for p in class_players if p.get("super_two_candidate"))

    if super_two_cutoff:
        cutoff_block = (
            f"<p class='lede'>After the <b>{season_measured}</b> season, the "
            f"2.000-3.000 year class had {super_two_cutoff['class_size']} "
            f"players, and the top {super_two_cutoff['qualifying_count']} of "
            f"them (the top {SUPER_TWO_TOP_PERCENTILE}%) fell at or above "
            f"<b>{super_two_cutoff['cutoff']}</b> years of service "
            f"({super_two_cutoff['cutoff_days']:,} days). That is the line "
            "this site currently projects the live class against, below.</p>"
        )
    else:
        cutoff_block = (
            "<p class='lede'>The class is currently too small to measure a "
            f"cutoff through (fewer than the project's own {_RULES.require('arbitration.super_two.minimum_class_size')} "
            "player floor). No projection is shown.</p>"
        )

    desc = (
        f"This winter's projected Super Two cutoff"
        f"{f' after the {season_measured} season' if season_measured else ''}: "
        f"{super_two_cutoff['cutoff'] if super_two_cutoff else 'not yet measurable'} "
        "years of service. Estimated from this project's own data, not an "
        "official MLB/MLBPA figure."
    )

    rows = "".join(
        f"<tr><td><a href=\"{BASE_PATH}{page_path(p)}\">{html.escape(p.get('name') or '')}</a></td>"
        f"<td>{html.escape(p.get('team') or '—')}</td>"
        f"<td class='n'>{html.escape(p.get('service_time') or '0.000')}</td>"
        f"<td>{html.escape(_s2_status(p, cutoff_days))}</td></tr>"
        for p in sorted(
            class_players, key=lambda p: (-int(p.get("service_days_total") or 0), p.get("name") or "")
        )
    )

    history = _s2_history_rows(db)
    history_rows = "".join(
        "<tr><td>{season}</td><td class='n'>{ours}</td><td class='n'>{pub}</td>"
        "<td>{verdict}</td></tr>".format(
            season=row["season"],
            ours=html.escape(row["ours"] or "—"),
            pub=html.escape(row["published"] or "not entered"),
            verdict=(
                "awaiting a published figure" if row["published"] is None
                else "class too small to measure" if row["ours"] is None
                else ("ok" if row["ok"] else f"off by {row['delta']:+d} days")
            ),
        )
        for row in history
    )

    graph = [
        {"@type": "CollectionPage", "name": "Super Two cutoff projection", "url": url,
         "description": desc},
        _breadcrumb_ld([
            ("Big League Service Time Tracker", f"{SITE_URL}/"),
            ("Super Two", url),
        ]),
    ]

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Super Two cutoff projection | Big League Service Time Tracker</title>
<meta name="description" content="{html.escape(desc)}" />
<link rel="canonical" href="{url}" />
<meta property="og:type" content="website" />
<meta property="og:title" content="Super Two cutoff projection" />
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
  {_site_nav("super-two.html")}
  {_crumbs(("All players", BASE_PATH), ("Super Two", None))}

  <h1>Super Two: this winter's projected cutoff</h1>
  <p class="subtitle">{projected_count} of {len(class_players)} players in the
  live 2.000-3.000 year class project above the line.</p>

  <p class="lede">A player with at least {_RULES.require('arbitration.super_two.service_range_years')[0]:.0f}
  but fewer than {_RULES.require('arbitration.super_two.service_range_years')[1]:.0f}
  years of major league service reaches arbitration a year early -- "Super
  Two" -- if he ranks in the top {SUPER_TWO_TOP_PERCENTILE}% of that class,
  by total service, and accrued at least {SUPER_TWO_MIN_PRIOR_DAYS} days of
  service in the preceding season. MLB does not publish the class or the
  cutoff; this project computes both from its own database of every
  tracked player's season-by-season service time, which is close to the
  league-wide population the rule is defined against.</p>

  {cutoff_block}

  <p class='lede'>The cutoff is <b>not fixed</b> -- it falls wherever that
  year's class falls, and it has moved year to year historically. The
  figures above and below are this project's own estimate, not an
  official MLB/MLBPA number.</p>

  <h2>Who is in the class right now</h2>
  <p>Every rostered player currently between 2.000 and 3.000 years of
  service, most service time first. "Projected Super Two" also requires
  the player to have reached {SUPER_TWO_MIN_PRIOR_DAYS} days of service in
  the season being measured -- a player can sit above the day line and
  still not be flagged yet if he has not reached that floor this season.</p>

  <table><thead><tr><th>Player</th><th>Club</th><th class="n">Service time</th>
  <th>Standing</th></tr></thead><tbody>{rows}</tbody></table>

  <h2>How this project's cutoffs have checked out</h2>
  <p>Computed cutoffs compared against published figures, where one has
  been entered (data/reference_super_two.json). A miss here means a
  per-player service-time defect somewhere in that season's data, not
  necessarily the class or rounding rule -- see
  scripts/validate_super_two.py.</p>

  <table><thead><tr><th>Season</th><th class="n">This site</th>
  <th class="n">Published</th><th>Result</th></tr></thead>
  <tbody>{history_rows}</tbody></table>

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
    The link may be mistyped, or point to a player this site has never
    published a page for. A player's page is never taken down just because he
    comes off a 40-man roster or retires &mdash; once published, it stays, now
    under "Previous players".
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
