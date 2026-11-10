"""The roster-rules guide: original copy, ruleset numbers, and inbound links.

    python3 tests/test_roster_rules_page.py
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cba  # noqa: E402
import build_roster_rules_page as guide  # noqa: E402
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
    page = guide.render()
    bad = guide.problems(page)
    check("builder checks pass", not bad)
    if bad:
        for item in bad:
            print(f"    {item}")
    disk = (ROOT / "docs" / wpp.SEND_DOWN_PATH).read_text(encoding="utf-8")
    check("docs page matches the builder", disk == page)
    check("sitemap lists the guide",
          f"/{wpp.SEND_DOWN_PATH}</loc>" in (ROOT / "docs" / "sitemap-core.xml").read_text())
    check("the guide is a hand-listed sitemap page",
          wpp.SEND_DOWN_PATH in [path for path, _prio in wpp.HAND_WRITTEN_PAGES])
    for name in ("update-service-time.yml", "backfill-history.yml"):
        body = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        check(f"{name} commits the guide", f"docs/{wpp.SEND_DOWN_PATH}" in body)


def test_the_numbers_follow_the_ruleset() -> None:
    live = guide.render()
    rules = cba.default()
    years = int(rules.require("options.standard_option_years"))
    days = int(rules.require("options.days_to_burn_option_year"))
    window = int(rules.require("designated_for_assignment.max_service_days"))
    active = int(rules.require("roster.active_roster"))
    check("live page states the option-year count", f"{years} option years" in live)
    check("live page states the option-day threshold", f"{days} or more days" in live)
    check("live page states the DFA window", f"{window} days" in live)
    check("live page states the active roster", f"{active} players" in live)
    check("the guide does not restate the credited-year length", "172" not in live)

    config_dir = ROOT / "config" / "cba"
    base = json.loads((config_dir / "2022.json").read_text(encoding="utf-8"))
    base["options"]["standard_option_years"] = 4
    base["options"]["days_to_burn_option_year"] = 11
    base["designated_for_assignment"]["max_service_days"] = 9
    base["roster"]["active_roster"] = 25
    base["rule_5"]["protected_seasons_if_signed_at_18_or_younger"] = 9
    scratch = config_dir / "9999.json"
    scratch.write_text(json.dumps(base), encoding="utf-8")
    saved = cba.DEFAULT_VERSION
    try:
        cba.DEFAULT_VERSION = "9999"
        cba.load.cache_clear()
        moved = guide.render()
    finally:
        scratch.unlink(missing_ok=True)
        cba.DEFAULT_VERSION = saved
        cba.load.cache_clear()
    check("a changed option-year count reaches the page", "4 option years" in moved)
    check("the old option-year count does not linger", "3 option years" not in moved)
    check("a changed day threshold reaches the page", "11 or more days" in moved)
    check("a changed DFA window reaches the page", "9 days" in moved)
    check("the old DFA window does not linger", "7 days" not in moved)
    check("a changed active roster reaches the page", "25 players" in moved)
    check("a changed Rule 5 season count reaches the page", "9 seasons" in moved)


def test_existing_pages_link_to_the_guide() -> None:
    docs = ROOT / "docs"
    link = f'href="/{wpp.SEND_DOWN_PATH}"'
    home_link = f'href="{wpp.SEND_DOWN_PATH}"'
    for name in ("service-time.html", "options.html", "non-tender-watch.html", "index.html"):
        text = (docs / name).read_text(encoding="utf-8")
        check(f"{name} links to the guide", link in text or home_link in text)
    club_pages = sorted((docs / "t").glob("*-non-tender.html"))
    check("every club non-tender page exists", len(club_pages) == 30)
    for path in club_pages:
        check(
            f"{path.name} links to the guide",
            link in path.read_text(encoding="utf-8"),
        )
    index = (docs / "index.html").read_text(encoding="utf-8")
    check("the homepage menu links to the guide", link in guide_header(index))
    taxes = subprocess.check_output(["git", "show", "HEAD:docs/taxes.html"])
    check("taxes.html is byte-identical to HEAD",
          taxes == (docs / "taxes.html").read_bytes())


def guide_header(html_text: str) -> str:
    import re
    match = re.search(r'<header class="site-top".*?</header>', html_text, re.S)
    return match.group(0) if match else ""


def test_the_menu_on_hand_written_pages_includes_the_guide() -> None:
    link = f'href="/{wpp.SEND_DOWN_PATH}"'
    for name in (
        "index.html", "contract.html", "lockout.html",
        "about.html", "privacy.html", "neutrality.html",
    ):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        check(f"{name} header links to the guide", link in guide_header(text))
        check(f"{name} footer links to the guide", link in text.split("<footer", 1)[-1])


def main() -> None:
    test_the_published_page_matches_the_builder()
    test_the_numbers_follow_the_ruleset()
    test_existing_pages_link_to_the_guide()
    test_the_menu_on_hand_written_pages_includes_the_guide()
    print(f"{PASS} passed, {FAIL} failed")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
