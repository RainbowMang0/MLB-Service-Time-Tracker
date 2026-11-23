"""The name lists on a club hub, and the counts that must match them.

``arbitration_eligible`` stays true after a player passes 6.000 years, so a
raw sum counts free agents too. The arbitration card counts only the players
the page lists under that heading: under 6.000, and not already listed on
the Super Two line. The Super Two card is that line, unchanged.
"""

from __future__ import annotations


def roster_groups(players: list[dict], open_player=None) -> dict[str, list[dict]]:
    """Same three lists the club page names.

    ``open_player`` drops someone a contract already covers from the free-agent
    and arbitration lists. Super Two stays the full list, matching the page.
    """
    def is_open(player: dict) -> bool:
        return True if open_player is None else bool(open_player(player))

    free_agents = [p for p in players if p.get("free_agent_eligible") and is_open(p)]
    super_two = [p for p in players if p.get("super_two_candidate")]
    already_named = {p.get("id") for p in free_agents} | {p.get("id") for p in super_two}
    arbitration = [
        p for p in players
        if p.get("arbitration_eligible") and p.get("id") not in already_named and is_open(p)
    ]
    return {"fa": free_agents, "s2": super_two, "arb": arbitration}


def hearing_players(players: list[dict], open_player=None) -> list[dict]:
    """Players the arbitration number, the name list, and the winter table share.

    Super Two is its own card, and it is also this winter's arbitration list.
    A contract that already covers next season is on neither: there is no
    hearing. ``roster_groups`` keeps those two facts apart. This puts them
    back together so a count and the names under it are the same people.
    """
    groups = roster_groups(players, open_player)

    def is_open(player: dict) -> bool:
        return True if open_player is None else bool(open_player(player))

    seen = {p.get("id") for p in groups["arb"]}
    extra = [p for p in groups["s2"] if p.get("id") not in seen and is_open(p)]
    return groups["arb"] + extra
