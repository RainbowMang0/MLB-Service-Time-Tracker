"""Limits on opener restarts and on a missing restricted-list placement.

A season of ten games or fewer does not open at the league opener.
A documented recall or selection is not preceded by invented days.
An activation after a trade does not block a regular who was already up.
"""

from __future__ import annotations

import datetime as dt

def spring_kept(rows, dfa_day: dt.date, opener: dt.date) -> bool:
    """A non-optioned spring designation is back by Opening Day."""
    status = None
    for day, _raw, hit in rows:
        if hit is None or day <= dfa_day or day > opener:
            continue
        if hit.id in {"stop.option", "stop.outright", "stop.released", "stop.free-agency"}:
            status = "out"
        elif (
            hit.id in {"neutral.trade", "neutral.claim"}
            or "trade" in hit.tags or "claim" in hit.tags
            or hit.effect == "start"
            or ("il" in hit.tags and hit.effect == "count_away")
        ):
            status = "kept"
    return status == "kept"


def option_spells_to_credit(opt, opt_pieces, service_pieces, threshold, roster_days):
    """Credit the season's optional assignment only when its total is under the threshold."""
    if roster_days <= 0 or not opt_pieces:
        return []
    if 0 < opt < threshold:
        return list(opt_pieces)
    return []


BLOCKING_RETURNS = {"start.recalled", "start.selected", "start.purchased", "start.rule5"}
ACTIVATION = {"start.activated", "start.reinstated-code"}
# A later signing, claim, or trade starts the clock on that date. It is not
# cancelled by an option that proves he was up for a recall.
HARD_ENTRY = {
    "start.mlb-signing", "start.mlb-contract-signing",
    "neutral.claim",
    "start.selected", "start.purchased", "start.rule5",
}
ENTRY_OPEN = ("neutral.trade", "neutral.claim")


def opening_restart(day, season_end, rows, grouped, appearance_counts, appearance_dates, returns, mlb_option) -> bool:
    """True when this opener should start service for a player who was optioned."""
    played = None if appearance_counts is None else appearance_counts.get(day.year)
    year_games = [item for item in (appearance_dates or []) if item.year == day.year]
    cup = played is not None and played <= 10
    if year_games and min(year_games) > day + dt.timedelta(days=6):
        cup = True
    if cup:
        return False
    if any(hit is not None and returns(hit) for _raw, hit in grouped.get(day, [])):
        return False
    blocking = set(BLOCKING_RETURNS)
    if played is None or played <= 10:
        blocking |= ACTIVATION
    later_return = False
    proven_up = False
    for later, raw, later_hit in rows:
        if later <= day:
            continue
        if later > season_end:
            break
        if later_hit is None:
            continue
        if later_hit.id in HARD_ENTRY:
            return False
        if later_hit.id == "neutral.trade" and trade_blocks_restart(rows, day, later, appearance_counts):
            return False
        if later_hit.id == "stop.option" and mlb_option(raw) and not later_return:
            proven_up = True
        if later_hit.id in blocking:
            later_return = True
            break
    return (not later_return) or bool(proven_up)


def trade_blocks_restart(rows, opener, trade_day, appearance_counts) -> bool:
    """A trade blocks Opening Day only when he was still optioned or in the minors.

    A full season he played after that assignment means the trade moved
    someone who was already up. It does not erase the opener.
    """
    last_option = None
    for row_day, _raw, hit in rows:
        if hit is None or row_day >= trade_day:
            break
        if hit.id in {"stop.option", "stop.assigned-minors", "stop.reassigned-minors"}:
            last_option = row_day
        elif hit.effect == "start" and last_option is not None and row_day > last_option:
            last_option = None
    if last_option is None:
        return False
    if appearance_counts:
        for year, games in appearance_counts.items():
            if last_option.year < int(year) < opener.year and games >= 20:
                return False
    return True


def _stopped_next_day(rows, day) -> bool:
    nxt = day + dt.timedelta(days=1)
    for later, _raw, hit in rows:
        if later < day or hit is None:
            continue
        if later > nxt:
            break
        if hit.id in {"stop.option", "stop.outright", "stop.released", "stop.free-agency"}:
            return True
    return False


def roster_entry(hits, items, mlb_present, has_option, terminal, rows=None, day=None):
    """A trade or claim onto a major-league club starts service that day.

    An option or outright the same day, or the next day, means he is not
    on the active roster.
    """
    if has_option or terminal:
        return None
    if day is not None and rows and _stopped_next_day(rows, day):
        return None
    for hit in hits:
        if hit.id not in ENTRY_OPEN:
            continue
        if any(mlb_present(raw) for raw, other in items if other is hit):
            return hit.id
    return None


def extend_open_spans(spans, stop_dates, calendar, horizon):
    """A stint with no later stop runs through the championship end."""
    extended = []
    for start, end in spans:
        season = calendar.seasons.get(start.year) if calendar is not None else None
        cap = None if season is None else season.championship_end
        if cap is not None and horizon is not None and horizon < cap:
            cap = horizon
        if cap is not None and cap > end and not any(end < stop <= cap for stop in stop_dates):
            end = cap
        extended.append((start, end))
    return extended


def restricted_cut(items, rows, day, opener, active_since):
    """Day to close service when a restricted-list return has no placement."""
    if not any("from the restricted list" in (raw.get("description") or "").lower() for raw, _hit in items):
        return None
    if opener is None or active_since is None or active_since >= day:
        return None
    for prev_day, _raw, prev_hit in rows:
        if prev_day >= day:
            break
        if prev_hit is not None and prev_hit.id == "stop.restricted" and prev_day >= opener:
            return None
    cut = opener - dt.timedelta(days=1) if active_since < opener else day - dt.timedelta(days=1)
    return cut if cut >= active_since else None


def season_events(rows, start, end, pieces) -> tuple[list[dict], bool]:
    noise = {
        "neutral.number-change", "neutral.roster-status", "neutral.winter-league",
        "neutral.national-team", "neutral.rehab", "neutral.spring-invite",
    }
    events = []
    for ev_day, _row, hit in rows:
        if hit is None or ev_day < start or ev_day > end or hit.id in noise:
            continue
        events.append({"date": ev_day.isoformat(), "rule": hit.id})
    carried = any(lo <= start <= hi for lo, hi, _rules in pieces) and not any(
        ev["date"] == start.isoformat() and str(ev["rule"]).startswith("start.") for ev in events
    )
    return events, carried
