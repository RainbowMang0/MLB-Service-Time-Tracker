#!/usr/bin/env python3
"""
build_dfa_outcomes_page.py
--------------------------
Writes docs/dfa-outcomes.html from the stored transaction feeds.

The page lists recent designations and the move that resolved each one.
It does not compute service time. A failure here must not discard a
page that is already on disk: the new files are written to temp names
and renamed only after both are ready.

  python3 scripts/build_dfa_outcomes_page.py
  python3 scripts/build_dfa_outcomes_page.py --check
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dfa_outcomes  # noqa: E402
import write_player_pages as wpp  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PATH = "dfa-outcomes.html"
OUT_FILE = ROOT / "docs" / PATH
DATA_FILE = ROOT / "data" / "dfa_outcomes.json"
GUIDE = "send-a-player-down.html"

# The document title the page is published under. It names the search and
# the site. The shorter share title is the same words without the brand.
TITLE = (
    "MLB DFA tracker: designated for assignment outcomes "
    "| Big League Service Time Tracker"
)
OG_TITLE = "MLB DFA tracker: designated for assignment outcomes"
DESCRIPTION = (
    "Recent designated-for-assignment outcomes: traded, claimed, outrighted, "
    "released, elected free agency, or still pending. From public data."
)
LEDE = (
    "Recent designated-for-assignment moves, and what happened to each player afterward."
)
EMPTY_PENDING = "No designation in this window is still waiting on an outcome."
EMPTY_RECENT = "No designation in this span."

_MONTHS = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

_COLUMNS = (
    "Player",
    "Club",
    "DFA date",
    "Outcome",
    "Outcome date",
    "New club",
    "Service time",
)


def long_date(iso: str) -> str:
    day = dt.date.fromisoformat(iso)
    return f"{_MONTHS[day.month]} {day.day}, {day.year}"


def load_players(db_path: Path, docs: Path) -> dict[int, dict]:
    """Name, stored service-time figure, and player-page path. No math."""
    try:
        data = json.loads(db_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    records = data.get("players") if isinstance(data, dict) else None
    if not isinstance(records, list):
        return {}
    folder = docs / "p"
    found: dict[int, dict] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        pid = record.get("id")
        if isinstance(pid, bool) or not isinstance(pid, int):
            continue
        name = " ".join(str(record.get("name") or "").split())
        href = ""
        if folder.is_dir():
            matches = sorted(folder.glob(f"{pid}-*.html"))
            if matches:
                href = f"{wpp.BASE_PATH}p/{matches[0].name}"
        found[pid] = {
            "name": name,
            "service_time": record.get("service_time") or "",
            "href": href,
        }
    return found


def build_payload(
    today: dt.date | None = None,
    root: Path | None = None,
) -> dict:
    root = root or ROOT
    today = today or dt.date.today()
    window = dfa_outcomes.window_days()
    players = load_players(root / "docs" / "data" / "service_time.json", root / "docs")
    grouped = dfa_outcomes.group_cache(root / "data" / "cache" / "transactions")
    rows = dfa_outcomes.collect(
        grouped, players, today=today, window_days=window
    )
    return {
        "as_of": today.isoformat(),
        "window_days": window,
        "span_days": dfa_outcomes.SPAN_DAYS,
        "rows": rows,
    }


def load_payload(path: Path | None = None) -> dict:
    path = path or DATA_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"as_of": "", "window_days": 0, "span_days": dfa_outcomes.SPAN_DAYS, "rows": []}
    if not isinstance(data, dict):
        return {"as_of": "", "window_days": 0, "span_days": dfa_outcomes.SPAN_DAYS, "rows": []}
    rows = data.get("rows")
    if not isinstance(rows, list):
        rows = []
    try:
        window = int(data.get("window_days") or 0)
    except (TypeError, ValueError):
        window = 0
    return {
        "as_of": str(data.get("as_of") or ""),
        "window_days": window,
        "span_days": dfa_outcomes.SPAN_DAYS,
        "rows": [row for row in rows if isinstance(row, dict)],
    }


def _clubs(rows: list[dict]) -> list[dict]:
    """One representative row per designating club, in the site's club order."""
    seen: dict[int, dict] = {}
    for row in rows:
        club_id = row.get("club_id")
        if not isinstance(club_id, int) or club_id in seen or not row.get("club"):
            continue
        seen[club_id] = row
    return sorted(
        seen.values(),
        key=lambda row: (dfa_outcomes.sheet_index(row.get("club_id")), row.get("club") or ""),
    )


def _slug(name: str) -> str:
    return wpp.slug(name)


def _time(iso: str, css: str = "") -> str:
    if not iso:
        return ""
    try:
        label = long_date(iso)
    except ValueError:
        return html.escape(iso)
    cls = f' class="{css}"' if css else ""
    return f'<time{cls} datetime="{html.escape(iso)}">{html.escape(label)}</time>'


def _player(row: dict) -> str:
    name = html.escape(str(row.get("name") or "Player"))
    href = str(row.get("href") or "")
    if href:
        return f'<a href="{html.escape(href, quote=True)}">{name}</a>'
    return name


