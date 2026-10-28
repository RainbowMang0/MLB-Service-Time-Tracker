# Handoff: site expansion

**For:** the site manager (the assistant that will run future work on this repo).
**Written:** 2026-10-28, against `origin/main` at `23783159d`.
**Scope:** everything built in the duty-day / contract / schedules / indexing
sessions, plus the decisions behind it. This file is a summary. The long-form
history, with the measurements and the reasons, is in `CLAUDE.md`.

> **Read this first.** `CLAUDE.md`'s "Where the project stands" block is dated
> 2026-09-08 and is out of date. Sections dated 09-10 through 10-28 sit below it
> and are correct. Refresh that block before relying on it.

---

## 1. What this area is

The site (bigleagueservicetime.com) started as a free service-time tracker. This
work added three free tools on top of it, plus the plumbing under them:

- **Duty-day / jock-tax tool** (`/taxes.html`): log where a player worked each day,
  allocate a salary across the states, and print a worksheet for a CPA.
- **Contract clock** (`/contract.html`): where a player stands against the
  arbitration and free-agency thresholds, when comparable players crossed them,
  and what an offer guarantees in present-value and net terms.
- **Neutrality statement** (`/neutrality.html`), as the brief requires.
- **Plumbing:** CBA thresholds moved into versioned config, a schedules pipeline,
  a statistics tab on player profiles, an alumni (retired-player) section, a
  split sitemap, and the Super Two reference data.

All of it is free and client-side. Nothing is stored on a server. A paid tier is
a later option, and any paid feature must be additive. Nothing that is free now
may be paywalled.

---

## 2. Status at a glance

| Item | State | Notes |
|---|---|---|
| CBA rulesets as config | **Done** | `config/cba/2022.json` is in force. `2027.json` is a placeholder that refuses to compute. |
| Duty-day engine | **Built** | Pure JS in `docs/duty-days.js`. 27 tests, passing. |
| Tax rates | **Partial** | 9 verified (no-wage-tax states), 14 estimate-tier, Georgia conflicting, 28 with no rate. See §3. |
| Duty-day UI and CPA export | **Built** | Not reviewed by a CPA. Blocking item 0a. |
| Schedules pipeline | **Live** | Daily job publishes 30 clubs. First live run reviewed 2026-09-08 (see §4). |
| Contract clock | **Built, accept side only** | Decline branch deliberately absent. Rebuilt 2026-09-11. |
| Stage panel and ex ante framing | **Partial** | Share-of-value figures withdrawn by the research. See §3. |
| Research file | **Written, nothing publishable** | `research/valuation-literature.md`. Every figure is abstract-only. |
| Statistics tab | **Built** | Statlines from data already fetched. No WAR, no earnings. |
| Alumni (retired players) | **Partly published** | 2,466 published. 1,756 held pending a measurement. |
| Split sitemap | **Built** | Reports indexed-vs-submitted per section. |
| Super Two reference | **Filled, 2021–2025** | Checked against our computed cutoffs. See §4. |
| Navigation | **Built** | Seven sections. Duty Days and Contract Clock carry an "in development" tag. |
| Homepage date | **Built** | "Last updated" date under the title, 2026-10-28. |

Test counts on `origin/main` as of this handoff, all passing:
`python3 tests/test_service_time.py` → **356**;
`node --test tests/duty_days.test.cjs` → **27**;
`node --test tests/contract.test.cjs` → **41**.

---

## 3. Key decisions, and why

**Free and client-side, no server.** A server would add accounts, a database,
encrypted columns and a breach surface. If nothing is stored server-side, the
privacy posture is a fact rather than a promise. The cost is no sync and no
alerts. Both were judged worth it.

**Three rate tiers, because "verified" must keep meaning something.** Each
jurisdiction's tax rate carries one of three tiers:

- `verified`: a person read it off the state's own guidance. Only the nine
  no-wage-tax states qualify, because "no wage tax" is structural, not a lookup.
- `estimate_unverified`: compiled from web-search summaries. It produces a number,
  labelled rough everywhere it appears. Fourteen states are here.
- `conflicting_sources`, or no rate: no number at all. Georgia is conflicting
  (5.19% and 5.49% from two summaries on the same day). Twenty-eight have no rate.

