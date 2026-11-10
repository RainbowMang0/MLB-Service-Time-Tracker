"""
Tests for the raw transaction store. No pytest, and no network: the feed
client is replaced, or the HTTP session is replaced, before any call.
"""

import datetime as dt
import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import fetch_mlb_data as mlb  # noqa: E402
import raw_transactions as raw  # noqa: E402

PASS = 0
FAIL = 0
TODAY = dt.date(2026, 5, 2)
NOW = dt.datetime(2026, 5, 2, 12, 0, tzinfo=dt.timezone.utc)


def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def raw_row(**overrides):
    row = {
        "id": 501,
        "person": {"id": 10, "fullName": "Test Player"},
        "toTeam": {"id": 145, "name": "Chicago White Sox"},
        "date": "2024-05-01",
        "effectiveDate": "2024-05-01",
        "resolutionDate": "2024-05-01",
        "typeCode": "SGN",
        "typeDesc": "Signed",
        "description": "Chicago White Sox signed Test Player.",
    }
    row.update(overrides)
    return row


def workdir():
    return pathlib.Path(tempfile.mkdtemp(prefix="raw-txns-"))


def test_checksum_ignores_order_and_changes_when_a_row_changes():
    first = raw_row()
    reordered = {key: first[key] for key in reversed(list(first))}
    later = raw_row(id=502, date="2024-06-01", description="Chicago White Sox optioned Test Player.")
    check(
        "key order does not change the checksum",
        raw.checksum_of([first]) == raw.checksum_of([reordered]),
    )
    check(
        "row order does not change the checksum",
        raw.checksum_of([first, later]) == raw.checksum_of([later, first]),
    )
    changed = raw_row(description="Chicago White Sox signed Test Player to a minor league contract.")
    check(
        "a corrected description changes the checksum",
        raw.checksum_of([first]) != raw.checksum_of([changed]),
    )
    moved = raw_row(toTeam={"id": 112, "name": "Chicago Cubs"})
    check(
        "a corrected team id changes the checksum",
        raw.checksum_of([first]) != raw.checksum_of([moved]),
    )
    check("the checksum names its algorithm", raw.checksum_of([first]).startswith("sha256:"))


def test_replace_stores_the_raw_rows_and_drops_what_the_new_payload_omits():
    store = workdir()
    try:
        older = raw_row(id=1, date="2020-01-01", description="Old sentence that the feed later corrected.")
        stale = raw_row(id=2, date="2024-05-01", description="Signed.", toTeam={"id": 145, "name": "Chicago White Sox"})
        raw.replace_player(store, 10, [older, stale], fetched_at="2026-01-01T00:00:00Z")

        corrected = raw_row(
            id=2,
            date="2024-05-01",
            description="Chicago White Sox signed Test Player.",
            toTeam={"id": 112, "name": "Chicago Cubs"},
            typeCode="SFA",
        )
        # Same date as the stale row, different description and team. A
        # date+description merge would have kept the old sentence beside
        # this one, or kept the old team id when the sentence matched.
        raw.replace_player(store, 10, [corrected], fetched_at="2026-05-02T12:00:00Z")

        doc = raw.load_player(store, 10)
        rows = doc["transactions"]
        check("the file records the player id", doc["player_id"] == 10)
        check("the file records when it was written", doc["fetched_at"] == "2026-05-02T12:00:00Z")
        check("the stored checksum matches the rows", doc["checksum"] == raw.checksum_of(rows))
        check("replace keeps only the new payload", len(rows) == 1)
        check("the type code is the one the feed returned", rows[0]["typeCode"] == "SFA")
        check("the description is the corrected one", rows[0]["description"] == corrected["description"])
        check("the team id is the corrected one", rows[0]["toTeam"]["id"] == 112)
        check("the old sentence is gone", all("Old sentence" not in r["description"] for r in rows))
        check("a row from a day the new payload omits is gone", all(r["date"] != "2020-01-01" for r in rows))
        check("reloading the file still matches the checksum", raw.integrity_problem(doc, 10) is None)
    finally:
        shutil.rmtree(store)


