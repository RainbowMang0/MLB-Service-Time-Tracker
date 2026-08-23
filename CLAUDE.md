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
scripts/super_two.py           the real Super Two cutoff, from the whole population
scripts/validate_super_two.py  --  checked against published cutoffs (offline)
scripts/probe_coverage.py      live API probe for finding #9 (run via Actions)
scripts/generate_demo_data.py  bundled sample data generator (no network)
tests/test_service_time.py     57 tests, no pytest needed: `python tests/test_service_time.py`
docs/                          the static site (index.html, styles.css, app.js)
docs/data/service_time.json    the database: every field, one object per player
docs/data/index.json           what the browser downloads for the table (0.21 MB)
docs/data/profiles/NN.json     per-player season detail, sharded by id % 64
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

### 1. Transaction coverage is SPARSE before 2009, not absent

⚠️ **Corrected 2026-08-21.** The original claim below — that the feed returns
nothing before 2009 — is wrong. `scripts/probe_coverage.py` run against the
live API returned 10 major-league transactions from 2005-2008 across five
players:

```
2005-01-13 | Pittsburgh Pirates purchased Jack Wilson.
2006-08-11 | Minnesota Twins activated LF Lew Ford from the 15-day disabled list.
2007-09-04 | Minnesota Twins activated LF Lew Ford.
2008-05-27 | Pittsburgh Pirates activated SS Jack Wilson from the 15-day disabled list.
```

The volume is thin — Lew Ford has one row in 2006 and one in 2007 — so
pre-2009 history is **partial**, not complete, and not absent either. The
honest model is "sparse and thinning as you go back", not a hard cutoff.

The original zeros were a sampling artifact: the six-players-per-season
sample, and the 3-of-64,643 cache measurement, both drew on players who
simply had no professional existence before 2009, so zero rows was the
expected result either way and proved nothing.

**What this invalidated** (all built on the hard-cutoff premise):
- `report_impossible_totals()` — its bound assumes no pre-2009 accrual, so
  its hits are false positives. Lew Ford's 1,085 days are real Minnesota
  seasons, not a defect.
- `history_complete: false` for every pre-2009 debut — too pessimistic. The
  UI flags genuinely-good figures as "partial".
- The "no data" display treatment for 0.000 records — same premise.
- Finding #6's gap-bridging concern — probably never existed.

Still true: a pre-2009 career reads **low**, because coverage thins rather
than stops.

**Addressed 2026-08-21.** `history_complete` is no longer a boolean keyed to
a cutoff year. `_missing_seasons()` measures it per player: if the earliest
major-league transaction we can see lands in his debut season, the front of
his career is visible and the figure is a real estimate; if it lands later,
everything before that is unrecoverable and we now report *how many* seasons
are missing. Records carry `missing_seasons` and `first_transaction`, and the
UI shows "−N seasons" instead of a bare "partial".

The cutoff rule flagged good figures as partial simply for debuting before
2009 — a 2003 debut whose 2003 transactions are in the feed is complete, and
now reads that way.

### 1b. The original (superseded) measurement

The `/transactions` endpoint returns nothing usable before 2009. Measured by
sampling six players per season and counting transactions involving a major
league club:

```
2005: 0   2006: 0   2007: 0   2008: 0   2009: 17   2010: 17   2011: 20   2012: 33   2013: 43
```

Jim Abbott was traded during 1995; his feed is empty. This is not a rate-limit
or query-shape problem — the data does not exist.

