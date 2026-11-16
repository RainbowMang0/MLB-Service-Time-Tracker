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


def _optioned_this_spring(rows, opener, mlb_option) -> bool:
    """An optional assignment this spring is still in effect on Opening Day.

    An option from a previous season is not this. A recall or an assignment
    onto the club before the opener clears it.
    """
    optioned = False
    for day, raw, hit in rows:
        if day >= opener:
            break
        if hit is None or day.year != opener.year:
            continue
        if hit.id == "stop.option" and mlb_option(raw):
            optioned = True
        elif optioned and (hit.effect == "start" or hit.id == "neutral.mlb-assignment"):
            optioned = False
    return optioned


def opening_restart(day, season_end, rows, grouped, appearance_counts, appearance_dates, returns, mlb_option, checked_years=None) -> bool:
    """True when this opener should start service for a player who was optioned."""
    played = None if appearance_counts is None else appearance_counts.get(day.year)
    year_games = [item for item in (appearance_dates or []) if item.year == day.year]
    if checked_years is not None and day.year in checked_years and not year_games:
        return False
    cup = played is not None and played <= 10
    if year_games and min(year_games) > day + dt.timedelta(days=6):
        cup = True
    if cup:
        return False
    # Optioned this spring: the season does not start at the opener. A later
    # option is the send-down after he came up, not proof he was up on Opening Day.
    if _optioned_this_spring(rows, day, mlb_option):
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
        # An assignment onto the club is the entry. A later option does not
        # reach back and prove he was on the opening-day roster.
        if later_hit.id == "neutral.mlb-assignment" and not proven_up:
            later_return = True
            break
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


# An option, outright, or release ends a game-date stint even when it falls
# on the game date. Clubs usually make that move the day he appeared.
HARD_STOPS = {"stop.option", "stop.outright", "stop.released"}
RETURN_ROWS = {
    "start.recalled", "start.selected", "start.activated", "start.reinstated-code",
    "start.mlb-signing", "start.mlb-contract-signing", "start.purchased", "start.rule5",
    "neutral.trade", "neutral.mlb-assignment",
}


def game_spans(dates, rows):
    """Consecutive games are one stint. A stop, or a new season, splits them.

    An option, outright, or release on a game date ends that stint the day
    before. The next game can start a new stint. The days in between are
    not filled in.
    """
    if not dates:
        return []
    stops = []
    hard = set()
    for day, _raw, hit in rows:
        if hit is None:
            continue
        if hit.effect == "stop":
            stops.append(day)
        if hit.id in HARD_STOPS:
            hard.add(day)

    def close(start, end_game):
        end = end_game - dt.timedelta(days=1) if end_game in hard else end_game
        if end >= start:
            return (start, end)
        return None

    spans = []
    start = prev = dates[0]
    for day in dates[1:]:
        between = any(prev < stop < day for stop in stops)
        if day.year != prev.year or between or prev in hard:
            piece = close(start, prev)
            if piece:
                spans.append(piece)
            start = day
        prev = day
    piece = close(start, prev)
    if piece:
        spans.append(piece)
    return spans


def days_carried_past_stop(ranges, rows) -> int:
    """Days inside a stint that began on or before an option, outright, or release and continues past it with no return yet.

    A later game may start its own stint. That stint is not counted here.
    """
    stops = sorted(day for day, _raw, hit in rows if hit is not None and hit.id in HARD_STOPS)
    returns = sorted(
        day for day, _raw, hit in rows
        if hit is not None and hit.id in RETURN_ROWS
    )
    total = 0
    for stop in stops:
        nxt = next((day for day in returns if day > stop), None)
        limit = (nxt - dt.timedelta(days=1)) if nxt else None
        for item in ranges:
            lo, hi = item[0], item[1]
            rules = item[2] if len(item) > 2 else ()
            # The short optional assignment is credited on purpose. It is not a stint that ran through.
            # Injured-list time is not this check. The failure is a game-date stint that keeps going.
            if "accrual.option-under-20" in rules or "accrual.appearance-game" not in rules:
                continue
            if lo > stop:
                continue
            start = max(lo, stop + dt.timedelta(days=1))
            end = hi if limit is None else min(hi, limit)
            if end >= start:
                total += (end - start).days + 1
    return total


def untracked_gap(rows, dates, year: int) -> bool:
    """A season with no transactions and a gap of 30 days or more between games."""
    if not dates:
        return False
    if any(day.year == year for day, _raw, _hit in rows):
        return False
    games = sorted(day for day in dates if day.year == year)
    return any((later - earlier).days >= 30 for earlier, later in zip(games, games[1:]))


