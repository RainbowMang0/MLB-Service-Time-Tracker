/*
 * contract.js -- the contract clock and offer-valuation engine.
 *
 * Same contract as duty-days.js: pure functions, no DOM, no fetch, no
 * storage. It runs in the browser today and under `node --test`, and would
 * run server-side unchanged.
 *
 * WHAT THIS DOES NOT DO, AND WILL NOT UNTIL THE DATA EXISTS
 * ========================================================
 * It does NOT project arbitration salaries, and it does NOT build a comp
 * set. Both need league-wide salary data, and this project has none:
 *
 *   - The service-time database holds no salaries. It never has.
 *   - MLB Trade Rumors' arbitration projections are someone else's work.
 *     Republishing them is the same line this project already drew over
 *     Baseball Reference's figures and over MLB's photographs.
 *   - No free, permitted, machine-readable salary source has been found.
 *
 * So the "decline the offer" branch of an extension analysis -- the modelled
 * arbitration path and free-agent outcome -- is deliberately absent rather
 * than approximated. A breakeven analysis built on invented salaries would
 * be the most confident-looking and least defensible number on the site.
 *
 * What IS here is the half that rests on facts the user supplies or the
 * database already holds: the clock, an empirically-measured accrual
 * distribution, present value, and what an offer nets after deductions.
 *
 * COPY DISCIPLINE
 * ===============
 * Nothing in this file, or in any string it returns, tells the reader what
 * to do. The engine reports; the reader decides.
 *
 * That is enforced rather than promised: tests/contract.test.cjs greps this
 * file and the page for directive vocabulary and fails on a hit. The list of
 * banned phrases lives THERE and only there -- writing it out here would trip
 * the check on its own documentation, which it duly did the first time.
 */