**The stored rate is the band a salary sits in, not the headline top rate.** New
York's headline 10.9% starts at $25M. The band most major league salaries sit in
is 9.65%. Storing the headline rate would overstate nearly every player. A test
pins this, so nobody "corrects" it upward. Massachusetts and California have the
same shape.

**Totals are never presented as a tax bill.** Every total says it is before any
home-state resident credit, and it says so differently for a player domiciled in
a state with no income tax. Getting that branch backwards was a real bug, now
pinned by a test.

**The decline branch of the contract tool is absent, not approximated.** Valuing
a decline needs league-wide salary data. The project has none, and no permitted
source has been found. Republishing arbitration projections is the same line
already drawn over Baseball Reference and MLB photographs. A breakeven built on
invented salaries would be the most confident-looking and least defensible number
on the site. `valueOffer()` ships `declineModelled: false`, and the page says so.

**"Never recommend" is enforced by a test.** The brief forbids directive language
("you should", "take the deal", and so on). `tests/contract.test.cjs` greps the
engine and the page for those phrases and fails on a hit, comments included.
Disclaimers are exempt by context. The file list is asserted to exist, so a typo
cannot quietly skip a file.

**Every CBA figure, in both the arithmetic and the prose, comes from config.**
Consolidating the constants was not finished when the code stopped declaring 172.
Twenty literals remained in the page text. Those are now derived too. A positive
control renders under a hypothetical 165-day agreement and fails if "172" survives.

**Projections are measured, not assumed.** The contract tool projects thresholds
from the database's own accrual history: 16,333 player-season transitions, banded
by service. Assuming full seasons gives the most optimistic answer and hides a
four-year spread. Excluded: prorated seasons, the unfinished current season, and
debut-presumed seasons. A band with fewer than 100 samples refuses to project.
A flat rate across the walk was a bug. A 0.086 player came out at 30 seasons
instead of 12. The walk now re-reads the band each season.

**Research is not published until a human has read the paper.** The literature
file uses four tiers: `cited`, `single_source`, `abstract_only`, and
`not_peer_reviewed`. Every figure there is `abstract_only` or thinner. Reading
the two key papers in full is the next research step, and it needs access the
build environment does not have. The research also killed the stage panel's
share-of-value figures. Krautmann's own papers put the same players at opposite
signs, so a "range" across them would be a measurement that is not one.

**Publish retired players as a measured batch, not all at once.** The 2,466
published alumni have complete history and more than one credited season. The
other 1,756 stay held. `sitemap-alumni.xml` reports them separately, so Search
Console can say whether retired-player pages index at all. Do not publish the
held 1,756 until that report exists.

**Alumni pages are reachable.** An A–Z directory at `/alumni/` is linked from the
homepage. Without it, a published alumnus would have no inbound internal link
from the pages that carry the site's links. The navigation strip on every page
also links into the sections.

**Crawl budget is spent on real changes only.** A daily counter tick was
rewriting 81% of the sitemap. The content hash now normalises the Y.DDD figure,
the spelled-out figure, the day counts and the meter percentage. On a simulated
ordinary day, 4 pages move instead of 1,348. A test pins what does still count
as a change: status, club, a new season row, a name, and any template edit.

**Navigation reversed an earlier decision, on purpose.** On 2026-09-08 the two
unfinished tools were kept off the homepage. On 2026-09-11 the owner put all
five sections in the menu. Duty Days and Contract Clock carry an "in
development" tag. The tag is the condition attached to the reversal. A test
asserts the homepage link may exist only while the tag does.

**No photos, and no cartoon likenesses.** Photos are copyright. A cartoon is not
a fix. A likeness that is recognisably one player falls under the right of
publicity. The case law is against it: Keller and Hart (EA's non-photographic
avatars of college athletes) both lost. Facts are not copyrightable, and the
project stands on facts. Visual richness can come from club colours, numbers,
position glyphs, initials, and the meter itself.

**No scraping of Baseball Reference, FanGraphs, or any site that forbids it.**
WAR is not a field MLB publishes. The public WAR figures come from sites that
forbid automated extraction. The statistics tab says so, and a test fails if a
WAR column appears.

**Schedules publish the state a game is played in, not the club's home state.**
A null means the venue cannot be resolved, and the UI asks. The Athletics play in
three states this season, so their home state is null by design.

---

## 4. Known bugs, risks, and open questions

