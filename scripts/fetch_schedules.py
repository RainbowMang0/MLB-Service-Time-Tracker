#!/usr/bin/env python3
"""
fetch_schedules.py
------------------
Publishes each club's season schedule, with the JURISDICTION of every game,
so the duty-day tool can propose a season without the browser ever calling
an API.

WHY THIS IS BUILD-TIME AND NOT A BROWSER FETCH
==============================================
The duty-day tool runs entirely client-side -- no server, no account, nothing
a player types leaves his device. That only works if the schedule is already
sitting there as a static file. It is the same shape as everything else here:
the daily Action does the fetching, commits JSON, and GitHub Pages serves it.

It also means the schedule is on the same footing as the service-time data --
one source (statsapi.mlb.com), one polite daily job, no new dependency and no
licensing question, because it is the same public endpoint this project has
been reading since it started.

WHAT THE JURISDICTION FIELD IS
==============================
Not the club. The STATE THE GAME IS PLAYED IN, which is the only thing that
matters for duty-day allocation. A Yankees home game is NY because the park
is in the Bronx, not because the club is a New York club. Toronto comes back
as CA-ON and is deliberately NOT flattened into a US state -- a road trip to
Canada is a foreign filing question and the tool has to keep saying so.

SHARDING
========
One file per club per season (docs/data/schedules/<season>/<teamId>.json),
because a player opens exactly one. Same instinct as the profile shards: the
table load must not pay for data almost nobody opens.

USAGE
=====
    python3 scripts/fetch_schedules.py --season 2026
    python3 scripts/fetch_schedules.py --season 2026 --fixture path.json
        (offline: read a recorded API response instead of calling out)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import fetch_mlb_data as mlb  # noqa: E402

OUT_ROOT = pathlib.Path(__file__).resolve().parents[1] / "docs" / "data" / "schedules"

# Spring training, regular season, and every postseason round. Spring games
# are included because spring training days are duty days -- which is exactly
# why the Arizona/Florida split matters so much to a player's allocation.
GAME_TYPES = "S,R,F,D,L,W"

# Non-US venues, mapped to the jurisdiction codes in config/tax/2026-states.json.
# Kept explicit rather than derived: a silent fallback to a US state for a
# foreign venue would put Canadian duty days into a US return.
NON_US_VENUE_JURISDICTIONS = {
    ("Canada", "Ontario"): "CA-ON",
    ("Canada", "British Columbia"): "CA-BC",
    ("Canada", "Quebec"): "CA-QC",
}


def venue_jurisdiction(venue: dict) -> tuple[str | None, str | None]:
    """
    (jurisdiction_code, human_label) for a game's venue.

    Returns (None, label) when the venue cannot be resolved -- an
    international series in Mexico City, London or Tokyo, most likely. That
    is reported loudly rather than guessed, because a day assigned to the
    wrong country is worse than a day the player is asked about.
    """
    location = (venue or {}).get("location") or {}
    country = location.get("country")
    state = location.get("stateAbbrev")
    state_name = location.get("state")
    city = location.get("city")
    label = ", ".join(x for x in (city, state_name or state, country) if x)

    if country in (None, "USA", "United States"):
        if state:
            return state, label
        return None, label

    key = (country, state_name)
    if key in NON_US_VENUE_JURISDICTIONS:
        return NON_US_VENUE_JURISDICTIONS[key], label
    return None, label


def parse_schedule(payload: dict) -> dict[int, list[dict]]:
    """
    Turn one /schedule response into {team_id: [game, ...]}.

    Each game appears twice -- once for the home club, once for the away club
    -- because each club's file has to say whether the game was home or away
    for THAT club.
    """
    by_team: dict[int, list[dict]] = {}
    unresolved: list[str] = []

    for date_block in payload.get("dates", []):
        date = date_block.get("date")
        for game in date_block.get("games", []):
            venue = game.get("venue") or {}
            jurisdiction, label = venue_jurisdiction(venue)
            if jurisdiction is None:
                unresolved.append(f"{date} {venue.get('name')} ({label or 'no location'})")

            teams = game.get("teams") or {}
            home = (teams.get("home") or {}).get("team") or {}
            away = (teams.get("away") or {}).get("team") or {}
            game_type = game.get("gameType")

            for team, is_home, opponent in (
                (home, True, away),
                (away, False, home),
            ):
                team_id = team.get("id")
                if not team_id:
                    continue
                by_team.setdefault(team_id, []).append(
                    {
                        "date": date,
                        "state": jurisdiction,
                        "home": is_home,
                        "opponent": opponent.get("name"),
                        "venue": venue.get("name"),
                        "venue_label": label,
                        "game_type": game_type,
                        "spring": game_type == "S",
                    }
                )

    if unresolved:
        print(
            f"  !! {len(unresolved)} game(s) at a venue with no jurisdiction; "
            "they are published with state=null so the UI asks rather than guesses:",
            file=sys.stderr,
        )
        for row in unresolved[:10]:
            print(f"     {row}", file=sys.stderr)

    for games in by_team.values():
        games.sort(key=lambda g: (g["date"], not g["home"]))
    return by_team


def fetch_season(season: int) -> dict:
    """Fetch the whole season in one call. Spring through the World Series."""
    params = {
        "sportId": mlb.SPORT_ID_MLB,
        "season": season,
        "gameTypes": GAME_TYPES,
        "hydrate": "venue(location)",
    }
    url = f"{mlb.BASE_URL}/schedule"
    resp = mlb.requests.get(url, params=params, timeout=mlb.REQUEST_TIMEOUT)
    resp.raise_for_status()
    mlb.time.sleep(mlb.POLITE_DELAY_SECONDS)
    return resp.json()


def write_schedules(season: int, payload: dict, out_root: pathlib.Path = OUT_ROOT) -> dict:
    by_team = parse_schedule(payload)
    season_dir = out_root / str(season)
    season_dir.mkdir(parents=True, exist_ok=True)

    # Rebuilt wholesale, like the player pages: a club whose games vanish
    # from the feed should lose its file rather than keep a stale one.
    for stale in season_dir.glob("*.json"):
        stale.unlink()

    index = []
    for team_id, games in sorted(by_team.items()):
        regular = [g for g in games if not g["spring"]]
        spring = [g for g in games if g["spring"]]
        home_states = {g["state"] for g in regular if g["home"] and g["state"]}
        spring_states = {g["state"] for g in spring if g["state"]}

        name = TEAM_NAMES.get(team_id)

        doc = {
            "season": season,
            "team_id": team_id,
            "team": name,
            "home_state": sorted(home_states)[0] if len(home_states) == 1 else None,
            "home_states": sorted(home_states),
            "spring_states": sorted(spring_states),
            "generated_at": dt.date.today().isoformat(),
            "games": [
                {
                    "d": g["date"],
                    "s": g["state"],
                    "h": 1 if g["home"] else 0,
                    "t": g["game_type"],
                    "v": g["venue"],
                    "o": g["opponent"],
                }
                for g in games
            ],
        }
        (season_dir / f"{team_id}.json").write_text(
            json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8"
        )
        index.append(
            {
                "team_id": team_id,
                "team": name,
                "home_state": doc["home_state"],
                "spring_states": doc["spring_states"],
                "games": len(games),
            }
        )

    index_doc = {
        "season": season,
        "generated_at": dt.date.today().isoformat(),
        "source": "MLB Stats API (statsapi.mlb.com) /schedule, venue location hydrated",
        "note": (
            "`state` on each game is the jurisdiction the game is PLAYED in, "
            "which is what duty-day allocation turns on -- not the club's own "
            "home state. A null state means the venue could not be resolved to "
            "a jurisdiction (an international series); the tool asks rather "
            "than guessing."
        ),
        "clubs": sorted(index, key=lambda c: (c["team"] or "", c["team_id"])),
    }
    (season_dir / "index.json").write_text(
        json.dumps(index_doc, indent=1) + "\n", encoding="utf-8"
    )

    total_games = sum(c["games"] for c in index)
    print(f"  wrote {len(index)} clubs, {total_games} club-games -> {season_dir}")
    return index_doc


# Club ids are stable and this avoids a second API call purely for names.
# Populated from /teams on the first live run; a missing name is cosmetic.
TEAM_NAMES: dict[int, str] = {}


def load_team_names() -> None:
    try:
        for team in mlb.get_teams():
            TEAM_NAMES[team["id"]] = team.get("name")
    except Exception as exc:  # pragma: no cover - network path
        print(f"  (could not load team names: {exc})", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=dt.date.today().year)
    parser.add_argument(
        "--fixture",
        help="Read a recorded /schedule response from this path instead of "
        "calling the API. For offline testing.",
    )
    args = parser.parse_args()

    print(f"Schedules for {args.season}")
    if args.fixture:
        payload = json.loads(pathlib.Path(args.fixture).read_text())
        print(f"  (offline: {args.fixture})")
    else:
        load_team_names()
        payload = fetch_season(args.season)

    write_schedules(args.season, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
