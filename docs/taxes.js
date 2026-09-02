/*
 * taxes.js -- the Duty Day Tracker page.
 *
 * This file is the UI ONLY. Every number it shows comes from duty-days.js,
 * which is pure and tested; nothing here re-implements an allocation, a
 * duty-day rule or a rounding decision. If you find yourself about to do
 * arithmetic in this file, it belongs in the engine instead -- that
 * separation is what lets the engine move server-side later without the
 * numbers changing.
 *
 * State lives in localStorage under one key per season. There is no account
 * and no upload; see the privacy note on the page, which has to stay true.
 */

(function () {
  "use strict";

  const DD = window.DutyDays;
  const STORAGE_PREFIX = "bigleague-duty-days:";
  const THEME_KEY = "mlb-service-time-theme";

  let RULES = null; // config/tax/duty-day-rules.json
  let STATES = null; // config/tax/2026-states.json
  let CLUBS = []; // schedule index for the chosen season
  let season = null; // the working season object (the saved shape)
  let scheduleCache = new Map();

  // -----------------------------------------------------------------------
  // Small helpers
  // -----------------------------------------------------------------------

  const $ = (id) => document.getElementById(id);

  function money(n) {
    if (n === null || n === undefined) return "—";
    return n.toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0,
    });
  }

  function pct(n) {
    return (n * 100).toFixed(1) + "%";
  }

  function safeGet(key) {
    try {
      return localStorage.getItem(key);
    } catch (e) {
      return null;
    }
  }

  function safeSet(key, value) {
    try {
      localStorage.setItem(key, value);
      return true;
    } catch (e) {
      return false;
    }
  }

  function storageKey(year) {
    return STORAGE_PREFIX + year;
  }

  function save() {
    if (!season) return;
    season.updatedAt = new Date().toISOString();
    const ok = safeSet(storageKey(season.season), JSON.stringify(season));
    if (!ok) {
      note(
        "schedule-status",
        "This browser refused to save (private window, or storage is full). The page still works — use Export before you close it."
      );
    }
  }

  function note(id, text) {
    const el = $(id);
    if (el) el.textContent = text;
  }

  // -----------------------------------------------------------------------
  // Theme toggle. Same contract as app.js: with no explicit choice the
  // answer is "dark", because dark is the site's identity rather than
  // something inherited from the OS. Answering prefers-color-scheme here is
  // the double-click bug this button has already had once.
  // -----------------------------------------------------------------------

  function initTheme() {
    const root = document.documentElement;
    const stored = safeGet(THEME_KEY);
    if (stored) root.setAttribute("data-theme", stored);
    const btn = $("theme-toggle");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const current = root.getAttribute("data-theme") || "dark";
      const next = current === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      safeSet(THEME_KEY, next);
    });
  }

  // -----------------------------------------------------------------------
  // Populating the form
  // -----------------------------------------------------------------------

  function jurisdictionOptions(includeBlank) {
    const entries = Object.entries(STATES.jurisdictions).sort((a, b) =>
      a[1].name < b[1].name ? -1 : 1
    );
    let html = includeBlank ? '<option value="">—</option>' : "";
    for (const [code, j] of entries) {
      const tag = j.has_wage_income_tax === false ? " (no income tax)" : "";
      html += `<option value="${code}">${j.name}${tag}</option>`;
    }
    return html;
  }

  function dayTypeOptions() {
    return Object.entries(RULES.day_types)
      .map(([key, rule]) => {
        const counts = rule.counts_as_duty_day ? "" : " — not a duty day";
        return `<option value="${key}">${rule.label}${counts}</option>`;
      })
      .join("");
  }

  function fillForm() {
    const thisYear = new Date().getFullYear();
    $("in-season").innerHTML = [thisYear, thisYear - 1, thisYear - 2]
      .map((y) => `<option value="${y}">${y}</option>`)
      .join("");

    for (const id of ["in-home-state", "in-domicile", "in-spring-state"]) {
      $(id).innerHTML = jurisdictionOptions(true);
    }
    $("bulk-jurisdiction").innerHTML = jurisdictionOptions(true);
    $("bulk-type").innerHTML = dayTypeOptions();
  }

  // -----------------------------------------------------------------------
  // Schedules
  // -----------------------------------------------------------------------

  async function loadScheduleIndex(year) {
    try {
      const resp = await fetch(`data/schedules/${year}/index.json`, { cache: "no-cache" });
      if (!resp.ok) throw new Error(String(resp.status));
      const doc = await resp.json();
      CLUBS = doc.clubs || [];
      $("in-club").innerHTML =
        '<option value="">— choose a club —</option>' +
        CLUBS.map(
          (c) => `<option value="${c.team_id}">${c.team || c.team_id}</option>`
        ).join("") +
        '<option value="manual">Not listed / minor league — enter days myself</option>';
      note(
        "schedule-status",
        `${CLUBS.length} clubs loaded for ${year}. Pick yours and the page will propose a season you can correct.`
      );
    } catch (e) {
      // A season with no published schedule is a normal state, not an error.
      // Manual entry still works, which is also the MiLB path.
      CLUBS = [];
      $("in-club").innerHTML =
        '<option value="manual">Enter my days myself</option>';
      note(
        "schedule-status",
        `No published schedule for ${year} yet. You can still build a season by hand — choose “Start a blank season”, then use the bulk edit to lay in your stretches. This is also how minor league seasons work, since those schedules are not published here.`
      );
    }
  }

  async function loadClubSchedule(teamId) {
    if (scheduleCache.has(teamId)) return scheduleCache.get(teamId);
    const resp = await fetch(`data/schedules/${season.season}/${teamId}.json`, {
      cache: "no-cache",
    });
    if (!resp.ok) throw new Error(`schedule ${resp.status}`);
    const doc = await resp.json();
    scheduleCache.set(teamId, doc);
    return doc;
  }

  // -----------------------------------------------------------------------
  // Building a season
  // -----------------------------------------------------------------------

  function readProfileFromForm() {
    return {
      domicile: $("in-domicile").value || null,
      homeState: $("in-home-state").value || null,
      club: $("in-club").value || null,
      allocableIncome: Number($("in-income").value) || null,
      springState: $("in-spring-state").value || null,
      springStart: $("in-spring-start").value || null,
      springEnd: $("in-spring-end").value || null,
      payStructure: "salary",
    };
  }

  function writeProfileToForm(p) {
    if (!p) return;
    if (p.domicile) $("in-domicile").value = p.domicile;
    if (p.homeState) $("in-home-state").value = p.homeState;
    if (p.club) $("in-club").value = p.club;
    if (p.allocableIncome) $("in-income").value = p.allocableIncome;
    if (p.springState) $("in-spring-state").value = p.springState;
    if (p.springStart) $("in-spring-start").value = p.springStart;
    if (p.springEnd) $("in-spring-end").value = p.springEnd;
  }

  async function buildFromSchedule() {
    const profile = readProfileFromForm();
    season.profile = profile;

    if (!profile.club || profile.club === "manual") {
      startBlank();
      return;
    }

    let doc;
    try {
      doc = await loadClubSchedule(profile.club);
    } catch (e) {
      note("schedule-status", `Could not load that club's schedule (${e.message}). Start a blank season instead.`);
      return;
    }

    const games = (doc.games || []).map((g) => ({
      date: g.d,
      state: g.s,
      home: g.h === 1,
      spring: g.t === "S",
    }));
    const regular = games.filter((g) => !g.spring);
    const spring = games.filter((g) => g.spring);

    if (!regular.length) {
      note("schedule-status", "That club's file has no regular season games in it. Start a blank season instead.");
      return;
    }

    const homeState = profile.homeState || doc.home_state;
    const springState =
      profile.springState || (doc.spring_states && doc.spring_states[0]) || null;
    const springStart =
      profile.springStart || (spring.length ? spring[0].date : null);
    const springEnd =
      profile.springEnd || (spring.length ? spring[spring.length - 1].date : null);

    season.days = DD.proposeSeasonDays({
      games,
      homeState,
      springState,
      springStart,
      springEnd,
      seasonStart: regular[0].date,
      seasonEnd: regular[regular.length - 1].date,
    });

    const unresolved = season.days.filter((d) => !d.jurisdiction).length;
    note(
      "schedule-status",
      `Proposed ${season.days.length} days from ${doc.team || "the club"}'s ${season.season} schedule.` +
        (unresolved
          ? ` ${unresolved} of them are at a venue with no jurisdiction on file (an international series) — set those yourself.`
          : "")
    );
    save();
    render();
  }

  function startBlank() {
    const profile = readProfileFromForm();
    season.profile = profile;
    const year = season.season;
    // A blank season is still a calendar, so there is something to bulk-edit
    // against. Every day starts as offseason -- i.e. counting for nothing --
    // so an unedited blank season allocates nothing rather than something
    // wrong.
    season.days = DD.dateRange(`${year}-02-01`, `${year}-11-15`).map((date) => ({
      date,
      type: "offseason",
      jurisdiction: null,
      confirmed: false,
      source: "blank",
    }));
    note(
      "schedule-status",
      "Blank season created. Every day starts as offseason and counts for nothing — use the bulk edit to lay in spring training, homestands, road trips and IL stints."
    );
    save();
    render();
  }

  // -----------------------------------------------------------------------
  // Rendering
  // -----------------------------------------------------------------------

  function render() {
    if (!season || !season.days.length) {
      $("panel-days").hidden = true;
      $("panel-alloc").hidden = true;
      $("panel-export").hidden = true;
      return;
    }
    $("panel-days").hidden = false;
    $("panel-alloc").hidden = false;
    $("panel-export").hidden = false;
    renderConfirmSummary();
    renderCalendar();
    renderAllocation();
  }

  function renderConfirmSummary() {
    const totals = DD.dutyDayTotals(season.days, RULES);
    const confirmed = totals.total - totals.unconfirmed;
    const el = $("confirm-summary");
    const pctDone = totals.total ? Math.round((confirmed / totals.total) * 100) : 0;
    el.innerHTML = `
      <div class="confirm-bar" role="img" aria-label="${pctDone}% of duty days confirmed">
        <div class="confirm-fill" style="width:${pctDone}%"></div>
      </div>
      <p><b>${confirmed.toLocaleString()}</b> of <b>${totals.total.toLocaleString()}</b>
      duty days confirmed (${pctDone}%). ${totals.excluded.toLocaleString()} days are
      classified as not counting.</p>
      ${
        totals.unknownTypes.length
          ? `<p class="warn">Unrecognised day types in this season: ${totals.unknownTypes.join(", ")}. They are excluded from the count.</p>`
          : ""
      }
    `;
  }

  function renderCalendar() {
    const byMonth = new Map();
    for (const day of season.days) {
      const key = day.date.slice(0, 7);
      if (!byMonth.has(key)) byMonth.set(key, []);
      byMonth.get(key).push(day);
    }

    const html = [];
    for (const [month, days] of byMonth) {
      const label = new Date(month + "-01T00:00:00Z").toLocaleDateString("en-US", {
        month: "long",
        year: "numeric",
        timeZone: "UTC",
      });
      const counted = days.filter((d) => DD.countsAsDutyDay(RULES, d.type)).length;
      html.push(`<div class="month">
        <h3>${label} <span class="month-count">${counted} duty days</span></h3>
        <div class="days">`);
      for (const day of days) {
        const counts = DD.countsAsDutyDay(RULES, day.type);
        const rule = RULES.day_types[day.type];
        const cls = [
          "day",
          counts ? "counts" : "no-count",
          day.confirmed ? "confirmed" : "proposed",
        ].join(" ");
        const title = `${day.date} — ${rule ? rule.label : day.type}` +
          `${day.jurisdiction ? " in " + day.jurisdiction : ""}` +
          `${day.confirmed ? " (confirmed)" : " (proposed, not yet confirmed)"}`;
        html.push(
          `<button type="button" class="${cls}" data-date="${day.date}" title="${title}">
             <span class="dnum">${Number(day.date.slice(8, 10))}</span>
             <span class="djur">${day.jurisdiction || "·"}</span>
           </button>`
        );
      }
      html.push("</div></div>");
    }
    $("calendar").innerHTML = html.join("");
  }

  function renderAllocation() {
    const result = DD.allocate(season.days, {
      allocableIncome: season.profile.allocableIncome || 0,
      rules: RULES,
      states: STATES,
      domicile: season.profile.domicile,
    });

    $("alloc-tiles").innerHTML = `
      <div class="tile"><span class="tile-label">Duty days</span><span class="tile-value">${result.totalDutyDays.toLocaleString()}</span></div>
      <div class="tile"><span class="tile-label">Jurisdictions</span><span class="tile-value">${result.rows.length}</span></div>
      <div class="tile"><span class="tile-label">Salary allocated</span><span class="tile-value">${money(result.totalAllocated)}</span></div>
      <div class="tile ${result.liabilityIsPartial || result.liabilityUsesEstimatedRates ? "tile-partial" : ""}">
        <span class="tile-label">Rough state tax${result.liabilityIsPartial ? " (partial)" : ""}</span>
        <span class="tile-value">${money(result.estimatedLiability)}</span>
        <span class="tile-foot">before home-state credit</span>
      </div>`;

    const tbody = $("alloc-table").querySelector("tbody");
    tbody.innerHTML = result.rows
      .map((r) => {
        let statusText, statusClass;
        if (r.liabilityBasis === "estimated_rate") {
          statusText = "Rough estimate — rate not verified";
          statusClass = "st-estimate";
        } else if (r.liabilityWithheldBecause === "conflicting_sources") {
          statusText = "Sources disagreed — no estimate";
          statusClass = "st-unverified";
        } else if (r.liabilityWithheldBecause === "rules_unverified") {
          statusText = "Rules not verified — no estimate";
          statusClass = "st-unverified";
        } else if (r.liabilityWithheldBecause === "no_rate_on_file") {
          statusText = "No rate on file";
          statusClass = "st-unverified";
        } else if (r.liabilityWithheldBecause === "unknown_jurisdiction") {
          statusText = "Unknown jurisdiction";
          statusClass = "st-unknown";
        } else if (r.liabilityBasis === "no_wage_income_tax") {
          statusText = "No wage income tax";
          statusClass = "st-notax";
        } else {
          statusText = "Verified rate";
          statusClass = "st-ok";
        }
        return `<tr>
          <td>${r.name}${r.isDomicile ? ' <span class="pill">domicile</span>' : ""}</td>
          <td class="num">${r.dutyDays}</td>
          <td class="num">${pct(r.share)}</td>
          <td class="num">${money(r.allocatedIncome)}</td>
          <td class="num">${r.liability === null ? "—" : money(r.liability)}</td>
          <td><span class="status ${statusClass}">${statusText}</span></td>
        </tr>`;
      })
      .join("");

    const warnings = [];
    // Ordered deliberately: the resident credit is the single biggest reason
    // this sum is not a tax bill, so it goes first and appears whenever there
    // is a number at all.
    if (result.estimatedLiability > 0) {
      const dom = STATES.jurisdictions[season.profile.domicile] || null;
      // Whether the resident credit applies at all depends on the domicile,
      // and getting this backwards matters: a player domiciled in Florida or
      // Texas has no home-state tax for a credit to offset, so for him the
      // sum is much closer to the real total. Telling him it overstates would
      // be wrong in exactly the case most players are in.
      if (dom && dom.has_wage_income_tax === false) {
        warnings.push(
          `<b>This is a rough figure.</b> You are domiciled in ${dom.name}, which has no ` +
            `wage income tax — so there is no resident credit to offset these, and the ` +
            `column above is closer to a real state total than it would be otherwise. ` +
            `It still excludes city and local taxes, and applies one marginal rate per ` +
            `state rather than walking brackets.`
        );
      } else {
        warnings.push(
          `<b>This is a rough figure, and it is a sum before your home-state credit.</b> ` +
            `${dom ? dom.name : "Your domicile state"} generally credits you for tax paid ` +
            `to other states, so adding the column above overstates what you actually pay — ` +
            `often substantially. It also excludes city and local taxes, and applies one ` +
            `marginal rate rather than walking brackets.`
        );
      }
    }
    if (result.liabilityUsesEstimatedRates) {
      warnings.push(
        `${result.jurisdictionsOnEstimatedRates} of ${result.rows.length} jurisdictions used a rate compiled from secondary sources rather than read off that state's own guidance. Good enough to orient a conversation with your CPA; not good enough to file on.`
      );
    }
    if (result.liabilityIsPartial) {
      warnings.push(
        `<b>The estimated tax total is partial and understates your liability.</b> ` +
          `${result.jurisdictionsWithheld} of ${result.rows.length} jurisdictions have no verified rate on file, so no tax is estimated for them. ` +
          `Their duty days and allocated salary are still correct and are what your preparer needs.`
      );
    }
    if (result.unconfirmedDutyDays > 0) {
      warnings.push(
        `${result.unconfirmedDutyDays.toLocaleString()} duty days are still unconfirmed proposals from the schedule.`
      );
    }
    for (const row of result.rows) {
      for (const w of row.warnings) warnings.push(w);
    }
    $("alloc-warnings").innerHTML = warnings.length
      ? "<ul>" + warnings.map((w) => `<li>${w}</li>`).join("") + "</ul>"
      : "";

    buildWorksheet(result);
    return result;
  }

  // -----------------------------------------------------------------------
  // The CPA worksheet. Printed via the browser rather than a PDF library:
  // no dependency, no build step, and it stays readable as HTML too.
  // -----------------------------------------------------------------------

  function buildWorksheet(result) {
    const p = season.profile;
    const generated = new Date().toISOString().slice(0, 10);
    const rows = result.rows
      .map(
        (r) => `<tr>
          <td>${r.name} (${r.jurisdiction})</td>
          <td class="num">${r.dutyDays}</td>
          <td class="num">${(r.share * 100).toFixed(3)}%</td>
          <td class="num">${money(r.allocatedIncome)}</td>
          <td class="num">${r.liability === null ? "not estimated" : money(r.liability)}</td>
        </tr>`
      )
      .join("");

    const dayRows = season.days
      .filter((d) => DD.countsAsDutyDay(RULES, d.type))
      .map(
        (d) => `<tr>
          <td>${d.date}</td>
          <td>${d.jurisdiction || "—"}</td>
          <td>${RULES.day_types[d.type] ? RULES.day_types[d.type].label : d.type}</td>
          <td>${d.confirmed ? "confirmed" : "proposed"}</td>
        </tr>`
      )
      .join("");

    $("print-worksheet").innerHTML = `
      <h1>Duty day allocation worksheet — ${season.season}</h1>
      <p class="ws-meta">Prepared ${generated} · bigleagueservicetime.com/taxes.html</p>

      <div class="ws-disclaimer">
        <b>This is an estimate and an organizational tool, not tax advice.</b>
        It was produced by software, not by a tax professional. The duty-day
        classifications below include unconfirmed proposals where marked, and
        state rules used here have not all been verified against primary
        sources. It is intended as day-by-day support for a qualified preparer
        to review, correct and rely on at their own judgement — not as a
        filing position.
      </div>

      <h2>Method</h2>
      <p>Salary is allocated across jurisdictions in proportion to duty days:</p>
      <p class="ws-formula">(duty days in jurisdiction ÷ total duty days) × allocable salary</p>
      <table class="ws-kv">
        <tr><th>Allocable salary</th><td>${money(p.allocableIncome || 0)}</td></tr>
        <tr><th>Total duty days</th><td>${result.totalDutyDays}</td></tr>
        <tr><th>Days excluded as non-duty</th><td>${result.excludedDays}</td></tr>
        <tr><th>Duty days confirmed by the player</th><td>${result.totalDutyDays - result.unconfirmedDutyDays} of ${result.totalDutyDays}</td></tr>
        <tr><th>Stated domicile</th><td>${p.domicile || "not stated"}</td></tr>
        <tr><th>Club home state</th><td>${p.homeState || "not stated"}</td></tr>
        <tr><th>Duty-day ruleset</th><td>${RULES.version}</td></tr>
        <tr><th>State ruleset</th><td>${STATES.version} (tax year ${STATES.tax_year})</td></tr>
      </table>

      <h2>Allocation by jurisdiction</h2>
      <table class="ws-table">
        <thead><tr><th>Jurisdiction</th><th class="num">Duty days</th><th class="num">Share</th><th class="num">Allocated salary</th><th class="num">Estimated tax</th></tr></thead>
        <tbody>${rows}</tbody>
        <tfoot><tr><th>Total</th><th class="num">${result.totalDutyDays}</th><th class="num">100%</th><th class="num">${money(result.totalAllocated)}</th><th class="num">${money(result.estimatedLiability)}${result.liabilityIsPartial ? " (partial)" : ""}</th></tr></tfoot>
      </table>
      ${
        result.estimatedLiability > 0
          ? `<p class="ws-warn"><b>The tax column is a rough figure, not a liability.</b>
             It is a sum <i>before</i> any resident credit: the player's domicile state
             generally credits tax paid to other states, so adding these rows overstates
             the true total, often substantially. It excludes city and local taxes
             entirely, and applies a single marginal rate per jurisdiction rather than
             walking brackets.${
               result.liabilityUsesEstimatedRates
                 ? ` ${result.jurisdictionsOnEstimatedRates} of ${result.rows.length} rates were compiled from secondary sources rather than read off the jurisdiction's own guidance.`
                 : ""
             }</p>`
          : ""
      }
      ${
        result.liabilityIsPartial
          ? `<p class="ws-warn"><b>The tax column is also incomplete.</b> ${result.jurisdictionsWithheld} of ${result.rows.length} jurisdictions have no verified rate in this tool, so no tax was estimated for them and the total above understates the liability. Duty days and allocated salary are complete for every jurisdiction.</p>`
          : ""
      }

      <h2>Day-by-day support</h2>
      <p class="ws-meta">${result.totalDutyDays} duty days. Days classified as non-duty (offseason, non-team appearances) are omitted from this table but were excluded from the denominator.</p>
      <table class="ws-table ws-days">
        <thead><tr><th>Date</th><th>Jurisdiction</th><th>Classification</th><th>Basis</th></tr></thead>
        <tbody>${dayRows}</tbody>
      </table>
    `;
  }

  // -----------------------------------------------------------------------
  // CSV
  // -----------------------------------------------------------------------

  function csvEscape(v) {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  function download(filename, text, mime) {
    const blob = new Blob([text], { type: mime || "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function exportSummaryCsv() {
    const result = renderAllocation();
    const lines = [
      ["# Duty day allocation summary", season.season].map(csvEscape).join(","),
      ["# Estimate and organizational tool, not tax advice. Review with a qualified preparer."].map(csvEscape).join(","),
      ["# Formula: (duty days in jurisdiction / total duty days) * allocable salary"].map(csvEscape).join(","),
      ["# Allocable salary", season.profile.allocableIncome || 0].map(csvEscape).join(","),
      ["# Total duty days", result.totalDutyDays].map(csvEscape).join(","),
      "",
      ["jurisdiction", "name", "duty_days", "share", "allocated_salary", "estimated_tax", "status"].join(","),
    ];
    for (const r of result.rows) {
      lines.push(
        [
          r.jurisdiction,
          r.name,
          r.dutyDays,
          (r.share * 100).toFixed(3) + "%",
          r.allocatedIncome.toFixed(2),
          r.liability === null ? "not_estimated" : r.liability.toFixed(2),
          r.liabilityWithheldBecause || r.liabilityBasis || "",
        ]
          .map(csvEscape)
          .join(",")
      );
    }
    download(`duty-days-${season.season}-summary.csv`, lines.join("\n"), "text/csv;charset=utf-8");
  }

  function exportDaysCsv() {
    const lines = [
      ["# Day-by-day duty day support", season.season].map(csvEscape).join(","),
      ["# Estimate and organizational tool, not tax advice."].map(csvEscape).join(","),
      "",
      ["date", "jurisdiction", "classification", "counts_as_duty_day", "confirmed_by_player", "source"].join(","),
    ];
    for (const d of season.days) {
      const rule = RULES.day_types[d.type];
      lines.push(
        [
          d.date,
          d.jurisdiction || "",
          rule ? rule.label : d.type,
          DD.countsAsDutyDay(RULES, d.type) ? "yes" : "no",
          d.confirmed ? "yes" : "no",
          d.source || "",
        ]
          .map(csvEscape)
          .join(",")
      );
    }
    download(`duty-days-${season.season}-days.csv`, lines.join("\n"), "text/csv;charset=utf-8");
  }

  // -----------------------------------------------------------------------
  // Wiring
  // -----------------------------------------------------------------------

  function loadSeason(year) {
    const saved = safeGet(storageKey(year));
    if (saved) {
      try {
        season = DD.migrateSeason(JSON.parse(saved));
        writeProfileToForm(season.profile);
        note(
          "schedule-status",
          `Loaded your saved ${year} season (${season.days.length} days). Everything stays in this browser.`
        );
        render();
        return;
      } catch (e) {
        /* fall through to a fresh season */
      }
    }
    season = DD.emptySeason(year);
    render();
  }

  function wire() {
    $("in-season").addEventListener("change", async (e) => {
      const year = Number(e.target.value);
      await loadScheduleIndex(year);
      loadSeason(year);
    });

    $("btn-build").addEventListener("click", buildFromSchedule);
    $("btn-blank").addEventListener("click", startBlank);

    for (const id of [
      "in-income",
      "in-domicile",
      "in-home-state",
      "in-spring-state",
      "in-spring-start",
      "in-spring-end",
    ]) {
      $(id).addEventListener("change", () => {
        if (!season) return;
        season.profile = { ...season.profile, ...readProfileFromForm() };
        save();
        if (season.days.length) render();
      });
    }

    $("btn-bulk-apply").addEventListener("click", () => {
      const from = $("bulk-from").value;
      const to = $("bulk-to").value;
      if (!from || !to || from > to) {
        note("schedule-status", "Pick a start and end date for the bulk edit, with the start first.");
        return;
      }
      season.days = DD.applyStretch(season.days, {
        from,
        to,
        type: $("bulk-type").value,
        jurisdiction: $("bulk-jurisdiction").value || null,
      });
      save();
      render();
    });

    $("btn-confirm-all").addEventListener("click", () => {
      season.days = season.days.map((d) =>
        d.confirmed ? d : { ...d, confirmed: true, source: d.source + "+bulk_confirm" }
      );
      save();
      render();
    });

    // Clicking a day toggles its confirmation. The smallest possible
    // confirm-or-correct gesture, which is the one the brief asks for.
    $("calendar").addEventListener("click", (e) => {
      const btn = e.target.closest(".day");
      if (!btn) return;
      const date = btn.dataset.date;
      season.days = season.days.map((d) =>
        d.date === date ? { ...d, confirmed: !d.confirmed, source: d.source + "+click" } : d
      );
      save();
      render();
    });

    $("btn-print").addEventListener("click", () => window.print());
    $("btn-csv-summary").addEventListener("click", exportSummaryCsv);
    $("btn-csv-days").addEventListener("click", exportDaysCsv);

    $("btn-export-json").addEventListener("click", () => {
      download(
        `duty-days-${season.season}.json`,
        JSON.stringify(season, null, 2),
        "application/json"
      );
    });

    $("in-import").addEventListener("change", (e) => {
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        try {
          const incoming = DD.migrateSeason(JSON.parse(reader.result));
          if (!incoming || !Array.isArray(incoming.days)) throw new Error("not a season file");
          season = incoming;
          $("in-season").value = String(season.season);
          writeProfileToForm(season.profile);
          save();
          render();
          note("schedule-status", `Imported a ${season.season} season with ${season.days.length} days.`);
        } catch (err) {
          note("schedule-status", `That file could not be read as a season (${err.message}).`);
        }
      };
      reader.readAsText(file);
      e.target.value = "";
    });

    $("btn-reset").addEventListener("click", () => {
      if (!confirm(`Delete your saved ${season.season} season from this browser? This cannot be undone, and it is not stored anywhere else.`)) {
        return;
      }
      try {
        localStorage.removeItem(storageKey(season.season));
      } catch (e) {}
      season = DD.emptySeason(season.season);
      render();
      note("schedule-status", "Season deleted from this browser.");
    });
  }

  async function init() {
    initTheme();
    try {
      const [rules, states] = await Promise.all([
        // Published copies of config/tax/. config/ sits outside docs/ and so
        // is not served; publish_config() in update_service_time.py copies it
        // to docs/data/config/ on every run.
        fetch("data/config/tax/duty-day-rules.json").then((r) => r.json()),
        fetch("data/config/tax/2026-states.json").then((r) => r.json()),
      ]);
      RULES = rules;
      STATES = states;
    } catch (e) {
      note(
        "schedule-status",
        "Could not load the tax rules files, so this page cannot compute anything. That is a deployment problem, not something you did."
      );
      return;
    }

    fillForm();
    wire();
    const year = Number($("in-season").value);
    await loadScheduleIndex(year);
    loadSeason(year);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
