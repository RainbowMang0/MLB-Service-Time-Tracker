"""Who on a 40-man roster can be listed as out of options.

A season is an option year when the player spent at least the ruleset's
day threshold on optional assignment. The transaction count and the
rulebook replay each apply that rule to stored rows. This module publishes
a player only when both name the same seasons.

The rulebook reads data/raw_transactions. The transaction count reads the
roster cache. A career that starts before the saved rows, a missing file,
or two different season lists is left off. Publishing one of those totals
would be a guess.

The standard number of option years comes from the ruleset. A player who
has used exactly that many may still have a fourth year. That exception
is not shown as an option left: fourth_option.py only decides whether to
label him. A player who already used four keeps every year and stays out.

Refusing an outright after five years of service is a different rule. It
is not decided here.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field

import cba
import fourth_option
import options as option_years
import player_sections
from il_stints import SeasonWindows
from mlb_clubs import CLUB_ABBR

ROOT_RAW = player_sections.ROOT / "data" / "raw_transactions"

REASON_NO_RAW = "no raw transactions"
REASON_NO_CACHE = "no transaction cache"
REASON_INCOMPLETE = "record does not cover the start of the career"
REASON_REPLAY = "rulebook replay failed"
REASON_DISAGREE = "option seasons disagree"

_WINDOWS: SeasonWindows | None = None
_INDEX: dict[int, str] | None = None


@dataclass
class Listed:
    player: dict
    years: tuple[int, ...]


@dataclass
class Omitted:
    player_id: int
    name: str
    team: str
    reason: str
    cache_years: tuple[int, ...] = ()
    rulebook_years: tuple[int, ...] = ()


@dataclass
class RosterOptions:
    out: list[Listed] = field(default_factory=list)
    one: list[Listed] = field(default_factory=list)
    omitted: list[Omitted] = field(default_factory=list)
    # Used three option years. A fourth year may exist. It is labeled,
    # not shown as an option still left.
    may_fourth: list[Listed] = field(default_factory=list)
    standard: int = 3
    index: dict[int, str] = field(default_factory=dict)


def as_date(generated_at: str | None) -> dt.date:
    text = (generated_at or "")[:10]
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return dt.date.today()


def _windows() -> SeasonWindows:
    global _WINDOWS
    if _WINDOWS is None:
        _WINDOWS = SeasonWindows.load()
    return _WINDOWS


def _default_cache(player: dict):
    return player_sections.load_cached_transactions(player.get("id"))


def _default_raw(player: dict):
    pid = player.get("id")
    path = ROOT_RAW / f"{pid}.json"
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rows = doc.get("transactions") if isinstance(doc, dict) else None
    return rows if isinstance(rows, list) else None


def _default_counter(player: dict, rows, today: dt.date):
    return option_years.summarize(player, rows, _windows(), today)


def _default_replay(player: dict, today: dt.date) -> dict:
    from rulebook_publish import replay_one

    cleaned = player
    if player.get("mlb_debut") in ("—", "") or player.get("last_played") in ("—", ""):
        cleaned = dict(player)
        if cleaned.get("mlb_debut") in ("—", ""):
            cleaned["mlb_debut"] = None
        if cleaned.get("last_played") in ("—", ""):
            cleaned["last_played"] = None
    return replay_one(cleaned, today)


def _years(values) -> tuple[int, ...]:
    found = []
    for value in values or []:
        try:
            found.append(int(value))
        except (TypeError, ValueError):
            continue
    return tuple(sorted(found))


def _omit(result: RosterOptions, player: dict, reason: str, cache=(), rulebook=()) -> None:
    pid = player.get("id")
    result.omitted.append(Omitted(
        player_id=pid if isinstance(pid, int) else -1,
        name=player.get("name") or "",
        team=player.get("team") or "",
        reason=reason,
        cache_years=_years(cache),
        rulebook_years=_years(rulebook),
    ))
    if isinstance(pid, int):
        result.index[pid] = "omit"


def select(
    players: list[dict],
    today: dt.date,
    *,
    cache_for=None,
    raw_for=None,
    counter=None,
    replay=None,
    standard: int | None = None,
    apply_fourth: bool = True,
) -> RosterOptions:
    """Split rostered players into out, one year left, and left off.

    `cache_for`, `raw_for`, `counter`, and `replay` exist so a test can
    stand in for the files. The defaults read the stored rows.
    """
    cache_for = cache_for or _default_cache
    raw_for = raw_for or _default_raw
    counter = counter or _default_counter
    replay = replay or _default_replay
    if standard is None:
        standard = int(cba.default().require("options.standard_option_years"))
    result = RosterOptions(standard=int(standard))

    for player in players:
        if not player.get("on_40_man"):
            continue
        pid = player.get("id")
        raw_rows = raw_for(player)
        if raw_rows is None:
            _omit(result, player, REASON_NO_RAW)
            continue
        rows = cache_for(player)
        if rows is None:
            _omit(result, player, REASON_NO_CACHE)
            continue
        count = counter(player, rows, today)
        cache_years = _years(getattr(count, "used_years", ()))
        if (
            getattr(count, "incomplete", False)
            or getattr(count, "remaining", None) is None
            or getattr(count, "out_of_options", None) is None
        ):
            _omit(result, player, REASON_INCOMPLETE, cache_years)
            continue
        try:
            replayed = replay(player, today) or {}
        except Exception as exc:
            _omit(result, player, f"{REASON_REPLAY} ({type(exc).__name__})", cache_years)
            continue
        rulebook_years = _years(replayed.get("option_years_used"))
        if cache_years != rulebook_years:
            _omit(result, player, REASON_DISAGREE, cache_years, rulebook_years)
            continue
        listed = Listed(player=player, years=cache_years)
        if len(cache_years) > result.standard:
            # Already used four or more. Do not take a year off.
            result.out.append(listed)
            status = "out"
        elif len(cache_years) == result.standard:
            decision = (
                fourth_option.decide(raw_rows, cache_years, today, _windows())
                if apply_fourth else None
            )
            # A reviewed hold stays off the out list even when the day count
            # would call the season settled.
            if decision is not None and isinstance(pid, int) and fourth_option.held(pid):
                decision = fourth_option.FourthOption(
                    fourth_option.UNKNOWN, decision.signed, decision.possible, decision.full
                )
            if decision is not None and decision.decision in (
                fourth_option.YES, fourth_option.UNKNOWN,
            ):
                result.may_fourth.append(listed)
                status = "fourth"
            else:
                result.out.append(listed)
                status = "out"
        elif result.standard - len(cache_years) == 1:
            result.one.append(listed)
            status = "one"
        else:
            status = "other"
        if isinstance(pid, int):
            result.index[pid] = status

    result.omitted.sort(key=lambda row: (row.name, row.player_id))
    result.may_fourth.sort(key=lambda row: (row.player.get("name") or "", row.player.get("id") or 0))
    return result


def prepare(players: list[dict], today: dt.date) -> RosterOptions:
    """Remember the roster just classified, for the club-page count."""
    global _INDEX
    result = select(players, today)
    _INDEX = result.index
    return result


def clear() -> None:
    global _INDEX
    _INDEX = None


def _fallback(players: list[dict], option_counts) -> int:
    if not option_counts:
        return 0
    total = 0
    for player in players:
        count = option_counts.get(player.get("id"))
        if count is not None and getattr(count, "out_of_options", False):
            total += 1
    return total


def roster_options_left(player: dict):
    """Options-left cell while a roster classification is prepared.

    ``"defer"`` means the caller should use the transaction count.
    None means a fourth year is not counted, so the cell is a dash.
    """
    if _INDEX is None:
        return "defer"
    status = _INDEX.get(player.get("id"))
    if status == "fourth":
        return None
    if status == "one":
        return 1
    if status == "out":
        return 0
    return "defer"


def count_for(players: list[dict], option_counts) -> int:
    """Out-of-options total for this club.

    After prepare(), the total is the published list. A caller that has
    not prepared, or that asks about players from outside that roster,
    gets the transaction-count total instead.
    """
    if _INDEX is None:
        return _fallback(players, option_counts)
    ids = []
    for player in players:
        pid = player.get("id")
        if pid not in _INDEX:
            return _fallback(players, option_counts)
        ids.append(pid)
    return sum(1 for pid in ids if _INDEX[pid] == "out")


def club_names() -> list[str]:
    return [name for name, _abbr in CLUB_ABBR]


def _by_team(rows: list[Listed]) -> dict[str, int]:
    found: dict[str, int] = {}
    for row in rows:
        team = row.player.get("team") or ""
        found[team] = found.get(team, 0) + 1
    return found


def veterans(result: RosterOptions) -> int:
    """Players out of options who already have five credited years.

    Five years is the outright-refusal line. It is counted here only so a
    log can say the pages did not apply it.
    """
    full = int(cba.default().require("service_time.days_per_credited_year"))
    floor = 5 * full
    return sum(
        1 for row in result.out
        if int(row.player.get("service_days_total") or 0) >= floor
    )


def report(result: RosterOptions) -> str:
    """Plain-text counts for the operator. Not a page."""
    out_n = _by_team(result.out)
    one_n = _by_team(result.one)
    lines = [
        f"Out of options: {len(result.out)}",
        f"One option year left: {len(result.one)}",
        f"Left off: {len(result.omitted)}",
        f"May have a fourth option: {len(result.may_fourth)}",
        (
            f"{veterans(result)} of the players out of options already have "
            "five or more years of service. Refusing an outright is not marked."
        ),
        "By club:",
    ]
    names = club_names()
    seen = set(names)
    for name in names:
        lines.append(f"  {name}: {out_n.get(name, 0)} out, {one_n.get(name, 0)} with one left")
    extra = sorted((set(out_n) | set(one_n)) - seen)
    for name in extra:
        label = name or "(no club)"
        lines.append(f"  {label}: {out_n.get(name, 0)} out, {one_n.get(name, 0)} with one left")
    if result.may_fourth:
        lines.append("May have a fourth option:")
        for row in result.may_fourth:
            seasons = ", ".join(str(year) for year in row.years)
            lines.append(f"  {row.player.get('name')} | {row.player.get('team')} | {seasons}")
    if result.omitted:
        lines.append("Left off:")
        for row in result.omitted:
            cache = ", ".join(str(year) for year in row.cache_years) or "none"
            rulebook = ", ".join(str(year) for year in row.rulebook_years) or "none"
            lines.append(
                f"  {row.name} | {row.team} | {row.reason} | "
                f"transaction count {cache} | rulebook {rulebook}"
            )
    return "\n".join(lines) + "\n"
