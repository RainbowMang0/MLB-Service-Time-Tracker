"""
Who the daily update asks the feed about. No network: the client is replaced.

The service-time math is not under test here. These checks are about which
requests a morning run makes.
"""

import datetime as dt
import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import update_service_time as u  # noqa: E402

PASS = 0
FAIL = 0

SEASONS = {
    2026: {
        "springStartDate": "2026-02-20",
        "regularSeasonEndDate": "2026-09-27",
        "postSeasonEndDate": "2026-10-28",
    },
    2027: {
        "springStartDate": "2027-02-19",
        "postSeasonEndDate": "2027-10-31",
    },
}


def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def test_offseason_is_after_the_postseason_and_before_spring():
    check("the last postseason day is still in season",
          not u.offseason_on(dt.date(2026, 10, 28), SEASONS))
    check("the next day is the offseason",
          u.offseason_on(dt.date(2026, 10, 29), SEASONS))
    check("midwinter is the offseason",
          u.offseason_on(dt.date(2027, 1, 15), SEASONS))
    check("the day before spring training is the offseason",
          u.offseason_on(dt.date(2027, 2, 18), SEASONS))
    check("spring training is not the offseason",
          not u.offseason_on(dt.date(2027, 2, 19), SEASONS))
    check("a July date is not the offseason",
          not u.offseason_on(dt.date(2026, 7, 4), SEASONS))
    check("a missing calendar does not claim an offseason",
          not u.offseason_on(dt.date(2026, 11, 10), {}))


def test_league_window_is_one_call_and_names_who_moved():
    calls = []

    def fake_league(start, end):
        calls.append((start, end))
        return [
            {"person": {"id": 10}, "date": "2026-11-10", "typeCode": "SGN",
             "description": "Signed.", "id": 1, "toTeam": {"id": 145}},
            {"person": {"id": 10}, "date": "2026-11-09", "typeCode": "OPT",
             "description": "Optioned.", "id": 2, "toTeam": {"id": 145}},
            {"date": "2026-11-10", "typeCode": "TR", "description": "Trade.", "id": 3},
            {"person": {"id": "nope"}},
        ]

    original = u.mlb.get_league_transactions
    u.mlb.get_league_transactions = fake_league
    try:
        today = dt.date(2026, 11, 10)
        moved = u.players_who_moved(today, days=3)
        check("the window is a single request", len(calls) == 1)
        check("the request covers the lookback through today",
              calls[0] == (dt.date(2026, 11, 8), today))
        check("a player with two rows is counted once", moved == {10})
    finally:
        u.mlb.get_league_transactions = original


def test_a_failed_window_does_not_mean_nobody_moved():
    def boom(start, end):
        raise RuntimeError("down")

    original = u.mlb.get_league_transactions
    u.mlb.get_league_transactions = boom
    try:
        check("a failed window asks for every player",
              u.players_who_moved(dt.date(2026, 11, 10)) is None)
    finally:
        u.mlb.get_league_transactions = original


def test_per_player_calls_are_only_movers_and_new_players():
    store = pathlib.Path(tempfile.mkdtemp(prefix="txn-cache-"))
    original = u.CACHE_DIR
    u.CACHE_DIR = store
    try:
        good = [{
            "date": "2024-04-01",
            "description": "Club selected the contract of X.",
            "team": "Chicago White Sox",
            "team_id": 145,
            "from_team_id": None,
            "to_team_id": 145,
        }]
        (store / "10.json").write_text(json.dumps(good), encoding="utf-8")
        legacy = [{"date": "2024-04-01", "description": "Signed.", "team": "X"}]
        (store / "30.json").write_text(json.dumps(legacy), encoding="utf-8")

        check("a quiet player with a good cache is not fetched",
              not u.needs_player_transaction_fetch(10, full_refresh=False, moved={20}))
        check("a player in the window is fetched",
              u.needs_player_transaction_fetch(20, full_refresh=False, moved={20}))
        check("a player with no cache is fetched",
              u.needs_player_transaction_fetch(40, full_refresh=False, moved=set()))
        check("a cache missing team ids is fetched",
              u.needs_player_transaction_fetch(30, full_refresh=False, moved=set()))
        check("a full refresh fetches everyone",
              u.needs_player_transaction_fetch(10, full_refresh=True, moved=set()))
        check("a failed window fetches everyone",
              u.needs_player_transaction_fetch(10, full_refresh=False, moved=None))

        skipped = u._fetch_transactions_incremental(10, False, network=False)
        check("skipping the network returns the cached rows",
              skipped == good and skipped[0]["to_team_id"] == 145)
    finally:
        u.CACHE_DIR = original
        shutil.rmtree(store)


