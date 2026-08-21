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
scripts/probe_coverage.py      live API probe for finding #9 (run via Actions)
scripts/generate_demo_data.py  bundled sample data generator (no network)
tests/test_service_time.py     45 tests, no pytest needed: `python tests/test_service_time.py`
docs/                          the static site (index.html, styles.css, app.js)
docs/data/service_time.json    the only data file the frontend reads
data/cache/transactions/       per-player transaction cache (rostered players only)
data/backfill_state.json       resumable backfill progress
.github/workflows/update-service-time.yml   daily 8am ET
.github/workflows/backfill-history.yml      manual, batched
.github/workflows/probe-coverage.yml        manual, read-only diagnostic
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

⚠️ **This finding is now in doubt — see finding #9.** The sample may have
consisted of players who had no pre-2009 existence to report, which would
make the zeros an artifact of the sample rather than of the feed.

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

### 4. A retired player's clock has to be stopped explicitly

Found the hard way: the first live backfill batch credited **246 of 500**
retired players with 15+ years of service. Angel Guzman, who last pitched in
2010, read 20.030. Joe Mauer read 17.137.

Careers do not reliably end with a transaction this parser recognizes. The
overwhelmingly common final move is `"elected free agency"` (799 rows across
the 1,356 cached histories), which is *not* a stop keyword — so the interval
it leaves open runs all the way to `horizon_end`, i.e. today.

**The obvious fix is wrong.** Adding `"elected free agency"` to
`ACTIVE_STOP_KEYWORDS` breaks 272 *currently rostered* players, because
there is no matching start keyword for the re-signing that follows — the feed
says "Team signed free agent RHP X", and `"signed"` can't be a start keyword
since minor league deals use the same verb. Measured over the cached
histories: Max Scherzer −628 days (3.6 years), Nick Martinez −1,249, Kenley
Jansen −473. Don't do it.

Instead `accrual_ceiling` caps accrual at the **end of the last season the
player appeared in** (`lastPlayedDate` from the bio endpoint, falling back to
his final transaction date if the API omits it). It is the exact mirror of
`accrual_floor` and applies only when `currently_rostered=False`, so the
daily job is provably unaffected — the ceiling is `None` there.

End of *season*, not last game, because service time is roster time: a player
keeps accruing while on the active roster or IL after his final appearance.

`backfill_history.py` now also warns loudly about any record ≥20.000 years.
The original bug was caught only by eyeballing the last five lines of a
500-line log.

**Verified live on 2026-08-21.** Re-running batch 1 with the fix:

| | before | after |
|---|---|---|
| retired players ≥20.000 yrs | 3 | 0 |
| retired players ≥15.000 yrs | 246 | 0 |
| rostered players changed | — | 0 of 1,356 |

Guzman 20.030 → 3.052, Mauer 17.137 → 9.159, Hoffman 17.142 → 1.164 (a
partial-history floor — only his 2009-10 seasons are visible). The batch also
ran in 14.6 min vs 31, because capping the season range cuts API calls.

### 5. A minor league club "activating" a player is NOT proof he left the majors

The tempting fix for finding #6 below is to stop treating non-MLB
transactions as noise to discard and start treating them as evidence: if
Durham activates you, you are not on an MLB active roster. Measured over the
1,356 cached histories, this removes 55,749 days from 343 players (25% of
them) — because **rehab assignments** look identical. A player on the MLB
injured list sent to Triple-A on rehab is still accruing service time, and
the affiliate still "activates" him. Vladimir Guerrero Jr. loses 3.84 years,
Goldschmidt 3.84, Lindor 3.50, Bobby Witt Jr. 3.74. Don't do this either.

(Exhibition entities — All-Star, Futures, Fall Stars, "X Prospects", college
and high school workouts — are separately identifiable by name, 2,401 rows.
They are not clubs at all and must never be read as roster assignments.)

### 6. OPEN: the clock still bridges gaps spent outside MLB

