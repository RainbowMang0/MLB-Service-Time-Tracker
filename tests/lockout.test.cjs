/*
 * Tests for docs/lockout.js -- the browser-side twin of
 * compute_status()/tile_value()/render_sentence() in
 * scripts/build_lockout_page.py.
 *
 * Run: node --test tests/lockout.test.cjs
 */

const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const LS = require("../docs/lockout.js");

const LOCKOUT_JS = path.resolve(__dirname, "..", "docs", "lockout.js");

const BASE_DATA = {
  cba_expires: "2026-12-01",
  cba_expires_at: "2026-12-01T23:59:00-05:00",
  lockout_start: null,
  lockout_end: null,
  regular_season_delayed_from: null,
};

function utc(...parts) {
  return new Date(Date.UTC(...parts));
}

// -------------------------------------------------------------------------
// Countdown / day-count math (mirrors tests/test_lockout.py)
// -------------------------------------------------------------------------

test("Nov 1 2026 is 30 days until the Dec 1 2026 expiry", () => {
  const status = LS.computeStatus(BASE_DATA, utc(2026, 10, 1, 16, 0, 0)); // noon ET
  assert.equal(status.kind, "countdown");
  assert.equal(status.day, 30);
  assert.equal(LS.tileValue(status), "30 days");
});

test("the lockout start date itself is day 1, inclusive", () => {
  const data = { ...BASE_DATA, lockout_start: "2026-12-02" };
  const status = LS.computeStatus(data, utc(2026, 11, 2, 16, 0, 0)); // noon ET, Dec 2
  assert.equal(status.kind, "lockout");
  assert.equal(status.day, 1);
});

test("day 9 of the lockout matches the Python fixture", () => {
  const data = { ...BASE_DATA, lockout_start: "2026-12-02" };
  const status = LS.computeStatus(data, utc(2026, 11, 10, 16, 0, 0)); // noon ET, Dec 10
  assert.equal(status.kind, "lockout");
  assert.equal(status.day, 9);
  assert.equal(LS.sentence(status, {}), "Day 9 of the lockout");
});

test("a future lockout_start does not clamp to Day 1 -- it is still the countdown", () => {
  const data = { ...BASE_DATA, lockout_start: "2026-12-05" };
  const status = LS.computeStatus(data, utc(2026, 10, 30, 16, 0, 0)); // noon ET, Nov 30
  assert.notEqual(status.kind, "lockout");
  assert.equal(status.kind, "countdown");
});

test("lockout_end freezes the day count", () => {
  const data = { ...BASE_DATA, lockout_start: "2026-12-02", lockout_end: "2027-03-01" };
  const status = LS.computeStatus(data, utc(2027, 5, 1, 16, 0, 0)); // well after lockout_end
  assert.equal(status.kind, "lockout_ended");
  assert.equal(status.day, 90); // Dec 2 2026 -> Mar 1 2027 inclusive
  assert.equal(
    LS.sentence(status, { lockoutEndLabel: "Mar. 1, 2027" }),
    "Lockout ended Mar. 1, 2027 after 90 days",
  );
});

test("days-at-stake appends to the sentence", () => {
  const data = {
    ...BASE_DATA,
    lockout_start: "2026-12-02",
    regular_season_delayed_from: "2027-03-26",
  };
  const status = LS.computeStatus(data, utc(2027, 3, 3, 16, 0, 0)); // noon ET, Apr 3
  assert.equal(status.daysAtStake, 9);
  assert.match(LS.sentence(status, {}), /9 service-time days at stake so far$/);
});

// -------------------------------------------------------------------------
// The expiry-instant boundary: 11:59 p.m. ET on Dec 1 2026 is
// 2026-12-02T04:59:00Z, not midnight UTC and not the visitor's local
// midnight. See scripts/build_lockout_page.py, cba_expires_instant().
// -------------------------------------------------------------------------

test("Nov 30, noon ET: still counting down, 1 day until", () => {
  const status = LS.computeStatus(BASE_DATA, utc(2026, 10, 30, 17, 0, 0)); // Nov 30 noon ET
  assert.equal(status.kind, "countdown");
  assert.equal(status.day, 1);
});

test("Dec 1, 12:00 am ET: expires today, not already expired", () => {
  const status = LS.computeStatus(BASE_DATA, utc(2026, 11, 1, 5, 0, 0)); // Dec 1 00:00 ET
  assert.equal(status.kind, "expiring_today");
  assert.equal(LS.sentence(status, {}), "The CBA expires tonight at 11:59 p.m. ET");
});

test("Dec 1, 11:58 pm ET: still expiring today, one minute left", () => {
  const status = LS.computeStatus(BASE_DATA, utc(2026, 11, 2, 4, 58, 0)); // Dec 1 23:58 ET
  assert.equal(status.kind, "expiring_today");
});

