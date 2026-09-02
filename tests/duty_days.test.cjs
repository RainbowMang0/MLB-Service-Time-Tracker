/*
 * Tests for docs/duty-days.js.
 *
 * Run: node --test tests/duty_days.test.cjs
 * No npm, no package.json, no build step -- node's built-in test runner and
 * the engine's CommonJS export, matching the repo's existing rule that
 * `python tests/test_service_time.py` needs nothing installed either.
 */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const DD = require("../docs/duty-days.js");

const ROOT = path.resolve(__dirname, "..");
const RULES = JSON.parse(
  fs.readFileSync(path.join(ROOT, "config/tax/duty-day-rules.json"), "utf8")
);
const STATES = JSON.parse(
  fs.readFileSync(path.join(ROOT, "config/tax/2026-states.json"), "utf8")
);

function day(date, type, jurisdiction, confirmed = true) {
  return { date, type, jurisdiction, confirmed, source: "test" };
}

// -------------------------------------------------------------------------
// Dates
// -------------------------------------------------------------------------

test("dates step in UTC, so a day never shifts under a west-coast clock", () => {
  assert.equal(DD.addDays("2026-04-01", 1), "2026-04-02");
  assert.equal(DD.addDays("2026-03-01", -1), "2026-02-28");
  assert.equal(DD.addDays("2026-12-31", 1), "2027-01-01");
  assert.equal(DD.daysBetween("2026-04-01", "2026-04-30"), 29);
  assert.equal(DD.dateRange("2026-04-01", "2026-04-03").length, 3);
});

// -------------------------------------------------------------------------
// Counting
// -------------------------------------------------------------------------

test("offseason days are excluded from the denominator", () => {
  const days = [
    day("2026-04-01", "game_home", "NY"),
    day("2026-12-01", "offseason", null),
  ];
  const t = DD.dutyDayTotals(days, RULES);
  assert.equal(t.total, 1);
  assert.equal(t.excluded, 1);
});

test("an unknown day type is excluded AND reported, never silently counted", () => {
  const days = [day("2026-04-01", "game_home", "NY"), day("2026-04-02", "invented", "NY")];
  const t = DD.dutyDayTotals(days, RULES);
  assert.equal(t.total, 1);
  assert.deepEqual(t.unknownTypes, ["invented"]);
});

test("a day in a no-tax state still occupies the denominator", () => {
  // The subtle one, and the reason a Florida road trip is not free: those
  // days earn no Florida tax but they dilute every other state's share.
  const withFlorida = [
    ...Array.from({ length: 9 }, (_, i) => day(`2026-04-0${i + 1}`, "game_home", "NY")),
    day("2026-04-10", "game_road", "FL"),
  ];
  const result = DD.allocate(withFlorida, {
    allocableIncome: 1000000,
    rules: RULES,
    states: STATES,
  });
  const ny = result.rows.find((r) => r.jurisdiction === "NY");
  assert.equal(result.totalDutyDays, 10);
  assert.equal(ny.dutyDays, 9);
  assert.equal(ny.allocatedIncome, 900000); // not 1,000,000
});

// -------------------------------------------------------------------------
// Apportionment
// -------------------------------------------------------------------------

test("allocations sum EXACTLY to the salary they came from", () => {
  // Three states over seven days is the classic case where naive rounding
  // leaves a stray cent. A CPA document that does not reconcile gets queried.
  const parts = DD.apportion(1000000, [3, 2, 2], ["A", "B", "C"]);
  assert.equal(parts.reduce((a, b) => a + b, 0), 1000000);

  const awkward = DD.apportion(3333333.33, [1, 1, 1], ["A", "B", "C"]);
  assert.equal(Math.round(awkward.reduce((a, b) => a + b, 0) * 100) / 100, 3333333.33);
});

test("apportioning across zero duty days does not divide by zero", () => {
  assert.deepEqual(DD.apportion(500000, [], []), []);
  const result = DD.allocate([], { allocableIncome: 500000, rules: RULES, states: STATES });
  assert.equal(result.totalDutyDays, 0);
  assert.deepEqual(result.rows, []);
  assert.equal(result.totalAllocated, 0);
});

