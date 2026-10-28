# Handoff: contract and profiles

**Written:** 2026-10-28, for the site manager taking over this work.
**Covers:** the session from 2026-09-08 to 2026-10-26. It touched the contract clock, the player statistics tab, the Super Two reference check, site navigation, and the research list behind the contract tool.
**Figures:** counts are from the data on `main` as of 2026-10-28. The daily job rewrites them every morning, so re-check before quoting.

Read `CLAUDE.md` first. It is the project handbook and holds the reasoning behind the accrual engine, the Super Two method and the earlier tools. This file records what this session built and decided, and points into `CLAUDE.md` where the detail lives.

---

## 1. What this area is

Five pieces. Everything runs in the browser, nothing is stored on a server, and nothing is paywalled.

1. **Contract clock** (`/contract.html`). Answers when a player reaches arbitration and free agency, how old he will be then, and what he is paid until then. It opens on a one-question short answer. The full five-panel tool sits behind a toggle.
2. **Statistics tab** on the player profile. Season-by-season hitting or pitching lines from the MLB Stats API.
3. **Super Two reference check.** Published cutoffs for 2021–2025, entered by hand. The table's Super Two tile quotes them and says whether the figure on screen is a prediction or a checked number.
4. **Site navigation.** One menu on every page, with the two unfinished tools marked "in development".
5. **Research list** (`research/valuation-literature.md`). Peer-reviewed work on valuing a player at each career stage. The contract tool's wording and structure come from it.

---

## 2. What's done

Commits on `main`, oldest first:

| Commit | Date | What |
|---|---|---|
| 49c2bd11c | 09-08 | Audit fixes to the contract tools (§2.1) |
| cc55ee291 | 09-08 | Research list, first pass, from abstracts only |
| ddd43e53f, 30a5de4f7, 2fdc0ec1c, aa68654ab, 9773fa7ce, 5096ca083 | 09-10 | Works read in full, one commit each (§5) |
| c5087b653 | 09-10 | Stage panel, age at threshold, ex ante framing |
| 1e3eaa01a | 09-11 | Site navigation on every page |
| d3391941f | 09-11 | Short answer leads; full tool behind a toggle |
| 0fe2b2d48 | 09-11 | Statistics tab on the player profile |
| a46531b9f | 10-25 | Merge of the above into `main` |
| 137a6087c | 10-26 | Super Two reference filled for 2021–2025 |
| ee4b9c778 | 10-26 | Tooltip uses prediction or checked wording |

Since then `main` has also received the daily runs of 10-27 and 10-28, and PR #9 (898d6860f). PR #9 adds a last-updated date under the homepage title. It is not part of this work.

### 2.1 Audit fixes (49c2bd11c)

- **The projection held one band for the whole career.** The walk used the rate of the band the player was in today. For a 0.086 player that gave free agency after 30 seasons (2056). Re-reading the band each season gives 12 (2038). Now `seasonsToReachBanded()`.
- **The player picker matched on name.** Two players named Max Muncy were on 40-man rosters at the time of the audit (ids 691777 and 571970; 289 and 1,741 days). Selection now uses name plus club, adds the id if that collides, and a typed name that is ambiguous refuses.
- **The advice lint skipped the file that writes the sentences.** The test read `contract.js` twice and never `contract-page.js`. It now reads the UI file too.
- **The homepage linked neither tool.** It does now (§2.8).

### 2.2 Short answer (d3391941f)

This is the default view of `/contract.html`. It has one input: pick a player, or type a service time such as `2.100`, which means 2 years and 100 days. It shows three readouts:

- **Arbitration:** the median season, with the typical range (20th to 80th percentile) beside it, and his age at the median.
- **Free agency:** the same.
- **Until then, paid:** the league minimum, for the pre-arbitration seasons left. It reads "Negotiable" once he is arbitration eligible or a Super Two.

Plain notes follow, including "the years are a median drawn from comparable players, not a schedule" and "this does not say what the player is worth" (§4).

### 2.3 Full tool (behind "Show the full tool")

Five panels:

1. **The clock.**
2. **What changes at each line.** Bargaining position at 3.000 and 6.000, explained by mechanism and rights. No dollar share of value (`stageOf()`).
3. **When comparable players crossed the line.** p20, median and p80 by threshold, with age at the median.
4. **The same player under two agreements.** 2022 against a second ruleset, or a proposal the user enters.
5. **What an offer guarantees.** Season-by-season offer. Guaranteed and non-guaranteed money are kept apart. Present value uses a user rate, and deductions use user rates.

