"""
Tests for scripts/build_lockout_page.py and docs/lockout.html. No pytest
dependency required -- run directly with `python3 tests/test_lockout.py`.

Covers: data/lockout_updates.json validation (the real file, plus bad
fixtures built in-process -- nothing written to disk), the countdown and
lockout-day-count math, entries rendering newest-first and HTML-escaped,
and a handful of whole-page checks (GA/AdSense/footer/contact exactly once,
the JSON-LD parses, the nav reaches lockout.html, the sitemap lists it).
"""

import copy
import datetime as dt
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_lockout_page as blp  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, condition: bool):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


GOOD = {
    "cba_expires": "2026-12-01",
    "lockout_start": None,
    "lockout_end": None,
    "regular_season_delayed_from": None,
    "updated": "2026-10-31",
    "entries": [
        {
            "date": "2026-10-29",
            "headline": "Headline one",
            "summary": "Summary one.",
            "source_name": "Some Outlet",
            "source_url": "https://example.com/one",
        },
        {
            "date": "2026-10-15",
            "headline": "Headline two",
            "summary": "Summary two.",
            "source_name": "Another Outlet",
            "source_url": "https://example.com/two",
        },
    ],
}


def test_the_real_data_file_validates():
    data = blp.load()
    check("data/lockout_updates.json loads and validates", isinstance(data, dict))
    check("real file has entries", len(data["entries"]) >= 1)


def test_a_well_formed_fixture_validates():
    try:
        blp.validate(copy.deepcopy(GOOD))
        ok = True
    except blp.LockoutDataError:
        ok = False
    check("a well-formed file raises nothing", ok)


def test_missing_top_level_field_is_rejected():
    bad = copy.deepcopy(GOOD)
    del bad["cba_expires"]
    raised = False
    try:
        blp.validate(bad)
    except blp.LockoutDataError as exc:
        raised = "cba_expires" in str(exc)
    check("missing cba_expires is rejected and named in the error", raised)


def test_bad_iso_date_is_rejected():
    bad = copy.deepcopy(GOOD)
    bad["cba_expires"] = "12/01/2026"
    raised = False
    try:
        blp.validate(bad)
    except blp.LockoutDataError:
        raised = True
    check("a non-ISO cba_expires is rejected", raised)

    bad2 = copy.deepcopy(GOOD)
    bad2["entries"][0]["date"] = "not-a-date"
    raised2 = False
    try:
        blp.validate(bad2)
    except blp.LockoutDataError:
        raised2 = True
    check("a non-ISO entry date is rejected", raised2)


def test_missing_source_is_rejected():
    bad = copy.deepcopy(GOOD)
    del bad["entries"][0]["source_name"]
    raised = False
    try:
        blp.validate(bad)
    except blp.LockoutDataError as exc:
        raised = "source_name" in str(exc)
    check("an entry missing source_name is rejected", raised)


def test_non_https_source_url_is_rejected():
    bad = copy.deepcopy(GOOD)
    bad["entries"][0]["source_url"] = "http://example.com/one"
    raised = False
    try:
        blp.validate(bad)
    except blp.LockoutDataError as exc:
        raised = "source_url" in str(exc)
    check("a non-https source_url is rejected", raised)


def test_countdown_before_a_lockout():
    data = copy.deepcopy(GOOD)
    status = blp.compute_status(data, dt.date(2026, 11, 1))
    check("Nov 1 2026 is 30 days until the Dec 1 2026 expiry", status["day"] == 30)
    check("status kind is countdown", status["kind"] == "countdown")
    check(
        "the headline reads '30 days until'",
        blp.status_headline(status) == "30 days until the CBA expires (Dec. 1, 2026)",
    )


def test_day_count_after_a_lockout_starts():
    data = copy.deepcopy(GOOD)
    data["lockout_start"] = "2026-12-02"
    status = blp.compute_status(data, dt.date(2026, 12, 10))
    check("day 1 is the start date itself (inclusive)", status["day"] == 9)
    check("status kind is lockout", status["kind"] == "lockout")
    check("the headline reads 'Day 9 of the lockout'", blp.status_headline(status) == "Day 9 of the lockout")

    # The start date itself is day 1, not day 0.
    status_day1 = blp.compute_status(data, dt.date(2026, 12, 2))
    check("the start date itself is day 1", status_day1["day"] == 1)


def test_service_time_days_at_stake():
    data = copy.deepcopy(GOOD)
    data["lockout_start"] = "2026-12-02"
    data["regular_season_delayed_from"] = "2027-03-26"
    status = blp.compute_status(data, dt.date(2027, 4, 3))
    check("9 days missed, inclusive of the original opener", status["days_at_stake"] == 9)
    check(
        "the headline appends the days-at-stake figure",
        "9 service-time days at stake" in blp.status_headline(status),
    )


def test_entries_render_newest_first():
    ordered = blp.sorted_entries(GOOD)
    check("newest entry (10-29) sorts first", ordered[0]["date"] == "2026-10-29")
    check("older entry (10-15) sorts second", ordered[1]["date"] == "2026-10-15")

    # Reverse the input order and confirm the output order is unaffected.
    reversed_input = copy.deepcopy(GOOD)
    reversed_input["entries"] = list(reversed(reversed_input["entries"]))
    ordered2 = blp.sorted_entries(reversed_input)
    check("sort order does not depend on input order", ordered2[0]["date"] == "2026-10-29")


