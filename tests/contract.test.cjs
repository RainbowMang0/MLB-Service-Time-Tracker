/*
 * Tests for docs/contract.js.
 *
 * Run: node --test tests/contract.test.cjs
 */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const CT = require("../docs/contract.js");

const ROOT = path.resolve(__dirname, "..");
const CBA = JSON.parse(fs.readFileSync(path.join(ROOT, "config/cba/2022.json"), "utf8"));
const MODEL = JSON.parse(
  fs.readFileSync(path.join(ROOT, "docs/data/accrual_model.json"), "utf8")
);

// The shape the browser gets from index.json's `rules` block.
const RULES = {
  version: CBA.version,
  usable: true,
  full_year_days: CBA.service_time.days_per_credited_year,
  free_agency_years: CBA.free_agency.credited_years_required,
  arbitration_years: CBA.arbitration.standard_years_required,
};

// -------------------------------------------------------------------------
// The clock
// -------------------------------------------------------------------------

test("service formatting matches the Y.DDD notation the rest of the site uses", () => {
  assert.equal(CT.formatService(0, RULES), "0.000");
  assert.equal(CT.formatService(172, RULES), "1.000");
  assert.equal(CT.formatService(1256, RULES), "7.052"); // Bo Bichette, live figure
});

test("the clock reports days to each threshold as a fact, not a range", () => {
  // 2.100 -- inside the Super Two band, short of arbitration.
  const c = CT.clock(2 * 172 + 100, RULES);
  assert.equal(c.service, "2.100");
  const arb = c.marks.find((m) => m.key === "arbitration");
  assert.equal(arb.reached, false);
  assert.equal(arb.daysRemaining, 72, "3.000 is 72 days away");
  const fa = c.marks.find((m) => m.key === "free_agency");
  assert.equal(fa.daysRemaining, 6 * 172 - (2 * 172 + 100));
  assert.equal(c.next.key, "arbitration", "the nearer threshold is the next one");
});

test("a threshold already passed reports zero remaining, never negative", () => {
  const c = CT.clock(7 * 172, RULES);
  assert.ok(c.marks.every((m) => m.reached && m.daysRemaining === 0));
  assert.equal(c.next, null);
});

test("every threshold comes from the ruleset, not from a constant", () => {
  const altered = { ...RULES, free_agency_years: 5 };
  const standard = CT.clock(4 * 172, RULES);
  const alt = CT.clock(4 * 172, altered);
  const faStandard = standard.marks.find((m) => m.key === "free_agency");
  const faAlt = alt.marks.find((m) => m.key === "free_agency");
  assert.equal(faStandard.daysRemaining, 2 * 172);
  assert.equal(faAlt.daysRemaining, 172, "a 5-year ruleset moves the threshold");
});

// -------------------------------------------------------------------------
// Projection
// -------------------------------------------------------------------------

test("a projection is always a range, never one date", () => {
  // §3.3: anything covering more than one season renders as a distribution.
  const p = CT.project(172, MODEL, RULES, 2026);
  assert.equal(p.available, true);
  const fa = p.targets.find((t) => t.key === "free_agency");
  assert.equal(fa.outcomes.length, 3);
  assert.deepEqual(
    fa.outcomes.map((o) => o.key),
    ["p20", "p50", "p80"]
  );
});

test("a slower accrual rate never reaches a threshold sooner", () => {
  const p = CT.project(172, MODEL, RULES, 2026);
  const fa = p.targets.find((t) => t.key === "free_agency");
  const [slow, mid, fast] = fa.outcomes.map((o) => o.seasons);
  assert.ok(slow >= mid, "p20 cannot be quicker than p50");
  assert.ok(mid >= fast, "p50 cannot be quicker than p80");
});

test("a season is capped at the credited maximum however fast the rate", () => {
  // 200 days in a season still credits 172, so it cannot buy a shortcut.
  assert.equal(CT.seasonsToReach(0, 172 * 3, 200, RULES), 3);
  assert.equal(CT.seasonsToReach(0, 172 * 3, 172, RULES), 3);
});

test("a zero accrual rate reports 'never', not a huge number", () => {
  // A band whose p20 is 0 must not render as reaching free agency in year 99.
  assert.equal(CT.seasonsToReach(0, 172, 0, RULES), null);
});

test("a player already past a threshold needs zero further seasons", () => {
  assert.equal(CT.seasonsToReach(6 * 172, 6 * 172, 100, RULES), 0);
});