def blocked_ranges(boundaries) -> list[tuple[dt.date, dt.date]]:
    """Days after a release or free agency, until the next start. Same rule as the publish check."""
    blocked_from = None
    spans = []
    events = sorted(
        boundaries or [],
        key=lambda event: (event["date"], 0 if event.get("kind") == "block" else 1),
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
    return spans


def clip_blocked(spans, boundaries):
    """Drop game-date days that fall after a release or free agency."""
    blocked = blocked_ranges(boundaries)
    if not blocked:
        return spans
    kept = []
    for start, end in spans:
        pieces = [(start, end)]
        for lo, hi in blocked:
            nxt = []
            for left, right in pieces:
                if hi < left or lo > right:
                    nxt.append((left, right))
                    continue
                if left < lo:
                    nxt.append((left, lo - dt.timedelta(days=1)))
                if right > hi:
                    nxt.append((hi + dt.timedelta(days=1), right))
            pieces = [(left, right) for left, right in nxt if right >= left]
        kept.extend(pieces)
    return kept


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


_CONTRACT_ON = {
    "start.mlb-signing", "start.mlb-contract-signing",
    "start.selected", "start.purchased", "start.rule5",
    "start.recalled", "stop.option",
}
_CONTRACT_OFF = {
    "neutral.minor-signing", "stop.released", "stop.free-agency",
    "stop.outright", "stop.retired",
}


def _mlb_contract_open(rows, day) -> bool:
    """True when a major-league contract is in effect before this date."""
    open_contract = False
    for row_day, _raw, hit in rows:
        if row_day >= day or hit is None:
            break
        if hit.id in _CONTRACT_OFF:
            open_contract = False
        elif hit.id in _CONTRACT_ON:
            open_contract = True
    return open_contract


def _outside_championship(day, calendar) -> bool:
    """Before the league opener, or after the last game. Spring and offseason."""
    season = calendar.seasons.get(day.year)
    if season is None:
        return False
    opener = calendar.league_start(day.year)
    return day < opener or day > season.championship_end


def without_paper_assignment(hits, rows, day, calendar):
    """Drop a minor-league assignment that is spring or offseason paperwork.

    The stop stays for a minor-league contract, a non-roster player, and a
    championship-season assignment. Those are real departures.
    """
    if not hits or not any(hit.id == "stop.assigned-minors" for hit in hits):
        return hits
    if any(hit.id == "neutral.minor-signing" for hit in hits):
        return hits
    if not _mlb_contract_open(rows, day):
        return hits
    if not _outside_championship(day, calendar):
        return hits
    return [hit for hit in hits if hit.id != "stop.assigned-minors"]


def opening_rule(floor, debut, rows, pre_debut_ids) -> str:
    """Pre-debut only when a roster move pulled the floor forward. An opening-week debut is the ordinary start."""
    if debut is not None and floor is not None and floor < debut and any(
        hit is not None and hit.id in pre_debut_ids and floor <= day < debut
        for day, _raw, hit in rows
    ):
        return "accrual.pre-debut-roster"
    return "accrual.presume-from-debut"


def service_floor(debut, rows, window_days, pre_debut_ids, calendar):
    """First day that can count. A debut in the opening week starts at the opener.

    A roster move in the short gap before the debut still wins. A call-up
    dated on the debut stays on that date.
    """
    if debut is None:
        return None
    earliest = debut - dt.timedelta(days=window_days)
    early = [
        day for day, _raw, hit in rows
        if hit is not None and hit.id in pre_debut_ids and earliest <= day < debut
    ]
    if early:
        return min(early)
    season = None if calendar is None else calendar.seasons.get(debut.year)
    if season is None:
        return debut
    opener = calendar.league_start(debut.year)
    if not (opener < debut <= opener + dt.timedelta(days=6)):
        return debut
    for day, _raw, hit in rows:
        if hit is None or day < opener:
            continue
        if day > debut:
            break
        if day < debut and hit.effect in {"start", "stop"}:
            return debut
        if day == debut and hit.effect == "start":
            return debut
    return opener


def _assignment_cancelled_next_day(rows, day) -> bool:
    """Optioned, outrighted, released, or sent to an affiliate the next day."""
    nxt = day + dt.timedelta(days=1)
    for later, _raw, hit in rows:
        if later < nxt or hit is None:
            continue
        if later > nxt:
            break
        if hit.id in {
            "stop.option", "stop.outright", "stop.released", "stop.free-agency",
            "stop.assigned-minors",
        }:
            return True
    return False


def assignment_opens(hits, day, later_days, grouped, calendar, confirms, rows) -> bool:
    """An assignment onto a major-league club during the season is a recall.

    Before the opener it is a spring note, and it opens service only when a
    later injured-list placement or option shows he was on that club. The
    next day sending him back out means he did not stay on the active roster.
    """
    if not any(hit.id == "neutral.mlb-assignment" for hit in hits):
        return False
    if rows is not None and _assignment_cancelled_next_day(rows, day):
        return False
    season = None if calendar is None else calendar.seasons.get(day.year)
    if season is not None:
        opener = calendar.league_start(day.year)
        if opener <= day <= season.championship_end:
            return True
    return bool(confirms(later_days, grouped))