`accrual_ceiling` (finding #4) stops a retired player's clock at the end of
his career. It does **not** close a gap in the *middle* of one. A player who
leaves MLB for independent ball, Japan, or a long minor league stretch, and
whose departure is phrased in a way no stop keyword matches, keeps accruing
across the years he was gone.

Lew Ford was the presumed case: credited 1,085 days (6.053) against a
window that, assuming coverage starts in 2009, caps at 688. **On closer
inspection he is probably correct** — 1,085 days is 6.31 years, close to
his real career total, reachable only by crediting his 2003-2007 Minnesota
seasons. See finding #9. The gap-bridging concern below may therefore be
smaller than it first appeared, or may not be real at all.

Both obvious fixes are measured and harmful — the free-agency stop keyword
(finding #4) and the minor-league-activation stop (finding #5). No safe fix
is known yet. What exists instead is detection:
`report_impossible_totals()` in `backfill_history.py` checks
`credited_days <= 172 × (ceiling_year − max(debut_year, 2009) + 1)`.

**That check is currently unreliable — see finding #9.** It assumes no
pre-2009 accrual, and its hits on the backfill look like false positives.
Treat a hit as "worth a look", not as proof.

Records now carry `last_played` and `accrual_ceiling` so a suspect number can
be checked directly. Diagnosing Ford stalled precisely because they weren't
stored.

**Prevalence is unknown** — it can only be measured by re-running a batch
with the instrumented code and reading the warning block.

### 7. Keyword matching must tolerate the player's name

The feed writes `<Team> <verb> <POS> <Player Name> <rest>`:

```
Cleveland Guardians designated RHP Some Name for assignment.
Boston Red Sox sent LHP Some Name outright to Worcester Red Sox.
Seattle Mariners claimed C Some Name off waivers from Miami Marlins.
```

So every multi-word keyword ("designated for assignment", "sent outright",
"claimed off waivers", "placed on the", "reinstated from the") matched
nothing. Measured over 64,635 cached descriptions: **11 of 16 keywords never
fired once, and 61% of transactions were ignored.**

The damage was one-sided — DFA and outright are STOPS, so players removed
from a roster kept accruing. Fixing it (regex with a bounded wildcard for the
name) removes 22,974 phantom days from 231 of 1,321 cached players (17%).
Tyler Austin 9.99 → 3.05 (his real figure is about three years; he bounced
between orgs and Japan). Players never DFA'd do not move at all: Scherzer,
Judge, Lindor, Goldschmidt, Jansen and Verlander are byte-identical.

`ACTIVE_START_KEYWORDS` / `ACTIVE_STOP_KEYWORDS` are now **documentation
only**. Editing them changes nothing — `_START_RE` / `_STOP_RE` do the work.

Two traps the patterns must keep avoiding:
- `sent ... outright` vs `sent ... on a rehab assignment` share a verb. A
  rehab stint keeps accruing (the player is on his MLB club's IL).
- `disabled list` is the pre-2019 name for the injured list (1,419 cached
  rows). Both must be treated as accruing.

### 8. The clock stops at today, not at season end

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
  writing that has not been done — **measured 2026-08-21: 0 of 64,643 cached
  rows carry a team ID**, so the MLB-club filter is still a complete no-op for
  every one of the 1,356 rostered players, and only the debut floor is
  protecting them. The backfill is unaffected (it fetches fresh with
  `use_cache=False`, so its rows do have IDs).
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
- 1,856 players in the database (1,356 rostered + 500 backfilled).
- 2020 proration, debut floor, demo purge, pagination, and
  `history_complete` flagging are all committed and live.
- 45 tests passing.

### Immediate next steps

0. **Measure how common finding #6 is.** Re-run backfill batch 1 with the
   instrumented code and read the `!! WARNING: N record(s) credit more
   service time than their season window allows` block. That number is
   currently unknown and decides whether #6 is a curiosity or a rewrite.
   The 1,000 players already backfilled predate `last_played` /
   `accrual_ceiling`, so they cannot be checked in place — they need
   re-running (delete their ids from `processed_ids`, or revert the two
   batch commits and start over; ~15 min per batch).

1. **Run the historical backfill.** Actions → "Backfill Historical Players" →
   batch 500 → re-trigger until it reports zero remaining. ~4,000 players to
   add, ~8 runs. Two batches are done (1,000 players). The accrual-ceiling fix
   is verified live (finding #4); the open question is #6. Failures are cheap:
   state is committed per batch and a retry resumes.
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
- **Keep summaries short.** Lead with the result and the decision needed.
  Detail belongs in this file and in commit messages, not in chat.
- Prefer `git apply` patches over hand-editing: they fail loudly and safely.
- Verify with checksums after any file transfer. It has caught real corruption.
- `.github` and other dot-directories are invisible in iPadOS Files and get
  silently skipped by folder uploads. This ate an afternoon once.

### 9. OPEN: does the feed actually carry pre-2009 history?

Finding #1 says coverage begins in 2009. Three independent observations from
2026-08-21 suggest it is wrong, or at least overstated:

- The cache holds transactions dated **2006-07-02** and **2008-06-24**
  (minor league signings for Albert Suárez and Ildemaro Vargas) and
  **2008-07-21** for Kyle Higashioka. The endpoint clearly can return
  pre-2009 rows.
- Only 3 of 64,643 cached rows predate 2009 — but that sample is biased.
  Nearly every cached player is currently rostered, so debuted well after
  2009 and had no professional transactions before then. Zero pre-2009 rows
  is what you would expect either way, so it is not evidence.
- The backfill arithmetic only works if pre-2009 seasons are being credited.
  Lew Ford: 1,085 days against a 2009-2012 window capping at 688. Angel
  Guzman: 568 days, which is exactly 172 × 3 + 52 — four seasons beginning
  in 2006, his debut year. And Ford's 1,085 days (6.31 years) lands close to
  his real career service time, which requires his 2003-2007 Minnesota
  seasons to be counted.

**Why it matters.** If the feed does cover pre-2009:
- `report_impossible_totals()` is wrong and its hits are false positives.
- `history_complete: false` is too pessimistic for many players, and the UI
  is flagging good figures as "partial".
- The "no data" treatment of 0.000 records may be mislabelling some players.
- Finding #6 (gap bridging) may be much smaller than feared, or not real.

**How to settle it in one query** — needs live API access, which the sandbox
does not have (statsapi.mlb.com is blocked by egress policy):

**Actions → "Probe Transaction Coverage" → Run workflow.** That runs
`scripts/probe_coverage.py`, which queries the flagged players year by year
and prints a verdict. It exists as a workflow because the sandbox cannot
reach statsapi.mlb.com — the egress proxy 403s that host — while Actions
runners can. The job only reads, commits nothing, and is deliberately
outside the update concurrency group, so it is safe to run at any time.

The distinction that matters is **major-league** rows, not any rows: a 2006
minor league signing does not show that a player's MLB roster history is
visible. The probe counts them separately.
