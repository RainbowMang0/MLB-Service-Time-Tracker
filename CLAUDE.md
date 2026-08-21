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
than stops. `history_complete` wants to become a graded signal (how much of
this player's career is actually visible?) rather than a boolean keyed to a
year. Not yet done.

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

**Still short of the gate** (93.7% vs ≥95%, 2.6% vs ≤2% over). What is left
is again concentrated in a handful of players, so it is diagnosable rather
than diffuse:
- under: David Huff (16), Shawn Kelley (12) — 28 of 43
- over: José Ramírez (13), Austin Romine (11) — 24 of 31

**Fix 2 (not yet attempted): carry-in.** If the first transaction seen for a
player is a STOP, he must have been active before it, so the interval should
open at the window start. This is what the vestigial
`carry_in_active_first_season` parameter was for. Measure it the same way —
one change, one before/after run — rather than bundling it with anything
else.

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

### Gate before a mass backfill

1. **Roster accuracy** — agreement ≥95% and over-crediting ≤2%, on at least
   two different club-seasons (one recent, one pre-2019 for disabled-list
   era wording).
2. **Baseball Reference spot-check** — `data/reference_service_time.json`
   still ships empty. Actions → "Validate Service Time" → reference. This is
   the only *independent* check: the roster comparison validates the
   pipeline against the same source it is built on, so a systematic
   misreading of MLB's semantics passes it. Needs figures entered by hand.
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