def test_offseason_reuses_stored_career_stats():
    store = pathlib.Path(tempfile.mkdtemp(prefix="txn-cache-"))
    original_cache = u.CACHE_DIR
    original_ids = u._MLB_TEAM_IDS
    bios = []
    u.CACHE_DIR = store
    u._MLB_TEAM_IDS = {145}

    def boom_bio(player_id, with_season_teams=True):
        bios.append(player_id)
        raise AssertionError("career stats were pulled")

    originals = {
        "bio": u.mlb.get_player_bio,
        "window": u.mlb.get_season_window,
        "uniform": u.mlb.get_uniform_season_start,
        "start": u.mlb.get_club_season_start,
        "end": u.mlb.get_club_season_end,
        "appearances": u.mlb.appearance_dates,
    }
    u.mlb.get_player_bio = boom_bio
    u.mlb.get_season_window = lambda year: (dt.date(year, 3, 28), dt.date(year, 10, 1))
    u.mlb.get_uniform_season_start = lambda year: dt.date(year, 3, 28)
    u.mlb.get_club_season_start = lambda year, club: dt.date(year, 3, 28)
    u.mlb.get_club_season_end = lambda year, club: dt.date(year, 10, 1)
    u.mlb.appearance_dates = lambda *args, **kwargs: []
    try:
        cached = [{
            "date": "2024-04-01",
            "description": "Chicago White Sox selected the contract of X.",
            "team": "Chicago White Sox",
            "team_id": 145,
            "from_team_id": None,
            "to_team_id": 145,
        }]
        (store / "10.json").write_text(json.dumps(cached), encoding="utf-8")
        prior = {
            "name": "Test Player",
            "mlb_debut": "2024-04-01",
            "last_played": "2024-09-01",
            "birth_date": "2000-01-01",
            "season_stats": {"2024": {"hitting": {"gamesPlayed": 10, "team_id": 145}}},
            "seasons": [{"y": 2024, "d": 100, "t": [145], "src": "read"}],
        }
        record = u.build_player_record(
            {"id": 10, "fullName": "Test Player", "team": "Chicago White Sox", "teamId": 145},
            full_refresh=False,
            fetch_transactions=False,
            refresh_career_stats=False,
            prior=prior,
            horizon_end=dt.date(2024, 10, 1),
        )
        check("stored career stats are not re-pulled", bios == [])
        check("the stored debut is kept", record["mlb_debut"] == "2024-04-01")
        check("the stored stat line is kept",
              record["season_stats"]["2024"]["hitting"]["gamesPlayed"] == 10)
        check("a service-time figure is still produced",
              isinstance(record["service_days_total"], int))

        check("a player with no stored stats still needs a pull",
              not u.prior_has_career_stats(None))
        check("an empty stored stat map counts as already collected",
              u.prior_has_career_stats({"season_stats": {}}))
    finally:
        u.CACHE_DIR = original_cache
        u._MLB_TEAM_IDS = original_ids
        u.mlb.get_player_bio = originals["bio"]
        u.mlb.get_season_window = originals["window"]
        u.mlb.get_uniform_season_start = originals["uniform"]
        u.mlb.get_club_season_start = originals["start"]
        u.mlb.get_club_season_end = originals["end"]
        u.mlb.appearance_dates = originals["appearances"]
        shutil.rmtree(store)


if __name__ == "__main__":
    test_offseason_is_after_the_postseason_and_before_spring()
    test_league_window_is_one_call_and_names_who_moved()
    test_a_failed_window_does_not_mean_nobody_moved()
    test_per_player_calls_are_only_movers_and_new_players()
    test_offseason_reuses_stored_career_stats()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
