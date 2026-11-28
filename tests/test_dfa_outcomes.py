"""DFA outcome classification and the /dfa-outcomes.html page.

    python3 tests/test_dfa_outcomes.py
"""

from __future__ import annotations

import datetime as dt
import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_dfa_outcomes_page as page  # noqa: E402
import cba  # noqa: E402
import career_moves  # noqa: E402
import dfa_outcomes as dfa  # noqa: E402
import write_player_pages as wpp  # noqa: E402

PASS = 0
FAIL = 0

DFA = "Club designated RHP Some Name for assignment."
TODAY = dt.date(2026, 11, 23)
WINDOW = 7


def check(label: str, ok: bool) -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {label}")


def _row(date: str, description: str, **ids) -> dict:
    row = {"date": date, "description": description}
    row.update(ids)
    return row


def _follow(description: str, date: str, **ids):
    row = _row(date, description, **ids)
    return (_day(date), career_moves.classify_move(row), dfa.destination_club(row))


def _day(iso: str) -> dt.date:
    return dt.date.fromisoformat(iso)


def _classify(followups, *, dfa_date="2026-11-17", today=TODAY, window=WINDOW):
    return dfa.classify_followups(
        _day(dfa_date), followups, today=today, window_days=window
    )


def test_each_outcome() -> None:
    trade = _classify([
        _follow(
            "Club traded RHP Some Name to Other.",
            "2026-11-18",
            from_team_id=147,
            to_team_id=111,
        )
    ])
    check("a later trade is a trade", trade[0] == "trade" and trade[1] == _day("2026-11-18"))
    check("the trade names the destination club", trade[2] == 111)

    claim = _classify([
        _follow(
            "Other claimed RHP Some Name off waivers from Club.",
            "2026-11-17",
            from_team_id=147,
            to_team_id=111,
        )
    ])
    check("a same-day claim is a claim", claim[0] == "claim" and claim[1] == _day("2026-11-17"))
    check("the claim names the club that claimed him", claim[2] == 111)

    outright = _classify([
        _follow(
            "Club sent RHP Some Name outright to the farm.",
            "2026-11-19",
            from_team_id=147,
            to_team_id=410,
        )
    ])
    check("an outright is an outright", outright[0] == "outright")
    check("an outright does not invent a new major league club", outright[2] is None)

    release = _classify([
        _follow("Club released RHP Some Name.", "2026-11-19", from_team_id=147)
    ])
    check("a release is a release", release[0] == "release" and release[2] is None)

    elected = _classify([
        _follow("RHP Some Name elected free agency.", "2026-11-20")
    ])
    check("an election is free agency", elected[0] == "free_agency" and elected[1] == _day("2026-11-20"))
    check("free agency does not name a new club", elected[2] is None)


def test_an_outright_he_rejects_is_free_agency() -> None:
    kind, day, club, _left = _classify([
        _follow(
            "Club sent RHP Some Name outright to the farm.",
            "2026-11-18",
            from_team_id=147,
        ),
        _follow("RHP Some Name elected free agency.", "2026-11-20"),
    ])
    check("rejecting the outright is free agency", kind == "free_agency")
    check("the election date is the outcome date", day == _day("2026-11-20") and club is None)


def test_a_trade_beats_an_outright_in_the_same_window() -> None:
    kind, _day_out, club, _left = _classify([
        _follow(
            "Club sent RHP Some Name outright to the farm.",
            "2026-11-17",
            from_team_id=147,
        ),
        _follow(
            "Club traded RHP Some Name to Other.",
            "2026-11-18",
            from_team_id=147,
            to_team_id=111,
        ),
    ])
    check("the trade is the outcome", kind == "trade" and club == 111)


def test_pending_counts_days_left_and_follows_the_ruleset_window() -> None:
    kind, _date, _club, left = _classify([], dfa_date="2026-11-23", today=TODAY, window=7)
    check("a designation today is pending", kind == "pending" and left == 7)
    kind, _date, _club, left = _classify([], dfa_date="2026-11-17", today=TODAY, window=7)
    check("the last open day has one day left", kind == "pending" and left == 1)
    kind, _date, _club, left = _classify([], dfa_date="2026-11-16", today=TODAY, window=7)
    check("the day after the window is not pending", kind == "unresolved" and left is None)
    kind, _date, _club, left = _classify([], dfa_date="2026-11-15", today=TODAY, window=9)
    check("a longer ruleset window is still open", kind == "pending" and left == 1)
    check(
        "the label names the days left",
        dfa.outcome_label("pending", 1) == "Pending, 1 day left",
    )
    check(
        "more than one day is plural",
        dfa.outcome_label("pending", 4) == "Pending, 4 days left",
    )


def test_a_move_outside_the_window_does_not_resolve_it() -> None:
    kind, *_rest = _classify(
        [_follow("RHP Some Name elected free agency.", "2026-12-20")],
        dfa_date="2026-11-01",
        today=dt.date(2026, 11, 20),
        window=7,
    )
    check("an election weeks later is not this designation's outcome", kind == "unresolved")