// -------------------------------------------------------------------------
// The refusal to guess -- the point of the whole design
// -------------------------------------------------------------------------

test("a state with no rate at all gets days and a share but NO liability", () => {
  // Idaho: nobody has entered a rate, from either tier. The allocation is
  // still complete and still useful -- it is the rate that is missing, not
  // the arithmetic.
  const days = [day("2026-04-01", "game_road", "ID")];
  const result = DD.allocate(days, {
    allocableIncome: 172000,
    rules: RULES,
    states: STATES,
  });
  const row = result.rows[0];
  assert.equal(row.dutyDays, 1);
  assert.equal(row.allocatedIncome, 172000);
  assert.equal(row.liability, null, "no liability without a rate");
  assert.equal(row.liabilityWithheldBecause, "no_rate_on_file");
  assert.equal(result.liabilityIsPartial, true);
});

test("a no-wage-tax state gets a real, confident zero", () => {
  const days = [day("2026-04-01", "game_home", "TX")];
  const result = DD.allocate(days, {
    allocableIncome: 172000,
    rules: RULES,
    states: STATES,
  });
  const tx = result.rows[0];
  assert.equal(tx.liability, 0);
  assert.equal(tx.liabilityBasis, "no_wage_income_tax");
  assert.equal(tx.liabilityWithheldBecause, null);
});

test("every rate on file declares which tier it came from", () => {
  // The invariant that keeps an unlabelled rate from reaching a user. A rate
  // may exist in one of exactly two states: verified (a person read it off
  // the jurisdiction's own guidance) or estimate_unverified (compiled from
  // secondary sources, and labelled that way everywhere it surfaces). There
  // is no third option, and in particular no rate with a bare "unverified".
  for (const [code, j] of Object.entries(STATES.jurisdictions)) {
    if (typeof j.rate_for_estimate === "number" && j.has_wage_income_tax !== false) {
      assert.ok(
        ["verified", "estimate_unverified"].includes(j.status),
        `${code} carries a rate but its status is ${j.status}`
      );
      assert.ok(j.source || j.status === "verified", `${code} has a rate but no source`);
    }
  }
});

test("an estimate-tier rate produces a number, flagged as an estimate", () => {
  // This is the thing the owner asked for: a real but incomplete answer,
  // rather than a blank. It must arrive labelled.
  const days = [day("2026-04-01", "game_road", "IL")];
  const result = DD.allocate(days, {
    allocableIncome: 100000,
    rules: RULES,
    states: STATES,
  });
  const il = result.rows[0];
  assert.equal(il.liability, 4950, "4.95% of the allocated salary");
  assert.equal(il.liabilityBasis, "estimated_rate");
  assert.equal(result.liabilityUsesEstimatedRates, true);
  assert.match(il.warnings.join(" "), /rough figure from secondary sources/);
});

test("a jurisdiction whose sources disagreed gets no number at all", () => {
  // Georgia: two summaries gave 5.19% and 5.49% the same day. Picking one
  // would be a coin flip presented as a figure.
  const days = [day("2026-04-01", "game_road", "GA")];
  const result = DD.allocate(days, {
    allocableIncome: 100000,
    rules: RULES,
    states: STATES,
  });
  assert.equal(result.rows[0].liability, null);
  assert.equal(result.rows[0].liabilityWithheldBecause, "conflicting_sources");
});

test("a no-tax state's zero is still a verified zero, not an estimate", () => {
  const result = DD.allocate([day("2026-04-01", "game_home", "TX")], {
    allocableIncome: 100000,
    rules: RULES,
    states: STATES,
  });
  assert.equal(result.rows[0].liabilityBasis, "no_wage_income_tax");
  assert.equal(result.liabilityUsesEstimatedRates, false);
});

test("a no-tax domicile means there is no credit to offset, and vice versa", () => {
  // Getting this backwards matters more than it looks: most players domicile
  // in Florida or Texas precisely because there is no state tax there, and
  // telling such a player his home state will credit him would be exactly
  // wrong. The engine exposes the fact; the UI picks the sentence.
  const fl = STATES.jurisdictions.FL;
  const mn = STATES.jurisdictions.MN;
  assert.equal(fl.has_wage_income_tax, false, "no credit is available from Florida");
  assert.equal(mn.has_wage_income_tax, true, "Minnesota does give a resident credit");
});