test("Dec 2, 12:01 am ET: expired, no lockout on record", () => {
  const status = LS.computeStatus(BASE_DATA, utc(2026, 11, 2, 5, 1, 0)); // Dec 2 00:01 ET
  assert.equal(status.kind, "expired_no_lockout");
  assert.match(LS.sentence(status, { cbaExpiresLabel: "Dec. 1, 2026" }), /^The CBA expired at 11:59 p\.m\. ET on Dec\. 1, 2026\./);
  assert.ok(!LS.sentence(status, { cbaExpiresLabel: "Dec. 1, 2026" }).includes("in this file"));
});

// -------------------------------------------------------------------------
// Timezone independence: a visitor's own device timezone must never change
// the result. Each instant above is re-run in a fresh node process with
// TZ forced to Tokyo and Los Angeles, and the JSON must come back identical
// to the UTC run -- proving the ET calendar comes from Intl's explicit
// timeZone option, never the host's local clock.
// -------------------------------------------------------------------------

function computeInSubprocess(tz, isoInstant) {
  const script = `
    const LS = require(${JSON.stringify(LOCKOUT_JS)});
    const data = ${JSON.stringify(BASE_DATA)};
    const now = new Date(${JSON.stringify(isoInstant)});
    process.stdout.write(JSON.stringify(LS.computeStatus(data, now)));
  `;
  const out = execFileSync(process.execPath, ["-e", script], {
    env: { ...process.env, TZ: tz },
    encoding: "utf8",
  });
  return JSON.parse(out);
}

test("a visitor in Tokyo sees the same status as a visitor in New York", () => {
  const instants = [
    "2026-11-30T17:00:00Z", // Nov 30 noon ET / Dec 1 2am Tokyo
    "2026-12-01T05:00:00Z", // Dec 1 midnight ET / Dec 1 2pm Tokyo
    "2026-12-02T04:58:00Z", // Dec 1 23:58 ET / Dec 2 1:58pm Tokyo
    "2026-12-02T05:01:00Z", // Dec 2 00:01 ET / Dec 2 2:01pm Tokyo
  ];
  for (const instant of instants) {
    const utcResult = computeInSubprocess("UTC", instant);
    const tokyoResult = computeInSubprocess("Asia/Tokyo", instant);
    assert.deepEqual(tokyoResult, utcResult, `Tokyo disagreed with UTC at ${instant}`);
  }
});

test("a visitor in Los Angeles sees the same status as a visitor in New York", () => {
  const instants = [
    "2026-11-30T17:00:00Z",
    "2026-12-01T05:00:00Z",
    "2026-12-02T04:58:00Z",
    "2026-12-02T05:01:00Z",
  ];
  for (const instant of instants) {
    const utcResult = computeInSubprocess("UTC", instant);
    const laResult = computeInSubprocess("America/Los_Angeles", instant);
    assert.deepEqual(laResult, utcResult, `Los Angeles disagreed with UTC at ${instant}`);
  }
});

// -------------------------------------------------------------------------
// tileValue() never repeats the full sentence -- see QA PR #24 finding #1.
// -------------------------------------------------------------------------

test("the tile value is short; the sentence is the long form", () => {
  const status = LS.computeStatus(BASE_DATA, utc(2026, 10, 1, 16, 0, 0));
  const tile = LS.tileValue(status);
  const sentence = LS.sentence(status, { cbaExpiresLabel: "Dec. 1, 2026" });
  assert.equal(tile, "30 days");
  assert.notEqual(tile, sentence);
  assert.ok(sentence.length > tile.length);
});

// -------------------------------------------------------------------------
// matchesFilter -- the topic filter's one pure decision: which entries
// stay visible for a given selected filter value. The inline DOM-wiring
// script in docs/lockout.html is the only caller; this is the part of it
// that's actually worth unit testing without a browser.
// -------------------------------------------------------------------------

test("'all' matches every category, including an empty/unknown one", () => {
  assert.equal(LS.matchesFilter("salary_cap", "all"), true);
  assert.equal(LS.matchesFilter("other", "all"), true);
  assert.equal(LS.matchesFilter("", "all"), true);
  assert.equal(LS.matchesFilter(null, "all"), true);
});

test("a specific filter only matches its own exact category", () => {
  assert.equal(LS.matchesFilter("salary_cap", "salary_cap"), true);
  assert.equal(LS.matchesFilter("negotiations", "salary_cap"), false);
  assert.equal(LS.matchesFilter("other", "salary_cap"), false);
});

test("'other' as a filter matches entries filed under other, not every non-match", () => {
  assert.equal(LS.matchesFilter("other", "other"), true);
  assert.equal(LS.matchesFilter("players_union", "other"), false);
});