The toggle is remembered per browser under `mlb-contract-advanced`.

### 2.4 Engine (`docs/contract.js`)

The engine is pure: no DOM, and it loads under Node. Main functions: `clock`, `shortAnswer`, `minimumSeasons`, `outcomeSeason`, `ageInSeason`, `withAges`, `stageOf`, `bandFor`, `seasonsToReach` (flat, kept for the ruleset comparison and its tests), `seasonsToReachBanded`, `project`, `presentValue`, `netOf`, `valueOffer` and `compareRulesets`.

Guard rails, all in code:

- A band with fewer than `MIN_BAND_SAMPLE` (100) careers reports `enough_data: false`, and the walk stops. It does not borrow a neighbour's rate.
- A zero rate reports "not reached at this rate", never a year.
- A season is capped at 172 days.
- `valueOffer()` returns `declineModelled: false`, and the page says there is no decline branch.
- Non-guaranteed money is reported apart and never added to the total.
- Every deduction rate is a user input (`allRatesAreUserInputs: true`).

**Age.** `birth_year` is a new column in `index.json`, taken from the `birthDate` the pipeline already fetched. Only the year is used, so the page says "about 29", never a date. A missing birth year reads "age not known".

**Survivorship.** The bands describe players who were in the majors the previous season, and the page says so. The flat 5–6 year band is survivorship, not durability.

**Advice lint.** `tests/contract.test.cjs` greps the engine, the page and their comments for directive vocabulary. Disclaimers are exempt by context.

### 2.5 Statistics tab (0fe2b2d48)

- It opens from a name in the table, or by deep link `#player/<id>` (a slash, not a dash).
- One table for hitting and one for pitching. Two-way players get both.
- Hitting columns: G, AB, R, H, 2B, 3B, HR, RBI, BB, SO, SB, AVG, OBP, SLG, OPS. Pitching columns: G, GS, W, L, SV, IP, SO, BB, H, ER, HR, ERA, WHIP. A column appears only if some season carries it.
- The panel states "No WAR and no salary", and says why.
- The tabs follow the WAI-ARIA pattern: arrow keys move between them, with a roving tabindex (`wireProfileTabs`).
- Data: `season_stats` on each record, served from the profile shards. It is not in `index.json`, so the table's payload did not grow.
- Field names were checked against a live payload on the 2026-10-26 run. All 15 hitting and 13 pitching keys arrived on every season row they apply to, and nothing unexpected arrived.

### 2.6 Super Two (137a6087c, ee4b9c778)

- `data/reference_super_two.json` holds the published cutoffs for 2021–2025, entered by hand from MLB Trade Rumors and AP offseason reports. Each row is labelled by the season the class *finished*, not the offseason it was announced in. The article's URL date is the evidence for the label.
- `update_service_time.py` ships them in `index.json` as `super_two_published`.
- The tooltip used to quote our own estimates as if they were published ("moved between 2.126 and 2.136"). It now quotes the published range, 2.116 to 2.140.
- The "still being played" sentence depends on `qualifying_season_complete`. That value is measured, not taken from the calendar.
- A second fix followed the same day. The tooltip described our 2026 figure, 2.127, in MLB's voice ("the cutoff after the 2026 season was 2.127"), although MLB has not announced it. The wording now depends on whether the reference file has that season. Unpublished: "a prediction, not a published number." Published: "a number that is already known." Both cite the measured record.
- Pinned by `test_an_unpublished_cutoff_is_called_a_prediction_not_a_fact()`, which also asserts that the live payload is in the prediction case.

### 2.7 Navigation (1e3eaa01a)

- `NAV_SECTIONS` in `scripts/write_player_pages.py` defines the menu. `_site_nav()` renders it for generated pages with BASE_PATH-absolute links. Hand-written pages carry literal copies.
- It is styled by `.site-nav` and `.nav-tag` in `styles.css`. It uses no JavaScript. Narrow screens scroll it sideways. `aria-current` marks the page you are on.
- A test asserts the same destinations on every page.
- The current menu: Service time, By club, Previous players, What is service time?, Contract clock (in development), Duty days (in development), Neutrality.

### 2.8 Homepage (49c2bd11c, 1e3eaa01a)

The homepage links every section, with the two unfinished tools marked. The owner first asked to hold the links back until the tools were ready. The owner then chose to link everything and mark the two.

---

## 3. What's half-done