def test_duplicate_rows_collapse_and_nested_club_ids_count() -> None:
    rows = [
        {"date": "2026-11-17", "description": DFA, "toTeam": {"id": 147}},
        {"date": "2026-11-17", "description": DFA, "id": 9},
        _row(
            "2026-11-18",
            "Club traded RHP Some Name to Other.",
            from_team_id=147,
            to_team_id=111,
        ),
    ]
    merged = dfa.merge_rows(rows[:2])
    check("two copies of one designation collapse", len(merged) == 1)
    check("the nested club id is kept", dfa.designating_club(merged[0]) == 147)
    events = dfa.events_for_player(
        5,
        rows,
        today=TODAY,
        window_days=WINDOW,
        span_days=60,
        player={"name": "Ada Player", "service_time": "2.015", "href": "/p/5-ada-player.html"},
    )
    check("one designation survives the duplicate feed", len(events) == 1)
    event = events[0]
    check("the stored service-time figure is copied", event["service_time"] == "2.015")
    check("the outcome is the trade", event["outcome"] == "trade" and event["new_club_id"] == 111)
    check("the player page path is kept", event["href"] == "/p/5-ada-player.html")


def test_two_designations_stay_two_rows_and_old_ones_drop() -> None:
    rows = [
        _row("2026-09-01", DFA, to_team_id=147),
        _row("2026-11-02", DFA, to_team_id=147),
        _row(
            "2026-11-03",
            "Other claimed RHP Some Name off waivers from Club.",
            from_team_id=147,
            to_team_id=111,
        ),
        _row("2026-11-17", DFA, to_team_id=147),
    ]
    events = dfa.events_for_player(
        5,
        rows,
        today=TODAY,
        window_days=WINDOW,
        span_days=60,
        player={"name": "Ada Player", "service_time": "1.000", "href": ""},
    )
    by_date = {event["dfa_date"]: event for event in events}
    check("a designation older than the span is left out", "2026-09-01" not in by_date)
    check("both recent designations are kept", set(by_date) == {"2026-11-17", "2026-11-02"})
    check("the earlier one was claimed", by_date["2026-11-02"]["outcome"] == "claim")
    check("the later one is still pending", by_date["2026-11-17"]["outcome"] == "pending")
    check("the last open day has one day left", by_date["2026-11-17"]["days_left"] == 1)


def test_the_page_lists_every_row_and_the_filter_is_plain_links() -> None:
    payload = {
        "as_of": "2026-11-23",
        "window_days": 9,
        "span_days": 60,
        "rows": [
            {
                "id": 5,
                "name": "Ada Player",
                "href": "/p/5-ada-player.html",
                "club_id": 147,
                "club": "Sample Club",
                "dfa_date": "2026-11-20",
                "outcome": "trade",
                "outcome_date": "2026-11-21",
                "new_club_id": 111,
                "new_club": "Other Club",
                "days_left": None,
                "service_time": "2.015",
            },
            {
                "id": 6,
                "name": "Bea Player",
                "href": "",
                "club_id": 147,
                "club": "Sample Club",
                "dfa_date": "2026-11-23",
                "outcome": "pending",
                "outcome_date": "",
                "new_club_id": None,
                "new_club": "",
                "days_left": 9,
                "service_time": "0.004",
            },
        ],
    }
    html_out = page.render(payload)
    bad = page.problems(html_out, ROOT / "docs" / "p", payload)
    check("builder checks pass on a fixture", not bad)
    if bad:
        for item in bad:
            print(f"    {item}")
    check("the document title is the search title", page.TITLE in html_out)
    check("the lede is one sentence", html_out.count('<p class="lede">') == 1 and page.LEDE in html_out)
    recent = html_out.split('id="recent"', 1)[1].split("<section", 1)[0]
    pending = html_out.split('id="pending"', 1)[1].split('id="recent"', 1)[0]
    check("both rows are in the main table", "Ada Player" in recent and "Bea Player" in recent)
    check("the newer designation is first", recent.index("Ada Player") > recent.index("Bea Player"))
    check("pending is its own section and names the days left", "Bea Player" in pending and "Pending, 9 days left" in pending)
    check("a resolved row is not repeated as pending", "Ada Player" not in pending)
    check("the club filter is an anchor", 'href="#sample-club"' in html_out and 'id="sample-club"' in html_out)
    check("a published player is linked", 'href="/p/5-ada-player.html">Ada Player</a>' in html_out)
    check("a player with no page is plain text", ">Bea Player</a>" not in html_out)
    check("service time is the stored figure", "2.015" in recent and "0.004" in recent)
    check("the new club is shown for a trade", "Other Club" in recent)
    check("the note uses the ruleset window", "The club has 9 days" in html_out)
    check("a different window does not leave the old length", "The club has 7 days" not in html_out)
    check("the note points at the roster guide", 'href="/send-a-player-down.html#dfa"' in html_out)
    check("no last-updated line", "last updated" not in html_out.lower())
    lowered = html_out.lower()
    for banned in ("baseball-reference", "fangraphs", "spotrac", "cot's", "mlb trade rumors"):
        check(f"fixture has no {banned}", banned not in lowered)


