"""Snippet length, share tags, and the links that keep list pages reachable.

No pytest. Titles stay at or under 60 characters. Descriptions stay at or
under 155. The share image is 1200 by 630. taxes.html is not part of this.

    python3 tests/test_share_pages.py
"""

from __future__ import annotations

import hashlib
import html
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import list_links  # noqa: E402
import page_titles  # noqa: E402
import share_meta  # noqa: E402
import write_player_pages as wpp  # noqa: E402

PASS = 0
FAIL = 0


def check(label, ok):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL += 1
        print(f"FAIL  {label}")


def test_titles_and_metas_fit_a_snippet():
    samples = [
        page_titles.homepage_title(),
        page_titles.super_two_title({"official": True, "cutoff": "2.133", "season": 2026}),
        page_titles.super_two_title(None),
        page_titles.non_tender_title(2026),
        page_titles.club_fa_title("Arizona Diamondbacks", 5, 2027),
        page_titles.club_fa_title("Chicago Cubs", 0, 2027),
        page_titles.player_title("Christian Encarnacion-Strand"),
        page_titles.club_roster_title("Arizona Diamondbacks"),
        page_titles.free_agents_title(2026),
        page_titles.arbitration_title(2027),
        page_titles.explainer_title(),
        page_titles.lockout_title(),
        page_titles.salary_cap_title(),
    ]
    for title in samples:
        check(f"title <= 60 ({len(title)}): {title}", len(title) <= 60)
    check(
        "homepage title names MLB service time",
        "MLB service time" in page_titles.homepage_title(),
    )
    check(
        "official Super Two title states 2.133 and not projection",
        page_titles.super_two_title({"official": True, "cutoff": "2.133", "season": 2026})
        == "Super Two cutoff is 2.133 after 2026 | Big League"
        and "projection" not in page_titles.super_two_core(
            {"official": True, "cutoff": "2.133", "season": 2026}
        ),
    )
    check(
        "a projection keeps the projection title",
        page_titles.super_two_core(None) == "Super Two cutoff projection",
    )
    metas = [
        page_titles.homepage_meta(),
        page_titles.non_tender_meta(84, 2026, 50, 42),
        page_titles.club_fa_meta("Chicago Cubs", 6, 1, 2027),
        page_titles.player_meta(
            "Christian Encarnacion-Strand", "12.067", 2131,
            "last with Los Angeles Dodgers", "No longer on a 40-man roster",
        ),
        page_titles.explainer_meta(172, "3.000", "6.000", "8.000", "10.000"),
        page_titles.free_agents_meta(127, 2026),
        page_titles.arbitration_meta(303, 281, 22, "3.000", 2027),
    ]
    for meta in metas:
        check(f"meta <= 155 ({len(meta)})", len(meta) <= 155)
    fa = page_titles.club_fa_meta("Chicago Cubs", 6, 1, 2027)
    check("one more uses is", "1 more is projected" in fa and "1 more are" not in fa)
    check("homepage meta keeps 172, 3.000 and 6.000",
          "172" in page_titles.homepage_meta()
          and "3.000" in page_titles.homepage_meta()
          and "6.000" in page_titles.homepage_meta())
    check("non-tender meta keeps the deadline",
          "November 20, 2026" in page_titles.non_tender_meta(84, 2026, 50, 42))


def test_rendered_pages_keep_the_snippet_limits():
    pages = {
        "super two": wpp.render_super_two(
            [], {}, "2026-11-09",
            {"official": True, "cutoff": "2.133", "season": 2026, "cutoff_days": 477},
        ),
        "free agents": wpp.render_free_agents([], {}, "2026-11-09"),
        "non-tender": wpp.render_non_tender_watch([], "2026-11-09", 2027),
        "options": wpp.render_options([], {}, "2026-11-09"),
        "explainer": wpp.render_explainer("2026-11-09"),
        "club": wpp.render_club("Arizona Diamondbacks", [], "2026-11-09"),
        "club fa": wpp.render_club_free_agents("Chicago Cubs", [], [], "2026-11-09", 2027),
    }
    for label, page in pages.items():
        title = html.unescape(page.split("<title>")[1].split("</title>")[0])
        desc = html.unescape(page.split('meta name="description" content="')[1].split('"')[0])
        check(f"{label} title <= 60 ({len(title)})", len(title) <= page_titles.TITLE_MAX)
        check(f"{label} meta <= 155 ({len(desc)})", len(desc) <= page_titles.META_MAX)
        check(f"{label} has one share image", page.count('property="og:image"') == 1)
        check(f"{label} has a twitter card", 'name="twitter:card"' in page)


