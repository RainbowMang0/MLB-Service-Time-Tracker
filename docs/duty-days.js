/*
 * duty-days.js -- the duty-day allocation engine.
 *
 * WHAT THIS IS
 * ============
 * Pure functions. No DOM, no fetch, no storage, no globals read at call time.
 * Give it a list of days and a salary, get back an allocation. That is the
 * whole contract, and it is deliberate:
 *
 *   - It runs in the browser today, which is why this tool needs no server,
 *     no database and no account. Nothing a player types here leaves his
 *     device.
 *   - It runs unchanged under Node, which is how it is tested
 *     (`node --test tests/duty_days.test.cjs`, no npm, no build step), and
 *     is what keeps a server-side version cheap if this is ever hosted.
 *
 * WHY THIS IS JAVASCRIPT WHEN THE REST OF THE PIPELINE IS PYTHON
 * =============================================================
 * The service-time engine is Python because it runs at build time in a
 * GitHub Action. This engine runs per-user at request time, in the browser,
 * on numbers the user typed. There is no Python available there. It is not a
 * second copy of anything -- no Python duty-day engine exists, and none
 * should be written. One domain, one implementation.
 *
 * THE FORMULA
 * ===========
 *   (duty days in jurisdiction / total duty days) * allocable income
 *
 * The denominator is total duty days for the year, so misclassifying a day
 * anywhere moves every jurisdiction's number, not just one. That is also why
 * a day in Florida still matters: it earns no Florida tax, but it sits in the
 * denominator and lowers everyone else's share.
 *
 * WHAT IT WILL NOT DO
 * ===================
 * Produce a dollar liability for a jurisdiction whose rules are marked
 * unverified in config/tax/2026-states.json. It will still count days and
 * compute the allocation percentage -- the laborious, rate-independent part
 * that a preparer actually wants -- and report the liability as null with a
 * reason. A missing number is safe; a wrong one is not.
 */

