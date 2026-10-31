#!/usr/bin/env python3
"""
build_lockout_page.py
----------------------
Reads data/lockout_updates.json and writes docs/lockout.html.

WHY THIS EXISTS
===============
The 2026 CBA expires December 1, 2026 and a lockout is widely expected the
next day. That is a live news story, and this project wants to carry it
without turning "update the lockout page" into a code change. So the page
is generated from one small, hand-edited data file
(data/lockout_updates.json) -- editing that file and pushing it to main is
the entire update. See that file's own _README for the fields.

This script is deliberately separate from write_player_pages.py: it does
not touch any player, club or alumni page, and a bad edit to
data/lockout_updates.json can only ever break this one page, never the
5,600-player pipeline. It reuses write_player_pages's GA/AdSense/contact
constants rather than re-declaring them, so the two cannot drift apart --
see that module's HEAD_TAGS/ANALYTICS docstring for why that matters.

WHAT IS VALIDATED
=================
Every field data/lockout_updates.json is required to have, every date is
checked for being real ISO (and lockout_start is required to be the day
regular business froze, not CBA expiry itself, so it may equal or trail
cba_expires but is never validated against it beyond being a real date),
every entry needs an https source, and all text is HTML-escaped at render
time regardless of what's already in the file -- the file is hand-edited by
one person, but a page that broke on a stray "<" would be a strange way to
find that out.

USAGE
=====
  python3 scripts/build_lockout_page.py             # validate and write docs/lockout.html
  python3 scripts/build_lockout_page.py --check      # validate only, write nothing
  python3 scripts/build_lockout_page.py --today 2026-12-10   # pin "today" (tests, previews)
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import write_player_pages as wpp  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "lockout_updates.json"
OUT_FILE = ROOT / "docs" / "lockout.html"

SITE_URL = wpp.SITE_URL
PAGE_URL = f"{SITE_URL}/lockout.html"

REQUIRED_TOP_FIELDS = (
    "cba_expires", "lockout_start", "lockout_end",
    "regular_season_delayed_from", "updated", "entries",
)
REQUIRED_ENTRY_FIELDS = ("date", "headline", "summary", "source_name", "source_url")


class LockoutDataError(ValueError):
    """A problem with data/lockout_updates.json, meant to fail CI loudly."""


def _parse_iso_date(value: str, field: str) -> dt.date:
    if not isinstance(value, str):
        raise LockoutDataError(f"{field}: expected an ISO date string, got {value!r}")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise LockoutDataError(f"{field}: {value!r} is not a valid ISO date (YYYY-MM-DD)") from exc


def _parse_optional_iso_date(value, field: str) -> dt.date | None:
    if value is None:
        return None
    return _parse_iso_date(value, field)


def validate(data: dict) -> None:
    """Raises LockoutDataError on anything a bad hand-edit could introduce.

    Checked here rather than left to render-time KeyErrors so CI reports a
    clear message instead of a traceback, and so the same check protects
    both the daily build and the push-triggered one.
    """
    if not isinstance(data, dict):
        raise LockoutDataError("top level must be a JSON object")

    missing = [f for f in REQUIRED_TOP_FIELDS if f not in data]
    if missing:
        raise LockoutDataError(f"missing required field(s): {', '.join(missing)}")

    _parse_iso_date(data["cba_expires"], "cba_expires")
    _parse_optional_iso_date(data["lockout_start"], "lockout_start")
    _parse_optional_iso_date(data["lockout_end"], "lockout_end")
    _parse_optional_iso_date(data["regular_season_delayed_from"], "regular_season_delayed_from")
    _parse_iso_date(data["updated"], "updated")

    entries = data["entries"]
    if not isinstance(entries, list):
        raise LockoutDataError("entries must be a list")

    for i, entry in enumerate(entries):
        label = f"entries[{i}]"
        if not isinstance(entry, dict):
            raise LockoutDataError(f"{label}: must be an object")
        missing = [f for f in REQUIRED_ENTRY_FIELDS if f not in entry or entry[f] in (None, "")]
        if missing:
            raise LockoutDataError(f"{label}: missing required field(s): {', '.join(missing)}")
        _parse_iso_date(entry["date"], f"{label}.date")
        url = entry["source_url"]
        if not isinstance(url, str) or not url.startswith("https://"):
            raise LockoutDataError(f"{label}.source_url: must be an https:// URL, got {url!r}")
        for field in ("headline", "summary", "source_name"):
            if not isinstance(entry[field], str) or not entry[field].strip():
                raise LockoutDataError(f"{label}.{field}: must be a non-empty string")


def load(path: pathlib.Path = DATA_FILE) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LockoutDataError(f"{path}: not found") from exc
    except json.JSONDecodeError as exc:
        raise LockoutDataError(f"{path}: invalid JSON ({exc})") from exc
    validate(data)
    return data


def sorted_entries(data: dict) -> list[dict]:
    """Newest first, regardless of the order they were hand-typed in."""
    return sorted(data["entries"], key=lambda e: e["date"], reverse=True)


# ---------------------------------------------------------------------------
# Day-counting conventions. Both are INCLUSIVE of the start date -- the day
# a lockout begins is "day 1" of it, matching how the 2021-22 lockout's
# widely reported 99-day length is counted (Dec 2, 2021 through Mar 10, 2022
# inclusive). The same convention applies to days of a season missed once
# regular_season_delayed_from is set.
# ---------------------------------------------------------------------------

def days_until(today: dt.date, target: dt.date) -> int:
    """Simple, EXCLUSIVE-of-today difference: "30 days until" on the 30th day
    before, zero on the day itself. Not the same inclusive convention as the
    day counters above -- a countdown counts what's left, not an elapsed
    span, so there is nothing to include."""
    return (target - today).days


def day_number(today: dt.date, start: dt.date) -> int:
    return (today - start).days + 1


def compute_status(data: dict, today: dt.date) -> dict:
    """The one status block the page (and its inline JS twin) renders from.

    `kind` is "countdown" (no lockout yet), "lockout" (one is underway or
    over -- lockout_end does not change the day count shown, it's still
    reported as the day the stoppage reached) or "resolved" (today is past
    cba_expires with no lockout_start -- a deal was reached in time, which
    this project does not predict, only reports once the file says so).
    """
    cba_expires = _parse_iso_date(data["cba_expires"], "cba_expires")
    lockout_start = _parse_optional_iso_date(data["lockout_start"], "lockout_start")
    delayed_from = _parse_optional_iso_date(
        data["regular_season_delayed_from"], "regular_season_delayed_from"
    )

    if lockout_start is not None:
        kind = "lockout"
        day = max(day_number(today, lockout_start), 1)
    elif today >= cba_expires:
        kind = "resolved"
        day = None
    else:
        kind = "countdown"
        day = days_until(today, cba_expires)

    days_at_stake = None
    if delayed_from is not None and today >= delayed_from:
        days_at_stake = day_number(today, delayed_from)

    return {
        "kind": kind,
        "day": day,
        "days_at_stake": days_at_stake,
        "cba_expires": cba_expires.isoformat(),
        "lockout_start": lockout_start.isoformat() if lockout_start else None,
        "regular_season_delayed_from": delayed_from.isoformat() if delayed_from else None,
    }


def status_headline(status: dict) -> str:
    if status["kind"] == "lockout":
        text = f"Day {status['day']} of the lockout"
    elif status["kind"] == "resolved":
        text = "The CBA has expired without a lockout on record in this file"
    else:
        n = status["day"]
        text = f"{n} day{'s' if n != 1 else ''} until the CBA expires (Dec. 1, 2026)"
    if status["days_at_stake"] is not None:
        n = status["days_at_stake"]
        text += f" — an estimated {n} service-time day{'s' if n != 1 else ''} at stake so far"
    return text


def last_updated(data: dict) -> dt.date:
    updated = _parse_iso_date(data["updated"], "updated")
    entries = sorted_entries(data)
    if entries:
        newest = _parse_iso_date(entries[0]["date"], "entries[0].date")
        return max(updated, newest)
    return updated


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

FAQ = [
    (
        "Does service time accrue during an MLB lockout?",
        "No. A player only earns a day of major league service time for a "
        "day spent on a major league roster, and a lockout freezes that "
        "entirely — there is no roster to be on. Time already earned before "
        "a lockout is never taken away; it simply stops adding up until "
        "play resumes.",
    ),
    (
        "What's the difference between a lockout and a strike?",
        "A lockout is called by the owners, who shut business down and bar "
        "players from club facilities. A strike is called by the players, "
        "who refuse to work. The 2021-22 work stoppage was a lockout; "
        "earlier disputes in MLB history, including the 1994-95 stoppage, "
        "were players' strikes.",
    ),
    (
        "When does the current CBA expire, and when could a lockout start?",
        "The agreement expires December 1, 2026. In the last round of "
        "bargaining, owners locked players out within a day of the old deal "
        "expiring, so a similar timeline is widely expected again if no new "
        "agreement is reached first.",
    ),
    (
        "Are free-agent signings and trades frozen during a lockout?",
        "Yes, for any player on a 40-man roster. Clubs cannot sign free "
        "agents, make trades, or complete other 40-man transactions while a "
        "lockout is in effect, which is why free-agent activity typically "
        "rushes to beat the deadline.",
    ),
    (
        "Does a lockout affect salary arbitration or minor league options?",
        "A lockout halts the business side of the sport broadly, which has "
        "historically pushed back tender deadlines and arbitration-related "
        "scheduling along with everything else. Minor league option rules "
        "themselves are unaffected by a lockout — see our "
        f'<a href="{{base}}service-time.html">service time explainer</a> '
        "for how options interact with the clock.",
    ),
    (
        "How long did the last MLB lockout last?",
        "99 days: it began December 2, 2021 and ended March 10, 2022. "
        "Opening Day moved from March 31 to April 7, but the two sides "
        "agreed to play a full 162-game season on a compressed schedule, so "
        "no games were ultimately missed.",
    ),
]


def _stat_tile(value: str, label: str) -> str:
    return f'<div class="stat-tile"><div class="value">{html.escape(value)}</div><div class="label">{html.escape(label)}</div></div>'


def _render_entries(entries: list[dict]) -> str:
    rows = []
    for e in entries:
        rows.append(
            "<li class='lockout-entry'>"
            f"<p class='lockout-entry-date'>{html.escape(e['date'])}</p>"
            f"<p class='lockout-entry-headline'><b>{html.escape(e['headline'])}</b></p>"
            f"<p class='lockout-entry-summary'>{html.escape(e['summary'])}</p>"
            f"<p class='lockout-entry-source'>Source: "
            f"<a href=\"{html.escape(e['source_url'])}\" rel=\"noopener\" target=\"_blank\">"
            f"{html.escape(e['source_name'])}</a></p>"
            "</li>"
        )
    return "<ul class='lockout-entries'>" + "".join(rows) + "</ul>"


def _render_faq() -> tuple[str, list[dict]]:
    html_parts = []
    ld_items = []
    for q, a in FAQ:
        a_plain = a.replace("{base}", "/")
        html_parts.append(f"<h3>{html.escape(q)}</h3><p>{a_plain}</p>")
        # Strip the one internal <a> tag for the structured-data answer text,
        # same text a search result would actually read aloud.
        a_text = a.replace("{base}", "/")
        for tag in ("<a href=\"/service-time.html\">", "</a>"):
            a_text = a_text.replace(tag, "")
        ld_items.append({
            "@type": "Question",
            "name": q,
            "acceptedAnswer": {"@type": "Answer", "text": a_text},
        })
    return "".join(html_parts), ld_items


def render(data: dict, today: dt.date) -> str:
    status = compute_status(data, today)
    headline = status_headline(status)
    entries = sorted_entries(data)
    updated = last_updated(data)

    desc = (
        "Tracking the 2026 MLB-MLBPA collective bargaining talks and the "
        "lockout expected once the agreement expires Dec. 1, 2026 — plain-"
        "English updates, key dates, and what a lockout means for service "
        "time, Super Two, and free agency."
    )

    tiles = [_stat_tile(headline.split(" — ")[0], "Status")]
    if status["days_at_stake"] is not None:
        tiles.append(_stat_tile(str(status["days_at_stake"]), "Service-time days at stake"))

    faq_html, faq_ld = _render_faq()

    graph = [
        {
            "@type": "Article",
            "headline": "MLB CBA Talks & 2026 Lockout Tracker",
            "description": desc,
            "datePublished": "2026-10-31",
            "dateModified": updated.isoformat(),
            "mainEntityOfPage": PAGE_URL,
            "url": PAGE_URL,
            "author": {"@type": "Organization", "name": "Big League Service Time Tracker"},
            "publisher": {"@type": "Organization", "name": "Big League Service Time Tracker"},
        },
        {"@type": "FAQPage", "mainEntity": faq_ld},
        wpp._breadcrumb_ld([
            ("Big League Service Time Tracker", f"{SITE_URL}/"),
            ("CBA & Lockout", PAGE_URL),
        ]),
    ]

    status_json = json.dumps({
        "cba_expires": status["cba_expires"],
        "lockout_start": status["lockout_start"],
        "regular_season_delayed_from": status["regular_season_delayed_from"],
    })

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<!-- Google Analytics. Hand-maintained here because this page is generated by
     its own small script, not scripts/write_player_pages.py -- see that
     module's GA_MEASUREMENT_ID/ADSENSE_CLIENT_ID. Change both together. -->
<script async src="https://www.googletagmanager.com/gtag/js?id={wpp.GA_MEASUREMENT_ID}"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', '{wpp.GA_MEASUREMENT_ID}');
</script>
<!-- End Google Analytics -->
<!-- Google AdSense -->
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={wpp.ADSENSE_CLIENT_ID}" crossorigin="anonymous"></script>
<meta name="google-adsense-account" content="{wpp.ADSENSE_CLIENT_ID}">
<!-- End Google AdSense -->
<title>MLB CBA Talks &amp; 2026 Lockout Tracker | Big League Service Time</title>

<meta name="description" content="{html.escape(desc)}" />
<meta name="theme-color" content="#0a0c10" />
<meta property="og:type" content="website" />
<meta property="og:title" content="MLB CBA Talks &amp; 2026 Lockout Tracker" />
<meta property="og:description" content="{html.escape(desc)}" />
<link rel="canonical" href="{PAGE_URL}" />
<meta property="og:url" content="{PAGE_URL}" />
<meta name="twitter:card" content="summary" />
{wpp._ld_script(graph)}

<script>
  try {{
    var t = localStorage.getItem("mlb-service-time-theme");
    if (t) document.documentElement.setAttribute("data-theme", t);
  }} catch (e) {{}}
</script>

<link rel="icon" href="/favicon.svg" type="image/svg+xml" />
<link rel="stylesheet" href="styles.css" />
</head>
<body>
  <div class="viz-root">

    <header class="page-header">
      <div class="page-header-inner">
        <h1>
          <svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
            <circle cx="16" cy="16" r="14" class="bm-ball" />
            <path d="M7.2 5.6c3.6 3 5.4 6.5 5.4 10.4s-1.8 7.4-5.4 10.4" class="bm-seam" />
            <path d="M24.8 5.6c-3.6 3-5.4 6.5-5.4 10.4s1.8 7.4 5.4 10.4" class="bm-seam" />
          </svg>
          <span>CBA &amp; Lockout Tracker</span>
        </h1>
        <p class="subtitle">Plain-English updates on the 2026 CBA talks, and what a lockout would mean for the game's clock.</p>
      </div>
    </header>

      <nav class="site-nav" aria-label="Site sections"><a href="./">Service time</a><a href="t/">By club</a><a href="alumni/">Previous players</a><a href="arbitration-eligible.html">Arbitration eligible</a><a href="super-two.html">Super Two</a><a href="service-time.html">What is service time?</a><a href="lockout.html" aria-current="page">CBA &amp; Lockout</a><a href="contract.html">Contract clock <span class="nav-tag">in development</span></a><a href="taxes.html">Duty days <span class="nav-tag">in development</span></a><a href="neutrality.html">Neutrality</a></nav>

    <main class="tax-main">

      <p class="lede">The current Basic Agreement between MLB and the
      players' union expires <b>December 1, 2026</b>. Owners are widely
      expected to lock players out shortly after, as they did in 2021. This
      page tracks that timeline and, once a lockout starts, how long it has
      run — updated from <a href="https://github.com/RainbowMang0/MLB-Service-Time-Tracker#readme" rel="noopener">one small public data file</a>, not a prediction of how talks end.</p>

      <section class="stat-tiles" aria-label="Lockout status" id="lockout-tiles">
        {''.join(tiles)}
      </section>
      <p class="panel-note" id="lockout-status-note">{html.escape(headline)}</p>
      <script id="lockout-status-data" type="application/json">{status_json}</script>
      <script>
        (function () {{
          try {{
            var data = JSON.parse(document.getElementById("lockout-status-data").textContent);
            var today = new Date();
            today.setHours(0, 0, 0, 0);
            function parseISO(s) {{
              if (!s) return null;
              var p = s.split("-").map(Number);
              return new Date(p[0], p[1] - 1, p[2]);
            }}
            function daysBetween(a, b) {{ return Math.round((b - a) / 86400000); }}
            var expires = parseISO(data.cba_expires);
            var lockoutStart = parseISO(data.lockout_start);
            var delayedFrom = parseISO(data.regular_season_delayed_from);
            var text;
            if (lockoutStart) {{
              var day = Math.max(daysBetween(lockoutStart, today) + 1, 1);
              text = "Day " + day + " of the lockout";
            }} else if (today >= expires) {{
              text = "The CBA has expired without a lockout on record in this file";
            }} else {{
              var n = daysBetween(today, expires);
              text = n + (n === 1 ? " day" : " days") + " until the CBA expires (Dec. 1, 2026)";
            }}
            if (delayedFrom && today >= delayedFrom) {{
              var missed = Math.max(daysBetween(delayedFrom, today) + 1, 1);
              text += " — an estimated " + missed + (missed === 1 ? " service-time day" : " service-time days") + " at stake so far";
            }}
            document.getElementById("lockout-status-note").textContent = text;
            var valueEl = document.querySelector("#lockout-tiles .stat-tile .value");
            if (valueEl) valueEl.textContent = text.split(" — ")[0];
          }} catch (e) {{ /* static build value stays as-is */ }}
        }})();
      </script>

      <section class="panel">
        <h2>Latest updates</h2>
        <p class="panel-note">Last updated {html.escape(updated.isoformat())}.</p>
        {_render_entries(entries)}
      </section>

      <section class="panel">
        <h2>What's being negotiated</h2>
        <p class="panel-note">
          The Basic Agreement is the contract between MLB's 30 clubs and the
          players' union (the MLBPA) that governs pay, free agency,
          arbitration, roster rules and more. Based on public reporting as
          talks continue, the clubs have proposed a salary cap paired with a
          minimum payroll floor, and the union has rejected a cap while
          pushing for a higher minimum salary and other player-side gains.
          Other reported topics include the length of the regular season and
          playoff format. None of this is final — it is what has been
          reported, not an agreement.
        </p>
      </section>

      <section class="panel">
        <h2>Lockout vs. strike, and the key dates</h2>
        <p class="panel-note">
          A <b>lockout</b> is management shutting the business down and
          barring players from club facilities; a <b>strike</b> is players
          refusing to work. The 2021-22 stoppage was a lockout: it began
          December 2, 2021 and ran 99 days, ending March 10, 2022. Opening
          Day moved from March 31 to April 7, but the sides later agreed to
          play a full 162-game season anyway, so no games were ultimately
          missed. The current agreement expires December 1, 2026.
        </p>
      </section>

      <section class="panel">
        <h2>How a lockout affects service time, Super Two and arbitration</h2>
        <p class="panel-note">
          No <a href="service-time.html">service time</a> accrues on days no
          games are played — the clock only moves while a player is on a
          major league roster. In 2021-22, the eventual deal restored a full
          162-game season, so no player's service time or free-agency timeline
          ended up shorter for it; that is public history, not a guarantee
          about any future lockout. A lockout has historically pushed back
          offseason business broadly, including salary arbitration scheduling
          and tender deadlines, and it has no effect on the
          <a href="super-two.html">Super Two</a> rule's mechanics themselves —
          only on how much of a season there ends up being to measure it
          against.
        </p>
      </section>

      <section class="panel">
        <h2>Minor league options, free agency and transactions</h2>
        <p class="panel-note">
          During a lockout, clubs cannot sign free agents, make trades, or
          complete other transactions involving players on a 40-man roster —
          business involving players not on a 40-man roster, including most
          minor league option moves, is generally unaffected. See
          <a href="arbitration-eligible.html">who's arbitration-eligible this
          winter</a> for the group whose contracts are usually decided right
          around when a lockout would hit.
        </p>
      </section>

      <section class="panel">
        <h2>FAQ</h2>
        {faq_html}
      </section>

      <p class="foot">
        This page is an independent tracker, not a prediction of how
        negotiations end. It reports what has been publicly reported, in our
        own words, and links to the original source for every item above.
        <br /><br />
        Not affiliated with or endorsed by Major League Baseball or any team.
        Service time figures are our own calculations from public data.
        <br /><br />
        <a href="about.html">About</a> &middot;
        <a href="privacy.html">Privacy</a> &middot;
        <a href="mailto:{wpp.CONTACT_EMAIL}">{wpp.CONTACT_EMAIL}</a>
      </p>

    </main>

    <footer class="page-footer">
      <p>
        Not affiliated with or endorsed by Major League Baseball or any team.
        Service time figures are our own calculations from public data.
      </p>
      <p><a href="./">Service time tracker</a> &middot; <a href="privacy.html">Privacy</a> &middot; <a href="neutrality.html">Neutrality</a> &middot; <a href="service-time.html">What is service time?</a> &middot; <a href="mailto:{wpp.CONTACT_EMAIL}">{wpp.CONTACT_EMAIL}</a></p>
    </footer>
  </div>
  <!-- Cloudflare Web Analytics; see the note in taxes.html. --><script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{{"token": "b1a0b70a9b8944a9892a99bc56e22e15"}}'></script>
</body>
</html>
"""


def build(data_file: pathlib.Path = DATA_FILE, out_file: pathlib.Path = OUT_FILE,
          today: dt.date | None = None) -> str:
    data = load(data_file)
    page = render(data, today or dt.date.today())
    out_file.write_text(page, encoding="utf-8")
    return page


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-file", type=pathlib.Path, default=DATA_FILE)
    parser.add_argument("--out", type=pathlib.Path, default=OUT_FILE)
    parser.add_argument("--check", action="store_true", help="validate only, write nothing")
    parser.add_argument("--today", type=str, default=None, help="pin today's date, ISO format")
    args = parser.parse_args(argv)

    try:
        data = load(args.data_file)
        if args.check:
            print(f"{args.data_file}: ok ({len(data['entries'])} entries)")
            return 0
        today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
        page = render(data, today)
        args.out.write_text(page, encoding="utf-8")
        print(f"wrote {args.out}")
        return 0
    except LockoutDataError as exc:
        print(f"lockout data error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
