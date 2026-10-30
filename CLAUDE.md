# Big League Service Time Tracker — working guide for Claude

This is the short guide every session reads. It holds what you need to do real
work. The full history, every measured finding with its evidence, changelogs and
design rationale are in **`reference/CLAUDE-details.md`** — read only when
working on X: open it (search by section title) when you change the
service-time rules, Super Two, the duty-day tool, the contract clock, sitemaps/
indexing, or when a code comment says *"see CLAUDE.md, '<section>'"* (those
sections now live there). Don't read it end to end.

- **Repo:** `RainbowMang0/MLB-Service-Time-Tracker`. **Live site:**
  https://bigleagueservicetime.com — GitHub Pages, `main` branch, `/docs` folder
  (`docs/` IS the public website; `.nojekyll`, so any file there is served).
  `docs/CNAME` sets the domain and derives every published URL.
- **Name:** "Big League Service Time Tracker", never "MLB Service Time Tracker"
  (MLB is a trademark). Descriptive "MLB Stats API", "not an official MLB/MLBPA
  figure" stays.
- **Owner (Braxton) is not a full-time developer.** Explain the *why*, keep
  summaries short, lead with the result and the decision needed.

## Where things stand (2026-10-30)

| page | what | state |
|---|---|---|
| `/` | service-time tracker, 5,600+ players, daily refresh | mature |
| `/t/`, `/alumni/`, `/p/` | club hubs, alumni directory, player pages | live (~1,350 rostered, ~2,500 alumni pages) |
| `/taxes.html` | duty-day / jock-tax allocation | live, **not CPA-reviewed** |
| `/contract.html` | contract clock (short answer + full tool) | live; no salary projections (no salary data) |
| `/neutrality.html` | neutrality statement | live |

