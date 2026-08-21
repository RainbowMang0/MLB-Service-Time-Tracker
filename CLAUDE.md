# MLB Service Time Tracker — project context

Handoff notes for Claude Code. This file captures decisions, hard-won
empirical findings, and open work so a fresh session doesn't have to
rediscover any of it.

- **Repo:** `RainbowMang0/MLB-Service-Time-Tracker`
- **Live site:** https://rainbowmang0.github.io/MLB-Service-Time-Tracker/
- **Hosting:** GitHub Pages, `main` branch, `/docs` folder
- **Owner is not a full-time developer.** Explain the *why*, not just the
  command. Prior web experience, ~5 years stale.

---

## What this is

A static site that estimates MLB service time for every player on a 40-man
roster, refreshes daily at 8am US/Eastern via GitHub Actions, and keeps
players in the database after they drop off a roster so it doubles as a
historical log.

Service time drives arbitration and free agency eligibility: 172 days = one
credited year, 3.000 years = arbitration eligible, 6.000 years = free agency
eligible. MLB and the MLBPA maintain the official ledger and **do not publish
it**. Everything here is reconstructed from public transaction descriptions
and is an estimate. The site says so prominently; keep it that way.

Research done in Aug 2026 found no public source that does this: Baseball
Reference publishes an Opening Day snapshot only, MLB Trade Rumors covers
arbitration-eligible players during the offseason, TJStats tracks active
roster days for rookie eligibility (a different statistic — it excludes IL
time, which *does* count toward service time). The niche is real.

---

## Layout

```
scripts/fetch_mlb_data.py      thin statsapi.mlb.com client, polite rate limiting
scripts/service_time.py        the service-time math + all domain rules
scripts/update_service_time.py daily job: 40-man rosters -> compute -> merge -> JSON
scripts/backfill_history.py    resumable backfill of non-rostered players (2009+)
scripts/validate_service_time.py  --as-of validation against known Baseball Reference figures
scripts/generate_demo_data.py  bundled sample data generator (no network)
tests/test_service_time.py     24 tests, no pytest needed: `python tests/test_service_time.py`
docs/                          the static site (index.html, styles.css, app.js)
docs/data/service_time.json    the only data file the frontend reads
data/cache/transactions/       per-player transaction cache (rostered players only)
data/backfill_state.json       resumable backfill progress
.github/workflows/update-service-time.yml   daily 8am ET
.github/workflows/backfill-history.yml      manual, batched
```

**Run the tests after touching `service_time.py`.** They encode real findings,
not toy cases — several were written directly from live API output.

---

## Domain findings — measured, not assumed

These cost real debugging. Don't re-litigate them without new evidence.

### 1. Transaction coverage begins in 2009

The `/transactions` endpoint returns nothing usable before 2009. Measured by
sampling six players per season and counting transactions involving a major
league club:

```
2005: 0   2006: 0   2007: 0   2008: 0   2009: 17   2010: 17   2011: 20   2012: 33   2013: 43
```

Jim Abbott was traded during 1995; his feed is empty. This is not a rate-limit
or query-shape problem — the data does not exist.

**Consequence:** a player who debuted before 2009 can never have his full
history reconstructed and will always read low. Those records carry
`history_complete: false` and the UI flags them "partial". `Justin Verlander`
(2005) and `Clayton Kershaw` (2008) are the obvious live examples.

### 2. The transaction feed is not limited to MLB

`/transactions` returns a player's *entire tracked history*: high school
showcases, college programs, minor league affiliates, All-Star and Futures
Game rosters. These use the same verbs as major league moves:

```
2023-02-08 | Grand Canyon Antelopes activated SS Jacob Wilson.
2025-07-14 | American League All-Stars activated SS Jacob Wilson.
2024-06-16 | College Workout activated LHP Gage Jump.
```

Matching on wording alone started Jacob Wilson's clock 17 months before his
debut and reported him at 3.000 years instead of 2.048. Two defenses now:

- **`accrual_floor`** (primary): intervals are clipped to the player's
  `mlbDebutDate`. This is authoritative and does most of the work.
- **MLB-club filter** (secondary): transactions are dropped unless a team ID
  matches one of the 30 clubs. Catches post-debut minor league events that the
  floor can't, e.g. a Triple-A club "activating" an optioned player.

### 3. 2020 must be prorated

The 2020 season ran ~66 days instead of ~186. MLB and the MLBPA agreed service
time would be scaled by `186/B` rather than credited as raw days, so a player
rostered all season earned a full year. Crediting raw days leaves every 2020
participant ~0.6 years short — Aaron Judge read 9.095 instead of 10.027.

