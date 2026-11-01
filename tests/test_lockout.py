"""
Tests for scripts/build_lockout_page.py and docs/lockout.html. No pytest
dependency required -- run directly with `python3 tests/test_lockout.py`.

Covers: data/lockout_updates.json validation (the real file, plus bad
fixtures built in-process -- nothing written to disk), the countdown and
lockout-day-count math (including the ET-anchored expiry-instant boundary
and the "expires tonight" state), entries rendering newest-first and
HTML-escaped, and a handful of whole-page checks (GA/AdSense/footer/contact
exactly once, the JSON-LD parses, the nav reaches lockout.html, the sitemap
generator lists it).
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


def et(year, month, day, hour=12, minute=0) -> dt.datetime:
    """A timezone-aware America/New_York instant, for compute_status()."""
    return dt.datetime(year, month, day, hour, minute, tzinfo=blp.ET)


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

PINNED_TODAY = dt.date(2026, 10, 31)  # matches GOOD's newest entry/updated


def test_the_real_data_file_validates():
    data = blp.load()
    check("data/lockout_updates.json loads and validates", isinstance(data, dict))
    check("real file has entries", len(data["entries"]) >= 1)


def test_a_well_formed_fixture_validates():
    try:
        blp.validate(copy.deepcopy(GOOD), today=PINNED_TODAY)
        ok = True
    except blp.LockoutDataError:
        ok = False
    check("a well-formed file raises nothing", ok)


def test_missing_top_level_field_is_rejected():
    bad = copy.deepcopy(GOOD)
    del bad["cba_expires"]
    raised = False
    try:
        blp.validate(bad, today=PINNED_TODAY)
    except blp.LockoutDataError as exc:
        raised = "cba_expires" in str(exc)
    check("missing cba_expires is rejected and named in the error", raised)


def test_bad_iso_date_is_rejected():
    bad = copy.deepcopy(GOOD)
    bad["cba_expires"] = "12/01/2026"
    raised = False
    try:
        blp.validate(bad, today=PINNED_TODAY)
    except blp.LockoutDataError:
        raised = True
    check("a non-ISO cba_expires is rejected", raised)

    bad2 = copy.deepcopy(GOOD)
    bad2["entries"][0]["date"] = "not-a-date"
    raised2 = False
    try:
        blp.validate(bad2, today=PINNED_TODAY)
    except blp.LockoutDataError:
        raised2 = True
    check("a non-ISO entry date is rejected", raised2)


def test_compact_iso_date_is_rejected():
    # fromisoformat() alone accepts "20261201"; the regex gate must catch it.
    bad = copy.deepcopy(GOOD)
    bad["cba_expires"] = "20261201"
    raised = False
    try:
        blp.validate(bad, today=PINNED_TODAY)
    except blp.LockoutDataError:
        raised = True
    check("a compact-ISO (no dashes) cba_expires is rejected", raised)


def test_week_date_is_rejected():
    bad = copy.deepcopy(GOOD)
    bad["entries"][0]["date"] = "2026-W44-4"
    raised = False
    try:
        blp.validate(bad, today=PINNED_TODAY)
    except blp.LockoutDataError:
        raised = True
    check("an ISO week-date entry date is rejected", raised)


def test_missing_source_is_rejected():
    bad = copy.deepcopy(GOOD)
    del bad["entries"][0]["source_name"]
    raised = False
    try:
        blp.validate(bad, today=PINNED_TODAY)
    except blp.LockoutDataError as exc:
        raised = "source_name" in str(exc)
    check("an entry missing source_name is rejected", raised)


def test_non_https_source_url_is_rejected():
    bad = copy.deepcopy(GOOD)
    bad["entries"][0]["source_url"] = "http://example.com/one"
    raised = False
    try:
        blp.validate(bad, today=PINNED_TODAY)
    except blp.LockoutDataError as exc:
        raised = "source_url" in str(exc)
    check("a non-https source_url is rejected", raised)


def test_bare_https_url_is_rejected():
    bad = copy.deepcopy(GOOD)
    bad["entries"][0]["source_url"] = "https://"
    raised = False
    try:
        blp.validate(bad, today=PINNED_TODAY)
    except blp.LockoutDataError:
        raised = True
    check("'https://' with no host is rejected", raised)


def test_url_with_embedded_whitespace_is_rejected():
    bad = copy.deepcopy(GOOD)
    bad["entries"][0]["source_url"] = "https:// javascript:alert(1)"
    raised = False
    try:
        blp.validate(bad, today=PINNED_TODAY)
    except blp.LockoutDataError:
        raised = True
    check("a source_url with embedded whitespace is rejected", raised)


def test_future_dated_entry_is_rejected():
    bad = copy.deepcopy(GOOD)
    bad["entries"][0]["date"] = "2099-01-01"
    raised = False
    try:
        blp.validate(bad, today=PINNED_TODAY)
    except blp.LockoutDataError:
        raised = True
    check("an entry dated far in the future is rejected", raised)


def test_lockout_end_before_lockout_start_is_rejected():
    bad = copy.deepcopy(GOOD)
    bad["lockout_start"] = "2026-12-10"
    bad["lockout_end"] = "2026-12-01"
    raised = False
    try:
        blp.validate(bad, today=PINNED_TODAY)
    except blp.LockoutDataError as exc:
        raised = "lockout_end" in str(exc)
    check("lockout_end before lockout_start is rejected", raised)


def test_countdown_before_a_lockout():
    data = copy.deepcopy(GOOD)
    status = blp.compute_status(data, et(2026, 11, 1))
    check("Nov 1 2026 is 30 days until the Dec 1 2026 expiry", status["day"] == 30)
    check("status kind is countdown", status["kind"] == "countdown")
    check(
        "the sentence reads '30 days until'",
        blp.render_sentence(status) == "30 days until the CBA expires (Dec. 1, 2026)",
    )


def test_day_count_after_a_lockout_starts():
    data = copy.deepcopy(GOOD)
    data["lockout_start"] = "2026-12-02"
    status = blp.compute_status(data, et(2026, 12, 10))
    check("day 9 on Dec 10 (start date inclusive)", status["day"] == 9)
    check("status kind is lockout", status["kind"] == "lockout")
    check("the sentence reads 'Day 9 of the lockout'", blp.render_sentence(status) == "Day 9 of the lockout")

    # The start date itself is day 1, not day 0.
    status_day1 = blp.compute_status(data, et(2026, 12, 2))
    check("the start date itself is day 1", status_day1["day"] == 1)


def test_a_future_lockout_start_is_still_the_countdown_state():
    # A lockout_start set ahead of time must not clamp to "Day 1" early --
    # see QA finding #6.
    data = copy.deepcopy(GOOD)
    data["lockout_start"] = "2026-12-05"
    status = blp.compute_status(data, et(2026, 11, 30))
    check("a future lockout_start does not read as 'lockout' yet", status["kind"] != "lockout")
    check("it is still the countdown state", status["kind"] == "countdown")


def test_lockout_end_freezes_the_day_count():
    data = copy.deepcopy(GOOD)
    data["lockout_start"] = "2026-12-02"
    data["lockout_end"] = "2027-03-01"
    status = blp.compute_status(data, et(2027, 5, 1))  # well after lockout_end
    check("status kind is lockout_ended", status["kind"] == "lockout_ended")
    check("day count freezes at the day the stoppage reached", status["day"] == 90)
    check(
        "the sentence reports the frozen count",
        blp.render_sentence(status) == "Lockout ended Mar. 1, 2027 after 90 days",
    )


def test_service_time_days_at_stake():
    data = copy.deepcopy(GOOD)
    data["lockout_start"] = "2026-12-02"
    data["regular_season_delayed_from"] = "2027-03-26"
    status = blp.compute_status(data, et(2027, 4, 3))
    check("9 days missed, inclusive of the original opener", status["days_at_stake"] == 9)
    check(
        "the sentence appends the days-at-stake figure",
        "9 service-time days at stake" in blp.render_sentence(status),
    )


# ---------------------------------------------------------------------------
# The expiry-instant boundary: the CBA expires 11:59 p.m. ET on cba_expires,
# i.e. 2026-12-02T04:59:00Z, never midnight UTC and never the visitor's or
# build server's local midnight. See QA finding #4.
# ---------------------------------------------------------------------------

def test_nov_30_is_still_one_day_until():
    data = copy.deepcopy(GOOD)
    status = blp.compute_status(data, et(2026, 11, 30))
    check("Nov 30 ET noon: countdown, 1 day until", status["kind"] == "countdown")
    check("Nov 30 ET noon: day == 1", status["day"] == 1)


def test_dec_1_midnight_et_is_expiring_today_not_already_expired():
    data = copy.deepcopy(GOOD)
    status = blp.compute_status(data, et(2026, 12, 1, 0, 0))
    check("Dec 1 00:00 ET is 'expiring_today', not 'expired'", status["kind"] == "expiring_today")
    check(
        "the sentence announces tonight's expiry",
        blp.render_sentence(status) == "The CBA expires tonight at 11:59 p.m. ET",
    )


def test_dec_1_11_58_pm_et_is_still_expiring_today():
    data = copy.deepcopy(GOOD)
    status = blp.compute_status(data, et(2026, 12, 1, 23, 58))
    check("Dec 1 23:58 ET is still 'expiring_today'", status["kind"] == "expiring_today")


def test_dec_2_12_01_am_et_is_expired():
    data = copy.deepcopy(GOOD)
    status = blp.compute_status(data, et(2026, 12, 2, 0, 1))
    check("Dec 2 00:01 ET is 'expired_no_lockout'", status["kind"] == "expired_no_lockout")
    sentence = blp.render_sentence(status)
    check(
        "the sentence names the exact expiry moment",
        sentence.startswith("The CBA expired at 11:59 p.m. ET on Dec. 1, 2026."),
    )
    check("the sentence never says 'in this file'", "in this file" not in sentence)
    check("the sentence never implies a deal was reached", "resolved" not in sentence.lower())


def test_cba_expires_instant_is_the_fixed_utc_moment():
    # 11:59 p.m. ET on Dec 1, 2026 is Dec is EST (UTC-5) -> 2026-12-02T04:59Z.
    instant = blp.cba_expires_instant(dt.date(2026, 12, 1))
    check(
        "cba_expires_instant resolves to 2026-12-02T04:59:00Z",
        instant.astimezone(dt.timezone.utc).isoformat() == "2026-12-02T04:59:00+00:00",
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


def test_the_page_links_the_site_stylesheets():
    html_text = LOCKOUT_PAGE.read_text(encoding="utf-8")
    check('links styles.css', '<link rel="stylesheet" href="styles.css" />' in html_text)
    check('links taxes.css (for .tax-main/.panel/.panel-note)', '<link rel="stylesheet" href="taxes.css" />' in html_text)


def test_the_status_sentence_is_not_duplicated():
    html_text = LOCKOUT_PAGE.read_text(encoding="utf-8")
    check(
        "exactly one #lockout-status-note element",
        html_text.count('id="lockout-status-note"') == 1,
    )


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


def test_the_page_never_reveals_how_it_is_built():
    html_text = LOCKOUT_PAGE.read_text(encoding="utf-8")
    lowered = html_text.lower()
    check("no 'in this file' copy", "in this file" not in lowered)
    check("no script filename reaches the HTML", "write_player_pages.py" not in html_text)
    check("no README link in the page", "readme" not in lowered)
    check("'public data', never 'public mlb data'", "public mlb data" not in lowered)


def test_the_page_says_public_data_not_public_mlb_data():
    html_text = LOCKOUT_PAGE.read_text(encoding="utf-8")
    check("'public data' phrase is present", "public data" in html_text.lower())
    check("'public MLB data' is never used", "public mlb data" not in html_text.lower())


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

    faq_answers = {q["name"]: q["acceptedAnswer"]["text"] for q in faq_node["mainEntity"]}
    lockout_q = next(q for q in faq_answers if "accrue during an MLB lockout" in q)
    check(
        "FAQ 1 does not give a flat 'No'",
        not faq_answers[lockout_q].strip().startswith("No."),
    )
    check(
        "FAQ 1 says offseason lockouts don't cost service time on their own",
        "doesn't cost a day on its own" in faq_answers[lockout_q],
    )


def test_visible_copy_matches_faq_on_off_days_and_offseason_lockouts():
    html_text = LOCKOUT_PAGE.read_text(encoding="utf-8")
    normalized = " ".join(html_text.split())
    check(
        "visible copy says off-days DO count during the season",
        "credited for every day on the active roster or injured list" in normalized
        or "off-days included" in normalized.lower()
        or "off-days: a player is credited" in normalized.lower(),
    )
    check(
        "visible copy does not say no service time accrues on days no games are played",
        "no service time accrues on days no games are played" not in normalized.lower(),
    )


def test_options_copy_is_not_self_contradicting():
    html_text = LOCKOUT_PAGE.read_text(encoding="utf-8")
    normalized = " ".join(html_text.split()).lower()
    check(
        "options copy does not claim option moves not on a 40-man roster are unaffected",
        "minor league option moves, is generally unaffected" not in normalized,
    )


def test_nav_reaches_lockout_on_hand_written_and_generated_pages():
    import write_player_pages as wpp

    for rel in ("index.html", "about.html", "privacy.html", "neutrality.html", "contract.html", "lockout.html"):
        html_text = (ROOT / "docs" / rel).read_text(encoding="utf-8")
        check(f"docs/{rel} nav links to lockout.html", 'href="lockout.html"' in html_text)

    generated = wpp.render_super_two([], {}, "2026-01-01", None)
    check("a generated page's nav links to /lockout.html", "/lockout.html" in generated)


def test_sitemap_generator_lists_the_lockout_page():
    # Must check the generator itself, not just the committed XML -- the
    # committed file is regenerated from this list every day, so a test that
    # only reads docs/sitemap-core.xml would stop catching a regression the
    # moment someone removes the entry from the source. See QA finding #2.
    import write_player_pages as wpp

    hand_written_paths = [path for path, _priority in wpp.HAND_WRITTEN_PAGES]
    check("lockout.html is in HAND_WRITTEN_PAGES", "lockout.html" in hand_written_paths)

    nav_paths = [path for path, _label, _tag in wpp.NAV_SECTIONS]
    check("lockout.html is in NAV_SECTIONS", "lockout.html" in nav_paths)


def test_committed_sitemap_also_lists_it_today():
    # Not the authoritative check (see above), but worth confirming the
    # currently-committed file agrees with what's been built.
    sitemap = (ROOT / "docs" / "sitemap-core.xml").read_text(encoding="utf-8")
    check("sitemap-core.xml currently lists /lockout.html", "/lockout.html</loc>" in sitemap)


def test_meta_description_fits_search_result_length():
    html_text = LOCKOUT_PAGE.read_text(encoding="utf-8")
    m = re.search(r'<meta name="description" content="([^"]*)"', html_text)
    check("a meta description is present", m is not None)
    if m:
        import html as html_mod
        desc = html_mod.unescape(m.group(1))
        check(f"meta description is <= 160 chars (got {len(desc)})", len(desc) <= 160)


if __name__ == "__main__":
    test_the_real_data_file_validates()
    test_a_well_formed_fixture_validates()
    test_missing_top_level_field_is_rejected()
    test_bad_iso_date_is_rejected()
    test_compact_iso_date_is_rejected()
    test_week_date_is_rejected()
    test_missing_source_is_rejected()
    test_non_https_source_url_is_rejected()
    test_bare_https_url_is_rejected()
    test_url_with_embedded_whitespace_is_rejected()
    test_future_dated_entry_is_rejected()
    test_lockout_end_before_lockout_start_is_rejected()
    test_countdown_before_a_lockout()
    test_day_count_after_a_lockout_starts()
    test_a_future_lockout_start_is_still_the_countdown_state()
    test_lockout_end_freezes_the_day_count()
    test_service_time_days_at_stake()
    test_nov_30_is_still_one_day_until()
    test_dec_1_midnight_et_is_expiring_today_not_already_expired()
    test_dec_1_11_58_pm_et_is_still_expiring_today()
    test_dec_2_12_01_am_et_is_expired()
    test_cba_expires_instant_is_the_fixed_utc_moment()
    test_entries_render_newest_first()
    test_entries_are_html_escaped()
    test_last_updated_is_the_newer_of_updated_and_newest_entry()
    test_the_page_has_been_built()
    test_the_page_links_the_site_stylesheets()
    test_the_status_sentence_is_not_duplicated()
    test_the_page_carries_exactly_one_ga_tag_and_one_adsense_block()
    test_the_page_carries_the_footer_disclaimer_and_contact()
    test_the_page_never_reveals_how_it_is_built()
    test_the_page_says_public_data_not_public_mlb_data()
    test_the_json_ld_parses_and_has_article_and_faqpage()
    test_visible_copy_matches_faq_on_off_days_and_offseason_lockouts()
    test_options_copy_is_not_self_contradicting()
    test_nav_reaches_lockout_on_hand_written_and_generated_pages()
    test_sitemap_generator_lists_the_lockout_page()
    test_committed_sitemap_also_lists_it_today()
    test_meta_description_fits_search_result_length()

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