def _cells(row: dict) -> list[str]:
    days = row.get("days_left")
    days_left = days if isinstance(days, int) else None
    outcome = dfa_outcomes.outcome_label(str(row.get("outcome") or ""), days_left)
    return [
        _player(row),
        html.escape(str(row.get("club") or "")),
        _time(str(row.get("dfa_date") or ""), "dfa-date"),
        html.escape(outcome),
        _time(str(row.get("outcome_date") or "")),
        html.escape(str(row.get("new_club") or "")),
        html.escape(str(row.get("service_time") or "")),
    ]


def _table(rows: list[dict]) -> str:
    head = "".join(f"<th scope=\"col\">{html.escape(label)}</th>" for label in _COLUMNS)
    body = []
    for row in rows:
        tds = "".join(f"<td>{cell}</td>" for cell in _cells(row))
        body.append(f"    <tr>{tds}</tr>")
    return (
        '<div class="tbl-wrap">\n'
        '<table class="pp-tbl">\n'
        f"  <thead><tr>{head}</tr></thead>\n"
        "  <tbody>\n"
        + "\n".join(body)
        + "\n  </tbody>\n</table>\n</div>"
    )


def _nav(clubs: list[dict]) -> str:
    links = ['<a href="#recent">All clubs</a>']
    for row in clubs:
        slug = _slug(str(row.get("club") or ""))
        links.append(
            f'<a href="#{html.escape(slug, quote=True)}">{html.escape(str(row.get("club") or ""))}</a>'
        )
    return (
        '<nav class="list-links dfa-clubs" aria-label="Clubs">'
        + " \u00b7 ".join(links)
        + "</nav>"
    )


def _note(window: int) -> str:
    guide = html.escape(f"{wpp.BASE_PATH}{GUIDE}#dfa", quote=True)
    return (
        '<p class="foot-note">A designation takes a player off the 40-man roster. '
        f"The club has {window} days to trade him, place him on waivers, or release him. "
        "If he clears waivers he can be sent to the minors, and a player who rejects "
        f'that assignment becomes a free agent. <a href="{guide}">How a designation works</a>.</p>'
    )


def render(payload: dict | None = None) -> str:
    payload = payload if payload is not None else load_payload()
    rows = [row for row in (payload.get("rows") or []) if isinstance(row, dict)]
    rows = list(rows)
    rows.sort(
        key=lambda row: (
            -dfa_outcomes._date_key(str(row.get("dfa_date") or "")),
            str(row.get("name") or "").casefold(),
            row.get("id") or 0,
        )
    )
    try:
        window = int(payload.get("window_days") or 0)
    except (TypeError, ValueError):
        window = 0
    pending = [row for row in rows if row.get("outcome") == "pending"]
    clubs = _clubs(rows)
    url = f"{wpp.SITE_URL}/{PATH}"
    graph = [
        {
            "@type": "CollectionPage",
            "name": OG_TITLE,
            "url": url,
            "description": DESCRIPTION,
        },
        wpp._breadcrumb_ld([
            ("Big League Service Time Tracker", f"{wpp.SITE_URL}/"),
            ("DFA outcomes", url),
        ]),
    ]
    players = wpp.BASE_PATH or "/"
    if pending:
        pending_body = "\n" + _table(pending)
    else:
        pending_body = f"\n  <p>{html.escape(EMPTY_PENDING)}</p>"
    if rows:
        recent_body = "\n" + _table(rows)
    else:
        recent_body = f"\n  <p>{html.escape(EMPTY_RECENT)}</p>"
    sections = []
    for row in clubs:
        name = str(row.get("club") or "")
        slug = _slug(name)
        owned = [item for item in rows if item.get("club_id") == row.get("club_id")]
        sections.append(
            f'<section id="{html.escape(slug, quote=True)}">\n'
            f"  <h2>{html.escape(name)}</h2>\n"
            f"{_table(owned)}\n"
            "</section>"
        )
    club_html = "\n".join(sections)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
{wpp.HEAD_TAGS}<title>{html.escape(TITLE)}</title>
<meta name="description" content="{html.escape(DESCRIPTION)}" />
<link rel="canonical" href="{url}" />
<meta property="og:type" content="website" />
<meta property="og:title" content="{html.escape(OG_TITLE)}" />
<meta property="og:description" content="{html.escape(DESCRIPTION)}" />
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
  {wpp._crumbs(("Players", players), ("DFA outcomes", None))}

  <h1>MLB DFA tracker</h1>
  <p class="lede">{html.escape(LEDE)}</p>
<section id="pending">
  <h2>Still in the window</h2>{pending_body}
</section>
{_nav(clubs)}
<section id="recent">
  <h2>Recent designations</h2>{recent_body}
</section>
{club_html}
{_note(window)}
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


