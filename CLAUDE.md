# Big League Service Time Tracker — project context

Handoff notes for Claude Code. This file captures decisions, hard-won
empirical findings, and open work so a fresh session doesn't have to
rediscover any of it.

- **Name:** "Big League Service Time Tracker" (renamed 2026-08-23). The repo
  and the Pages URL still say `MLB-Service-Time-Tracker` -- renaming a repo
  changes the live URL and breaks every existing link, so that was left
  alone deliberately. The rename is about the *product name*: "MLB Service
  Time Tracker" reads like an official MLB product, and MLB is a trademark.
  Descriptive uses of "MLB" stay everywhere they are accurate -- "MLB Stats
  API", "estimated MLB service time", "not an official MLB/MLBPA figure" --
  because that is honest attribution, and stripping it would make the site
  less clear rather than safer. The footer now also states plainly that the
  project is not affiliated with or endorsed by MLB or the MLBPA.
- **Repo:** `RainbowMang0/MLB-Service-Time-Tracker`
- **Live site:** https://bigleagueservicetime.com (custom domain, live
  2026-08-26). The `github.io` URL redirects to it. `docs/CNAME` holds the
  hostname and *derives* every published URL — see "Moving to a domain".
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
scripts/super_two.py           the real Super Two cutoff, from the whole population
scripts/write_player_pages.py  static crawlable page per rostered player + sitemap
scripts/commit_data.sh         commits generated data, surviving a concurrent push

scripts/validate_service_time.py   --as-of, against hand-entered Baseball Reference figures
scripts/validate_against_rosters.py  day-by-day against MLB's own historical rosters
scripts/validate_super_two.py      against published cutoffs (offline)
scripts/validate_published.py      published files agree + every URL/link resolves (offline)
scripts/probe_player.py            everything the model knows about one player
scripts/make_reference_worksheet.py  which players are worth hand-checking vs B-R
scripts/probe_coverage.py          live API probe for finding #1 (run via Actions)
scripts/generate_demo_data.py      bundled sample data generator (no network)
tests/test_service_time.py     205 tests, no pytest needed: `python tests/test_service_time.py`
docs/                          the static site (index.html, styles.css, app.js)
docs/data/service_time.json    the database: every field, one object per player
docs/data/index.json           what the browser downloads for the table (0.22 MB)
docs/data/profiles/NN.json     per-player season detail, sharded by id % 64
docs/data/page_lastmod.json    per-page content hash, so sitemap lastmod is honest
docs/p/<id>-<slug>.html        one static page per rostered player (generated)
docs/t/<club-slug>.html        one hub page per club, its 40-man by service time
docs/t/index.html              the club directory; the homepage's one link in
docs/page.css                  shared by every static page (generated)
docs/404.html                  generated; site-absolute URLs, see below
docs/sitemap.xml, robots.txt   generated alongside the pages
docs/CNAME                     if present, sets the site's domain AND its URLs
data/cache/transactions/       per-player transaction cache (rostered players only)
data/backfill_state.json       resumable backfill progress
.github/workflows/update-service-time.yml   daily 8am ET
.github/workflows/backfill-history.yml      manual, batched
.github/workflows/validate.yml              manual: rosters | reference | super-two
                                            | player | published
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
- **The validator and the probe did not apply finding #15.** Found
  2026-08-26. `update_service_time.py` moves `accrual_floor` back to a
  pre-debut roster move; `validate_against_rosters.py` and
  `probe_player.py` both passed a bare `mlbDebutDate`. So from the moment
  rules v3 shipped, **the gate scored a model the site does not publish** —
  and #15 moves the floor for 56% of players, so this was not a rounding
  matter. It surfaced when the probe reported Braxton Fulford MODEL UNDER on
  two days his published record already credits.
  This is the same shape as "Fix 2" in the validation notes, where the
  validator built intervals without the MLB-club filter and consequently
  blamed José Ramírez and Austin Romine for defects they never had. **Any
  check must construct the floor the same way the pipeline does.**
  Both now do. Re-measured, the gate moved *up* — 2014 98.8% → 99.1%, Tampa
  Bay 98.6% → 98.7%, the other two identical, over-crediting unchanged in all
  four. The old numbers were pessimistic rather than flattering, which is the
  safe direction, but a gate that is wrong in either direction is not a gate.
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

