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

  const files = [
    "docs/contract.js",
    "docs/contract.html",
    "docs/contract.js",
  ].filter((f) => fs.existsSync(path.join(ROOT, f)));

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
