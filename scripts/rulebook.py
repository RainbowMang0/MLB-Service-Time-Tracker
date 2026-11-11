"""
rulebook.py
-----------
One classification of every saved transaction, shared by service time,
optional assignments, and major-league injured-list spells.

The rules live in data/rulebook/rules.json. Each rule has one effect
(start, stop, count_away, neutral) and a stable id. First match wins.
A row that matches nothing is unknown: an error for a current 40-man
player, a warning for everyone else. Citations on the rules point at
the job 2 rules reference (CBA, Major League Rules, and public reporting).

Thresholds that already live in config/cba are read from there. This
module does not copy them and does not write the published site.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from functools import lru_cache
from pathlib import Path

import cba
import rulebook_limits
from mlb_clubs import MLB_CLUB_IDS

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "data" / "rulebook" / "rules.json"
WINTER_PATH = ROOT / "data" / "rulebook" / "winter_teams.json"
CALENDAR_PATH = ROOT / "data" / "reference" / "season_calendar.json"
EXCEPTIONS_PATH = ROOT / "data" / "reference" / "service_time_exceptions.json"

RULEBOOK_VERSION = "rulebook-1"

# 1996 settlement: 75 regular-season days the strike wiped out.
# The 52/23 split is an assumption (cancelled 1994 remainder, delayed 1995 start).
STRIKE_1994_LAST = dt.date(1994, 8, 11)
STRIKE_1994_DAYS = 52
STRIKE_1995_DAYS = 23
RESTARTABLE_STOPS = {
    "stop.option",
    "stop.assigned-minors",
    "stop.reassigned-minors",
    "stop.taxi-squad",
}
BLOCKING_STOPS = ("stop.free-agency", "stop.released")

_EFFECTS = frozenset({"start", "stop", "count_away", "neutral"})
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
_RETRO_RE = re.compile(r"retroactive to ([A-Za-z]+ \d{1,2}, \d{4})", re.I)

# Roster moves that can pull the floor a short gap before the first game.
# A signing cannot. The width is the measured empty band in the rulebook.
_PRE_DEBUT_RULES = frozenset({
    "start.selected",
    "start.recalled",
    "start.purchased",
    "start.rule5",
    "start.activated",
    "start.reinstated-code",
})

_SKIP_LOOKAHEAD = frozenset({
    "neutral.rehab",
    "neutral.all-star",
    "neutral.national-team",
    "neutral.spring-invite",
    "neutral.number-change",
    "neutral.roster-status",
    "neutral.winter-league",
    "neutral.loan-offseason",
    "neutral.transfer-other",
    "neutral.temp-inactive-minors",
    "neutral.reserve-minors",
    "neutral.waived",
    "neutral.acquired",
    "neutral.minor-activation",
    "neutral.minor-il",
    "neutral.minor-personal-list",
    "neutral.suspension-minors",
    "neutral.recall-minors",
    "neutral.no-mlb-club",
    "neutral.draft",
    "neutral.minor-signing",
    "neutral.amateur-signing",
    "neutral.rule5-minors",
    "neutral.purchase-other",
    "neutral.claim-minors",
    "neutral.postseason-reassignment",
    "neutral.offseason-inactive",
    "neutral.blank-status",
    "neutral.series-inactive",
})


class RulebookError(Exception):
    """The rulebook file or a result that must not be published is wrong."""


class PublishBlocked(RulebookError):
    """A current 40-man player has a transaction the rulebook cannot classify."""


def _date(value) -> dt.date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _int_id(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _team(row: dict, key: str) -> tuple[int | None, str]:
    """(id, name) from a raw feed object or a flat cache row."""
    flat_id = _int_id(row.get(key + "_id"))
    obj = row.get(key)
    if isinstance(obj, dict):
        obj_id = _int_id(obj.get("id"))
        name = obj.get("name") if isinstance(obj.get("name"), str) else ""
        return obj_id if obj_id is not None else flat_id, name
    if isinstance(obj, str) and key == "team":
        return flat_id, obj
    return flat_id, ""


def row_ids(row: dict) -> dict:
    team_id, team_name = _team(row, "team")
    from_id, from_name = _team(row, "fromTeam")
    if from_id is None:
        from_id, from_name = _team(row, "from_team")
    to_id, to_name = _team(row, "toTeam")
    if to_id is None:
        to_id, extra = _team(row, "to_team")
        if extra:
            to_name = extra
    # Cache rows use from_team_id / to_team_id, already read via _team when
    # the object form is absent. Flat keys win when the object was missing.
    if from_id is None:
        from_id = _int_id(row.get("from_team_id"))
    if to_id is None:
        to_id = _int_id(row.get("to_team_id"))
    if team_id is None:
        team_id = _int_id(row.get("team_id"))
    return {
        "team": team_id,
        "from": from_id,
        "to": to_id,
        "team_name": team_name,
        "from_name": from_name,
        "to_name": to_name,
    }


def _blob(row: dict, ids: dict) -> str:
    parts = [
        row.get("description") or "",
        row.get("typeDesc") or "",
        ids["team_name"],
        ids["from_name"],
        ids["to_name"],
    ]
    return " ".join(p for p in parts if p)


def _mlb_present(ids: dict) -> bool:
    return any(i in MLB_CLUB_IDS for i in (ids["team"], ids["from"], ids["to"]) if i is not None)


def _fold(text: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", text or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


@lru_cache(maxsize=1)
def load_winter_teams(path: str | None = None) -> tuple[frozenset, frozenset]:
    """(team ids, names safe to match when the id is missing).

    Names shared with a summer club, such as Charros de Jalisco, are matched
    by id only so a summer assignment still stops service.
    """
    file = Path(path) if path else WINTER_PATH
    doc = json.loads(file.read_text(encoding="utf-8"))
    ids = []
    names = []
    for row in doc.get("teams") or []:
        ids.append(int(row["id"]))
        if row.get("name_match", True) and row.get("name"):
            names.append(_fold(row["name"]))
    return frozenset(ids), frozenset(names)


def _is_winter(row: dict, ids: dict) -> bool:
    code = row.get("typeCode") if isinstance(row.get("typeCode"), str) else ""
    desc = row.get("description") or ""
    if code != "ASG" and not re.search(r"\bassigned to\b", desc, re.I):
        return False
    winter_ids, names = load_winter_teams()
    if ids["to"] in winter_ids:
        return True
    folded = _fold(" ".join(part for part in (ids["to_name"], ids["team_name"]) if part))
    if not folded:
        folded = _fold(desc)
    return folded in names or any(f" {name} " in f" {folded} " for name in names)


def _is_series_inactive(row: dict, ids: dict, calendar: Calendar) -> bool:
    """Held off the active list for an overseas opener. Section 9."""
    desc = (row.get("description") or "").lower()
    if "temporarily inactive" not in desc and "reserve list" not in desc:
        return False
    day = _date(row.get("date"))
    if day is None:
        return False
    club = next((ids[key] for key in ("to", "from", "team") if ids.get(key) in MLB_CLUB_IDS), None)
    if club is None:
        return False
    season = calendar.seasons.get(day.year)
    if season is None or season.championship_start >= season.first_domestic:
        return False
    if club not in season.international_clubs:
        return False
    return season.championship_start - dt.timedelta(days=3) <= day < season.first_domestic


def _any_team(ids: dict) -> bool:
    return any(i is not None for i in (ids["team"], ids["from"], ids["to"]))


class Hit:
    __slots__ = ("id", "effect", "tags", "cite")

    def __init__(self, rule: _Rule):
        self.id = rule.id
        self.effect = rule.effect
        self.tags = frozenset(rule.tags)
        self.cite = rule.cite

    def __repr__(self) -> str:
        return f"Hit({self.id})"


class _Rule:
    __slots__ = (
        "id", "effect", "tags", "cite", "note", "type_codes", "description",
        "not_description", "team_name", "mlb", "destination", "season", "years",
    )

    def __init__(self, raw: dict):
        self.id = raw["id"]
        self.effect = raw["effect"]
        self.tags = tuple(raw.get("tags") or [])
        self.cite = raw.get("cite") or ""
        self.note = raw.get("note") or ""
        self.type_codes = frozenset(raw.get("type_codes") or [])
        self.description = re.compile(raw["description"], re.I) if raw.get("description") else None
        self.not_description = re.compile(raw["not_description"], re.I) if raw.get("not_description") else None
        self.team_name = re.compile(raw["team_name"], re.I) if raw.get("team_name") else None
        self.mlb = raw.get("mlb") or "any"
        self.destination = raw.get("destination") or "any"
        self.season = raw.get("season") or "any"
        years = raw.get("years")
        self.years = (int(years[0]), int(years[1])) if years else None


@lru_cache(maxsize=1)
def load_rulebook(path: str | None = None) -> dict:
    """Compiled rulebook. Cached for the process."""
    file = Path(path) if path else RULES_PATH
    doc = json.loads(file.read_text(encoding="utf-8"))
    rules = []
    seen = set()
    for raw in doc["rules"]:
        if raw.get("effect") not in _EFFECTS:
            raise RulebookError(f"rule {raw.get('id')} has effect {raw.get('effect')!r}")
        if raw["id"] in seen:
            raise RulebookError(f"duplicate rule id {raw['id']}")
        seen.add(raw["id"])
        rules.append(_Rule(raw))
    windows = []
    for raw in doc["dfa_windows"]:
        years = raw["years"]
        windows.append({
            "id": raw["id"],
            "years": (int(years[0]), int(years[1])),
            "days": int(raw["days"]),
            "open_question": bool(raw.get("open_question")),
            "cite": raw.get("cite") or "",
            "note": raw.get("note") or "",
        })
    return {
        "version": doc["version"],
        "rules": rules,
        "accrual": doc["accrual"],
        "dfa_windows": windows,
        "open_questions": doc["open_questions"],
        "pre_debut_roster_window_days": int(doc["pre_debut_roster_window_days"]),
    }


def open_questions() -> list[dict]:
    return list(load_rulebook()["open_questions"])


class SeasonCal:
    __slots__ = (
        "year", "championship_start", "championship_end", "first_domestic",
        "international_clubs", "tiebreakers",
    )

    def __init__(self, row: dict):
        self.year = int(row["season"])
        self.championship_start = dt.date.fromisoformat(row["championship_start"])
        self.championship_end = dt.date.fromisoformat(row["championship_end"])
        self.first_domestic = dt.date.fromisoformat(row["first_domestic_game"])
        clubs = set()
        for game in row.get("games_outside_us_and_canada") or []:
            played = dt.date.fromisoformat(game["date"])
            if played < self.first_domestic:
                clubs.add(int(game["away_id"]))
                clubs.add(int(game["home_id"]))
        self.international_clubs = frozenset(clubs)
        ties = []
        for game in row.get("tiebreakers_excluded") or []:
            ties.append((
                dt.date.fromisoformat(game["date"]),
                frozenset({int(game["away_id"]), int(game["home_id"])}),
            ))
        self.tiebreakers = tuple(ties)


class Calendar:
    def __init__(self, seasons: list[SeasonCal]):
        self.seasons = {s.year: s for s in seasons}

    @classmethod
    def load(cls, path: str | None = None) -> "Calendar":
        file = Path(path) if path else CALENDAR_PATH
        doc = json.loads(file.read_text(encoding="utf-8"))
        return cls([SeasonCal(row) for row in doc["seasons"]])

    def league_start(self, year: int) -> dt.date:
        """First game inside the ordinary band, for clubs that did not open overseas.

        Section 9. 187 days inclusive is end minus 186 days.
        """
        season = self.seasons[year]
        band = season.championship_end - dt.timedelta(days=186)
        if season.championship_start >= band:
            return season.championship_start
        return season.first_domestic

    def in_championship(self, day: dt.date) -> bool:
        for season in self.seasons.values():
            end = season.championship_end
            for tie_day, _clubs in season.tiebreakers:
                if tie_day > end:
                    end = tie_day
            if season.championship_start <= day <= end:
                return True
        return False

    def window(self, year: int, club_id: int | None, club_on) -> tuple[dt.date, dt.date]:
        """(start, end) for one player-season. club_on(date) is his major-league club."""
        season = self.seasons[year]
        opening_club = club_on(season.championship_start)
        if opening_club in season.international_clubs:
            start = season.championship_start
        else:
            start = self.league_start(year)
        end = season.championship_end
        # Tiebreaker days count through 2021 for the clubs that played them.
        # From 2022 they do not. Section 9; the 2017-21 wording is an open question.
        if year <= 2021:
            for tie_day, clubs in season.tiebreakers:
                if club_on(tie_day) in clubs and tie_day > end:
                    end = tie_day
        return start, end

    def next_opener_after(self, day: dt.date, club_id: int | None) -> dt.date | None:
        found = None
        for year in sorted(self.seasons):
            start = self.league_start(year)
            season = self.seasons[year]
            if club_id in season.international_clubs:
                start = min(start, season.championship_start)
            if start > day and (found is None or start < found):
                found = start
        return found


@lru_cache(maxsize=1)
def load_calendar(path: str | None = None) -> Calendar:
    return Calendar.load(path)


@lru_cache(maxsize=1)
def load_exceptions(path: str | None = None) -> dict[int, list[dict]]:
    file = Path(path) if path else EXCEPTIONS_PATH
    if not file.exists():
        return {}
    doc = json.loads(file.read_text(encoding="utf-8"))
    out: dict[int, list[dict]] = {}
    for entry in doc.get("exceptions") or []:
        out.setdefault(int(entry["mlbam_id"]), []).append({
            "start": dt.date.fromisoformat(entry["start"]),
            "end": dt.date.fromisoformat(entry["end"]),
            "counts": bool(entry["counts"]),
            "kind": entry.get("kind") or "unspecified",
        })
    return out


def _match(rule: _Rule, row: dict, ids: dict, blob: str, calendar: Calendar) -> bool:
    code = row.get("typeCode") if isinstance(row.get("typeCode"), str) else ""
    type_hit = bool(rule.type_codes) and code in rule.type_codes
    desc_hit = bool(rule.description) and bool(rule.description.search(blob))
    if rule.type_codes and rule.description:
        if not (type_hit or desc_hit):
            return False
    elif rule.type_codes and not type_hit:
        return False
    elif rule.description and not desc_hit:
        return False
    if rule.not_description and rule.not_description.search(blob):
        return False
    if rule.team_name:
        # The destination name is on toTeam in the raw feed and, in the flat
        # cache, on the team string. The whole name has to match so a club
        # such as "Diablos Rojos del Mexico" is not read as the Mexico
        # national team.
        candidates = [ids["to_name"], ids["team_name"]]
        if not any(name and rule.team_name.search(name.strip()) for name in candidates):
            return False
    if rule.mlb == "required" and not _mlb_present(ids):
        return False
    if rule.mlb == "forbidden" and (not _any_team(ids) or _mlb_present(ids)):
        return False
    day = _date(row.get("date"))
    if rule.destination != "any":
        to_id = ids["to"]
        is_mlb = to_id in MLB_CLUB_IDS if to_id is not None else None
        if is_mlb is None:
            return False
        if rule.destination == "mlb" and not is_mlb:
            return False
        if rule.destination == "not_mlb" and is_mlb:
            return False
    if rule.years and day is not None:
        if not (rule.years[0] <= day.year <= rule.years[1]):
            return False
    if rule.season != "any" and day is not None:
        inside = calendar.in_championship(day)
        if rule.season == "championship" and not inside:
            return False
        if rule.season == "offseason" and inside:
            return False
    return True


_ENGINE_RULES = frozenset({
    "neutral.winter-league",
    "neutral.blank-status",
    "neutral.series-inactive",
})


def classify_row(row: dict, calendar: Calendar | None = None, book: dict | None = None) -> Hit | None:
    """The one classification. None means unknown."""
    calendar = calendar or load_calendar()
    book = book or load_rulebook()
    ids = row_ids(row)
    blob = _blob(row, ids)
    by_id = {rule.id: rule for rule in book["rules"]}
    code = row.get("typeCode") if isinstance(row.get("typeCode"), str) else ""
    desc = row.get("description") or ""
    if code == "SC" and not desc.strip() and "neutral.blank-status" in by_id:
        return Hit(by_id["neutral.blank-status"])
    if _is_national_split(ids) and "neutral.national-team" in by_id:
        return Hit(by_id["neutral.national-team"])
    if _is_winter(row, ids) and "neutral.winter-league" in by_id:
        return Hit(by_id["neutral.winter-league"])
    if _is_series_inactive(row, ids, calendar) and "neutral.series-inactive" in by_id:
        return Hit(by_id["neutral.series-inactive"])
    for rule in book["rules"]:
        if rule.id in _ENGINE_RULES:
            continue
        if _match(rule, row, ids, blob, calendar):
            return Hit(rule)
    return None


def _retro_start(row: dict, day: dt.date) -> dt.date:
    """Earlier effective date for an injured-list placement, within three weeks."""
    eff = _date(row.get("effectiveDate"))
    if eff is not None and 0 < (day - eff).days <= 21:
        return eff
    match = _RETRO_RE.search(row.get("description") or "")
    if not match:
        return day
    try:
        month_name, day_s, year_s = match.group(1).replace(",", "").split()
        parsed = dt.date(int(year_s), _MONTHS[month_name.lower()], int(day_s))
    except (KeyError, ValueError):
        return day
    if 0 < (day - parsed).days <= 21:
        return parsed
    return day


def _dfa_days(year: int, book: dict, ruleset: cba.Ruleset) -> tuple[int, str]:
    for window in book["dfa_windows"]:
        lo, hi = window["years"]
        if lo <= year <= hi:
            days = window["days"]
            # 2017 and later must stay on the ruleset's cap so the two cannot drift.
            if year >= 2017 and not window["open_question"]:
                days = int(ruleset.require("designated_for_assignment.max_service_days"))
            return days, window["id"]
    return int(ruleset.require("designated_for_assignment.max_service_days")), "accrual.dfa-window-7"


def _club_after(ids: dict, hit: Hit | None) -> int | None:
    order = (ids["to"], ids["team"], ids["from"])
    if hit and ("option" in hit.tags or "outright" in hit.tags or "release" in hit.tags or "dfa" in hit.tags):
        order = (ids["from"], ids["team"], ids["to"])
    for club in order:
        if club in MLB_CLUB_IDS:
            return club
    return None


def _merge(intervals: list[tuple[dt.date, dt.date, frozenset]]) -> list[tuple[dt.date, dt.date, frozenset]]:
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda item: (item[0], item[1]))
    merged = [ordered[0]]
    for start, end, rules in ordered[1:]:
        last_start, last_end, last_rules = merged[-1]
        if start <= last_end + dt.timedelta(days=1):
            merged[-1] = (last_start, max(last_end, end), last_rules | rules)
        else:
            merged.append((start, end, rules))
    return merged


def _subtract(intervals, lo: dt.date, hi: dt.date):
    out = []
    for start, end, rules in intervals:
        if hi < start or lo > end:
            out.append((start, end, rules))
            continue
        if start < lo:
            out.append((start, lo - dt.timedelta(days=1), rules))
        if end > hi:
            out.append((hi + dt.timedelta(days=1), end, rules))
    return out


def _overlap_days(start: dt.date, end: dt.date, lo: dt.date, hi: dt.date) -> int:
    a = max(start, lo)
    b = min(end, hi)
    if b < a:
        return 0
    return (b - a).days + 1


def _span_days(start: dt.date, end: dt.date) -> int:
    if end < start:
        return 0
    return (end - start).days + 1


def _format(days: int, full_year: int) -> str:
    years, rest = divmod(days, full_year)
    return f"{years}.{rest:03d}"


def _prorate(year: int, raw: int, start: dt.date, end: dt.date, ruleset: cba.Ruleset) -> int:
    shortened = ruleset.shortened_seasons()
    if year not in shortened:
        return raw
    normal = shortened[year]
    span = (end - start).days + 1
    if span <= 0 or span >= normal:
        return raw
    return int(round(raw * normal / span))


def _lookahead_confirms(later_days, grouped) -> bool:
    """An 'assigned to <major-league club>' row put him on the active list
    only when a later row shows an injured-list placement or an option.
    """
    for day in later_days:
        if day not in grouped:
            continue
        hits = [hit for _row, hit in grouped[day] if hit is not None]
        if not hits:
            continue
        if all(hit.id in _SKIP_LOOKAHEAD for hit in hits):
            continue
        if any("option" in hit.tags or "il" in hit.tags for hit in hits):
            return True
        return False
    return False


def _terminal(hits: list[Hit]) -> bool:
    for hit in hits:
        if hit.id == "stop.free-agency" or "free-agency" in hit.tags:
            return True
        if hit.effect == "stop" and "dfa" not in hit.tags:
            return True
    return False


def _opens(hit: Hit) -> bool:
    if hit.effect == "start":
        return True
    if hit.effect == "count_away" and "dfa" not in hit.tags:
        return True
    return False


def _returns_to_roster(hit: Hit) -> bool:
    """A recall, selection, activation, or signing. An injured-list row is not one."""
    return hit.effect == "start"


# National-team split squads and aliases. The exact-name rule misses
# "Mexico Rojo" and "Mexico Verde" and would treat them as minor-league
# assignments. "Diablos Rojos del Mexico" is not in this set.
_NATIONAL_SPLIT_IDS = frozenset({
    1192,  # Mexico Rojo
    6487,  # Mexico Verde
    1331,  # Samurai Jpn.
    878,   # Kingdom of the Netherlands
    800,   # Czechia
    4564,  # USAB Stars Team
    4565,  # USAB Stripes Team
    2492,  # USA Blue Team
})
_NATIONAL_SPLIT_NAMES = frozenset({
    "mexico rojo",
    "mexico verde",
    "samurai jpn",
    "samurai japan",
    "kingdom of the netherlands",
    "czechia",
    "usab stars team",
    "usab stripes team",
    "usa blue team",
})


def _is_national_split(ids: dict) -> bool:
    if ids["to"] in _NATIONAL_SPLIT_IDS:
        return True
    for name in (ids["to_name"], ids["team_name"]):
        if _fold(name) in _NATIONAL_SPLIT_NAMES:
            return True
    return False


def _apply_appearance_dates(intervals, rows, dates, floor, horizon):
    """Restart at the first major-league game after a stop the feed never recalled.

    Consecutive game dates are one stint. A recorded stop strictly between
    two games splits them, so an option the feed did record is not filled in.
    The stint is still a feed hole: the caller lists it for review.
    """
    usable = sorted({
        day for day in dates
        if (floor is None or day >= floor) and day <= horizon
    })
    if not usable:
        return intervals, []
    stop_dates = {
        day for day, _row, hit in rows
        if hit is not None and hit.effect == "stop"
    }
    spans = []
    start = prev = usable[0]
    for day in usable[1:]:
        if any(prev < stop < day for stop in stop_dates):
            spans.append((start, prev))
            start = day
        prev = day
    spans.append((start, prev))
    review = []
    merged = _merge(intervals)
    for start, end in spans:
        covered = 0
        for lo, hi, _rules in merged:
            overlap_lo = max(lo, start)
            overlap_hi = min(hi, end)
            if overlap_hi >= overlap_lo:
                covered += _span_days(overlap_lo, overlap_hi)
        added = _span_days(start, end) - covered
        if added <= 0:
            continue
        intervals.append((start, end, frozenset({"accrual.appearance-game"})))
        review.append({
            "year": start.year,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days_added": added,
            "reason": (
                "Major-league games fall after a stop that has no recall in the file. "
                "Service restarts at the first of those games. The transactions feed "
                "can be refetched for the missing row; this stint stays on the review list."
            ),
        })
    if review:
        intervals = _merge(intervals)
    return intervals, review


def evaluate(
    transactions: list[dict] | None,
    *,
    player_id: int | None = None,
    name: str | None = None,
    debut: dt.date | None = None,
    on_40_man: bool = False,
    horizon: dt.date | None = None,
    career_end: dt.date | None = None,
    appearance_years: set[int] | None = None,
    appearance_counts: dict[int, int] | None = None,
    appearance_dates: list[dt.date] | None = None,
    exceptions: list[dict] | None = None,
    roy_years: set[int] | None = None,
    calendar: Calendar | None = None,
    ruleset: cba.Ruleset | None = None,
    book: dict | None = None,
) -> dict:
    """Service time, option years, and injured-list spells for one player.

    `transactions` are raw feed rows or the flat cache shape. Dates alone
    decide same-day results; row order does not.
    """
    calendar = calendar or load_calendar()
    book = book or load_rulebook()
    ruleset = ruleset or cba.default()
    full_year = int(ruleset.require("service_time.days_per_credited_year"))
    cap = int(ruleset.require("service_time.max_days_creditable_per_season"))
    option_threshold = int(ruleset.require_verified("options.days_to_burn_option_year"))
    if horizon is None:
        horizon = dt.date(2026, 11, 9)
    if exceptions is None and player_id is not None:
        exceptions = load_exceptions().get(int(player_id), [])
    exceptions = exceptions or []
    if roy_years is None and player_id is not None:
        roy_years = cba.roy_full_year_seasons_by_player().get(int(player_id), set())
    roy_years = roy_years or set()

    rows = []
    unknowns = []
    ambiguous = []
    for row in transactions or []:
        if not isinstance(row, dict):
            continue
        day = _date(row.get("date"))
        hit = classify_row(row, calendar, book) if day is not None else None
        if day is None or hit is None:
            unknowns.append({
                "player_id": player_id,
                "name": name,
                "date": day.isoformat() if day else None,
                "type_code": row.get("typeCode") or "",
                "description": (row.get("description") or "")[:240],
                "on_40_man": bool(on_40_man),
                "severity": "error" if on_40_man else "warning",
            })
            if day is None:
                continue
        rows.append((day, row, hit))
    rows.sort(key=lambda item: (item[0], item[1].get("id") or 0, item[1].get("description") or ""))

    grouped: dict[dt.date, list[tuple[dict, Hit | None]]] = {}
    for day, row, hit in rows:
        grouped.setdefault(day, []).append((row, hit))

    # Same-day minor-league contract suppresses a bare major-league signing.
    for day, items in grouped.items():
        ids = {hit.id for _row, hit in items if hit is not None}
        if ({"neutral.minor-signing", "neutral.spring-invite"} & ids) and (
            ids & {"start.mlb-signing", "start.mlb-contract-signing"}
        ):
            grouped[day] = [
                (row, None if hit is not None and hit.id in {"start.mlb-signing", "start.mlb-contract-signing"} else hit)
                for row, hit in items
            ]

    window_days = book["pre_debut_roster_window_days"]
    floor = debut
    if debut is not None:
        earliest = debut - dt.timedelta(days=window_days)
        early = [
            day for day, _row, hit in rows
            if hit is not None and hit.id in _PRE_DEBUT_RULES and earliest <= day < debut
        ]
        if early:
            floor = min(early)

    club = None
    club_timeline: list[tuple[dt.date, int]] = []
    for day, row, hit in rows:
        ids = row_ids(row)
        updated = _club_after(ids, hit)
        if updated is not None and updated != club:
            club = updated
            club_timeline.append((day, updated))

    def club_on(day: dt.date) -> int | None:
        current = None
        for when, club_id in club_timeline:
            if when <= day:
                current = club_id
            else:
                break
        return current

    # Optional-assignment spells, including ones before the service floor.
    # Service uses the same hits.
    on_option = False
    option_start: dt.date | None = None
    option_spells: list[tuple[dt.date, dt.date]] = []
    il_spells: list[tuple[dt.date, dt.date, str]] = []
    il_open: tuple[dt.date, str] | None = None

    intervals: list[tuple[dt.date, dt.date, frozenset]] = []
    active_since: dt.date | None = None
    span_rules: set[str] = set()
    dfa_until: dt.date | None = None
    dfa_rule = "count.dfa"
    last_stop = None  # "dfa" or "other"
    last_stop_id = None
    presumed = False
    boundaries = []
    # An appearance restart covers that season only. The unanswered option is
    # put back at the end of the season so a later year with its own call-up
    # starts from the minors instead of inheriting a full season.
    pending_reclose: dt.date | None = None

    def close_service(end: dt.date, rule: str | None) -> None:
        nonlocal active_since, span_rules
        if active_since is not None and end >= active_since:
            rules = set(span_rules)
            if rule:
                rules.add(rule)
            intervals.append((active_since, end, frozenset(rules)))
        active_since = None
        span_rules = set()

    def open_service(day: dt.date, rule: str) -> None:
        nonlocal active_since, span_rules, last_stop, last_stop_id
        if active_since is None:
            active_since = day
            span_rules = {rule}
            last_stop = None
            last_stop_id = None
            boundaries.append({"date": day.isoformat(), "kind": "start", "rule": rule})

    def end_option(end: dt.date) -> None:
        nonlocal on_option, option_start
        if on_option and option_start is not None and end >= option_start:
            option_spells.append((option_start, end))
        on_option = False
        option_start = None

    def begin_option(day: dt.date) -> None:
        nonlocal on_option, option_start
        if not on_option:
            on_option = True
            option_start = day

    def close_il(end: dt.date) -> None:
        nonlocal il_open
        if il_open is not None and end >= il_open[0]:
            il_spells.append((il_open[0], end, il_open[1]))
        il_open = None

    dates = sorted(grouped)
    restart_days = set()
    reclose_days = set()
    if appearance_years:
        for year in appearance_years:
            if year not in calendar.seasons:
                continue
            opener, season_end = calendar.window(
                year, club_on(calendar.seasons[year].championship_start), club_on
            )
            if floor is None or opener >= floor:
                restart_days.add(opener)
                reclose_days.add(season_end + dt.timedelta(days=1))
    if floor is not None and (not dates or floor not in grouped):
        dates = sorted(set(dates) | {floor} | restart_days | reclose_days)
    else:
        dates = sorted(set(dates) | restart_days | reclose_days)

    for index, day in enumerate(dates):
        if (
            active_since is not None
            and dfa_until is not None
            and day > dfa_until
            and (floor is None or dfa_until >= floor)
        ):
            close_service(dfa_until, "accrual.dfa-window")
            dfa_until = None
            last_stop = "dfa"
            last_stop_id = "accrual.dfa-window"

        if pending_reclose is not None and day > pending_reclose and active_since is not None:
            # Close the filled season. Do not invent an option the file does not have.
            close_service(pending_reclose, None)
            last_stop = "other"
            last_stop_id = "stop.option"
            pending_reclose = None

        if day in restart_days and (floor is None or day >= floor) and active_since is None:
            season_end = calendar.window(day.year, club_on(day), club_on)[1]
            reopen = rulebook_limits.opening_restart(day, season_end, rows, grouped, appearance_counts, appearance_dates, _returns_to_roster, lambda raw: _mlb_present(row_ids(raw)))
            if reopen and (on_option or last_stop_id in RESTARTABLE_STOPS):
                if on_option:
                    end_option(day - dt.timedelta(days=1))
                open_service(day, "accrual.appearance-restart")
                pending_reclose = season_end

        if (
            not presumed
            and floor is not None
            and day >= floor
            and active_since is None
            and last_stop is None
        ):
            if on_option and option_start is not None and option_start < floor:
                end_option(floor - dt.timedelta(days=1))
            if not on_option:
                rule = "accrual.pre-debut-roster" if debut is not None and floor < debut else "accrual.presume-from-debut"
                open_service(floor if day > floor else day, rule)
                presumed = True
        elif floor is not None and day >= floor:
            presumed = True

        items = grouped.get(day, [])
        season_row = calendar.seasons.get(day.year)
        cut = rulebook_limits.restricted_cut(items, rows, day, None if season_row is None else season_row.championship_start, active_since)
        if cut is not None:
            close_service(cut, "stop.restricted")
            last_stop, last_stop_id = "other", "stop.restricted"
        hits = [hit for _row, hit in items if hit is not None]
        if not hits and day != floor:
            continue
        tags = set()
        for hit in hits:
            tags |= set(hit.tags)
        ids = {hit.id for hit in hits}
        has_dfa = any("dfa" in hit.tags for hit in hits)
        has_trade = any("trade" in hit.tags for hit in hits)
        has_claim = any("claim" in hit.tags for hit in hits)
        has_option = any("option" in hit.tags for hit in hits)
        has_restart = has_trade or has_claim or bool(ids & {
            "start.mlb-signing", "start.mlb-contract-signing", "start.selected",
            "start.purchased", "start.rule5",
        })
        terminal = _terminal(hits)
        service_day = floor is None or day >= floor

        if has_dfa:
            dfa_rule = next(hit.id for hit in hits if "dfa" in hit.tags)

        # Designation resolved the same day by a trade, claim, or new deal.
        dfa_restart = has_dfa and has_restart and not terminal
        # A claim, trade, or return to the active list on a later day also
        # ends the designation. The window must not keep running and then
        # close him out after he is already back.
        if (
            service_day
            and dfa_until is not None
            and not has_dfa
            and not terminal
            and (has_restart or any(_opens(hit) for hit in hits))
        ):
            dfa_until = None
            span_rules.add("accrual.trade-resolves-dfa")
            if active_since is None:
                open_service(day, "accrual.trade-resolves-dfa")
        if service_day and dfa_restart:
            if active_since is None:
                open_service(day, "accrual.trade-resolves-dfa")
            else:
                span_rules.add("accrual.trade-resolves-dfa")
                span_rules.add(dfa_rule)
            dfa_until = None
            last_stop = None
            if on_option:
                end_option(day - dt.timedelta(days=1))
        elif service_day and terminal:
            stop_id = next(
                (hit.id for hit in hits if hit.effect == "stop" or "free-agency" in hit.tags),
                "stop",
            )
            if active_since is not None:
                close_service(day - dt.timedelta(days=1), stop_id)
            dfa_until = None
            last_stop = "other"
            last_stop_id = stop_id
            pending_reclose = None
            def _hit_from_mlb(wanted: str) -> bool:
                for raw, hit in items:
                    if hit is not None and hit.id == wanted and _mlb_present(row_ids(raw)):
                        return True
                return False

            if any(hit.id in BLOCKING_STOPS and _hit_from_mlb(hit.id) for hit in hits):
                boundaries.append({"date": day.isoformat(), "kind": "block", "rule": stop_id})
            # A new major-league contract, including one that options him the
            # same day, ends the prior free-agency block. An option by one of
            # the 30 does too: the club could not option him unless he was
            # under contract. The day itself stays off the active list.
            deal = next((hit.id for hit in hits if hit.id in {
                "start.mlb-signing", "start.mlb-contract-signing", "start.selected",
                "start.purchased", "start.rule5",
            }), None)
            if deal is None and has_option and _hit_from_mlb("stop.option"):
                deal = "stop.option"
            if deal is not None:
                boundaries.append({"date": day.isoformat(), "kind": "start", "rule": deal})
        elif service_day and has_dfa and active_since is not None and not terminal:
            window, window_id = _dfa_days(day.year, book, ruleset)
            limit = day + dt.timedelta(days=window - 1)
            opener = calendar.next_opener_after(day, club_on(day))
            # A spring designation earns Opening Day only when he was not
            # already optioned and a claim, trade, or injured-list placement
            # has him on a major-league roster by that morning.
            if opener is not None and day < opener and (on_option or not rulebook_limits.spring_kept(rows, day, opener)):
                limit = min(limit, opener - dt.timedelta(days=1))
            elif opener is not None and day < opener and limit < opener:
                limit = opener
                span_rules.add("accrual.spring-dfa-rostered")
            dfa_until = limit
            span_rules.add(dfa_rule)
            span_rules.add(window_id)
        elif has_dfa and active_since is None and not terminal and not dfa_restart:
            last_stop = "dfa"
            last_stop_id = dfa_rule
        elif service_day and active_since is None and last_stop == "dfa" and has_restart and not terminal:
            open_service(day, "accrual.trade-resolves-dfa")
        elif service_day and active_since is None:
            openers = [hit for hit in hits if _opens(hit)]
            if openers:
                start_day = day
                if "il" in openers[0].tags:
                    raw = next(row for row, hit in items if hit is not None and hit.id == openers[0].id)
                    start_day = _retro_start(raw, day)
                    if floor is not None:
                        start_day = max(start_day, floor)
                open_service(start_day, openers[0].id)
            elif any(hit.id == "neutral.mlb-assignment" for hit in hits):
                later = dates[index + 1:]
                if _lookahead_confirms(later, grouped):
                    open_service(day, "start.assignment-confirmed")
        elif service_day and active_since is not None:
            for hit in hits:
                if hit.effect == "count_away":
                    span_rules.add(hit.id)

        # Option tally uses the same hits, including before the service floor.
        if has_option and not dfa_restart:
            begin_option(day)
        elif on_option and (dfa_restart or (service_day and active_since == day and not has_dfa)):
            end_option(day - dt.timedelta(days=1))
        elif on_option and has_dfa and not terminal and not dfa_restart:
            end_option(day)
        elif on_option and terminal and "minors" not in tags:
            # A move from one affiliate to another does not end the optional
            # assignment. He is still optioned. Section 3.
            # Outright and designation days count. A release or free-agency day does not.
            inclusive = "stop.outright" in ids or has_dfa
            end_option(day if inclusive else day - dt.timedelta(days=1))
        elif on_option and any(_opens(hit) and "dfa" not in hit.tags for hit in hits):
            end_option(day - dt.timedelta(days=1))

        # Injured-list spells. A minor-league list is not tag il.
        placements = [hit for hit in hits if "il" in hit.tags and "il-transfer" not in hit.tags and hit.effect == "count_away"]
        transfers = [hit for hit in hits if "il-transfer" in hit.tags]
        if placements and (active_since is not None or (service_day and any(_opens(hit) for hit in placements))):
            if il_open is None:
                raw = next(row for row, hit in items if hit is not None and "il" in hit.tags and "il-transfer" not in hit.tags)
                il_open = (_retro_start(raw, day), placements[0].id)
        elif il_open is not None and not transfers and (
            any(hit.id in ("start.activated", "start.reinstated-code") for hit in hits)
            or terminal
            or has_option
        ):
            close_il(day - dt.timedelta(days=1))

    if active_since is not None:
        end = horizon
        if dfa_until is not None:
            end = min(end, dfa_until)
            span_rules.add("accrual.dfa-window")
        close_service(end, None)
    if on_option and option_start is not None:
        option_spells.append((option_start, horizon))
    if il_open is not None:
        il_spells.append((il_open[0], horizon, il_open[1]))

    # Restricted-list rows with no sourced exception stay stops and are flagged.
    for day, row, hit in rows:
        if hit is None or "ambiguous" not in hit.tags:
            continue
        covered = any(item["start"] <= day <= item["end"] for item in exceptions)
        if not covered:
            ambiguous.append({
                "player_id": player_id,
                "name": name,
                "date": day.isoformat(),
                "type_code": row.get("typeCode") or "",
                "description": (row.get("description") or "")[:240],
                "rule_id": hit.id,
                "on_40_man": bool(on_40_man),
                "severity": "warning",
                "reason": "Restricted-list wording is shared. No sourced exception covers this date, so the day does not count.",
            })

    for item in exceptions:
        rule = f"exception.{item['kind']}"
        lo = item["start"]
        hi = item["end"]
        if floor is not None:
            lo = max(lo, floor)
        hi = min(hi, horizon)
        if lo > hi:
            continue
        if item["counts"]:
            intervals.append((lo, hi, frozenset({rule})))
        else:
            intervals = _subtract(intervals, lo, hi)
    intervals = _merge(intervals)

    # Overseas-opener extras (Tokyo 2019 and 2012) earn nothing until the
    # domestic opener. The row itself is neutral, so the clock resumes.
    seen_holes = set()
    for day, _row, hit in rows:
        if hit is None or "series-inactive" not in hit.tags:
            continue
        season = calendar.seasons.get(day.year)
        if season is None or season.championship_start >= season.first_domestic:
            continue
        key = (season.championship_start, season.first_domestic - dt.timedelta(days=1))
        if key in seen_holes:
            continue
        seen_holes.add(key)
        intervals = _subtract(intervals, key[0], key[1])
    if seen_holes:
        intervals = _merge(intervals)

    appearance_review: list[dict] = []
    if appearance_dates:
        intervals, appearance_review = _apply_appearance_dates(
            intervals, rows, appearance_dates, floor, horizon
        )

    # Seasons.
    if career_end is not None and not on_40_man:
        last_year = career_end.year
    else:
        last_year = horizon.year
    first_year = None
    if debut is not None:
        first_year = debut.year
    if rows:
        first_txn_year = rows[0][0].year
        first_year = first_txn_year if first_year is None else min(first_year, first_txn_year)
    if first_year is None:
        first_year = last_year
    years = [year for year in range(first_year, last_year + 1) if year in calendar.seasons]
    txn_years = {day.year for day, _row, _hit in rows}

    seasons = []
    review_flags = []
    total = 0
    all_ranges = []
    option_days: dict[int, int] = {}
    for year in years:
        start, end = calendar.window(year, club_on(calendar.seasons[year].championship_start), club_on)
        pieces = []
        for iv_start, iv_end, rules in intervals:
            lo = max(iv_start, start)
            hi = min(iv_end, end, horizon)
            if hi >= lo:
                pieces.append((lo, hi, rules))
        roster_days = sum(_span_days(lo, hi) for lo, hi, _rules in pieces)

        opt = 0
        opt_pieces = []
        for spell_start, spell_end in option_spells:
            lo = max(spell_start, start)
            hi = min(spell_end, end, horizon)
            if hi >= lo:
                opt += _span_days(lo, hi)
                opt_pieces.append((lo, hi))
        if opt:
            option_days[year] = opt

        # Under-20 credit. Section 2. Not applied when he never reached the
        # active list at all this season (the written exception for a player
        # with no service before the option who is then outrighted or released).
        credit = 0
        credit_pieces = []
        if roster_days > 0 and 0 < opt < option_threshold:
            covered = []
            for lo, hi, _rules in pieces:
                covered.append((lo, hi))
            for lo, hi in opt_pieces:
                # Drop days already inside a service range.
                remaining = [(lo, hi)]
                for cov_lo, cov_hi in covered:
                    nxt = []
                    for a, b in remaining:
                        if cov_hi < a or cov_lo > b:
                            nxt.append((a, b))
                            continue
                        if a < cov_lo:
                            nxt.append((a, cov_lo - dt.timedelta(days=1)))
                        if b > cov_hi:
                            nxt.append((cov_hi + dt.timedelta(days=1), b))
                    remaining = [(a, b) for a, b in nxt if b >= a]
                for a, b in remaining:
                    credit += _span_days(a, b)
                    credit_pieces.append((a, b, frozenset({"accrual.option-under-20"})))
        raw = roster_days + credit
        if (
            appearance_years is not None
            and raw > 0
            and year not in txn_years
            and year not in appearance_years
        ):
            raw = 0
            pieces = []
            credit = 0
            credit_pieces = []
        # 1996 settlement: 75 wiped days. The 52/23 split is an assumption.
        # Only a player still accruing on the key date gets that year's share,
        # and the ordinary cap still applies.
        if year == 1994 and any(lo <= STRIKE_1994_LAST <= hi for lo, hi, _rules in pieces):
            extra_start = STRIKE_1994_LAST + dt.timedelta(days=1)
            extra_end = extra_start + dt.timedelta(days=STRIKE_1994_DAYS - 1)
            raw += STRIKE_1994_DAYS
            credit_pieces.append((extra_start, extra_end, frozenset({"accrual.strike-service-1996"})))
        elif year == 1995:
            opener_1995 = calendar.seasons[1995].championship_start
            if any(lo <= opener_1995 <= hi for lo, hi, _rules in pieces):
                extra_end = opener_1995 - dt.timedelta(days=1)
                extra_start = opener_1995 - dt.timedelta(days=STRIKE_1995_DAYS)
                raw += STRIKE_1995_DAYS
                credit_pieces.append((extra_start, extra_end, frozenset({"accrual.strike-service-1996"})))
        prorated = _prorate(year, raw, start, end, ruleset)
        credited = min(prorated, cap)
        season_rules = sorted({rule for _lo, _hi, rules in pieces for rule in rules})
        if credit:
            season_rules = sorted(set(season_rules) | {"accrual.option-under-20"})
        if any("accrual.strike-service-1996" in rules for _lo, _hi, rules in credit_pieces):
            season_rules = sorted(set(season_rules) | {"accrual.strike-service-1996"})
        if year in roy_years:
            credited = max(credited, full_year)
            credited = min(credited, cap)
            if "accrual.roy-full-year" not in season_rules:
                season_rules.append("accrual.roy-full-year")
        cal_row = calendar.seasons[year]
        if start < cal_row.first_domestic:
            season_rules.append("accrual.international-opener")
        else:
            season_rules.append("accrual.league-opener")
        if year <= 2021 and end > cal_row.championship_end:
            season_rules.append("accrual.tiebreaker-through-2021")
        if year == 2020 and "accrual.shortened-2020" not in season_rules:
            season_rules.append("accrual.shortened-2020")
        if year == 2022 and "accrual.lockout-2022" not in season_rules:
            season_rules.append("accrual.lockout-2022")
        if "accrual.dfa-window-pre2012" in season_rules:
            review_flags.append({
                "year": year,
                "rule": "accrual.dfa-window-pre2012",
                "reason": "This season uses the pre-2012 designation window, and that length is unknown.",
            })
        total += credited
        ranged = []
        for lo, hi, rules in pieces + credit_pieces:
            item = {
                "start": lo.isoformat(),
                "end": hi.isoformat(),
                "days": _span_days(lo, hi),
                "rules": sorted(rules),
            }
            ranged.append(item)
            all_ranges.append(item)
        if raw == 0 and opt == 0 and credited == 0:
            continue
        events, carried_in = rulebook_limits.season_events(rows, start, end, pieces)
        seasons.append({
            "year": year,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "roster_days": roster_days,
            "option_days": opt,
            "option_service_credit": credit,
            "raw_days": raw,
            "prorated_days": prorated,
            "credited_days": credited,
            "rules": season_rules,
            "events": events,
            "carried_in": carried_in,
            "ranges": ranged,
        })

    used = [year for year, days in sorted(option_days.items()) if days >= option_threshold]
    saved = [year for year, days in sorted(option_days.items()) if 0 < days < option_threshold]
    notable_rules = sorted({
        hit.id for _day, _row, hit in rows
        if hit is not None and hit.id in {
            "neutral.all-star",
            "neutral.winter-league",
            "neutral.national-team",
            "stop.taxi-squad",
            "count.suspension",
            "count.admin-leave",
            "count.military",
            "count.family-medical",
            "stop.restricted",
            "count.dfa",
            "stop.free-agency",
        }
    })
    il_out = [
        {"start": a.isoformat(), "end": b.isoformat(), "days": _span_days(a, b), "rule": rule}
        for a, b, rule in il_spells
        if b >= a
    ]
    result = {
        "player_id": player_id,
        "name": name,
        "rules_version": RULEBOOK_VERSION,
        "on_40_man": bool(on_40_man),
        "service_time": _format(total, full_year),
        "service_days": total,
        "seasons": seasons,
        "ranges": all_ranges,
        "option_years_used": used,
        "option_years_saved": saved,
        "option_days": {str(year): days for year, days in sorted(option_days.items())},
        "il_ranges": il_out,
        "notable_rules": notable_rules,
        "unknowns": unknowns,
        "ambiguous": ambiguous,
        "boundaries": boundaries,
        "appearance_review": appearance_review,
        "review_flags": review_flags,
    }
    result["sanity"] = sanity_errors(result, cap=cap, full_year=full_year, ruleset=ruleset)
    return result


def sanity_errors(result: dict, cap: int | None = None, full_year: int | None = None, ruleset: cba.Ruleset | None = None) -> list[str]:
    """Accounting checks. Empty means the player's ranges obey the rulebook."""
    ruleset = ruleset or cba.default()
    if cap is None:
        cap = int(ruleset.require("service_time.max_days_creditable_per_season"))
    errors = []
    seasons = result["seasons"]
    if result["service_days"] < 0:
        errors.append("negative total")
    if sum(season["credited_days"] for season in seasons) != result["service_days"]:
        errors.append("season days do not add up to the total")
    for season in seasons:
        if season["credited_days"] < 0 or season["raw_days"] < 0:
            errors.append(f"{season['year']} has negative days")
        if season["credited_days"] > cap:
            errors.append(f"{season['year']} is over the season cap")
        ranges = season["ranges"]
        ordered = sorted(ranges, key=lambda item: item["start"])
        for left, right in zip(ordered, ordered[1:]):
            if right["start"] <= left["end"]:
                errors.append(f"{season['year']} has overlapping ranges")
                break
        piece_days = sum(item["days"] for item in ranges)
        if piece_days != season["raw_days"]:
            errors.append(f"{season['year']} ranges do not add up to the raw days")
        start = dt.date.fromisoformat(season["start"])
        end = dt.date.fromisoformat(season["end"])
        expected = _prorate(season["year"], season["raw_days"], start, end, ruleset)
        expected = min(expected, cap)
        if "accrual.roy-full-year" in season["rules"]:
            expected = min(cap, max(expected, int(ruleset.require("service_time.days_per_credited_year"))))
        if season["credited_days"] != expected:
            errors.append(f"{season['year']} credited days do not follow the cap and the 2020 scale")
    # No credited day on or after a free-agency election or a release, until a
    # later start. The stop day itself is already excluded by the walk.
    blocked_from = None
    spans = []
    events = sorted(
        result.get("boundaries") or [],
        key=lambda event: (event["date"], 0 if event["kind"] == "block" else 1),
    )
    for event in events:
        if event.get("kind") == "block":
            if blocked_from is None:
                blocked_from = dt.date.fromisoformat(event["date"])
        elif event.get("kind") == "start" and blocked_from is not None:
            started = dt.date.fromisoformat(event["date"])
            if started > blocked_from:
                spans.append((blocked_from, started - dt.timedelta(days=1)))
                blocked_from = None
    if blocked_from is not None:
        spans.append((blocked_from, dt.date.max))
    for season in seasons:
        for item in season["ranges"]:
            if any(str(rule).startswith("exception.") for rule in item.get("rules") or []):
                continue
            start = dt.date.fromisoformat(item["start"])
            end = dt.date.fromisoformat(item["end"])
            for lo, hi in spans:
                if start <= hi and end >= lo:
                    errors.append(
                        f"{season['year']} credits service after a release or free agency ({item['start']})"
                    )
                    break
    return errors


def assert_publishable(result: dict) -> None:
    """Raise when a current 40-man player has an unclassified transaction.

    Alumni unknowns are warnings on the result and do not raise.
    """
    if result.get("on_40_man") and result.get("unknowns"):
        first = result["unknowns"][0]
        raise PublishBlocked(
            f"player {result.get('player_id')} is on the 40-man and has an "
            f"unclassified transaction on {first.get('date')}: {first.get('type_code')} "
            f"{first.get('description')}"
        )


def canonical_json(result: dict) -> str:
    """Stable text for the determinism check. Key order does not matter."""
    return json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