As of 2026-08-25, fully recomputed under **rules version 3** (findings #15
and #16), with every rostered player published as a crawlable static page.

- **5,571 players, every one with a season-by-season profile**, all stamped
  `rules_version: 3`. Zero invariant violations — each player's season rows
  sum exactly to his published total.
- **1,358 on a 40-man.** Each has a static page at
  `docs/p/<id>-<slug>.html`, listed in `docs/sitemap.xml`.
- **189 tests passing.**
- 1 player at or above 20.000 years (Verlander, 21.073), which is correct.
- **27 players read exactly 0.000** and are hidden from the table by
  default — all 27 have never played a major league game (prospects added
  to a 40-man to protect them from the Rule 5 draft) and are reachable
  through the "Yet to accrue a day" filter. The two *debuted* players who
  read 0.000 before v3 — Daniel Fields and Elih Villanueva — are fixed by
  finding #15 and now read 0.003 and 0.001.

What the data is made of, across all 28,988 accruing seasons:

| | share |
|---|---|
| `read` — driven by transactions in that season | **79.4%** |
| `presumed` — from the debut, before any transaction | 12.3% |
| `carry` — status carried forward from an earlier season | 8.4% |

Only **0.0% of accruing seasons have no club identified**.

Payload: table index 0.22 MB; database 9.1 MB (not downloaded by the
browser); 64 profile shards, 3.4 MB in total, one fetched per profile
opened; 1,358 player pages, 7.1 MB, one fetched per crawl or direct link.

**All four gate club-seasons plus the Baseball Reference check re-run
against the fully recomputed v3 database, 2026-08-25.**

| | agree | over-credit | under-credit | vs. v2 |
|---|---|---|---|---|
| Cleveland 2011 | **99.9%** | 0.1% | 0.0% | unchanged |
| Yankees 2014 | **99.1%** | 0.2% | 0.8% | 96.8% → 99.1% |
| Yankees 2018 | **99.8%** | 0.1% | 0.1% | 99.1% → 99.8% |
| Tampa Bay 2022 | **98.7%** | 0.4% | 0.9% | 98.6% → 98.7% |

*(Re-measured 2026-08-26 after the validator was fixed to apply finding #15 —
see the bug history. The first v3 numbers were slightly pessimistic: 2014 read
98.8% and Tampa Bay 98.6%.)*

Baseball Reference: **17 passed, 0 failed, 2 known gaps** (Scherzer +73d,
Verlander +89d, both bounded by the seasons their own records declare
presumed). **Twelve figures exact to the day**, up from nine, and the other
five are all ±1. Total absolute residual across the seventeen
complete-history players: **11 days → 4 days.** Soto still +6, matching his
accepted delta exactly.

**Over-crediting did not move in any of the four club-seasons**, which was
the condition #15 and #16 were held to before shipping — 2% was the agreed
revert line, and it stayed at 0.1–0.4%. The two seasons that improved are
the two that contained the defects (David Huff in 2014, A.J. Cole in 2018);
the two that were already clean came back identical.

### Still open

- **Juan Soto, +6 days** — diagnosed (see above), accepted, not worth the
  regressions the fix caused.
- **Scherzer +73d and Verlander +89d** — both GAP rows, bounded by the
  seasons their own records declare presumed. Not defects; the known limit
  of what the feed can see.
- What remains in the roster gate is thin and scattered rather than
  concentrated: Yankees 2014's worst is now Slade Heathcott at `under=4`,
  Yankees 2018's is Ben Heller at `under=1`. There is no longer a single
  player worth naming as the next bug.
- **Braxton Fulford, +1 day against MLB's rosters and −3 against Baseball
  Reference** — finding #17. The +1 is one recall the roster never reflects;
  the −3 is on the far side of MLB's own roster data and cannot be reached
  from public sources. Neither is worth a rules change.
- Any future rules change needs `SERVICE_TIME_RULES_VERSION` bumped and a
  `--recompute-all` pass; the normal queue only looks for players it does
  not already have.

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

### Why Baseball Reference is not scraped, and what is done instead

**Asked 2026-08-26: "check Baseball Reference's numbers against all the
players you can." Declined, and worth recording why, because it is a
reasonable-sounding request that will come up again.**

Three separate reasons, and each is sufficient on its own:

1. **B-R's terms prohibit automated extraction.** This is the same line the
   project already drew over player photographs — the service-time figures
   are this project's own reconstruction and defensible as such; someone
   else's data or images republished are not.
2. **It is the project's own recorded decision.** The reference figures are
   hand-entered "both to respect B-R's terms and because a handful of
   hand-checked figures is enough to catch a systemic error."
3. **B-R is not the best oracle available anyway.** Finding #17 measured it
   sitting **4 days above MLB's own roster endpoint** on Braxton Fulford. The
   likeliest reading is that B-R has the official MLBPA ledger — the
   unpublished thing this project exists to approximate. Tuning toward B-R
   past the point where MLB's own rosters agree would be tuning toward a
   source we cannot see and cannot check.

**What is used instead: MLB's historical 40-man rosters, swept wide.** They
are free, unlimited, sanctioned, and give day-level truth rather than a career
total. `validate_against_rosters.py` now takes comma-separated `--team` and
`--season` lists and crosses them, caching each player's transactions across
the whole sweep, so dozens of club-seasons cost one fetch per player.

**And it classifies the errors rather than only counting them.** A summary
saying "0.4% over-credited" names no rule to change, which is where every
previous round stalled. Each disagreeing player-date is labelled by the roster
move on either side of it — Fulford's disputed day reads
`recalled+0d / optioned-1d` — and the labels are counted. A shape that
dominates is a candidate rule. **A long tail of singletons is not**: it is the
residue of a feed that does not record everything, and chasing it is precisely
how findings #4, #5 and #10 shipped plausible rules that measurement then
refused.

**For widening the independent check, effort is the constraint, so spend it
well.** `make_reference_worksheet.py` picks the players whose figures would
actually discriminate: on a 40-man, complete history (a player with missing
seasons scores GAP and so cannot fail), ranked by *churn* — the number of
roster moves the parser had to classify — and spread across service-time bands
and debut eras. Aaron Judge matching exercises almost none of
`service_time.py`; Rob Zastryzny with 81 moves exercises option boundaries,
DFA, outright, waiver claims, trades and IL transitions at once. `--json`
emits rows prefilled with everything except the figure itself.

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

### Gate status (2026-08-27, rules version 5 — CURRENT)

Same twelve club-seasons, same 13,678 judgements across 562 players:

```
AGREE        13,516  (98.8%)   was 13,511
MODEL OVER       37  ( 0.3%)   was 42
MODEL UNDER     125  ( 0.9%)   unchanged
```

**Strictly better and nothing regressed**: five over-credits became
agreements, under-crediting did not move a single judgement, and no
club-season got worse. That is the only shape a rule which can only *close*
an interval is allowed to have.

**Baseball Reference under v5: 17 passed, 0 failed, 2 known gaps** — identical
to v3 and v4. Scherzer +73d, Verlander +89d, no complete-history player moved.

What v5 did NOT fix, and why that is correct: Nick Maronde still reads
`over=12, agree=0` on the same `traded / recalled` shape. The rule only trims
the interval the arrival itself *opened*, and his was already open when the
trade happened — so `start == arrival` is false and nothing is touched. That
conservatism is what protects Caleb Ferguson's Tommy John year, and the price
is that trades into an already-open interval stay uncorrected. Widening it is
the same broad-rule trap; do not, without roster truth on the specific
players.

### Gate status (2026-08-26, rules version 4 — superseded by v5 above)

Twelve club-seasons in one sweep (Cleveland, Yankees, Tampa Bay x 2011, 2014,
2018, 2022), 13,678 player-date judgements across 562 players:

```
AGREE        13,511  (98.8%)
MODEL OVER       42  ( 0.3%)
MODEL UNDER     125  ( 0.9%)
```

The four gate club-seasons, v3 → v4:

| | v3 | v4 |
|---|---|---|
| Cleveland 2011 | 99.9% / 0.1% / 0.0% | **100.0% / 0.0% / 0.0%** |
| Yankees 2014 | 99.1% / 0.2% / 0.8% | 99.1% / 0.2% / 0.8% |
| Yankees 2018 | 99.8% / 0.1% / 0.1% | 99.8% / 0.1% / 0.1% |
| Tampa Bay 2022 | 98.7% / 0.4% / 0.9% | **98.9% / 0.2% / 0.9%** |

**Over-crediting did not rise anywhere, and fell where finding #19 fired.**
Cleveland 2011 is now perfect — 1,083 judgements, not one wrong. Tampa Bay's
over-crediting halved. The two that contained no claim-then-recall came back
byte-identical, which is what a narrow rule should do.

**Baseball Reference under v4: 17 passed, 0 failed, 2 known gaps** — identical
to v3, Scherzer +73d and Verlander +89d, both bounded by their own declared
presumed seasons. No complete-history player moved, which is the regression
detector reporting no regression.

The other eight club-seasons in the sweep, for context: Yankees 2011 and Rays
2014 also came back perfect; the worst is Cleveland 2022 at 94.7% (0.9% over,
4.4% under), driven by Carlos Vargas and Cody Morris alone.

### Gate status (2026-08-26, rules version 3 — superseded by v4 above)

| | agree | over-credit | under-credit |
|---|---|---|---|
| Cleveland 2011 | **99.9%** | 0.1% | 0.0% |
| Yankees 2014 | **99.1%** | 0.2% | 0.8% |
| Yankees 2018 | **99.8%** | 0.1% | 0.1% |
| Tampa Bay 2022 | **98.7%** | 0.4% | 0.9% |

These supersede the 2026-08-25 figures below, which were measured before the
validator applied finding #15 and so scored a model the site does not publish.
The correction moves agreement **up** in the two seasons it touches and leaves
over-crediting identical in all four — the old numbers were pessimistic, which
is the safe direction for a gate to be wrong in, but they were still wrong.

Findings #15 and #16 shipped together. Over-crediting is flat across all
four; under-crediting fell by two thirds in the two seasons that had any,
and the two that were already clean came back byte-identical. Worst
remaining players are Slade Heathcott (`under=4`, 2014) and Ben Heller
(`under=1`, 2018) — nothing concentrated enough to name as the next bug.

### Gate status (2026-08-25, first v3 run — superseded, see above)

| | agree | over-credit | under-credit |
|---|---|---|---|
| Cleveland 2011 | 99.9% | 0.1% | 0.0% |
| Yankees 2014 | 98.8% | 0.2% | 1.0% |
| Yankees 2018 | 99.8% | 0.1% | 0.1% |
| Tampa Bay 2022 | 98.6% | 0.4% | 1.0% |

### Gate status (2026-08-23, after the rules-version-2 recompute)

| | agree | over-credit | under-credit |
|---|---|---|---|
| Cleveland 2011 | **99.9%** | 0.1% | 0.0% |
| Yankees 2014 | **96.8%** | 0.2% | 3.1% |
| Yankees 2018 | **99.1%** | 0.1% | 0.8% |
| Tampa Bay 2022 | **98.6%** | 0.4% | 1.0% |

What remains is under-crediting, still concentrated in a handful of players
rather than spread thin: David Huff (16 of Yankees 2014's 36, `agree=0`),
Shawn Kelley (8), A.J. Cole (8 of Yankees 2018's 9), Josh Lowe (6) and Luke
Raley (5) on the 2022 Rays. Over-crediting — the failure mode behind every
revert in this project — is 0.1-0.4% across all four.

### Gate status (2026-08-21, before findings #13 and #14)

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

### 12. An inferred season needs something to corroborate it

**Found in the same pre-backfill audit.** Carry-in presumes a player is on a
roster from his debut until the feed says otherwise. For a player whose feed
is silent for years, that presumption had nothing bounding it at all.

Erick Almonte debuted in 2001, played about thirty major league games, and
has no transaction of any kind until 2011. He was credited **2005 through
2010 at the full 172-day cap** — six consecutive seasons with no stat line
and no transaction in any of them — for **6.064 years**.

Measured across the database: **57 players, 112 seasons, 19,264 days — 112
player-years.** By year:

| 2005 | 2006 | 2007 | 2008 | 2009 | 2010 |
|---|---|---|---|---|---|
| 57 | 27 | 16 | 7 | 4 | 1 |

A season is now credited nothing when **all three** hold: no transaction is
dated inside it, MLB's own year-by-year splits show no appearance in it, and
the only reason he looked active was an interval carried or presumed in from
elsewhere.

**This is evidence-based, not era-based**, and the distinction matters given
finding #1. Nothing in the rule mentions a cutoff year; 96% of what it
removes falls in 2005-2008 because that is where evidence is thinnest, which
is a *result* rather than an assumption. Verlander and Scherzer keep every
presumed season — their splits place them on a club in all of them —
and **no reference player moves at all** (total residual unchanged at 200
days).

The accepted cost is a player who spent a whole season on the injured list
without one dated transaction. Rare enough that it does not appear in the
modern era at all: one season in 2010, four in 2009, none after.

### 13. Finding #6 was real: the clock does bridge a gap spent in the minors

**Confirmed 2026-08-22 by probing Aaron Sanchez.** Finding #6 above — a
player's clock running through years he spent outside MLB — was marked
"LIKELY NOT REAL" and superseded. It is real. It was being looked for in the
wrong place.

His accrual interval, live:

```
2022-10-03 .. 2026-09-27      <- four years, one open interval
```

He last pitched in the majors in 2022. What the feed shows since:

```
2024-08-06   Buffalo Bisons released RHP Aaron Sanchez
2026-01-27   Kansas City Royals signed him to a MINOR LEAGUE contract
2026-06-23   Omaha Storm Chasers released RHP Aaron Sanchez
```

**Both releases are by minor league clubs**, so the MLB-club filter drops
them and nothing ever closes his interval. The Kansas City row survives the
filter — it names a major league club — so `career_end`, which fell back to
"his last transaction of any kind", landed in 2026 and the ceiling never
bit.

The ceiling was doing exactly what it was written to do. The fallback under
it was wrong: **a minor league signing is not evidence of a major league
roster spot.** It now asks when the feed last put him on one, from two
sources already fetched:

* the last season his year-by-year splits show an appearance
* the last transaction that is actually a roster event — a start or a stop —
  which still covers a man who was rostered without appearing

`lastPlayedDate` remains authoritative when the API supplies it; this only
replaces the fallback for the 1,058 non-rostered players who lack it.

**Scope is not measurable from the stored data**, because the stored data
has the same blind spot: Sanchez's 2026 season reads `src: "read"` on the
strength of that minor league signing. At most 218 players are affected (the
non-rostered players still accruing in the current season); the recompute
will produce the real figure. No reference player can move — all nineteen
are on a 40-man, and the ceiling only applies off it.

### 14. A Rule 5 return does not close the clock

**Found 2026-08-23 by probing Christian Cairo**, who read 172 days — a full
credited year — for a 2025 season he spent entirely in the minors.

```
2024-12-11  mlb  START  Atlanta Braves activated SS Christian Cairo.
2024-12-11  mlb    .    Atlanta Braves purchased contract of SS Christian Cairo
                        from Cleveland Guardians in the Rule 5 Draft
2025-03-20  mlb    .    SS Christian Cairo returned to Cleveland Guardians
                        from Atlanta Braves.
```

The Rule 5 selection opens an interval with "activated". Being **returned**
to his original organization is phrased with a verb the parser did not know,
so nothing closed it and the interval ran to the horizon. The roster probe
confirms it independently: he was on nobody's 40-man on any of 28 sampled
dates in 2025.

**The direction was measured, not assumed** — the lesson of findings #4, #5
and #10, where a plausible reading of MLB's vocabulary was encoded and was
wrong. Of the 53 `returned to` rows in the 65,109-row cache:

| count | shape | meaning |
|---|---|---|
| 18 | returned to an MLB club **from an MLB club** | Rule 5 return |
| 34 | returned to a minor league club **from MLB** | assignment ends |
| **0** | returned to an MLB club **from a minor club** | would be a START |

In every one the player *leaves* the club named after "from". There is no
case in the corpus where this wording puts a player **on** a major league
roster, so it is unambiguously a stop. A negative lookahead keeps it off
`returned to the active roster`, which is a start — no such row exists today,
but the two readings are one word apart.

**Measured live, rostered population, before → after:**

| | |
|---|---|
| players changed | 34 of 1,360 (2.5%) |
| credited days removed | **1,823** |
| credited days added | **0** |

Brewer Hicklen 2.060 → 0.035, Jacob Latz 4.167 → 2.145, Brendon Little
3.049 → 2.011, Cairo 1.000 → 0.002. Only-removes is the expected shape for a
rule that can only *close* an interval, and the opposite of the reverted
fixes in #4 and #5, which both added days.

⚠️ **Two measurement traps hit while confirming this**, both worth avoiding
again:

1. The first sandbox pass reported **zero** players changed. The script
   filtered transactions with a regex for nested `"id"` keys, but the cache
   stores `team_id` / `from_team_id` / `to_team_id` **flat**, so every row was
   dropped and every player fell through as having no transactions. Use
   `_involves_mlb_club` from the pipeline, never a hand-rolled filter.
2. The sandbox predicted 3,337 days removed; the pipeline removed 1,823. Both
   are right. The sandbox summed **raw interval days**; the pipeline credits
   only days inside a season window, capped at 172. Interval days and
   credited days are not the same unit — compare like with like, or compare
   only the *direction*.

### 15. RESOLVED: roster time before a player's first game is discarded

**Shipped 2026-08-25 as rules version 3.** The finding as originally written
follows; the rule that fixed it, and its measurement, are at the end.


**Found 2026-08-23 by probing Daniel Fields**, surfaced by
`report_debuted_but_empty()` after the rules-version-2 recompute. He debuted
2015-06-04 and reads **0.000**. His 2015:

```
2015-06-02  mlb  START  Detroit Tigers recalled Daniel Fields from Toledo Mud Hens.
2015-06-04  mlb  STOP   Detroit Tigers optioned CF Daniel Fields to Toledo Mud Hens.
```

He was on the roster from 06-02 and earned two or three days. He is credited
none, and `build_global_active_intervals` returns no intervals at all.

**The cause is not `accrual_floor`.** Relaxing the floor changes nothing,
because carry-in drops the transaction before the builder ever sees it:

```python
if t.date <= horizon_end
and (presume_active_from is None or t.date >= presume_active_from)
```

Every row dated before the debut is filtered out. That guard is right for a
pre-debut **stop** — the reason it exists — but it also discards the
**start** that actually put him on the roster.

**Prevalence is high and the cost per player is small.** Of the 1,331 cached
rostered players with a debut date, **744 (56%) have an MLB roster start
before their debut**, almost always one day before:

```
A.J. Puk            debut 2019-08-21   selected 2019-08-20   (+1d)
Andrew Vaughn       debut 2021-04-02   selected 2021-04-01   (+1d)
Alec Burleson       debut 2022-09-08   recalled 2022-09-07   (+1d)
```

Service time is roster time, not playing time, so each of those days is
genuinely owed. It only becomes visible as a **zero** when the whole stint
falls before the first game, as Fields's did.

⚠️ **The obvious fix is dangerous and must not be shipped without
measurement.** Moving the floor back to the earliest pre-debut start would
have used this row for Andrew Vaughn:

```
2019-06-28   Chicago White Sox signed 1B Andrew Vaughn.
```

His draft signing, two years before his debut, named an MLB club and reads
as a start. That is finding #2 all over again, and the debut floor is
precisely what stops it.

A safe fix has to distinguish a roster move (selected / recalled /
activated) from a signing, and probably bound how far before the debut it
will reach. Both are heuristics with magic numbers, which is the shape of
every rule this project has had to revert. **Measure on the cached
population before believing any of it**, and note that the Baseball
Reference residuals are currently mixed (±1-2 days, 8 exact), *not* a clean
systematic under-credit — so whatever is happening is not simply "everyone
is short by a day".

Two players currently read 0.000 because of this, both reported by
`report_debuted_but_empty()`: Elih Villanueva and Daniel Fields.

#### The rule, and why it is narrower than the mechanism

`roster_start_before_debut()` in `service_time.py` moves `accrual_floor`
back to the earliest transaction before the debut that is **both** a roster
move and inside a bounded window:

```python
PRE_DEBUT_ROSTER_WINDOW_DAYS = 45
_ROSTER_MOVE_RE = (selected/purchased the contract | recalled | activated
                   | reinstated | added to the active roster
                   | returned to the active roster | claimed off waivers)
```

Two guards, and both are load-bearing:

* **A signing is not a roster move.** This is what stops Andrew Vaughn's
  2019 draft signing — the trap recorded above — from reaching back two
  years. `signed`, `assigned to`, `drafted` and `traded` are all absent from
  the pattern on purpose.
* **45 days.** A club selects a contract a day or two before the debut, not
  a season before. The window is deliberately far wider than the +1d that
  744 of 1,331 players actually show, so it is a backstop rather than a
  tuning knob: nothing in the cached population sits between 45 days and a
  year, so the exact number is not a magic constant the result depends on.

`compute_service_time()` derives `presume_active_from` from `accrual_floor`,
so carry-in and the floor move together — the guard that was discarding the
start now no longer sees it as pre-debut.

**Measured, rostered population, before → after:** the rules-v3 recompute
credited about 2,900 days across the two rules together, and every one of
them is on the *front* of a career. Both zeros are gone: Daniel Fields reads
0.003, Elih Villanueva 0.001 — a one-game career now credited the one day
his appearance proves he was rostered.

⚠️ **A measurement trap hit while confirming this, worth recording.** The
first check compared yesterday's published file to today's, saw all nine
exact Baseball Reference players at +1, and concluded the rule was
over-crediting by a day league-wide. **It was not.** The two files were
generated a day apart and *every accruing player gains a day* in between.
The proof is in the split: accruing players had median +1 (764 of them
exactly +1), non-accruing players median 0 (3 at +1, i.e. the real movers).
Never diff two data files generated on different days and read the
difference as a rules change — recompute both under the same `horizon_end`,
or diff only the non-accruing population.

**The independent check is what settled it.** Under v3 the Baseball
Reference comparison went from 9 exact to **12 exact of 17**, with the
remaining five at ±1 day: deGrom −2 → **0**, Jansen −1 → **0**, Devers −1 →
**0**, Betts −2 → −1. Total absolute residual across the seventeen
complete-history players: **11 days → 4 days.** That is the shape a correct
front-of-career fix should have — it moves players toward B-R rather than
uniformly up.

### 16. RESOLVED: a trade is invisible to the parser

**Shipped 2026-08-25 as rules version 3, alongside #15.** The finding as
originally written follows; the rule and its measurement are at the end.


**Found 2026-08-24 by probing David Huff**, the largest single error left in
the roster gate — 16 of Yankees 2014's 36 wrong judgements, with `agree=0`:
the model never once agreed with the roster about him.

```
2014-06-06  mlb  STOP   San Francisco Giants designated LHP David Huff for assignment.
2014-06-11  mlb    .    San Francisco Giants traded LHP David Huff to New York Yankees for cash.
```

The DFA closes his interval on 06-05. The trade that put him on the Yankees
is not a start keyword, so nothing reopens it, and he is credited nothing for
a season MLB's own roster shows him **active** (`A`) on every sampled date
from 06-14 to 09-27.

CLAUDE.md already noted this shape for Ichiro in 2012 — "he arrived by trade
and stayed, and *traded* is not a start keyword" — where it was masked
because he later re-signed as a free agent and that *is* a start. Huff has no
such second event, so it is fully exposed.

**Measured over the 65,160-row cache:**

| | |
|---|---|
| `traded` rows | **1,258**, across 751 players (over half the rostered population) |
| surviving the MLB-club filter | 1,239 |
| currently read as a start | **0** |
| currently read as a stop | **0** |

So 1,239 roster-relevant rows are invisible to the parser entirely.

⚠️ **"traded = START" is NOT a safe rule**, and the measurement says why.
Of those rows, grouped by what happened in the ten days before:

| | |
|---|---|
| after a DFA — a start is correct (Huff's shape) | 166 |
| after an option — a start is **wrong**, he goes to the new club's minors | 60 |
| neither | 1,013 |

A blanket start keyword fixes the 166 and breaks at least the 60. The 1,013
depend on what state the player was in, which this measurement does not
resolve — a trade of an already-active player is a harmless no-op, a trade of
an outrighted one is not. So the damage is *at least* 60 and not bounded by
it.

The distinction that matters is **why the clock was stopped**: a DFA'd player
who is traded joins the new club's 40-man, an optioned player who is traded
reports to the new club's affiliate. `build_global_active_intervals` tracks
only active/not-active, not the reason, so a correct rule needs the state
machine to carry the last stop's *kind*. That is a real change, and it needs
`SERVICE_TIME_RULES_VERSION` → 3, a full recompute and a re-run of both
gates.

**Direction is under-crediting**, which is the safe side, and the gates pass
without it — so this is the best-understood remaining defect rather than an
urgent one. It is worth batching with finding #15, since both need the same
recompute.

#### The rule: a trade restarts the clock only after a DFA

The measurement above says a blanket start keyword is wrong, and it names
the distinguishing fact — **why the clock was stopped**. So the interval
walk now carries that:

```python
last_stop_kind = None          # "dfa" | "other" | None
...
if has_stop:
    last_stop_kind = "dfa" if any(_DFA_RE.search(r.description) for r in rows) else "other"
    ...close the interval...
elif has_start:
    if active_since is None: active_since = day
elif has_trade and active_since is None and last_stop_kind == "dfa":
    active_since = day         # Huff's shape: DFA'd, then traded onto a 40-man
    last_stop_kind = None
```

Note the `elif` chain: a trade is consulted **only** when the date carries
neither a start nor a stop, so it can never override an explicit roster
move on the same day, and stop-wins (finding #10) is untouched. And it fires
only when the player is *currently stopped* — a trade of an already-active
player stays the no-op it should be, which is what makes the 1,013
"neither" rows safe.

The 60 option-then-trade rows the naive rule would have broken are excluded
by construction: their `last_stop_kind` is `"other"`, so the branch never
runs.

**Measured live on the roster gate**, which is the check this was diagnosed
from:

| club-season | before (v2) | after (v3) |
|---|---|---|
| Yankees 2014 | 96.8% / 0.2% over / 3.1% under | **98.8% / 0.2% / 1.0%** |
| Yankees 2018 | 99.1% / 0.1% / 0.8% | **99.8% / 0.1% / 0.1%** |
| Tampa Bay 2022 | 98.6% / 0.4% / 1.0% | **98.6% / 0.4% / 1.0%** |
| Cleveland 2011 | 99.9% / 0.1% / 0.0% | **99.9% / 0.1% / 0.0%** |

**David Huff is gone from the worst-players list entirely** — he was
`under=16, agree=0`, the single largest error in the gate, and the model now
agrees with the roster about him. A.J. Cole, 8 of Yankees 2018's 9
under-credits, likewise: he was traded to the Yankees mid-2018.

**Over-crediting did not move in any of the four.** That was the condition
this change was held to before shipping — a rule that merely inflated
figures would have pushed it up, and 2% was the agreed revert line. It
stayed at 0.1–0.4%, exactly where v2 left it, while under-crediting fell by
two thirds in the two seasons that had any.

The two unchanged club-seasons are as much the result as the two that moved:
Cleveland 2011 and Tampa Bay 2022 came back **identical**, which is what a
rule that fires only on a specific DFA-then-trade shape should do to seasons
that do not contain one.

### 17. A one-day recall the roster never reflects, and a gap B-R can see that we cannot

**Raised 2026-08-26 by the owner**, comparing Braxton Fulford against Baseball
Reference: B-R has him at **97 days** entering 2026, we publish **94** for his
2025.

Probed day by day against MLB's own historical rosters (`probe_player.py`,
interval 1, the whole 2025 season). MLB's roster endpoint says he was accruing
on exactly three stretches:

```
2025-04-14 .. 2025-04-24   11 days   (contract selected 04-14; debut 04-16)
2025-06-06 .. 2025-06-30   25 days
2025-08-03 .. 2025-09-28   57 days
                          ---------
                           93 days
```

So there are **three different numbers**, and it is worth being clear which is
which:

| source | 2025 | |
|---|---|---|
| MLB's own historical 40-man rosters | **93** | day-by-day ground truth |
| what we publish | **94** | +1 |
| Baseball Reference | **97** | +4 over MLB's rosters |

**Our +1 is a single day: 2025-04-26.** He was recalled 04-26 and optioned
again 04-27, and the roster snapshot for 04-26 shows him *not* on the active
roster. The feed says he was recalled; the roster says he never arrived.

⚠️ **Do not "fix" this by treating short recalls as paper moves.** Measured
over the 1,368 cached rostered players, a start followed by a stop with
nothing between happens **793 times at one day**, 313 at two, 270 at three.
The overwhelming majority are real — the 26th man, a spot starter, a bullpen
shuttle — and each is a day genuinely earned. Transaction *shape* cannot tell
a phantom recall from a real one-day call-up; only roster truth can, and
over-crediting across the four gate club-seasons is 0.1–0.4%, so whatever
this is, it is not systemic. This is the same trap as findings #4, #5 and #10:
a plausible reading of MLB's vocabulary that measurement refuses.

**The B-R gap is the more interesting number and we cannot close it.** B-R is
4 days above MLB's *own* roster endpoint, not just above us. Nothing in the
public data reaches those days. The likeliest explanation is that B-R has the
official MLBPA ledger, which is exactly the thing this project exists to
approximate because it is not published. Recorded, not chased.

**A methodology note for the next time this comes up:** the first probe run
reported two *under*-credits on 04-14 and 04-15 that did not exist. That was
the validator bug in the bug history above, not a defect in the data. Check
that a probe applies the same floor as the pipeline before believing what it
says about a player.

#### Zach Agnos: the same shape, and this time we are exactly right

**Raised 2026-08-26 by the owner**, a second B-R discrepancy found the same
way. Probed day by day against MLB's rosters at interval 1, both his seasons,
with the bereavement fix (finding #18) already in place:

| season | compared days | agree | over | under |
|---|---|---|---|---|
| 2025 | 162 | **162** | 0 | 0 |
| 2026 | 187 | **187** | 0 | 0 |

**349 compared days, not one disagreement.** Our 293 days (1.121) is exactly
what MLB's own historical rosters say, including the three bereavement days
in June 2025 that the checker used to score against us.

So the pattern from Fulford repeats and is now two for two: **where B-R
differs from us, we match MLB's own roster endpoint.** That does not make B-R
wrong — the likeliest reading is still that B-R has the official MLBPA ledger,
which records things the public feed does not — but it does mean the gap is
not reachable from public data, and that tuning toward it would be tuning
toward a source that cannot be checked.

The practical rule this gives future sessions: **when a B-R discrepancy comes
in, probe the player against MLB's rosters first.** If we match the rosters,
the disagreement is B-R's extra information and there is nothing to fix. Only
a disagreement with the rosters is a defect in this project.

### 18. The truth source was wrong about bereavement and paternity days

**Found 2026-08-26 by the owner, chasing Zach Agnos.** His 2025 contains a
placement most players' histories do not:

```
2025-06-03  mlb    .    Colorado Rockies placed RHP Zach Agnos on the bereavement list.
2025-06-06  mlb  START  Colorado Rockies activated RHP Zach Agnos from the bereavement list.
```

The CBA is explicit, and this is sourced rather than inferred: a player on the
**Bereavement / Family Medical Emergency list** or the **Paternity Leave
list** counts against the 40-man, does **not** count against the active
roster, and **continues to accrue service time**.

**Our model already had this right, and it is worth saying how**: neither
placement is a start or a stop, so an open interval simply runs across them.
Nothing had to be added.

**The checker had it wrong.** `validate_against_rosters.py` treated anything
off the active roster and not IL-shaped as not accruing, so those days scored
as MODEL OVER — the gate marking us wrong for days we credit correctly. The
codes had been visible in its own output all along: `BRV` in Cleveland 2011,
`FME` and `PL` in Tampa Bay 2022, each sitting in the "treated as NOT
accruing" line.

`ACCRUING_INACTIVE_CODES = {"BRV", "FME", "PL"}` now joins the IL shapes, and
`probe_player.py` shares the same predicate so the two cannot drift apart the
way the floor rule did (see the bug history).

⚠️ **This is an a-priori addition to a script that refuses them on purpose.**
"Never hardcode what the data can tell you" is right there in this file, and a
guessed meaning for `RM` once inverted a fifth of a sample. The difference is
the source: the CBA states this, the feed does not have to be interrogated for
it. The **restricted list and suspended list are deliberately excluded** —
they look identical from the roster endpoint's point of view and do *not*
accrue — and every unclassified code still reports in `off_roster_codes`, so a
common one cannot hide.

Effect on the gate is small, one or two judgements per club-season, but in the
direction of scoring a right answer as right.

### 19. Swept wide, and there is no systematic rule error left to find

**Measured 2026-08-26**, after the owner asked for the figures to be made as
accurate as the available information allows. Nine club-seasons — Rockies, Red
Sox and Dodgers across 2021, 2024 and 2025, chosen because an earlier
32-club-season sweep put their over-crediting highest — sampled weekly against
MLB's own historical rosters:

```
11,078 player-date judgements across 408 players and 9 club-seasons
AGREE        10,916  (98.5%)
MODEL OVER       73  ( 0.7%)
MODEL UNDER      89  ( 0.8%)
```

Inside the gate on both counts. **The interesting part is the shape breakdown,
and its answer is that there is no shape to fix**:

| | judgements | distinct shapes |
|---|---|---|
| MODEL OVER | 73 | **69** |
| MODEL UNDER | 89 | **89** |

Exactly one shape occurs more than once: `recalled+0d / optioned-1d`, five
times — Braxton Fulford's case from finding #17, a recall reversed the next
day that MLB's roster never reflects. Five judgements in 11,078 is 0.05%, and
finding #17 already measured why transaction shape cannot separate those from
the 793 genuine one-day call-ups in the cache. Still not a rule.

Everything else is **per-player**, not per-pattern. The worst are Bernardo
Flores Jr. (over=13, agree=0), Kyle Hurt (under=14), Adael Amador (under=13),
Ryan Feltner (under=12), Sam Hilliard (under=11) — each one player whose state
is wrong for a stretch of weeks, and no two sharing a signature.

**So the aggregate conclusion: there is no systematic error visible at the
aggregate level.** But the sweep's *worst individual player* was worth
following, and that is where the rule came from — see below.

#### The rule it did produce: a claim straight to a recall is a minor league stint

Bernardo Flores Jr. came back `over=13, agree=0` — never once agreeing, which
is the exact signature that found David Huff and became finding #16. Following
that thread through the cache turned up the shape:

```
2025-08-21  Baltimore Orioles claimed LHP Josh Walker off waivers.
2025-09-29  Baltimore Orioles recalled LHP Josh Walker from Norfolk Tides.
```

**He cannot be recalled FROM Norfolk unless he was AT Norfolk.** So when a
waiver claim's very next major league move is a recall, the claim put him on
the 40-man and sent him down, and every day in between is phantom. This is the
feed's own words rather than a belief about MLB's vocabulary — the distinction
that separates it from the rules findings #4 and #5 had to revert.

**Confirmed against MLB's own rosters at one-day resolution, before shipping:**

| player | season | agree | over | under |
|---|---|---|---|---|
| Josh Walker | 2025 | **0** | **39** | 0 |
| Cole Sulser | 2023 | **0** | **59** | 0 |

Ninety-eight phantom days across two players, zero agreement on any of them,
exactly the windows the rule predicts.

Measured over the 1,368 cached rostered players: **16 changed (1.2%), 301
credited days removed, 0 added.** Only-removes is the expected shape for a
rule that can only close an interval, and it means over-crediting can only
fall.

⚠️ **The BROAD version is catastrophically wrong and is pinned as a test.**
Closing any interval that straddles any recall removes 2,848 days from 53
players — but Caleb Ferguson's interval runs 2019 to 2022 across his Tommy
John year, which he spent on the 60-day IL and therefore **accruing**, so it
deletes about three legitimate years. Patrick Corbin loses a whole real season
the same way. The shipped rule fires only when nothing at all sits between the
claim and the recall, and only trims the interval the claim itself opened.

Shipped as `SERVICE_TIME_RULES_VERSION = 4`.

#### Extended to trades, and the same-day trap avoided (rules version 5)

The v4 gate's own shape breakdown named the next one immediately:
`traded+... / recalled-...` was **24 of the 42 remaining over-credits (57%)**,
across Nick Maronde (`over=12, agree=0` — the never-agrees signature again),
Austin Meadows and Yohan Ramírez.

It is the same situation: a trade, like a waiver claim, puts a player on the
new club's 40-man without saying which roster he reported to. A recall
answers it.

**Confirmed against MLB's rosters before wiring**, as #19 was:

```
Yohan Ramírez, Cleveland 2022:  agree 11 | over 38 | under 0
```

Traded 2022-05-16, recalled from Columbus 2022-06-23 — 38 days, and the
offline measurement had predicted exactly −38 for him. Combined with the
claim case: **31 of 1,370 cached players (2.3%), 687 credited days removed, 0
added.**

⚠️ **A same-day trap, caught while shipping.** Ramírez was `activated` AND
`recalled from Indianapolis` on the same date. A row-by-row walk fired or did
not depending on which the API listed first — precisely the sort-order
dependence of finding #10, which was worth 160 days there. Detection is now
grouped **by date**: a recall anywhere in the next date's rows proves where he
was, whatever shares that date. Both orderings are pinned as a test.

**The general lesson, which is the opposite of what the aggregate suggested:**
the sweep's own summary says there is no shape worth chasing, and it is right
about shapes — but the *worst single player* is still a lead, because a
player who never agrees has a mechanism behind him. Aggregates rule out
systematic error; individual outliers are where the remaining rules live.

#### A flaw the run exposed in the diagnostic itself

The "69 distinct shapes" is inflated, and the reason is worth keeping. The
first classifier printed the **exact** day offset to the neighbouring move, so
Rio Ruiz — wrong for two months, sampled weekly — produced nine separate rows
(`waiver-claim+3d`, `+10d`, `+17d` ...) that were one defect. Sam Hilliard
produced eleven.

Offsets are now bucketed (`0d`, `1d`, `2-7d`, `8-30d`, `31-90d`, `90d+`) and
each shape reports how many distinct **players** it covers, not just how many
player-dates. A continuous error collapses to one row, which is what lets a
genuinely repeated shape stand out from it.

**The unit matters here the same way it did in finding #14**: player-dates are
not defects. One player wrong for a month at weekly sampling is one defect
worth four judgements, and counting the judgements makes a handful of players
look like a pattern.

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

**Findings #15 and #16 are shipped as rules version 3, the database is fully
recomputed, and all four gate club-seasons plus the Baseball Reference check
are green.** Every rostered player also has a crawlable page. What follows is
genuinely open work rather than a queue.

1. **Widen player pages to non-rostered players** — a one-line change to
   `_should_publish()` in `scripts/write_player_pages.py`.

   **Asked and decided 2026-08-26: hold until there is traffic data.** Not a
   forgotten item — revisit it 2-4 weeks after the domain and Search Console
   are live, when there is an answer to "do these pages rank at all?"

   The measurement the decision was made on:

   | | rostered (published) | non-rostered (would add) |
   |---|---|---|
   | players | 1,358 | **4,213** |
   | page payload | 6.5 MB | ~20 MB projected |
   | declare missing seasons | 0% | **27%** (1,148 players) |
   | accruing seasons that are `presumed` | 0% | **16%** |

   So it is four fifths of the database and the whole long-tail of search
   ("how much service time did X have"), and it is also where the estimates
   are weakest — the `presumed`/`absent` caveats do far more work on these
   pages than on any current player's.

   **Why waiting costs nothing and publishing early might.** The change stays
   one line however long it sits. But pages are rebuilt wholesale, so
   reversing means withdrawing thousands of URLs a crawler has already seen,
   and a young site does not want to teach Google that its URLs disappear.
   Publishing is the hard-to-undo direction; waiting is free.

   A third option was on the table and is worth remembering: publish only the
   ~3,065 non-rostered players whose records declare **no** missing seasons.
   That keeps the weakest estimates unpublished at the cost of a slightly
   less trivial `_should_publish()` and a line explaining why some players
   have no page.

2. **There is no analytics on the site**, so its traffic is unknown — and it
   is now the *blocking* unknown, because the pages exist but nothing can
   say whether they are found. It is also the input every question about
   advertising depends on. Cloudflare Web Analytics or GoatCounter would
   answer it without a consent banner; both are one script tag and neither
   sets a cookie. **Needs an owner decision on which.**

3. **~~A custom domain~~ — DONE 2026-08-26.** `bigleagueservicetime.com`,
   bought at Porkbun, apex on GitHub's four A records plus a `www` CNAME.
   `docs/CNAME` drove the switch and the validator caught the one hand edit
   (`og:url` and `canonical` in `docs/index.html`) exactly as designed.
   Remaining: tick **Enforce HTTPS** in Settings → Pages once the certificate
   provisions, then verify the property in Search Console and submit
   `https://bigleagueservicetime.com/sitemap.xml`.

4. **Fill `data/reference_super_two.json`.** Every row is still `published:
   null`, so `validate_super_two.py` reports the computed cutoffs and passes
   nothing. The figures land in the 2.11-2.14 band where reported cutoffs
   historically fall, which is encouraging and is *not* evidence — that is
   exactly the shape of every belief this project has had to revert. Needs
   published MLBTR cutoffs entered by hand.

5. **Owner decisions, recorded rather than pending:** no LICENSE file (so
   the code is readable but not reusable); advertising deferred until
   ~10,000 pageviews a month; player photos declined on copyright grounds.
   See "Deliberately not done".

### Known limitations

- ~~No handling for paternity/bereavement edge cases~~ — **resolved by
  finding #18.** Those placements are neither a start nor a stop, so an open
  interval runs across them, which is exactly what the CBA requires. What was
  actually wrong was the *checker*, and it is fixed.
- Service-time-manipulation grievance outcomes (e.g. Kris Bryant) are invisible
  to public transaction data.
- ~~Elih Villanueva reads 0.000~~ — **fixed by finding #15.** He debuted and
  last played on 2011-06-15, a one-game career, and now reads 0.001: the one
  day his appearance proves he was rostered. `report_debuted_but_empty()` in
  the daily job names anyone still in this class, and as of 2026-08-25 it
  names nobody.

## Discoverability: every rostered player has a real page

**Shipped 2026-08-25.** Until then the whole site was one URL. A profile
opened behind a `#` fragment, which no search engine follows, so a person
googling "Bo Bichette service time" could not land on the answer this
project computes — the single thing it exists to publish.

`scripts/write_player_pages.py` writes one static HTML file per rostered
player to `docs/p/<id>-<slug>.html`, plus `sitemap.xml` and `robots.txt`. It
runs from `_write_outputs()`, so the daily job and the backfill both keep it
current with no extra step.

### Why static files and not a routing shim

The tempting cheap fix is to let `app.js` read a path like `/p/592450` and
render the profile client-side. **It cannot work on GitHub Pages**, which
returns a genuine HTTP 404 for a path with no file behind it. A crawler sees
the 404 and stops; there is no JavaScript running to rescue it. Static files
are not the heavyweight option here, they are the only option.

The cost is bounded and was measured before committing to it: 1,358 files,
7.1 MB, written in about two seconds. They are rebuilt **wholesale** —
`shutil.rmtree` then regenerate — so a player who drops off a 40-man loses
his page rather than leaving a stale one to be indexed forever.

### Rostered players only, deliberately

`_should_publish()` gates on `on_40_man`. Retired players are four fifths of
the database and would be four fifths of the pages; the owner's call was to
publish current players first and see how they index. Widening it is one
line — see "Immediate next steps".

### Three things the pages have to get right

**1. Each page must stand alone.** A crawler and a first-time visitor both
arrive with no context, so every page carries the player's figure in its
`<title>`, a meta description, JSON-LD, and — the part that is easy to skip
— the same "this is an estimate, not an official MLB/MLBPA figure" caveat
the main site carries. A page that presents the number without it would be
the one page a stranger ever reads.

**2. The table must link with a real `href`.** A crawler follows links, not
click handlers. `playerHref()` emits `<a href="p/...">` for rostered players
and the click handler still opens the overlay, so nothing changes for a
person browsing the table while middle-click and "open in new tab" now work.
Non-rostered players have no page, so they stay a `<button>` — a link to a
404 is worse than no link.

**3. The two slug functions must never drift.** `slug()` in
`write_player_pages.py` builds the *filename*; `playerSlug()` in `app.js`
builds the *href*. A disagreement between them is a 404 on every affected
player, and it is silent — nothing in the pipeline would notice. They are
cross-checked against all 5,571 names (0 mismatches), and
`validate_published.py` now resolves every internal link on every generated
page against the filesystem, so a drift fails the check rather than shipping
1,358 quiet 404s.

### Accents are transliterated, not stripped

The first version ran `[^a-z0-9]+ -> "-"` over the raw name, which turned
"Adolis García" into `adolis-garc-a` and "Agustín Ramírez" into
`agust-n-ram-rez` — the accented letter vanishing entirely rather than
degrading to ASCII. Roughly a quarter of a 40-man roster has an accent in
the name, so this was not an edge case.

Both functions now NFKD-normalize and drop combining marks, which leaves the
base letter behind (`garcía` → `garcia`), with a small explicit map for the
characters NFKD will not decompose (`ø`, `ł`, `æ`, `ß`, `đ`, `þ`). Verified
identical across all 5,571 names in Python and in Node.

### Moving to a domain is one file: `docs/CNAME`

`SITE_URL` is **derived from `docs/CNAME`**, the same file GitHub Pages reads
to decide the domain — so the two can never disagree. `echo example.com >
docs/CNAME`, rerun the pipeline, and every canonical, `og:url`, sitemap entry
and JSON-LD `url` follows. `BASE_PATH` collapses from
`/MLB-Service-Time-Tracker/` to `/` at the same time, because a custom domain
is served from the root and a project page is not.

**One hand edit remains** and it is deliberately not automated:
`docs/index.html` is hand-maintained and carries its own absolute `og:url`.
`validate_published.py` fails loudly and names it, so the switch cannot ship
half-done. Everything else is generated. Dry-run the whole thing by writing a
CNAME, regenerating, running the validator, then deleting it again.

A stale canonical is worse than no canonical: it tells a search engine the
real page lives at a URL that no longer serves it.

### `docs/404.html` is generated, for one specific reason

GitHub Pages serves `404.html` for any unknown path on the site — including
paths under `/p/`, which is exactly where a stale or mistyped player URL
lands. **So every URL in it has to be site-absolute.** A relative
`styles.css` resolves against `/p/` for a bad player URL, 404s in turn, and
leaves an unstyled error page.

It used to be hand-written, which made those absolute paths the one thing
guaranteed to break on a domain move. It is now generated from `BASE_PATH`,
so it cannot.

It is `noindex`, and it names the real reason a link goes stale: pages exist
for current players only and are removed when a player drops off a 40-man.

### Club pages, so the player pages are not crawl leaves

Every player page used to be reachable only from the index and to link only
back to it — the flattest possible structure, and one that gives a crawler no
reason to treat any of it as a coherent section.

`docs/t/<club-slug>.html` is one page per club: its whole 40-man, sorted by
service time, with free-agency / arbitration / Super Two counts. Player pages
link up to their club, club pages link down to each player, and both carry
`BreadcrumbList` JSON-LD. 30 files, 0.3 MB.

They are also the query people actually type. "Phillies arbitration eligible"
is a far commoner search than any single player's name.

**`docs/t/index.html` is the directory**, and it exists so the hubs are one
hop from the homepage rather than three. Before it, a club page was reachable
only through a player page — index → player → club, exactly backwards for
pages meant to *be* the section hubs.

It is also why `index.html` carries **one** hand-written link, `t/`, rather
than 30 club links. A hand-maintained file holding 30 slugs would silently
404 one of them the first time a club renamed, and the Athletics have already
done that once. `validate_published.py` resolves that link too — a directory
href is checked against the `index.html` that actually serves it, so an empty
`t/` fails rather than passing as "a directory exists".

**A wording trap:** a 40-man roster can hold more than 40 — players on the
60-day IL stay on it without counting against the limit, and the Phillies
page reads 45. The count is exactly what MLB's roster endpoint returns, but
"all 45 players on the 40-man" reads like a contradiction, so the copy says
"45 players" without the "all".

### `lastmod` has to mean something or it means nothing

Every sitemap URL used to carry today's date, every day. In season that is
roughly half true — an accruing player really does gain a day — but the other
half of the roster does not move, and between the World Series and Opening Day
*nothing* does. A sitemap claiming 1,390 daily changes through a five-month
offseason is the textbook unreliable-`lastmod`, and Google's documented
response is to ignore `lastmod` for the whole site.

So each rendered page is hashed and the date only moves when the hash does.
`docs/data/page_lastmod.json` holds `{path: {hash, lastmod}}`.

Three decisions worth keeping:

* **Hash the rendered HTML, not the record.** A template change really does
  change the page, and hashing the underlying figures would miss it.
* **Strip the footer stamp first.** "Last updated YYYY-MM-DD" moves on every
  page every day, so without `_VOLATILE_RE` nothing would ever compare equal
  and the whole thing would be an expensive no-op.
* **A missing manifest degrades to "everything changed today"** — which is
  exactly the old behaviour, so losing the file can never publish a date that
  is wrong in a harmful direction.

It lives under `docs/data/`, which both workflows already stage, so this
needed no workflow change — unlike every other generated path.

Verified by simulating the next day's run: with identical data, 0 of 1,389
pages moved; with one player given a day, exactly 2 moved — his page and his
club's.

### The page CSS is a real file now

It was inlined into each of the 1,358 player pages: ~1.4 KB apiece, and
worse, uncacheable — clicking from one player to another re-downloaded the
same rules every time. `docs/page.css` is generated alongside the pages and
fetched once. Total page payload went **7.0 MB → 6.5 MB** even after adding
breadcrumbs, a richer JSON-LD graph and 30 club pages.

### The theme has to be carried by hand

`styles.css` honours `prefers-color-scheme` on its own, so a visitor who
never touched the toggle sees the right theme everywhere. An *explicit*
choice lives in `localStorage` under `mlb-service-time-theme`, and a static
page does not load `app.js` — so the player pages and `404.html` each carry
a four-line inline script that reads that key and stamps `data-theme`.

It is inline and in `<head>` deliberately: deferred or external, it would
run after first paint and flash the wrong theme on every click through from
the table.

### The workflows had to stage more than `docs/data`

Both jobs passed only `docs/data` to `commit_data.sh`, which would have left
`docs/p`, `sitemap.xml` and `robots.txt` generated but never committed — the
exact shape of the earlier bug where `index.json` sat frozen while the
database updated daily underneath it. Caught before shipping; both now name
`docs/data docs/p docs/t docs/page.css docs/404.html docs/sitemap.xml
docs/robots.txt` explicitly.

**Add every new generated path to both workflows.** The paths are listed by
hand rather than staging `docs` wholesale, so that `index.html`, `app.js` and
`styles.css` — which are hand-edited — are never swept into an automated
commit. That is the right trade, but it means a new output file is invisible
until someone remembers to list it.

### Frontend performance is measured and is fine — do not "optimize" it

The old note here said the frontend "loads the entire JSON at once" and
should be split if the dataset grew. **That was fixed and the note went
stale**: the browser downloads a 0.21 MB compact index, not the 8.8 MB
database, and a player's season detail comes from one of 64 shards fetched
only when a profile is opened.

Measured 2026-08-23 at a 4× CPU throttle (a rough stand-in for the owner's
iPad against a desktop runner), over the full 5,541-row table:

| | |
|---|---|
| one search keystroke | **18 ms** |
| typing 8 characters | 96 ms total |
| sort by name | 41 ms |
| sort by status | 50 ms |
| next page | 127 ms |

Every one is inside the interactive budget with room to spare. Filtering and
re-sorting all 5,569 players on each keystroke *sounds* like it should be
slow and is not. Re-measure before believing otherwise.

### Deliberately not done

**Player photos — rejected on copyright grounds (2026-08-23).** MLB's image
CDN is keyed by the same person id already stored, so a profile headshot
would have cost one `<img>` tag: no pipeline change, no storage, no API call,
nothing added to the table payload. It is the licensing that decides it.
Those are MLB's copyrighted photographs, and the owner's decision was to keep
the project clear of any copyright question rather than rely on the fact that
fan sites usually go unchallenged.

Worth understanding *why* it is a real risk rather than a theoretical one:
the service-time figures are this project's own reconstruction from public
transaction text and are defensible as such. Photographs are not — they would
be someone else's work republished. That distinction is the whole reason the
Baseball Reference figures are hand-entered rather than scraped, and it
applies here for the same reason.

It also interacts with advertising: photos on a free fan page is the
low-risk combination; the same photos beside ad units is commercial use of a
league's images and a much easier complaint to make. Photos and ads are close
to mutually exclusive, and the project has both turned off.

`scripts/probe_headshots.py` existed briefly to measure how many of the 5,569
players actually have a photo (the CDN answers 200 with a silhouette rather
than 404, so it compared response bytes against a known-silhouette
reference). It was removed with this decision. If photos are ever
reconsidered, that measurement is the first thing to redo — four fifths of
this database is retired players.

**Advertising — not now (2026-08-23).** Display ads on a niche sports site
return roughly $3-10 per 1,000 pageviews, and AdSense does not pay out below
$100. At this project's traffic that is years to a first payment, against a
mandatory cookie-consent banner, several hundred KB of third-party
JavaScript on a page whose entire payload is 0.21 MB, and a grey area in
GitHub Pages' terms about commercial use. Revisit above ~10,000 pageviews a
month; until then a GitHub Sponsors link costs nothing and carries none of
it.

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
