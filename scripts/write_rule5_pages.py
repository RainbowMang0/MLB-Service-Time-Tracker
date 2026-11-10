#!/usr/bin/env python3
"""Write the Rule 5 board from data/rule5/rule5_2026.json.

No API calls. The yearly pull lives in fetch_rule5.py and is not part
of the daily update.

    python3 scripts/write_rule5_pages.py
"""

from __future__ import annotations

import html
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).parent))

import page_titles  # noqa: E402
import write_player_pages as wpp  # noqa: E402

DATA = ROOT / "data" / "rule5" / "rule5_2026.json"
DOCS = ROOT / "docs"

# Named in public previews of this year's class, in that order.
# A display sort only. Not a rank.
HAND_CHECKED = (
    801739,  # Carlos Lagrange
    700649,  # Henry Lalane
    802126,  # Joe Whitman
    815219,  # Luis De La Torre
    700248,  # Marty Gair
    686846,  # Reggie Crawford
    692368,  # Adrián Sugastey
    806611,  # Jancel Villarroel
    694754,  # Sabin Ceballos
    687218,  # Will Bednar
    702373,  # Felix Arronde
)
_HAND_INDEX = {pid: i for i, pid in enumerate(HAND_CHECKED)}

ESTIMATE_BANNER = (
    "Eligibility estimated from public signing data; some cases are marked unknown."
)

LEAGUE_FAQ = (
    (
        "When is the 2026 Rule 5 reserve-list deadline?",
        "November 17, 2026. The draft is scheduled for December 9, 2026. "
        "The labor agreement expires December 1, 2026, so that draft date may change.",
    ),
    (
        "Who is Rule 5 eligible for the first time in 2026?",
        "A player left off the 40-man whose first professional contract reaches "
        "its fourth or fifth December meeting in 2026. Age is taken on the June 5 "
        "before that contract: 18 or younger means the fifth meeting, and 19 or "
        "older means the fourth. A major league debut does not start the clock.",
    ),
    (
        "Why are some players marked estimated or unknown?",
        "Estimated means the earliest of more than one professional signing was "
        "used, or the player is a winter or spring international signee whose "
        "June 5 age date is unsettled. Unknown means a birth date or signing "
        "date is missing, or a fall signing falls where the public schedule does "
        "not settle whether that December meeting counts.",
    ),
)


def sort_players(players: list[dict]) -> list[dict]:
    """Hand-checked names first. Everyone else by level, then draft round."""

    def key(player: dict):
        pid = player.get("player_id")
        if pid in _HAND_INDEX:
            return (0, _HAND_INDEX[pid], 0, "", player.get("name") or "")
        draft = player.get("draft_sort") or [999, ""]
        return (
            1,
            player.get("level_rank") if player.get("level_rank") is not None else 9,
            draft[0],
            draft[1] if len(draft) > 1 else "",
            player.get("name") or "",
        )

    return sorted(players, key=key)


def _page_index() -> dict[int, str]:
    found = {}
    folder = DOCS / "p"
    if not folder.is_dir():
        return found
    for path in folder.glob("*.html"):
        head = path.name.split("-", 1)[0]
        if head.isdigit():
            found[int(head)] = path.name
    return found


def _badge(status: str) -> str:
    kind = {
        "Eligible (first time)": "badge-warning",
        "Eligible (again)": "badge-neutral",
        "Unknown": "badge-serious",
    }.get(status, "badge-neutral")
    return f'<span class="badge {kind}">{html.escape(status)}</span>'


def _signed(player: dict) -> str:
    year = player.get("signed_year")
    how = player.get("how")
    if year and how:
        return f"{year}, {html.escape(how)}"
    if year:
        return str(year)
    return "—"


def _row(player: dict, pages: dict[int, str], prefix: str) -> str:
    name = html.escape(player.get("name") or "")
    href = pages.get(player.get("player_id"))
    who = f'<a href="{prefix}p/{href}">{name}</a>' if href else name
    est = ""
    if player.get("estimated"):
        tip = html.escape(" ".join(player.get("estimated_reasons") or []))
        est = f' <span class="rule5-est" title="{tip}">Estimated</span>'
    age = player["age"] if player.get("age") is not None else "—"
    level = html.escape(player.get("level") or "—")
    aff = html.escape(player.get("affiliate") or "")
    pos = html.escape(player.get("position") or "—")
    return (
        f"<tr><td>{who}</td><td>{pos}</td><td class=\"n\">{age}</td>"
        f"<td>{_signed(player)}</td><td title=\"{aff}\">{level}</td>"
        f"<td>{_badge(player.get('status') or '')}{est}</td></tr>"
    )


