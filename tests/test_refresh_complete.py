"""
The daily refresh is skipped only when a complete run already finished today.

A rostered last_updated of today is not that signal. These tests cover the
marker the workflow reads, the decision to write it, and the rules-version
warning that must not fail validation.

Run directly: python3 tests/test_refresh_complete.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import refresh_complete  # noqa: E402
import update_service_time  # noqa: E402
import validate_published  # noqa: E402

PASS = 0
FAIL = 0
TODAY = "2026-11-09"


def check(label: str, condition: bool):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   - {label}")
    else:
        FAIL += 1
        print(f"  FAIL - {label}")


def finished(**overrides) -> dict:
    marker = refresh_complete.build_marker(
        completed_on=TODAY,
        rostered=1117,
        updated=1117,
        failures=0,
        rules_version=10,
    )
    marker.update(overrides)
    return marker


def test_a_finished_run_today_counts_as_done():
    check(
        "complete marker for today is done",
        refresh_complete.marker_is_complete(finished(), TODAY),
    )


def test_yesterday_is_not_done():
    check(
        "yesterday's finished run is not done today",
        not refresh_complete.marker_is_complete(
            finished(completed_on="2026-11-08"), TODAY
        ),
    )


def test_a_missing_marker_is_not_done():
    check("no marker is not done", not refresh_complete.marker_is_complete(None, TODAY))
    check(
        "a non-object marker is not done",
        not refresh_complete.marker_is_complete(["2026-11-09"], TODAY),
    )


def test_partial_and_failed_runs_are_not_done():
    check(
        "a failure does not count",
        not refresh_complete.marker_is_complete(finished(failures=1, updated=1116), TODAY),
    )
    check(
        "an updated count below the roster does not count",
        not refresh_complete.marker_is_complete(finished(updated=4), TODAY),
    )
    check(
        "a short roster does not count",
        not refresh_complete.marker_is_complete(
            finished(rostered=899, updated=899), TODAY
        ),
    )
    check(
        "the publish floor itself can count",
        refresh_complete.marker_is_complete(finished(rostered=900, updated=900), TODAY),
    )


def test_bad_counts_are_not_done():
    check(
        "string counts do not count",
        not refresh_complete.marker_is_complete(
            finished(rostered="1117", updated="1117", failures="0"), TODAY
        ),
    )
    check(
        "boolean counts do not count",
        not refresh_complete.marker_is_complete(
            finished(rostered=True, updated=True, failures=False), TODAY
        ),
    )
    check(
        "a missing count does not count",
        not refresh_complete.marker_is_complete(
            {k: v for k, v in finished().items() if k != "failures"}, TODAY
        ),
    )


def test_rules_version_on_the_marker_does_not_decide():
    # A finished refresh stays finished for that date. Version drift is a
    # separate warning, not a reason to pull the roster again today.
    check(
        "an older rules version on a finished marker is still done",
        refresh_complete.marker_is_complete(finished(rules_version=8), TODAY),
    )


def test_write_and_read_roundtrip():
    marker = finished()
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "refresh_complete.json"
        refresh_complete.write_marker(path, marker)
        check("written marker reads back", refresh_complete.read_marker(path) == marker)
        check("a missing file reads as no marker", refresh_complete.read_marker(pathlib.Path(tmp) / "absent.json") is None)
        check(
            "the file on disk is a complete marker",
            refresh_complete.marker_is_complete(json.loads(path.read_text()), TODAY),
        )


def test_cli_prints_yes_or_no(caps=None):
    import contextlib
    import io

    marker = finished()
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "refresh_complete.json"
        refresh_complete.write_marker(path, marker)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            refresh_complete.main(["--today", TODAY, "--marker", str(path)])
        check("cli prints yes for a finished day", out.getvalue().strip() == "yes")

        broken = pathlib.Path(tmp) / "broken.json"
        broken.write_text("{not json")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            refresh_complete.main(["--today", TODAY, "--marker", str(broken)])
        check("cli prints no for an unreadable marker", out.getvalue().strip() == "no")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            refresh_complete.main(
                ["--today", "2026-11-10", "--marker", str(path)]
            )
        check("cli prints no for a different date", out.getvalue().strip() == "no")


def test_the_marker_is_written_only_for_a_full_success():
    check(
        "a full clean roster is marked",
        refresh_complete.should_mark_complete(limit=None, failures=0, rostered=1117),
    )
    check(
        "a limited run is not marked",
        not refresh_complete.should_mark_complete(limit=5, failures=0, rostered=1117),
    )
    check(
        "limit 0 is still a limited run",
        not refresh_complete.should_mark_complete(limit=0, failures=0, rostered=1117),
    )
    check(
        "one failure is not marked",
        not refresh_complete.should_mark_complete(limit=None, failures=1, rostered=1117),
    )
    check(
        "a short roster is not marked",
        not refresh_complete.should_mark_complete(limit=None, failures=0, rostered=899),
    )
    check(
        "the publish floor matches the marker floor",
        refresh_complete.MIN_ROSTERED == update_service_time.MIN_EXPECTED_ROSTER,
    )


def test_marker_write_sits_after_publish_and_off_the_skip_paths():
    source = (ROOT / "scripts" / "update_service_time.py").read_text()
    main = source.split("def main(", 1)[1].split("\ndef check_run_is_sane(", 1)[0]
    derived = main.split("if args.recompute_derived:", 1)[1].split(
        "if not mlb_fetch_enabled", 1
    )[0]
    disabled = main.split("if not mlb_fetch_enabled()", 1)[1].split(
        "roster = mlb.get_all_40man_players()", 1
    )[0]
    check(
        "a derived recompute does not write the marker",
        "_mark_refresh_complete" not in derived,
    )
    check(
        "a disabled fetch does not write the marker",
        "_mark_refresh_complete" not in disabled,
    )
    check(
        "the marker is written after the publish returns",
        main.rfind("_write_outputs(db)") < main.rfind("_mark_refresh_complete("),
    )
    check(
        "a refused publish exits before any marker is written",
        main.find("sys.exit(1)") < main.find("_mark_refresh_complete("),
    )


def test_the_workflow_reads_the_marker_from_the_branch_tip():
    text = (ROOT / ".github" / "workflows" / "update-service-time.yml").read_text()
    gate = text.split("id: gate", 1)[1].split("- name: Set up Python", 1)[0]
    check("the gate asks the marker script", "scripts/refresh_complete.py" in gate)
    check("the gate fetches the branch tip", 'git fetch --depth=1 --quiet origin "$BRANCH"' in gate)
    check(
        "the gate shows the marker from the tip",
        'git show "origin/${BRANCH}:data/refresh_complete.json"' in gate,
    )
    check(
        "the gate no longer uses the newest rostered last_updated",
        "last_updated" not in gate and "max(" not in gate,
    )
    check(
        "the daily job commits the marker",
        "data/refresh_complete.json" in text,
    )
    backfill = (ROOT / ".github" / "workflows" / "backfill-history.yml").read_text()
    check(
        "the backfill job does not commit the marker",
        "refresh_complete.json" not in backfill,
    )


def test_rules_version_warning_reports_counts_and_does_not_fail():
    players = [
        {"id": 1, "rules_version": 10, "on_40_man": True},
        {"id": 2, "rules_version": 10, "on_40_man": False},
        {"id": 3, "rules_version": 8, "on_40_man": False},
        {"id": 4, "rules_version": 9, "on_40_man": True},
        {"id": 5, "on_40_man": False},
    ]
    report = validate_published.rules_version_report(players, 10)
    check("total is every published player", report["total"] == 5)
    check("two match the code", report["matched"] == 2)
    check("three do not, including the missing one", report["other"] == 3)
    check("version 8 counted", report["by_version"]["8"] == 1)
    check("version 9 counted", report["by_version"]["9"] == 1)
    check("version 10 counted", report["by_version"]["10"] == 2)
    check("missing counted", report["by_version"]["missing"] == 1)
    check("rostered split", report["rostered"] == 2 and report["rostered_matched"] == 1)
    check("one rostered player is behind", report["rostered_other"] == 1)

    text = validate_published.format_rules_version_report(report)
    check("mismatch is a warning", text.startswith("WARNING:"))
    check("warning names the counts", "2 of 5" in text and "3 do not" in text)
    check("warning says the run still passes", "does not fail the run" in text)
    check("warning lists each version", "version 8: 1" in text and "missing: 1" in text)
    check(
        "versions are listed oldest first, then missing",
        text.index("version 8:") < text.index("version 9:") < text.index("version 10:") < text.index("missing:"),
    )

    clean = validate_published.rules_version_report(
        [{"rules_version": 10, "on_40_man": True}], 10
    )
    clean_text = validate_published.format_rules_version_report(clean)
    check("a full match is not phrased as a warning", not clean_text.startswith("WARNING:"))
    check("a full match still reports the count", "1 of 1" in clean_text)


def test_utc_today_is_the_utc_date():
    import datetime as dt

    expected = dt.datetime.now(dt.timezone.utc).date().isoformat()
    check("utc today matches the UTC calendar date", refresh_complete.utc_today() == expected)


if __name__ == "__main__":
    test_a_finished_run_today_counts_as_done()
    test_yesterday_is_not_done()
    test_a_missing_marker_is_not_done()
    test_partial_and_failed_runs_are_not_done()
    test_bad_counts_are_not_done()
    test_rules_version_on_the_marker_does_not_decide()
    test_write_and_read_roundtrip()
    test_cli_prints_yes_or_no()
    test_the_marker_is_written_only_for_a_full_success()
    test_marker_write_sits_after_publish_and_off_the_skip_paths()
    test_the_workflow_reads_the_marker_from_the_branch_tip()
    test_rules_version_warning_reports_counts_and_does_not_fail()
    test_utc_today_is_the_utc_date()

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
