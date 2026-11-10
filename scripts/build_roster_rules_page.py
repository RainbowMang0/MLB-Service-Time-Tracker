#!/usr/bin/env python3
"""
build_roster_rules_page.py
--------------------------
Writes the evergreen roster-rules guide at docs/send-a-player-down.html.

The page is original explanation. Numbers that already live in config/cba/
are read from there. Calendar dates for the 2026 offseason are stated as
dates, with no clock time. Nothing here computes service time.

  python3 scripts/build_roster_rules_page.py
  python3 scripts/build_roster_rules_page.py --check
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cba  # noqa: E402
import write_player_pages as wpp  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT_FILE = ROOT / "docs" / wpp.SEND_DOWN_PATH
PAGE_URL = f"{wpp.SITE_URL}/{wpp.SEND_DOWN_PATH}"

# Offseason calendar for this guide. Dates only: do not attach a clock.
RESERVE_LIST_DATE = "November 17, 2026"
NON_TENDER_DATE = "November 20, 2026"
RULE_5_DRAFT_DATE = "December 9, 2026"

TITLE = (
    "Can a team send a player down? Options, DFA, and outright assignment "
    "| Big League Service Time Tracker"
)
OG_TITLE = "Can a team send a player down? Options, DFA, and outright assignment"
DESCRIPTION = (
    "Can a team send a player down? What out of options means, what DFA means, "
    "and how an outright assignment works, plus the 2026 roster deadlines."
)

GLOSSARY = (
    (
        "Designate for assignment",
        "https://www.mlb.com/glossary/transactions/designate-for-assignment",
    ),
    (
        "Outright waivers",
        "https://www.mlb.com/glossary/transactions/outright-waivers",
    ),
    (
        "40-man roster",
        "https://www.mlb.com/glossary/transactions/40-man-roster",
    ),
    (
        "Minor league options",
        "https://www.mlb.com/glossary/transactions/minor-league-options",
    ),
)

_MONTHS = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _agreement_end(rules: cba.Ruleset) -> str:
    """Calendar date only. The ruleset stores a clock; this page does not."""
    raw = rules.effective_end or ""
    day = dt.date.fromisoformat(raw[:10])
    return f"{_MONTHS[day.month]} {day.day}, {day.year}"


def _facts() -> dict:
    rules = cba.default()
    return {
        "option_years": int(rules.require("options.standard_option_years")),
        "option_days": int(rules.require("options.days_to_burn_option_year")),
        "dfa_days": int(rules.require("designated_for_assignment.max_service_days")),
        "active": int(rules.require("roster.active_roster")),
        "september": int(rules.require("roster.active_roster_september")),
        "reserve": int(rules.require("roster.reserve_list")),
        "young": int(rules.require("rule_5.protected_seasons_if_signed_at_18_or_younger")),
        "older": int(rules.require("rule_5.protected_seasons_if_signed_at_19_or_older")),
        "agreement": html.escape(rules.label),
        "agreement_end": _agreement_end(rules),
    }


def _faq(f: dict) -> list[tuple[str, str]]:
    """Questions and answers. The visible FAQ and the JSON-LD share this list."""
    years = f["option_years"]
    days = f["option_days"]
    window = f["dfa_days"]
    active = f["active"]
    reserve = f["reserve"]
    return [
        (
            "Can a team send a player down?",
            (
                "Yes, when he is on the 40-man roster and still has an option "
                f"year. Most players get {years} option years. The club can "
                "send him to the minors and keep him on the 40-man. Once those "
                "years are used, he is out of options, and the club has to "
                "designate him and pass him through waivers first."
            ),
        ),
        (
            "What does out of options mean?",
            (
                "It means his option years are used up. The club cannot send "
                "him to the minors and leave him on the 40-man roster. A short "
                f"stay of fewer than {days} days in one season does not use an "
                "option year."
            ),
        ),
        (
            "What does DFA mean?",
            (
                "DFA means designated for assignment. The player comes off the "
                f"40-man roster immediately. The club has {window} days to "
                "trade him, put him on waivers, or release him. He is still "
                "under contract during that window."
            ),
        ),
        (
            "Is a DFA the same as a release?",
            (
                "No. A release ends the club's contract and the player can "
                "sign anywhere. A designation only starts the "
                f"{window}-day window. The club might still trade him or send "
                "him to the minors."
            ),
        ),
        (
            "What is an outright assignment?",
            (
                "It takes a player off the 40-man roster and assigns him to "
                "the minors after he clears waivers. A player with more than "
                "three years of major league service, or who has been "
                "outrighted before, can turn it down and become a free agent. "
                "If he accepts, the contract continues and he plays in the "
                "minors."
            ),
        ),
        (
            "What is the difference between the 40-man roster and the active roster?",
            (
                f"The active roster is the {active} players who can play in a "
                f"game. The {reserve}-man roster is the reserve list. It "
                "includes the active roster, players on the injured list, and "
                "some players in the minors."
            ),
        ),
        (
            "When is the 2026 non-tender deadline?",
            (
                f"{NON_TENDER_DATE}. A club that does not offer next season's "
                "contract to a player on the 40-man who is not yet a free "
                "agent makes him a free agent. That is a different move from "
                "an option, a designation, or an outright."
            ),
        ),
    ]


def _faq_html(faq: list[tuple[str, str]]) -> str:
    blocks = []
    for question, answer in faq:
        blocks.append(f"  <h3>{question}</h3>\n  <p>{answer}</p>")
    return "\n".join(blocks)


def _faq_ld(faq: list[tuple[str, str]]) -> dict:
    return {
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": question,
                "acceptedAnswer": {"@type": "Answer", "text": answer},
            }
            for question, answer in faq
        ],
    }


def _href(path: str) -> str:
    return f"{wpp.BASE_PATH}{path}"


def render() -> str:
    f = _facts()
    faq = _faq(f)
    years = f["option_years"]
    days = f["option_days"]
    window = f["dfa_days"]
    active = f["active"]
    september = f["september"]
    reserve = f["reserve"]
    young = f["young"]
    older = f["older"]
    graph = [
        {
            "@type": "WebPage",
            "name": "Can a team send a player down?",
            "url": PAGE_URL,
            "description": DESCRIPTION,
        },
        _faq_ld(faq),
        wpp._breadcrumb_ld([
            ("Big League Service Time Tracker", f"{wpp.SITE_URL}/"),
            ("Can a team send a player down?", PAGE_URL),
        ]),
    ]
    sources = "\n".join(
        f'    <li><a href="{url}">MLB glossary — {html.escape(label)}</a></li>'
        for label, url in GLOSSARY
    )
    options = _href(wpp.OPTIONS_PATH)
    service = _href(wpp.EXPLAINER_PATH)
    arb = _href(wpp.ARBITRATION_ELIGIBLE_PATH)
    nontender = _href(wpp.NON_TENDER_WATCH_PATH)
    players = wpp.BASE_PATH or "/"
    lockout = _href("lockout.html")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
{wpp.HEAD_TAGS}
<title>{html.escape(TITLE)}</title>
<meta name="description" content="{html.escape(DESCRIPTION)}" />
<link rel="canonical" href="{PAGE_URL}" />
<meta property="og:type" content="article" />
<meta property="og:title" content="{html.escape(OG_TITLE)}" />
<meta property="og:description" content="{html.escape(DESCRIPTION)}" />
<meta property="og:url" content="{PAGE_URL}" />
<meta name="twitter:card" content="summary" />
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
  {wpp._crumbs(( "Players", players), ("Can a team send a player down?", None))}

  <h1>Can a team send a player down?</h1>
  <p class="lede">An option, a designation for assignment, and an outright
  assignment are three different ways a club takes a player off the active
  roster.</p>
  <p class="what-is"><a href="#optional-assignment">Option</a>
  &middot; <a href="#dfa">DFA</a>
  &middot; <a href="#outright">Outright</a></p>

  <h2 id="optional-assignment">Optional assignment</h2>
  <p><b>Yes, if he still has an option year.</b> The club can send him to the
  minors and keep him on the 40-man roster. He leaves the active roster. He
  does not leave the organization.</p>
  <p>Most players get <b>{years} option years</b>. One year is used when he
  spends <b>{days} or more days</b> in the minors on an optional assignment
  in that season. Several trips in the same season still count as one year.
  A stay shorter than {days} days does not use the year, and those days still
  count as major league service. <a href="{service}">How a day of service is
  counted</a> is a separate question.</p>
  <p>A player who has used those years is <a href="{options}">out of
  options</a>. The club cannot send him down and keep the 40-man spot. A
  player with more than five years of major league service has to agree
  before the club can option him. Some players qualify for one more option
  year. This site does not guess which ones. <a href="{players}">Look up a
  player</a> for the count this site does publish.</p>
  <p class="milestone"><b>Example.</b> A catcher on the 40-man has one option
  year left. The club can send him to Triple-A in the spring and call him
  back. If he is down for {days} days or more, that uses the last option
  year. If he is back sooner, it does not.</p>

  <h2 id="dfa">Designation for assignment</h2>
  <p><b>A DFA takes him off the 40-man roster. It is not a release.</b> He is
  still under contract while the club decides the next move.</p>
  <p>The club has <b>{window} days</b> to trade him, to put him on waivers, or
  to release him. If another club claims him, he joins that club's 40-man
  roster. If he clears waivers for an outright assignment, he can be sent to
  the minors and he comes off the 40-man. A release is its own move: the
  contract ends and he can sign anywhere. Designating him does not do that
  by itself.</p>
  <p class="milestone"><b>Example.</b> A reliever who is out of options is
  designated on a Monday. He is off the 40-man that day, and he is still
  that club's player. Within {window} days the club trades him, tries to
  send him to the minors through waivers, or releases him.</p>

  <h2 id="outright">Outright assignment</h2>
  <p><b>An outright sends him to the minors and removes him from the
  40-man</b>, after he clears waivers. It is the path a club uses when he is
  out of options, or when a designation is not resolved by a trade or a
  claim.</p>
  <p>A player with <b>more than three years</b> of major league service, or
  who has been outrighted before, can turn the assignment down and become a
  free agent. A player with <b>more than five years</b> of service can refuse
  to be sent to the minors at all. <a href="{service}">What a year of service
  is</a> lives on the service-time page. <a href="{arb}">Who is
  arbitration-eligible</a> is a related list, not the same test.</p>
  <p>If he accepts, or if he cannot refuse, the contract continues. He plays
  in the minors and he is no longer on the 40-man. If he turns it down and
  becomes a free agent, that club no longer controls the contract.</p>
  <p class="milestone"><b>Example.</b> A first baseman with four years of
  service clears waivers. He can turn down the outright and become a free
  agent. A teammate with one year of service who has never been outrighted
  has to accept. He stays under contract, in the minors, off the 40-man.</p>

  <h2 id="rosters">The 40-man roster and the active roster</h2>
  <p>The active roster is who can play in a game. During the season it holds
  <b>{active} players</b>, and <b>{september} in September</b>. The 40-man
  roster is the reserve list. It holds <b>{reserve} players</b>: the active
  roster, players on the injured list, and some players in the minors.</p>
  <p>A player has to be on the 40-man before he can be on the active roster.
  An option takes him off the active roster and leaves him on the 40-man. A
  designation or an outright takes him off the 40-man. The injured list is
  not a trip to the minors. He is still on the 40-man.</p>

  <h2 id="out-of-options">Why being out of options matters</h2>
  <p>While option years remain, a club can move a player between the majors
  and the minors and keep the roster spot. Once he is out of options, that
  door is shut. Sending him to the minors means taking him off the 40-man,
  which means a designation and waivers. Another club can claim him. That is
  why an out-of-options player is harder to move down, in spring training
  and again around roster deadlines.</p>
  <p>The <a href="{options}">out-of-options list</a> is this site's own count
  from public data. It is not the league's official record.</p>

  <h2 id="rule-5">Rule 5 protection</h2>
  <p><b>{RESERVE_LIST_DATE}</b> is the day clubs set the reserve list. A
  player on the 40-man roster that day is protected from the Rule 5 draft.
  The draft itself is a later event, scheduled for {RULE_5_DRAFT_DATE}.</p>
  <p>In general, a player who is not already on the 40-man has to be added by
  that day if another club would be allowed to select him. A player signed
  at 18 or younger is generally eligible after <b>{young} seasons</b>. A
  player signed at 19 or older is generally eligible after <b>{older}
  seasons</b>. Clubs protect the ones they do not want selected by placing
  them on the reserve list. This page does not decide who is eligible.</p>

  <h2 id="non-tender">The non-tender deadline</h2>
  <p><b>{NON_TENDER_DATE}</b> is the non-tender deadline. A club that does not
  offer next season's contract to a player on the 40-man who is not yet a
  free agent makes him a free agent. That is not an option, a designation,
  or an outright. It is a decision about next year's contract.</p>
  <p>See <a href="{nontender}">this winter's non-tender watch</a> and
  <a href="{arb}">who is arbitration-eligible</a>.</p>

  <h2 id="faq">Common questions</h2>
  <div class="faq">
{_faq_html(faq)}
  </div>

  <h2>Where these rules are written down</h2>
  <ul class="sources">
{sources}
  </ul>
  <p class="foot-note">These are the roster rules in the {f["agreement"]},
  which runs through {f["agreement_end"]}. A later agreement can change them.
  <a href="{lockout}">Labor talks</a> are tracked separately. This is a
  plain-English guide, not a ruling on any one contract. The examples above
  are hypothetical.</p>
</div>{wpp._site_footer()}{wpp._search_script_tag()}</div>
{wpp.ANALYTICS}
</body>
</html>
"""


