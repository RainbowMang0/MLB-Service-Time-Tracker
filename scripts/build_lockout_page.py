#!/usr/bin/env python3
"""
build_lockout_page.py
----------------------
Reads data/lockout_updates.json and writes docs/lockout.html.

WHY THIS EXISTS
===============
The 2026 CBA expires December 1, 2026 and a lockout is widely expected the
next day. That is a live news story, and this project wants to carry it
without turning "update the lockout page" into a code change. So the page
is generated from one small, hand-edited data file
(data/lockout_updates.json) -- editing that file and pushing it to main is
the entire update. See that file's own _README for the fields.

This script is deliberately separate from write_player_pages.py: it does
not touch any player, club or alumni page. A bad edit to
data/lockout_updates.json is isolated to this one page by two things working
together, not one: CI runs `--check` on every push and PR so a bad edit is
normally rejected before it ever reaches main, AND the daily workflow runs
this script's step with continue-on-error, so even if a bad file slipped
through, the one commit step that follows still runs and ships everything
else the job computed -- only docs/lockout.html stays as it was (see
.github/workflows/update-service-time.yml). It reuses write_player_pages's
GA/AdSense/Cloudflare constants rather than re-declaring them, so the two
cannot drift apart.

WHAT IS VALIDATED
=================
Every field data/lockout_updates.json is required to have, every date is
checked for being real ISO *and* written as YYYY-MM-DD (not a week-date or a
compact form `fromisoformat` would also accept), lockout_start must be
strictly after cba_expires (a lockout can't start before the agreement it
would follow has expired), lockout_end may not precede lockout_start, every
entry needs a well-formed https source (lowercase scheme only, a real host,
no embedded whitespace, no zero-width or other invisible/control
characters), no entry may be dated more than a day past today in
US/Eastern, and all text is HTML-escaped at render time regardless of
what's already in the file -- the file is hand-edited by one person, but a
page that broke on a stray "<" would be a strange way to find that out.

TIMEZONE
========
The agreement expires at 11:59 p.m. US/Eastern on cba_expires, not at
midnight UTC and not at the visitor's or the build server's local midnight.
Every comparison here -- the countdown, the lockout day count, the
"expires tonight" state -- is anchored to the America/New_York calendar via
ZoneInfo, never dt.date.today(). docs/lockout.js carries the same rule for
the browser, keyed off an explicit `timeZone: "America/New_York"` so a
visitor in Tokyo or Los Angeles sees the same status a visitor in New York
does.

USAGE
=====
  python3 scripts/build_lockout_page.py             # validate and write docs/lockout.html
  python3 scripts/build_lockout_page.py --check      # validate only, write nothing
  python3 scripts/build_lockout_page.py --today 2026-12-10   # pin "today" (tests, previews)
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import pathlib
import re
import sys
import unicodedata
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import page_titles  # noqa: E402
import write_player_pages as wpp  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "lockout_updates.json"
OUT_FILE = ROOT / "docs" / "lockout.html"

# The salary cap & floor explainer is the lockout section's second page. It is
# generated from its own small, hand-edited data file by the same convention as
# the tracker: every dollar figure lives in the data file, never in this
# builder, so a change to the proposal during bargaining is a one-number edit.
SALARY_CAP_DATA_FILE = ROOT / "data" / "lockout_salary_cap.json"
SALARY_CAP_OUT_FILE = ROOT / "docs" / "lockout-salary-cap.html"

SITE_URL = wpp.SITE_URL
PAGE_URL = f"{SITE_URL}/lockout.html"
SALARY_CAP_PAGE_URL = f"{SITE_URL}/lockout-salary-cap.html"

# The two pages of the lockout section, as (href, label) pairs. The tab strip
# below lives inside the page content, not the shared site header -- it is
# local sub-navigation between this section's two pages, not a sitewide
# destination, so it does not belong in the header's own tab row or sheet.
LOCKOUT_TABS = (
    ("lockout.html", "Tracker"),
    ("lockout-salary-cap.html", "Salary cap & floor"),
)

ET = ZoneInfo("America/New_York")

REQUIRED_TOP_FIELDS = (
    "cba_expires", "lockout_start", "lockout_end",
    "regular_season_delayed_from", "updated", "entries",
)
REQUIRED_ENTRY_FIELDS = ("date", "headline", "summary", "source_name", "source_url")

# The topic filter above "Latest updates": a small, fixed set of buckets an
# entry can be hand-tagged into via the OPTIONAL entries[].category field
# (see data/lockout_updates.json's own _README). "other" is both a value an
# editor can pick on purpose and the fallback for a missing, blank, or
# misspelled category -- see _entry_category() -- so this field can never
# fail validation and an old entry written before this feature existed
# keeps rendering exactly as before, just filed under "Other".
ENTRY_CATEGORIES = (
    ("salary_cap", "Salary cap & floor"),
    ("negotiations", "Negotiations"),
    ("deadlines", "Deadlines & dates"),
    ("players_union", "Players & union"),
    ("owners_league", "Owners & league"),
    ("other", "Other"),
)
ENTRY_CATEGORY_LABELS = dict(ENTRY_CATEGORIES)
OTHER_CATEGORY = "other"


def _entry_category(entry: dict) -> str:
    """The entry's filter bucket -- never raises, never blank.

    Anything that isn't one of the known category values (missing, empty,
    wrong type, a typo) falls back to "other" rather than failing
    validation; see ENTRY_CATEGORIES above.
    """
    value = entry.get("category")
    if isinstance(value, str) and value in ENTRY_CATEGORY_LABELS:
        return value
    return OTHER_CATEGORY

_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

_MONTH_ABBR = ["Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July",
               "Aug.", "Sep.", "Oct.", "Nov.", "Dec."]
_MONTH_FULL = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


def format_date_abbrev(d: dt.date) -> str:
    """"Dec. 1, 2026" -- the short form used in tiles and inline sentences."""
    return f"{_MONTH_ABBR[d.month - 1]} {d.day}, {d.year}"


def format_date_full(d: dt.date) -> str:
    """"December 1, 2026" -- the long form used in prose paragraphs."""
    return f"{_MONTH_FULL[d.month - 1]} {d.day}, {d.year}"


class LockoutDataError(ValueError):
    """A problem with data/lockout_updates.json, meant to fail CI loudly."""


def now_et() -> dt.datetime:
    return dt.datetime.now(ET)


def _parse_iso_date(value: str, field: str) -> dt.date:
    if not isinstance(value, str) or not _ISO_DATE_RE.fullmatch(value):
        raise LockoutDataError(
            f"{field}: expected an ISO date string YYYY-MM-DD, got {value!r}"
        )
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise LockoutDataError(f"{field}: {value!r} is not a valid date") from exc


def _parse_optional_iso_date(value, field: str) -> dt.date | None:
    if value is None:
        return None
    return _parse_iso_date(value, field)


_INVISIBLE_CATEGORIES = ("Cf", "Cc")


def _check_https_url(url, field: str) -> None:
    if not isinstance(url, str):
        raise LockoutDataError(f"{field}: must be an https:// URL, got {url!r}")
    # Case-sensitive on purpose: "HTTPS://" or "Https://" is rejected outright
    # rather than normalized, so a scheme typo fails loudly instead of being
    # quietly accepted.
    if not url.startswith("https://"):
        raise LockoutDataError(f"{field}: must start with 'https://', got {url!r}")
    if any(ch.isspace() or unicodedata.category(ch) in _INVISIBLE_CATEGORIES for ch in url):
        raise LockoutDataError(
            f"{field}: must not contain whitespace or invisible/control characters, got {url!r}"
        )
    parts = urlsplit(url)
    if not parts.hostname:
        raise LockoutDataError(f"{field}: must be a well-formed https:// URL with a host, got {url!r}")


def validate(data: dict, today: dt.date | None = None) -> None:
    """Raises LockoutDataError on anything a bad hand-edit could introduce.

    Checked here rather than left to render-time KeyErrors so CI reports a
    clear message instead of a traceback, and so the same check protects
    both the daily build and the push-triggered one.
    """
    if today is None:
        today = now_et().date()

    if not isinstance(data, dict):
        raise LockoutDataError("top level must be a JSON object")

    missing = [f for f in REQUIRED_TOP_FIELDS if f not in data]
    if missing:
        raise LockoutDataError(f"missing required field(s): {', '.join(missing)}")

    cba_expires = _parse_iso_date(data["cba_expires"], "cba_expires")
    lockout_start = _parse_optional_iso_date(data["lockout_start"], "lockout_start")
    lockout_end = _parse_optional_iso_date(data["lockout_end"], "lockout_end")
    _parse_optional_iso_date(data["regular_season_delayed_from"], "regular_season_delayed_from")
    _parse_iso_date(data["updated"], "updated")

    if lockout_start is not None and lockout_start <= cba_expires:
        raise LockoutDataError(
            f"lockout_start ({lockout_start}) must be strictly after cba_expires "
            f"({cba_expires}) -- the agreement doesn't expire until 11:59 p.m. ET "
            "that day"
        )

    if lockout_start is not None and lockout_end is not None and lockout_end < lockout_start:
        raise LockoutDataError(
            f"lockout_end ({lockout_end}) is before lockout_start ({lockout_start})"
        )

    entries = data["entries"]
    if not isinstance(entries, list):
        raise LockoutDataError("entries must be a list")

    future_cutoff = today + dt.timedelta(days=1)
    for i, entry in enumerate(entries):
        label = f"entries[{i}]"
        if not isinstance(entry, dict):
            raise LockoutDataError(f"{label}: must be an object")
        missing = [f for f in REQUIRED_ENTRY_FIELDS if f not in entry or entry[f] in (None, "")]
        if missing:
            raise LockoutDataError(f"{label}: missing required field(s): {', '.join(missing)}")
        entry_date = _parse_iso_date(entry["date"], f"{label}.date")
        if entry_date > future_cutoff:
            raise LockoutDataError(f"{label}.date: {entry_date} is in the future")
        _check_https_url(entry["source_url"], f"{label}.source_url")
        for field in ("headline", "summary", "source_name"):
            if not isinstance(entry[field], str) or not entry[field].strip():
                raise LockoutDataError(f"{label}.{field}: must be a non-empty string")


def load(path: pathlib.Path = DATA_FILE, today: dt.date | None = None) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LockoutDataError(f"{path}: not found") from exc
    except json.JSONDecodeError as exc:
        raise LockoutDataError(f"{path}: invalid JSON ({exc})") from exc
    validate(data, today)
    return data


def _tab_strip(active: str) -> str:
    """The two-tab strip shared by both lockout pages.

    Plain links, no JavaScript. `active` is the href of the current page; the
    matching tab carries aria-current, and the CSS keys off that rather than a
    hand-set class so the visible state and the accessible state cannot
    disagree -- the same rule the main nav follows.
    """
    items = []
    for href, label in LOCKOUT_TABS:
        mark = ' aria-current="page"' if href == active else ""
        items.append(f'<a href="{href}"{mark}>{html.escape(label)}</a>')
    return (
        '<nav class="lockout-tabs" aria-label="CBA and lockout sections">'
        + "".join(items)
        + "</nav>"
    )


def _filter_bar() -> str:
    """The topic filter above "Latest updates".

    Rendered `hidden` by construction: a visitor with JavaScript off (or a
    crawler) never sees it at all, so there is no dead, non-functional
    control -- every entry just stays visible, unfiltered, in its normal
    place. The inline script below this bar's call site removes `hidden`
    once it has wired click handling, and is the only thing that ever does.
    Native <button> elements carry keyboard activation (Enter/Space) and
    focus for free; aria-pressed is the one bit of state javascript has to
    maintain by hand.
    """
    buttons = ['<button type="button" class="lockout-filter-btn" '
               'data-filter="all" aria-pressed="true">All</button>']
    for value, label in ENTRY_CATEGORIES:
        buttons.append(
            f'<button type="button" class="lockout-filter-btn" '
            f'data-filter="{value}" aria-pressed="false">{html.escape(label)}</button>'
        )
    return (
        '<div class="lockout-filter" id="lockout-filter" role="group" '
        'aria-label="Filter updates by topic" hidden>'
        + "".join(buttons)
        + "</div>"
        '<p class="panel-note lockout-filter-empty" id="lockout-filter-empty" hidden>'
        "No updates for this topic yet.</p>"
    )


_FILTER_SCRIPT = """
      <script>
        (function () {
          try {
            var bar = document.getElementById("lockout-filter");
            var empty = document.getElementById("lockout-filter-empty");
            var list = document.querySelector(".lockout-entries");
            if (!bar || !list) return;
            var items = Array.prototype.slice.call(list.querySelectorAll(".lockout-entry"));
            var buttons = Array.prototype.slice.call(bar.querySelectorAll(".lockout-filter-btn"));
            function applyFilter(value) {
              var visible = 0;
              items.forEach(function (li) {
                var show = LockoutStatus.matchesFilter(li.getAttribute("data-category"), value);
                li.hidden = !show;
                if (show) visible++;
              });
              if (empty) empty.hidden = visible !== 0;
            }
            buttons.forEach(function (btn) {
              btn.addEventListener("click", function () {
                buttons.forEach(function (b) {
                  b.setAttribute("aria-pressed", b === btn ? "true" : "false");
                });
                applyFilter(btn.getAttribute("data-filter"));
              });
            });
            bar.hidden = false;
          } catch (e) { /* static build stays as all-entries-visible */ }
        })();
      </script>"""


def sorted_entries(data: dict) -> list[dict]:
    """Newest first, regardless of the order they were hand-typed in."""
    return sorted(data["entries"], key=lambda e: e["date"], reverse=True)


# ---------------------------------------------------------------------------
# The salary cap & floor explainer: its own data file, validated the same way
# the updates file is, and rendered below. Every figure a reader sees comes
# from data/lockout_salary_cap.json; none is typed into this builder.
# ---------------------------------------------------------------------------

REQUIRED_CAP_TOP_FIELDS = (
    "as_of", "updated", "meta_description", "summary",
    "figures", "scale", "sections", "faq", "sources",
)


def validate_salary_cap(data: dict) -> None:
    """Raises LockoutDataError on anything a bad hand-edit could introduce.

    Checked here, not left to render-time KeyErrors, so CI reports a clear
    message and the same gate protects the daily build and the push-triggered
    one -- exactly as validate() does for the updates file.
    """
    if not isinstance(data, dict):
        raise LockoutDataError("salary-cap: top level must be a JSON object")

    missing = [f for f in REQUIRED_CAP_TOP_FIELDS if f not in data]
    if missing:
        raise LockoutDataError(f"salary-cap: missing required field(s): {', '.join(missing)}")

    _parse_iso_date(data["as_of"], "salary-cap.as_of")
    _parse_iso_date(data["updated"], "salary-cap.updated")

    for field in ("meta_description", "summary"):
        if not isinstance(data[field], str) or not data[field].strip():
            raise LockoutDataError(f"salary-cap.{field}: must be a non-empty string")

    figures = data["figures"]
    if not isinstance(figures, list) or not figures:
        raise LockoutDataError("salary-cap.figures: must be a non-empty list")
    for i, fig in enumerate(figures):
        if not isinstance(fig, dict):
            raise LockoutDataError(f"salary-cap.figures[{i}]: must be an object")
        for field in ("value", "label"):
            if not isinstance(fig.get(field), str) or not fig[field].strip():
                raise LockoutDataError(f"salary-cap.figures[{i}].{field}: must be a non-empty string")

    scale = data["scale"]
    if not isinstance(scale, dict):
        raise LockoutDataError("salary-cap.scale: must be an object")
    for field in ("min", "max"):
        if not isinstance(scale.get(field), (int, float)):
            raise LockoutDataError(f"salary-cap.scale.{field}: must be a number")
    if scale["min"] >= scale["max"]:
        raise LockoutDataError("salary-cap.scale: min must be less than max")
    marks = scale.get("marks")
    if not isinstance(marks, list) or not marks:
        raise LockoutDataError("salary-cap.scale.marks: must be a non-empty list")
    for i, mark in enumerate(marks):
        if not isinstance(mark, dict):
            raise LockoutDataError(f"salary-cap.scale.marks[{i}]: must be an object")
        if not isinstance(mark.get("amount"), (int, float)):
            raise LockoutDataError(f"salary-cap.scale.marks[{i}].amount: must be a number")
        if not (scale["min"] <= mark["amount"] <= scale["max"]):
            raise LockoutDataError(
                f"salary-cap.scale.marks[{i}].amount ({mark['amount']}) is outside "
                f"the scale range {scale['min']}-{scale['max']}"
            )
        for field in ("value", "label"):
            if not isinstance(mark.get(field), str) or not mark[field].strip():
                raise LockoutDataError(f"salary-cap.scale.marks[{i}].{field}: must be a non-empty string")
        if mark.get("side") not in ("top", "bottom"):
            raise LockoutDataError(f"salary-cap.scale.marks[{i}].side: must be 'top' or 'bottom'")

    sections = data["sections"]
    if not isinstance(sections, list) or not sections:
        raise LockoutDataError("salary-cap.sections: must be a non-empty list")
    for i, sec in enumerate(sections):
        label = f"salary-cap.sections[{i}]"
        if not isinstance(sec, dict):
            raise LockoutDataError(f"{label}: must be an object")
        for field in ("title", "summary"):
            if not isinstance(sec.get(field), str) or not sec[field].strip():
                raise LockoutDataError(f"{label}.{field}: must be a non-empty string")
        paragraphs = sec.get("paragraphs", [])
        bullets = sec.get("bullets", [])
        if not isinstance(paragraphs, list) or not isinstance(bullets, list):
            raise LockoutDataError(f"{label}: paragraphs and bullets must be lists")
        if not paragraphs and not bullets:
            raise LockoutDataError(f"{label}: needs at least one paragraph or bullet")
        for item in [*paragraphs, *bullets]:
            if not isinstance(item, str) or not item.strip():
                raise LockoutDataError(f"{label}: every paragraph/bullet must be a non-empty string")
        for j, link in enumerate(sec.get("links", []) or []):
            if not isinstance(link, dict) or not link.get("text") or not link.get("href"):
                raise LockoutDataError(f"{label}.links[{j}]: each link needs text and href")

    faq = data["faq"]
    if not isinstance(faq, list) or not (3 <= len(faq) <= 4):
        raise LockoutDataError("salary-cap.faq: must be a list of 3-4 question/answer pairs")
    for i, qa in enumerate(faq):
        if not isinstance(qa, dict):
            raise LockoutDataError(f"salary-cap.faq[{i}]: must be an object")
        for field in ("q", "a"):
            if not isinstance(qa.get(field), str) or not qa[field].strip():
                raise LockoutDataError(f"salary-cap.faq[{i}].{field}: must be a non-empty string")

    sources = data["sources"]
    if not isinstance(sources, list) or not sources:
        raise LockoutDataError("salary-cap.sources: must be a non-empty list")
    for i, src in enumerate(sources):
        label = f"salary-cap.sources[{i}]"
        if not isinstance(src, dict):
            raise LockoutDataError(f"{label}: must be an object")
        for field in ("name", "date"):
            if not isinstance(src.get(field), str) or not src[field].strip():
                raise LockoutDataError(f"{label}.{field}: must be a non-empty string")
        _check_https_url(src.get("url"), f"{label}.url")


def load_salary_cap(path: pathlib.Path = SALARY_CAP_DATA_FILE) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LockoutDataError(f"{path}: not found") from exc
    except json.JSONDecodeError as exc:
        raise LockoutDataError(f"{path}: invalid JSON ({exc})") from exc
    validate_salary_cap(data)
    return data


# ---------------------------------------------------------------------------
# Day-counting conventions. Both are INCLUSIVE of the start date -- the day
# a lockout begins is "day 1" of it, matching how the 2021-22 lockout's
# widely reported 99-day length is counted (Dec 2, 2021 through Mar 10, 2022
# inclusive). The same convention applies to days of a season missed once
# regular_season_delayed_from is set.
# ---------------------------------------------------------------------------

def days_until(today: dt.date, target: dt.date) -> int:
    """Simple, EXCLUSIVE-of-today difference: "30 days until" on the 30th day
    before, zero on the day itself. Not the same inclusive convention as the
    day counters above -- a countdown counts what's left, not an elapsed
    span, so there is nothing to include."""
    return (target - today).days


def day_number(today: dt.date, start: dt.date) -> int:
    return (today - start).days + 1


def cba_expires_instant(cba_expires: dt.date) -> dt.datetime:
    """11:59 p.m. US/Eastern on the day the agreement expires -- see Yahoo
    Sports, 2026-10-29: "expires at 11:59 p.m. ET on Dec. 1." That is
    2026-12-02T04:59Z, because December is Eastern Standard Time (UTC-5);
    ZoneInfo gets the right offset without hardcoding it."""
    return dt.datetime(cba_expires.year, cba_expires.month, cba_expires.day, 23, 59, tzinfo=ET)


def compute_status(data: dict, now: dt.datetime) -> dict:
    """The one status block the page (and docs/lockout.js) renders from.

    `now` must be timezone-aware; `now_et()` is the normal caller. `kind` is
    one of:
      "countdown"          -- before cba_expires, any day but the day itself.
      "expiring_today"      -- the ET calendar day cba_expires falls on, before
                               11:59 p.m. ET.
      "lockout"              -- lockout_start has arrived (<= today, ET) and
                               lockout_end hasn't passed yet, or isn't set.
      "lockout_ended"        -- lockout_end has passed; the day count freezes
                               at the day the stoppage reached.
      "expired_no_lockout"   -- past 11:59 p.m. ET on cba_expires, with no
                               lockout_start on record yet. Not "resolved":
                               this says only that the clock in the agreement
                               ran out, never that a deal was reached.

    A lockout_start in the future (not yet <= today) is deliberately NOT
    "lockout" -- it falls through to whichever of the states above actually
    describes today, because the lockout hasn't started yet.
    """
    if now.tzinfo is None:
        raise ValueError("compute_status requires a timezone-aware `now`")

    cba_expires = _parse_iso_date(data["cba_expires"], "cba_expires")
    lockout_start = _parse_optional_iso_date(data["lockout_start"], "lockout_start")
    lockout_end = _parse_optional_iso_date(data["lockout_end"], "lockout_end")
    delayed_from = _parse_optional_iso_date(
        data["regular_season_delayed_from"], "regular_season_delayed_from"
    )

    expires_at = cba_expires_instant(cba_expires)
    today = now.astimezone(ET).date()

    if lockout_start is not None and lockout_start <= today:
        if lockout_end is not None and today > lockout_end:
            kind = "lockout_ended"
            day = day_number(lockout_end, lockout_start)
        else:
            kind = "lockout"
            day = day_number(today, lockout_start)
            if lockout_end is not None:
                day = min(day, day_number(lockout_end, lockout_start))
    elif now < expires_at:
        if today == cba_expires:
            kind = "expiring_today"
            day = None
        else:
            kind = "countdown"
            day = days_until(today, cba_expires)
    else:
        kind = "expired_no_lockout"
        day = None

    days_at_stake = None
    if delayed_from is not None and today >= delayed_from:
        days_at_stake = day_number(today, delayed_from)

    return {
        "kind": kind,
        "day": day,
        "days_at_stake": days_at_stake,
        "cba_expires": cba_expires.isoformat(),
        "cba_expires_at": expires_at.isoformat(),
        "cba_expires_label": format_date_abbrev(cba_expires),
        "lockout_start": lockout_start.isoformat() if lockout_start else None,
        "lockout_end": lockout_end.isoformat() if lockout_end else None,
        "lockout_end_label": format_date_abbrev(lockout_end) if lockout_end else None,
        "regular_season_delayed_from": delayed_from.isoformat() if delayed_from else None,
    }


def tile_value(status: dict) -> str:
    """The short string the stat tile shows -- the full sentence goes under
    it once, in render_sentence(), rather than being printed a second time."""
    kind, day = status["kind"], status["day"]
    if kind == "countdown":
        return f"{day} day{'s' if day != 1 else ''}"
    if kind == "expiring_today":
        return "Expires tonight"
    if kind == "lockout":
        return f"Day {day}"
    if kind == "lockout_ended":
        return "Lockout ended"
    return "Expired"  # expired_no_lockout


def render_sentence(status: dict) -> str:
    kind, day = status["kind"], status["day"]
    if kind == "countdown":
        text = f"{day} day{'s' if day != 1 else ''} until the CBA expires ({status['cba_expires_label']})"
    elif kind == "expiring_today":
        text = "The CBA expires tonight at 11:59 p.m. ET"
    elif kind == "lockout":
        text = f"Day {day} of the lockout"
    elif kind == "lockout_ended":
        text = f"Lockout ended {status['lockout_end_label']} after {day} day{'s' if day != 1 else ''}"
    else:
        text = (
            f"The CBA expired at 11:59 p.m. ET on {status['cba_expires_label']}. "
            "This page will update when a lockout or new agreement is confirmed."
        )
    if status["days_at_stake"] is not None:
        n = status["days_at_stake"]
        text += f" — an estimated {n} service-time day{'s' if n != 1 else ''} at stake so far"
    return text


def last_updated(data: dict) -> dt.date:
    updated = _parse_iso_date(data["updated"], "updated")
    entries = sorted_entries(data)
    if entries:
        newest = _parse_iso_date(entries[0]["date"], "entries[0].date")
        return max(updated, newest)
    return updated


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _faq_entries(cba_expires: dt.date) -> list[tuple[str, str]]:
    cba_expires_full = format_date_full(cba_expires)
    return [
        (
            "Does service time accrue during an MLB lockout?",
            "Not during the offseason. Service time is only earned for days "
            "on a major league roster during the championship season, so an "
            "offseason lockout — one that starts in December, for example "
            "— doesn't cost a day on its own; there's no season running for "
            "the clock to measure. If a lockout ends up delaying the start "
            "of a season and regular-season days are actually lost, whether "
            "those days count toward service time depends on the settlement "
            "that ends the stoppage — it has been handled differently "
            "across past work stoppages.",
        ),
        (
            "What's the difference between a lockout and a strike?",
            "A lockout is called by the owners, who shut business down and "
            "bar players from club facilities. A strike is called by the "
            "players, who refuse to work. The 2021-22 work stoppage was a "
            "lockout; earlier disputes in MLB history, including the "
            "1994-95 stoppage, were players' strikes.",
        ),
        (
            "When does the current CBA expire, and when could a lockout start?",
            f"The agreement expires {cba_expires_full}, specifically 11:59 "
            "p.m. ET that night. In the last round of bargaining, owners "
            "locked players out within a day of the old deal expiring, so a "
            "similar timeline is widely expected again if no new agreement "
            "is reached first.",
        ),
        (
            "Are free-agent signings and trades frozen during a lockout?",
            "Yes. Clubs can't sign players to major league contracts, trade "
            "players who are on a 40-man roster, or make other 40-man moves "
            "while a lockout is in effect — which is why that kind of "
            "business typically rushes to beat the deadline. Minor league "
            "contracts, which don't touch a 40-man roster, can continue.",
        ),
        (
            "Does a lockout affect salary arbitration or minor league options?",
            "A lockout halts the business side of the sport broadly, which "
            "has historically pushed back tender deadlines and "
            "arbitration-related scheduling along with everything else. "
            "Options apply only to players on the 40-man roster, so option "
            "moves freeze right along with the other 40-man transactions "
            "above — the option rule itself doesn't change.",
        ),
        (
            "How long did the last MLB lockout last?",
            "99 days: it began December 2, 2021 and ended March 10, 2022. "
            "Opening Day moved from March 31 to April 7, but the two sides "
            "agreed to play a full 162-game season on a compressed "
            "schedule, so no games were ultimately missed.",
        ),
    ]


def _stat_tile(value: str, label: str) -> str:
    return f'<div class="stat-tile"><div class="value">{html.escape(value)}</div><div class="label">{html.escape(label)}</div></div>'


def _render_entries(entries: list[dict]) -> str:
    rows = []
    for e in entries:
        category = _entry_category(e)
        rows.append(
            f"<li class='lockout-entry' data-category=\"{category}\">"
            f"<p class='lockout-entry-date'>{html.escape(e['date'])}</p>"
            f"<p class='lockout-entry-headline'><b>{html.escape(e['headline'])}</b></p>"
            f"<p class='lockout-entry-summary'>{html.escape(e['summary'])}</p>"
            f"<p class='lockout-entry-source'>Source: "
            f"<a href=\"{html.escape(e['source_url'])}\" rel=\"noopener\" target=\"_blank\">"
            f"{html.escape(e['source_name'])}</a></p>"
            "</li>"
        )
    return "<ul class='lockout-entries'>" + "".join(rows) + "</ul>"


def _render_faq(cba_expires: dt.date) -> tuple[str, list[dict]]:
    html_parts = []
    ld_items = []
    for q, a in _faq_entries(cba_expires):
        html_parts.append(f"<h3>{html.escape(q)}</h3><p>{a}</p>")
        ld_items.append({
            "@type": "Question",
            "name": q,
            "acceptedAnswer": {"@type": "Answer", "text": a},
        })
    return "".join(html_parts), ld_items


def render(data: dict, now: dt.datetime) -> str:
    status = compute_status(data, now)
    entries = sorted_entries(data)
    updated = last_updated(data)
    cba_expires = dt.date.fromisoformat(status["cba_expires"])
    cba_expires_label = status["cba_expires_label"]

    desc = (
        "2026 MLB CBA talks and the expected lockout. "
        f"Countdown to {cba_expires_label}, sourced updates, and what a lockout "
        "means for service time."
    )

    tiles = [_stat_tile(tile_value(status), "Status")]
    if status["days_at_stake"] is not None:
        tiles.append(_stat_tile(str(status["days_at_stake"]), "Service-time days at stake"))

    faq_html, faq_ld = _render_faq(cba_expires)

    graph = [
        {
            "@type": "Article",
            "headline": page_titles.lockout_og(),
            "description": desc,
            "datePublished": "2026-10-31",
            "dateModified": updated.isoformat(),
            "mainEntityOfPage": PAGE_URL,
            "url": PAGE_URL,
            "author": {"@type": "Organization", "name": "Big League Service Time Tracker"},
            "publisher": {"@type": "Organization", "name": "Big League Service Time Tracker"},
        },
        {"@type": "FAQPage", "mainEntity": faq_ld},
        wpp._breadcrumb_ld([
            ("Big League Service Time Tracker", f"{SITE_URL}/"),
            ("CBA & Lockout", PAGE_URL),
        ]),
    ]

    # Everything docs/lockout.js needs to recompute the status live, keyed
    # the same as compute_status()'s own return value so the two stay easy to
    # compare by eye.
    status_json = json.dumps({
        "cba_expires": status["cba_expires"],
        "cba_expires_at": status["cba_expires_at"],
        "lockout_start": status["lockout_start"],
        "lockout_end": status["lockout_end"],
        "regular_season_delayed_from": status["regular_season_delayed_from"],
        "labels": {
            "cbaExpiresLabel": status["cba_expires_label"],
            "lockoutEndLabel": status["lockout_end_label"],
        },
    })

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
{wpp.HEAD_TAGS}
<title>{html.escape(page_titles.lockout_title())}</title>

<meta name="description" content="{html.escape(desc)}" />
<meta name="theme-color" content="#0a0c10" />
<meta property="og:type" content="website" />
<meta property="og:title" content="{html.escape(page_titles.lockout_og())}" />
<meta property="og:description" content="{html.escape(desc)}" />
<link rel="canonical" href="{PAGE_URL}" />
<meta property="og:url" content="{PAGE_URL}" />
{wpp._ld_script(graph)}

<script>
  try {{
    var t = localStorage.getItem("mlb-service-time-theme");
    if (t) document.documentElement.setAttribute("data-theme", t);
  }} catch (e) {{}}
</script>

<link rel="icon" href="/favicon.svg" type="image/svg+xml" />
<link rel="stylesheet" href="styles.css" />
<link rel="stylesheet" href="taxes.css" />
<link rel="stylesheet" href="lockout.css" />
</head>
<body>
  <div class="viz-root">

    {wpp._site_header("lockout.html")}

    <header class="page-header">
      <div class="page-header-inner">
        <h1>
          <svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
            <circle cx="16" cy="16" r="14" class="bm-ball" />
            <path d="M7.2 5.6c3.6 3 5.4 6.5 5.4 10.4s-1.8 7.4-5.4 10.4" class="bm-seam" />
            <path d="M24.8 5.6c-3.6 3-5.4 6.5-5.4 10.4s1.8 7.4 5.4 10.4" class="bm-seam" />
          </svg>
          <span>CBA &amp; Lockout Tracker</span>
        </h1>
        <p class="subtitle">Plain-English updates on the 2026 CBA talks, and what a lockout would mean for the game's clock.</p>
      </div>
    </header>

    <main class="tax-main">

      {_tab_strip("lockout.html")}

      <p class="lede">The current Basic Agreement between MLB and the
      players' union expires <b>{html.escape(cba_expires_label)}</b> at 11:59
      p.m. ET. Owners are widely expected to lock players out shortly after,
      as they did in 2021. This page tracks that timeline and, once a
      lockout starts, how long it has run. It reports public news, not a
      prediction of how talks end.</p>

      <section class="stat-tiles" aria-label="Lockout status" id="lockout-tiles">
        {''.join(tiles)}
      </section>
      <p class="panel-note" id="lockout-status-note">{html.escape(render_sentence(status))}</p>
      <script id="lockout-status-data" type="application/json">{status_json}</script>
      <script src="lockout.js"></script>
      <script>
        (function () {{
          try {{
            var data = JSON.parse(document.getElementById("lockout-status-data").textContent);
            var status = LockoutStatus.computeStatus(data);
            var sentence = LockoutStatus.sentence(status, data.labels);
            document.getElementById("lockout-status-note").textContent = sentence;
            var valueEl = document.querySelector("#lockout-tiles .stat-tile .value");
            if (valueEl) valueEl.textContent = LockoutStatus.tileValue(status);
          }} catch (e) {{ /* static build value stays as-is */ }}
        }})();
      </script>

      <section class="panel">
        <h2>Latest updates</h2>
        {_filter_bar()}
        {_render_entries(entries)}
      </section>
      {_FILTER_SCRIPT}

      <section class="panel">
        <h2>What's being negotiated</h2>
        <p class="panel-note">
          The Basic Agreement is the contract between MLB's 30 clubs and the
          players' union (the MLBPA) that governs pay, free agency,
          arbitration, roster rules and more. Based on public reporting as
          talks continue, the clubs have proposed a salary cap paired with a
          minimum payroll floor, and the union has rejected a cap while
          pushing for a higher minimum salary and other player-side gains.
          Other reported topics include the length of the regular season and
          playoff format. None of this is final — it is what has been
          reported, not an agreement.
        </p>
      </section>

      <section class="panel">
        <h2>Lockout vs. strike, and the key dates</h2>
        <p class="panel-note">
          A <b>lockout</b> is management shutting the business down and
          barring players from club facilities; a <b>strike</b> is players
          refusing to work. The 2021-22 stoppage was a lockout: it began
          December 2, 2021 and ran 99 days, ending March 10, 2022. Opening
          Day moved from March 31 to April 7, but the sides later agreed to
          play a full 162-game season anyway, so no games were ultimately
          missed. The current agreement expires {html.escape(cba_expires_label)}.
        </p>
      </section>

      <section class="panel">
        <h2>How a lockout affects service time, Super Two and arbitration</h2>
        <p class="panel-note">
          No <a href="service-time.html">service time</a> accrues while the
          season is shut down — an offseason lockout itself doesn't cost a
          day, because service time is only earned during the championship
          season, and that includes off-days: a player is credited for every
          day on the active roster or injured list, not just days his team
          actually plays. What would matter is regular-season days actually
          missed; in 2021-22 the eventual deal restored a full 162-game
          season, so no player's service time or free-agency timeline ended
          up shorter for it — that is public history, not a guarantee about
          any future lockout. A lockout has historically pushed back
          offseason business broadly, including salary arbitration
          scheduling and tender deadlines, and it has no effect on the
          <a href="super-two.html">Super Two</a> rule's mechanics themselves
          — only on how much of a season there ends up being to measure it
          against.
        </p>
      </section>

      <section class="panel">
        <h2>Minor league options, free agency and transactions</h2>
        <p class="panel-note">
          During a lockout, clubs cannot sign free agents, make trades, or
          complete any other transaction involving a player on the 40-man
          roster. Optioning a player is itself a 40-man roster move, so
          option moves freeze right along with everything else in that
          group; business involving players who are not on a 40-man roster
          — most of the minor leagues — continues as normal. See
          <a href="arbitration-eligible.html">who's arbitration-eligible this
          winter</a> for the group whose contracts are usually decided right
          around when a lockout would hit.
        </p>
      </section>

      <section class="panel">
        <h2>FAQ</h2>
        {faq_html}
      </section>

      <p class="foot">
        This page is an independent tracker, not a prediction of how
        negotiations end. It reports what has been publicly reported, in our
        own words, and links to the original source for every item above.
      </p>

    </main>

    {wpp._site_footer()}{wpp._search_script_tag()}
  </div>
  {wpp.ANALYTICS}
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Salary cap & floor explainer -- rendering
# ---------------------------------------------------------------------------

def _cap_tile(fig: dict) -> str:
    note = fig.get("note")
    note_html = f'<div class="tile-note">{html.escape(note)}</div>' if note else ""
    return (
        '<div class="stat-tile accent-muted">'
        f'<div class="value">{html.escape(fig["value"])}</div>'
        f'<div class="label">{html.escape(fig["label"])}</div>'
        f'{note_html}'
        "</div>"
    )


def _cap_scale_svg(scale: dict) -> str:
    """One horizontal scale, drawn here with inline SVG -- no libraries, no
    images. Only the league-level marks the data file carries; positions are
    computed from each mark's amount against the scale's own min/max, and the
    `side` (top/bottom) keeps two close marks from colliding. Decorative to a
    screen reader: the same figures sit in the tiles and the text, so the SVG
    is aria-hidden and the figures are not read twice."""
    lo, hi = float(scale["min"]), float(scale["max"])
    left, right = 70.0, 930.0
    axis_y = 85.0
    span = right - left

    def x_of(amount: float) -> float:
        return left + (float(amount) - lo) / (hi - lo) * span

    parts = [
        f'<line class="cap-scale-axis" x1="{left:.0f}" y1="{axis_y:.0f}" '
        f'x2="{right:.0f}" y2="{axis_y:.0f}" />'
    ]
    for mark in scale["marks"]:
        cx = x_of(mark["amount"])
        anchor = "start" if cx < left + 20 else "end" if cx > right - 20 else "middle"
        if mark["side"] == "top":
            value_y, label_y = axis_y - 20, axis_y - 38
        else:
            value_y, label_y = axis_y + 30, axis_y + 48
        parts.append(
            f'<line class="cap-scale-tick" x1="{cx:.1f}" y1="{axis_y - 8:.0f}" '
            f'x2="{cx:.1f}" y2="{axis_y + 8:.0f}" />'
            f'<circle class="cap-scale-dot" cx="{cx:.1f}" cy="{axis_y:.0f}" r="4" />'
            f'<text class="cap-scale-value" x="{cx:.1f}" y="{value_y:.0f}" '
            f'text-anchor="{anchor}">{html.escape(mark["value"])}</text>'
            f'<text class="cap-scale-label" x="{cx:.1f}" y="{label_y:.0f}" '
            f'text-anchor="{anchor}">{html.escape(mark["label"])}</text>'
        )
    caption = scale.get("caption", "")
    return (
        '<figure class="cap-scale">'
        '<svg viewBox="0 0 1000 150" class="cap-scale-svg" role="img" '
        f'aria-label="{html.escape(caption)}" preserveAspectRatio="xMidYMid meet">'
        + "".join(parts)
        + "</svg>"
        + (f'<figcaption class="panel-note">{html.escape(caption)}</figcaption>' if caption else "")
        + "</figure>"
    )


def _cap_section(sec: dict) -> str:
    body = []
    if sec.get("note"):
        body.append(f'<p class="cap-note">{html.escape(sec["note"])}</p>')
    for para in sec.get("paragraphs", []):
        body.append(f"<p>{html.escape(para)}</p>")
    bullets = sec.get("bullets", [])
    if bullets:
        lis = "".join(f"<li>{html.escape(b)}</li>" for b in bullets)
        body.append(f"<ul class='cap-bullets'>{lis}</ul>")
    links = sec.get("links", []) or []
    if links:
        anchors = " &middot; ".join(
            f'<a href="{html.escape(ln["href"])}">{html.escape(ln["text"])}</a>'
            for ln in links
        )
        body.append(f'<p class="cap-links">{anchors}</p>')
    return (
        '<section class="panel cap-section">'
        f'<h2>{html.escape(sec["title"])}</h2>'
        f'<p class="panel-note cap-section-summary">{html.escape(sec["summary"])}</p>'
        '<details class="cap-details"><summary>Read more</summary>'
        f'<div class="cap-section-body">{"".join(body)}</div>'
        "</details>"
        "</section>"
    )


def _cap_faq(faq: list[dict]) -> tuple[str, list[dict]]:
    html_parts = []
    ld_items = []
    for qa in faq:
        q, a = qa["q"], qa["a"]
        html_parts.append(f"<h3>{html.escape(q)}</h3><p>{html.escape(a)}</p>")
        ld_items.append({
            "@type": "Question",
            "name": q,
            "acceptedAnswer": {"@type": "Answer", "text": a},
        })
    return "".join(html_parts), ld_items


def _cap_sources(data: dict) -> str:
    rows = []
    for src in data["sources"]:
        rows.append(
            "<li>"
            f'{html.escape(src["name"])} <span class="cap-source-date">({html.escape(src["date"])})</span> &mdash; '
            f'<a href="{html.escape(src["url"])}" rel="noopener" target="_blank">link</a>'
            "</li>"
        )
    note = data.get("sources_note", "")
    note_html = f'<p class="panel-note">{html.escape(note)}</p>' if note else ""
    return (
        '<section class="panel cap-section">'
        "<h2>Sources</h2>"
        '<p class="panel-note cap-section-summary">Every figure above is tied to a published proposal or news report.</p>'
        f"{note_html}"
        '<details class="cap-details"><summary>Show sources</summary>'
        f'<ul class="cap-sources">{"".join(rows)}</ul>'
        "</details>"
        "</section>"
    )


def render_salary_cap(data: dict) -> str:
    as_of = _parse_iso_date(data["as_of"], "salary-cap.as_of")
    updated = _parse_iso_date(data["updated"], "salary-cap.updated")
    as_of_label = format_date_full(as_of)

    desc = data["meta_description"]
    tiles = "".join(_cap_tile(fig) for fig in data["figures"])
    scale_svg = _cap_scale_svg(data["scale"])
    sections = "".join(_cap_section(sec) for sec in data["sections"])
    faq_html, faq_ld = _cap_faq(data["faq"])
    sources_html = _cap_sources(data)

    graph = [
        {
            "@type": "Article",
            "headline": "MLB's proposed salary cap and floor, explained",
            "description": desc,
            "datePublished": "2026-11-04",
            "dateModified": updated.isoformat(),
            "mainEntityOfPage": SALARY_CAP_PAGE_URL,
            "url": SALARY_CAP_PAGE_URL,
            "author": {"@type": "Organization", "name": "Big League Service Time Tracker"},
            "publisher": {"@type": "Organization", "name": "Big League Service Time Tracker"},
        },
        {"@type": "FAQPage", "mainEntity": faq_ld},
        wpp._breadcrumb_ld([
            ("Big League Service Time Tracker", f"{SITE_URL}/"),
            ("CBA & Lockout", PAGE_URL),
            ("Salary cap & floor", SALARY_CAP_PAGE_URL),
        ]),
    ]

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
{wpp.HEAD_TAGS}
<title>{html.escape(page_titles.salary_cap_title())}</title>

<meta name="description" content="{html.escape(desc)}" />
<meta name="theme-color" content="#0a0c10" />
<meta property="og:type" content="article" />
<meta property="og:title" content="{html.escape(page_titles.salary_cap_og())}" />
<meta property="og:description" content="{html.escape(desc)}" />
<link rel="canonical" href="{SALARY_CAP_PAGE_URL}" />
<meta property="og:url" content="{SALARY_CAP_PAGE_URL}" />
{wpp._ld_script(graph)}

<script>
  try {{
    var t = localStorage.getItem("mlb-service-time-theme");
    if (t) document.documentElement.setAttribute("data-theme", t);
  }} catch (e) {{}}
</script>

<link rel="icon" href="/favicon.svg" type="image/svg+xml" />
<link rel="stylesheet" href="styles.css" />
<link rel="stylesheet" href="taxes.css" />
<link rel="stylesheet" href="lockout.css" />
</head>
<body>
  <div class="viz-root">

    {wpp._site_header("lockout.html")}

    <header class="page-header">
      <div class="page-header-inner">
        <h1>
          <svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
            <circle cx="16" cy="16" r="14" class="bm-ball" />
            <path d="M7.2 5.6c3.6 3 5.4 6.5 5.4 10.4s-1.8 7.4-5.4 10.4" class="bm-seam" />
            <path d="M24.8 5.6c-3.6 3-5.4 6.5-5.4 10.4s1.8 7.4 5.4 10.4" class="bm-seam" />
          </svg>
          <span>The owners' salary cap and floor proposal, explained</span>
        </h1>
        <p class="subtitle">The league-level numbers in MLB's proposed cap and floor, how they sit against today's luxury tax, and what they could mean for service time.</p>
      </div>
    </header>

    <main class="tax-main">

      {_tab_strip("lockout-salary-cap.html")}

      <section class="stat-tiles cap-tiles" aria-label="Proposed cap and floor, league-level figures">
        {tiles}
      </section>

      <p class="lede cap-summary">{html.escape(data["summary"])}</p>
      <p class="panel-note cap-asof">As of {html.escape(as_of_label)}. These are proposals reported during bargaining, not final terms.</p>

      {scale_svg}

      {sections}

      <section class="panel">
        <h2>FAQ</h2>
        {faq_html}
      </section>

      {sources_html}

      <p class="foot">
        This page explains publicly reported bargaining proposals in our own
        words, and links to a source for every figure. It is not a prediction
        of how negotiations end, and the numbers are proposals that can change.
      </p>

    </main>

    {wpp._site_footer()}{wpp._search_script_tag()}
  </div>
  {wpp.ANALYTICS}
</body>
</html>
"""