(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.ContractTools = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const SCHEMA_VERSION = 1;

  // ---------------------------------------------------------------------
  // Service-time arithmetic. Every threshold arrives from the ruleset --
  // there is no 172, no 3 and no 6 written down in this file.
  // ---------------------------------------------------------------------

  function formatService(days, rules) {
    const y = rules.full_year_days;
    return `${Math.floor(days / y)}.${String(days % y).padStart(3, "0")}`;
  }

  function toDays(years, rules) {
    return Math.round(years * rules.full_year_days);
  }

  /**
   * Where a player stands against each threshold the agreement defines.
   * Days remaining is a fact about today, not a projection, so it is a
   * single number rather than a range.
   */
  function clock(serviceDays, rules) {
    const arbDays = toDays(rules.arbitration_years, rules);
    const faDays = toDays(rules.free_agency_years, rules);

    const marks = [
      {
        key: "arbitration",
        label: "Arbitration eligible",
        atDays: arbDays,
        atService: formatService(arbDays, rules),
      },
      {
        key: "free_agency",
        label: "Free agency eligible",
        atDays: faDays,
        atService: formatService(faDays, rules),
      },
    ].map((m) => ({
      ...m,
      reached: serviceDays >= m.atDays,
      daysRemaining: Math.max(0, m.atDays - serviceDays),
    }));

    const next = marks.find((m) => !m.reached) || null;

    return {
      serviceDays,
      service: formatService(serviceDays, rules),
      fullYearDays: rules.full_year_days,
      marks,
      next,
      // Days accrued inside the current credited year -- the figure after the
      // decimal point, which is what a threshold conversation is usually about.
      daysIntoCurrentYear: serviceDays % rules.full_year_days,
    };
  }

  // ---------------------------------------------------------------------
  // Projection. Never a single date: §3.3 of the brief, and the honest
  // shape of the thing.
  // ---------------------------------------------------------------------

  function bandFor(serviceDays, model, rules) {
    const band = Math.min(6, Math.floor(serviceDays / rules.full_year_days));
    return (model.bands || []).find((b) => b.band === band) || null;
  }

  /**
   * How many more seasons until `targetDays`, if the player accrues at a
   * given per-season rate. Returns null when the rate is zero and the
   * target is unreached -- "never, at this rate" is the honest answer and
   * the caller has to render it as such rather than as a large number.
   */
  function seasonsToReach(serviceDays, targetDays, perSeasonDays, rules) {
    if (serviceDays >= targetDays) return 0;
    if (!perSeasonDays || perSeasonDays <= 0) return null;
    let days = serviceDays;
    let seasons = 0;
    // Walk season by season rather than dividing, because a season is capped
    // at the credited maximum however many days a player is rostered.
    const capped = Math.min(perSeasonDays, rules.full_year_days);
    while (days < targetDays && seasons < 40) {
      days += capped;
      seasons += 1;
    }
    return days >= targetDays ? seasons : null;
  }

  /**
   * Project the threshold crossings as a RANGE, from the empirical accrual
   * model rather than from an assumption of full seasons.
   *
   * The three rates are what comparable players actually accrued -- p20,
   * p50, p80 of the next-season distribution for this service band. They
   * are labelled as outcomes of a population, not as probabilities for this
   * individual, because that is what they are.
   */
  function project(serviceDays, model, rules, currentSeason) {
    const band = bandFor(serviceDays, model, rules);
    if (!band || !band.sample) {
      return { available: false, reason: "no_model_for_band", band: band || null };
    }
    if (!band.enough_data) {
      return { available: false, reason: "band_sample_too_small", band };
    }

    const scenarios = [
      { key: "p20", label: "Slower than most", daysPerSeason: band.p20 },
      { key: "p50", label: "Typical", daysPerSeason: band.p50 },
      { key: "p80", label: "Faster than most", daysPerSeason: band.p80 },
    ];

    const targets = [
      { key: "arbitration", label: "Arbitration eligible", days: toDays(rules.arbitration_years, rules) },
      { key: "free_agency", label: "Free agency eligible", days: toDays(rules.free_agency_years, rules) },
    ];

    const rows = targets.map((t) => ({
      ...t,
      reached: serviceDays >= t.days,
      daysRemaining: Math.max(0, t.days - serviceDays),
      outcomes: scenarios.map((s) => {
        const seasons = seasonsToReach(serviceDays, t.days, s.daysPerSeason, rules);
        return {
          ...s,
          seasons,
          // The season a player would FINISH crossing in. Null propagates:
          // an unreachable target must not render as a year.
          season: seasons === null ? null : currentSeason + seasons,
        };
      }),
    }));

    return {
      available: true,
      band,
      scenarios,
      targets: rows,
      basis: model.method,
      sample: band.sample,
    };
  }

  // ---------------------------------------------------------------------
  // Present value
  // ---------------------------------------------------------------------

  /**
   * Discount a stream of {year, amount} to present value.
   *
   * Deferred money is the reason this exists. A contract that pays $10M in
   * 2040 is not a $10M contract, and a player comparing offers without
   * discounting is comparing the wrong numbers. The rate is the caller's
   * input, exposed in the UI, because the right rate is a judgement about
   * the reader's own alternatives rather than a fact this tool knows.
   */
  function presentValue(cashflows, annualRate, baseYear) {
    let pv = 0;
    const detail = cashflows.map((cf) => {
      const t = Math.max(0, cf.year - baseYear);
      const factor = 1 / Math.pow(1 + annualRate, t);
      const discounted = cf.amount * factor;
      pv += discounted;
      return { ...cf, yearsOut: t, factor, presentValue: discounted };
    });
    return { presentValue: pv, nominal: cashflows.reduce((a, c) => a + c.amount, 0), detail };
  }

  // ---------------------------------------------------------------------
  // Deductions
  // ---------------------------------------------------------------------

  /**
   * Gross to net, itemised.
   *
   * EVERY rate here is the caller's input. This engine does not hold a
   * federal bracket table, an agent-commission figure or a dues schedule,
   * and it does not pretend to: the same rule that governs the tax table
   * applies here, and a number nobody verified must not arrive wearing the
   * site's authority. The UI states the defaults ARE assumptions and lets
   * the reader change every one.
   *
   * `stateTax` is expected to come from the duty-day engine, where it is
   * already labelled by tier and carries its own caveats.
   */
  function netOf(gross, deductions) {
    const {
      agentPct = 0,
      federalEffectiveRate = 0,
      stateTax = 0,
      duesAndOther = 0,
    } = deductions || {};

    const agent = gross * agentPct;
    const federal = gross * federalEffectiveRate;
    const items = [
      { key: "agent", label: "Agent commission", amount: agent, isInput: true },
      { key: "federal", label: "Federal income tax", amount: federal, isInput: true },
      { key: "state", label: "State and local tax", amount: stateTax, isInput: true },
      { key: "dues", label: "Dues and other", amount: duesAndOther, isInput: true },
    ];
    const total = items.reduce((a, i) => a + i.amount, 0);
    return {
      gross,
      items,
      totalDeductions: total,
      net: gross - total,
      // Stated rather than implied: every figure above came from the reader.
      allRatesAreUserInputs: true,
    };
  }

  // ---------------------------------------------------------------------
  // Valuing an offer
  // ---------------------------------------------------------------------

  /**
   * What an offer guarantees, in nominal and present-value terms, gross and
   * net.
   *
   * This is deliberately the ACCEPT side only. The decline side needs
   * salary comps this project does not have and will not invent -- see the
   * header. `declineModelled: false` ships in the result so a caller cannot
   * present this as a comparison by omission.
   */
  function valueOffer(offer, opts) {
    const {
      discountRate = 0.05,
      baseYear,
      agentPct = 0,
      federalEffectiveRate = 0,
      stateEffectiveRate = 0,
      duesAndOther = 0,
    } = opts || {};

    const years = (offer.years || []).map((y) => ({
      year: y.year,
      amount: Number(y.amount) || 0,
      guaranteed: y.guaranteed !== false,
      note: y.note || "",
    }));

    const guaranteedYears = years.filter((y) => y.guaranteed);
    const nominalGuaranteed = guaranteedYears.reduce((a, y) => a + y.amount, 0);
    const nominalTotal = years.reduce((a, y) => a + y.amount, 0);

    const pv = presentValue(
      guaranteedYears.map((y) => ({ year: y.year, amount: y.amount })),
      discountRate,
      baseYear
    );

    const net = netOf(nominalGuaranteed, {
      agentPct,
      federalEffectiveRate,
      stateTax: nominalGuaranteed * stateEffectiveRate,
      duesAndOther,
    });

    // Net present value: discount each year's cash flow after applying the
    // same proportional deductions. Deductions are proportional here, which
    // is an approximation and is labelled as one.
    const netRate = nominalGuaranteed > 0 ? net.net / nominalGuaranteed : 0;
    const netPv = presentValue(
      guaranteedYears.map((y) => ({ year: y.year, amount: y.amount * netRate })),
      discountRate,
      baseYear
    );

    return {
      schemaVersion: SCHEMA_VERSION,
      years,
      nominalTotal,
      nominalGuaranteed,
      nonGuaranteed: nominalTotal - nominalGuaranteed,
      discountRate,
      baseYear,
      presentValueGross: pv.presentValue,
      presentValueNet: netPv.presentValue,
      net,
      perYear: pv.detail,
      // Flags a caller must surface rather than quietly drop.
      declineModelled: false,
      deductionsAreProportionalApproximation: true,
    };
  }

  // ---------------------------------------------------------------------
  // Ruleset comparison -- the thing nothing else in baseball will have
  // ---------------------------------------------------------------------

  /**
   * The same player under two agreements, side by side.
   *
   * The 2022 agreement expires 2026-12-01. When a successor lands, filling
   * in config/cba/2027.json makes this live the same day, for every player,
   * with no code change.
   *
   * Both rulesets must be usable. A placeholder returns `available: false`
   * rather than a delta against nulls -- comparing a real agreement to an
   * empty one would produce a large and entirely meaningless number.
   */
  function compareRulesets(serviceDays, rulesA, rulesB, model, currentSeason) {
    if (!rulesA || !rulesB || !rulesA.usable || !rulesB.usable) {
      return {
        available: false,
        reason: "a ruleset is a placeholder and has no values to compare",
        a: rulesA ? rulesA.version : null,
        b: rulesB ? rulesB.version : null,
      };
    }

    const build = (rules) => ({
      version: rules.version,
      clock: clock(serviceDays, rules),
      projection: project(serviceDays, model, rules, currentSeason),
    });

    const a = build(rulesA);
    const b = build(rulesB);

    const deltas = a.clock.marks.map((markA) => {
      const markB = b.clock.marks.find((m) => m.key === markA.key);
      return {
        key: markA.key,
        label: markA.label,
        a: { atService: markA.atService, daysRemaining: markA.daysRemaining },
        b: markB
          ? { atService: markB.atService, daysRemaining: markB.daysRemaining }
          : null,
        // Positive = the second agreement puts the threshold further away.
        daysDelta: markB ? markB.daysRemaining - markA.daysRemaining : null,
      };
    });

    return { available: true, a, b, deltas };
  }

  return {
    SCHEMA_VERSION,
    formatService,
    toDays,
    clock,
    bandFor,
    seasonsToReach,
    project,
    presentValue,
    netOf,
    valueOffer,
    compareRulesets,
  };
});
