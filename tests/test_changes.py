"""The daily change log and /changes.html.

    python3 tests/test_changes.py
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_changes_page as page  # noqa: E402
import build_offseason_page as hub  # noqa: E402
import change_log  # noqa: E402
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


def _row(desc: str, day: str = "2026-08-01", **ids) -> dict:
    row = {"date": day, "description": desc}
    row.update(ids)
    return row


def _player(pid: int, days: int, *, flag: int = 0, version: int = 10) -> dict:
    return {
        "id": pid,
        "name": f"Player {pid}",
        "service_days_total": days,
        "on_40_man": True,
        "team_id": 147,
        "team": "New York Yankees",
        "rules_version": version,
        "super_two_candidate": bool(flag),
    }


def _snap(players: list[list], version: int = 10, day: str = "2026-11-16") -> dict:
    return {"as_of": day, "rules_version": version, "players": players}


def test_roster_moves_use_the_listed_kinds() -> None:
    samples = [
        (_row("New York Mets recalled 1B Eric Wagaman from Syracuse Mets.", from_team_id=552, to_team_id=121), "call-up"),
        (_row("Houston Astros selected the contract of RHP Roddery Munoz from Sugar Land.", "2026-11-02", to_team_id=117), "call-up"),
        (_row("New York Yankees optioned SS Anthony Volpe to Scranton.", from_team_id=147, to_team_id=531), "option"),
        (_row("New York Mets designated SS Christian Cairo for assignment.", "2026-11-02", to_team_id=121), "dfa"),
        (_row("Chicago Cubs outrighted INF Some Player to Iowa Cubs.", from_team_id=112), "outright"),
        (_row("Athletics released RHP Some Pitcher.", from_team_id=133), "release"),
        (_row("Atlanta Braves signed free agent RHP Evan Phillips.", "2026-11-12", to_team_id=144), "signing"),
        (_row("Atlanta Braves signed Some Player to a minor league contract.", "2026-11-12", to_team_id=144), "signing"),
        (_row("San Francisco Giants traded RHP Ryan Walker to Milwaukee Brewers.", "2026-11-15", from_team_id=137, to_team_id=158), "trade"),
        (_row("New York Yankees placed RHP Gerrit Cole on the 15-day injured list.", "2026-07-04", to_team_id=147), "il"),
        (_row("Toronto Blue Jays activated RHP Cody Ponce from the 60-day injured list.", "2026-11-02", to_team_id=141), "il"),
    ]
    for row, kind in samples:
        check(f"{kind}: {row['description'][:48]}", change_log.classify_move(row) == kind)
    trade = samples[-3][0]
    check(
        "a trade names both clubs",
        change_log.team_label("trade", trade) == "San Francisco Giants to Milwaukee Brewers",
    )
    skipped = [
        _row("SS Brayan Rocchio assigned to Tiburones de La Guaira.", "2026-11-15", to_team_id=698),
        _row("Baltimore Orioles claimed RHP Chase Hampton off waivers from St. Louis Cardinals.", "2026-11-02", from_team_id=138, to_team_id=110),
        _row("RHP Kenley Jansen elected free agency.", "2026-11-02", to_team_id=116),
        _row("Tampa Bay Rays activated RHP Ty Johnson.", "2026-11-02", to_team_id=139),
        _row("New York Yankees placed RHP Gerrit Cole on the 15-day injured list.", "2026-11-02", to_team_id=147),
    ]
    for row in skipped:
        check(f"not a listed move: {row['description'][:48]}", change_log.classify_move(row) is None)


def test_thresholds_follow_the_ruleset_length_and_super_two_is_separate() -> None:
    names = {1: "Ada Player", 2: "Bea Player"}
    teams = {1: "New York Yankees", 2: "New York Mets"}
    previous = _snap([[1, 99, 1, 147, 0], [2, 200, 1, 121, 0]])
    current = _snap([[1, 100, 1, 147, 0], [2, 200, 1, 121, 1]], version=10, day="2026-11-17")
    changed = change_log.service_changes(
        previous, current, per_year=100, names=names, teams=teams
    )
    check("100 days is one year when the agreement says 100", [item["years"] for item in changed["thresholds"]] == [1])
    check("the crossing names the player", changed["thresholds"][0]["name"] == "Ada Player")
    check("super two entry is its own list", [item["name"] for item in changed["super_two"]] == ["Bea Player"])
    quiet = change_log.service_changes(
        previous, current, per_year=172, names=names, teams=teams
    )
    check("the same 100 days do not cross a longer year", quiet["thresholds"] == [])
    check("super two does not depend on the year length", len(quiet["super_two"]) == 1)
    jumped = change_log.service_changes(
        _snap([[1, 50, 1, 147, 0]]),
        _snap([[1, 250, 1, 147, 0]]),
        per_year=100,
        names=names,
        teams=teams,
    )
    check(
        "a jump can cross more than one year",
        [item["years"] for item in jumped["thresholds"]] == [1, 2],
    )
    check("no previous snapshot does not invent crossings", change_log.service_changes(
        None, current, per_year=100, names=names, teams=teams
    )["thresholds"] == [])


def test_a_rules_change_is_one_count_not_a_player_list() -> None:
    names = {1: "Ada Player", 2: "Bea Player", 3: "New Player"}
    teams = {1: "New York Yankees", 2: "New York Mets", 3: "Boston Red Sox"}
    previous = _snap([[1, 10, 1, 147, 0], [2, 300, 1, 121, 0]], version=9)
    current = _snap([[1, 400, 1, 147, 1], [2, 250, 1, 121, 0], [3, 80, 1, 111, 0]], version=10)
    changed = change_log.service_changes(
        previous, current, per_year=100, names=names, teams=teams
    )
    check("rule change lists nobody", changed["thresholds"] == [] and changed["super_two"] == [])
    check("rule change counts up and down", changed["corrections"] == {"up": 1, "down": 1})
    sentence = page.correction_sentence(changed["corrections"])
    check("the sentence is one line", sentence.count("\n") == 0 and "Ada" not in sentence and "Bea" not in sentence)
    check("wording names both directions", sentence == "Service time corrections: 1 player up, 1 player down.")
    same = change_log.service_changes(
        _snap([[1, 10, 1, 147, 0]], version=10),
        _snap([[1, 10, 1, 147, 0]], version=10),
        per_year=100,
        names=names,
        teams=teams,
    )
    check("an unchanged day has no correction", same["corrections"] is None)


def test_empty_days_are_omitted_and_the_window_is_60_days() -> None:
    names = {1: "Ada Player"}
    teams = {1: "New York Yankees"}
    snap = _snap([[1, 10, 1, 147, 0]])
    today = dt.date(2026, 11, 17)
    quiet = change_log.build_log(
        snap, snap, {}, today=today, per_year=100, names=names, teams=teams, existing=[]
    )
    check("a day with no changes is not added", quiet == [])
    move = {"id": 1, "name": "Ada Player", "kind": "trade", "team": "New York Yankees to Boston Red Sox"}
    logged = change_log.build_log(
        snap, snap, {"2026-11-15": [move, dict(move)]},
        today=today, per_year=100, names=names, teams=teams, existing=[],
    )
    check("a move day is kept", len(logged) == 1 and logged[0]["date"] == "2026-11-15")
    check("the same move is not repeated", len(logged[0]["moves"]) == 1)
    again = change_log.build_log(
        snap, snap, {"2026-11-15": [move]},
        today=today, per_year=100, names=names, teams=teams, existing=logged,
    )
    check("a rerun does not duplicate the move", len(again) == 1 and len(again[0]["moves"]) == 1)
    old = {"date": "2026-01-01", "moves": [move]}
    recent = {"date": "2026-11-01", "moves": [move]}
    kept = change_log.trim([old, recent], today)
    check("the window keeps 60 days", [item["date"] for item in kept] == ["2026-11-01"])


def test_the_opening_entry_is_only_the_newest_day() -> None:
    today = dt.date(2026, 11, 17)
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cache = root / "data" / "cache" / "transactions"
        cache.mkdir(parents=True)
        (cache / "1.json").write_text(json.dumps([
            _row("Atlanta Braves signed free agent RHP Evan Phillips.", "2026-11-10", to_team_id=144),
            _row("San Francisco Giants traded RHP Ryan Walker to Milwaukee Brewers.", "2026-11-15", from_team_id=137, to_team_id=158),
        ]), encoding="utf-8")
        (cache / "2.json").write_text(json.dumps([
            _row("SS Brayan Rocchio assigned to Tiburones de La Guaira.", "2026-11-16", to_team_id=698),
        ]), encoding="utf-8")
        docs = root / "docs" / "p"
        docs.mkdir(parents=True)
        (docs / "1-ada-player.html").write_text("x", encoding="utf-8")
        db = {"1": _player(1, 40), "2": _player(2, 80)}
        db["1"]["name"] = "Ada Player"
        before = db["1"]["service_days_total"]
        log = change_log.publish(db, today=today, root=root, cache_dir=cache, per_year=100)
        check("service time on the player is unchanged", db["1"]["service_days_total"] == before)
        check("the opening log has one day", [day["date"] for day in log["days"]] == ["2026-11-15"])
        check("the older signing is not in the opening entry", "Evan" not in json.dumps(log))
        check("the transaction sentence is not stored", "traded RHP" not in json.dumps(log))
        raw = (root / "data" / "changes_snapshot.json").read_text(encoding="utf-8")
        check("the snapshot has no names or seasons", "Ada" not in raw and "seasons" not in raw and "description" not in raw)
        check("the snapshot is one compact line", raw.count("\n") == 1 and len(raw) < 5000)
        html_out = (root / "docs" / "changes.html").read_text(encoding="utf-8")
        check("the player page is linked", 'href="/p/1-ada-player.html"' in html_out)
        check("the trade group is on the page", "Trades" in html_out and "San Francisco Giants to Milwaukee Brewers" in html_out)

        # Same day, a new move and a year crossing. The opening trade stays.
        db["1"]["service_days_total"] = 100
        cached = json.loads((cache / "1.json").read_text(encoding="utf-8"))
        cached.append(_row("New York Yankees recalled SS Ada Player from Scranton.", "2026-11-17", to_team_id=147))
        (cache / "1.json").write_text(json.dumps(cached), encoding="utf-8")
        again = change_log.publish(db, today=today, root=root, cache_dir=cache, per_year=100)
        by_date = {day["date"]: day for day in again["days"]}
        check("the opening day is still there", "2026-11-15" in by_date)
        check("today is added for the new move and the crossing", "2026-11-17" in by_date)
        check("today lists the call-up once", len(by_date["2026-11-17"]["moves"]) == 1)
        check("today lists the year", by_date["2026-11-17"]["thresholds"][0]["years"] == 1)
        check("the trade was not copied onto today", by_date["2026-11-17"]["moves"][0]["kind"] == "call-up")


def test_a_failed_diff_does_not_raise() -> None:
    ok = change_log.publish_safely(None)  # type: ignore[arg-type]
    check("a bad database returns false", ok is False)
    source = (ROOT / "scripts" / "update_service_time.py").read_text(encoding="utf-8")
    check("the daily publish calls the safe wrapper", "change_log.publish_safely(db)" in source)
    body = source.split("def _write_outputs")[1].split("def _write_accrual_model")[0]
    check("the wrapper is inside the publish, after the database write", "publish_safely" in body)


def test_the_page_groups_a_day_and_links_players() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        docs = pathlib.Path(tmp)
        (docs / "p").mkdir()
        (docs / "p" / "5-ada-player.html").write_text("x", encoding="utf-8")
        log = {"days": [
            {
                "date": "2026-11-17",
                "thresholds": [{"id": 5, "name": "Ada Player", "team": "New York Yankees", "years": 1}],
                "super_two": [{"id": 6, "name": "Bea Player", "team": "New York Mets"}],
                "moves": [
                    {"id": 5, "name": "Ada Player", "team": "New York Yankees", "kind": "call-up"},
                    {"id": 7, "name": "Cy Player", "team": "Boston Red Sox", "kind": "option"},
                    {"id": 8, "name": "Dee Player", "team": "San Francisco Giants to Milwaukee Brewers", "kind": "trade"},
                ],
                "corrections": {"up": 2, "down": 1},
            },
            {
                "date": "2026-11-15",
                "moves": [{"id": 9, "name": "Eve Player", "team": "Chicago Cubs", "kind": "release"}],
            },
        ]}
        html_out = page.render(log, docs)
        check("builder checks pass", not page.problems(html_out, docs, log))
        check("one lede", html_out.count('<p class="lede">') == 1 and page.LEDE in html_out)
        check("newest day first", html_out.index("November 17, 2026") < html_out.index("November 15, 2026"))
        order = [
            "<h3>Reached 1 year</h3>",
            "<h3>Super Two range</h3>",
            "<h3>Call-ups</h3>",
            "<h3>Options</h3>",
            "<h3>Trades</h3>",
            "<p>Service time corrections: 2 players up, 1 player down.</p>",
        ]
        positions = [html_out.index(label) for label in order]
        check("groups follow the type order", positions == sorted(positions))
        check("a published player is linked", 'href="/p/5-ada-player.html">Ada Player</a>' in html_out)
        check("a player with no page is plain text", ">Bea Player</a>" not in html_out and "Bea Player" in html_out)
        check("no last-updated line", "last updated" not in html_out.lower())
        title = page_titles.changes_title()
        description = page_titles.changes_meta()
        check("title is the search title", title in html_out and len(title) <= page_titles.TITLE_MAX)
        check("description fits and says public data", description in html_out and "public data" in description.lower() and len(description) <= page_titles.META_MAX)
        check("description names the page", "MLB service time and roster changes today" in description)


def test_the_page_is_wired_into_the_site() -> None:
    core = (ROOT / "docs" / "sitemap-core.xml").read_text(encoding="utf-8")
    check("sitemap lists the page", "/changes.html</loc>" in core)
    check(
        "the page is a hand-listed sitemap page",
        "changes.html" in [path for path, _prio in wpp.HAND_WRITTEN_PAGES],
    )
    home = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    check("the homepage links the page", 'href="/changes.html"' in home)
    for name in ("update-service-time.yml", "backfill-history.yml"):
        body = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        check(f"{name} commits the page", "docs/changes.html" in body)
        check(f"{name} commits the log", "data/changes.json" in body)
        check(f"{name} commits the snapshot", "data/changes_snapshot.json" in body)
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    check("ci runs the page check", "scripts/build_changes_page.py --check" in ci)
    check("ci names this suite", "tests/test_changes.py" in ci)

    with tempfile.TemporaryDirectory() as tmp:
        docs = pathlib.Path(tmp)
        (docs / "changes.html").write_text("x", encoding="utf-8")
        (docs / "free-agents.html").write_text("x", encoding="utf-8")
        rendered = hub.render(docs)
        check("the hub links the page when it exists", 'href="/changes.html"' in rendered)
    missing = hub.render(ROOT / "docs" / "p")
    check("the hub omits the page when it is not on disk", "changes.html" not in missing)

    disk = (ROOT / "docs" / hub.PATH).read_text(encoding="utf-8")
    check("the published hub matches the builder", disk == hub.render())
    check("the published hub links the page", 'href="/changes.html"' in disk)

    taxes = (ROOT / "docs" / "taxes.html").read_bytes()
    check(
        "taxes.html is unchanged",
        hashlib.sha256(taxes).hexdigest() == "ef78b2f8160046145ce58382a0da8a55dc5ab3b98130fab271db847e1a45fe62",
    )

    snapshot = ROOT / "data" / "changes_snapshot.json"
    log_path = ROOT / "data" / "changes.json"
    html_path = ROOT / "docs" / "changes.html"
    check("snapshot, log, and page are on disk", snapshot.is_file() and log_path.is_file() and html_path.is_file())
    if snapshot.is_file():
        raw = snapshot.read_bytes()
        check("committed snapshot stays compact", len(raw) < 400_000)
        text = raw.decode("utf-8")
        check("committed snapshot stores no prose", "description" not in text and "seasons" not in text and '"name"' not in text)
    if log_path.is_file() and html_path.is_file():
        log = json.loads(log_path.read_text(encoding="utf-8"))
        check("the log has a seeded day", bool(log.get("days")))
        rendered = page.render(log)
        check("the published page matches the builder", html_path.read_text(encoding="utf-8") == rendered)
        check("the published page passes its checks", not page.problems(rendered, ROOT / "docs", log))
        lowered = rendered.lower()
        for banned in ("baseball-reference", "fangraphs", "spotrac", "cot's", "mlb trade rumors", "last updated"):
            check(f"published page has no {banned}", banned not in lowered)


def main() -> None:
    test_roster_moves_use_the_listed_kinds()
    test_thresholds_follow_the_ruleset_length_and_super_two_is_separate()
    test_a_rules_change_is_one_count_not_a_player_list()
    test_empty_days_are_omitted_and_the_window_is_60_days()
    test_the_opening_entry_is_only_the_newest_day()
    test_a_failed_diff_does_not_raise()
    test_the_page_groups_a_day_and_links_players()
    test_the_page_is_wired_into_the_site()
    print(f"{PASS} passed, {FAIL} failed")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