def problems(page: str, docs: Path | None = None, payload: dict | None = None) -> list[str]:
    """Editorial checks. Empty means the page is safe to write."""
    docs = docs or (ROOT / "docs")
    payload = payload if payload is not None else load_payload()
    found: list[str] = []

    def need(label: str, ok: bool) -> None:
        if not ok:
            found.append(label)

    url = f"{wpp.SITE_URL}/{PATH}"
    try:
        window = int(payload.get("window_days") or 0)
    except (TypeError, ValueError):
        window = 0
    need("title", f"<title>{html.escape(TITLE)}</title>" in page)
    need("share title", f'property="og:title" content="{html.escape(OG_TITLE)}"' in page)
    need("description", f'content="{html.escape(DESCRIPTION)}"' in page)
    need("description length", len(DESCRIPTION) <= 155)
    need("public data in the description", "public data" in DESCRIPTION.lower())
    need("canonical", f'rel="canonical" href="{url}"' in page)
    need("one h1", page.count("<h1>") == 1)
    need("one lede", page.count('<p class="lede">') == 1)
    match = re.search(r'<p class="lede">(.*?)</p>', page, re.S)
    lede = " ".join(match.group(1).split()) if match else ""
    need("lede is the one short sentence", lede == LEDE)
    need("lede is one sentence", lede.count(".") == 1 and lede.endswith("."))
    need("pending section", 'id="pending"' in page)
    need("recent section", 'id="recent"' in page)
    need("adsense", "ca-pub-8748967682918425" in page)
    need("share image", 'property="og:image"' in page)
    need("guide link", f'href="{wpp.BASE_PATH}{GUIDE}#dfa"' in page)
    if window:
        need("window length comes from the ruleset", f"The club has {window} days" in page)
    rows = [row for row in (payload.get("rows") or []) if isinstance(row, dict)]
    recent = page.split('id="recent"', 1)[-1].split("<section", 1)[0]
    for row in rows:
        name = html.escape(str(row.get("name") or ""))
        iso = str(row.get("dfa_date") or "")
        need(f"row {name} {iso}", name in recent and f'datetime="{iso}"' in recent)
        club = str(row.get("club") or "")
        if club:
            slug = _slug(club)
            need(f"anchor {slug}", f'id="{slug}"' in page and f'href="#{slug}"' in page)
    pending_html = ""
    if 'id="pending"' in page and 'id="recent"' in page:
        pending_html = page.split('id="pending"', 1)[1].split('id="recent"', 1)[0]
    for row in rows:
        if row.get("outcome") != "pending":
            continue
        name = html.escape(str(row.get("name") or ""))
        need(f"pending {name}", name in pending_html and "Pending," in pending_html)
    dates = re.findall(r'<time class="dfa-date" datetime="(\d{4}-\d{2}-\d{2})"', recent)
    need("newest designation first", dates == sorted(dates, reverse=True))
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
        "should",
        "scripts/",
        ".py",
        "github.com",
    ):
        need(f"no {banned!r}", banned not in lowered)
    return found


def _commit(files: list[tuple[Path, str]]) -> None:
    """Write every file to a temp name, then rename. A crash keeps the old bytes."""
    staged: list[tuple[Path, Path]] = []
    try:
        for path, text in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            tmp.write_text(text, encoding="utf-8")
            staged.append((tmp, path))
        for tmp, path in staged:
            os.replace(tmp, path)
    except Exception:
        for tmp, _path in staged:
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def _summary(rows: list[dict]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        kind = str(row.get("outcome") or "")
        counts[kind] = counts.get(kind, 0) + 1
    parts = [f"{len(rows)} rows", f"pending {counts.get('pending', 0)}"]
    for kind in dfa_outcomes.OUTCOMES:
        parts.append(f"{kind} {counts.get(kind, 0)}")
    parts.append(f"unresolved {counts.get('unresolved', 0)}")
    return "DFA outcomes: " + ", ".join(parts) + "."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate only, write nothing")
    args = parser.parse_args(argv)
    if args.check:
        if not DATA_FILE.is_file() or not OUT_FILE.is_file():
            print("dfa outcomes check failed: page or snapshot is missing", file=sys.stderr)
            return 1
        stored = load_payload()
        page = render(stored)
        bad = problems(page, ROOT / "docs", stored)
        if OUT_FILE.read_text(encoding="utf-8") != page:
            bad.append(f"{OUT_FILE} does not match the builder")
        try:
            as_of = dt.date.fromisoformat(stored.get("as_of") or "")
        except ValueError:
            as_of = None
            bad.append("snapshot has no as_of date")
        if as_of is not None:
            fresh = build_payload(as_of)
            if fresh.get("rows") != stored.get("rows"):
                bad.append("snapshot does not match the stored transactions")
            if fresh.get("window_days") != stored.get("window_days"):
                bad.append("snapshot window does not match the ruleset")
        if bad:
            print("dfa outcomes check failed:", file=sys.stderr)
            for item in bad:
                print(f"  - {item}", file=sys.stderr)
            return 1
        print(f"{OUT_FILE.name}: ok")
        return 0

    payload = build_payload()
    page = render(payload)
    bad = problems(page, ROOT / "docs", payload)
    if bad:
        print("dfa outcomes page failed checks:", file=sys.stderr)
        for item in bad:
            print(f"  - {item}", file=sys.stderr)
        return 1
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    _commit([(DATA_FILE, text), (OUT_FILE, page)])
    print(_summary(payload["rows"]))
    print(f"wrote {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