def test_entries_are_html_escaped():
    data = copy.deepcopy(GOOD)
    data["entries"][0]["headline"] = "<script>alert(1)</script> & friends"
    rendered = blp._render_entries(blp.sorted_entries(data))
    check("a raw <script> tag is not injected verbatim", "<script>alert(1)</script>" not in rendered)
    check("the escaped form is present instead", "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered)
    check("a bare & is escaped too", "&amp; friends" in rendered)


def test_last_updated_is_the_newer_of_updated_and_newest_entry():
    data = copy.deepcopy(GOOD)
    data["updated"] = "2026-09-01"  # older than the newest entry (2026-10-29)
    check(
        "newest entry wins when it's newer than `updated`",
        blp.last_updated(data) == dt.date(2026, 10, 29),
    )
    data["updated"] = "2026-12-01"  # newer than any entry
    check(
        "`updated` wins when it's the newer of the two",
        blp.last_updated(data) == dt.date(2026, 12, 1),
    )


# ---------------------------------------------------------------------------
# Whole-page checks against the real, built docs/lockout.html.
# ---------------------------------------------------------------------------

LOCKOUT_PAGE = ROOT / "docs" / "lockout.html"


def test_the_page_has_been_built():
    check(f"{LOCKOUT_PAGE} exists", LOCKOUT_PAGE.exists())


def test_the_page_carries_exactly_one_ga_tag_and_one_adsense_block():
    html_text = LOCKOUT_PAGE.read_text(encoding="utf-8")
    ga_script = "googletagmanager.com/gtag/js?id=G-G04P969TLX"
    ga_config = "gtag('config', 'G-G04P969TLX')"
    adsense_script = "pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-8748967682918425"
    adsense_meta = 'google-adsense-account" content="ca-pub-8748967682918425'
    check("exactly one GA script tag", html_text.count(ga_script) == 1)
    check("exactly one GA config call", html_text.count(ga_config) == 1)
    check("exactly one AdSense script tag", html_text.count(adsense_script) == 1)
    check("exactly one AdSense meta tag", html_text.count(adsense_meta) == 1)


def test_the_page_carries_the_footer_disclaimer_and_contact():
    html_text = LOCKOUT_PAGE.read_text(encoding="utf-8")
    normalized = " ".join(html_text.split())
    required = (
        "Not affiliated with or endorsed by Major League Baseball or any "
        "team. Service time figures are our own calculations from public "
        "data."
    )
    check("carries the required disclaimer sentence", required in normalized)
    check("links to About", "about.html" in html_text)
    check("links to Privacy", "privacy.html" in html_text)
    check("shows the contact address", "contact@bigleagueservicetime.com" in html_text)


def test_the_json_ld_parses_and_has_article_and_faqpage():
    html_text = LOCKOUT_PAGE.read_text(encoding="utf-8")
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html_text, re.S)
    check("a JSON-LD script block is present", m is not None)
    data = json.loads(m.group(1))
    types = {node.get("@type") for node in data.get("@graph", [])}
    check("JSON-LD includes an Article node", "Article" in types)
    check("JSON-LD includes a FAQPage node", "FAQPage" in types)

    faq_node = next(n for n in data["@graph"] if n.get("@type") == "FAQPage")
    visible_questions = re.findall(r"<h3>(.*?)</h3>", html_text)
    import html as html_mod
    ld_questions = [q["name"] for q in faq_node["mainEntity"]]
    check(
        "every JSON-LD question matches a visible <h3> FAQ question",
        ld_questions == [html_mod.unescape(q) for q in visible_questions],
    )


def test_nav_reaches_lockout_on_hand_written_and_generated_pages():
    import write_player_pages as wpp

    for rel in ("index.html", "about.html", "privacy.html", "neutrality.html", "contract.html", "lockout.html"):
        html_text = (ROOT / "docs" / rel).read_text(encoding="utf-8")
        check(f"docs/{rel} nav links to lockout.html", 'href="lockout.html"' in html_text)

    generated = wpp.render_super_two([], {}, "2026-01-01", None)
    check("a generated page's nav links to /lockout.html", "/lockout.html" in generated)


def test_sitemap_lists_lockout_page():
    sitemap = (ROOT / "docs" / "sitemap-core.xml").read_text(encoding="utf-8")
    check("sitemap-core.xml lists /lockout.html", "/lockout.html</loc>" in sitemap)


if __name__ == "__main__":
    test_the_real_data_file_validates()
    test_a_well_formed_fixture_validates()
    test_missing_top_level_field_is_rejected()
    test_bad_iso_date_is_rejected()
    test_missing_source_is_rejected()
    test_non_https_source_url_is_rejected()
    test_countdown_before_a_lockout()
    test_day_count_after_a_lockout_starts()
    test_service_time_days_at_stake()
    test_entries_render_newest_first()
    test_entries_are_html_escaped()
    test_last_updated_is_the_newer_of_updated_and_newest_entry()
    test_the_page_has_been_built()
    test_the_page_carries_exactly_one_ga_tag_and_one_adsense_block()
    test_the_page_carries_the_footer_disclaimer_and_contact()
    test_the_json_ld_parses_and_has_article_and_faqpage()
    test_nav_reaches_lockout_on_hand_written_and_generated_pages()
    test_sitemap_lists_lockout_page()

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
