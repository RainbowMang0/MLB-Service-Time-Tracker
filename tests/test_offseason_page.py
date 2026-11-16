"""The offseason hub: calendar order, real links only, and the sitemap.

    python3 tests/test_offseason_page.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_offseason_page as hub  # noqa: E402
import cba  # noqa: E402
import list_links  # noqa: E402
import page_titles  # noqa: E402
import write_player_pages as wpp  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool) -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {label}")


def test_the_published_page_matches_the_builder() -> None:
    page = hub.render()
    bad = hub.problems(page)
    check("builder checks pass", not bad)
    if bad:
        for item in bad:
            print(f"    {item}")
    disk = (ROOT / "docs" / hub.PATH).read_text(encoding="utf-8")
    check("docs page matches the builder", disk == page)
    core = (ROOT / "docs" / "sitemap-core.xml").read_text(encoding="utf-8")
    check("sitemap lists the hub", f"/{hub.PATH}</loc>" in core)
    check(
        "the hub is a hand-listed sitemap page",
        hub.PATH in [path for path, _prio in wpp.HAND_WRITTEN_PAGES],
    )
    for name in ("update-service-time.yml", "backfill-history.yml"):
        body = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        check(f"{name} commits the hub", f"docs/{hub.PATH}" in body)
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    check("ci runs the hub check", "scripts/build_offseason_page.py --check" in ci)
    check("ci names this suite", "tests/test_offseason_page.py" in ci)

    title = page_titles.offseason_title()
    _iso, agreement = hub._agreement_end(cba.default())
    description = page_titles.offseason_meta(agreement)
    check("title stays in a snippet", len(title) <= page_titles.TITLE_MAX)
    check("description stays in a snippet", len(description) <= page_titles.META_MAX)
    for phrase in (
        "2026",
        "offseason",
        "Rule 5",
        "November 17",
        "non-tender",
        "free agents",
        "contract years",
        "CBA",
        "public data",
    ):
        check(f"description names {phrase}", phrase.lower() in description.lower() or phrase in description)
    check("description names the agreement end", agreement in description)

    taxes = (ROOT / "docs" / "taxes.html").read_bytes()
    pinned = hashlib.sha256(taxes).hexdigest()
    check(
        "taxes.html is unchanged",
        pinned == "ef78b2f8160046145ce58382a0da8a55dc5ab3b98130fab271db847e1a45fe62",
    )


def test_a_missing_page_is_left_out() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        docs = pathlib.Path(tmp)
        (docs / "rule-5.html").write_text("x", encoding="utf-8")
        (docs / "free-agents.html").write_text("x", encoding="utf-8")
        rels = [item[0] for group in hub.calendar_groups(docs) for item in group["items"]]
    check("a page on disk is linked", "rule-5.html" in rels and "free-agents.html" in rels)
    check("a page that is not on disk is left out", "lockout.html" not in rels)
    check("contract years stay out when contract.html is absent", "contract.html" not in rels)


def test_the_agreement_end_follows_the_ruleset() -> None:
    config_dir = ROOT / "config" / "cba"
    base = json.loads((config_dir / "2022.json").read_text(encoding="utf-8"))
    base["effective_end"] = "2028-01-15T23:59:00-05:00"
    scratch = config_dir / "9998.json"
    scratch.write_text(json.dumps(base), encoding="utf-8")
    saved = cba.DEFAULT_VERSION
    try:
        cba.DEFAULT_VERSION = "9998"
        cba.load.cache_clear()
        moved = hub.render()
    finally:
        scratch.unlink(missing_ok=True)
        cba.DEFAULT_VERSION = saved
        cba.load.cache_clear()
    check("a changed agreement end reaches the hub", "January 15, 2028" in moved)
    check("the old agreement end does not linger", "December 1, 2026" not in moved)
    check("the rule 5 date is unchanged", "November 17, 2026" in moved)


def test_related_pages_point_at_the_hub() -> None:
    docs = ROOT / "docs"
    link = 'href="/offseason.html"'
    home = (docs / "index.html").read_text(encoding="utf-8")
    check("the homepage links the hub", "offseason.html" in home)
    for name in (
        "arbitration-eligible.html",
        "super-two.html",
        "non-tender-watch.html",
        "free-agents.html",
        "options.html",
    ):
        text = (docs / name).read_text(encoding="utf-8")
        expected = list_links.related_league_line(name, "/")
        check(f"{name} points at the hub with the other lists", expected in text and link in expected)
    for name in ("send-a-player-down.html", "rule-5.html"):
        check(f"{name} points at the hub", link in (docs / name).read_text(encoding="utf-8"))


def test_the_calendar_is_only_the_offseason_pages() -> None:
    page = hub.render()
    calendar = page.split("<h2>Calendar</h2>", 1)[1].split("<h2>By club</h2>", 1)[0]
    check("the calendar does not add the Super Two list", "super-two.html" not in calendar)
    check("the calendar does not add the options list", 'href="/options.html"' not in calendar)
    clubs = page.split("<h2>By club</h2>", 1)[1]
    check("club rows link arbitration pages", "-arbitration.html" in clubs)
    check("club rows link free-agent pages", "-free-agents.html" in clubs)
    check("club rows link non-tender pages", "-non-tender.html" in clubs)
    check("club rows do not repeat the Rule 5 boards", "-rule-5.html" not in clubs)


def main() -> None:
    test_the_published_page_matches_the_builder()
    test_a_missing_page_is_left_out()
    test_the_agreement_end_follows_the_ruleset()
    test_related_pages_point_at_the_hub()
    test_the_calendar_is_only_the_offseason_pages()
    print(f"{PASS} passed, {FAIL} failed")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
