#!/usr/bin/env python3
"""
build_changes_page.py
---------------------
Writes the daily change log at docs/changes.html from data/changes.json.

The page is a list of dated groups. It does not compute service time.

  python3 scripts/build_changes_page.py --check
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import page_titles  # noqa: E402
import write_player_pages as wpp  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PATH = "changes.html"
OUT_FILE = ROOT / "docs" / PATH
LOG_FILE = ROOT / "data" / "changes.json"

LEDE = "Service time thresholds and roster moves from public data, newest day first."

_MONTHS = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

MOVE_LABELS = (
    ("call-up", "Call-ups"),
    ("option", "Options"),
    ("dfa", "Designated for assignment"),
    ("outright", "Outrights"),
    ("release", "Releases"),
    ("signing", "Signings"),
    ("trade", "Trades"),
    ("claim", "Waiver claims"),
    ("il", "Injured list"),
)


def long_date(iso: str) -> str:
    day = dt.date.fromisoformat(iso)
    return f"{_MONTHS[day.month]} {day.day}, {day.year}"


def year_heading(years: int) -> str:
    if years == 1:
        return "Reached 1 year"
    return f"Reached {years} years"


def correction_sentence(corrections: dict) -> str:
    """One line. Counts only — never a list of names."""
    try:
        up = int(corrections.get("up") or 0)
        down = int(corrections.get("down") or 0)
    except (TypeError, ValueError):
        return ""
    parts = []
    if up:
        parts.append(f"{up} player up" if up == 1 else f"{up} players up")
    if down:
        parts.append(f"{down} player down" if down == 1 else f"{down} players down")
    if not parts:
        return ""
    return "Service time corrections: " + ", ".join(parts) + "."


def load_log(path: Path | None = None) -> dict:
    path = path or LOG_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"days": []}
    if not isinstance(data, dict):
        return {"days": []}
    days = data.get("days")
    if not isinstance(days, list):
        days = []
    return {"days": [day for day in days if isinstance(day, dict) and day.get("date")]}


def player_href(player_id, docs: Path) -> str | None:
    """The published player page, when the site has one."""
    try:
        pid = int(player_id)
    except (TypeError, ValueError):
        return None
    folder = docs / "p"
    if not folder.is_dir():
        return None
    matches = sorted(folder.glob(f"{pid}-*.html"))
    if not matches:
        return None
    return f"{wpp.BASE_PATH}p/{matches[0].name}"


def _person(item: dict, docs: Path) -> str:
    name = html.escape(str(item.get("name") or "Player"))
    href = player_href(item.get("id"), docs)
    who = f'<a href="{html.escape(href, quote=True)}">{name}</a>' if href else name
    team = " ".join(str(item.get("team") or "").split())
    if team:
        return f"{who} · {html.escape(team)}"
    return who


def _group(title: str, items: list[dict], docs: Path) -> str:
    ordered = sorted(
        items,
        key=lambda item: ((item.get("name") or "").casefold(), item.get("id") or 0),
    )
    lines = "\n".join(f"    <li>{_person(item, docs)}</li>" for item in ordered)
    return f"  <h3>{html.escape(title)}</h3>\n  <ul class=\"sources\">\n{lines}\n  </ul>"


def _day(day: dict, docs: Path) -> str:
    iso = str(day.get("date") or "")
    blocks = []
    by_year: dict[int, list] = {}
    for item in day.get("thresholds") or []:
        if not isinstance(item, dict):
            continue
        try:
            years = int(item.get("years"))
        except (TypeError, ValueError):
            continue
        by_year.setdefault(years, []).append(item)
    for years in sorted(by_year):
        blocks.append(_group(year_heading(years), by_year[years], docs))
    super_two = [item for item in (day.get("super_two") or []) if isinstance(item, dict)]
    if super_two:
        blocks.append(_group("Super Two range", super_two, docs))
    by_kind: dict[str, list] = {}
    for item in day.get("moves") or []:
        if isinstance(item, dict) and item.get("kind"):
            by_kind.setdefault(str(item["kind"]), []).append(item)
    for kind, label in MOVE_LABELS:
        if by_kind.get(kind):
            blocks.append(_group(label, by_kind[kind], docs))
    sentence = ""
    if isinstance(day.get("corrections"), dict):
        sentence = correction_sentence(day["corrections"])
    if sentence:
        blocks.append(f"  <p>{html.escape(sentence)}</p>")
    try:
        stamp = long_date(iso)
    except ValueError:
        stamp = iso
    body = "\n".join(blocks)
    return (
        "<section>\n"
        f'  <h2><time datetime="{html.escape(iso)}">{html.escape(stamp)}</time></h2>\n'
        f"{body}\n"
        "</section>"
    )


def _days(log: dict, docs: Path) -> str:
    days = [day for day in (log.get("days") or []) if isinstance(day, dict)]
    days.sort(key=lambda day: str(day.get("date") or ""), reverse=True)
    return "\n".join(_day(day, docs) for day in days)


def render(log: dict | None = None, docs: Path | None = None) -> str:
    docs = docs or (ROOT / "docs")
    log = log if log is not None else load_log()
    title = page_titles.changes_title()
    og = page_titles.changes_og()
    description = page_titles.changes_meta()
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
            ("What changed", url),
        ]),
    ]
    players = wpp.BASE_PATH or "/"
    body = _days(log, docs)
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
  {wpp._crumbs(("Players", players), ("What changed", None))}

  <h1>What changed</h1>
  <p class="lede">{html.escape(LEDE)}</p>
{body}
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


def problems(page: str, docs: Path | None = None, log: dict | None = None) -> list[str]:
    """Editorial checks. Empty means the page is safe to write."""
    docs = docs or (ROOT / "docs")
    found: list[str] = []

    def need(label: str, ok: bool) -> None:
        if not ok:
            found.append(label)

    title = page_titles.changes_title()
    description = page_titles.changes_meta()
    url = f"{wpp.SITE_URL}/{PATH}"
    need("title", f"<title>{html.escape(title)}</title>" in page)
    need("title length", len(title) <= page_titles.TITLE_MAX)
    need("description", f'content="{html.escape(description)}"' in page)
    need("description length", len(description) <= page_titles.META_MAX)
    need("public data in the description", "public data" in description.lower())
    need("canonical", f'rel="canonical" href="{url}"' in page)
    need("one h1", page.count("<h1>") == 1)
    need("one lede", page.count('<p class="lede">') == 1)
    match = re.search(r'<p class="lede">(.*?)</p>', page, re.S)
    lede = " ".join(match.group(1).split()) if match else ""
    need("lede is the one short sentence", lede == LEDE)
    need("lede is one sentence", lede.count(".") == 1 and lede.endswith("."))
    need("adsense", "ca-pub-8748967682918425" in page)
    need("share image", 'property="og:image"' in page)
    if log:
        for day in log.get("days") or []:
            iso = str(day.get("date") or "")
            if iso:
                need(f"date {iso}", f'datetime="{iso}"' in page)
            sentence = ""
            if isinstance(day.get("corrections"), dict):
                sentence = correction_sentence(day["corrections"])
            if sentence:
                need(
                    "correction line",
                    f"<p>{html.escape(sentence)}</p>" in page,
                )
    # Header links are the shared chrome. Check them when this docs tree is
    # the real site. A scratch tree used by a test has no index.html.
    if (docs / "index.html").is_file():
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
        "github.com",
    ):
        need(f"no {banned!r}", banned not in lowered)
    return found


def write_text(path: Path, page: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate only, write nothing")
    parser.add_argument("--out", type=Path, default=OUT_FILE)
    args = parser.parse_args(argv)
    log = load_log()
    page = render(log)
    bad = problems(page, ROOT / "docs", log)
    if args.check:
        if not args.out.exists():
            bad.append(f"missing {args.out}")
        elif args.out.read_text(encoding="utf-8") != page:
            bad.append(f"{args.out} does not match the builder")
        if not log.get("days"):
            bad.append("the change log has no days")
        if bad:
            print("changes page check failed:", file=sys.stderr)
            for item in bad:
                print(f"  - {item}", file=sys.stderr)
            return 1
        print(f"{args.out.name}: ok")
        return 0
    if bad:
        print("changes page failed checks:", file=sys.stderr)
        for item in bad:
            print(f"  - {item}", file=sys.stderr)
        return 1
    write_text(args.out, page)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