- **Statistics tab.** Works for current 40-man players who have a statline. It is empty for retired players (§6, item 1). Traded players show one line per season (§6, item 4). It has no advanced metrics: it shows only what MLB's year-by-year split returns.
- **Projections.** These are measured, but only for players who were in the majors the previous season, and thin bands refuse. Nothing projects arbitration salaries, comparables, breakeven or a decline branch, because the project holds no salary data.
- **Ruleset comparison.** It works for 2022 and for user proposals. Comparing against `2027.json` refuses, by design, because that file is a placeholder.
- **Super Two.** The reference is filled for 2021–2025, but the validator fails four of five (§6, item 2). The 2026 cutoff is computed (2.127), but MLB has not published it.
- **Research.** Eight works were read in full, and the rest are cited or thinner (§5). The file's header and §9 still describe the state before the PDFs were read.
- **"Two calculators."** This was built as one page with two layers, not two pages. The simple layer answers *when*, not *how much he is worth* (§4).

---

## 4. Key decisions and why

- **The simple question is "when", not "how much he is worth".** Worth needs a price of a win and a performance record. The project stores neither and does not scrape the sources that publish them. The short answer says so on the page.
- **Projections come from measured careers, not assumed full seasons.** A full-season assumption gives the most optimistic date possible and presents it as the answer.
- **A thin band refuses rather than borrows.** Borrowing a neighbour's rate would hide thin data exactly on the long projections, which are the ones people read.
- **The site never tells anyone what to do.** A test covers the engine, the page and the comments. A phrase in a comment becomes a phrase in the UI after one refactor.
- **The stage panel explains mechanism and rights, not a dollar share.** The literature disagrees about size by an order of magnitude, and about sign on arbitration (§5). A range across that disagreement would look like a measurement.
- **The clock values an offer at signing (ex ante).** Whether a player "earned" his money is a different question, which only hindsight answers. The page names the method and uses the Jason Bay example from Krautmann (2013) to make the difference concrete.
- **The decline branch and comparables are absent, not approximated.** A breakeven built on invented salaries would be the most confident-looking and least defensible number on the site, aimed at the highest-stakes decision a player makes.
- **Guaranteed and non-guaranteed money stay separate.** A club option in the total would overstate an offer by the amount most likely to vanish.
- **No WAR anywhere.** MLB's Stats API does not publish WAR. The public WAR sources are Baseball-Reference and FanGraphs, and the project does not scrape either. This is a recorded project decision, and the stats panel says so.
- **No earnings.** They need salary data, and no salary source is permitted or machine-readable.
- **No likeness of any player, photo or cartoon.** The right of publicity attaches to identifiability, not to the medium. The reasoning is in `CLAUDE.md` under "Cartoon likenesses do NOT sidestep the photo problem". The safe visual alternatives are club colours, jersey number, position glyph, initials and the service-time meter.
- **The tooltip says "prediction" until MLB publishes.** A figure MLB has not announced is not described as one it has.
- **`config/cba/2027.json` stays a refusing placeholder.** Do not fill it by copying 2022. A ruleset that looks filled in but is guessed is worse than one that refuses.

---

## 5. Research: what the reading list established

File: `research/valuation-literature.md`. It was compiled on 2026-09-08 from search results and abstracts. The works the owner supplied as PDFs were read in full on 2026-09-10. Each entry carries a tag:

- `verified`: read in full.
- `cited`: title, authors, journal and year corroborated, but the paper itself not read.
- `single_source`: one source only, so the citation needs checking.
- `abstract_only`: a figure taken from an abstract or summary.
- `not_peer_reviewed`: industry or blog work. It must never ship wearing the site's authority.

**Read in full (`verified`):**

- Scully (1974), "Pay and Performance in Major League Baseball," *American Economic Review* 64(6). The issue number was corrected to 64(6).
- Krautmann (1999), "What's Wrong with Scully-Estimates of a Player's Marginal Revenue Product," *Economic Inquiry* 37(2). It applies both methods to the same players and gets a per-team surplus of $4.5M or $57M, depending on the method.
- Blair, Humphreys & Pyun (2017), "Monopsony Exploitation in Professional Sport: Evidence from Major League Baseball Position Players, 2000–2011," *Managerial and Decision Economics* 38(5). **This was read from the working paper** (WVU No. 15-48), not the published version. The MER definition is settled and Table 4 is transcribed. The arbitration journeyman is put at 25% of his value.
- Krautmann, Gustafson & Hadley (2000), "Who pays for minor league training costs?" *Contemporary Economic Policy* 18(1).
- Fair (2008), "Estimated Age Effects in Baseball," *Journal of Quantitative Analysis in Sports* 4(1). The peak ages are 27.6 (OPS), 28.3 (OBP) and 26.5 (ERA). The earlier "peak 28" was a rounding that hid the gap between pitchers and hitters.
- Solow & Krautmann (2020), "Do You Get What You Pay for?" *Journal of Sports Economics* 21(7). It sets out the worked ex ante method: performance at signing, aged forward, converted to revenue, discounted, and set against guaranteed salary.
- Fizel, Krautmann & Hadley (2002), "Equity and Arbitration in Major League Baseball," *Managerial and Decision Economics* 23(7).
- Krautmann (2013), "What Is Right With Scully Estimates of a Player's Marginal Revenue Product: Reply," *Journal of Sports Economics* 14(1). It is a Reply, not a standalone article. It is the source of the Jason Bay example and of the distinction between ex ante (what sets a salary) and ex post (whether it was earned).