Handled by `_prorate_shortened_season()` in `service_time.py`, applied before
the 172-day cap. `SHORTENED_SEASONS = {2020}`.

The `186/B` formula comes from contemporaneous reporting of the agreement, not
from MLB's ledger. It reproduces Judge's known figure, which is decent
evidence — but if a 2020-era player looks wrong, check here first.

### 4. The clock stops at today, not at season end

`horizon_end` defaults to the last season's end date. Left alone, every
currently-rostered player is credited for the remaining weeks of a season that
hasn't been played. The daily job passes `horizon_end=TODAY`.

---

## Bug history worth knowing

- **Cache stored team names, not IDs.** The MLB-club filter checked for team
  objects with IDs, but the cache wrote a name string, so the filter silently
  passed everything. The debut floor masked it. Fixed; cached entries written
  before the fix lack IDs and are treated as unjudgeable (kept). **A
  `--full-refresh` run is needed to fully realize the filter.** As of this
  writing that has not been done.
- **Demo players persisted forever.** The merge logic never deletes players,
  so seven bundled sample records ("Sample City Marlins") survived into live
  data. `MIN_REAL_PLAYER_ID = 100000` filters them; real MLB person IDs are
  six digits.
- **Workflow committed a path that might not exist.** `git add data/cache`
  exits 128 when the directory is absent, failing the job after 30 minutes of
  work. Now guarded.

---

## Current state

- Daily workflow works and has run unattended successfully.
- ~1,363 players in the database (40-man rosters + previous players).
- 2020 proration, debut floor, demo purge, pagination, and
  `history_complete` flagging are all committed and live.
- 22 tests passing.

### Immediate next steps

1. **Run the historical backfill.** Actions → "Backfill Historical Players" →
   batch 500 → re-trigger until it reports zero remaining. ~4,000 players to
   add, ~8 runs. This code has been tested against a stubbed API but **never
   against the live one** — the first batch is its real trial. Failures are
   cheap: state is committed per batch and a retry resumes.
2. **Then run the daily workflow once with `full_refresh` checked**, to
   rebuild the cache with team IDs and activate the MLB-club filter.
3. **Validate.** `scripts/validate_service_time.py` now exists:
   `build_player_record()` takes a `horizon_end` override, so it can compute
   what a player's service time WOULD HAVE READ as of a past date (e.g. a
   prior Opening Day) rather than only "as of today." Compare that against
   Baseball Reference's `s.YYYY` figures, which are a fixed target rather
   than a moving one. Doing this required a real bug fix along the way:
   `build_global_active_intervals()` previously only used `horizon_end` to
   cap the trailing *open* interval — a stop transaction (option/DFA/release)
   dated *after* `horizon_end` would still truncate an earlier interval that,
   as of that date, hadn't ended yet. It now drops every transaction dated
   after `horizon_end` before building intervals at all. Covered by a new
   regression test (`test_as_of_past_date_ignores_later_transactions`).

   **Still needed:** the reference file (`data/reference_service_time.json`)
   ships empty — copy `data/reference_service_time.example.json`, fill in a
   handful of well-known players' `s.YYYY` figures by hand from their
   Baseball Reference pages (deliberately not scraped), and run
   `python scripts/validate_service_time.py`. Nothing has actually been
   checked against a real external number yet; this just makes doing so
   possible. Requires network access to the live MLB Stats API, so it can't
   run in this offline sandbox — run it locally or from a Codespace/Action.

### Known limitations

- Super Two status is *flagged as a candidate* only. The real cutoff requires
  league-wide data the API doesn't expose.
- No handling for paternity/bereavement edge cases beyond keyword matching.
- Service-time-manipulation grievance outcomes (e.g. Kris Bryant) are invisible
  to public transaction data.
- The frontend loads the entire JSON at once. Fine at ~5,300 players; if the
  dataset grows much beyond that, split the file and lazy-load history.

---

## Working style notes

- The owner has been working from an **iPad via GitHub Codespaces**. Safari's
  terminal **corrupts pastes over a few KB** — a 14KB base64 blob came through
  with commands interleaved into data. Deliver code as files through GitHub's
  web uploader, or as small pastes. If they've moved to a desktop, this no
  longer applies.
- Prefer `git apply` patches over hand-editing: they fail loudly and safely.
- Verify with checksums after any file transfer. It has caught real corruption.
- `.github` and other dot-directories are invisible in iPadOS Files and get
  silently skipped by folder uploads. This ate an afternoon once.