def test_player_and_alumni_links():
    trout = {
        "id": 545361, "name": "Mike Trout", "team": "Los Angeles Angels",
        "on_40_man": True, "service_time": "15.048", "service_days_total": 2628,
        "free_agent_eligible": True, "position": "CF", "mlb_debut": "2011-07-08",
    }
    page = wpp.render(trout, {}, "2026-11-09", [])
    check("Trout links the free-agent list", 'href="/free-agents.html"' in page)
    check("Trout links the Angels free-agent page",
          'href="/t/los-angeles-angels-free-agents.html"' in page)
    check("Trout links the contract clock", 'href="/contract.html"' in page)
    check("a player on no list gets no empty winter row",
          "On this winter" not in wpp.render(
              {"id": 1, "name": "Rookie", "team": "Atlanta Braves", "on_40_man": True,
               "service_time": "0.040", "service_days_total": 40, "position": "P",
               "mlb_debut": "2024-04-01"},
              {}, "2026-11-09", [],
          ))
    cubs = wpp.render_club(
        "Chicago Cubs",
        [{"id": 1, "name": "A", "team": "Chicago Cubs", "on_40_man": True,
          "service_time": "1.000", "service_days_total": 172, "free_agent_eligible": True,
          "position": "P"}],
        "2026-11-09",
    )
    check("club meter links free agents", 'href="chicago-cubs-free-agents.html"' in cubs)
    check("club meter links arbitration", 'href="chicago-cubs-arbitration.html"' in cubs)
    check("club meter links Super Two", 'href="/super-two.html"' in cubs)

    alum = {
        "id": 9, "name": "Ada Alumni", "team": "Atlanta Braves", "on_40_man": False,
        "service_time": "2.000", "service_days_total": 344, "position": "P",
        "mlb_debut": "2014-04-01",
        "seasons": [{"y": 2016, "d": 80, "t": [144]}],
    }
    index = list_links.index_seasons([
        (9, "Ada Alumni", alum["seasons"]),
        (10, "Shared Season", [{"y": 2016, "d": 90, "t": [144]}]),
    ])
    apage = wpp.render(alum, {}, "2026-11-09", [], season_index=index)
    check("alumnus links her last club", 'href="/t/atlanta-braves.html"' in apage)
    check("alumnus links a season teammate", "Shared Season" in apage)
    check("alumnus links the surname list", 'href="/alumni/a.html"' in apage)
    old = wpp.render(
        {"id": 11, "name": "Old Club", "team": "Club Nowhere", "on_40_man": False,
         "service_time": "1.000", "service_days_total": 172, "mlb_debut": "2008-04-01"},
        {}, "2026-11-09", [],
    )
    check("an unknown club name is not a dead link", "club-nowhere" not in old)
    check("the stored club is still named", "Last with Club Nowhere" in old)
    same = page_titles.title_hints([
        {"id": 1, "name": "Alex Smith", "position": "P", "mlb_debut": "2018-04-01"},
        {"id": 2, "name": "Alex Smith", "position": "CF", "mlb_debut": "2021-05-01"},
        {"id": 3, "name": "Only One", "position": "C", "mlb_debut": "2019-04-01"},
    ])
    t1 = page_titles.player_title("Alex Smith", same[1])
    t2 = page_titles.player_title("Alex Smith", same[2])
    check("shared names get different titles", t1 != t2 and "P" in t1 and "CF" in t2)
    check("a unique name has no extra hint", 3 not in same)
    check("shared titles stay within 60", len(t1) <= 60 and len(t2) <= 60)
    for club in (
        "Arizona Diamondbacks", "Philadelphia Phillies",
        "San Francisco Giants", "Washington Nationals",
    ):
        titled = page_titles.club_roster_title(club)
        check(f"{club} hub keeps the brand ({len(titled)})", titled.endswith("| Big League") and len(titled) <= 60)

    nt = wpp.render_non_tender_watch(
        [({"id": 1, "name": "N", "team": "Arizona Diamondbacks",
           "service_time": "5.000", "service_days_total": 900}, ["played in just 4 games last season"])],
        "2026-11-09", 2027,
    )
    check(
        "non-tender club header points at the club subpage",
        'href="/t/arizona-diamondbacks-non-tender.html"' in nt,
    )
    check("non-tender page links the other lists",
          all(href in nt for href in (
              "arbitration-eligible.html", "super-two.html", "free-agents.html", "options.html",
          )))


def test_share_image_and_parked_taxes_page():
    path = ROOT / "docs" / "og.png"
    check("share image exists", path.is_file())
    raw = path.read_bytes()
    check("share image is a PNG", raw.startswith(b"\x89PNG"))
    width = int.from_bytes(raw[16:20], "big")
    height = int.from_bytes(raw[20:24], "big")
    check(f"share image is 1200x630 ({width}x{height})", width == 1200 and height == 630)
    tags = share_meta.tags("https://bigleagueservicetime.com")
    check("tags include og:image", 'property="og:image"' in tags)
    check("tags include twitter:card", 'name="twitter:card"' in tags)
    taxes = (ROOT / "docs" / "taxes.html").read_text(encoding="utf-8")
    check("taxes.html has no og:image", "og:image" not in taxes)
    check(
        "taxes.html is the parked file",
        hashlib.sha256((ROOT / "docs" / "taxes.html").read_bytes()).hexdigest()
        == "ef78b2f8160046145ce58382a0da8a55dc5ab3b98130fab271db847e1a45fe62",
    )


if __name__ == "__main__":
    test_titles_and_metas_fit_a_snippet()
    test_rendered_pages_keep_the_snippet_limits()
    test_player_and_alumni_links()
    test_share_image_and_parked_taxes_page()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