**Cited, not read (`cited`):**

- **Walters, von Allmen & Krautmann (2017), *Atlantic Economic Journal* 45(3), on risk aversion and wages.** This matters: the recommendation that the site must not imply an extension is a discount (§8c of the file) rests on this paper, and nobody has read it. Nothing on the page makes that claim today. Read the paper before any offer-panel work that touches extensions.
- Krautmann, von Allmen & Berri (2009); Krautmann & Oppenheimer (2002, contract length); Krautmann & Solow (2009, performance dynamics); Burgess & Marburger (1993, negotiated versus arbitrated salaries); Bradbury (2009, aging); Hakes & Turner (2011, aging); Nguyen & Matthews (2024, aging).

**Single source, abstract only, or not peer reviewed:**

- `single_source`: Schuckers et al. (2023, aging imputation); Miller (2000, final-offer arbitration); a 2005 paper on arbitrator bias; Stiroh (2007) and Paulsen (2020), on selection.
- `abstract_only`: several figures, including the reserve-clause finding.
- `not_peer_reviewed`: dollars per win. The roughly $8M-per-win figure circulating for free agency traces to FanGraphs and Hardball Times work, not to a journal. Coverage of service-time manipulation sits in law reviews (student-edited) and in SABR and Baseball Prospectus.

**What the reading changed:**

- The share-of-value panel was dropped. The papers disagree in sign on arbitration, so any range would span a contradiction.
- The stage panel now states the order of the stages and the mechanism, and says that the size is unsettled, with the method named.
- Solow & Krautmann (2020) and Krautmann (2013) settle which method the tool performs: it values an offer at signing. The page says so.
- Fair's age curves led to the age-at-threshold column.
- **Stale parts of the file.** The header ("No primary source could be opened", "nothing in here is fit to publish") and §9 ("every figure above is `abstract_only` or thinner") describe the state before the PDFs were read. The per-entry tags are the current status. The header and §9 need updating.

---

## 6. Known bugs and open questions

Roughly in order of how much a reader could be misled.

1. **The Statistics tab is empty for retired players, and its message is wrong.** The empty-state copy says a record computed before collection "fills in on the next run." The daily job processes only the 40-man (`get_all_40man_players()`). That is true for current players. It is false for the rest: 4,249 of the 4,250 non-rostered records have an empty `season_stats`. Fix the copy now. Filling the records needs their bios re-fetched through the backfill. I did not check the exact invocation this session.

2. **The Super Two validator fails four of five published years.** `python3 scripts/validate_super_two.py` reports 1 passed, 4 failed, exit 1:
   - 2025: ours 2.137, published 2.140, −3 days
   - 2024: ours 2.135, published 2.132, +3 days
   - 2023: ours 2.130, published 2.118, +12 days
   - 2022: ours 2.135, published 2.128, +7 days
   - 2021: ours 2.116, published 2.116, exact

   The tolerance is 2 days. Whether that is the right bar, or the method is not precise enough to check at 2 days, is for the owner to decide. Do not tune the method toward the reference. The errors have no consistent sign, and a cutoff is a boundary between two adjacent players, so a small per-player error moves it by however far apart those two sit.

