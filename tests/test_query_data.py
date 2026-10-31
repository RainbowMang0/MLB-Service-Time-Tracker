"""
Tests for scripts/query_data.py. No pytest dependency required -- run
directly with `python3 tests/test_query_data.py`.

All tests run against the small fixture database in tests/fixtures/
(service_time_fixture.json, super_two_reported_fixture.json,
salary_estimates_fixture.json) rather than the real, multi-megabyte
docs/data/service_time.json -- see that file's own module docstring for why
this tool exists in the first place.
"""

import contextlib
import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import query_data  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
DATA_FILE = FIXTURES / "service_time_fixture.json"
REPORTED_FILE = FIXTURES / "super_two_reported_fixture.json"
SALARY_FILE = FIXTURES / "salary_estimates_fixture.json"
MISSING_FILE = FIXTURES / "does_not_exist.json"

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


def run(args: list[str]) -> tuple[int, str, str]:
    """Runs query_data.main() in-process against the fixture data file by
    default, capturing stdout/stderr instead of spawning a subprocess."""
    full_args = ["--data-file", str(DATA_FILE)] + args
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = query_data.main(full_args)
    return code, out.getvalue(), err.getvalue()


def run_json(args: list[str]):
    code, out, err = run(args + ["--json"])
    assert code == 0, f"expected exit 0, got {code}: {err}"
    return json.loads(out)


def test_player_match_by_exact_id():
    code, out, _ = run(["--player", "900001", "--include-previous"])
    check("exit 0", code == 0)
    check("matched row shows the fixture player", "Fixture Alpha" in out)
    check("id column present", "900001" in out)


def test_player_match_by_name_substring_is_case_insensitive():
    rows = run_json(["--player", "rangeboy"])
    ids = {r["id"] for r in rows}
    check("substring match finds both Rangeboy players", ids == {900002, 900003})

    rows_upper = run_json(["--player", "RANGEBOY"])
    check("match is case-insensitive", {r["id"] for r in rows_upper} == ids)


def test_player_id_match_does_not_fall_back_to_substring():
    rows = run_json(["--player", "900099"])
    check("an id with no exact match returns nothing", rows == [])


def test_range_boundaries_are_inclusive():
    rows = run_json(["--range", "2.000-3.000"])
    ids = {r["id"] for r in rows}
    # 900002 (2.000) and 900003 (3.000) sit exactly on the boundaries and
    # must be included; 900001 (1.050) and 900004 (3.001) sit just outside
    # and must not be. 900005 is excluded because it is not on the 40-man
    # (the default roster filter), not because of the range.
    check(
        "both boundary players are included, neighbours excluded",
        ids == {900002, 900003, 900006, 900007},
    )


def test_range_invalid_format_is_a_clean_error():
    code, out, err = run(["--range", "not-a-range-at-all-x"])
    check("bad range exits nonzero", code != 0)
    check("bad range prints a helpful message", "years.days" in err)
    check("bad range prints nothing to stdout", out == "")


def test_range_low_after_high_is_a_clean_error():
    code, _out, err = run(["--range", "3.000-2.000"])
    check("inverted range exits nonzero", code != 0)
    check("inverted range names the problem", "after" in err)


def test_team_filter_by_name_substring():
    rows = run_json(["--team", "Fixture Rockies"])
    ids = {r["id"] for r in rows}
    check("team name substring matches only that club's current players", ids == {900001, 900004, 900006})


def test_team_filter_by_exact_id():
    rows = run_json(["--team", "9002"])
    ids = {r["id"] for r in rows}
    check(
        "team id matches only that club's current players",
        ids == {900002, 900003, 900007},
    )


def test_fields_selection():
    rows = run_json(["--player", "900001", "--include-previous", "--fields", "id,team"])
    check("exactly the requested fields come back", len(rows) == 1 and set(rows[0].keys()) == {"id", "team"})
    check("field values are correct", rows[0]["id"] == 900001 and rows[0]["team"] == "Fixture Rockies")


def test_unknown_field_is_a_clean_error_listing_available_fields():
    code, out, err = run(["--fields", "not_a_real_field"])
    check("unknown field exits nonzero", code != 0)
    check("error names the bad field", "not_a_real_field" in err)
    check("error lists real fields as a hint", "salary_estimate" in err and "service_time" in err)
    check("unknown field prints nothing to stdout", out == "")


def test_count_prints_only_a_number():
    code, out, _ = run(["--range", "2.000-3.000", "--count"])
    check("count exits 0", code == 0)
    check("count output is a single integer line", out.strip().isdigit() and int(out.strip()) == 4)


def test_count_is_unaffected_by_limit():
    code, out, _ = run(["--range", "2.000-3.000", "--count", "--limit", "1"])
    check("count ignores --limit", code == 0 and int(out.strip()) == 4)


def test_json_output_is_valid_json():
    rows = run_json(["--range", "2.000-3.000"])
    check("json output parses to a list", isinstance(rows, list))
    check("json rows carry typed values, not strings", all(isinstance(r["id"], int) for r in rows))


