/*
 * contract-page.js -- the Contract Clock page.
 *
 * UI only. Every figure comes from contract.js, which is pure and tested.
 * No arithmetic belongs in this file.
 *
 * On copy: this page reports and never directs. The banned vocabulary is
 * listed in tests/contract.test.cjs, which greps this file too.
 */

(function () {
  "use strict";

  const CT = window.ContractTools;
  const THEME_KEY = "mlb-service-time-theme";

  let RULES = null; // the `rules` block from index.json
  let MODEL = null; // docs/data/accrual_model.json
  let PLAYERS = []; // [{id, name, days, on40}]
  let serviceDays = null;
  let currentSeason = new Date().getFullYear();
  let offerYears = [];

  const $ = (id) => document.getElementById(id);

  function money(n) {
    if (n === null || n === undefined || !isFinite(n)) return "—";
    return n.toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0,
    });
  }

  function safeGet(k) {
    try { return localStorage.getItem(k); } catch (e) { return null; }
  }
  function safeSet(k, v) {
    try { localStorage.setItem(k, v); return true; } catch (e) { return false; }
  }

  function initTheme() {
    const root = document.documentElement;
    const stored = safeGet(THEME_KEY);
    if (stored) root.setAttribute("data-theme", stored);
    const btn = $("theme-toggle");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const next = (root.getAttribute("data-theme") || "dark") === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      safeSet(THEME_KEY, next);
    });
  }

  // -----------------------------------------------------------------------
  // Parsing service time. "2.100" is 2 years and 100 days, NOT 2.1 years --
  // the decimal part is a day count out of the credited year, which is the
  // single most common way to misread this notation.
  // -----------------------------------------------------------------------

  function parseService(text) {
    const m = /^\s*(\d+)\s*[.:]\s*(\d{1,3})\s*$/.exec(String(text || ""));
    if (!m) return null;
    const years = Number(m[1]);
    const days = Number(m[2]);
    if (days >= RULES.full_year_days) return null;
    return years * RULES.full_year_days + days;
  }

  // -----------------------------------------------------------------------
  // Rendering
  // -----------------------------------------------------------------------

  function renderClock() {
    if (serviceDays === null) {
      $("clock-tiles").innerHTML = "";
      $("panel-projection").hidden = true;
      $("panel-rulesets").hidden = true;
      return;
    }

    const c = CT.clock(serviceDays, RULES);
    const tiles = [
      `<div class="tile"><span class="tile-label">Service time</span>
         <span class="tile-value">${c.service}</span>
         <span class="tile-foot">${c.serviceDays.toLocaleString()} days credited</span></div>`,
    ];
    for (const mark of c.marks) {
      tiles.push(
        `<div class="tile ${mark.reached ? "" : "tile-partial"}">
           <span class="tile-label">${mark.label}</span>
           <span class="tile-value">${mark.reached ? "Reached" : mark.daysRemaining + " days"}</span>
           <span class="tile-foot">at ${mark.atService}</span>
         </div>`
      );
    }
    $("clock-tiles").innerHTML = tiles.join("");

    renderProjection();
    renderRulesets();
  }

  function renderProjection() {
    const p = CT.project(serviceDays, MODEL, RULES, currentSeason);
    const panel = $("panel-projection");

    if (!p.available) {
      panel.hidden = false;
      $("projection-basis").textContent =
        p.reason === "band_sample_too_small"
          ? "Too few comparable player-seasons at this service level to draw a distribution from. No projection is shown rather than one built on a handful of careers."
          : "No measured population for this service level, so no projection is shown.";
      $("projection-table").querySelector("tbody").innerHTML = "";
      $("projection-notes").innerHTML = "";
      return;
    }

    panel.hidden = false;
    $("projection-basis").innerHTML =
      `Measured from <b>${p.sample.toLocaleString()}</b> player-seasons in this project's own database: ` +
      `players who were in the majors with between ${p.band.label} years of service, and what they accrued the following season. ` +
      `Median ${p.band.p50} days; ${Math.round(p.band.share_full_year * 100)}% reached a full credited year, ` +
      `${Math.round(p.band.share_zero * 100)}% accrued none.`;

    const cell = (o) => {
      if (o.seasons === null) {
        return `<td><span class="status st-unverified">Not reached at this rate</span></td>`;
      }
      if (o.seasons === 0) return `<td><span class="status st-ok">Already reached</span></td>`;
      return `<td><b>${o.season}</b><span class="cell-sub">${o.seasons} more season${o.seasons === 1 ? "" : "s"} · ${o.daysPerSeason} days/yr</span></td>`;
    };

    $("projection-table").querySelector("tbody").innerHTML = p.targets
      .map(
        (t) => `<tr>
          <td>${t.label}<span class="cell-sub">at ${CT.formatService(t.days, RULES)}</span></td>
          <td class="num">${t.reached ? "—" : t.daysRemaining}</td>
          ${t.outcomes.map(cell).join("")}
        </tr>`
      )
      .join("");

    $("projection-notes").innerHTML =
      "<ul>" +
      [
        "These are outcomes for a population, not probabilities for one player. What a given season holds depends on health, role and club decisions that nothing here can see.",
        "The distribution is measured from estimated service time, so it carries every limitation of the estimates behind it.",
        "Seasons are capped at the credited maximum, so no rate of accrual can shorten the path below one credited year per season.",
      ]
        .map((s) => `<li>${s}</li>`)
        .join("") +
      "</ul>";
  }

  // -----------------------------------------------------------------------
  // Ruleset comparison
  // -----------------------------------------------------------------------

  function comparisonRules() {
    return {
      version: $("in-ruleset-b").value || "comparison",
      usable: true,
      full_year_days: Number($("in-b-fyd").value) || RULES.full_year_days,
      arbitration_years: Number($("in-b-arb").value),
      free_agency_years: Number($("in-b-fa").value),
    };
  }

  function renderRulesets() {
    const panel = $("panel-rulesets");
    panel.hidden = false;
    const b = comparisonRules();
    const cmp = CT.compareRulesets(serviceDays, RULES, b, MODEL, currentSeason);

    if (!cmp.available) {
      $("ruleset-table").querySelector("tbody").innerHTML = "";
      $("ruleset-note").textContent = cmp.reason;
      return;
    }

    $("ruleset-table").querySelector("tbody").innerHTML = cmp.deltas
      .map((d) => {
        const delta = d.daysDelta;
        const cls = delta === 0 ? "" : delta > 0 ? "st-unverified" : "st-ok";
        const text =
          delta === 0
            ? "no change"
            : `${delta > 0 ? "+" : ""}${delta} days${delta > 0 ? " further away" : " sooner"}`;
        return `<tr>
          <td>${d.label}</td>
          <td>${d.a.atService}<span class="cell-sub">${d.a.daysRemaining} days away</span></td>
          <td>${d.b.atService}<span class="cell-sub">${d.b.daysRemaining} days away</span></td>
          <td class="num"><span class="status ${cls}">${text}</span></td>
        </tr>`;
      })
      .join("");

    $("ruleset-note").textContent =
      "The comparison column is whatever you type into it. config/cba/2027.json is an empty placeholder until an agreement is signed — when one is, filling it in makes this live for every player with no code change.";
  }

  // -----------------------------------------------------------------------
  // The offer
  // -----------------------------------------------------------------------

  function renderOfferRows() {
    const tbody = $("offer-table").querySelector("tbody");
    tbody.innerHTML = offerYears
      .map(
        (y, i) => `<tr>
          <td><input type="number" class="cell-in" data-i="${i}" data-f="year" value="${y.year}" min="2000" max="2100" /></td>
          <td class="num"><input type="number" class="cell-in num" data-i="${i}" data-f="amount" value="${y.amount}" min="0" step="100000" /></td>
          <td><select class="cell-in" data-i="${i}" data-f="guaranteed">
            <option value="yes"${y.guaranteed ? " selected" : ""}>Yes</option>
            <option value="no"${y.guaranteed ? "" : " selected"}>No</option>
          </select></td>
          <td><input type="text" class="cell-in" data-i="${i}" data-f="note" value="${(y.note || "").replace(/"/g, "&quot;")}" placeholder="club option, deferred…" /></td>
          <td><button type="button" class="btn btn-danger btn-sm" data-remove="${i}">Remove</button></td>
        </tr>`
      )
      .join("");
  }

  function renderOffer() {
    const opts = {
      baseYear: currentSeason,
      discountRate: Number($("in-discount").value) || 0,
      agentPct: Number($("in-agent").value) || 0,
      federalEffectiveRate: Number($("in-federal").value) || 0,
      stateEffectiveRate: Number($("in-state").value) || 0,
      duesAndOther: Number($("in-dues").value) || 0,
    };
    const v = CT.valueOffer({ years: offerYears }, opts);

    $("offer-tiles").innerHTML = `
      <div class="tile"><span class="tile-label">Guaranteed, nominal</span>
        <span class="tile-value">${money(v.nominalGuaranteed)}</span>
        <span class="tile-foot">as written, undiscounted</span></div>
      <div class="tile"><span class="tile-label">Guaranteed, present value</span>
        <span class="tile-value">${money(v.presentValueGross)}</span>
        <span class="tile-foot">at ${(opts.discountRate * 100).toFixed(1)}% a year</span></div>
      <div class="tile"><span class="tile-label">After your deductions</span>
        <span class="tile-value">${money(v.net.net)}</span>
        <span class="tile-foot">nominal, your rates</span></div>
      <div class="tile"><span class="tile-label">Net present value</span>
        <span class="tile-value">${money(v.presentValueNet)}</span>
        <span class="tile-foot">discounted and net</span></div>
      ${
        v.nonGuaranteed > 0
          ? `<div class="tile tile-partial"><span class="tile-label">Not guaranteed</span>
               <span class="tile-value">${money(v.nonGuaranteed)}</span>
               <span class="tile-foot">excluded from every figure above</span></div>`
          : ""
      }`;

    $("offer-detail").querySelector("tbody").innerHTML = v.perYear
      .map(
        (r) => `<tr>
          <td>${r.year}</td>
          <td class="num">${money(r.amount)}</td>
          <td class="num">${r.yearsOut}</td>
          <td class="num">${r.factor.toFixed(4)}</td>
          <td class="num">${money(r.presentValue)}</td>
        </tr>`
      )
      .join("");

    const notes = [
      "<b>Only the accept side is computed.</b> There is no modelled outcome for declining, because that needs salary comps this project does not have. See the last panel.",
      `Deductions are applied proportionally across years rather than year by year, which is an approximation. Every rate is one you entered — ${v.net.items.map((i) => i.label.toLowerCase()).join(", ")} — and none is supplied or verified by this site.`,
    ];
    if (v.nonGuaranteed > 0) {
      notes.push(
        `${money(v.nonGuaranteed)} is marked not guaranteed and is excluded from the totals above rather than added to them.`
      );
    }
    $("offer-notes").innerHTML = "<ul>" + notes.map((n) => `<li>${n}</li>`).join("") + "</ul>";
  }

  // -----------------------------------------------------------------------
  // Wiring
  // -----------------------------------------------------------------------

  function wire() {
    $("in-service").addEventListener("input", (e) => {
      const days = parseService(e.target.value);
      if (days === null) {
        if (e.target.value.trim()) {
          $("clock-status").textContent =
            `Enter service time as years.days, e.g. 2.100 — the part after the point is a day count out of ${RULES.full_year_days}, not a fraction.`;
        }
        return;
      }
      serviceDays = days;
      $("clock-status").textContent = `Showing ${CT.formatService(days, RULES)}.`;
      renderClock();
    });

    $("in-player").addEventListener("change", (e) => {
      const match = PLAYERS.find((p) => p.name === e.target.value);
      if (!match) return;
      serviceDays = match.days;
      $("in-service").value = CT.formatService(match.days, RULES);
      $("clock-status").textContent =
        `${match.name} — ${CT.formatService(match.days, RULES)} as of the last daily update.` +
        (match.on40 ? "" : " He is not on a 40-man roster; his figure is where his clock stopped.");
      renderClock();
    });

    $("in-season").addEventListener("change", (e) => {
      currentSeason = Number(e.target.value) || currentSeason;
      renderClock();
      renderOffer();
    });

    for (const id of ["in-ruleset-b", "in-b-fyd", "in-b-arb", "in-b-fa"]) {
      $(id).addEventListener("change", () => {
        if ($(id).id === "in-ruleset-b") applyRulesetPreset();
        if (serviceDays !== null) renderRulesets();
      });
    }

    $("btn-add-year").addEventListener("click", () => {
      const last = offerYears[offerYears.length - 1];
      offerYears.push({
        year: last ? last.year + 1 : currentSeason + 1,
        amount: last ? last.amount : 1000000,
        guaranteed: true,
        note: "",
      });
      renderOfferRows();
      renderOffer();
    });

    $("offer-table").addEventListener("input", (e) => {
      const el = e.target.closest(".cell-in");
      if (!el) return;
      const i = Number(el.dataset.i);
      const f = el.dataset.f;
      if (!offerYears[i]) return;
      if (f === "guaranteed") offerYears[i].guaranteed = el.value === "yes";
      else if (f === "note") offerYears[i].note = el.value;
      else offerYears[i][f] = Number(el.value) || 0;
      renderOffer();
    });
    $("offer-table").addEventListener("change", (e) => {
      if (e.target.closest("select.cell-in")) {
        const el = e.target;
        offerYears[Number(el.dataset.i)].guaranteed = el.value === "yes";
        renderOffer();
      }
    });

    $("offer-table").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-remove]");
      if (!btn) return;
      offerYears.splice(Number(btn.dataset.remove), 1);
      renderOfferRows();
      renderOffer();
    });

    for (const id of ["in-discount", "in-agent", "in-federal", "in-state", "in-dues"]) {
      $(id).addEventListener("input", renderOffer);
    }
  }

  function applyRulesetPreset() {
    const choice = $("in-ruleset-b").value;
    // Presets are labelled as hypotheticals in the option text. Nothing here
    // claims to be a proposal anyone actually tabled -- they exist so the
    // comparison has something to show before an agreement lands.
    const presets = {
      "fa-7": { fyd: RULES.full_year_days, arb: RULES.arbitration_years, fa: 7 },
      "fa-5": { fyd: RULES.full_year_days, arb: RULES.arbitration_years, fa: 5 },
      "arb-2": { fyd: RULES.full_year_days, arb: 2, fa: RULES.free_agency_years },
      same: {
        fyd: RULES.full_year_days,
        arb: RULES.arbitration_years,
        fa: RULES.free_agency_years,
      },
    };
    const p = presets[choice] || presets.same;
    $("in-b-fyd").value = p.fyd;
    $("in-b-arb").value = p.arb;
    $("in-b-fa").value = p.fa;
  }

  async function init() {
    initTheme();
    try {
      const [index, model] = await Promise.all([
        fetch("data/index.json", { cache: "no-cache" }).then((r) => r.json()),
        fetch("data/accrual_model.json", { cache: "no-cache" }).then((r) => r.json()),
      ]);
      RULES = index.rules;
      if (!RULES || typeof RULES.full_year_days !== "number") {
        throw new Error("index.json carries no CBA rules block");
      }
      RULES.usable = true;
      MODEL = model;

      const fields = index.fields || [];
      const ix = (n) => fields.indexOf(n);
      PLAYERS = (index.players || []).map((row) => ({
        id: row[ix("id")],
        name: row[ix("name")],
        days: row[ix("days")],
        on40: row[ix("on_40_man")] === 1,
      }));
    } catch (e) {
      $("clock-status").textContent =
        `Could not load the data files (${e.message}). That is a deployment problem, not something you did.`;
      return;
    }

    $("in-season").value = currentSeason;

    // Rostered players first: this page is about a decision in front of
    // someone, and a retired player's clock stopped years ago.
    const rostered = PLAYERS.filter((p) => p.on40).sort((a, b) => (a.name < b.name ? -1 : 1));
    $("player-list").innerHTML = rostered
      .map((p) => `<option value="${p.name.replace(/"/g, "&quot;")}"></option>`)
      .join("");

    $("in-ruleset-b").innerHTML = `
      <option value="same">Same as 2022 (no change)</option>
      <option value="fa-7">Hypothetical: free agency at 7 years</option>
      <option value="fa-5">Hypothetical: free agency at 5 years</option>
      <option value="arb-2">Hypothetical: arbitration at 2 years</option>`;
    applyRulesetPreset();

    $("clock-status").textContent =
      `${rostered.length.toLocaleString()} players on a 40-man roster. Pick one, or type a service time directly.`;

    offerYears = [
      { year: currentSeason + 1, amount: 5000000, guaranteed: true, note: "" },
      { year: currentSeason + 2, amount: 8000000, guaranteed: true, note: "" },
      { year: currentSeason + 3, amount: 12000000, guaranteed: false, note: "club option" },
    ];

    wire();
    renderOfferRows();
    renderOffer();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