test("the accrual model is measured, and says how many careers it rests on", () => {
  // The distribution has to be defensible as evidence, not a prior. If the
  // sample ever collapses, that is a data bug worth failing on.
  assert.ok(MODEL.transitions > 10000, "measured over a real population");
  for (const band of MODEL.bands) {
    assert.ok(typeof band.sample === "number");
    if (band.enough_data) {
      assert.ok(band.p20 <= band.p50 && band.p50 <= band.p80, `${band.label} ordered`);
      assert.ok(band.p80 <= MODEL.full_year_days, `${band.label} cannot exceed the cap`);
    }
  }
});

test("a band with too little data refuses to project rather than guessing", () => {
  const thin = {
    ...MODEL,
    bands: MODEL.bands.map((b) => ({ ...b, enough_data: false })),
  };
  const p = CT.project(172, thin, RULES, 2026);
  assert.equal(p.available, false);
  assert.equal(p.reason, "band_sample_too_small");
});

// -------------------------------------------------------------------------
// Present value
// -------------------------------------------------------------------------

test("deferred money is worth less, and the discount is visible per year", () => {
  const pv = CT.presentValue(
    [
      { year: 2026, amount: 1000000 },
      { year: 2036, amount: 1000000 },
    ],
    0.05,
    2026
  );
  assert.equal(pv.nominal, 2000000);
  assert.ok(pv.presentValue < pv.nominal, "ten years out is worth less today");
  assert.equal(pv.detail[0].presentValue, 1000000, "this year is undiscounted");
  assert.ok(Math.abs(pv.detail[1].presentValue - 613913.25) < 1, "1.05^-10");
});

test("a zero discount rate leaves the nominal figure untouched", () => {
  const pv = CT.presentValue([{ year: 2030, amount: 5000000 }], 0, 2026);
  assert.equal(pv.presentValue, 5000000);
});

// -------------------------------------------------------------------------
// Deductions
// -------------------------------------------------------------------------

test("gross to net is itemised, and every rate is declared a user input", () => {
  const n = CT.netOf(1000000, {
    agentPct: 0.04,
    federalEffectiveRate: 0.35,
    stateTax: 50000,
    duesAndOther: 10000,
  });
  assert.equal(n.items.find((i) => i.key === "agent").amount, 40000);
  assert.equal(n.items.find((i) => i.key === "federal").amount, 350000);
  assert.equal(n.totalDeductions, 450000);
  assert.equal(n.net, 550000);
  assert.equal(n.allRatesAreUserInputs, true);
  assert.ok(n.items.every((i) => i.isInput), "no rate here is the site's own");
});

// -------------------------------------------------------------------------
// Valuing an offer
// -------------------------------------------------------------------------

test("an offer reports guaranteed and non-guaranteed money separately", () => {
  const v = CT.valueOffer(
    {
      years: [
        { year: 2027, amount: 5000000 },
        { year: 2028, amount: 5000000 },
        { year: 2029, amount: 20000000, guaranteed: false, note: "club option" },
      ],
    },
    { baseYear: 2026, discountRate: 0.05 }
  );
  assert.equal(v.nominalTotal, 30000000);
  assert.equal(v.nominalGuaranteed, 10000000, "a club option is not guaranteed");
  assert.equal(v.nonGuaranteed, 20000000);
  assert.ok(v.presentValueGross < v.nominalGuaranteed, "future years discount");
});

test("an offer result states that the decline branch is NOT modelled", () => {
  // The single most important flag in this file. Without it a caller could
  // present an accept-side figure as though it were a comparison.
  const v = CT.valueOffer({ years: [{ year: 2027, amount: 1000000 }] }, { baseYear: 2026 });
  assert.equal(v.declineModelled, false);
  assert.equal(v.deductionsAreProportionalApproximation, true);
});

test("net present value sits below gross present value once deductions apply", () => {
  const v = CT.valueOffer(
    { years: [{ year: 2027, amount: 10000000 }, { year: 2028, amount: 10000000 }] },
    {
      baseYear: 2026,
      discountRate: 0.05,
      agentPct: 0.04,
      federalEffectiveRate: 0.35,
      stateEffectiveRate: 0.05,
    }
  );
  assert.ok(v.presentValueNet < v.presentValueGross);
  assert.ok(v.presentValueNet > 0);
});