def test_incomplete_rows_are_detected():
    healthy = raw_row()
    check("a full feed row is complete", not raw.row_is_incomplete(healthy))
    check("a fromTeam id is enough", not raw.row_is_incomplete(
        raw_row(toTeam=None, fromTeam={"id": 145, "name": "Chicago White Sox"}, team=None)
    ))
    no_team = raw_row()
    no_team.pop("toTeam")
    check("a row with no team id is incomplete", raw.row_is_incomplete(no_team))
    check("a row with a team name but no id is incomplete", raw.row_is_incomplete(
        raw_row(toTeam={"name": "Chicago White Sox"})
    ))
    check("a row missing its type code is incomplete", raw.row_is_incomplete(raw_row(typeCode="")))
    check("a row missing its date is incomplete", raw.row_is_incomplete(raw_row(date="")))
    check("a row missing its description is incomplete", raw.row_is_incomplete(raw_row(description="  ")))
    check("a row missing its transaction id is incomplete", raw.row_is_incomplete(raw_row(id=None)))
    check("a non-row is incomplete", raw.row_is_incomplete("signed"))


def test_an_incomplete_file_is_refetched_and_a_good_file_is_not():
    store = workdir()
    calls = []

    def fake_player(player_id, start, end):
        calls.append((player_id, start, end))
        return [raw_row(person={"id": player_id, "fullName": "Test Player"})]

    original = raw.fetch_player_transactions
    raw.fetch_player_transactions = fake_player
    try:
        broken = raw_row()
        broken.pop("toTeam")
        raw.replace_player(store, 10, [broken], fetched_at="2026-05-01T00:00:00Z")
        raw.replace_player(
            store, 11, [raw_row(id=11, person={"id": 11})], fetched_at="2026-05-01T00:00:00Z"
        )
        # Tamper after a valid write so the checksum no longer matches.
        tampered = raw.load_player(store, 11)
        tampered["transactions"][0]["description"] = "rewritten on disk"
        raw.player_path(store, 11).write_text(json.dumps(tampered), encoding="utf-8")

        summary = raw.run_full(store, [10, 11, 12], today=TODAY, now=NOW)
        fetched_ids = [pid for pid, _start, _end in calls]
        check("the incomplete file was fetched", 10 in fetched_ids)
        check("the checksum mismatch was fetched", 11 in fetched_ids)
        check("the missing player was fetched", 12 in fetched_ids)
        check("the fetch asks for the whole history", calls[0][1] == raw.EARLIEST and calls[0][2] == TODAY)
        doc = raw.load_player(store, 10)
        check("the refetched row has its team id", doc["transactions"][0]["toTeam"]["id"] == 145)
        check("the refetched file is no longer incomplete", not raw.has_incomplete_row(doc))
        check("three players are stored", summary["stored"] == 3 and summary["remaining"] == 0)

        calls.clear()
        again = raw.run_full(store, [10, 11, 12], today=TODAY, now=NOW)
        check("a second fill does not fetch anyone again", calls == [] and again["fetch_attempts"] == 0)
    finally:
        raw.fetch_player_transactions = original
        shutil.rmtree(store)


def test_a_failed_fetch_does_not_replace_a_good_file():
    store = workdir()
    original_row = raw_row()
    raw.replace_player(store, 10, [original_row], fetched_at="2026-05-01T00:00:00Z")
    # Force another fetch by breaking the checksum.
    doc = raw.load_player(store, 10)
    doc["checksum"] = "sha256:" + "0" * 64
    raw.player_path(store, 10).write_text(json.dumps(doc), encoding="utf-8")

    def boom(player_id, start, end):
        raise RuntimeError("feed down")

    original = raw.fetch_player_transactions
    raw.fetch_player_transactions = boom
    try:
        summary = raw.run_full(store, [10], today=TODAY, now=NOW, limit=5)
        kept = raw.load_player(store, 10)
        check("the failed fetch leaves the previous rows", kept["transactions"] == [original_row])
        check("the run reports the failure", summary["fetch_attempts"] == 1 and summary["fetch_failures"] == 1)
    finally:
        raw.fetch_player_transactions = original
        shutil.rmtree(store)


