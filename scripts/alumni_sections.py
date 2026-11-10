#!/usr/bin/env python3
"""Derived injured-list, season-line, and career-move data for alumni pages.

Alumni are published and off the 40-man, and their raw transactions were
never stored: a full cache is about 13 KB a player, and a few thousand of
those files would add tens of megabytes. This script fetches the same
public feed the roster job uses, builds the three page sections, and
stores only that derived data.

Nothing here writes a service-time figure or a raw transaction file.
The page builder reads the derived file when a player has no transaction
cache. A player who already has a cache is left on that path.

The feed is thin before 2015. When a player's own rows start after his
debut, the injured-list block says the public record begins in that year
instead of claiming he never spent a day on the list.

    python scripts/alumni_sections.py --status
    python scripts/alumni_sections.py                 # fetch whoever is missing, then rewrite pages
    python scripts/alumni_sections.py --limit 20
    python scripts/alumni_sections.py --refresh       # monthly: fetch the set again
    python scripts/alumni_sections.py --rewrite-only  # pages only, no network
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from career_moves import build_moves  # noqa: E402
import injury_labels  # noqa: E402
from il_stints import SeasonWindows, build_stints  # noqa: E402
from mlb_clubs import CLUB_NAMES, MLB_CLUB_IDS  # noqa: E402
import player_sections  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
STORE_DIR = ROOT / "data" / "alumni_sections"
SHARD_COUNT = 64

# Rows before this season are sparse in the public feed. A debut earlier
# than that, with no row from the early years, is not a measured zero.
THIN_FEED_YEAR = 2015

# Keys that would carry a feed sentence or a move's coded reason.
# The derived file must not have them at any level.
FEED_KEYS = frozenset({
    "description",
    "typeDesc",
    "typeCode",
    "effectiveDate",
    "resolutionDate",
})

PAGE_ID_RE = re.compile(r"^(\d+)-")
SITEMAP_URL_RE = re.compile(
    r"<loc>([^<]+)</loc><lastmod>\d{4}-\d{2}-\d{2}</lastmod>"
)


def published_page_ids(page_dir: pathlib.Path) -> set[int]:
    """Player ids that already have a page. The filename starts with the id."""
    ids: set[int] = set()
    if not page_dir.exists():
        return ids
    for path in page_dir.glob("*.html"):
        match = PAGE_ID_RE.match(path.name)
        if match:
            ids.add(int(match.group(1)))
    return ids


def cached_transaction_ids(cache_dir: pathlib.Path) -> set[int]:
    ids: set[int] = set()
    if not cache_dir.exists():
        return ids
    for path in cache_dir.glob("*.json"):
        if path.stem.isdigit():
            ids.add(int(path.stem))
    return ids


def discover_alumni_ids(
    page_ids: set[int],
    cached_ids: set[int],
    players_by_id: dict,
) -> list[int]:
    """Published players with no transaction file who are not on a 40-man.

    A rostered player missing a cache stays with the daily job. A player
    who already has a cache keeps that file as the source for his page.
    """
    cached = set(cached_ids)
    found: list[int] = []
    for pid in sorted(page_ids):
        if pid in cached:
            continue
        record = players_by_id.get(pid)
        if record is None:
            record = players_by_id.get(str(pid))
        if not isinstance(record, dict) or record.get("on_40_man"):
            continue
        found.append(pid)
    return found


def ids_to_fetch(alumni_ids: list[int], stored_ids: set[int], *, refresh: bool) -> list[int]:
    """Skip anyone already derived, unless this run is a refresh."""
    if refresh:
        return list(alumni_ids)
    stored = set(stored_ids)
    return [pid for pid in alumni_ids if pid not in stored]


def debut_year(player: dict) -> int | None:
    raw = player.get("mlb_debut")
    if not raw or raw == "—":
        return None
    text = str(raw)[:4]
    if len(text) == 4 and text.isdigit():
        return int(text)
    return None


def feed_start_year(transactions: list[dict] | None) -> int | None:
    years = []
    for row in transactions or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get("date") or "")[:4]
        if len(text) == 4 and text.isdigit():
            years.append(int(text))
    return min(years) if years else None


def il_record_year(debut: int | None, feed_start: int | None) -> int | None:
    """The year the injured-list note should name, or None when the feed covers the debut.

    None leaves the ordinary empty line in place. A year replaces that
    line when there is no stay, and is added under the table when there is.
    """
    if debut is None:
        return None
    if feed_start is None:
        if debut < THIN_FEED_YEAR:
            return THIN_FEED_YEAR
        return None
    if debut < feed_start:
        return feed_start
    return None


def _serialize_stint(stint) -> dict:
    return {
        "start": stint.start.isoformat(),
        "end": stint.end.isoformat() if stint.end else None,
        "lists": [
            [length, when.isoformat(), how]
            for length, when, how in stint.lists
        ],
        "clubs": list(stint.clubs),
        "days": stint.days,
        "kind": stint.kind,
        "close": stint.close,
        # The derived label only -- the feed text it came from is read in
        # build_compact() and discarded, never written here.
        "label": stint.label,
        "label_source": stint.label_source,
        "label_conflict": stint.label_conflict,
        "multi": stint.multi,
    }


def build_compact(
    player: dict,
    transactions: list[dict] | None,
    season_stats: dict | None,
    names: dict,
    today: dt.date,
    windows: SeasonWindows,
) -> dict:
    """Stints, move lines, and season lines. Feed sentences are not copied."""
    rows = [row for row in (transactions or []) if isinstance(row, dict)]
    stints = build_stints(rows, windows, today)
    injury_labels.apply_labels(stints)
    debut, debut_club = player_sections._debut(player, rows)
    closes = {stint.end for stint in stints if stint.kind == "exact" and stint.end is not None}
    moves = build_moves(
        rows,
        debut=debut,
        debut_club_id=debut_club,
        names=names,
        il_close_dates=closes,
    )
    feed_start = feed_start_year(rows)
    stats = {}
    for year, groups in (season_stats or {}).items():
        try:
            key = str(int(year))
        except (TypeError, ValueError):
            continue
        if isinstance(groups, dict):
            stats[key] = groups
    return {
        "feed_start": feed_start,
        "il_record_year": il_record_year(debut_year(player), feed_start),
        "stints": [_serialize_stint(stint) for stint in stints],
        "moves": [
            {"date": move.date.isoformat(), "kind": move.kind, "text": move.text}
            for move in moves
        ],
        "season_stats": dict(sorted(stats.items())),
    }


def contains_feed_key(value) -> bool:
    """True if a derived record still carries a feed field."""
    if isinstance(value, dict):
        if FEED_KEYS.intersection(value):
            return True
        return any(contains_feed_key(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_feed_key(item) for item in value)
    return False


def shard_path(player_id: int, root: pathlib.Path | None = None) -> pathlib.Path:
    base = root or STORE_DIR
    return base / f"{int(player_id) % SHARD_COUNT:02d}.json"


def _load_shard(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    players = data.get("players") if isinstance(data, dict) else None
    return players if isinstance(players, dict) else {}


def load_store(root: pathlib.Path | None = None) -> dict[int, dict]:
    """Every derived record, keyed by player id. Missing directory is empty."""
    base = root or STORE_DIR
    out: dict[int, dict] = {}
    if not base.exists():
        return out
    for path in sorted(base.glob("[0-9][0-9].json")):
        for key, record in _load_shard(path).items():
            try:
                out[int(key)] = record
            except (TypeError, ValueError):
                continue
    return out


def save_record(player_id: int, record: dict, root: pathlib.Path | None = None) -> None:
    path = shard_path(player_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    players = _load_shard(path)
    players[str(int(player_id))] = record
    payload = json.dumps({"players": players}, separators=(",", ":"), sort_keys=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(payload)
    temporary.replace(path)


def store_bytes(root: pathlib.Path | None = None) -> int:
    base = root or STORE_DIR
    if not base.exists():
        return 0
    return sum(path.stat().st_size for path in base.glob("[0-9][0-9].json"))


def team_names_from_db(db: dict) -> dict[int, str]:
    """Canonical club names. Stored `team` strings are not a second table.

    `db` stays in the signature so callers do not change. The database mixes
    the current name and a former name for one id, and the last row used to win.
    """
    if not isinstance(db, dict):
        raise TypeError(db)
    return dict(CLUB_NAMES)


def fetch_compact(
    player: dict,
    names: dict,
    today: dt.date,
    windows: SeasonWindows,
    mlb_ids: set[int],
) -> dict:
    """One player. Transactions are not written. A stats failure still keeps the moves."""
    import fetch_mlb_data as mlb
    from update_service_time import _fetch_transactions_incremental

    player_id = int(player["id"])
    # use_cache=False: return the rows, do not add a transaction file.
    transactions = _fetch_transactions_incremental(
        player_id, full_refresh=True, use_cache=False
    )
    stats: dict = {}
    try:
        bio = mlb.get_player_bio(player_id)
        stats = mlb.select_major_league_season_stats(
            mlb.season_stats_from_bio(bio), mlb_ids
        ) or {}
    except Exception as exc:  # the moves are still worth keeping
        print(f"    season lines failed for {player_id}: {exc}", file=sys.stderr)
    return build_compact(player, transactions, stats, names, today, windows)


def _existing_page(page_dir: pathlib.Path, player_id: int) -> pathlib.Path | None:
    matches = sorted(page_dir.glob(f"{int(player_id)}-*.html"))
    if len(matches) == 1:
        return matches[0]
    return None


def _bump_lastmod(changed: list[tuple[str, str]], day: str) -> None:
    import write_player_pages as pages

    try:
        current = json.loads(pages.LASTMOD_PATH.read_text())
    except (OSError, ValueError):
        current = {}
    if not isinstance(current, dict):
        current = {}
    for rel, html in changed:
        current[rel] = {"hash": pages._content_key(html), "lastmod": day}
    pages.LASTMOD_PATH.write_text(json.dumps(current, indent=0, sort_keys=True))


def _bump_sitemap(rels: list[str], day: str) -> None:
    import write_player_pages as pages

    site = pages.SITE_URL.rstrip("/")
    wanted = {f"{site}/{rel}" for rel in rels}
    path = pages.DOCS / "sitemap-alumni.xml"
    if path.exists():
        text = path.read_text()

        def replace(match: re.Match) -> str:
            loc = match.group(1)
            if loc in wanted:
                return f"<loc>{loc}</loc><lastmod>{day}</lastmod>"
            return match.group(0)

        path.write_text(SITEMAP_URL_RE.sub(replace, text))
    index = pages.DOCS / "sitemap.xml"
    if index.exists():
        index.write_text(re.sub(
            r"(<loc>" + re.escape(site) + r"/sitemap-alumni\.xml</loc><lastmod>)\d{4}-\d{2}-\d{2}(</lastmod>)",
            rf"\g<1>{day}\2",
            index.read_text(),
        ))


def rewrite_pages(
    db: dict,
    generated_at: str,
    only_ids: set[int] | None = None,
) -> dict:
    """Rewrite alumni pages that have derived data. Does not delete a page.

    Pages whose visible content is unchanged are left byte-for-byte, so a
    second run does not move their sitemap date.
    """
    import write_player_pages as pages

    store = load_store()
    names = team_names_from_db(db)
    day = generated_at[:10]
    try:
        lastmod = json.loads(pages.LASTMOD_PATH.read_text())
    except (OSError, ValueError):
        lastmod = {}
    if not isinstance(lastmod, dict):
        lastmod = {}
    changed: list[str] = []
    unchanged = 0
    missing = 0
    failed = 0
    for player_id, compact in sorted(store.items()):
        if only_ids is not None and player_id not in only_ids:
            continue
        player = db.get(str(player_id))
        if player is None:
            player = db.get(player_id)
        if not isinstance(player, dict):
            missing += 1
            continue
        path = _existing_page(pages.PAGE_DIR, player_id)
        if path is None:
            missing += 1
            continue
        rel = f"p/{path.name}"
        try:
            html = pages.render(player, names, generated_at, [], None, compact)
        except Exception as exc:  # one bad page should not drop the rest
            failed += 1
            print(f"  page failed for {player_id}: {exc}", file=sys.stderr)
            continue
        previous = path.read_text()
        new_key = pages._content_key(html)
        old_key = pages._content_key(previous)
        # A daily service-time tick is not a change. Leave the file, and its
        # sitemap date, alone when the hashed content matches.
        if new_key == old_key:
            unchanged += 1
            continue
        if html != previous:
            path.write_text(html)
        lastmod[rel] = {"hash": new_key, "lastmod": day}
        changed.append(rel)
        if len(changed) % 200 == 0:
            pages.LASTMOD_PATH.write_text(json.dumps(lastmod, indent=0, sort_keys=True))
            _bump_sitemap(changed, day)
    if changed:
        pages.LASTMOD_PATH.write_text(json.dumps(lastmod, indent=0, sort_keys=True))
        _bump_sitemap(changed, day)
    return {
        "changed": len(changed),
        "unchanged": unchanged,
        "missing": missing,
        "failed": failed,
    }


def _report(alumni: list[int], stored: dict[int, dict], todo: list[int]) -> None:
    print(f"Alumni pages with no transaction file: {len(alumni)}")
    print(f"Derived records on disk: {len(stored)}")
    print(f"Still to fetch: {len(todo)}")
    print(f"Derived store: {store_bytes():,} bytes")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alumni page sections, stored compactly.")
    parser.add_argument("--status", action="store_true", help="Count the set and exit.")
    parser.add_argument("--limit", type=int, default=0, help="Fetch at most this many (0 = all remaining).")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch the whole alumni set again. The ordinary run skips ids already stored.",
    )
    parser.add_argument(
        "--rewrite-only",
        action="store_true",
        help="Rebuild pages from the derived store. No network.",
    )
    parser.add_argument(
        "--no-rewrite",
        action="store_true",
        help="Fetch and store, and leave the pages for a later --rewrite-only.",
    )
    args = parser.parse_args(argv)

    import write_player_pages as pages
    from update_service_time import CACHE_DIR, load_existing_db

    db = load_existing_db()
    page_ids = published_page_ids(pages.PAGE_DIR)
    cached = cached_transaction_ids(CACHE_DIR)
    alumni = discover_alumni_ids(page_ids, cached, db)
    stored = load_store()
    todo = ids_to_fetch(alumni, set(stored), refresh=args.refresh)

    if args.status:
        _report(alumni, stored, todo)
        return 0

    generated_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if args.rewrite_only:
        result = rewrite_pages(db, generated_at)
        print(
            f"Pages rewritten: {result['changed']}. "
            f"Unchanged: {result['unchanged']}. Missing: {result['missing']}. "
            f"Failed: {result['failed']}."
        )
        return 0

    if args.limit:
        todo = todo[: args.limit]
    print(f"Fetching {len(todo)} alumni. {len(stored)} already stored.")
    if not todo and not args.no_rewrite:
        result = rewrite_pages(db, generated_at)
        print(f"Nothing to fetch. Pages rewritten: {result['changed']}.")
        return 0

    names = team_names_from_db(db)
    windows = SeasonWindows.load()
    today = dt.date.today()
    failed: list[tuple[int, str]] = []
    done = 0
    for index, player_id in enumerate(todo, 1):
        player = db.get(str(player_id))
        if not isinstance(player, dict):
            failed.append((player_id, "no database record"))
            continue
        try:
            compact = fetch_compact(player, names, today, windows, set(MLB_CLUB_IDS))
            save_record(player_id, compact)
            done += 1
            print(
                f"  [{index}/{len(todo)}] {player.get('name')}: "
                f"{len(compact['stints'])} stints, {len(compact['moves'])} moves, "
                f"{len(compact['season_stats'])} seasons",
                flush=True,
            )
        except Exception as exc:  # one player should not end the run
            failed.append((player_id, str(exc)))
            print(f"  [{index}/{len(todo)}] FAILED {player.get('name')} ({player_id}): {exc}", file=sys.stderr)

    failure_path = STORE_DIR / "failures.json"
    if failed:
        STORE_DIR.mkdir(parents=True, exist_ok=True)
        failure_path.write_text(json.dumps(
            [{"id": player_id, "error": message} for player_id, message in failed],
            indent=2,
        ))
    elif failure_path.exists():
        failure_path.unlink()

    print(f"Stored {done}. Failed {len(failed)}. Derived store: {store_bytes():,} bytes.")
    if args.no_rewrite:
        return 1 if failed else 0
    result = rewrite_pages(db, generated_at)
    print(
        f"Pages rewritten: {result['changed']}. "
        f"Unchanged: {result['unchanged']}. Missing: {result['missing']}. "
        f"Failed: {result['failed']}."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
