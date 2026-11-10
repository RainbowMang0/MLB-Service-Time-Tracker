"""Document titles and meta descriptions short enough for a search snippet.

A title stays at or under 60 characters. A description stays at or under
155. The short brand suffix is added only when the whole title still fits.
Callers pass figures they already computed; nothing here is a service-time
rule, and a number is never invented.
"""

from __future__ import annotations

TITLE_MAX = 60
META_MAX = 155
BRAND = " | Big League"


def fit_title(core: str, *, brand: str = BRAND) -> str:
    """The snippet title. Prefer `core` plus the brand when that still fits."""
    core = " ".join((core or "").split())
    if not core:
        core = "Big League Service Time"
    if brand and len(core) + len(brand) <= TITLE_MAX:
        return core + brand
    if len(core) <= TITLE_MAX:
        return core
    trimmed = core[: TITLE_MAX + 1].rsplit(" ", 1)[0].rstrip(" ,;:-|")
    if len(trimmed) < 12:
        trimmed = core[:TITLE_MAX].rstrip()
    return trimmed


def og_title(core: str) -> str:
    """The share title: the distinctive words, never longer than a snippet."""
    return fit_title(core, brand="")


def fit_meta(text: str, *, limit: int = META_MAX) -> str:
    """Trim a description on a sentence or word boundary. Do not invent words."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    window = text[: limit + 1]
    for mark in (". ", "! ", "? "):
        idx = window.rfind(mark)
        if 40 <= idx < limit:
            return window[: idx + 1].rstrip()
    cut = window.rsplit(" ", 1)[0].rstrip(" ,;:-")
    if len(cut) > limit - 1:
        cut = cut[: limit - 1].rstrip(" ,;:-")
    if cut and not cut.endswith((".", "!", "?")):
        cut += "."
    return cut


def _be(n: int) -> str:
    return "is" if n == 1 else "are"


def _players(n: int) -> str:
    return "player" if n == 1 else "players"


def homepage_title() -> str:
    return fit_title("MLB service time for every 40-man player")


def homepage_meta() -> str:
    return fit_meta(
        "MLB service time for every 40-man player, plus previous players. "
        "172 days credits a year, 3.000 reaches arbitration, and 6.000 reaches free agency."
    )


def super_two_core(cutoff: dict | None) -> str:
    """Official when the cutoff is published. Otherwise the projection wording."""
    if cutoff and cutoff.get("official") and cutoff.get("cutoff") and cutoff.get("season"):
        return f"Super Two cutoff is {cutoff['cutoff']} after {cutoff['season']}"
    return "Super Two cutoff projection"


def super_two_title(cutoff: dict | None) -> str:
    return fit_title(super_two_core(cutoff))


def non_tender_core(year: int | None) -> str:
    if year:
        return f"MLB non-tender watch before November 20, {year}"
    return "MLB non-tender watch"


def non_tender_title(year: int | None) -> str:
    return fit_title(non_tender_core(year))


def non_tender_meta(count: int, year: int | None, final_year: int, low_games: int) -> str:
    deadline = f"November 20, {year}" if year else "the November deadline"
    return fit_meta(
        f"{count} players on the non-tender watch before {deadline}: "
        f"{final_year} in a final arbitration year and {low_games} with limited "
        "playing time. From this project's data, not a prediction."
    )


def club_fa_core(club: str, now_count: int, next_season: int | None) -> str:
    if now_count:
        return f"{club} free agents this winter"
    if next_season:
        return f"{club} free agents after {next_season}"
    return f"{club} free agents"


def club_fa_title(club: str, now_count: int, next_season: int | None) -> str:
    return fit_title(club_fa_core(club, now_count, next_season))


def club_fa_meta(club: str, now_count: int, later_count: int, next_season: int | None) -> str:
    if now_count == 0:
        now = f"No {club} players are free agents now"
    elif now_count == 1:
        now = f"1 {club} player is a free agent now"
    else:
        now = f"{now_count} {club} players are free agents now"
    later = ""
    if next_season is not None:
        later = f", and {later_count} more {_be(later_count)} projected after {next_season}"
    return fit_meta(
        f"{now}{later}. From this project's service-time data, not an official figure."
    )


def player_title(name: str, hint: str = "") -> str:
    who = f"{name} ({hint})" if hint else name
    return fit_title(f"{who} service time")


def player_og(name: str, service: str, hint: str = "") -> str:
    who = f"{name} ({hint})" if hint else name
    core = f"{who} — {service} service time"
    if len(core) <= TITLE_MAX:
        return core
    return og_title(f"{who} service time")


def title_hints(players: list[dict]) -> dict:
    """A short label for each id whose name is shared, else nothing.

    Position when every player with that name has a different one. Debut
    year when positions collide or are missing. Both when a year is shared
    too, so the document titles stay unique.
    """
    groups: dict[str, list[dict]] = {}
    for player in players:
        groups.setdefault(player.get("name") or "", []).append(player)
    hints: dict = {}
    for group in groups.values():
        if len(group) < 2:
            continue
        positions = [(player.get("position") or "").strip() for player in group]
        years = [(player.get("mlb_debut") or "")[:4] for player in group]
        pos_ok = all(positions) and len(set(positions)) == len(group)
        year_ok = all(years) and len(set(years)) == len(group)
        for player, pos, year in zip(group, positions, years):
            if pos_ok:
                hint = pos
            elif year_ok:
                hint = year
            elif pos and year:
                hint = f"{pos} {year}"
            else:
                hint = year or pos or str(player.get("id") or "")
            if player.get("id") is not None and hint:
                hints[player["id"]] = hint
    return hints


def player_meta(name: str, service: str, days: int, where: str, status: str) -> str:
    place = where or "in the major leagues"
    return fit_meta(
        f"{name}: estimated {service} years ({days} days) {place}. "
        f"{status}. Not an official MLB/MLBPA figure."
    )


def club_roster_core(club: str) -> str:
    """The distinctive title. Long club names drop '40-man roster' so the brand fits."""
    full = f"{club} service time, 40-man roster"
    if len(full) + len(BRAND) <= TITLE_MAX:
        return full
    return f"{club} service time"


def club_roster_title(club: str) -> str:
    return fit_title(club_roster_core(club))


def club_roster_meta(club: str, total: int, fa: int, arb: int) -> str:
    return fit_meta(
        f"{club} 40-man roster: {total} players, {fa} free-agent eligible, "
        f"{arb} arbitration eligible. Estimates from public data."
    )


def club_arb_core(club: str, year: int | None) -> str:
    if year:
        return f"{club} arbitration {year}"
    return f"{club} arbitration"


def club_arb_title(club: str, year: int | None) -> str:
    return fit_title(club_arb_core(club, year))


def club_arb_meta(club: str, count: int, year: int | None) -> str:
    when = f" for {year}" if year else ""
    return fit_meta(
        f"{count} {club} {_players(count)} {_be(count)} projected "
        f"arbitration-eligible this winter{when}. From this project's service-time data."
    )


def club_nt_core(club: str, year: int | None) -> str:
    if year:
        return f"{club} non-tender watch {year}"
    return f"{club} non-tender watch"


def club_nt_title(club: str, year: int | None) -> str:
    return fit_title(club_nt_core(club, year))


def club_nt_meta(club: str, count: int, year: int | None) -> str:
    when = f" for {year}" if year else ""
    return fit_meta(
        f"{count} {club} {_players(count)} {_be(count)} on this winter's "
        f"non-tender watch{when}. From this project's data, not a prediction."
    )


def free_agents_core(year: int | None) -> str:
    if year:
        return f"MLB free agents after the {year} season"
    return "MLB free agents by year"


def free_agents_title(year: int | None) -> str:
    return fit_title(free_agents_core(year))


def free_agents_meta(count: int, year: int | None) -> str:
    when = f" after the {year} season" if year else ""
    return fit_meta(
        f"{count} players reach free agency{when}, plus later classes by year. "
        "From this project's service-time data, not an official figure."
    )


def arbitration_core(year: int | None) -> str:
    if year:
        return f"Arbitration-eligible players, {year}"
    return "Arbitration-eligible players this winter"


def arbitration_title(year: int | None) -> str:
    return fit_title(arbitration_core(year))


def arbitration_meta(count: int, standard: int, super_two: int, years_label: str, year: int | None) -> str:
    when = f" heading into {year}" if year else ""
    return fit_meta(
        f"{count} players projected arbitration-eligible this winter{when}: "
        f"{standard} at {years_label}+ and {super_two} Super Two. "
        "From this project's service-time data."
    )


def options_title() -> str:
    return fit_title("MLB players out of options")


def options_meta(out: int, one: int) -> str:
    return fit_meta(
        f"{out} players are out of minor league options, and {one} have one "
        "option year left. Counted from public data."
    )


def explainer_title() -> str:
    return fit_title("What MLB service time is")


def explainer_meta(days: int, arb: str, fa: str, gold: str, pension: str) -> str:
    return fit_meta(
        f"What MLB service time is: {days} days to a year, arbitration at {arb}, "
        f"free agency at {fa}, a gold card at {gold}, and a full pension at {pension}."
    )


def club_index_title() -> str:
    return fit_title("Service time by club, all 30 teams")


def club_index_meta(total: int) -> str:
    return fit_meta(
        f"Service time for {total} players on a 40-man roster, by club: "
        "free agency, arbitration, and Super Two. Estimates from public data."
    )


def alumni_index_title() -> str:
    return fit_title("Previous players, service time A to Z")


def alumni_index_meta(total: str) -> str:
    return fit_meta(
        f"Service time for {total} players who have come off a 40-man roster, A to Z. "
        "Estimates from public data, not official figures."
    )


def alumni_letter_title(letter: str) -> str:
    return fit_title(f"Previous players, {letter}")


def alumni_letter_meta(count: int, letter: str) -> str:
    return fit_meta(
        f"Service time for {count} previous players whose surname begins with {letter}. "
        "Estimates from public data, not official figures."
    )


def lockout_title() -> str:
    return fit_title("MLB CBA and 2026 lockout tracker")


def lockout_og() -> str:
    return og_title("MLB CBA and 2026 lockout tracker")


def salary_cap_title() -> str:
    return fit_title("MLB salary cap and floor proposal")


def salary_cap_og() -> str:
    return og_title("MLB salary cap and floor proposal")