def test_window_replaces_the_date_range_and_full_fetches_gaps():
    store = workdir()
    calls = []
    league_calls = []

    def fake_player(player_id, start, end):
        calls.append(player_id)
        return [raw_row(
            id=9000 + player_id,
            person={"id": player_id, "fullName": "Test Player"},
            date="2020-04-01",
            description=f"Full history for {player_id}.",
        )]

    def fake_league(start, end):
        league_calls.append((start, end))
        # The daily window is one request covering the whole lookback.
        if not (start <= TODAY <= end):
            return []
        return [
            raw_row(
                id=2,
                person={"id": 10, "fullName": "Test Player"},
                date="2026-05-02",
                description="Corrected sentence for the recent day.",
                typeCode="OPT",
            ),
            raw_row(id=3, person={"id": 12, "fullName": "No File Yet"}, date="2026-05-02"),
            raw_row(id=4, person={"id": 99, "fullName": "Not Published"}, date="2026-05-02"),
            {"id": 5, "date": "2026-05-02", "typeCode": "ASG", "typeDesc": "Assigned"},
        ]

    original_player = raw.fetch_player_transactions
    original_league = raw.fetch_league_transactions
    raw.fetch_player_transactions = fake_player
    raw.fetch_league_transactions = fake_league
    try:
        early = raw_row(id=1, date="2020-01-01", description="Stays. This day is outside the window.")
        recent = raw_row(id=2, date="2026-05-02", description="This sentence was corrected later.")
        raw.replace_player(store, 10, [early, recent], fetched_at="2026-05-01T00:00:00Z")
        broken = raw_row(id=8, person={"id": 13, "fullName": "Broken"})
        broken.pop("toTeam")
        raw.replace_player(store, 13, [broken], fetched_at="2026-04-01T00:00:00Z")

        summary = raw.run_window(store, [10, 12, 13], today=TODAY, now=NOW, days=3)
        doc = raw.load_player(store, 10)
        descriptions = [row["description"] for row in doc["transactions"]]
        check("the older row is kept", early["description"] in descriptions)
        check("the corrected recent sentence replaces the old one",
              "Corrected sentence for the recent day." in descriptions)
        check("the stale recent sentence is not kept beside it",
              "This sentence was corrected later." not in descriptions)
        check("the window update keeps the type code",
              any(row.get("typeCode") == "OPT" for row in doc["transactions"]))
        check("a published player with no file is fetched whole", 12 in calls)
        check("that player's file is the full pull, not just the window day",
              raw.load_player(store, 12)["transactions"][0]["description"] == "Full history for 12.")
        check("a player who is not published gets no file", not raw.player_path(store, 99).exists())
        check("an incomplete file is refetched", 13 in calls)
        check("the refetched file has a team id",
              raw.load_player(store, 13)["transactions"][0]["toTeam"]["id"] == 145)
        check("the window stored the published players", summary["stored"] == 3)

        calls.clear()
        league_calls.clear()
        raw.run_window(store, [10, 12, 13], today=TODAY, now=NOW, days=3)
        check("the same day does not call the feed again", calls == [] and league_calls == [])
    finally:
        raw.fetch_player_transactions = original_player
        raw.fetch_league_transactions = original_league
        shutil.rmtree(store)