def build(data_file: pathlib.Path = DATA_FILE, out_file: pathlib.Path = OUT_FILE,
          now: dt.datetime | None = None) -> str:
    data = load(data_file, today=(now.astimezone(ET).date() if now else None))
    page = render(data, now or now_et())
    out_file.write_text(page, encoding="utf-8")
    return page


def build_salary_cap(data_file: pathlib.Path = SALARY_CAP_DATA_FILE,
                     out_file: pathlib.Path = SALARY_CAP_OUT_FILE) -> str:
    data = load_salary_cap(data_file)
    page = render_salary_cap(data)
    out_file.write_text(page, encoding="utf-8")
    return page


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-file", type=pathlib.Path, default=DATA_FILE)
    parser.add_argument("--out", type=pathlib.Path, default=OUT_FILE)
    parser.add_argument("--check", action="store_true", help="validate only, write nothing")
    parser.add_argument("--today", type=str, default=None,
                         help="pin today's ET date (noon ET), ISO format")
    args = parser.parse_args(argv)

    today = dt.date.fromisoformat(args.today) if args.today else None
    now = dt.datetime(today.year, today.month, today.day, 12, 0, tzinfo=ET) if today else now_et()

    try:
        data = load(args.data_file, today=now.astimezone(ET).date())
        cap_data = load_salary_cap()
        if args.check:
            print(f"{args.data_file}: ok ({len(data['entries'])} entries)")
            print(f"{SALARY_CAP_DATA_FILE}: ok ({len(cap_data['sections'])} sections)")
            return 0
        page = render(data, now)
        args.out.write_text(page, encoding="utf-8")
        print(f"wrote {args.out}")
        cap_page = render_salary_cap(cap_data)
        SALARY_CAP_OUT_FILE.write_text(cap_page, encoding="utf-8")
        print(f"wrote {SALARY_CAP_OUT_FILE}")
        return 0
    except LockoutDataError as exc:
        print(f"lockout data error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