(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api; // Node: `node --test`, and any future server use
  } else {
    root.DutyDays = api; // Browser: plain <script src>, no module type needed
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const SCHEMA_VERSION = 1;

  // ---------------------------------------------------------------------
  // Dates. Everything is an ISO "YYYY-MM-DD" string, compared as a string
  // and stepped through with UTC arithmetic. Never `new Date("2026-04-01")`
  // in local time -- that shifts a day backwards west of UTC and would put
  // a game in the wrong state for anyone on the west coast.
  // ---------------------------------------------------------------------

  function toUTC(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return Date.UTC(y, m - 1, d);
  }

  function fromUTC(ms) {
    return new Date(ms).toISOString().slice(0, 10);
  }

  function addDays(iso, n) {
    return fromUTC(toUTC(iso) + n * 86400000);
  }

  function daysBetween(startIso, endIso) {
    return Math.round((toUTC(endIso) - toUTC(startIso)) / 86400000);
  }

  /** Every date from start to end inclusive. */
  function dateRange(startIso, endIso) {
    const out = [];
    const span = daysBetween(startIso, endIso);
    for (let i = 0; i <= span; i++) out.push(addDays(startIso, i));
    return out;
  }

  // ---------------------------------------------------------------------
  // Building a proposed season
  // ---------------------------------------------------------------------

  /**
   * Propose one classified day per calendar date from a team schedule.
   *
   * "Propose" is the operative word. The brief's rule is that the app
   * suggests and the player confirms -- it never silently assumes. Every day
   * this produces carries `confirmed: false` and a `source` saying where the
   * guess came from, so the UI can show what is still unreviewed and the
   * export can say how much of the return rests on unconfirmed guesses.
   *
   * @param {object} opts
   * @param {Array}  opts.games        [{date, state, home}] from the schedule feed
   * @param {string} opts.homeState    jurisdiction of the club's home park
   * @param {string} [opts.springState]
   * @param {string} [opts.springStart] ISO date
   * @param {string} [opts.springEnd]   ISO date
   * @param {string} opts.seasonStart   ISO date (first day of the championship season)
   * @param {string} opts.seasonEnd     ISO date (last day, including postseason duty)
   */
  function proposeSeasonDays(opts) {
    const {
      games = [],
      homeState,
      springState = null,
      springStart = null,
      springEnd = null,
      seasonStart,
      seasonEnd,
    } = opts;

    const byDate = new Map();
    for (const g of games) {
      // A doubleheader is two games on one date and still one duty day.
      if (!byDate.has(g.date)) byDate.set(g.date, g);
    }

    const days = [];

    if (springStart && springEnd && springState) {
      for (const date of dateRange(springStart, springEnd)) {
        const game = byDate.get(date);
        days.push({
          date,
          type: "spring_training",
          jurisdiction: game ? game.state : springState,
          confirmed: false,
          source: game ? "schedule:spring_game" : "proposed:spring_camp",
        });
      }
    }

    // The championship season. A date with a game is sourced to that game's
    // venue; a date without one is an off day, and where the player actually
    // was on an off day is exactly the kind of thing only he knows -- so it
    // is proposed as "at home" and flagged for confirmation.
    let lastState = homeState;
    for (const date of dateRange(seasonStart, seasonEnd)) {
      const game = byDate.get(date);
      if (game) {
        const type = game.home ? "game_home" : "game_road";
        days.push({
          date,
          type,
          jurisdiction: game.state,
          confirmed: false,
          source: "schedule:game",
        });
        lastState = game.state;
        continue;
      }
      // No game. If the club is mid-road-trip -- the day before and after are
      // both in the same non-home state -- the player was almost certainly
      // still there.
      const prev = byDate.get(addDays(date, -1));
      const next = byDate.get(addDays(date, 1));
      const midTrip =
        prev && next && prev.state === next.state && prev.state !== homeState;
      days.push({
        date,
        type: midTrip ? "off_day_in_state" : "off_day_home",
        jurisdiction: midTrip ? prev.state : homeState,
        confirmed: false,
        source: midTrip ? "proposed:mid_road_trip" : "proposed:off_day_home",
      });
      if (midTrip) lastState = prev.state;
    }

    days.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
    return days;
  }

  /**
   * Reclassify a stretch of dates. This is the bulk edit the brief asks for:
   * an IL stint, an optional assignment, a road trip the player actually
   * spent somewhere else. Returns a new array; does not mutate.
   */
  function applyStretch(days, { from, to, type, jurisdiction, confirmed = true }) {
    return days.map((d) => {
      if (d.date < from || d.date > to) return d;
      return {
        ...d,
        type: type || d.type,
        jurisdiction: jurisdiction !== undefined ? jurisdiction : d.jurisdiction,
        confirmed,
        source: "user:bulk_edit",
      };
    });
  }

  // ---------------------------------------------------------------------
  // Counting
  // ---------------------------------------------------------------------

  function dayTypeRule(rules, type) {
    return (rules && rules.day_types && rules.day_types[type]) || null;
  }

  function countsAsDutyDay(rules, type) {
    const rule = dayTypeRule(rules, type);
    // An unknown day type is NOT counted, and is reported separately. A day
    // type the rules file has never heard of is a bug or a stale saved
    // season, and quietly folding it into the denominator would move every
    // jurisdiction's number without saying so.
    return rule ? rule.counts_as_duty_day === true : false;
  }

  /**
   * Duty-day totals, overall and per jurisdiction.
   * Returns { total, byJurisdiction, excluded, unknownTypes, unconfirmed }.
   */
  function dutyDayTotals(days, rules) {
    const byJurisdiction = {};
    let total = 0;
    let excluded = 0;
    let unconfirmed = 0;
    const unknownTypes = new Set();

    for (const day of days) {
      if (!dayTypeRule(rules, day.type)) unknownTypes.add(day.type);
      if (!countsAsDutyDay(rules, day.type)) {
        excluded++;
        continue;
      }
      const key = day.jurisdiction || "UNKNOWN";
      byJurisdiction[key] = (byJurisdiction[key] || 0) + 1;
      total++;
      if (!day.confirmed) unconfirmed++;
    }

    return {
      total,
      byJurisdiction,
      excluded,
      unconfirmed,
      unknownTypes: [...unknownTypes],
    };
  }

  // ---------------------------------------------------------------------
  // Allocation
  // ---------------------------------------------------------------------

  /**
   * Split `amount` across weights so the parts sum EXACTLY to the whole.
   *
   * Largest remainder, not naive rounding. On a CPA document the per-state
   * allocations have to reconcile to the salary they came from -- a column
   * that adds up to $2,999,999.94 against a $3,000,000 salary is the first
   * thing a preparer will query, and they would be right to.
   */
  function apportion(amount, weights, keys) {
    const totalWeight = weights.reduce((a, b) => a + b, 0);
    if (totalWeight <= 0) return keys.map(() => 0);

    const cents = Math.round(amount * 100);
    const exact = weights.map((w) => (cents * w) / totalWeight);
    const floors = exact.map(Math.floor);
    let remainder = cents - floors.reduce((a, b) => a + b, 0);

    const order = exact
      .map((v, i) => ({ i, frac: v - Math.floor(v) }))
      .sort((a, b) => b.frac - a.frac || a.i - b.i);

    const out = floors.slice();
    for (let k = 0; k < order.length && remainder > 0; k++, remainder--) {
      out[order[k].i]++;
    }
    return out.map((c) => c / 100);
  }

  /**
   * Estimate liability for one jurisdiction, or refuse and say why.
   * Returns { liability, basis, reason }.
   */
  function estimateLiability(jurisdiction, allocatedIncome) {
    if (!jurisdiction) {
      return { liability: null, basis: null, reason: "unknown_jurisdiction" };
    }
    if (jurisdiction.has_wage_income_tax === false) {
      // The one case that can be stated with confidence: a state with no
      // wage income tax produces no wage liability, whatever the rate table
      // of any other state says.
      return { liability: 0, basis: "no_wage_income_tax", reason: null };
    }
    if (jurisdiction.status !== "verified") {
      return { liability: null, basis: null, reason: "rules_unverified" };
    }
    const rate = jurisdiction.top_marginal_rate;
    if (typeof rate !== "number") {
      return { liability: null, basis: null, reason: "no_rate_on_file" };
    }
    return {
      liability: Math.round(allocatedIncome * rate * 100) / 100,
      basis: "top_marginal_rate",
      reason: null,
    };
  }

  /**
   * The main entry point.
   *
   * @param {Array}  days          classified days
   * @param {object} opts
   * @param {number} opts.allocableIncome
   * @param {object} opts.rules     config/tax/duty-day-rules.json
   * @param {object} opts.states    config/tax/2026-states.json
   * @param {string} [opts.domicile] the player's home state, for context
   */
  function allocate(days, opts) {
    const { allocableIncome = 0, rules, states, domicile = null } = opts || {};
    const totals = dutyDayTotals(days, rules);
    const jurisdictions = (states && states.jurisdictions) || {};

    const keys = Object.keys(totals.byJurisdiction).sort();
    const counts = keys.map((k) => totals.byJurisdiction[k]);
    const amounts = apportion(allocableIncome, counts, keys);

    const rows = keys.map((key, i) => {
      const j = jurisdictions[key] || null;
      const allocated = amounts[i];
      const { liability, basis, reason } = estimateLiability(j, allocated);
      const warnings = [];

      if (!j) {
        warnings.push(`No rules on file for jurisdiction "${key}".`);
      } else {
        if (j.status !== "verified" && j.has_wage_income_tax !== false) {
          warnings.push(
            `${j.name}: rules are unverified, so no liability is estimated. Days and allocation are still shown.`
          );
        }
        if (j.local_taxes && j.local_taxes.applies) {
          warnings.push(
            `${j.name}: a local or city income tax may apply on top of state tax` +
              (j.local_taxes.known_jurisdictions.length
                ? ` (${j.local_taxes.known_jurisdictions.join(", ")})`
                : "") +
              `. Not calculated here.`
          );
        }
        if (j.country && j.country !== "US") {
          warnings.push(
            `${j.name} is outside the United States. Foreign filing and treaty questions are not modelled.`
          );
        }
      }

      return {
        jurisdiction: key,
        name: j ? j.name : key,
        dutyDays: totals.byJurisdiction[key],
        share: totals.total ? totals.byJurisdiction[key] / totals.total : 0,
        allocatedIncome: allocated,
        liability,
        liabilityBasis: basis,
        liabilityWithheldBecause: reason,
        isDomicile: domicile != null && key === domicile,
        status: j ? j.status : "unknown",
        warnings,
      };
    });

    rows.sort((a, b) => b.dutyDays - a.dutyDays || (a.jurisdiction < b.jurisdiction ? -1 : 1));

    const estimated = rows.filter((r) => r.liability !== null);
    const withheld = rows.filter((r) => r.liability === null);

    return {
      schemaVersion: SCHEMA_VERSION,
      totalDutyDays: totals.total,
      excludedDays: totals.excluded,
      unconfirmedDutyDays: totals.unconfirmed,
      unknownDayTypes: totals.unknownTypes,
      allocableIncome,
      rows,
      totalAllocated: rows.reduce((a, r) => a + r.allocatedIncome, 0),
      estimatedLiability: estimated.reduce((a, r) => a + r.liability, 0),
      jurisdictionsEstimated: estimated.length,
      jurisdictionsWithheld: withheld.length,
      // Deliberately explicit rather than derived in the UI: a reader has to
      // be able to see at a glance that the total is partial.
      liabilityIsPartial: withheld.length > 0,
    };
  }

  // ---------------------------------------------------------------------
  // Saved-season shape. This object is the whole of a user's state, and it
  // is versioned because it is also the migration path: today it lives in
  // localStorage and in an exported file, and if this ever gets accounts it
  // becomes a database row unchanged.
  // ---------------------------------------------------------------------

  function emptySeason(year) {
    return {
      schemaVersion: SCHEMA_VERSION,
      season: year,
      profile: {
        domicile: null,
        homeState: null,
        club: null,
        allocableIncome: null,
        payStructure: "salary",
      },
      days: [],
      notes: "",
      updatedAt: null,
    };
  }

  function migrateSeason(saved) {
    if (!saved || typeof saved !== "object") return null;
    if (saved.schemaVersion === SCHEMA_VERSION) return saved;
    // No older versions exist yet. When one does, upgrade it here rather
    // than discarding it -- a player who logged a season should never lose
    // it to a schema change.
    return { ...emptySeason(saved.season), ...saved, schemaVersion: SCHEMA_VERSION };
  }

  return {
    SCHEMA_VERSION,
    addDays,
    dateRange,
    daysBetween,
    proposeSeasonDays,
    applyStretch,
    dutyDayTotals,
    countsAsDutyDay,
    apportion,
    estimateLiability,
    allocate,
    emptySeason,
    migrateSeason,
  };
});