test("the total always declares it is before the resident credit", () => {
  // The biggest single reason the sum is not a tax bill: a domicile state
  // generally credits tax paid elsewhere, so adding per-state liabilities
  // double-counts. It is a property of the result so no caller can render
  // the total without having been told.
  const result = DD.allocate([day("2026-04-01", "game_road", "NY")], {
    allocableIncome: 100000,
    rules: RULES,
    states: STATES,
  });
  assert.equal(result.beforeResidentCredit, true);
});

test("New York uses the band a major league salary is actually in", () => {
  // 9.65%, not the 10.9% headline rate that does not begin until $25M.
  // Using the headline number would overstate essentially every player.
  const ny = STATES.jurisdictions.NY;
  assert.equal(ny.rate_for_estimate, 0.0965);
  assert.equal(ny.top_marginal_rate, 0.109);
  assert.ok(ny.rate_for_estimate < ny.top_marginal_rate);
});

test("a local-tax state warns even though it cannot compute the local tax", () => {
  const days = [day("2026-04-01", "game_road", "PA")];
  const result = DD.allocate(days, { allocableIncome: 1000, rules: RULES, states: STATES });
  const warnings = result.rows[0].warnings.join(" ");
  assert.match(warnings, /local or city income tax/);
  assert.match(warnings, /Philadelphia/);
});

test("Toronto is carried as a foreign jurisdiction, not dropped", () => {
  const days = [day("2026-06-01", "game_road", "CA-ON")];
  const result = DD.allocate(days, { allocableIncome: 1000, rules: RULES, states: STATES });
  assert.equal(result.rows[0].dutyDays, 1);
  assert.match(result.rows[0].warnings.join(" "), /outside the United States/);
});

// -------------------------------------------------------------------------
// Proposing a season
// -------------------------------------------------------------------------

test("proposed days are never marked confirmed", () => {
  // The brief: the app proposes, the player confirms, it never silently
  // assumes. If this ever regresses, the export would overstate its own
  // reliability.
  const days = DD.proposeSeasonDays({
    games: [
      { date: "2026-04-01", state: "NY", home: true },
      { date: "2026-04-04", state: "MA", home: false },
    ],
    homeState: "NY",
    seasonStart: "2026-04-01",
    seasonEnd: "2026-04-05",
  });
  assert.ok(days.length === 5);
  assert.ok(days.every((d) => d.confirmed === false));
  const totals = DD.dutyDayTotals(days, RULES);
  assert.equal(totals.unconfirmed, totals.total);
});

test("an off day inside a road trip is sourced to the road state", () => {
  const days = DD.proposeSeasonDays({
    games: [
      { date: "2026-05-01", state: "MA", home: false },
      { date: "2026-05-03", state: "MA", home: false },
    ],
    homeState: "NY",
    seasonStart: "2026-05-01",
    seasonEnd: "2026-05-03",
  });
  const middle = days.find((d) => d.date === "2026-05-02");
  assert.equal(middle.jurisdiction, "MA");
  assert.equal(middle.type, "off_day_in_state");
  assert.equal(middle.source, "proposed:mid_road_trip");
});

test("an off day between homestands stays at home", () => {
  const days = DD.proposeSeasonDays({
    games: [
      { date: "2026-05-01", state: "NY", home: true },
      { date: "2026-05-03", state: "NY", home: true },
    ],
    homeState: "NY",
    seasonStart: "2026-05-01",
    seasonEnd: "2026-05-03",
  });
  const middle = days.find((d) => d.date === "2026-05-02");
  assert.equal(middle.jurisdiction, "NY");
  assert.equal(middle.type, "off_day_home");
});

test("a doubleheader is two games but one duty day", () => {
  const days = DD.proposeSeasonDays({
    games: [
      { date: "2026-07-04", state: "OH", home: false },
      { date: "2026-07-04", state: "OH", home: false },
    ],
    homeState: "NY",
    seasonStart: "2026-07-04",
    seasonEnd: "2026-07-04",
  });
  assert.equal(days.length, 1);
});