(Kept for the record. The zeros are real but mean "these players had no
pre-2009 history", not "the feed has no pre-2009 history".)

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

### 6. LIKELY NOT REAL: the clock "bridging gaps" spent outside MLB

`accrual_ceiling` (finding #4) stops a retired player's clock at the end of
his career. It does **not** close a gap in the *middle* of one. A player who
leaves MLB for independent ball, Japan, or a long minor league stretch, and
whose departure is phrased in a way no stop keyword matches, keeps accruing
across the years he was gone.

⚠️ **Superseded 2026-08-21.** This whole finding rested on the hard-2009
cutoff, which finding #1 disproves. The evidence for it evaporated; keep it
only so the reasoning is not repeated from scratch.

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
  before the fix lack IDs and are treated as unjudgeable (kept). A
  `--full-refresh` run was needed to fully realize the filter, and for a long
  time it had never been done: measured 2026-08-21, **0 of 64,643 cached rows
  carried a team ID**, so the filter was a complete no-op for every rostered
  player and only the debut floor protected them.
  **RESOLVED 2026-08-21** — a `full_refresh` run rebuilt the cache and
  **64,703 of 64,775 rows (99%) now carry an id**. The filter is live. (The
  backfill was never affected: it fetches fresh with `use_cache=False`.)
- **Demo players persisted forever.** The merge logic never deletes players,
  so seven bundled sample records ("Sample City Marlins") survived into live
  data. `MIN_REAL_PLAYER_ID = 100000` filters them; real MLB person IDs are
  six digits.
- **Workflow committed a path that might not exist.** `git add data/cache`
  exits 128 when the directory is absent, failing the job after 30 minutes of
  work. Now guarded.

---

## Player profiles

Clicking a name in the table opens a profile: debut date, every season, the
club(s) he was with, days credited, and a running total.

**The data was already there.** `compute_service_time()` has always returned a
`by_season` breakdown and the pipeline always threw it away. Persisting it
costs no extra API call.

### The three things that took thought

**1. Which club, in a season with no transactions.** Measured: **15% of
accruing seasons have no transaction naming a club** — a player who simply
plays all year generates none (Aaron Judge 2017 and 2024 are blank). Three
sources, in descending authority:

1. Year-by-year stat splits, **hydrated onto the `/people` call the pipeline
   already makes** (`BIO_SEASON_TEAMS_HYDRATE`), so it is free rather than a
   second request per player. Filtered against the 30 club ids, because
   splits can carry minor league lines.
2. Transactions dated inside the season, destination club first.
3. The last known club, carried forward. A club change produces a
   transaction, so a silent season almost always means he stayed put.

(1) is silent for a season in which a player never appeared — injured all
year — which is exactly where (3) earns its place.

**2. Say how each season is known.** After carry-in the seasons behind a
total differ a lot in confidence, and a profile that presented them as equal
would overstate what this project can see. Each row carries `src`:

| | meaning |
|---|---|
| `read` | transactions in this season drove it directly |
| `carry` | no transactions this season; status carried forward |
| `presumed` | earlier than his first transaction of any kind — pure debut presumption, and what `missing_seasons` counts |

Verlander's profile is the argument for this existing: 2005-2014 all read
"Presumed from debut" with no club, then 2015 onward switch to real clubs and
real transactions. The page shows you *why* his figure is what it is instead
of asking you to trust it.

**3. Do not undo the payload work.** The full breakdown for 5,568 players is
~0.9 MB, four times the compact index that was deliberately cut to 0.21 MB.
So profiles are **sharded by `player_id % 64`** into ~12 KB files: opening one
fetches a single shard, and the table load is untouched for the visitors who
never open a profile. Modulo rather than a name or team prefix because it
spreads evenly and never changes — a traded player stays in his shard.

### `missing_seasons` means something different now

Carry-in changed what the flag describes, and the first regenerated batch
made it obvious. Miguel Cabrera debuted 2003-06-20, his first recorded
roster move is 2011-08-26, so `missing_seasons` is 8 — but **six of those
eight seasons are now credited**, presumed from his debut. Only 2003 and
2004 are absent, because `MIN_TRANSACTION_YEAR = 2005` is where the pipeline
starts computing at all. He reads 19.000 against a real 21.000: short by the
two absent seasons, not by eight.

So the flag now conflates two different things, and the UI separates them
rather than the pipeline, because both are derivable in the browser:

* **presumed** — seasons with `src: "presumed"`. Credited, but from the
  debut date rather than from transactions. Less certain, not missing.
* **absent** — `first season row year − debut year`. Not counted at all.
  This is the part that makes a total a genuine floor.

Do not "fix" this by making the flag smaller. The eight seasons really are
the ones the feed cannot speak to; what changed is that six of them are now
estimated instead of dropped, and the profile says which.

### The invariant

`write_profiles()` checks that each player's season rows sum to his published
total, and warns loudly per player if not. If they disagreed, the profile
would contradict the table it was opened from and a reader would rightly stop
trusting both. It fired immediately in the sandbox (estimated season windows
against real stored totals), which is how it earned its place.

### Bug found on the way

**The workflows never staged `index.json`.** Both jobs ran
`git add -A docs/data/service_time.json`, so the file the browser actually
downloads was frozen at whatever was last committed by hand while the
database updated daily underneath it. Both now stage `docs/data` whole,
which also covers `profiles/`.

## Eligibility is a statement about current players only

Spotted by the owner from the numbers alone: **1,475 "free agency eligible"
and 1,279 "arbitration eligible" against 1,359 players on a 40-man roster.**
Not an arithmetic bug — a category error, twice over.

| | all 5,568 | on a 40-man | no longer rostered |
|---|---|---|---|
| free agency eligible | 1,475 | **301** | 1,174 |
| arbitration eligible | 1,279 | **371** | 908 |

1. **The tiles counted the whole database.** 1,174 of those "free agency
   eligible" are retired men who crossed 6.000 years years ago. The flag is
   really "accrued 6.000+ years"; for an active player that is the same
   statement, for a retired one it is not.
2. **The tiles ignored the filters.** `renderStatTiles(allPlayers)` ran once
   at load, so filtering the table to current players left the header
   describing a different population than the rows beneath it. That is what
   made it look like arithmetic.

Now: census tiles (tracked / on a 40-man / previous) stay whole-database,
because that is what they are for. Every eligibility figure — free agency,
arbitration, Super Two — counts rostered players only, and the status column
reads "Not on a roster" rather than an eligibility badge for everyone else.
The status filters follow the same rule.

### And no club is published for a player who is not rostered

The stored `team` for a non-rostered player is the last club we saw him
with, which is **stale by construction**. Printing it next to his name
asserts a roster spot he does not hold.

The table's payload no longer carries it — `write_index()` blanks it for
anyone off a 40-man, so the browser cannot show it even by accident, and the
team filter offers only clubs someone is actually on. The database keeps the
field, and **the profile's per-season club column is untouched**: those are
dated facts about which club he was with in a given year, which is exactly
the right place for that information.

## Current state

As of 2026-08-22, after the carry-in regeneration:

- **5,568 players, every one with a season-by-season profile.** Zero
  invariant violations (each player's season rows sum exactly to his
  published total).
- **Carry-in is on.** It cleared the roster gate in both eras with
  over-crediting flat at 0.2%, and cut the total error across the eighteen
  Baseball Reference figures from 1,662 days to about 200.
- **Reference check: 17 passed, 0 failed, 2 known gaps** — all 19 rows now
  have figures (Lindor entered 2026-08-22 at 10.113, we read 10.113). The
  two gaps are Scherzer and Verlander, both reading high by roughly half a
  presumed season.
- **76 tests passing.**

What the data is made of, measured across all 29,740 accruing seasons:

| | share |
|---|---|
| `read` — driven by transactions in that season | **76.7%** |
| `presumed` — from the debut, before any transaction | 13.7% |
| `carry` — status carried forward from an earlier season | 9.7% |

Only **0.4% of accruing seasons have no club identified**, down from 15%
before the stat-splits hydrate. Exactly one player is at or above 20.000
years (Verlander), which is correct.

Payload: table index 0.21 MB; 64 profile shards, ~9 KB each gzipped.

### Still open

- **Juan Soto, +6 days** — diagnosed (see above), accepted, not worth the
  regressions the fix caused.
- **José Ramírez, +8 days offline / −1 live** — the offline figure was the
  estimated season windows, not the model. Nothing to chase.
- The 2014 roster sample's remaining under-credits are concentrated in
  David Huff (16 of 39), who was claimed off waivers and bounced between
  clubs mid-season — probably a waiver-claim carry-in case.
- Backfilled players were recomputed with `--recompute-stale`. Any future
  rules change needs the same treatment: the normal queue only looks for
  players it does not already have.

## Validation — the process, and the current number

Every bug in this project was found by a human noticing a number looked
silly. That works at 20 players and fails at 4,000. As of 2026-08-21 there
is a real gate.

### Ground truth exists

`/teams/{id}/roster?rosterType=40Man&date=YYYY-MM-DD` **honours the date**
and returns the roster as it stood, with a per-player status. Verified: the
2012-06-15 Yankees come back as Alex Rodriguez, Andruw Jones and Andy
Pettitte, with zero overlap against today's roster. Service time is days on
an active roster or major league IL, so the thing this project estimates is
directly observable one date at a time.

Rebuilding careers this way is far too expensive (30 clubs × ~186 days × N
seasons). Sampling is cheap: one club-season at a 7-day interval is ~27
calls and yields ~1,100 player-date judgements.

### The numbers (2026-08-21, Yankees, weekly sampling)

| | 2018 | 2014 |
|---|---|---|
| agree | 95.3% | **88.6%** |
| model OVER-credits | 1.3% | **2.6%** |
| model UNDER-credits | 3.4% | **8.7%** |

**2014 fails the gate on both counts, so the mass backfill is blocked.**

Over-crediting — the failure mode behind every earlier embarrassment — is
small in both. The problem is UNDER-crediting, and it is concentrated:

- 2018: 27 of 38 under-credits are Ben Heller alone, on the 60-day IL all
  season, where the roster says accruing and the model credits nothing.
- 2014: 55 of 103 are two players. **Ichiro Suzuki (under=28, agree=0)** was
  on the active roster all year and gets zero days — he arrived by trade in
  2012 and stayed, and "traded" is not a start keyword. **Masahiro Tanaka
  (under=27)** signed as a free agent in January 2014 and went straight onto
  the active roster; "signed free agent" is not a start keyword either.

### The carry-in problem (the main remaining defect)

`build_global_active_intervals()` only ever opens an interval on an explicit
start keyword. A player who is *already* on a roster when the window opens,
or who joins by trade or by signing a major league contract, never gets one —
so he accrues nothing until some future recall or activation happens to fire.

This is worse in the backfill population than in the daily one: veterans and
older eras are exactly where players sit on rosters for years without a
transaction the parser recognises.

`compute_service_time()` still carries a `carry_in_active_first_season`
parameter, currently a documented no-op. This is what it was for.

**Fix 1 (shipped): a major league free agent signing starts the clock.**
Measured on Yankees 2014, before → after:

| | before | after |
|---|---|---|
| agree | 88.6% | 93.7% |
| over-credit | 2.6% | 2.6% |
| under-credit | 8.7% | 3.7% |

Under-crediting more than halved (103 → 43) and over-crediting did not move
at all (31 → 31), which is what you want from a change that only adds
intervals. Tanaka went under=27 → under=2, and Ichiro dropped off the list
entirely — he had re-signed with the Yankees as a free agent, so the same
fix caught him. Minor league deals stay excluded by the explicit
"...to a minor league contract" wording (828 cached rows vs 534 major).

**Fix 2 (shipped): the validator must apply the MLB-club filter.** It was
building intervals straight from the raw feed, so it scored a *different
model than production* — affiliate "activated" rows opened intervals the
real pipeline discards. 2014 again, before → after:

| | before | after |
|---|---|---|
| agree | 93.7% | **96.2%** |
| over-credit | 2.6% | **0.2%** |
| under-credit | 3.7% | 3.7% |

29 of the 31 over-credits were the validator's own artefact. José Ramírez
and Austin Romine disappeared entirely — they were never pipeline defects.

**2014 now passes the gate.** Over-crediting, the failure mode behind every
earlier revert, is down to 0.2%.

The lesson generalises: a measurement that does not run the same code as
production measures the wrong thing, and tuning against it would have meant
"fixing" two players who were never broken.

What remains is under-crediting, still concentrated: David Huff (16),
Shawn Kelley (12), Dean Anna (5), Slade Heathcott (4) — 37 of 43. And in
2018, Ben Heller (27), on the 60-day IL all season where the roster says
accruing and the model credits nothing. That IL case is the strongest
remaining candidate: a player on a major league injured list is accruing by
definition, but an IL placement currently only avoids stopping the clock —
it never starts one, so a player optioned and then moved to the 60-day IL
stays stopped.

**Fix 3 (shipped 2026-08-22): carry-in.**

The original sketch — "if the first transaction seen is a STOP, open the
interval at the window start" — turned out to be too narrow. It does not
catch Verlander at all, because his first transaction is an IL placement,
which is a *start*. The general statement is stronger and simpler:

> **A player is presumed to be on a roster from his major league debut until
> the feed says otherwise.**

That is the exact mirror of `accrual_ceiling`, which already presumes a
player stayed rostered to the end of his last season rather than demanding a
transaction to prove it. The default was asymmetric: presumed-off at the
front, presumed-on at the back.

Implemented as `presume_active_from` in `build_global_active_intervals()`,
surfaced as `presume_active_from_debut` on `compute_service_time()`. The
vestigial `carry_in_active_first_season` parameter is now a deprecated alias
for it rather than a no-op.

**It is ON**, behind one switch — the `PRESUME_ACTIVE_FROM_DEBUT` env var,
honoured by the daily job, the backfill and the roster validator together, so
what the validator scores is what production produces. Set it to `0` to score
the old behaviour; the switch stays precisely because it is what makes the
A/B possible.

*What bounds the over-crediting risk* (the failure mode behind every revert
here): `accrual_floor` blocks the presumption before the debut,
`accrual_ceiling` blocks it after the last season played, and in between any
option, DFA, release or outright closes the interval normally. What is left
exposed is a player who left an MLB roster in a way the feed never recorded —
overwhelmingly a pre-2009 coverage problem, since 2009+ demotions are
reliably present.

**Offline evidence, before the live run.** All eighteen Baseball Reference
figures, as of 2026-01-26, scored both ways. Only three players move at all:

| player | B-R | carry-in off | carry-in on |
|---|---|---|---|
| Justin Verlander | 20.002 | 11.000 (−1550d) | 20.090 (**+88d**) |
| Max Scherzer | 17.079 | 17.000 (−79d) | 17.156 (**+77d**) |
| Juan Soto | 7.134 | 7.135 (+1d) | 7.140 (**+6d**) |
| *the other 15* | | | **byte-identical** |

Total absolute error across the eighteen: **1,662 days → 203 days.**

The fifteen unchanged players are the property that matters most: where the
feed is complete, carry-in is inert. It only fires where the feed is silent,
which is exactly where the old default was guessing wrong.

Scherzer is the predicted cost, and worth understanding rather than tuning
away. He was optioned down and recalled during 2008; neither move is in the
feed, so carry-in credits his whole 2008 (155 days) instead of the 79 he
earned. An under-credit became a slightly smaller over-credit.

Soto (+1d → +6d) is the one modern player it touches, and the five days are
the only movement in the well-covered era in either direction. Watch him if
a later measurement suggests carry-in over-credits generally; on this
evidence he is noise.

A tempting refinement, **rejected**: start the presumption at the later of
the debut and the 2009 season, to avoid the sparse era entirely. Measured, it
gives Verlander 17.000 — three years short — and leaves Scherzer unchanged.
It trades a fixed 9-year error for a fixed 3-year one to avoid an 88-day one.

**Whole-population dry run** over all 1,324 cached 40-man players: 27 changed
(2%), 4,336 days added, **0 days removed** — carry-in can only add, and the
run confirms it does. The movers are the right shape: Shea Langeliers
1.547 → 4.134 (Oakland's everyday catcher since 2022), Mitch Spence
0.198 → 1.802. Both read absurdly low before.

**Measured live, 2026-08-22, and it cleared the gate in both eras:**

| | agreement | over-credit | under-credit |
|---|---|---|---|
| Yankees 2014 | 96.5% → **96.8%** | 0.2% → **0.2%** | 3.3% → 3.1% |
| Yankees 2018 | 99.0% → **99.0%** | 0.2% → **0.2%** | 0.8% → 0.8% |

**Over-crediting did not move at all in either season.** That is the number
that decided it — a change that merely inflated figures would have pushed it
up. 2018 is untouched end to end, which is the same result as the reference
players: carry-in fires only where the feed is silent.

The gain looks small here and is enormous on career totals (Verlander,
−1550d → +88d) for the reason described below: a single-season roster sample
cannot see a defect that lives in the *years before* the sampled season.

Note why the roster check nearly missed this and the Baseball Reference check
caught it immediately: sampling one club-season makes a carry-in player look
like a handful of missing days, while a career total makes nine years
unmissable. Breadth and independence are different virtues.

Run it: Actions → "Validate Service Time" → rosters.

### Never hardcode what the data can tell you

The first version of the roster validator hardcoded status codes and got
`RM` wrong — guessed "restricted list, accruing"; it is what an **optioned**
player gets, and there is no `OPT` code in the feed at all. A fifth of the
sample was inverted and it reported 23.3% under-crediting that did not
exist. Agreement went 76.7% → 95.3% once fixed.

That is the same failure as DFA-as-a-stop, free-agency-as-a-stop, and the
2009 cutoff: **a plausible belief about MLB's vocabulary, encoded, wrong.**

So codes are now calibrated per run from evidence. The lever needs no
external data: *a player cannot earn major league service time before his
major league debut*, so any code seen before a player's `mlbDebutDate`
cannot mean accruing. Only `A` and the injured-list shape (`D7`/`D10`/`D15`/
`D60`) are assumed a priori. Anything unclassified is excluded from the
comparison and its share is reported, so a partial mapping cannot quietly
skew the result.

Apply the same instinct to any new rule: prefer a check the data can settle
over a belief about what MLB means.

### Gate status (2026-08-21)

| | agree | over-credit | under-credit |
|---|---|---|---|
| Cleveland 2011 | **99.9%** | 0.1% | 0.0% |
| Yankees 2014 | **96.8%** | 0.2% | 3.1% |
| Yankees 2018 | **99.0%** | 0.2% | 0.8% |
| Tampa Bay 2022 | **98.6%** | 0.4% | 1.0% |

Tampa Bay was 97.8% / 1.1% before the same-date fix (finding #10); Yankees
2014 came back byte-identical, which is what a fix for a defect that season
did not have should do.

All with carry-in on, 2026-08-22. Four club-seasons, three organizations,
three decades. Cleveland 2011 is the best result the project has produced:
one wrong judgement in 1,083.

**The Yankees-only sample was optimistic about over-crediting.** Tampa Bay
2022 comes in at 1.1%, five times the 0.2% both Yankees seasons showed —
still inside the gate, but the honest figure for over-crediting is nearer
1% than 0.2%. The Rays option players relentlessly, which is exactly the
traffic that exposes it (Ben Bowden over=4/agree=0, Luke Bard over=3).
Sampling one club had been flattering the number; this is why the gate asks
for more than one.

**Both pass comfortably** (≥95% agreement, ≤2% over-crediting), in two
different eras — 2014 for "disabled list" wording, 2018 for "injured list".
Tests green at 52.

2018 is 11 wrong judgements out of 1,116. The IL fix took it from 96.5% to
99.0% and Ben Heller from under=27 to under=1. Notably it touched 20% of
players and added 15,029 days while over-crediting stayed flat at 0.2% —
a change that was merely inflating numbers would have pushed that up.

Over-crediting, which caused every revert on 2026-08-21 (20-year phantom
careers, 246 of 500 retired players over 15 years), is down to 0.2%.

**The remaining error is under-crediting and it is one known case.** Ben
Heller is 27 of the 37 under-credits in 2018: on the 60-day IL all season,
where the roster says accruing and the model credits nothing. A player on a
major league injured list accrues by definition, but an IL placement
currently only *avoids stopping* the clock — it never *starts* one. So a
player who is optioned and then moved to the 60-day IL stays stopped.

That fix is now shipped and measured (see above): 2018 went 96.5% → 99.0%,
2014 went 96.2% → 96.5%.

What is left is small and concentrated in 2014: David Huff (16 of 39
under-credits, agree=0), Shawn Kelley (8), Dean Anna (5), Slade Heathcott
(4). Huff is the only one worth a look — he was claimed off waivers and
bounced between clubs mid-season, so he is probably a waiver-claim
carry-in case rather than a new class of bug.

### The first independent check (2026-08-22)

Eighteen Baseball Reference figures are in — the first numbers in this
project checked against a source that is not MLB's own transaction feed.
All are the 01/26 snapshot, compared against what the model computes as of
2026-01-26.

| player | B-R | ours | off by |
|---|---|---|---|
| Aaron Judge | 9.051 | 9.051 | **0** |
| Paul Goldschmidt | 14.059 | 14.059 | **0** |
| Carlos Correa | 10.119 | 10.119 | **0** |
| Corey Seager | 10.032 | 10.032 | **0** |
| Shohei Ohtani | 8.000 | 8.000 | **0** |
| Pete Alonso | 7.000 | 7.000 | **0** |
| Vladimir Guerrero Jr. | 6.157 | 6.157 | **0** |
| Bo Bichette | 6.063 | 6.063 | **0** |
| Kenley Jansen | 15.073 | 15.072 | −1 |
| José Ramírez | 11.074 | 11.073 | −1 |
| Rafael Devers | 8.070 | 8.069 | −1 |
| Nolan Arenado | 12.155 | 12.156 | +1 |
| Gerrit Cole | 12.111 | 12.112 | +1 |
| Juan Soto | 7.134 | 7.135 | +1 |
| Jacob deGrom | 11.139 | 11.137 | −2 |
| Mookie Betts | 11.070 | 11.068 | −2 |
| Max Scherzer | 17.079 | 16.169 | −82 (GAP, 1 season) |
| Justin Verlander | 20.002 | 11.000 | −1550 (GAP, 10 seasons) |

**Eight are exact and all sixteen non-gap figures land within two days**,
across careers of six to fifteen years. Verdict: 16 passed, 0 failed, 2 known
gaps. That is the first evidence for this project's math that does not come
from MLB.

Both gaps are the model's own `missing_seasons` flag doing its job — each
reads low by roughly the seasons it already declares it cannot see. The
validator scores three ways for this reason: `ok`, `GAP` (low, with
`missing_seasons > 0`) and `FAIL`. Reading *high* is always a FAIL, since
missing history cannot inflate a figure.

**Do not tune against offline numbers.** These were first computed in the
sandbox from the transaction cache with *estimated* season windows
(Mar 28 – Oct 1), because the sandbox cannot reach the API. That put José
Ramírez at +8 and made him look like the one modern outlier worth chasing.
With real season windows he is −1, and the residuals collapse to ≤2 days
across the board. The estimate was the error, not the model.

Also note a matching total is weaker evidence than it looks — errors in
opposite directions cancel. The roster check compares day by day and catches
that; the two checks are complementary and neither replaces the other.

**How to read the B-R page.** There is no per-season `s.YYYY` column — that
was a wrong assumption baked into the first version of this file and the
validator. B-R shows a single dated snapshot in the bio block, e.g.
"9.051 (01/26)". `as_of` on every row is therefore one offseason date
(2026-01-26), which works because **service time does not accrue between the
World Series and Opening Day** — the exact day need not match B-R's label.

All 19 rows now carry a figure.

#### Verlander is not a pre-2009 problem — he is the carry-in problem

The tempting read is "pre-2009 debut, thin coverage, expected." It is wrong,
and the size of the gap is what gives it away: his 2005-2008 seasons are
worth about 3.5 years, but he is short by 9.

His first major-league transaction of any kind is **2015-04-08** — a disabled
list placement. There is nothing before it. He was on Detroit's roster
continuously from 2006, never optioned, never DFA'd, never released, and not
injured until 2015, so the feed has no reason to say anything about him. Six
of his ten invisible seasons (2009-2014) sit squarely inside the era where
coverage is known to be good.

`build_global_active_intervals()` only ever opens an interval on an explicit
start, so he accrues nothing until that 2015 IL placement fires. 2015-2025 is
eleven seasons at the 172-day cap = 11.000 exactly, which is what we print.

This is the carry-in defect described above, now measured on a single player:
**9 years.** It is worst exactly where it is least visible — durable veterans
who sit on a roster for a decade without a transaction the parser recognises.
The roster validator only sampled Yankees 2014 and 2018, single seasons where
a carry-in player looks like a handful of missing days; a career total makes
it unmissable. That is the argument for this check existing.

The fix and its risk are unchanged from the carry-in note above: opening the
interval at the window start when the first transaction seen is a stop (or,
for Verlander, when a player has a debut but no transactions for years after
it) would credit him correctly — but `accrual_floor` is the only thing
stopping the same rule from crediting a career minor leaguer for every year
between his cup of coffee and his next recall. Over-crediting caused every
revert in this project. Measure it on Yankees 2014 + 2018 before believing it.

### Reading the reference check after carry-in

Turning carry-in on flipped the sign of the two known gaps, and the
validator's verdict rule had to catch up. Live, 2026-08-22:

| player | B-R | before carry-in | after |
|---|---|---|---|
| Justin Verlander | 20.002 | 11.000 (−1550d) | 20.091 (**+89d**) |
| Max Scherzer | 17.079 | 16.169 (−82d) | 17.152 (**+73d**) |

The old rule said reading HIGH is always a failure, on the reasoning that
missing history cannot inflate a figure. **Carry-in makes that premise
false**: those seasons are now credited, so a presumption can over-credit as
easily as the old default under-credited. Same limitation, opposite sign.

The rule now judges a `missing_seasons > 0` player in either direction, but
bounded — the presumption cannot be wrong by more than the seasons it
presumes, so beyond `missing_seasons × 172` it is a real failure. A player
with complete history is held to the tolerance both ways, and those sixteen
are what actually guards against regressions.

#### Juan Soto: a 6-day residual, diagnosed and accepted

He is the only complete-history player outside tolerance, and the cause is
exact: **MLB's `mlbDebutDate` for him is 2018-05-15, but the feed says his
contract was selected on 2018-05-20.** The 05-15 row is a plain "assigned
to", with no start wording. B-R counts from the 20th; we accrue from the
debut date. Five days, plus one of ordinary rounding.

**The obvious fix was tried and measured, and is harmful.** Scoping carry-in
so it never overrides a start the feed can see:

| | before | after |
|---|---|---|
| Juan Soto | +6d | +6d (**unchanged** — his 05-15 row is not a start) |
| Jacob deGrom | +1d | **−87d** |
| Shohei Ohtani | +0d | **−56d** |

deGrom and Ohtani were already on a roster before their first debut-season
*start* transaction fired, so deferring to it threw away the front of their
first year. Reverted. This is the same shape as the free-agency stop keyword
and the minor-league-activation stop: a plausible rule, measured, wrong.

Recorded instead as `accepted_delta: 6` on his reference row, with the
diagnosis in a `note`. It is compared **exactly**, not as a widened
tolerance — if the residual drifts, the row fails again.

### Super Two, computed rather than guessed (2026-08-22)

This was a documented limitation for a good reason. The rule is not a fixed
threshold:

> at least two but less than three years of service, **86+ days in the
> immediately preceding season**, and **ranked in the top 22%** by total
> service of the class of players with two-to-three years.

"Top 22% of the class" needs the whole class, which MLB does not publish, so
the code flagged candidates on a flat 86-day proxy and said it was not
authoritative.

**Completing the database ended that.** 5,568 players each carrying a season
breakdown *is* the league-wide population. `scripts/super_two.py` computes
the class and the boundary directly, for any past season, with no extra API
calls:

| season | cutoff | class | top 22% |
|---|---|---|---|
| 2025 | **2.136** | 206 | 45 |
| 2024 | 2.128 | 201 | 44 |
| 2023 | 2.126 | 177 | 38 |
| 2022 | 2.130 | 187 | 41 |
| 2021 | 2.112 | 226 | 49 |

These land in the band where reported cutoffs have historically fallen, with
no tuning of any kind. **That is encouraging and it is not evidence** — every
serious bug here has been a plausible belief that landed where someone
expected. `data/reference_super_two.json` takes published cutoffs by hand,
same as the Baseball Reference file, and is the only thing that can settle
it. Actions → "Validate Service Time" → super-two.

**Two different questions, and the code answers the second.** The 2025 cutoff
is a historical fact about a class that has since moved on — those players
are mostly past 3.000 now, and flagging them today would report a decision
already made. What a reader wants is "is this player on track?", which is a
projection: current service time compared against the most recent measured
boundary. The threshold has moved 2.112–2.136 over five years, so a player
within a few days of it could land either side. Still far better than a flat
86-day proxy.

The 86-day condition is now checked *exactly* from the season rows, in its
correct role — a second test on top of the ranking, not the whole rule.

**Which season the 86 days are counted in matters**, and the first version
got it wrong. The rule says "the immediately preceding season" — preceding
the offseason where eligibility is decided. For a projection about the NEXT
offseason that is the season being played now, not the one the cutoff came
from.

Anthony Kay is the case that exposed it (spotted by the owner): 2.155 and on
a 40-man, above the 2.136 line, but not flagged. His breakdown says why — he
sat at 2.004 after 2023, accrued **nothing** in 2024 or 2025, then came back
for 151 days in 2026. Tested against 2025 he has zero days and fails; tested
against 2026 he clears 86 comfortably, which is the right answer to "is he
on track".

`qualifying_season()` now picks the season being played, falling back to the
reference season early on — in April nobody has 86 days yet, and switching
then would empty the list rather than project it. Which season is far enough
along is *measured* (has anyone reached 86 days in it?) rather than assumed
from the calendar. Six players moved, all correctly:

| | 2025 | 2026 | |
|---|---|---|---|
| Anthony Kay | 0 | 151 | added |
| Ron Marinaccio | 31 | 137 | added |
| José Herrera | 157 | 0 | dropped |
| Josh Winckowski | 130 | 0 | dropped |
| Bo Naylor | 172 | 45 | dropped |
| Logan Allen (671106) | 172 | 60 | dropped |

**A mid-season consequence to accept:** Logan Allen at 60 days could still
cross 86 before the season ends, so the flag is not his final answer — it
flips on the day he crosses. That is the honest behaviour for a live
projection, and it resolves exactly once the season is over.

`latest_complete_season()` was fixed alongside for the same reason: it
returned `today_year - 1` unconditionally, which goes stale every offseason
and would quietly publish a cutoff a full season out of date. It now checks
whether anyone has reached the 172-day cap in the current year.

*Two Logan Allens, both Cleveland pitchers.* The first pass at this diff
keyed on name and printed the wrong one's figures (1.079, not even in the
band). Same collision as the two Luis Perdomos — **never key this dataset by
name.**

**`--recompute-derived`** was added alongside: reload the stored database,
redo the derived post-passes (Super Two, index, profiles) and rewrite the
published files, with no API calls. A change to how something is *derived*
from records that are already correct does not need half an hour of
re-fetching.

### 10. Same-date transactions: the stop wins

**Found 2026-08-22 by probing Ben Bowden**, who scored `over=4 agree=0` on
Tampa Bay 2022 while his stored record credited him 0 days that season. Both
numbers came from the same code. What differed was the order two rows
arrived in:

```
2022-03-31  STOP   Colorado Rockies optioned LHP Ben Bowden
2022-04-29  START  Tampa Bay Rays claimed LHP Ben Bowden off waivers
2022-04-29  STOP   Tampa Bay Rays optioned LHP Ben Bowden to Durham Bulls
```

Walked start-first: the start opens an interval, the stop closes it at
04-28, zero days. Walked stop-first: the stop is a no-op (he was already
optioned in March), the start opens an interval, and **nothing ever closes
it** — 160 phantom days to the end of the season.

`sorted(..., key=lambda t: t.date)` is stable, so which happened was decided
by the order the API listed two rows in. Measured directly: **0 days vs 160,
same player, same code.**

Intervals are now built from transactions **grouped by date**, and when a
date carries both a start and a stop, **the stop wins** — the player ends
that day off the active roster.

**A rejected rule, recorded so it is not retried.** The first fix treated a
same-date pair as a *wash*, leaving the player in whatever state he was
already in. It reads well — claimed-then-optioned never reaches the roster,
optioned-then-recalled never leaves it — and it is wrong where it matters
most. Of 223 same-date pairs in the cached histories, 212 end in an option,
a DFA or an outright:

| count | start | stop |
|---|---|---|
| 127 | activated from the IL | optioned |
| 61 | claimed off waivers | optioned |
| 32 | recalled | optioned |
| 24 | contract selected | optioned |

A player activated off the IL is *already accruing*, so "unchanged" keeps
him accruing straight through an option — and that is the commonest pair of
the four. Measured across the cache the wash rule **added 5,623 days**.
Stop-wins only ever removes them.

*Measured, old walk → stop-wins, over all 1,324 cached players:*

| | |
|---|---|
| players changed | 48 (3.6%) |
| days removed | **1,512** |
| days added | **0** |
| reference players moved | **none** (total residual 203 → 200) |

The cost is a genuine paper move — optioned and recalled the same day,
never actually leaving — which now reads as a stop. Rare, and in the safe
direction.

**This is what the Rays' 1.1% over-crediting was.** They churn players, so
same-day claim-and-option happens constantly; the Yankees seasons barely saw
it. The gate asking for more than one club is what surfaced it.

*Measured live after the fix:* Tampa Bay 2022 **97.8% → 98.6%** agreement,
over-crediting **1.1% → 0.4%**, with Bowden, Luke Bard and Angel Perdomo all
gone from the worst-players list. Yankees 2014 came back **byte-identical**.

### 11. Carry-in must anchor on the debut, not on the floor

**Found 2026-08-22 while auditing the database before a full recompute.**
34 players had no `mlbDebutDate` at all — they have never appeared in a
major league game — and were credited service time anyway. Robinson Ortiz
read **8.124 years**. Leandro Lopez, signed 2021-01-15 and still without a
debut, read 5.000. Across the 34: **29,112 days, 169 player-years, all of it
phantom.**

The cause is two reasonable decisions colliding. `accrual_floor` falls back
to a player's earliest major-league-club transaction when he has no debut
date — sensible on its own, and there to stop crediting anyone from his
college days. Carry-in then presumed he was on a roster from the floor. For
a prospect that floor is the day the club signed or drafted him, so the
presumption ran from his signing to today.

Carry-in now anchors on the **debut date specifically**, never on the
floor's fallback. The two are the same date for anyone who has played; for
anyone who has not, there is no major league service time to presume.

All 34 are on 40-man rosters, so the daily job clears them; no backfill
needed for this one.

*The general lesson, again:* the guard that made the fallback safe
(`accrual_floor` blocks pre-debut accrual) stopped being a guard the moment
carry-in started treating the floor as a starting gun rather than a wall.

### Recomputing after a rules change: `rules_version`

A rules change has no natural completion marker, and the two obvious ones
both fail:

* **a missing season breakdown** (what `--recompute-stale` keys on) says
  nothing about *which* rules produced the breakdown that is there.
* **`last_updated`** is stamped with today's date by any job that touches a
  record, so a morning backfill makes the whole database look freshly
  computed under rules that changed at lunchtime. Tried, and it queued zero
  players.

Every record now carries `rules_version`, and `--recompute-all` rebuilds any
record not stamped with the current one. **Bump
`SERVICE_TIME_RULES_VERSION` in `update_service_time.py` whenever a change
would give a stored player a different figure.** That keeps the recompute
resumable across batches, which matters for a job that takes hours.

### Gate before a mass backfill

1. **Roster accuracy** — agreement ≥95% and over-crediting ≤2%, on at least
   two different club-seasons (one recent, one pre-2019 for disabled-list
   era wording).
2. **Baseball Reference spot-check** — **PASSING** as of 2026-08-22: 18 of
   19 figures entered (all but Lindor), 16 passed, 0 failed, 2 known gaps,
   8 of them exact. Actions → "Validate Service Time" → reference. This is
   the only *independent* check: the roster comparison
   validates the pipeline against the same source it is built on, so a
   systematic misreading of MLB's semantics passes it. See "The first
   independent check" below.
3. **Tests green** — `python tests/test_service_time.py`.

---

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
   Baseball Reference's dated snapshot figure, which is a fixed target
   rather than a moving one. Doing this required a real bug fix along the way:
   `build_global_active_intervals()` previously only used `horizon_end` to
   cap the trailing *open* interval — a stop transaction (option/DFA/release)
   dated *after* `horizon_end` would still truncate an earlier interval that,
   as of that date, hadn't ended yet. It now drops every transaction dated
   after `horizon_end` before building intervals at all. Covered by a new
   regression test (`test_as_of_past_date_ignores_later_transactions`).

   **Status:** the reference file has 19 rows, 18 with figures entered
   (2026-08-22); only Lindor is blank. Fill in the rest by hand from the players' Baseball Reference
   pages — deliberately not scraped — and run
   `python scripts/validate_service_time.py`. Requires network access to the
   live MLB Stats API, so it can't run in this offline sandbox — run it from
   Actions → "Validate Service Time" → reference.

### Known limitations

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
