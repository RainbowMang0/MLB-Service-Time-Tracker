#!/usr/bin/env python3
"""
build_offseason_page.py
-----------------------
Writes the offseason hub at docs/offseason.html.

The page only links files that are already on disk, in offseason-calendar
order. Dates that already live on the roster guide are read from there.
The agreement's end date is read from the ruleset. Nothing here computes
service time.

  python3 scripts/build_offseason_page.py
  python3 scripts/build_offseason_page.py --check
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_roster_rules_page as guide  # noqa: E402
import cba  # noqa: E402
import mlb_clubs  # noqa: E402
import page_titles  # noqa: E402
import write_player_pages as wpp  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PATH = "offseason.html"
OUT_FILE = ROOT / "docs" / PATH

LEDE = "Dates and lists for this winter, from public data."

# The three dates the hub leads with. Each one is also a link.
KEY_FACTS = (
    ("rule-5.html", "Rule 5 protection"),
    (wpp.NON_TENDER_WATCH_PATH, "Non-tender deadline"),
    ("lockout.html", "CBA expiration"),
)

_MONTHS = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

_CALENDAR_ORDER = (
    wpp.FREE_AGENTS_PATH,
    "contract.html",
    "rule-5.html",
    wpp.SEND_DOWN_PATH,
    "out-of-options.html",
    wpp.ARBITRATION_ELIGIBLE_PATH,
    wpp.NON_TENDER_WATCH_PATH,
    "lockout.html",
)


def _iso(long_form: str) -> str:
    """'November 17, 2026' -> '2026-11-17'. The display string stays the source."""
    month_name, rest = long_form.split(" ", 1)
    day, year = rest.replace(",", "").split()
    month = _MONTHS.index(month_name)
    return dt.date(int(year), month, int(day)).isoformat()


def _agreement_end(rules: cba.Ruleset) -> tuple[str, str]:
    """ISO date and the calendar label. Empty when the ruleset has no end."""
    raw = (rules.effective_end or "")[:10]
    if len(raw) < 10:
        return "", ""
    day = dt.date.fromisoformat(raw)
    return day.isoformat(), f"{_MONTHS[day.month]} {day.day}, {day.year}"


def _a(rel: str, label: str) -> str:
    href = html.escape(f"{wpp.BASE_PATH}{rel}", quote=True)
    return f'<a href="{href}">{html.escape(label)}</a>'


def calendar_groups(docs: Path) -> list[dict]:
    """Offseason order. A link is left out when that file is not on disk."""
    _iso_end, agreement = _agreement_end(cba.default())
    spec = [
        {
            "when": "After the 2026 season",
            "heading": "After the 2026 season",
            "iso": "",
            "items": [
                (
                    wpp.FREE_AGENTS_PATH,
                    "Free agents",
                    "Players who reached free agency after the 2026 season.",
                ),
                (
                    "contract.html",
                    "Contract clock",
                    "Years until arbitration and free agency.",
                ),
            ],
        },
        {
            "when": guide.RESERVE_LIST_DATE,
            "heading": f"{guide.RESERVE_LIST_DATE} · Rule 5 protection deadline",
            "iso": _iso(guide.RESERVE_LIST_DATE),
            "items": [
                (
                    "rule-5.html",
                    "Rule 5 protection board",
                    f"Reserve list due. The draft is scheduled for {guide.RULE_5_DRAFT_DATE}.",
                ),
                (
                    wpp.SEND_DOWN_PATH,
                    "Can a team send a player down?",
                    "Options, a designation for assignment, and an outright.",
                ),
                (
                    "out-of-options.html",
                    "Out of options",
                    "40-man players who have used every option year.",
                ),
            ],
        },
        {
            "when": guide.NON_TENDER_DATE,
            "heading": f"{guide.NON_TENDER_DATE} · Non-tender deadline",
            "iso": _iso(guide.NON_TENDER_DATE),
            "items": [
                (
                    wpp.ARBITRATION_ELIGIBLE_PATH,
                    "Arbitration eligible",
                    "Players projected arbitration-eligible this winter.",
                ),
                (
                    wpp.NON_TENDER_WATCH_PATH,
                    "Non-tender watch",
                    "The watch list before the deadline.",
                ),
            ],
        },
    ]
    if agreement:
        spec.append(
            {
                "when": agreement,
                "heading": f"{agreement} · CBA expiration",
                "iso": _iso_end,
                "items": [
                    (
                        "lockout.html",
                        "Lockout tracker",
                        "The agreement expires, and what a lockout means for service time.",
                    ),
                ],
            }
        )
    kept = []
    for group in spec:
        items = [item for item in group["items"] if (docs / item[0]).is_file()]
        if not items:
            continue
        group = dict(group)
        group["items"] = items
        kept.append(group)
    return kept


def club_rows(docs: Path) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """Clubs that have at least one arbitration, free-agent, or non-tender page."""
    rows = []
    for name, _abbr in mlb_clubs.CLUB_ABBR:
        links = []
        for rel, label in (
            (wpp.club_free_agents_path(name), "Free agents"),
            (wpp.club_arbitration_path(name), "Arbitration"),
            (wpp.club_non_tender_path(name), "Non-tender"),
            (f"t/{wpp.slug(name)}-out-of-options.html", "Out of options"),
        ):
            if (docs / rel).is_file():
                links.append((rel, label))
        if not links:
            continue
        roster = wpp.club_path(name)
        rows.append((name, roster if (docs / roster).is_file() else "", links))
    return rows


def _facts_html(docs: Path) -> str:
    when_of = {}
    for group in calendar_groups(docs):
        for rel, _label, _note in group["items"]:
            when_of[rel] = group["when"]
    cells = []
    for rel, caption in KEY_FACTS:
        when = when_of.get(rel)
        if not when:
            continue
        cells.append(
            f"    <div><span>{html.escape(caption)}</span><b>{_a(rel, when)}</b></div>"
        )
    if not cells:
        return ""
    return '  <div class="facts">\n' + "\n".join(cells) + "\n  </div>"


def _calendar_html(docs: Path) -> str:
    blocks = []
    for group in calendar_groups(docs):
        heading = html.escape(group["heading"])
        if group["iso"]:
            stamp = f'<time datetime="{html.escape(group["iso"])}">{heading}</time>'
        else:
            stamp = f"<time>{heading}</time>"
        notes = [
            f'    <p class="foot-note">{_a(rel, label)} — {html.escape(note)}</p>'
            for rel, label, note in group["items"]
        ]
        blocks.append("  <li>\n    " + stamp + "\n" + "\n".join(notes) + "\n  </li>")
    return '<h2>Calendar</h2>\n<ol class="pp-tl">\n' + "\n".join(blocks) + "\n</ol>"


def _clubs_html(docs: Path) -> str:
    lis = []
    for name, roster, links in club_rows(docs):
        parts = [_a(roster, name)] if roster else [html.escape(name)]
        parts.extend(_a(rel, label) for rel, label in links)
        lis.append(f"  <li>{' · '.join(parts)}</li>")
    if not lis:
        return ""
    return '<h2>By club</h2>\n<ul class="sources">\n' + "\n".join(lis) + "\n</ul>"


def render(docs: Path | None = None) -> str:
    docs = docs or (ROOT / "docs")
    title = page_titles.offseason_title()
    og = page_titles.offseason_og()
    agreement = _agreement_end(cba.default())[1]
    description = page_titles.offseason_meta(agreement)
    url = f"{wpp.SITE_URL}/{PATH}"
    graph = [
        {
            "@type": "CollectionPage",
            "name": og,
            "url": url,
            "description": description,
        },
        wpp._breadcrumb_ld([
            ("Big League Service Time Tracker", f"{wpp.SITE_URL}/"),
            ("Offseason", url),
        ]),
    ]
    players = wpp.BASE_PATH or "/"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
{wpp.HEAD_TAGS}<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}" />
<link rel="canonical" href="{url}" />
<meta property="og:type" content="website" />
<meta property="og:title" content="{html.escape(og)}" />
<meta property="og:description" content="{html.escape(description)}" />
<meta property="og:url" content="{url}" />
{wpp._ld_script(graph)}
<link rel="icon" href="{wpp.BASE_PATH}favicon.svg" type="image/svg+xml" />
<link rel="stylesheet" href="{wpp.BASE_PATH}styles.css" />
<link rel="stylesheet" href="{wpp.BASE_PATH}page.css" />
<script>
  try {{
    var t = localStorage.getItem("mlb-service-time-theme");
    if (t) document.documentElement.setAttribute("data-theme", t);
  }} catch (e) {{}}
</script>
</head>
<body>
<div class="viz-root">{wpp._site_header(None)}<div class="wrap">
  {wpp._crumbs(("Players", players), ("Offseason", None))}

  <h1>MLB offseason</h1>
  <p class="lede">{html.escape(LEDE)}</p>
{_facts_html(docs)}
{_calendar_html(docs)}
{_clubs_html(docs)}
</div>{wpp._site_footer()}{wpp._search_script_tag()}</div>
{wpp.ANALYTICS}
</body>
</html>
"""


