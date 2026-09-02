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

test("an unverified state gets days and a share but NO liability", () => {
  const days = [day("2026-04-01", "game_road", "PA")];
  const result = DD.allocate(days, {
    allocableIncome: 172000,
    rules: RULES,
    states: STATES,
  });
  const pa = result.rows[0];
  assert.equal(pa.dutyDays, 1);
  assert.equal(pa.allocatedIncome, 172000);
  assert.equal(pa.liability, null, "no liability from an unverified rate");
  assert.equal(pa.liabilityWithheldBecause, "rules_unverified");
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

test("every jurisdiction with a rate on file is one somebody verified", () => {
  // The invariant that keeps a guessed rate from ever reaching a user: if a
  // rate exists, its status must say a human established it.
  for (const [code, j] of Object.entries(STATES.jurisdictions)) {
    if (typeof j.top_marginal_rate === "number") {
      assert.equal(
        j.status,
        "verified",
        `${code} carries a rate but is not marked verified`
      );
    }
  }
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

  assert.equal(result.jurisdictionsEstimated, 1, "only Texas can be estimated");
  assert.equal(result.jurisdictionsWithheld, 2, "NY and PA are unverified");
  assert.equal(result.liabilityIsPartial, true);
});