3. **Statlines are missing for accruing rostered seasons.** As of 2026-10-28, 669 of 6,307 accruing rostered seasons have no statline, so about 89% are covered. They span about 470 players. 202 of the gaps are 2026, and 447 are 2024 or later, so the gap grows toward the present.
   - **Minor-league moves do not explain it.** I tested this. 65% of the gap seasons had an option, assignment, outright or rehab transaction that year, against 68% of covered seasons. So the idea that a later minor-league split overwrites the MLB line is not supported by the transaction data.
   - **Some are probably real misses.** A full 172 days of credited time usually means games were played. Examples: Andrew Benintendi 2022, Braxton Garrett 2025, David Peterson 2026. An all-season injured-list stay would also show 172, so this is not proof.
   - **40 rostered players have no statline at all.** Most of them have never played a major league game (no debut date).
   - **Next step:** fetch one affected player's year-by-year response from the live API, and compare it with the stored record. Run that from an Action. Earlier sessions found that the sandbox has no route to the API.

4. **Traded players show one line per season, and the rule depends on API order.** `season_stats_from_bio()` keeps the *later* split when a player has two in one season. The stats note says this shows "the club he finished the year with." That holds only if the API lists splits in date order, which has not been checked against a live traded-player payload. The rule also drops the first club's counting stats, so a traded player's totals are understated. Counting stats could be summed across clubs. Rate stats cannot be combined without a combined line from the API. This is not decided.

5. **The offer panel's default rates are unsourced.** It starts at agent commission 4%, federal effective rate 35% and discount rate 5%. The panel says every rate is the user's, but a default is still a claim. 35% is nearer a top marginal rate than an effective rate for most salaries. A tax professional should review the defaults, or they should start blank.

6. **The Jason Bay example misattributes its projection.** The explainer says "Krautmann (2013) uses Jason Bay … against a projection of 3.2 wins above replacement." In the Reply, the 3.2 WARP projection comes from Baseball Prospectus. The page should name that source.

7. **The league minimum is documented, not read off the agreement.** The short answer prints "$780,000 … about the league minimum (2026)" for every remaining pre-arbitration season. The schedule rises each year (2022: $700,000; 2023: $720,000; 2024: $740,000; 2025: $760,000; 2026: $780,000), so later seasons are overstated. The config note for `salary.mlb_minimum` says it is "not consumed by any engine code yet." That is no longer true, because the short answer reads it. The note needs fixing.

8. **Profile shards have grown.** The 64 shards now total about 4.9 MB, up from the 3.4 MB recorded in `CLAUDE.md`, because statlines ride in them. They download when a profile opens, not on page load. Still, `CLAUDE.md` says a stats tab should have its own shard, fetched when the tab opens. Consider moving `season_stats` into a separate shard set.

9. **The 2026-27 Super Two cutoff is not announced.** MLB normally sets it in November. Ours computes 2.127. `CLAUDE.md` records MLBTR's estimate as 2.130 to 2.134, which was not re-checked this session. When MLB publishes, add a sixth row to `data/reference_super_two.json` and re-run the validator. That row will be the first one predicted before publication.

10. **The research file is stale, and one recommendation rests on an unread paper.** See §5: the header and §9 predate the PDFs, and the no-extension-discount recommendation rests on Walters et al. (2017), which is `cited`.

11. **Search Console has not been read.** The split sitemaps (`sitemap-alumni.xml`, `sitemap-players.xml`) decide whether the 1,756 held alumni pages get published and whether `/free-agency.html` gets built. That check was due 2026-09-22.

12. **The CBA expires 2026-12-01**, 34 days from this file's date. When a new agreement lands, fill `config/cba/2027.json` from the agreement's own text, not from 2022. Make it the default, then run the full recompute (`--recompute-all`, batched, per `CLAUDE.md` on the 8am gate) so the stored records are rebuilt under it. `accrual_model.json` rebuilds with them, and it reads the in-force credited-year length, so the projections move with the ruleset.

13. **The figures in this file are a snapshot.** Counts are from 2026-10-28 and will drift with each daily run.

---

## 7. Files that matter