def _missing_targets(page: str, docs: Path) -> list[str]:
    missing = []
    for href in re.findall(r'href="([^"]+)"', page):
        if href.startswith(("mailto:", "#")):
            continue
        if href.startswith(("http://", "https://")):
            if not href.startswith(f"{wpp.SITE_URL}/") and href.rstrip("/") != wpp.SITE_URL:
                missing.append(href)
            continue
        clean = href.split("#", 1)[0].split("?", 1)[0]
        if not clean:
            continue
        if clean.startswith("/"):
            rel = clean[len(wpp.BASE_PATH):] if clean.startswith(wpp.BASE_PATH) else clean[1:]
            target = (docs / rel) if rel else docs
        else:
            target = docs / clean
        if clean.endswith("/") or target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            missing.append(href)
    return missing


def problems(page: str, docs: Path | None = None) -> list[str]:
    """Editorial checks. Empty means the page is safe to write."""
    docs = docs or (ROOT / "docs")
    found: list[str] = []

    def need(label: str, ok: bool) -> None:
        if not ok:
            found.append(label)

    title = page_titles.offseason_title()
    _iso_end, agreement = _agreement_end(cba.default())
    description = page_titles.offseason_meta(agreement)
    url = f"{wpp.SITE_URL}/{PATH}"
    need("title", f"<title>{html.escape(title)}</title>" in page)
    need("title length", len(title) <= page_titles.TITLE_MAX)
    need("description", f'content="{html.escape(description)}"' in page)
    need("description length", len(description) <= page_titles.META_MAX)
    need("public data in the description", "public data" in description.lower())
    need("canonical", f'rel="canonical" href="{url}"' in page)
    need("one h1", page.count("<h1>") == 1)
    need("one lede", page.count('<p class="lede">') == 1)
    lede = " ".join(re.search(r'<p class="lede">(.*?)</p>', page, re.S).group(1).split())
    need("lede is the one short sentence", lede == LEDE)
    need("lede is one sentence", lede.count(".") == 1 and lede.endswith("."))
    need("adsense", "ca-pub-8748967682918425" in page)
    need("share image", 'property="og:image"' in page)
    calendar = page.split("<h2>Calendar</h2>", 1)[-1].split("<h2>By club</h2>", 1)[0]
    positions = [calendar.find(f'href="{wpp.BASE_PATH}{rel}"') for rel in _CALENDAR_ORDER]
    need(
        "calendar order",
        all(pos >= 0 for pos in positions) and positions == sorted(positions),
    )
    facts = ""
    if '<div class="facts">' in page and "<h2>Calendar</h2>" in page:
        facts = page.split('<div class="facts">', 1)[1].split("<h2>Calendar</h2>", 1)[0]
    for rel, _caption in KEY_FACTS:
        need(f"key date links {rel}", f'href="{wpp.BASE_PATH}{rel}"' in facts)
    need("rule 5 date", guide.RESERVE_LIST_DATE in page)
    need("rule 5 iso", f'datetime="{_iso(guide.RESERVE_LIST_DATE)}"' in page)
    need("non-tender date", guide.NON_TENDER_DATE in page)
    need("draft date", guide.RULE_5_DRAFT_DATE in page)
    _iso_end, agreement = _agreement_end(cba.default())
    if agreement:
        need("cba expiration", agreement in page)
        need("cba iso", f'datetime="{_iso_end}"' in page)
    for rel in _CALENDAR_ORDER:
        if (docs / rel).is_file():
            need(f"links {rel}", f'href="{wpp.BASE_PATH}{rel}"' in page)
    club_dir = docs / "t"
    if club_dir.is_dir():
        for pattern in (
            "*-arbitration.html", "*-free-agents.html", "*-non-tender.html",
            "*-out-of-options.html",
        ):
            for path in club_dir.glob(pattern):
                rel = f"t/{path.name}"
                need(f"links {rel}", f'href="{wpp.BASE_PATH}{rel}"' in page)
    dead = _missing_targets(page, docs)
    need("no dead links", not dead)
    if dead:
        found.extend(f"dead link {href}" for href in dead[:8])
    lowered = page.lower()
    for banned in (
        "last updated",
        "baseball-reference",
        "baseball reference",
        "fangraphs",
        "spotrac",
        "cot's",
        "mlb trade rumors",
        "trade rumors",
        "public mlb data",
        "scripts/",
        ".py",
        "pipeline",
        "github.com",
        "should",
        "p.m.",
        "a.m.",
        "23:59",
    ):
        need(f"no {banned!r}", banned not in lowered)
    return found


def write(path: Path = OUT_FILE) -> str:
    page = render()
    bad = problems(page)
    if bad:
        raise SystemExit("offseason hub failed checks: " + "; ".join(bad))
    path.write_text(page, encoding="utf-8")
    return page


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate only, write nothing")
    parser.add_argument("--out", type=Path, default=OUT_FILE)
    args = parser.parse_args(argv)
    page = render()
    bad = problems(page)
    if args.check:
        if not args.out.exists():
            bad.append(f"missing {args.out}")
        elif args.out.read_text(encoding="utf-8") != page:
            bad.append(f"{args.out} does not match the builder")
        if bad:
            print("offseason hub check failed:", file=sys.stderr)
            for item in bad:
                print(f"  - {item}", file=sys.stderr)
            return 1
        print(f"{args.out.name}: ok")
        return 0
    if bad:
        print("offseason hub failed checks:", file=sys.stderr)
        for item in bad:
            print(f"  - {item}", file=sys.stderr)
        return 1
    args.out.write_text(page, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