**Blocking, and not code:**

- **0a. CPA review.** No tax professional has reviewed the duty-day methodology
  or the disclaimer wording. This blocks promoting the tool to anyone.
- **0b. MLBPA and club question.** Nobody has asked whether an active player
  distributing a compensation-modelling tool inside clubhouses needs clearance.
  This blocks promotion.

**Data and accuracy:**

- **Tax rates are unverified in substance.** The 14 estimate-tier rates come from
  web-search summaries of secondary sites. Each needs a primary source before it
  is trusted. The sandbox could not open any state site.
- **Georgia has no rate** until its sources are read directly.
- **Super Two precision.** Against the published reference, our cutoffs are exact
  for 2021, within 3 days for 2024 and 2025, 7 days out for 2022, and 12 days out
  for 2023. The method is validated. The precision is not. Do not present the
  computed cutoff as exact.
- **2026-27 Super Two.** MLB has not published it. Our computed figure is 2.127.
  MLB Trade Rumors' estimate has moved between 2.130 and 2.134. MLB finalises in
  November. Add the official figure as a sixth row in the reference file when it
  lands. It is the first prediction that will be checked against a published
  number.
- **Villanueva (finding #20) and Lonsway (finding #21)** read 0.000 despite
  debuting. Same shape: a start and a stop on the same debut date. Stop-wins
  zeroes them. A fix to credit one day to each was measured across 1,380 players:
  two days in total. Not shipped. Their pages no longer claim they were never up.
- **Lonsway's probe against MLB's rosters is still open.** Actions → Validate
  Service Time → `player`, id 675920, season 2026, interval 1. The sandbox has no
  route to statsapi, so this could not be run here. If the rosters never list him
  as active on 2026-08-29, we match them and there is nothing to fix.
- **Postseason placeholders.** About 106 games publish with no state: "AL Stadium",
  "NL Stadium", "TBD". The four Mexico City games are also null. The UI asks.
  Suppressing placeholders would need a rule about which game types are real.
  Judged more risk than the untidiness.
- **The statistics tab was not checked against the payload.** Confirm the table
  download is unchanged, and the statlines sit in a shard fetched on demand. This
  session did not verify either.

**Indexing (the main open measurement):**

- As of 2026-09-08, Search Console showed **782 pages discovered but not indexed**,
  eight days after the sitemap was submitted. The site was new, so this is partly
  normal.
- Diagnosis: thin templated pages (median 199 words, of which about 28 words per
  page are unique), and weak internal linking. The homepage's static HTML has
  only two internal links. Player pages are three hops from home.
- **Check the split sitemap report in Search Console first.** Club pages indexing
  while player pages do not points at thin content. Both lagging equally points at
  site age. This session did not see the current report.
- **Traffic data.** A reminder was scheduled for 2026-09-22 to check Cloudflare
  analytics and Search Console. That date has passed. Nobody has recorded what
  was found.

**Process risks:**

- **`CLAUDE.md` is long (4,253 lines) and its status block is stale.** Treat the
  dated sections as the history and check them against the code.
- **The Python suite needs `requests` installed locally.** CI installs it. A fresh
  sandbox did not have it.
- **Live verification needs Actions runs.** The build sandbox blocks
  `statsapi.mlb.com`, Baseball Reference, and every state tax site. Anything that
  touches the live API must be checked by a workflow run.
- **Never key data by name.** Two different Max Muncys are on 40-man rosters right
  now (ids 691777 and 571970). The contract picker keys on name and club, and
  refuses an ambiguous typed name. Thirty-six names are duplicated in the
  database.

---

## 5. Files that matter

**Config (the rules, versioned):**
- `config/cba/2022.json`: the agreement in force. Every value carries a source status.
- `config/cba/2027.json`: a placeholder. Filling it makes the site current on the day a new agreement lands.
- `config/tax/duty-day-rules.json`: day types and whether each counts as a duty day.
- `config/tax/2026-states.json`: 52 jurisdictions, each with a tier.

**Python (build-time):**
- `scripts/cba.py`: the only reader of `config/cba/`.
- `scripts/service_time.py`, `scripts/super_two.py`: the engines, parameterised by ruleset.
- `scripts/accrual_model.py`: builds `docs/data/accrual_model.json`.
- `scripts/fetch_schedules.py`: publishes club schedules. `MLB_CLUB_IDS` filters out spring-training opponents.
- `scripts/update_service_time.py`: the daily job. It publishes config and the accrual model.
- `scripts/write_player_pages.py`: generates player, club, alumni and sitemap pages. Holds `_is_alumnus`, the split-sitemap writer, and `HAND_WRITTEN_PAGES`.
- `scripts/validate_published.py`: checks that published files agree and every link resolves.

**Browser (the tools):**
- `docs/duty-days.js`: duty-day engine. Pure, dual-mode, runs under Node.
- `docs/taxes.html`, `docs/taxes.js`, `docs/taxes.css`: the duty-day page.
- `docs/contract.js`: contract engine. Pure. Contains `seasonsToReachBanded`.
- `docs/contract-page.js`: contract UI. Only formats, never computes.
- `docs/contract.html`: contract page.
- `docs/neutrality.html`: neutrality statement.

**Data:**
- `docs/data/accrual_model.json`: next-season accrual by service band.
- `docs/data/schedules/2026/`: one file per club, plus `index.json`.
- `docs/data/config/`: published copy of `config/`. Checked for drift by the validator.
- `data/reference_super_two.json`: published Super Two cutoffs, 2021–2025, hand-entered. `ours` is filled by the validator and must never be pasted in.
- `research/valuation-literature.md`: the literature review. Not for publication until read in full.

**Tests:**
- `tests/test_service_time.py` (356), `tests/duty_days.test.cjs` (27), `tests/contract.test.cjs` (41).

**Workflows:**
- `.github/workflows/update-service-time.yml`: the daily job, with the 8am Eastern gate.
- `.github/workflows/validate.yml`: manual checks, including `tests`.

---

## 6. Ideas discussed, not built

- **WAR on the statistics tab.** Blocked by the terms of the public sources.
- **Earnings history and the decline branch.** Both need licensed salary data.
- **Future value and the ex ante method.** Needs the salary data above, plus a price
  for a win. The method is in Solow and Krautmann, not yet read in full.
- **A single `/free-agency.html` page.** The most-searched question in the niche,
  and it deserves its own URL. Wait for the split-sitemap report first.
- **Per-player free-agency pages.** Would add thousands of thin pages to the set
  already under-indexed. Not without the report.
- **Publishing the 1,756 held alumni.** Only after `sitemap-alumni.xml` has reported.
- **Teammate cross-links, or `noindex` on the thinnest pages.** Real options with
  real trade-offs. Not decided.
- **A debut-day credit rule.** Would fix Villanueva and Lonsway. Worth two days in
  total, so not worth a recompute.
- **A 2027 proposed-cap comparison.** The contract tool's ruleset comparison
  already works. It needs the agreement, or a proposal, as a config file.
- **A paid tier** with sync, alerts, multi-season history and agency views. Later,
  and additive only.
- **Advertising.** Deferred until roughly 10,000 pageviews a month.
- **Persistence and export for the contract tool.** The duty-day tool has both. The
  contract tool has neither. Not built.
- **Player photos and cartoon likenesses.** Rejected. See §3.

---

## 7. Working notes

- **Run the tests** from the repo root:
  `python3 tests/test_service_time.py`,
  `node --test tests/duty_days.test.cjs`,
  `node --test tests/contract.test.cjs`.
  The Python suite needs `pip install requests` first.
- **Run the validator** after any change to generated output:
  `python3 scripts/validate_published.py`.
- **Regenerate derived output without API calls:**
  `python3 scripts/update_service_time.py --recompute-derived`.
  This is the right way to test a template change. It does not touch the
  database.
- **Do not stage `docs/` wholesale.** Hand-edited files (`index.html`, `app.js`,
  `styles.css`) must never be swept into a generated commit.
- **Every new generated path must be added to both workflows' `commit_data.sh`
  lists.** An unlisted output is silently never committed. This has happened
  before.
- **Changing a threshold means one file.** Edit `config/cba/`. Do not hardcode.
  The prose is derived too, and a test checks it.
- **Copy discipline.** Check any new user-facing sentence against the advice
  vocabulary list in `tests/contract.test.cjs`. That list is the only place the
  banned phrases are written down.
- **Summaries for the owner should be short.** Lead with the result and the decision
  needed.
