"""The homepage must not fall back to the full player database.

home.json is the roster the first screen paints from. index.json is the rest
of the table, loaded after that. The database file is tens of megabytes; a
fallback to it, or a sample roster baked into the script, would put that
cost back on a visitor whose roster file failed to load.

    python3 tests/test_homepage_load.py
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import home_index  # noqa: E402

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


def test_the_homepage_does_not_fetch_the_full_database():
    app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    for name, text in (("app.js", app), ("index.html", html)):
        check(f"{name} does not name the full database", "service_time.json" not in text)
        check(f"{name} has no sample roster", "Sample Player" not in text)
        check(f"{name} does not bypass the HTTP cache", "no-store" not in text)
    check("app.js is deferred", 'defer src="app.js"' in html)
    check(
        "the first file is preloaded",
        'rel="preload" href="data/home.json"' in html,
    )
    check("app.js fetches the roster file", 'const HOME_URL = "data/home.json"' in app)
    check("a failed load can be tried again", "Try again" in app)
    for key in ("tracked", "rostered", "previous", "yet_to_debut", "incomplete"):
        check(f"the census reads {key} off the roster file", f"census.{key}" in app)


def test_home_json_is_the_roster_slice():
    index = json.loads((ROOT / "docs" / "data" / "index.json").read_text(encoding="utf-8"))
    home_path = home_index.HOME_FILE
    check("home.json exists", home_path.is_file())
    if not home_path.is_file():
        return
    home = json.loads(home_path.read_text(encoding="utf-8"))
    problems = home_index.agreement_problems(index, home)
    check("home.json matches the rostered rows and census of index.json", not problems)
    for problem in problems:
        print(f"       {problem}")
    check(
        "home.json is less than half the index",
        home_path.stat().st_size * 2 < (ROOT / "docs" / "data" / "index.json").stat().st_size,
    )


if __name__ == "__main__":
    test_the_homepage_does_not_fetch_the_full_database()
    test_home_json_is_the_roster_slice()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