- **Rules version 6 engine is merged (PR #13), but the database is NOT
  recomputed** — non-rostered records stay on v5 until the owner runs the
  backfill with `--recompute-all`. Rostered players converge via the daily job.
- **Super Two (PR #17 merged):** validator passes 2021–2022; 2023–2025 still
  fail (+12/+3/−3 days). Re-check after the recompute; never tune toward the
  reference.
- **PR #15** (league-minimum salary estimate) is open, not merged.
- **CBA expires 2026-12-01** — see "CBA rules" below.
- Long-form, area-specific notes (dated 2026-10-28) are in `handoff/`
  (`site-expansion.md`, `contract-and-profiles.md`,
  `baseball-reference-validation.md`). Read the relevant one only when working
  in that area — they have the sharpest open-issue lists.

## Layout

```
scripts/fetch_mlb_data.py      thin statsapi.mlb.com client, polite rate limiting
scripts/service_time.py        the service-time math + all domain rules
scripts/cba.py                 loads config/cba/*.json; the ONLY reader of it
scripts/accrual_model.py       next-season accrual bands -> contract projections
scripts/fetch_schedules.py     club schedules + the state each game is played in
scripts/update_service_time.py daily job: 40-man rosters -> compute -> merge -> JSON
                               (SERVICE_TIME_RULES_VERSION lives here)
scripts/backfill_history.py    resumable backfill of non-rostered players
scripts/super_two.py           Super Two class + cutoff from the whole population
scripts/write_player_pages.py  static player/club/alumni pages, sitemaps, NAV_SECTIONS
scripts/commit_data.sh         commits generated data, surviving a concurrent push
scripts/validate_*.py          service_time (vs B-R hand entries), against_rosters,
                               super_two (offline), published (offline: files agree,
                               every link resolves)
scripts/probe_player.py        everything the model knows about one player
config/cba/2022.json           agreement in force; every value carries a source
config/cba/2027.json           placeholder that REFUSES to compute
config/cba/roy_full_year_awards.json  hand-kept ROY credits (not a ruleset)
config/tax/                    duty-day rules + 2026 state rates (tiered)
data/reference_*.json          hand-entered B-R figures, published Super Two cutoffs
data/cache/transactions/       per-player transaction cache (rostered only)
data/backfill_state.json       resumable backfill progress
docs/index.html, app.js, styles.css      main table (hand-edited)
docs/taxes.*, duty-days.js               duty-day tool (hand-edited; engine runs in node)
docs/contract.html/.js, contract-page.js contract clock (engine / UI)
docs/neutrality.html                     hand-edited
docs/data/service_time.json    the database (generated)
docs/data/index.json           what the browser downloads (generated)
docs/data/profiles/NN.json     per-player season detail, sharded by id % 64
docs/data/config/              published COPY of config/ (generated)
docs/p/ docs/t/ docs/alumni/   generated pages; also page.css, 404.html,
                               service-time.html, sitemap*.xml, robots.txt
.github/workflows/             ci.yml (every push/PR, offline), update-service-time.yml
                               (daily 8am ET), backfill-history.yml (manual),
                               validate.yml (manual), probe-coverage.yml
```

## Tests and checks (run before every PR)

```
python3 tests/test_service_time.py      # ~439 tests, no pytest; needs `requests`
node --test tests/duty_days.test.cjs     # 27
node --test tests/contract.test.cjs      # 41, includes the advice-vocabulary lint
python3 scripts/validate_published.py    # files agree + zero dead links
python3 scripts/validate_super_two.py    # currently exits 1 for 2023-2025 (known)
```

- Run the Python tests after touching `service_time.py` — they encode real,
  measured findings.
- CI (`ci.yml`) runs the offline checks on every push and PR.
- To view the site: `cd docs && python3 -m http.server`.
- The sandbox usually cannot reach statsapi.mlb.com; live checks run as Actions
  (Validate Service Time → rosters | reference | super-two | player | published | tests).

## Data pipeline and generated files

- Daily job (`update_service_time.py`, no flags) rebuilds only today's 40-man
  players, then rewrites index.json, profiles, pages and sitemaps.
  `--recompute-derived` redoes derived passes and published files from the
  stored DB with **no API calls** — use it for template/page changes.
- **Never hand-edit generated files** (`docs/data/`, `docs/p/`, `docs/t/`,
  `docs/alumni/`, `page.css`, `404.html`, `service-time.html`, `sitemap*.xml`,
  `robots.txt`). Change the generator and rebuild. When merging, take main's
  generated output and rebuild from cached data.
- **Add every new generated path to both workflows' `commit_data.sh` lists**
  (update-service-time.yml and backfill-history.yml); they list paths by hand.
- `config/` is copied to `docs/data/config/` by `publish_config()`;
  `validate_published.py` fails on drift.
- **Any change that would give a stored player a different figure:** bump
  `SERVICE_TIME_RULES_VERSION`, then the owner runs backfill `--recompute-all`
  in batches **clear of 12:00–13:00 UTC** (the daily job's 8am ET gate skips
  the day silently if a backfill holds the lock). `force: true` bypasses the gate.
- Key everything per player by **id, never by name** (duplicate names exist).

## Hard rules

1. **No scraping Baseball Reference, FanGraphs, or any salary site**, and never
   republish someone else's figures or projections. B-R figures are
   hand-entered by the owner only. No player photos or cartoon likenesses.
2. **Never commit secrets** (tokens, keys, `.env`).
3. **No live API recompute unless the owner asks.** Use cached data and
   `--recompute-derived`; the owner runs full recomputes/backfills.
4. **Salary display:** show the league minimum (from `config/cba`, never
   hardcoded) only for clearly pre-arbitration players with **no extension or
   other non-minimum/multi-year deal**; label it as an estimate; **when unclear,
   show nothing.** No other salary figures exist — there is no salary data.
5. **The site is an estimate** and says so prominently; keep the disclaimer and
   "not affiliated with MLB or the MLBPA".
6. **Never recommend** take/decline/should — `tests/contract.test.cjs` lints for
   it, comments included.
7. **A number nobody verified never ships as authoritative:** tax rates carry a
   tier (`verified` / `estimate_unverified` / `conflicting_sources`), CBA values
   a `sources` status — both test-enforced.
8. **Don't re-litigate measured findings without new evidence** — several
   plausible fixes were tried and reverted (index in the details file).
9. Never merge a PR without the owner's approval. Docs/code changes go through
   a branch + PR.

## CBA rules (pointers)

- Every threshold (172 days = 1 year, 3.000 arb, 6.000 free agency, Super Two
  22% / 86 days, league minimum…) lives in `config/cba/`. **Nothing may
  declare 172 or any threshold as a literal** — in code *or* prose.
  `test_the_explainer_prose_follows_the_ruleset_not_a_literal()` renders under a
  hypothetical 165-day agreement as the positive control.
- Python reads via `cba.default()` / `cba.load("2027")`; the browser reads the
  `rules` block stamped into `index.json`.
- `cba.require()` has no default on purpose — a missing value raises.
- New agreement: fill `config/cba/2027.json` from the agreement's own text,
  make it default, then full `--recompute-all`.
- Rules v6 specifics: season window starts at each club's own opener, ends on
  the last game actually played; 20-day optional-assignment rule; ROY full-year
  credit. 2020 is prorated. Clock stops at today (`horizon_end=TODAY`).

## Measured findings — index (detail in `reference/CLAUDE-details.md`)

#1 transactions sparse (not absent) before 2009 · #2 feed includes non-MLB ·
#3 2020 prorated · #4 retired player's clock stopped explicitly · #5 minor-club
"activation" ≠ left majors · #6/#13 clock bridges a minors gap · #7 keyword
match tolerates the name · #8 stop at today · #10 same-date: stop wins ·
#11 carry-in anchors on debut · #12 inferred season needs corroboration ·
#14 Rule 5 return doesn't close the clock · #15 pre-debut roster time discarded ·
#16 trade restarts clock only after a DFA · #17 Fulford recall/B-R gap ·
#18 bereavement/paternity · #19 claim→recall = minor stint · #20/#21 one-day
careers (Villanueva, Lonsway: accepted) · #22 rules v6. Known accepted residuals:
Soto +6d, Scherzer +73d, Verlander +89d (presumed seasons). Never hardcode MLB
status codes — calibrate from data.

## Working style

- Prefer `git apply` patches; verify file transfers with checksums.
- After changing a sentence that quotes a number, check the rendered output.
- Check where a string is already escaped before escaping again.
- Keep detail in commit messages and `reference/CLAUDE-details.md`, not chat.
