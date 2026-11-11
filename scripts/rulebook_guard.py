"""Checks the publish must pass, and the label for a season that moved.

A short season cannot jump by 50 days without a person clearing it.
A handful of careers and seasons are pinned so a later replay cannot
drift off the figures already reviewed.
"""

from __future__ import annotations

import json
from pathlib import Path

# Spring options of exactly 20 days from the league opener. Publish the
# rulebook figure. The higher number counted a shorter club opener.
RELEASED = {
    (680730, 2026),  # Mitchell Parker, 2.167
    (669020, 2026),  # Ryan Rolison, same 20-day option
}

# Career service time the site must show, and two seasons pinned by days.
CAREERS = {
    679032: "2.130",  # Johan Rojas. June 25 through September 27 is 95 days; the career sums to 2.130.
    676617: "2.129",  # Riley O'Brien
    680730: "2.167",  # Mitchell Parker
    687597: "2.133",  # Jordan Beck
    693821: "3.068",  # Bryce Elder
    676979: "6.028",  # Garrett Crochet
    664062: "6.026",  # Tony Gonsolin
}
SEASONS = {
    (671732, 2024): 152,  # Lawrence Butler
    (676664, 2025): 172,  # JP Sears
}

_BOILERPLATE = {
    "accrual.league-opener",
    "accrual.international-opener",
    "accrual.shortened-2020",
    "accrual.lockout-2022",
    "accrual.lockout-1990",
    "accrual.tiebreaker-through-2021",
    "accrual.credited-year",
    "accrual.season-cap",
}


def released(player_id: int, year: int) -> bool:
    return (int(player_id), int(year)) in RELEASED


def short_increase(rule_days: int, published_days: int, games: int) -> bool:
    """An upward jump of 50 or more days in a season of 10 or fewer games."""
    return games <= 10 and rule_days - published_days >= 50


def verified_problems(players: list[dict]) -> list[str]:
    by_id = {int(player["id"]): player for player in players}
    problems = []
    for player_id, expected in CAREERS.items():
        player = by_id.get(player_id)
        if player is None:
            problems.append(f"player {player_id} is missing from the publish")
            continue
        got = player.get("service_time")
        if got != expected:
            problems.append(f"{player.get('name')} is {got}, and the reviewed figure is {expected}")
    for (player_id, year), days in SEASONS.items():
        player = by_id.get(player_id)
        if player is None:
            problems.append(f"player {player_id} is missing from the publish")
            continue
        got = next((int(row.get("d") or 0) for row in player.get("seasons") or [] if int(row.get("y") or 0) == year), None)
        if got != days:
            problems.append(f"{player.get('name')} {year} is {got} days, and the reviewed figure is {days}")
    return problems


def _from_rules(rules: list, delta: int) -> str:
    rules = [rule for rule in rules if rule not in _BOILERPLATE]
    if delta > 0:
        prefixes = ("accrual.", "start.", "count.", "exception.", "neutral.")
    elif delta < 0:
        prefixes = ("stop.", "exception.", "accrual.")
    else:
        prefixes = ("accrual.", "start.", "count.", "stop.")
    for prefix in prefixes:
        for rule in rules:
            if rule.startswith(prefix):
                return rule
    return rules[-1] if rules else "rulebook"


def explain_rule(season: dict, delta: int) -> str:
    """The rule that accounts for the days that moved, not a calendar stamp."""
    events = list(season.get("events") or [])
    if delta > 0 and any(row.get("rule") == "neutral.all-star" for row in events):
        seen = False
        for row in events:
            if row.get("rule") == "neutral.all-star":
                seen = True
                continue
            rule = row.get("rule") or ""
            if seen and (rule.startswith("count.il") or rule.startswith("count.dl")):
                return rule
        return "neutral.all-star"
    if delta > 0 and season.get("carried_in") and events:
        first = events[0].get("rule") or ""
        if first.startswith("count.il") or first.startswith("count.dl"):
            return "accrual.already-rostered"
    return _from_rules(list(season.get("rules") or []), delta)