def test_refresh_replaces_every_player_and_resumes():
    store = workdir()
    calls = []

    def fake_player(player_id, start, end):
        calls.append(player_id)
        return [raw_row(
            id=player_id,
            person={"id": player_id, "fullName": "Test Player"},
            description=f"Replaced history {player_id}.",
        )]

    original = raw.fetch_player_transactions
    raw.fetch_player_transactions = fake_player
    try:
        raw.replace_player(
            store, 10, [raw_row(description="Old history that a re-pull must drop.")],
            fetched_at="2026-01-01T00:00:00Z",
        )
        first = raw.run_refresh(store, [10, 20], today=TODAY, now=NOW, limit=1)
        check("the first batch fetches one player", calls == [10])
        check("the re-pull replaced the old sentence",
              raw.load_player(store, 10)["transactions"][0]["description"] == "Replaced history 10.")
        check("the pass is still in progress", first["refresh_in_progress"] and first["refresh_remaining"] == 1)

        raw.run_refresh(store, [10, 20], today=TODAY, now=NOW, limit=5)
        check("the resume fetches only the player still left", calls == [10, 20])
        done = raw.run_refresh(store, [10, 20], today=TODAY, now=NOW, limit=5)
        check("a finished pass does not fetch again", calls == [10, 20] and done["refresh_remaining"] == 0)
    finally:
        raw.fetch_player_transactions = original
        shutil.rmtree(store)


def test_player_fetch_retries_then_returns_the_raw_rows():
    payload = {"transactions": [raw_row()]}
    attempts = {"n": 0}
    sleeps = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    def fake_get(url, params=None, timeout=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise mlb.requests.ConnectionError("temporary")
        fake_get.params = params
        fake_get.url = url
        return Response()

    original_get = mlb._session.get
    original_sleep = mlb.time.sleep
    previous_pause = mlb.os.environ.get("MLB_FETCH_PAUSE_SECONDS")
    mlb._session.get = fake_get
    mlb.time.sleep = lambda seconds: sleeps.append(seconds)
    mlb.os.environ["MLB_FETCH_PAUSE_SECONDS"] = "0"
    try:
        rows = raw.fetch_player_transactions(10, raw.EARLIEST, TODAY)
        check("a failed request is retried", attempts["n"] == 3)
        check("the backoff sleeps grow", sleeps[:2] == [mlb.RETRY_BACKOFF_SECONDS, mlb.RETRY_BACKOFF_SECONDS * 2])
        check("the rows are the feed's rows", rows == payload["transactions"])
        check("the request is the player transaction feed", fake_get.url.endswith("/transactions"))
        check("the request names the player and the dates",
              fake_get.params["playerId"] == 10
              and fake_get.params["startDate"] == raw.EARLIEST.isoformat()
              and fake_get.params["endDate"] == TODAY.isoformat())
    finally:
        mlb._session.get = original_get
        mlb.time.sleep = original_sleep
        if previous_pause is None:
            mlb.os.environ.pop("MLB_FETCH_PAUSE_SECONDS", None)
        else:
            mlb.os.environ["MLB_FETCH_PAUSE_SECONDS"] = previous_pause


def test_status_counts_a_fixture_database():
    root = workdir()
    try:
        players = root / "players.json"
        players.write_text(json.dumps({"players": [{"id": 10}, {"id": 20}]}), encoding="utf-8")
        store = root / "store"
        raw.replace_player(store, 10, [raw_row()], fetched_at="2026-05-02T12:00:00Z")
        code = raw.main([
            "--status",
            "--data-dir", str(store),
            "--players-file", str(players),
            "--today", TODAY.isoformat(),
        ])
        # main prints the summary. Recompute it here and compare the fields
        # the command is responsible for, without depending on stdout order
        # beyond the helper the command itself prints.
        summary = raw.summarize(store, [10, 20], today=TODAY)
        check("status exits cleanly", code == 0)
        check("one stored, one remaining", summary["stored"] == 1 and summary["remaining"] == 1)
        check("the store size counts the player file", summary["bytes"] == raw.player_path(store, 10).stat().st_size)
    finally:
        shutil.rmtree(root)


if __name__ == "__main__":
    test_checksum_ignores_order_and_changes_when_a_row_changes()
    test_replace_stores_the_raw_rows_and_drops_what_the_new_payload_omits()
    test_incomplete_rows_are_detected()
    test_an_incomplete_file_is_refetched_and_a_good_file_is_not()
    test_a_failed_fetch_does_not_replace_a_good_file()
    test_window_replaces_the_date_range_and_full_fetches_gaps()
    test_refresh_replaces_every_player_and_resumes()
    test_player_fetch_retries_then_returns_the_raw_rows()
    test_status_counts_a_fixture_database()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