def _table(players: list[dict], pages: dict[int, str], prefix: str) -> str:
    if not players:
        return "<p>None.</p>"
    body = "".join(_row(p, pages, prefix) for p in players)
    return (
        "<table><thead><tr><th>Player</th><th>Pos</th><th class=\"n\">Age</th>"
        "<th>Signed</th><th>Level</th><th>Status</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def _faq_html(pairs: tuple[tuple[str, str], ...]) -> str:
    blocks = []
    for question, answer in pairs:
        blocks.append(f"<h3>{html.escape(question)}</h3><p>{html.escape(answer)}</p>")
    return "<h2>Questions</h2>" + "".join(blocks)


def _faq_ld(pairs: tuple[tuple[str, str], ...]) -> dict:
    return {
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": question,
                "acceptedAnswer": {"@type": "Answer", "text": answer},
            }
            for question, answer in pairs
        ],
    }


def _shell(title: str, og: str, desc: str, url: str, crumbs: str, heading: str,
           body: str, graph: list[dict], depth: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
{wpp.HEAD_TAGS}
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}" />
<link rel="canonical" href="{html.escape(url)}" />
<meta property="og:type" content="website" />
<meta property="og:title" content="{html.escape(og)}" />
<meta property="og:description" content="{html.escape(desc)}" />
<meta property="og:url" content="{html.escape(url)}" />
{wpp._ld_script(graph)}
<link rel="icon" href="{wpp.BASE_PATH}favicon.svg" type="image/svg+xml" />
<link rel="stylesheet" href="{depth}styles.css" />
<link rel="stylesheet" href="{depth}page.css" />
<link rel="stylesheet" href="{depth}rule5.css" />
<script>
  try {{
    var t = localStorage.getItem("mlb-service-time-theme");
    if (t) document.documentElement.setAttribute("data-theme", t);
  }} catch (e) {{}}
</script>
</head>
<body>
<div class="viz-root">{wpp._site_header(None)}<div class="wrap">
  {crumbs}
  <h1>{html.escape(heading)}</h1>
  <p class="rule5-flag"><strong>Estimated.</strong> {ESTIMATE_BANNER}</p>
  {body}
</div>{wpp._site_footer()}{wpp._search_script_tag()}</div>
{wpp.ANALYTICS}
</body>
</html>
"""


def _club_faq(club: str) -> tuple[tuple[str, str], ...]:
    return (
        (
            f"Which {club} players are Rule 5 eligible in 2026?",
            f"Minor leaguers on a {club} affiliate roster who are off the 40-man "
            "and whose first professional contract makes the December 2026 meeting "
            "one where they can be selected. The list is estimated from public signing data.",
        ),
        (
            f"When does {club} have to protect them?",
            "November 17, 2026. The draft is scheduled for December 9, 2026. "
            "The labor agreement expires December 1, 2026, so that draft date may change.",
        ),
        LEAGUE_FAQ[2],
    )


def render_league(data: dict) -> str:
    rows = []
    for club in data["clubs"]:
        slug = wpp.slug(club["club"])
        counts = club["counts"]
        name = html.escape(club["club"])
        rows.append(
            f'<tr><td><a href="t/{slug}-rule-5.html">{name}</a></td>'
            f'<td class="n">{counts["eligible_first"]}</td>'
            f'<td class="n">{counts["eligible_again"]}</td>'
            f'<td class="n">{counts["unknown"]}</td></tr>'
        )
    totals = data.get("counts") or {}
    body = f"""
  <p>Minor leaguers on affiliate rosters who are off the 40-man and whose first professional contract makes them selectable in the 2026 Rule 5.</p>
  <p>The reserve list is due November 17, 2026, and the draft is scheduled for December 9, 2026.</p>
  <p>The labor agreement expires December 1, 2026, so the December draft may be delayed or dropped.</p>
  <p class="rule5-note">A row marked estimated uses the earliest of more than one signing, or is a winter or spring international signing whose June 5 age date is unsettled. Players who are not yet eligible are left off each club list.</p>
  <table class="rule5-clubs"><thead><tr><th>Club</th><th class="n">First time</th>
  <th class="n">Eligible again</th><th class="n">Unknown</th></tr></thead>
  <tbody>{''.join(rows)}</tbody></table>
  {_faq_html(LEAGUE_FAQ)}