// -------------------------------------------------------------------------
// Ruleset comparison
// -------------------------------------------------------------------------

test("two agreements are compared side by side with an explicit delta", () => {
  const proposed = { ...RULES, version: "proposed", free_agency_years: 7 };
  const cmp = CT.compareRulesets(3 * 172, RULES, proposed, MODEL, 2026);
  assert.equal(cmp.available, true);
  const fa = cmp.deltas.find((d) => d.key === "free_agency");
  assert.equal(fa.a.atService, "6.000");
  assert.equal(fa.b.atService, "7.000");
  assert.equal(fa.daysDelta, 172, "a seventh year puts free agency 172 days further out");
});

test("comparing against a placeholder ruleset refuses rather than returning a delta", () => {
  // config/cba/2027.json is empty until an agreement lands. A delta against
  // nulls would be a large and entirely meaningless number.
  const placeholder = { version: "2027", usable: false };
  const cmp = CT.compareRulesets(3 * 172, RULES, placeholder, MODEL, 2026);
  assert.equal(cmp.available, false);
  assert.match(cmp.reason, /placeholder/);
});

// -------------------------------------------------------------------------
// Copy discipline -- §3.4, enforced mechanically
// -------------------------------------------------------------------------

test("no file in the contract module tells the reader what to do", () => {
  // The brief's hard line: the tool reports what the numbers say and never
  // says "take the deal", "decline", "you should" or "we recommend". Copy
  // review is easy to promise and easy to forget, so it is a test.
  //
  // Matched as whole words against user-facing files. Comments count too --
  // a phrase in a comment today is a phrase in the UI after one refactor.
  const BANNED_ADVICE_WORDS = [
    "you should",
    "we recommend",
    "recommended",
    "take the deal",
    "take this deal",
    "turn it down",
    "walk away",
    "best option",
    "better option",
    "good deal",
    "bad deal",
    "worth it",
    "advise",
    "advisable",
    "our advice",
    "you ought",
    "you'd be better",
  ];

  // contract-page.js is the file that writes almost every user-facing
  // sentence on the page, and it was missing from this list while its own
  // header comment claimed to be grepped by it -- docs/contract.js was
  // listed twice instead. A lint that does not read the UI is not a lint.
  const files = [
    "docs/contract.js",
    "docs/contract-page.js",
    "docs/contract.html",
  ];
  for (const f of files) {
    assert.ok(fs.existsSync(path.join(ROOT, f)), `${f} must exist to be linted`);
  }

  const hits = [];
  for (const rel of files) {
    const text = fs.readFileSync(path.join(ROOT, rel), "utf8").toLowerCase();
    for (const phrase of BANNED_ADVICE_WORDS) {
      let i = text.indexOf(phrase);
      while (i !== -1) {
        // "not tax advice" / "financial advice" are disclaimers, not advice.
        const context = text.slice(Math.max(0, i - 40), i + phrase.length + 20);
        if (!/not .{0,20}advice|advice\b.{0,20}(is|are) not/.test(context)) {
          hits.push(`${rel}: "${phrase}" in "...${context.trim()}..."`);
        }
        i = text.indexOf(phrase, i + 1);
      }
    }
  }
  assert.deepEqual(hits, [], "advice vocabulary found in the contract module");
});

// -------------------------------------------------------------------------
// The projection walk re-bands
// -------------------------------------------------------------------------

test("the projection re-reads the band each season instead of holding the first", () => {
  // The measured rate rises with service -- p20 is 32 days in the 0-1 band and
  // 172 in the 5-6 band -- so holding a rookie's slowest season across a whole
  // career compounds an artefact. Measured against this model, a 0.086 player
  // projected to free agency came out at 30 seasons held and 12 re-banded.
  const rookie = 86;
  const fa = 6 * RULES.full_year_days;
  const band0 = MODEL.bands.find((b) => b.band === 0);

  const held = CT.seasonsToReach(rookie, fa, band0.p20, RULES);
  const banded = CT.seasonsToReachBanded(rookie, fa, MODEL, "p20", RULES);

  assert.ok(banded < held, "re-banding cannot be slower than holding the worst band");
  assert.ok(banded <= 15, `a projected career of ${banded} seasons is not a career`);

  const p = CT.project(rookie, MODEL, RULES, 2026);
  const row = p.targets.find((t) => t.key === "free_agency");
  assert.equal(row.outcomes.find((o) => o.key === "p20").seasons, banded);
});