def test_the_page_is_wired_into_the_site() -> None:
    core = (ROOT / "docs" / "sitemap-core.xml").read_text(encoding="utf-8")
    check("sitemap lists the page", "/dfa-outcomes.html</loc>" in core)
    check(
        "the page is a hand-listed sitemap page",
        "dfa-outcomes.html" in [path for path, _prio in wpp.HAND_WRITTEN_PAGES],
    )
    for name in ("update-service-time.yml", "backfill-history.yml"):
        body = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        check(f"{name} commits the page", "docs/dfa-outcomes.html" in body)
        check(f"{name} commits the snapshot", "data/dfa_outcomes.json" in body)
        check(f"{name} rebuilds the page", "scripts/build_dfa_outcomes_page.py" in body)
    daily = (ROOT / ".github" / "workflows" / "update-service-time.yml").read_text(encoding="utf-8")
    raw_job = daily.split("raw-transactions:", 1)[1]
    check("the raw job commits the snapshot with the store", raw_job.count("data/dfa_outcomes.json") >= 1)
    check("the raw job commits the page with the store", "docs/dfa-outcomes.html" in raw_job)
    check("both raw commits pass the page list", raw_job.count('"${transaction_pages[@]}"') >= 2)
    check(
        "the raw job does not restore every club page",
        "docs/t\n" not in raw_job and "docs/t \\" not in raw_job,
    )
    commit_sh = (ROOT / "scripts" / "commit_data.sh").read_text(encoding="utf-8")
    calls = [
        i for i, line in enumerate(commit_sh.splitlines())
        if line.strip() == "rebuild_transaction_pages"
    ]
    reset_at = next(
        i for i, line in enumerate(commit_sh.splitlines())
        if line.strip().startswith("git reset --hard")
    )
    first_commit = next(
        i for i, line in enumerate(commit_sh.splitlines())
        if line.strip().startswith('git commit')
    )
    check("a transaction commit rebuilds the pages first", any(i < first_commit for i in calls))
    check("a rejected push rebuilds the pages after taking the remote tip", any(i > reset_at for i in calls))
    check(
        "the rebuild reads both stores and the snapshot path",
        all(token in commit_sh for token in ("data/cache", "data/raw_transactions", "data/dfa_outcomes.json")),
    )
    rebuild = (ROOT / "scripts" / "rebuild_transaction_pages.py").read_text(encoding="utf-8")
    check("the rebuild writes this page", "scripts/build_dfa_outcomes_page.py" in rebuild)
    check("the rebuild writes the out-of-options pages", "scripts/out_of_options_pages.py" in rebuild)
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    check("ci runs the page check", "scripts/build_dfa_outcomes_page.py --check" in ci)
    check("ci names this suite", "tests/test_dfa_outcomes.py" in ci)

    docs = ROOT / "docs"
    link = 'href="/dfa-outcomes.html"'
    for name in ("offseason.html", "changes.html", "send-a-player-down.html"):
        check(f"{name} links the page", link in (docs / name).read_text(encoding="utf-8"))

    html_path = docs / "dfa-outcomes.html"
    data_path = ROOT / "data" / "dfa_outcomes.json"
    check("the page and snapshot are on disk", html_path.is_file() and data_path.is_file())
    if html_path.is_file() and data_path.is_file():
        stored = page.load_payload(data_path)
        rendered = page.render(stored)
        bad = page.problems(rendered, docs, stored)
        check("the published page matches the builder", html_path.read_text(encoding="utf-8") == rendered)
        check("the published page passes its checks", not bad)
        if bad:
            for item in bad:
                print(f"    {item}")
        window = int(cba.default().require("designated_for_assignment.max_service_days"))
        check("the published note uses the live ruleset", f"The club has {window} days" in rendered)
        lowered = rendered.lower()
        for banned in ("baseball-reference", "fangraphs", "spotrac", "cot's", "mlb trade rumors", "last updated"):
            check(f"published page has no {banned}", banned not in lowered)

    taxes = (docs / "taxes.html").read_bytes()
    check(
        "taxes.html is unchanged",
        hashlib.sha256(taxes).hexdigest() == "ef78b2f8160046145ce58382a0da8a55dc5ab3b98130fab271db847e1a45fe62",
    )


def main() -> None:
    test_each_outcome()
    test_an_outright_he_rejects_is_free_agency()
    test_a_trade_beats_an_outright_in_the_same_window()
    test_pending_counts_days_left_and_follows_the_ruleset_window()
    test_a_move_outside_the_window_does_not_resolve_it()
    test_duplicate_rows_collapse_and_nested_club_ids_count()
    test_two_designations_stay_two_rows_and_old_ones_drop()
    test_the_page_lists_every_row_and_the_filter_is_plain_links()
    test_the_page_is_wired_into_the_site()
    print(f"{PASS} passed, {FAIL} failed")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