def test_combined_filters_are_anded():
    rows = run_json(["--range", "2.000-3.000", "--team", "Fixture Astros", "--player", "candidatetwo"])
    ids = {r["id"] for r in rows}
    check("range + team + player narrows to the one player matching all three", ids == {900007})

    rows_conflict = run_json(["--range", "2.000-3.000", "--team", "Fixture Rockies", "--player", "candidatetwo"])
    check("a combination matching no one returns nothing", rows_conflict == [])


def test_include_previous_default_excludes_non_rostered():
    rows_default = run_json(["--player", "previous"])
    check("non-rostered player is hidden by default", rows_default == [])

    rows_included = run_json(["--player", "previous", "--include-previous"])
    check("--include-previous surfaces the non-rostered player", {r["id"] for r in rows_included} == {900005})


def test_limit_caps_rows_in_both_output_modes():
    code, out, _ = run(["--range", "2.000-3.000", "--limit", "1"])
    lines = [l for l in out.splitlines() if l.strip()]
    data_lines = [l for l in lines if l[0].isdigit()]
    check("plain-text output respects --limit", len(data_lines) == 1)
    check("plain-text output notes how many more matched", "3 more match" in out)

    rows = run_json(["--range", "2.000-3.000", "--limit", "1"])
    check("json output respects --limit too", len(rows) == 1)


def test_limit_zero_means_unlimited():
    rows = run_json(["--range", "2.000-3.000", "--limit", "0"])
    check("--limit 0 returns every match", len(rows) == 4)


def test_super_two_with_a_reported_file():
    payload = run_json(["--super-two", "--reported-file", str(REPORTED_FILE)])
    cutoff, rng, players = payload["cutoff"], payload["range"], payload["players"]
    check("computed cutoff is echoed from the data file", cutoff["cutoff_days"] == 400)
    check("a usable reported file produces a real range", rng["is_range"] is True)
    check("range low is the computed cutoff", rng["low_days"] == 400)
    check("range high comes from the reported figure (2.080 -> 424 days)", rng["high_days"] == 424)
    check(
        "players in or near the cutoff are listed, others are not",
        {p["id"] for p in players} == {900006, 900007},
    )


def test_super_two_without_a_reported_file():
    payload = run_json(["--super-two", "--reported-file", str(MISSING_FILE)])
    rng = payload["range"]
    check("a missing reported file falls back to a single-valued range", rng["is_range"] is False)
    check("the fallback range still anchors on the computed cutoff", rng["low_days"] == rng["high_days"] == 400)

    code, out, _ = run(["--super-two", "--reported-file", str(MISSING_FILE)])
    check("plain-text output says no reported figure is on file", "No usable reported figure" in out)


def test_super_two_respects_team_filter_and_count():
    code, out, _ = run(
        ["--super-two", "--reported-file", str(REPORTED_FILE), "--team", "Fixture Astros", "--count"]
    )
    check("super-two --count is a single number", out.strip().isdigit())
    check("team filter narrows the super-two list", int(out.strip()) == 1)  # only 900007 is on the Astros


def test_salary_estimate_field_reads_the_salary_file():
    rows = run_json(
        [
            "--player", "900001", "--include-previous",
            "--fields", "id,salary_estimate", "--salary-file", str(SALARY_FILE),
        ]
    )
    check("a listed pre-arb id gets a minimum-salary estimate", "780,000" in rows[0]["salary_estimate"])

    ext_rows = run_json(
        [
            "--player", "900006",
            "--fields", "id,salary_estimate", "--salary-file", str(SALARY_FILE),
        ]
    )
    check("a player with an extension on file gets no dollar estimate", "extension" in ext_rows[0]["salary_estimate"])

    missing_rows = run_json(
        [
            "--player", "900007",
            "--fields", "id,salary_estimate", "--salary-file", str(SALARY_FILE),
        ]
    )
    check("a player in neither list gets a blank estimate", missing_rows[0]["salary_estimate"] == "")


def test_missing_data_file_is_a_clean_error():
    out_buf, err_buf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        code = query_data.main(["--data-file", str(MISSING_FILE)])
    check("missing data file exits nonzero", code != 0)
    check("missing data file names the path", str(MISSING_FILE) in err_buf.getvalue())


if __name__ == "__main__":
    test_player_match_by_exact_id()
    test_player_match_by_name_substring_is_case_insensitive()
    test_player_id_match_does_not_fall_back_to_substring()
    test_range_boundaries_are_inclusive()
    test_range_invalid_format_is_a_clean_error()
    test_range_low_after_high_is_a_clean_error()
    test_team_filter_by_name_substring()
    test_team_filter_by_exact_id()
    test_fields_selection()
    test_unknown_field_is_a_clean_error_listing_available_fields()
    test_count_prints_only_a_number()
    test_count_is_unaffected_by_limit()
    test_json_output_is_valid_json()
    test_combined_filters_are_anded()
    test_include_previous_default_excludes_non_rostered()
    test_limit_caps_rows_in_both_output_modes()
    test_limit_zero_means_unlimited()
    test_super_two_with_a_reported_file()
    test_super_two_without_a_reported_file()
    test_super_two_respects_team_filter_and_count()
    test_salary_estimate_field_reads_the_salary_file()
    test_missing_data_file_is_a_clean_error()

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
