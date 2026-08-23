# Big League Service Time Tracker

Estimated major league service time for every player on a 40-man roster,
plus a historical log of players who have since dropped off one. Rebuilt
every morning at 8am US/Eastern by a GitHub Actions job.

**Live site:** https://rainbowmang0.github.io/MLB-Service-Time-Tracker/

Service time is what decides when a player reaches salary arbitration and
free agency. 172 days credits a full year; 3.000 years reaches arbitration,
6.000 reaches free agency. MLB and the MLBPA maintain the official ledger
jointly and **do not publish it**, so everything here is reconstructed from
public roster-transaction descriptions and is an estimate.

Currently tracking **5,569 players**, each with a season-by-season
breakdown.

---

## Read this before trusting a number

**These are estimates, not the official figures.** They are reconstructed
from what MLB's public transaction feed says — "recalled", "optioned",
"placed on the 10-day injured list" — which is a good but imperfect proxy
for the ledger MLB actually keeps.

**How good, measured two independent ways.** Both checks live in
`scripts/` and run from the Actions tab.

*Against Baseball Reference* (the only genuinely independent check, since
it does not come from MLB's transaction feed). Nineteen players, hand-entered
from their B-R pages rather than scraped, compared as of a fixed offseason
date:

| | |
|---|---|
| exact to the day | 8 |
| within 2 days | 16 of 17 complete-history players |
| known gaps (flagged by the site itself) | 2 |
| failures | 0 |

*Against MLB's own historical rosters*, day by day, across four club-seasons
in three organizations and three decades:

| club-season | agreement | over-credits |
|---|---|---|
| Cleveland 2011 | 99.9% | 0.1% |
| Yankees 2014 | 96.8% | 0.2% |
| Yankees 2018 | 99.0% | 0.2% |
| Tampa Bay 2022 | 98.6% | 0.4% |

Over-crediting is tracked separately because it is the failure mode that has
caused every serious bug in this project — a figure that reads too high is
worse than one that reads too low, since missing history can only ever make
a number small.

> **These figures predate the rules change of 2026-08-23** (a Rule 5 return
> now closes a player's accrual interval). Both checks are being re-run
> against the recomputed database; expect them to move slightly.

**Where it is weakest.** A player whose career began before the transaction
feed thins out reads low, and the site says so per player rather than
hiding it — the table flags how many of his seasons are presumed rather than
read, and his profile marks each season as read from transactions, carried
forward, or presumed from his debut. A figure with presumed seasons behind
it is a floor, not an estimate.

**Not covered at all:** service-time-manipulation grievance outcomes (e.g.
Kris Bryant), which never appear in public transaction data.

---

## How it works

```
scripts/fetch_mlb_data.py         thin statsapi.mlb.com client, polite rate limiting
scripts/service_time.py           the service-time math and every domain rule
scripts/update_service_time.py    daily job: 40-man rosters -> compute -> merge -> JSON
scripts/backfill_history.py       resumable backfill of players no longer rostered
scripts/super_two.py              the Super Two cutoff, computed from the population
scripts/validate_service_time.py  checks figures against Baseball Reference
scripts/validate_against_rosters.py  checks intervals against MLB's historical rosters
scripts/validate_super_two.py     checks the cutoff against published figures
scripts/probe_player.py           everything the model knows about one player
scripts/commit_data.sh            commits generated data, surviving a concurrent push

docs/                             the static site (index.html, styles.css, app.js)
docs/data/service_time.json       the database: every field, one object per player
docs/data/index.json              what the browser downloads for the table (~0.2 MB)
docs/data/profiles/NN.json        per-player season detail, sharded by id % 64

.github/workflows/update-service-time.yml   daily 8am ET
.github/workflows/backfill-history.yml      manual, batched
.github/workflows/validate.yml              manual, read-only
```

Players who drop off a 40-man roster are **never deleted**. They stay with
`"on_40_man": false`, which is what makes this a historical log rather than
a roster snapshot. Eligibility labels and club names are shown only for
players actually on a roster today — "free agency eligible" is a statement
about a current contract, and a retired player's last known club is stale by
construction.

The browser downloads a compact index (0.21 MB) rather than the full 8.8 MB
database, and fetches a player's season detail only when you open his
profile — one ~55 KB shard out of 64, not the 3.5 MB of season detail in
total.

---

## Run it locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python tests/test_service_time.py        # 119 tests, no pytest needed

python scripts/update_service_time.py --limit 25   # a quick real run
cd docs && python3 -m http.server 8000             # then open localhost:8000
```

A full run fetches transaction history for ~1,360 rostered players and takes
about half an hour.

Flags on `update_service_time.py`:

- `--limit N` — only the first N roster players. Fast, and skips the publish
  sanity checks, which a deliberately truncated run would trip.
- `--full-refresh` — ignore the transaction cache and re-pull every history.
- `--recompute-derived` — no API calls at all: reload the stored database,
  redo the derived passes (Super Two, index, profiles) and rewrite the
  published files. For a change to how something is *derived* from records
  that are already correct.
- `--ignore-sanity` — publish even if the run looks like an outage. For the
  day the guard is wrong, not for getting past it.

### The publish guard

A daily run refuses to write anything if fewer than 900 players come back
from the roster fetch, if the roster shrinks more than 25% overnight, or if
more than 10% of players fail to build. All three are shapes that would
otherwise publish confidently wrong data over a correct file — an empty
fetch would flag every player in the database as no longer rostered and the
job would report success.

---

## Deploying your own

1. Push the project to a GitHub repository.
2. **Settings → Actions → General → Workflow permissions** → *Read and write
   permissions*. Without this the daily job runs and silently fails to commit
   its results.
3. **Settings → Pages** → *Deploy from a branch*, branch `main`, folder
   `/docs`.
4. **Actions → "Daily Service Time Update" → Run workflow**, with
   **force** checked. The workflow otherwise only does real work at 8am
   Eastern, so an unforced manual run outside that window completes instantly
   without fetching anything.
5. **Actions → "Backfill Historical Players"** to add players who are not on
   a roster today. Batched and resumable — re-trigger until it reports zero
   remaining.

### Why the schedule has two cron entries

GitHub Actions cron is UTC-only, and US/Eastern shifts by an hour twice a
year. The workflow fires at both 12:00 and 13:00 UTC and checks the real
Eastern wall-clock hour, so it does real work exactly once a day at 8am
Eastern year-round with no manual upkeep.

---

## Data source

`statsapi.mlb.com` — the public API behind MLB.com's own stats pages.
Unofficial and undocumented, no key required, and widely relied on by the
baseball-analytics community.

The Baseball Reference figures in `data/reference_service_time.json` are
entered **by hand**, deliberately not scraped, both to respect B-R's terms
and because a handful of hand-checked numbers is enough to catch a
systematic error.

## Not affiliated with MLB

This is an independent project. It is not affiliated with, endorsed by, or
sponsored by Major League Baseball, the Major League Baseball Players
Association, or any of their clubs. Descriptive references to MLB, its
clubs, and its public API identify what the data is about and where it comes
from; no ownership or endorsement is implied.

## License

No license file yet, which under GitHub's terms means all rights are
reserved: the code is public to read but not licensed for reuse. Adding an
`LICENSE` (MIT is the usual choice for something like this) would change
that.
