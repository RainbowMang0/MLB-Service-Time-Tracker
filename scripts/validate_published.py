#!/usr/bin/env python3
"""
validate_published.py
---------------------
Do the three published files agree with each other?

WHY THIS EXISTS
===============
The site publishes the same figures three times, in three shapes:

    docs/data/service_time.json   the database -- every field, one object
                                  per player, and the pipeline's own source
                                  of truth on the next run
    docs/data/index.json          the compact table payload the browser
                                  actually downloads
    docs/data/profiles/NN.json    per-player season detail, sharded by id % 64

Only the last two are ever read by a visitor, and they are DERIVED. So a
defect in the derivation publishes wrong numbers while the database sitting
next to them is perfectly correct, and every other check in this project --
which all read the database -- passes.

That is not hypothetical. Both workflows once ran `git add` on
service_time.json alone, so index.json was frozen at whatever had last been
committed by hand while the database updated daily underneath it. The site
served stale figures and nothing noticed, because nothing compared them.

WHAT IS CHECKED
===============
  * every database player appears in the index, and vice versa
  * day counts, roster flags and Super Two flags match
  * a club is published for rostered players only, and blanked otherwise
    (a non-rostered player's stored club is stale by construction, so
    printing it would assert a roster spot he does not hold)
  * every player has a profile, in the shard his id actually maps to
  * each profile's total matches the database
  * each profile's season rows sum to that total
  * every absolute URL in every published page matches the site's own
    SITE_URL, and every internal link resolves to a file that exists

Offline: reads only what is on disk, so it validates exactly what would be
served. Exits non-zero on any disagreement.

    python scripts/validate_published.py
"""

from __future__ import annotations

import glob
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB_FILE = ROOT / "docs" / "data" / "service_time.json"
INDEX_FILE = ROOT / "docs" / "data" / "index.json"
PROFILE_DIR = ROOT / "docs" / "data" / "profiles"
PROFILE_SHARDS = 64


def check_published() -> list[str]:
    problems: list[str] = []

    db = {p["id"]: p for p in json.loads(DB_FILE.read_text())["players"]}
    idx = json.loads(INDEX_FILE.read_text())
    teams = idx["teams"]

    seen = set()
    for row in idx["players"]:
        pid, name, team_ix, _pos_ix, days, on40, _missing, super_two = row
        seen.add(pid)
        rec = db.get(pid)
        if rec is None:
            problems.append(f"index carries {name} ({pid}), absent from the database")
            continue
        if days != rec["service_days_total"]:
            problems.append(
                f"{name}: index says {days} days, database says {rec['service_days_total']}"
            )
        if bool(on40) != bool(rec.get("on_40_man")):
            problems.append(f"{name}: index and database disagree on roster status")
        if bool(super_two) != bool(rec.get("super_two_candidate")):
            problems.append(f"{name}: index and database disagree on the Super Two flag")

        published_club = teams[team_ix]
        if on40 and published_club != (rec.get("team") or ""):
            problems.append(
                f"{name}: index club {published_club!r}, database {rec.get('team')!r}"
            )
        if not on40 and published_club:
            problems.append(
                f"{name}: a club ({published_club!r}) is published for a player who is "
                "not on a 40-man; his stored club is stale by construction"
            )

    for pid in set(db) - seen:
        problems.append(f"{db[pid].get('name')} ({pid}) is in the database but not the index")

    profiled = 0
    for path in sorted(glob.glob(str(PROFILE_DIR / "*.json"))):
        bucket = int(pathlib.Path(path).stem)
        shard = json.loads(pathlib.Path(path).read_text())
        for key, profile in shard.get("players", {}).items():
            profiled += 1
            pid = int(key)
            if pid % PROFILE_SHARDS != bucket:
                problems.append(
                    f"{profile.get('name')} sits in shard {bucket:02d} but his id maps "
                    f"to {pid % PROFILE_SHARDS:02d}, so the browser will not find him"
                )
            rec = db.get(pid)
            if rec is None:
                problems.append(f"profile {pid} has no database record")
                continue
            if profile.get("days") != rec["service_days_total"]:
                problems.append(
                    f"{profile.get('name')}: profile says {profile.get('days')} days, "
                    f"database says {rec['service_days_total']}"
                )
            rows = sum(int(s.get("d") or 0) for s in profile.get("seasons") or [])
            if rows != profile.get("days"):
                problems.append(
                    f"{profile.get('name')}: season rows sum to {rows}, profile total "
                    f"is {profile.get('days')} -- the profile contradicts its own table"
                )

    for pid in set(db) - {int(k) for p in glob.glob(str(PROFILE_DIR / "*.json"))
                          for k in json.loads(pathlib.Path(p).read_text()).get("players", {})}:
        problems.append(f"{db[pid].get('name')} ({pid}) has no profile")

    print(f"database {len(db)} | index {len(idx['players'])} | profiles {profiled}")
    return problems


