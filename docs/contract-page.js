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
  let LABEL_TO_PLAYER = new Map(); // datalist label -> player; never keyed by name alone
  let birthYear = null;            // null is a real answer: "age unknown"
  let superTwo = false;

  const $ = (id) => document.getElementById(id);

  // Names and clubs come from the published payload rather than from a
  // visitor, so this is discipline rather than a live hole -- the same
  // discipline app.js uses everywhere, and the one place it lapsed was a bug.
  function esc(t) {
    return String(t).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

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

  /**
   * Resolve what was typed or picked into exactly one player.
   *
   * A datalist puts the option's `value` into the input, so the value is the
   * id and the resolution is unambiguous. A reader who types a bare name gets
   * one only if that name is unique -- with a duplicate we refuse and say so,
   * rather than picking one and being silently wrong about which.
   */
  function playerFromInput(text) {
    const raw = String(text || "").trim();
    if (!raw) return null;
    const picked = LABEL_TO_PLAYER.get(raw);
    if (picked) return picked;
    const byId = PLAYERS.find((p) => String(p.id) === raw);
    if (byId) return byId;
    const byName = PLAYERS.filter((p) => p.name.toLowerCase() === raw.toLowerCase());
    if (byName.length === 1) return byName[0];
    if (byName.length > 1) {
      $("clock-status").textContent =
        `${byName.length} players are called ${byName[0].name}. Pick one from the list rather than typing the name — ` +
        byName.map((p) => `${p.club || "no club"} (${CT.formatService(p.days, RULES)})`).join(", ") + ".";
    }
    return null;
  }

  /**
   * Build the datalist and the label lookup together, so they cannot drift.
   *
   * The option value is what the browser puts in the input, so it has to be
   * readable -- an id in the box would look like a bug. Name-plus-club is
   * unique across every 40-man today, but the whole point of this fix is not
   * to rest on a string being unique, so a collision appends the id rather
   * than letting two options resolve to one player.
   */
  function buildPlayerList(players) {
    const counts = new Map();
    for (const p of players) {
      const base = p.name + (p.club ? " · " + p.club : "");
      counts.set(base, (counts.get(base) || 0) + 1);
    }
    LABEL_TO_PLAYER = new Map();
    const options = [];
    for (const p of players) {
      const base = p.name + (p.club ? " · " + p.club : "");
      const label = counts.get(base) > 1 ? `${base} (#${p.id})` : base;
      LABEL_TO_PLAYER.set(label, p);
      options.push(`<option value="${esc(label)}"></option>`);
    }
    $("player-list").innerHTML = options.join("");
  }

  // -----------------------------------------------------------------------
  // Rendering
  // -----------------------------------------------------------------------

  function renderClock() {
    if (serviceDays === null) {
      $("clock-tiles").innerHTML = "";
      $("panel-stage").hidden = true;
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

    renderStage();
    renderProjection();
    renderRulesets();
  }

  function renderStage() {
    const panel = $("panel-stage");
    if (serviceDays === null) { panel.hidden = true; return; }
    panel.hidden = false;

    const st = CT.stageOf(serviceDays, RULES, superTwo);
    const stages = CT.STAGES;

    $("stage-tiles").innerHTML = stages
      .map((sg) => {
        const here = sg.key === st.key;
        const passed = stages.indexOf(sg) < stages.findIndex((x) => x.key === st.key);
        return `<div class="tile ${here ? "" : "tile-partial"}">
          <span class="tile-label">${esc(sg.label)}${here ? " · now" : passed ? " · passed" : ""}</span>
          <span class="tile-value" style="font-size:.95rem;line-height:1.45;font-family:inherit;letter-spacing:0">${esc(sg.rights)}</span>
          ${
            here && st.nextStageAt
              ? `<span class="tile-foot">${st.daysToNextStage} days to ${esc(st.nextGain || "")} at ${esc(st.nextStageAt)}</span>`
              : here
              ? `<span class="tile-foot">the last line on the clock</span>`
              : ""
          }
        </div>`;
      })
      .join("");

    const notes = [];
    if (st.viaSuperTwo) {
      notes.push(
        "He reaches arbitration early as a <b>Super Two</b> — the top share of the two-to-three-year class by service time. That is the one case where the stage and the raw figure disagree, and it is worth a fourth arbitration year."
      );
    }
    // The honest statement about magnitude, which is the part that is NOT
    // settled. See research/valuation-literature.md -- this wording is the
    // conclusion of that whole survey and should not be loosened.
    notes.push(
      "<b>How large is the gap between pay and value at each stage? Published research does not agree, and the disagreement is an order of magnitude.</b> Applying the two dominant methods to the same players, Krautmann (1999) reports a surplus of either $4.5M or $57M per club. His own later reply (2013) explains why they differ: one asks what determines a salary <i>at signing</i>, the other asks whether a player <i>earned</i> it afterwards. They are different questions, so this page reports the rights that change rather than a share of value."
    );
    notes.push(
      "What is not in dispute is the ordering. Every source agrees restricted players are paid less relative to their value than free agents, and that the gap is widest at the front of the clock."
    );
    $("stage-notes").innerHTML = "<ul>" + notes.map((n) => `<li>${n}</li>`).join("") + "</ul>";
  }

  function renderProjection() {
    const p = CT.withAges(CT.project(serviceDays, MODEL, RULES, currentSeason), birthYear);
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
      `Measured from <b>${p.model.transitions.toLocaleString()}</b> player-seasons in this project's own database: ` +
      `what players in the majors actually accrued the following season, banded by the service they had already earned. ` +
      `This player starts in the ${p.band.label} band (${p.band.sample.toLocaleString()} player-seasons, median ${p.band.p50} days; ` +
      `${Math.round(p.band.share_full_year * 100)}% reached a full credited year, ${Math.round(p.band.share_zero * 100)}% accrued none) ` +
      `and the walk moves him into the next band as he crosses it.`;

    const cell = (o) => {
      if (o.seasons === null) {
        return `<td><span class="status st-unverified">Not reached at this rate</span></td>`;
      }
      if (o.seasons === 0) return `<td><span class="status st-ok">Already reached</span></td>`;
      const rate = o.seasons === 1 ? `${o.daysPerSeason} days` : `from ${o.daysPerSeason} days/yr`;
      return `<td><b>${o.season}</b><span class="cell-sub">${o.seasons} more season${o.seasons === 1 ? "" : "s"} · ${rate}</span></td>`;
    };

    // Age at the MEDIAN outcome only. Three ages across three columns would
    // be noise; the question a reader has is "how old will I be", and the
    // median is the honest single answer to it.
    const ageCell = (t) => {
      const mid = t.outcomes.find((o) => o.key === "p50");
      if (t.reached) return `<td class="num">—</td>`;
      if (!mid || mid.age === null) {
        return `<td class="num"><span class="status st-unverified">not known</span></td>`;
      }
      return `<td class="num"><b>${mid.age}</b></td>`;
    };

    $("projection-table").querySelector("tbody").innerHTML = p.targets
      .map(
        (t) => `<tr>
          <td>${t.label}<span class="cell-sub">at ${CT.formatService(t.days, RULES)}</span></td>
          <td class="num">${t.reached ? "—" : t.daysRemaining}</td>
          ${t.outcomes.map(cell).join("")}
          ${ageCell(t)}
        </tr>`
      )
      .join("");

    $("projection-notes").innerHTML =
      "<ul>" +
      [
        "These are outcomes for a population, not probabilities for one player. What a given season holds depends on health, role and club decisions that nothing here can see.",
        "The rate is re-read at each season from the band the player would then be in, because the measured rate rises with service. Holding a rookie's slowest season across a decade would produce a date no career reaches.",
        // The survivorship caveat. The aging-curve literature (Nguyen &
        // Matthews 2024; Schuckers et al. 2023) shows curves fitted only on
        // players still playing overestimate, and these bands condition on
        // exactly that. Saying so is the difference between a measured claim
        // and an overclaim.
        "<b>These bands describe players who kept a job.</b> Each is measured from players who were still in the majors the following season, so a player who is about to lose his roster spot is not in the population — and the 20th-percentile column is not a floor. The flat top band, where every outcome is a full credited year, is that selection showing rather than a fact about durability.",
        birthYear
          ? "Age on arrival is approximate, from birth year only. It matters because the thresholds are about service time while the research on what a player is worth is about age: Fair (2008) puts peak performance between 26.5 and 28.3 depending on the measure, with pitchers peaking earlier and declining faster."
          : "Age on arrival is not shown because this player's birth year is not in the published data yet. It fills in on the next daily update.",
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
      // A typed figure is not a player, so anything player-specific has to
      // clear -- carrying the last player's birth year into a hand-entered
      // number would silently report someone else's age.
      birthYear = null;
      superTwo = false;
      $("clock-status").textContent = `Showing ${CT.formatService(days, RULES)}.`;
      renderClock();
    });

    // Resolved by ID, never by name. Two players on 40-man rosters share the
    // name "Max Muncy" today (289 days and 1,741 days), and the database holds
    // 36 duplicated names in all -- two Logan Allens, two Luis Perdomos. A
    // name lookup silently returns whichever row comes first, which on this
    // page is an eight-and-a-half-year error inside a contract decision.
    //
    // The datalist option's `value` therefore carries the id and its `label`
    // carries what the reader sees, so a duplicate name is disambiguated by
    // the club shown beside it.
    $("in-player").addEventListener("change", (e) => {
      const match = playerFromInput(e.target.value);
      if (!match) return;
      serviceDays = match.days;
      birthYear = match.birthYear;
      superTwo = match.superTwo;
      $("in-service").value = CT.formatService(match.days, RULES);
      $("clock-status").textContent =
        `${match.name}${match.club ? " · " + match.club : ""} — ${CT.formatService(match.days, RULES)} as of the last daily update.` +
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
      const teams = index.teams || [];
      PLAYERS = (index.players || []).map((row) => ({
        id: row[ix("id")],
        name: row[ix("name")],
        days: row[ix("days")],
        on40: row[ix("on_40_man")] === 1,
        // Blank for anyone off a 40-man: a non-rostered player's stored club
        // is stale by construction, and the payload already blanks it.
        club: teams[row[ix("team")]] || null,
        hasPage: row[ix("has_page")] === 1,
        superTwo: row[ix("super_two")] === 1,
        // -1 is the index() miss for a payload written before birth_year
        // existed; treat that as unknown rather than reading row[-1].
        birthYear: ix("birth_year") >= 0 ? row[ix("birth_year")] || null : null,
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
    buildPlayerList(rostered);

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