test("re-banding preserves the ordering the panel's column headings promise", () => {
  for (const days of [0, 86, 172, 400, 900]) {
    const p = CT.project(days, MODEL, RULES, 2026);
    for (const t of p.targets) {
      const [slow, mid, fast] = t.outcomes.map((o) => o.seasons);
      if (slow === null || mid === null || fast === null) continue;
      assert.ok(slow >= mid, `${t.key} at ${days}: p20 cannot beat p50`);
      assert.ok(mid >= fast, `${t.key} at ${days}: p50 cannot beat p80`);
    }
  }
});

test("a thin band stops the walk rather than borrowing a neighbour's rate", () => {
  // MIN_BAND_SAMPLE exists so a band with too few careers refuses. Walking
  // through it on someone else's number would defeat that on the long paths,
  // which are exactly the ones that cross the most bands.
  const thin = {
    ...MODEL,
    bands: MODEL.bands.map((b) => (b.band === 3 ? { ...b, enough_data: false } : b)),
  };
  assert.equal(CT.seasonsToReachBanded(0, 6 * RULES.full_year_days, thin, "p50", RULES), null);
});

test("the flat walk is still exact, because the ruleset comparison relies on it", () => {
  assert.equal(CT.seasonsToReach(0, 172 * 3, 200, RULES), 3);
  assert.equal(CT.seasonsToReach(0, 172, 0, RULES), null);
  assert.equal(CT.seasonsToReach(6 * 172, 6 * 172, 100, RULES), 0);
});

test("a projection reports the whole measured population, not one band's slice", () => {
  const p = CT.project(86, MODEL, RULES, 2026);
  assert.equal(p.model.transitions, MODEL.transitions);
  assert.ok(p.model.transitions > p.band.sample, "the walk crosses bands");
});

// -------------------------------------------------------------------------
// The player picker must never be keyed by name
// -------------------------------------------------------------------------