def problems(page: str) -> list[str]:
    """Editorial checks. Empty means the page is safe to write."""
    found: list[str] = []

    def need(label: str, ok: bool) -> None:
        if not ok:
            found.append(label)

    need("title", TITLE in page)
    need("description", DESCRIPTION in page)
    need("canonical", f'rel="canonical" href="{PAGE_URL}"' in page)
    need("one h1", page.count("<h1>") == 1)
    need("option section", 'id="optional-assignment"' in page)
    need("dfa section", 'id="dfa"' in page)
    need("outright section", 'id="outright"' in page)
    need("roster section", 'id="rosters"' in page)
    need("rule 5 section", 'id="rule-5"' in page)
    need("non-tender section", 'id="non-tender"' in page)
    need("reserve-list date", RESERVE_LIST_DATE in page)
    need("non-tender date", NON_TENDER_DATE in page)
    need("draft date", RULE_5_DRAFT_DATE in page)
    need("three examples", page.count("<b>Example.</b>") == 3)
    need("FAQPage", '"@type": "FAQPage"' in page)
    need("adsense", "ca-pub-8748967682918425" in page)
    for label, url in GLOSSARY:
        need(f"glossary {label}", url in page)
    for path in (
        wpp.OPTIONS_PATH,
        wpp.EXPLAINER_PATH,
        wpp.ARBITRATION_ELIGIBLE_PATH,
        wpp.NON_TENDER_WATCH_PATH,
        "lockout.html",
    ):
        need(f"link {path}", f'href="{wpp.BASE_PATH}{path}"' in page)
    faq = _faq(_facts())
    for question, answer in faq:
        need(f"visible question {question}", f"<h3>{question}</h3>" in page)
        need(f"visible answer {question}", f"<p>{answer}</p>" in page)
        need(f"json question {question}", question in page)
    lowered = page.lower()
    for banned in (
        "last updated",
        "baseball-reference",
        "baseball reference",
        "fangraphs",
        "scripts/",
        ".py",
        "pipeline",
        "stats api",
        "github.com",
        "p.m.",
        "a.m.",
        "23:59",
        "5:00",
    ):
        need(f"no {banned!r}", banned not in lowered)
    need("no should", "should" not in lowered)
    return found


def write(path: Path = OUT_FILE) -> str:
    page = render()
    bad = problems(page)
    if bad:
        raise SystemExit("roster guide failed checks: " + "; ".join(bad))
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
            print("roster guide check failed:", file=sys.stderr)
            for item in bad:
                print(f"  - {item}", file=sys.stderr)
            return 1
        print(f"{args.out.name}: ok")
        return 0
    if bad:
        print("roster guide failed checks:", file=sys.stderr)
        for item in bad:
            print(f"  - {item}", file=sys.stderr)
        return 1
    args.out.write_text(page, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