def check_links() -> list[str]:
    """Every published URL points at this site, and every internal link resolves.

    This is the check that makes a domain move safe. SITE_URL is derived from
    docs/CNAME, so the generated pages follow a new domain automatically -- but
    a canonical or og:url left behind in a HAND-EDITED file does not, and a
    stale canonical is worse than none: it tells a search engine the real page
    lives at a URL that no longer serves it.

    The link half catches the other silent failure: slug() and playerSlug()
    drifting apart, or a club page linking to a player who has dropped off a
    40-man. Both produce a 404 that nothing else would notice.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from write_player_pages import BASE_PATH, SITE_URL

    problems: list[str] = []
    docs = pathlib.Path(__file__).resolve().parents[1] / "docs"
    site_root = f"{SITE_URL}/"

    # 1. Hand-edited files that carry an absolute URL of their own.
    for name in ("index.html", "taxes.html", "neutrality.html"):
        page = docs / name
        if not page.exists():
            continue
        for url in re.findall(r'(?:og:url" content|rel="canonical" href)="([^"]+)"',
                              page.read_text()):
            if not url.startswith(site_root):
                problems.append(
                    f"docs/{name}: og:url/canonical is {url!r}, but the site is "
                    f"served from {site_root!r} -- update it by hand "
                    "(generated pages follow docs/CNAME on their own)"
                )

    # 2. Every page: absolute URLs must be ours, relative links must resolve.
    #    index.html is in here because it carries the one hand-written link into
    #    the generated section ("t/"), and a hand-written link is exactly the
    #    kind that rots without anyone noticing.
    generated = ([docs / "index.html", docs / "404.html",
                  docs / "taxes.html", docs / "neutrality.html"]
                 + sorted(docs.glob("p/*.html")) + sorted(docs.glob("t/*.html")))
    generated = [p for p in generated if p.exists()]
    missing_targets: set[str] = set()
    foreign = 0
    for page in generated:
        text = page.read_text()
        for url in re.findall(r'(?:href|content)="(https?://[^"]+)"', text):
            if url.startswith("https://github.com/"):
                continue  # the methodology link, deliberately off-site
            if not url.startswith(site_root):
                foreign += 1
                if foreign <= 5:
                    problems.append(f"{page.relative_to(docs.parent)}: foreign URL {url!r}")
        for href in re.findall(r'href="((?!https?:|#|mailto:)[^"]+)"', text):
            clean = href.split("#")[0].split("?")[0]
            if not clean:
                continue
            if clean.startswith("/"):
                # A site-absolute href resolves against the SITE root, not the
                # filesystem root. Joining it onto a path with pathlib silently
                # discards the left side, so "/favicon.svg" would be looked for
                # at the top of the disk and reported dead on every page.
                # BASE_PATH is "/" on a custom domain and "/<repo>/" on a
                # project page; either way what follows it is relative to docs/.
                rel = clean[len(BASE_PATH):] if clean.startswith(BASE_PATH) else clean[1:]
                target = (docs / rel).resolve() if rel else docs
            else:
                target = (page.parent / clean).resolve()
            # A directory href is served by its index.html, so that is what has
            # to exist -- "t/" resolving to a directory with nothing in it would
            # be a 404 for a visitor and this check would have passed it.
            if clean.endswith("/") or target.is_dir():
                target = target / "index.html"
            if not target.exists():
                missing_targets.add(f"{page.relative_to(docs.parent)} -> {href}")

    for broken in sorted(missing_targets)[:10]:
        problems.append(f"dead internal link: {broken}")
    if len(missing_targets) > 10:
        problems.append(f"... and {len(missing_targets) - 10} more dead internal links")

    print(f"pages {len(generated)} | dead links {len(missing_targets)} | site {site_root}")
    return problems


def check_config_published() -> list[str]:
    """The rulesets the browser fetches must exist and match config/.

    docs/data/config/ is a published COPY of config/ (see publish_config() in
    update_service_time.py), because config/ sits outside docs/ and GitHub
    Pages will not serve it. A copy that drifts from its source is the same
    failure this project already had when index.json sat frozen while the
    database updated underneath it -- the duty-day tool would compute against
    a different tax table than the one in the repo, and nothing would say so.

    Also asserts that no jurisdiction carries a tax rate without being marked
    verified. That invariant is what keeps a guessed rate from reaching a
    user; it is checked in the JS tests too, and it is worth checking on both
    sides of the fence.
    """
    problems: list[str] = []
    root = pathlib.Path(__file__).resolve().parents[1]
    src_dir = root / "config"
    out_dir = root / "docs" / "data" / "config"

    if not src_dir.exists():
        return ["config/ is missing entirely"]
    if not out_dir.exists():
        return [
            "docs/data/config/ is missing -- the duty-day tool cannot load its "
            "rules. Run update_service_time.py (any mode) to publish it."
        ]

    sources = sorted(src_dir.rglob("*.json"))
    for src in sources:
        dest = out_dir / src.relative_to(src_dir)
        if not dest.exists():
            problems.append(f"{src.relative_to(root)} is not published to docs/data/config/")
        elif dest.read_bytes() != src.read_bytes():
            problems.append(
                f"{dest.relative_to(root)} differs from {src.relative_to(root)} "
                "-- the published copy is stale"
            )

    for extra in sorted(out_dir.rglob("*.json")):
        if not (src_dir / extra.relative_to(out_dir)).exists():
            problems.append(
                f"{extra.relative_to(root)} is published but has no source in config/"
            )

    states_path = src_dir / "tax" / "2026-states.json"
    if states_path.exists():
        states = json.loads(states_path.read_text())
        for code, j in states.get("jurisdictions", {}).items():
            if isinstance(j.get("top_marginal_rate"), (int, float)) and j.get("status") != "verified":
                problems.append(
                    f"tax jurisdiction {code} carries a rate but is not marked "
                    "verified -- an unchecked rate must never be publishable"
                )
    return problems


def main() -> None:
    problems = check_published() + check_links() + check_config_published()
    if not problems:
        print("\nAll three published files agree.")
        return
    print(f"\n{len(problems)} disagreement(s):")
    for problem in problems[:40]:
        print(f"    {problem}")
    if len(problems) > 40:
        print(f"    ... and {len(problems) - 40} more")
    sys.exit(1)


if __name__ == "__main__":
    main()