test("the contract page resolves a player without keying on his name", () => {
  // "Never key this dataset by name" is one of the project's oldest rules --
  // two Logan Allens, two Luis Perdomos, and 36 duplicated names in all. Two
  // players called Max Muncy are on 40-man rosters right now, at 289 days and
  // 1,741 days, so a name lookup on this page was an eight-and-a-half-year
  // error inside a contract decision.
  const src = fs.readFileSync(path.join(ROOT, "docs/contract-page.js"), "utf8");
  assert.ok(
    !/PLAYERS\.find\(\s*\(\s*p\s*\)\s*=>\s*p\.name\s*===/.test(src),
    "a bare name lookup is back in contract-page.js"
  );
  assert.ok(src.includes("LABEL_TO_PLAYER"), "the datalist label map is gone");
});

test("the published index still contains the duplicate names this guards against", () => {
  // If this ever fails the dataset changed, not the code -- but the guard
  // above would then be resting on an assumption nobody rechecked.
  const index = JSON.parse(
    fs.readFileSync(path.join(ROOT, "docs/data/index.json"), "utf8")
  );
  const ix = (n) => index.fields.indexOf(n);
  const rostered = index.players.filter((r) => r[ix("on_40_man")] === 1);
  const seen = new Map();
  for (const r of rostered) {
    const n = r[ix("name")];
    seen.set(n, (seen.get(n) || 0) + 1);
  }
  const dupes = [...seen].filter(([, c]) => c > 1);
  assert.ok(dupes.length > 0, "expected at least one duplicated name on a 40-man");

  // And the label the page builds for them must separate them.
  const labels = new Set(
    rostered.map((r) => `${r[ix("name")]} · ${index.teams[r[ix("team")]] || ""}`)
  );
  assert.equal(labels.size, rostered.length, "name-plus-club must be unique, or the id kicks in");
});

// -------------------------------------------------------------------------
// Reachability
// -------------------------------------------------------------------------

test("the homepage deliberately does not yet link the contract or duty-day tools", () => {
  // Reverted 2026-09-08 at the owner's direction: neither tool is finished,
  // and the contract tool in particular is missing the valuation work this
  // is now being researched for. Linking from the homepage is the one
  // hard-to-undo direction -- it teaches a crawler these URLs matter, and a
  // young site should not point its only inbound authority at a tool it is
  // about to change. Flip this test when the tools are ready.
  const home = fs.readFileSync(path.join(ROOT, "docs/index.html"), "utf8");
  assert.ok(!/href="contract\.html"/.test(home), "homepage links the contract tool before it is ready");
  assert.ok(!/href="taxes\.html"/.test(home), "homepage links the duty-day tool before it is ready");
});

// -------------------------------------------------------------------------
// Career stage
// -------------------------------------------------------------------------

test("the stage reports the mechanism and never a share of value", () => {
  // Published estimates of what a restricted player captures run from about
  // a tenth of his value to essentially all of it, and Krautmann (1999) shows
  // most of that spread is method: the same players come to $4.5M or $57M of
  // surplus per team depending only on the approach. A range spanning that
  // would read as a measurement. So the engine returns what is uncontested --
  // the bargaining rights that change at each line -- and no number.
  const R = { ...RULES };
  for (const days of [0, 300, 516, 900, 1032, 1500]) {
    const st = CT.stageOf(days, R, false);
    const text = JSON.stringify(st);
    assert.ok(st.rights && st.rights.length > 20, "every stage explains the mechanism");
    assert.ok(
      !/\b\d{1,3}\s?%|\bpercent\b/i.test(text),
      `stage at ${days} days quotes a share of value: ${text}`
    );
  }
});

test("stage boundaries follow the ruleset, including Super Two", () => {
  const R = RULES;
  assert.equal(CT.stageOf(0, R, false).key, "pre_arbitration");
  assert.equal(CT.stageOf(3 * 172 - 1, R, false).key, "pre_arbitration");
  assert.equal(CT.stageOf(3 * 172, R, false).key, "arbitration");
  assert.equal(CT.stageOf(6 * 172 - 1, R, false).key, "arbitration");
  assert.equal(CT.stageOf(6 * 172, R, false).key, "free_agency");

  // Super Two reaches arbitration early, and the stage says so -- the one
  // case where the stage and the raw figure disagree.
  const early = CT.stageOf(2 * 172 + 140, R, true);
  assert.equal(early.key, "arbitration");
  assert.equal(early.viaSuperTwo, true);
  assert.equal(CT.stageOf(4 * 172, R, true).viaSuperTwo, false, "not via Super Two once past 3.000");
});

test("a five-year ruleset moves the stage boundary with it", () => {
  const alt = { ...RULES, free_agency_years: 5 };
  assert.equal(CT.stageOf(5 * 172, RULES, false).key, "arbitration");
  assert.equal(CT.stageOf(5 * 172, alt, false).key, "free_agency");
});

// -------------------------------------------------------------------------
// Age at a threshold
// -------------------------------------------------------------------------

test("age is attached to projected crossings, and refuses when unknown", () => {
  // A player reaching free agency at 27 and one reaching it at 32 have
  // identical clocks and very different futures. Fair (2008) puts peak
  // performance at 26.5-28.3 depending on the measure, so the age at which a
  // player arrives is the part the service clock cannot say.
  const p = CT.withAges(CT.project(400, MODEL, RULES, 2026), 1999);
  const fa = p.targets.find((t) => t.key === "free_agency");
  for (const o of fa.outcomes) {
    if (o.season === null) continue;
    assert.equal(o.age, o.season - 1999, "age tracks the projected season");
  }

  // Unknown birth year must render as unknown, never as an age computed from
  // a missing field -- the same discipline as a null tax rate.
  const unknown = CT.withAges(CT.project(400, MODEL, RULES, 2026), null);
  assert.ok(unknown.targets.every((t) => t.outcomes.every((o) => o.age === null)));
  assert.equal(unknown.birthYear, null);
});

test("an implausible birth year yields no age rather than a wrong one", () => {
  assert.equal(CT.ageInSeason(1890, 2026), null);
  assert.equal(CT.ageInSeason(2020, 2026), null);
  assert.equal(CT.ageInSeason(null, 2026), null);
  assert.equal(CT.ageInSeason(1999, 2026), 27);
});

test("attaching ages cannot change a projected date", () => {
  // withAges is a presentational join. If it could move a season it would be
  // silently editing a measured projection.
  const base = CT.project(400, MODEL, RULES, 2026);
  const aged = CT.withAges(base, 1996);
  assert.deepEqual(
    aged.targets.map((t) => t.outcomes.map((o) => o.season)),
    base.targets.map((t) => t.outcomes.map((o) => o.season))
  );
});