def write_reports(reports: Path, changes, holds, restricted, flags, named, named_order) -> None:
    """The grouped review under reports/replay/."""
    reports.mkdir(parents=True, exist_ok=True)
    up = sum(1 for row in changes if row["delta_days"] > 0)
    down = sum(1 for row in changes if row["delta_days"] < 0)
    crossers = [row for row in changes if row["crossed"]]
    roster_crossers = [row for row in crossers if row["on_40_man"]]
    by_rule: dict[str, list] = {}
    for row in changes:
        by_rule.setdefault(row["rule_id"], []).append(row)
    lines = [
        "# Replay for the owner",
        "",
        f"{len(changes)} players change: {up} up, {down} down.",
        f"{len(holds)} seasons stay at the published number until someone clears them.",
        f"{len(roster_crossers)} current roster players cross 3 years, 6 years, the 2026 Super Two line (2.133), or an eligibility line.",
        "",
        "A higher season is published. A lower season is kept and listed under Holds.",
        "The live Super Two line used here is 2.133.",
        f"2020 is included. {sum(1 for row in holds if row['season'] == 2020)} of the holds are 2020 seasons.",
        "",
        "## Named checks",
        "",
    ]
    if not named:
        lines.append("None.")
    named = sorted(named, key=lambda row: named_order.index(row["player_id"]))
    for row in named:
        if row.get("held"):
            lines.append(
                f"- {row['name']} {row['season']} stays at {row['published_days']} "
                f"(the rulebook says {row['rulebook_days']}) until someone clears it."
            )
        elif row.get("new_days", row["published_days"]) != row["published_days"]:
            lines.append(f"- {row['name']} {row['season']} goes from {row['published_days']} to {row['new_days']}.")
        else:
            extra = ""
            if row.get("games", 0) > row.get("published_days", 0):
                extra = f" He played {row['games']} games that season, more than those days, so the missing roster row stays a review item."
            lines.append(
                f"- {row['name']} {row['season']} stays at {row['published_days']}. "
                "The rulebook matches the published figure, so there is nothing to hold." + extra
            )
    lines += ["", "## Threshold crossers", ""]
    ordered = sorted(crossers, key=lambda row: (0 if row["on_40_man"] else 1, row["name"] or ""))
    if not ordered:
        lines.append("None.")
    for row in ordered:
        who = "roster" if row["on_40_man"] else "alumni"
        lines.append(
            f"- {row['name']} ({who}): {row['old_service_time']} to {row['new_service_time']}. "
            f"{', '.join(row['crossed'])}. {row['rule_id']}."
        )
    lines += ["", "## By rule", ""]
    for rule, rows in sorted(by_rule.items(), key=lambda item: -len(item[1])):
        lines.append(f"### {rule} ({len(rows)})")
        lines.append("")
        for row in sorted(rows, key=lambda item: -abs(item["delta_days"]))[:12]:
            lines.append(f"- {row['name']}: {row['old_service_time']} to {row['new_service_time']}. {row['reason']}")
        if len(rows) > 12:
            lines.append(f"- {len(rows) - 12} more in changes.json.")
        lines.append("")
    lines += ["## Changes of 25 days or more", ""]
    large = [row for row in changes if abs(row["delta_days"]) >= 25]
    if not large:
        lines.append("None.")
    for row in sorted(large, key=lambda item: -abs(item["delta_days"])):
        lines.append(f"- {row['name']}: {row['reason']}")
    lines += ["", "## Holds", "", f"{len(holds)} seasons. Cleared stays false until a person sets it.", ""]
    for row in holds[:40]:
        lines.append(
            f"- {row['name']} {row['season']}: rulebook {row['rulebook_days']}, "
            f"kept {row['published_days']}. {row['reason']}"
        )
    if len(holds) > 40:
        lines.append(f"- {len(holds) - 40} more in data/rulebook/holds.json.")
    waiting = len({row.get("player_id") for row in restricted})
    lines += [
        "",
        "## Restricted list still unclassified",
        "",
        f"{len(restricted)} rows for {waiting} current roster players. "
        "They earn no service until someone classifies them. "
        "If that makes a season lower than the published figure, the season is in Holds.",
        "",
    ]
    restricted = sorted(restricted, key=lambda row: (row.get("name") or "", row.get("date") or ""))
    for row in restricted:
        lines.append(f"- {row.get('name')} {row.get('date')}: {row.get('description')}")
    lines += ["", "## Pre-2012 designation window", "", f"{len(flags)} seasons use that window. The length is unknown, so each one is listed.", ""]
    for row in flags[:20]:
        lines.append(f"- {row['name']} {row['year']}.")
    if len(flags) > 20:
        lines.append(f"- {len(flags) - 20} more.")
    lines.append("")
    (reports / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (reports / "changes.json").write_text(json.dumps(changes, indent=2) + "\n", encoding="utf-8")
    (reports / "restricted_unclassified.json").write_text(json.dumps(restricted, indent=2) + "\n", encoding="utf-8")
    (reports / "pre2012.json").write_text(json.dumps(flags, indent=2) + "\n", encoding="utf-8")