test("spring training days are proposed in the spring state", () => {
  const days = DD.proposeSeasonDays({
    games: [],
    homeState: "OH",
    springState: "AZ",
    springStart: "2026-02-20",
    springEnd: "2026-02-22",
    seasonStart: "2026-03-26",
    seasonEnd: "2026-03-26",
  });
  const spring = days.filter((d) => d.type === "spring_training");
  assert.equal(spring.length, 3);
  assert.ok(spring.every((d) => d.jurisdiction === "AZ"));
});

// -------------------------------------------------------------------------
// Bulk edit
// -------------------------------------------------------------------------

test("a bulk edit rewrites only the stretch it names, and confirms it", () => {
  let days = DD.proposeSeasonDays({
    games: [],
    homeState: "NY",
    seasonStart: "2026-06-01",
    seasonEnd: "2026-06-10",
  });
  days = DD.applyStretch(days, {
    from: "2026-06-03",
    to: "2026-06-05",
    type: "injured_list_rehab",
    jurisdiction: "FL",
  });
  const touched = days.filter((d) => d.type === "injured_list_rehab");
  assert.equal(touched.length, 3);
  assert.ok(touched.every((d) => d.jurisdiction === "FL" && d.confirmed === true));
  assert.equal(days.find((d) => d.date === "2026-06-02").jurisdiction, "NY");
  assert.equal(days.find((d) => d.date === "2026-06-02").confirmed, false);
});

test("applyStretch does not mutate the array it was given", () => {
  const before = [day("2026-06-01", "game_home", "NY", false)];
  const after = DD.applyStretch(before, {
    from: "2026-06-01",
    to: "2026-06-01",
    type: "offseason",
  });
  assert.equal(before[0].type, "game_home");
  assert.equal(after[0].type, "offseason");
});

// -------------------------------------------------------------------------
// Saved state
// -------------------------------------------------------------------------

test("a saved season round-trips through JSON unchanged", () => {
  // This object is the export file, the localStorage value, and -- if this
  // ever gets accounts -- the database row. It has to survive a round trip.
  const season = DD.emptySeason(2026);
  season.days = [day("2026-04-01", "game_home", "NY")];
  season.profile.allocableIncome = 780000;
  const restored = DD.migrateSeason(JSON.parse(JSON.stringify(season)));
  assert.deepEqual(restored, season);
});

test("a season saved under an older schema is upgraded, not discarded", () => {
  const old = { season: 2025, days: [day("2025-04-01", "game_home", "NY")] };
  const migrated = DD.migrateSeason(old);
  assert.equal(migrated.schemaVersion, DD.SCHEMA_VERSION);
  assert.equal(migrated.days.length, 1);
  assert.ok(migrated.profile, "the upgrade fills in the missing profile block");
});

// -------------------------------------------------------------------------
// End to end
// -------------------------------------------------------------------------

test("a realistic split season reconciles and reports what it withheld", () => {
  const days = [
    ...DD.dateRange("2026-04-01", "2026-04-30").map((d) => day(d, "game_home", "TX")),
    ...DD.dateRange("2026-05-01", "2026-05-15").map((d) => day(d, "game_road", "NY")),
    ...DD.dateRange("2026-05-16", "2026-05-31").map((d) => day(d, "game_road", "PA")),
  ];
  const result = DD.allocate(days, {
    allocableIncome: 5000000,
    rules: RULES,
    states: STATES,
    domicile: "TX",
  });

  assert.equal(result.totalDutyDays, 61);
  assert.equal(result.totalAllocated, 5000000, "allocations reconcile to salary");

  const tx = result.rows.find((r) => r.jurisdiction === "TX");
  assert.equal(tx.liability, 0);
  assert.equal(tx.isDomicile, true);

  assert.equal(result.jurisdictionsEstimated, 3, "TX confidently, NY and PA on estimate rates");
  assert.equal(result.jurisdictionsWithheld, 0);
  assert.equal(result.liabilityUsesEstimatedRates, true, "so the total is a rough figure");
  assert.equal(result.beforeResidentCredit, true);
});