"""
    url = f"{wpp.SITE_URL}/rule-5.html"
    core = "Rule 5 eligible players 2026"
    desc = page_titles.fit_meta(
        f"Rule 5 eligible players in 2026, by club. "
        f"{totals.get('eligible_first', 0)} minor leaguers look eligible for the "
        "first time. Estimated from public signing data, not an official list."
    )
    graph = [
        {"@type": "CollectionPage", "name": core, "url": url, "description": desc},
        _faq_ld(LEAGUE_FAQ),
        wpp._breadcrumb_ld([
            ("Big League Service Time Tracker", f"{wpp.SITE_URL}/"),
            ("Rule 5 eligible 2026", url),
        ]),
    ]
    return _shell(
        page_titles.fit_title(core), page_titles.og_title(core), desc, url,
        wpp._crumbs(("Players", wpp.BASE_PATH), ("Rule 5", None)),
        "Rule 5 eligible players, 2026",
        body, graph, "",
    )


def render_club_page(club: dict, pages: dict[int, str]) -> str:
    name = club["club"]
    slug = wpp.slug(name)
    eligible = sort_players([
        p for p in club["players"]
        if p.get("status") in ("Eligible (first time)", "Eligible (again)")
    ])
    unknown = sort_players([p for p in club["players"] if p.get("status") == "Unknown"])
    counts = club["counts"]
    faq = _club_faq(name)
    body = f"""
  <p>Minor leaguers in the {html.escape(name)} system who are off the 40-man and look selectable in the 2026 Rule 5.</p>
  <p>The reserve list is due November 17, 2026, and the draft is scheduled for December 9, 2026.</p>
  <p>The labor agreement expires December 1, 2026, so the December draft may be delayed or dropped.</p>
  <p class="rule5-note">{counts['eligible_first']} first-time, {counts['eligible_again']} eligible again, {counts['unknown']} unknown. A row marked estimated uses the earliest of more than one signing, or is a winter or spring international signing whose June 5 age date is unsettled.</p>
  <h2>Eligible</h2>
  {_table(eligible, pages, "../")}
  <h2>Unknown</h2>
  <p class="rule5-note">These players are on an affiliate roster and off the 40-man, and the signing record is not enough to classify them.</p>
  {_table(unknown, pages, "../")}
  <p class="actions"><a href="{slug}.html">&larr; {html.escape(name)} roster</a>
  &middot; <a href="{wpp.BASE_PATH}rule-5.html">all clubs</a></p>
  {_faq_html(faq)}
"""
    url = f"{wpp.SITE_URL}/t/{slug}-rule-5.html"
    core = f"Rule 5 eligible {name} 2026"
    desc = page_titles.fit_meta(
        f"Rule 5 eligible {name} in 2026: {counts['eligible_first']} minor leaguers "
        "off the 40-man look eligible for the first time. Estimated from public "
        "signing data, not an official list."
    )
    graph = [
        {"@type": "CollectionPage", "name": core, "url": url, "description": desc},
        _faq_ld(faq),
        wpp._breadcrumb_ld([
            ("Big League Service Time Tracker", f"{wpp.SITE_URL}/"),
            (name, f"{wpp.SITE_URL}/t/{slug}.html"),
            (core, url),
        ]),
    ]
    return _shell(
        page_titles.fit_title(core), page_titles.og_title(core), desc, url,
        wpp._crumbs(
            ("Players", wpp.BASE_PATH),
            (name, f"{slug}.html"),
            ("Rule 5", None),
        ),
        f"Rule 5 eligible {name}, 2026",
        body, graph, "../",
    )


def write_all(data: dict) -> None:
    pages = _page_index()
    (DOCS / "rule-5.html").write_text(render_league(data), encoding="utf-8")
    for club in data["clubs"]:
        rel = DOCS / "t" / f"{wpp.slug(club['club'])}-rule-5.html"
        rel.write_text(render_club_page(club, pages), encoding="utf-8")
    print(f"wrote league page and {len(data['clubs'])} club boards", flush=True)


def main() -> None:
    if not DATA.exists():
        raise SystemExit(f"missing {DATA}. Run fetch_rule5.py first.")
    write_all(json.loads(DATA.read_text()))


if __name__ == "__main__":
    main()