| Path | What it does |
|---|---|
| `docs/contract.html` | Contract clock page: the short answer, then the five panels behind a toggle |
| `docs/contract.js` | Pure engine: clock, short answer, projections, stage, offers, rulesets. Loads under Node |
| `docs/contract-page.js` | UI: player picker, short answer, panels, toggle state |
| `docs/taxes.css` | Styles for the short answer, the lead panel and the ex ante note. The file is shared with the duty-day tool |
| `docs/data/accrual_model.json` | Measured next-season accrual by service band. The daily job rebuilds it (`_write_accrual_model`) |
| `docs/app.js` | Table, tiles and the Super Two tooltip (`superTwoSpread`), profile overlay, Statistics tab (`statsTable`, `renderStatsPanel`, `wireProfileTabs`) |
| `docs/styles.css` | `.site-nav`, `.nav-tag`, `.profile-tabs`, `.stats-table` |
| `scripts/fetch_mlb_data.py` | `HITTING_KEYS`, `PITCHING_KEYS`, `season_stats_from_bio()` |
| `scripts/update_service_time.py` | Writes `season_stats` (filtered to the 30 clubs), `birth_date`, the `birth_year` column, `mlb_minimum` in the rules block, `_published_super_two_history()` and `_write_accrual_model()` |
| `scripts/super_two.py` | Super Two cutoff, and `qualifying_season_complete` |
| `scripts/validate_super_two.py` | Compares computed cutoffs with the reference file. Currently fails four of five |
| `data/reference_super_two.json` | Published cutoffs for 2021–2025, with a source note per row |
| `config/cba/2022.json` | The in-force agreement, including `salary.mlb_minimum*` (documented, not read off the text) |
| `config/cba/2027.json` | Placeholder that refuses to compute. Do not fill it from 2022 |
| `scripts/write_player_pages.py` | `NAV_SECTIONS`, `_site_nav()` |
| `research/valuation-literature.md` | Reading list with tags. §8 is what changed the design. The header and §9 are stale |
| `tests/test_service_time.py` | 356 checks, including the prediction-wording test and the navigation test |
| `tests/contract.test.cjs` | 41 tests, including the advice lint |
| `CLAUDE.md` | Project handbook. Sections added this session: "The contract page audited", "The player profile vision", "Cartoon likenesses…", "A recorded SEO opportunity", and the Super Two sections |

---

## 8. Ideas discussed, not built

- **A free-agency page** (`/free-agency.html`). It would answer "when is [player] a free agent," a high-volume search. It is recorded, not built. Build one well-linked page, and only after the sitemap report.
- **Per-player free-agency pages.** Rejected for now. They would be thousands of thin pages, on the same pattern that left 782 URLs unindexed in September.
- **WAR columns on the main table** (current and career). WAR is not in MLB's API, and the public sources are not scraped (§4).
- **A current-year earnings column.** Blocked on salary data.
- **Arbitration projections, comparable players, breakeven and a decline branch** (the brief's Modules B and C). Blocked on licensed salary data. Nothing else is blocking them.
- **A "how much is he worth" calculator.** The page says it cannot answer this without a price of a win and a performance record. Any dollars-per-win figure must be labelled as an industry convention with a named source (§5).
- **A past-earnings tab and a future-value tab** (the owner's profile vision). Earnings are blocked on salary data. Future value is blocked because the records hold no performance or salary figures, so the ex ante method cannot start.
- **Two separate calculator pages.** Built as one page with two layers instead. Revisit this if the owner wants separate URLs.
- **Summing counting stats across clubs** for traded players (§6, item 4).
- **Nash bargaining for the offer panel.** Solow & Krautmann (2011) model a thin superstar market as a Nash bargain. It is a lead in the research file, not built.
- **Cartoon likenesses and player photos.** Both are rejected (§4).
- **Teammate links, or `noindex` on the thinnest player pages,** as fixes for the indexing problem. Discussed, not decided. See `CLAUDE.md`.

---

## 9. How to check the work

From the repo root:

- `python3 tests/test_service_time.py`: expect `356 passed, 0 failed`.
- `node --test tests/contract.test.cjs`: expect 41 passing.
- `node --test tests/duty_days.test.cjs`: expect 27 passing.
- `python3 scripts/validate_published.py`: expect the three published files to agree, and zero dead links.
- `python3 scripts/validate_super_two.py`: currently exits 1 (§6, item 2).

In a browser, serve `docs/` (`python3 -m http.server` from inside `docs/`), then open:

- `/contract.html`: the short answer, then "Show the full tool".
- `/#player/592450`: Aaron Judge's profile. Click "Statistics".
- The "Super Two track" tile: hover over it.

If Playwright's default Chromium is missing, launch it with `executablePath: '/opt/pw-browsers/chromium-1243/chrome-linux64/chrome'`.

The "Validate Service Time" workflow runs the same checks from Actions: rosters, reference, super-two, player, published and tests.

**Rules when editing:**

- Key anything per player by id, never by name.
- Run the advice lint after any copy change. It reads comments too.
- After changing a sentence that quotes a number, check the rendered output, not just the arithmetic. `CLAUDE.md` describes the positive-control test pattern.
- Check where a string is escaped before escaping it again. `renderStatTiles()` already escapes the whole title.
- Do not republish salary projections from any source. Do not scrape Baseball-Reference or FanGraphs.
