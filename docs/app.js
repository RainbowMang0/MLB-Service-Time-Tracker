(() => {
  "use strict";

  // The compact index is what the browser downloads: 0.21 MB against 2.84 MB
  // for the full database, because it drops fields the table never reads,
  // recomputes everything derivable from the day count, and stores rows as
  // arrays with team/position lookup tables instead of repeating key and team
  // strings 5,568 times.
  //
  // service_time.json is still the database and still published -- it is the
  // pipeline's own source of truth on the next run, and anyone who wants the
  // full records can fetch it. It is just no longer on the page's critical
  // path. The fallback below keeps older deployments working.
  const DATA_URL = "data/index.json";
  const FULL_DATA_URL = "data/service_time.json";
  // Per-player season detail, sharded by `id % 64` (see write_profiles() in
  // scripts/update_service_time.py). Opening a profile fetches one ~12 KB
  // file instead of the ~0.9 MB the whole breakdown would cost, so the table
  // load is unaffected for the visitors who never open one.
  const PROFILE_SHARDS = 64;
  const profileShardCache = new Map();

  const FULL_YEAR_DAYS = 172;

  // --- club identity -------------------------------------------------------
  // Keyed by MLB team id, which never changes; the NAMES do. The profile
  // shards carry the club as it was called at the time ("Cleveland Indians",
  // "Oakland Athletics") while the table's index carries the current name
  // ("Cleveland Guardians", "Athletics"), so both spellings are listed and a
  // renamed club keeps its color in a 2015 season row.
  //
  // Color is identity, never the only signal for anything: the club is named
  // in text beside the stripe, several clubs share a navy, and nothing here
  // encodes a quantity.
  const CLUBS = {
    108: { c: "#B8001E", n: ["Los Angeles Angels", "Los Angeles Angels of Anaheim"] },
    109: { c: "#CB0033", n: ["Arizona Diamondbacks"] },
    110: { c: "#D43400", n: ["Baltimore Orioles"] },
    111: { c: "#F00032", n: ["Boston Red Sox"] },
    112: { c: "#2264F9", n: ["Chicago Cubs"] },
    113: { c: "#CC001D", n: ["Cincinnati Reds"] },
    114: { c: "#325C91", n: ["Cleveland Guardians", "Cleveland Indians"] },
    115: { c: "#772CE9", n: ["Colorado Rockies"] },
    116: { c: "#EA5400", n: ["Detroit Tigers"] },
    117: { c: "#FF942D", n: ["Houston Astros"] },
    118: { c: "#068BFF", n: ["Kansas City Royals"] },
    119: { c: "#0088F1", n: ["Los Angeles Dodgers"] },
    120: { c: "#A00000", n: ["Washington Nationals"] },
    121: { c: "#055FDB", n: ["New York Mets"] },
    133: { c: "#006E61", n: ["Athletics", "Oakland Athletics"] },
    134: { c: "#EEA900", n: ["Pittsburgh Pirates"] },
    135: { c: "#8B5420", n: ["San Diego Padres"] },
    136: { c: "#009393", n: ["Seattle Mariners"] },
    137: { c: "#FF5C1F", n: ["San Francisco Giants"] },
    138: { c: "#E0003A", n: ["St. Louis Cardinals"] },
    139: { c: "#8ECAFF", n: ["Tampa Bay Rays"] },
    140: { c: "#5196FF", n: ["Texas Rangers"] },
    141: { c: "#62A6FF", n: ["Toronto Blue Jays"] },
    142: { c: "#3680DC", n: ["Minnesota Twins"] },
    143: { c: "#FF102A", n: ["Philadelphia Phillies"] },
    144: { c: "#EE0048", n: ["Atlanta Braves"] },
    145: { c: "#6B6862", n: ["Chicago White Sox"] },
    146: { c: "#00B2F7", n: ["Miami Marlins"] },
    147: { c: "#0037C4", n: ["New York Yankees"] },
    158: { c: "#FFC900", n: ["Milwaukee Brewers"] },
  };

  const CLUB_BY_NAME = (() => {
    const map = new Map();
    Object.keys(CLUBS).forEach((id) => {
      CLUBS[id].n.forEach((name) => map.set(name, CLUBS[id].c));
    });
    return map;
  })();

  // The dark-theme twin. Small now: the palette below already sits in a
  // mid-lightness band, where it used to be full of near-black navies that
  // needed a large lift to be seen at all. Lifting these as hard would wash
  // them out instead.
  function lighten(hex, amount) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    const mix = (c) => Math.round(c + (255 - c) * amount);
    const r = mix((n >> 16) & 255);
    const g = mix((n >> 8) & 255);
    const b = mix(n & 255);
    return `#${((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1)}`;
  }

  const clubStyle = (color) =>
    color ? `--club:${color};--club-d:${lighten(color, 0.16)}` : "";

  // Set from the payload. Super Two is no longer guessed at in the browser:
  // it depends on where a player ranks in the league-wide 2-3 year class, so
  // the pipeline computes it (see scripts/super_two.py) and ships a flag.
  let superTwoCutoff = null;

  /**
   * Rebuild the player objects the rest of this file expects.
   *
   * Everything here is a pure function of the day count, verified against all
   * 5,568 records at build time with zero mismatches -- so shipping these
   * fields would be shipping the same information twice.
   */
  function hydrate(payload) {
    const teams = payload.teams || [];
    const positions = payload.positions || [];
    return (payload.players || []).map((row) => {
      const [id, name, teamIx, posIx, days, on40, missing, superTwoFlag] = row;
      const years = Math.floor(days / FULL_YEAR_DAYS);
      const rem = days % FULL_YEAR_DAYS;
      const frac = years + rem / FULL_YEAR_DAYS;
      // Older payloads have no flag; fall back to the historical heuristic so
      // a deployment that has not regenerated yet still renders something
      // sensible rather than marking everyone ineligible.
      const superTwo =
        superTwoFlag === undefined
          ? frac >= 2 && frac < 3 && rem >= 86
          : superTwoFlag === 1;
      return {
        id,
        name,
        team: teams[teamIx] || null,
        position: positions[posIx] || null,
        service_time: `${years}.${String(rem).padStart(3, "0")}`,
        service_days_total: days,
        free_agent_eligible: frac >= 6,
        super_two_candidate: superTwo,
        arbitration_eligible: frac >= 3 || superTwo,
        on_40_man: on40 === 1,
        // missing_seasons: 0 complete, -1 incomplete by an unknown amount
        // (a record written before the field existed).
        history_complete: missing === 0,
        missing_seasons: missing > 0 ? missing : 0,
        last_updated: payload.generated_at ? payload.generated_at.slice(0, 10) : null,
      };
    });
  }

  // Embedded fallback so the page still shows something meaningful if
  // opened directly from disk (file://) where fetch() of a local JSON file
  // is blocked by the browser, or if data/service_time.json hasn't been
  // generated yet. The real deployed site (served over http/https by
  // GitHub Pages or any static host) will always prefer the live fetch.
  const FALLBACK_DATA = {
    generated_at: null,
    source: "EMBEDDED FALLBACK SAMPLE (fetch of data/service_time.json failed)",
    disclaimer:
      "Could not load data/service_time.json, so this page is showing a small embedded sample instead. " +
      "Service time figures are always estimates derived from public transaction records, never official figures.",
    player_count: 3,
    players: [
      {
        id: 1, name: "Sample Player A", team: "Sample Team", position: "SP",
        service_time: "3.045", service_days_total: 688,
        free_agent_eligible: false, arbitration_eligible: true, super_two_candidate: false,
        on_40_man: true, last_updated: "2026-08-15",
      },
      {
        id: 2, name: "Sample Player B", team: "Sample Team", position: "OF",
        service_time: "6.010", service_days_total: 1042,
        free_agent_eligible: true, arbitration_eligible: true, super_two_candidate: false,
        on_40_man: true, last_updated: "2026-08-15",
      },
      {
        id: 3, name: "Sample Player C", team: "Another Team", position: "C",
        service_time: "1.020", service_days_total: 192,
        free_agent_eligible: false, arbitration_eligible: false, super_two_candidate: false,
        on_40_man: false, last_updated: "2026-08-15",
      },
    ],
  };

  let allPlayers = [];
  let sortKey = "service_days_total";
  let sortDir = "desc";

  // The dataset grows from ~1,400 rostered players to ~5,300 once the
  // historical backfill lands. Rendering that many <tr> nodes at once makes
  // the page unusable on an iPad, so the table is paged.
  const PAGE_SIZE = 100;
  let currentPage = 1;
  let coverageStartYear = 2009;
  let lastFocused = null;

  const el = (id) => document.getElementById(id);

  const fetchJson = (url) =>
    fetch(url, { cache: "no-store" }).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });

  // Player and team names come from an external API and are injected via
  // innerHTML, so escape them rather than trusting the feed.
  const esc = (value) =>
    String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  function classify(p) {
    // Eligibility is a statement about a player's CURRENT contractual
    // situation, so it only means anything for someone on a 40-man roster.
    // "Free agency eligible" applied to a man who last played in 2013 is a
    // category error -- he is not eligible for anything, he is finished. All
    // the flag really says about him is that he accrued 6.000+ years, which
    // the service-time column already shows.
    if (!p.on_40_man) {
      return { label: "Not on a roster", cls: "badge-neutral" };
    }
    // No reconstructable history at all -- calling these players
    // "Pre-Arbitration" is flatly wrong (Arthur Rhodes pitched 20 seasons).
    if (p.service_days_total === 0 && p.history_complete === false) {
      return { label: "Unknown", cls: "badge-neutral" };
    }
    // On a 40-man, never accrued a day. "Pre-Arbitration" implied a clock
    // that has not started: these are prospects added to the 40-man to
    // protect them from the Rule 5 draft, who have never been on an active
    // major league roster.
    if (p.service_days_total === 0) {
      return { label: "Yet to debut", cls: "badge-neutral" };
    }
    if (p.free_agent_eligible) return { label: "Free Agent Eligible", cls: "badge-good" };
    if (p.super_two_candidate) {
      return {
        label: superTwoCutoff ? `Super Two track` : "Possible Super Two",
        cls: "badge-serious",
      };
    }
    if (p.arbitration_eligible) return { label: "Arbitration Eligible", cls: "badge-warning" };
    return { label: "Pre-Arbitration", cls: "badge-neutral" };
  }

  // A player who has never accrued a day has nothing this site measures. He
  // is kept in the database -- he is really on a 40-man and will appear the
  // day he debuts -- but he is off the table unless asked for, because a
  // page of 0.000 rows is the first thing an ascending sort shows and it
  // reads as a defect rather than as a squad of prospects.
  const hasService = (p) => Number(p.service_days_total) > 0;

  function statusMatches(p, filterValue) {
    if (filterValue === "no-service") return !hasService(p);
    if (!hasService(p)) return false;
    if (!filterValue) return true;
    if (filterValue === "not-rostered") return !p.on_40_man;
    // Every other status is an eligibility one, and those apply to current
    // players only -- see classify().
    if (!p.on_40_man) return false;
    if (filterValue === "fa") return !!p.free_agent_eligible;
    if (filterValue === "super-two") return !!p.super_two_candidate;
    if (filterValue === "arb") return !!p.arbitration_eligible && !p.free_agent_eligible;
    if (filterValue === "pre-arb") {
      return !p.free_agent_eligible && !p.arbitration_eligible && !p.super_two_candidate;
    }
    return true;
  }

  // history_complete is false when a player debuted before the transaction
  // feed begins (2009). Those figures are a floor, not an estimate, and the
  // table says so rather than publishing a number known to be low.
  const isComplete = (p) => p.history_complete !== false;

  function historyMatches(p, filterValue) {
    if (!filterValue) return true;
    if (filterValue === "complete") return isComplete(p);
    if (filterValue === "partial") return !isComplete(p);
    return true;
  }

  function renderStatTiles(players) {
    const total = players.length;
    const rostered = players.filter((p) => p.on_40_man);
    const current = rostered.length;
    const previous = total - current;
    // Counted over rostered players only. Across the whole database these
    // read absurdly: 1,475 "free agency eligible" against 1,359 players on a
    // 40-man, because 1,174 of them are retired men who crossed 6.000 years
    // years ago. The tiles sat above a table that could be filtered to
    // current players, so the header described a different population than
    // the rows under it.
    const fa = rostered.filter((p) => p.free_agent_eligible).length;
    const arb = rostered.filter((p) => p.arbitration_eligible && !p.free_agent_eligible).length;
    const superTwo = rostered.filter((p) => p.super_two_candidate).length;
    const partial = players.filter((p) => !isComplete(p)).length;

    // Census first, and quietly: these describe the whole database, which is
    // mostly retired players, and they used to sit in tiles identical to the
    // eligibility ones. Seven equal tiles meant three different kinds of fact
    // all shouting at the same volume.
    const census = el("stat-census");
    if (census) {
      const n = (v) => v.toLocaleString();
      // The yet-to-debut count is stated because the table hides those rows.
      // Without it the census would say 1,360 are on a 40-man while the table
      // paged through 1,333, which is the same kind of silent mismatch that
      // made the eligibility tiles look like an arithmetic bug.
      const yetToDebut = players.filter((p) => !hasService(p)).length;
      census.innerHTML =
        `<b>${n(total)}</b> players tracked` +
        `<span class="sep">·</span><b>${n(current)}</b> on a 40-man roster` +
        `<span class="sep">·</span><b>${n(previous)}</b> previously rostered` +
        (yetToDebut
          ? `<span class="sep">·</span><b>${n(yetToDebut)}</b> yet to accrue a day` +
            ` <span class="census-note">(not listed below)</span>`
          : "");
    }

    const tiles = [
      {
        label: "Free agency eligible",
        value: fa,
        accent: "accent-good",
        title: "Players currently on a 40-man roster with 6.000+ years of service. "
          + "Players who have left the majors are not counted: eligibility is a "
          + "statement about a current contract, and most of this database is "
          + "retired players.",
      },
      {
        label: "Arbitration eligible",
        value: arb,
        accent: "accent-warning",
        title: "Players currently on a 40-man roster with 3.000+ years of service "
          + "(or on the Super Two track), not yet at 6.000.",
      },
      {
        label: superTwoCutoff
          ? `Super Two track (≥ ${superTwoCutoff.cutoff})`
          : "Possible Super Two",
        // rostered-only, same reason as the two tiles above
        value: superTwo,
        accent: "accent-serious",
        title: superTwoCutoff
          ? `The Super Two cutoff after the ${superTwoCutoff.season} season was ` +
            `${superTwoCutoff.cutoff}, measured from this project's own data: ` +
            `${superTwoCutoff.class_size} players finished that year between 2.000 and ` +
            `3.000 years, and the top 22% of them (${superTwoCutoff.qualifying_count}) ` +
            `qualified for arbitration a year early. Listed here are players at or above ` +
            `that line who have also accrued 86+ days in ` +
            `${superTwoCutoff.qualifying_season || superTwoCutoff.season}, the second ` +
            `half of the rule. While that season is still being played a player can ` +
            `cross 86 days and join this list. Next offseason's cutoff will differ — it ` +
            `has moved between 2.126 and 2.136 over the last four years — so a player ` +
            `near the line could land either side.`
          : "",
      },
    ];
    if (partial > 0) {
      tiles.push({
        label: "Incomplete history",
        value: partial,
        // Muted, not critical red. This is a statement about how far back the
        // transaction feed reaches, not an error in the figures.
        accent: "accent-muted",
        title: "Players whose first recorded roster move is later than their debut. "
          + "Those seasons are presumed rather than read, so the figure is less "
          + "certain — and a floor if any of the career predates the data.",
      });
    }

    el("stat-tiles").innerHTML = tiles
      .map(
        (t) => `
      <div class="stat-tile ${t.accent}"${t.title ? ` title="${esc(t.title)}"` : ""}>
        <div class="value">${t.value.toLocaleString()}</div>
        <div class="label">${t.label}</div>
      </div>`
      )
      .join("");
  }

  function populateTeamFilter(players) {
    // Only clubs someone is actually rostered on. A retired player's stored
    // club is his last known one, which is stale by construction -- offering
    // it as a filter would imply a roster he is not on.
    const teams = Array.from(
      new Set(players.filter((p) => p.on_40_man).map((p) => p.team).filter(Boolean))
    ).sort();
    const select = el("team-filter");
    const current = select.value;
    select.innerHTML =
      '<option value="">All teams</option>' +
      teams.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join("");
    select.value = current;
  }

  function getFilteredSorted() {
    const q = el("search-input").value.trim().toLowerCase();
    const team = el("team-filter").value;
    const status = el("status-filter").value;
    const rosterFilter = el("roster-filter").value;
    const historyFilter = el("history-filter") ? el("history-filter").value : "";

    let rows = allPlayers.filter((p) => {
      if (q && !(`${p.name} ${p.team}`.toLowerCase().includes(q))) return false;
      if (team && p.team !== team) return false;
      if (!statusMatches(p, status)) return false;
      if (rosterFilter === "current" && !p.on_40_man) return false;
      if (rosterFilter === "previous" && p.on_40_man) return false;
      if (!historyMatches(p, historyFilter)) return false;
      return true;
    });

    rows.sort((a, b) => {
      let av = a[sortKey];
      let bv = b[sortKey];
      if (typeof av === "boolean") { av = av ? 1 : 0; bv = bv ? 1 : 0; }
      if (typeof av === "string") { av = av.toLowerCase(); bv = (bv || "").toLowerCase(); }
      if (av < bv) return sortDir === "asc" ? -1 : 1;
      if (av > bv) return sortDir === "asc" ? 1 : -1;
      return 0;
    });

    return rows;
  }

  function renderTable() {
    const rows = getFilteredSorted();
    const tbody = el("players-tbody");

    if (rows.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" class="empty-state">No players match these filters.</td></tr>`;
      renderPagination(0, 0);
      return;
    }

    const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    if (currentPage > pageCount) currentPage = pageCount;
    if (currentPage < 1) currentPage = 1;
    const start = (currentPage - 1) * PAGE_SIZE;
    const pageRows = rows.slice(start, start + PAGE_SIZE);

    tbody.innerHTML = pageRows
      .map((p) => {
        const status = classify(p);
        // How much of the career is missing, measured per player. A bare
        // "partial" flag keyed to a cutoff year said nothing about the size
        // of the gap -- and flagged plenty of complete figures, since the
        // feed does carry some pre-2009 history.
        const gap = Number(p.missing_seasons) || 0;
        const partial = !isComplete(p)
          ? ` <abbr class="partial-flag" title="The first roster move on record for this player is ${
              gap ? `${gap} season${gap === 1 ? "" : "s"} after his debut` : "later than his debut"
            }. Those seasons are filled in by presuming he was on a roster from his debut rather than read from transactions, so this figure is less certain than most — and a floor if any of his career predates the data. Open his profile to see which seasons.">${
              gap ? `${gap} presumed` : "partial"
            }</abbr>`
          : "";
        // A player whose whole career predates the transaction feed has no
        // visible days at all. "0.000" would read as a measured figure when
        // it actually means "no data" -- Alan Embree pitched 16 seasons and
        // still computes to zero. Show nothing rather than a false zero.
        // A complete-history player at 0.000 really did accrue nothing (a
        // 40-man prospect who never reached an active roster), so that one
        // stays.
        const noData = p.service_days_total === 0 && !isComplete(p);

        // The meter. Scaled 0 -> 6.000, the point at which the clock stops
        // mattering, with the fill colored by the same status shown one
        // column over so the bar and the badge cannot disagree.
        const pct = Math.max(
          0,
          Math.min(1, p.service_days_total / (6 * FULL_YEAR_DAYS))
        ) * 100;
        const fillCls = !p.on_40_man
          ? ""
          : p.free_agent_eligible
          ? "f-good"
          : p.super_two_candidate
          ? "f-serious"
          : p.arbitration_eligible
          ? "f-warning"
          : "";
        const [years, days] = String(p.service_time || "0.000").split(".");
        const serviceCell = noData
          ? `<abbr class="no-data" title="This player's entire career predates ${coverageStartYear}, when the transaction feed begins, so no service time can be reconstructed. This is missing data, not zero service time.">no data</abbr>`
          : `<span class="svc">
               <span class="svc-num"><span class="svc-years">${esc(years)}</span><span class="svc-days">.${esc(days)}</span></span>
               <span class="svc-track" style="--pct:${pct.toFixed(1)}"><span class="svc-fill ${fillCls}"></span></span>
             </span>`;

        // A club is only published for a rostered player (a retired man's
        // stored club is stale by construction), so the stripe appears on
        // exactly the rows that assert one.
        const club = p.on_40_man ? CLUB_BY_NAME.get(p.team) : null;
        return `
        <tr>
          <td class="player-name" style="${clubStyle(club)}"><button type="button" class="player-link" data-player-id="${esc(
            p.id
          )}">${esc(p.name) || "—"}</button>${partial}</td>
          <td>${
            p.on_40_man
              ? `${club ? `<span class="club-dot" style="${clubStyle(club)}"></span>` : ""}${esc(p.team) || "—"}`
              : "—"
          }</td>
          <td>${esc(p.position) || "—"}</td>
          <td class="num-col svc-col">${serviceCell}</td>
          <td><span class="badge ${status.cls}">${status.label}</span></td>
        </tr>`;
      })
      .join("");

    renderPagination(rows.length, pageCount);
  }

  function renderPagination(totalRows, pageCount) {
    const status = el("page-status");
    const prev = el("page-prev");
    const next = el("page-next");
    if (!status || !prev || !next) return;

    if (totalRows === 0) {
      status.textContent = "";
      prev.disabled = true;
      next.disabled = true;
      return;
    }

    const start = (currentPage - 1) * PAGE_SIZE + 1;
    const end = Math.min(currentPage * PAGE_SIZE, totalRows);
    status.textContent = `${start}–${end} of ${totalRows} players · page ${currentPage} of ${pageCount}`;
    prev.disabled = currentPage <= 1;
    next.disabled = currentPage >= pageCount;
  }

  function wirePagination() {
    const prev = el("page-prev");
    const next = el("page-next");
    if (!prev || !next) return;
    prev.addEventListener("click", () => {
      if (currentPage > 1) {
        currentPage--;
        renderTable();
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    });
    next.addEventListener("click", () => {
      currentPage++;
      renderTable();
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  // The service-time column sorts by its numeric day count, so its data-key
  // and the active sort key are deliberately different names. Comparing them
  // raw meant the column the table was ALREADY sorted by lost its marker at
  // load -- the one column that most needs it.
  const sortKeyFor = (key) => (key === "service_time" ? "service_days_total" : key);

  function updateSortIndicators() {
    document.querySelectorAll("#players-table thead th[data-key]").forEach((th) => {
      if (sortKeyFor(th.dataset.key) === sortKey) {
        th.setAttribute("aria-sort", sortDir === "asc" ? "ascending" : "descending");
      } else {
        th.removeAttribute("aria-sort");
      }
    });
  }

  function wireSorting() {
    document.querySelectorAll("#players-table thead th[data-key]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.key;
        // Sort service_time by its numeric day-count field for correctness.
        const effectiveKey = sortKeyFor(key);
        if (sortKey === effectiveKey) {
          sortDir = sortDir === "asc" ? "desc" : "asc";
        } else {
          sortKey = effectiveKey;
          sortDir = key === "name" || key === "team" ? "asc" : "desc";
        }
        currentPage = 1;
        updateSortIndicators();
        renderTable();
      });
    });
  }

  function wireFilters() {
    // Any filter change invalidates the current page number -- landing the
    // user on "page 7 of 2" after narrowing a search is disorienting.
    const onFilterChange = () => {
      currentPage = 1;
      renderTable();
    };
    ["search-input", "team-filter", "status-filter", "roster-filter", "history-filter"]
      .forEach((id) => {
        const node = el(id);
        if (!node) return;
        node.addEventListener("input", onFilterChange);
        node.addEventListener("change", onFilterChange);
      });
  }

  function wireThemeToggle() {
    const root = document.documentElement;
    const stored = localStorageSafeGet("mlb-service-time-theme");
    if (stored) root.setAttribute("data-theme", stored);
    el("theme-toggle").addEventListener("click", () => {
      const current = root.getAttribute("data-theme") === "dark" ? "dark" : "light";
      const next = current === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      localStorageSafeSet("mlb-service-time-theme", next);
    });
  }

  // NOTE: browser localStorage is used here (not inside a sandboxed
  // artifact preview) purely for remembering the light/dark toggle across
  // visits to the deployed site. Wrapped defensively in case it's
  // unavailable (privacy mode, etc.).
  function localStorageSafeGet(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function localStorageSafeSet(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* ignore */ }
  }

  // --- player profile -----------------------------------------------------

  // How a season's days are known. After the carry-in rule (a player is
  // presumed rostered from his debut until the feed says otherwise) the
  // seasons behind a total differ a lot in confidence, and a profile that
  // presented them as equally solid would be overstating what this project
  // can actually see.
  const SOURCE_LABELS = {
    read: {
      label: "From transactions",
      short: "Read",
      cls: "src-read",
      title: "Roster moves recorded in this season drove the calculation directly.",
    },
    carry: {
      label: "Carried forward",
      short: "Carried",
      cls: "src-carry",
      title:
        "No roster moves were recorded this season, so his status was carried " +
        "forward from an earlier one. Changing clubs produces a transaction, " +
        "so a silent season almost always means he stayed put.",
    },
    presumed: {
      label: "Presumed from debut",
      short: "Presumed",
      cls: "src-presumed",
      title:
        "This season is earlier than the first transaction of any kind on " +
        "record for him. He is presumed to have been on a roster from his " +
        "debut. This is the least certain kind of season and the one counted " +
        "by the missing-seasons flag.",
    },
  };

  const formatDays = (days) => {
    const years = Math.floor(days / FULL_YEAR_DAYS);
    return `${years}.${String(days % FULL_YEAR_DAYS).padStart(3, "0")}`;
  };

  function loadProfileShard(playerId) {
    const bucket = String(playerId % PROFILE_SHARDS).padStart(2, "0");
    if (!profileShardCache.has(bucket)) {
      profileShardCache.set(
        bucket,
        fetchJson(`data/profiles/${bucket}.json`).catch(() => null)
      );
    }
    return profileShardCache.get(bucket);
  }

  function seasonNote(season) {
    // Explains a season whose credited days differ from the days he was
    // actually on a roster. Without this the numbers look arbitrary: a
    // player rostered all year reads 172 against 188 days, and 2020 reads
    // 172 against 66.
    const raw = Number(season.raw) || 0;
    const pro = season.pro == null ? raw : Number(season.pro);
    const credited = Number(season.d) || 0;
    if (pro !== raw) {
      return `${raw} days on a roster, scaled to ${pro} because the ${season.y} season was shortened`;
    }
    if (raw > credited && credited === FULL_YEAR_DAYS) {
      return `${raw} days on a roster; a season credits at most ${FULL_YEAR_DAYS}`;
    }
    return "";
  }

  /**
   * The career at a glance: one bar per season, height by days credited,
   * colored by club, hatched where the season is presumed rather than read.
   *
   * The season table below it is the record and it opens as twenty-plus rows
   * of digits. Verlander's ten hatched bars followed by eleven solid ones is
   * the argument for the source column made visible in one look -- and the
   * two crossing markers say where the total actually changed what he was
   * entitled to.
   */
  function careerStrip(seasons, teams) {
    if (!seasons.length) return "";

    let running = 0;
    let markedArb = false;
    let markedFa = false;

    const cells = seasons.map((season) => {
      const days = Number(season.d) || 0;
      running += days;

      let cross = "";
      if (!markedArb && running >= 3 * FULL_YEAR_DAYS) {
        markedArb = true;
        cross = `<span class="cs-cross">3.000</span>`;
      }
      // Both can land in the same season for a player credited a long
      // presumed stretch; free agency is the one worth showing.
      if (!markedFa && running >= 6 * FULL_YEAR_DAYS) {
        markedFa = true;
        cross = `<span class="cs-cross">6.000</span>`;
      }

      const clubIds = season.t || [];
      const club = clubIds.length
        ? CLUB_BY_NAME.get(teams[String(clubIds[clubIds.length - 1])]) ||
          (CLUBS[clubIds[clubIds.length - 1]] || {}).c
        : null;
      const clubNames = clubIds
        .map((id) => teams[String(id)] || `Club ${id}`)
        .join(", ");

      const src = SOURCE_LABELS[season.src] || SOURCE_LABELS.read;
      const cls = [
        "cs-season",
        days === 0 ? "cs-zero" : "",
        season.src === "presumed" ? "cs-presumed" : "",
      ].filter(Boolean).join(" ");

      const title = `${season.y} · ${clubNames || "no club identified"} · ` +
        `${days} day${days === 1 ? "" : "s"} · ${src.label} · running total ${formatDays(running)}`;

      return `<span class="${cls}" style="${clubStyle(club)}" title="${esc(title)}">
        ${cross}
        <span class="cs-bar" style="--h:${((days / FULL_YEAR_DAYS) * 100).toFixed(1)}"></span>
        <span class="cs-year">${esc(String(season.y).slice(2))}</span>
      </span>`;
    }).join("");

    const anyPresumed = seasons.some((s) => s.src === "presumed");
    const anyZero = seasons.some((s) => (Number(s.d) || 0) === 0);
    // Describes the encoding rather than keying colors: the bars are colored
    // by club, so a legend swatch in any one color would be a false key.
    const legend = [
      `<span>Bar height = days credited (full = ${FULL_YEAR_DAYS})</span>`,
      `<span>Color = club</span>`,
      anyPresumed ? `<span><i class="lg-presumed"></i>presumed from debut</span>` : "",
      anyZero ? `<span><i class="lg-zero"></i>no days credited</span>` : "",
    ].filter(Boolean).join("");

    return `
      <div class="career-strip" role="img"
           aria-label="Career strip: ${seasons.length} seasons from ${seasons[0].y} to ${
             seasons[seasons.length - 1].y
           }, each bar the days credited that season.">${cells}</div>
      <div class="career-legend">${legend}</div>`;
  }

  function renderProfile(profile, teams) {
    const body = el("profile-body");
    if (!body) return;

    const seasons = profile.seasons || [];
    let running = 0;
    const rows = seasons
      .map((season) => {
        running += Number(season.d) || 0;
        const src = SOURCE_LABELS[season.src] || SOURCE_LABELS.read;
        const clubs = (season.t || [])
          .map((id) => esc(teams[String(id)] || `Club ${id}`))
          .join(", ");
        const note = seasonNote(season);
        const dayCell = Number(season.d) === 0
          ? `<span class="season-zero">0</span>`
          : String(season.d);
        return `
          <tr${Number(season.d) === 0 ? ' class="season-empty"' : ""}>
            <td class="num-col">${esc(season.y)}</td>
            <td>${clubs || "—"}</td>
            <td class="num-col">${dayCell}${
              note ? ` <abbr class="season-note" title="${esc(note)}">*</abbr>` : ""
            }</td>
            <td class="num-col">${formatDays(running)}</td>
            <td><span class="src-pill ${src.cls}" title="${esc(src.title)}"><span class="src-full">${src.label}</span><span class="src-short">${src.short}</span></span></td>
          </tr>`;
      })
      .join("");

    // Two different things get conflated if you only look at missing_seasons,
    // and they deserve different words. Miguel Cabrera debuted in 2003 and
    // his first recorded roster move is 2011, so the flag says 8 -- but six
    // of those seasons ARE credited now (presumed from his debut) and only
    // 2003-04 are absent entirely, because they fall before the earliest
    // season the pipeline computes. He reads 19.000 against a real 21.000:
    // short by the two absent seasons, not by eight.
    const presumedCount = seasons.filter((s) => s.src === "presumed").length;
    const debutYear = profile.mlb_debut ? Number(profile.mlb_debut.slice(0, 4)) : null;
    const firstYear = seasons.length ? Number(seasons[0].y) : null;
    const absentCount =
      debutYear && firstYear && firstYear > debutYear ? firstYear - debutYear : 0;

    const notes = [];
    if (presumedCount) {
      notes.push(
        `The first roster move on record for him is
         ${esc(profile.first_transaction) || "later than his debut"}.
         ${presumedCount} season${presumedCount === 1 ? " is" : "s are"} credited by
         presuming he stayed on a roster from his debut rather than by reading
         transactions — marked "Presumed from debut" below.`
      );
    }
    if (absentCount) {
      notes.push(
        `${absentCount} season${absentCount === 1 ? "" : "s"} before ${firstYear}
         ${absentCount === 1 ? "is" : "are"} not counted at all: the transaction
         data does not reach that far back. This total is a floor by roughly
         that much.`
      );
    }
    const gapNote = notes.length
      ? `<p class="profile-gap">${notes.join(" ")}</p>`
      : "";

    body.innerHTML = `
      <header class="profile-header">
        <h2 id="profile-title">${esc(profile.name)}</h2>
        <p class="profile-sub">
          ${profile.on_40_man ? `${esc(profile.team) || "No current club"} · ` : ""}${
            profile.position ? `${esc(profile.position)} · ` : ""
          }${profile.on_40_man ? "On a 40-man roster" : "Not on a 40-man roster"}
        </p>
      </header>
      <dl class="profile-facts">
        <div><dt>Service time</dt><dd class="profile-total">${esc(profile.service_time)}</dd></div>
        <div><dt>Total days</dt><dd>${esc(profile.days)}</dd></div>
        <div><dt>MLB debut</dt><dd>${esc(profile.mlb_debut) || "—"}</dd></div>
        <div><dt>Last played</dt><dd>${esc(profile.last_played) || "Active"}</dd></div>
      </dl>
      ${careerStrip(seasons, teams)}
      ${gapNote}
      <div class="season-table-wrap">
        <table class="season-table">
          <thead>
            <tr>
              <th scope="col">Season</th>
              <th scope="col">Club</th>
              <th scope="col">Days</th>
              <th scope="col"><span class="src-full">Running total</span><span class="src-short">Total</span></th>
              <th scope="col"><span class="src-full">How this season is known</span><span class="src-short">Source</span></th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <p class="profile-foot">
        172 days credit a full year. A season can credit at most 172 no matter how
        many days a player spends on a roster, so the running total advances by at
        most 1.000 per year. Hover a starred figure or a label in the last column
        for detail.
      </p>`;
  }

  function openProfile(playerId) {
    const overlay = el("profile-overlay");
    const body = el("profile-body");
    if (!overlay || !body) return;

    lastFocused = document.activeElement;
    overlay.hidden = false;
    document.body.classList.add("profile-open");
    body.innerHTML = `<p class="profile-loading">Loading…</p>`;
    const closeBtn = el("profile-close");
    if (closeBtn) closeBtn.focus();

    loadProfileShard(playerId).then((shard) => {
      const profile = shard && shard.players ? shard.players[String(playerId)] : null;
      if (!profile) {
        // Expected for players whose records predate the profile build. Say
        // so plainly rather than showing an empty table.
        const known = allPlayers.find((p) => p.id === playerId);
        body.innerHTML = `
          <header class="profile-header">
            <h2 id="profile-title">${esc(known ? known.name : "Player")}</h2>
          </header>
          <p class="profile-empty">
            No season breakdown has been computed for this player yet. Profiles are
            written by the daily job and the historical backfill; this one will
            appear after the next full run.
          </p>`;
        return;
      }
      renderProfile(profile, (shard && shard.teams) || {});
    });
  }

  function closeProfile() {
    const overlay = el("profile-overlay");
    if (!overlay || overlay.hidden) return;
    overlay.hidden = true;
    document.body.classList.remove("profile-open");
    if (window.location.hash.startsWith("#player/")) {
      history.replaceState(null, "", window.location.pathname + window.location.search);
    }
    if (lastFocused && lastFocused.focus) lastFocused.focus();
  }

  function wireProfile() {
    const tbody = el("players-tbody");
    if (tbody) {
      tbody.addEventListener("click", (event) => {
        const trigger = event.target.closest("[data-player-id]");
        if (!trigger) return;
        const id = Number(trigger.getAttribute("data-player-id"));
        if (!Number.isFinite(id)) return;
        history.replaceState(null, "", `#player/${id}`);
        openProfile(id);
      });
    }
    const closeBtn = el("profile-close");
    if (closeBtn) closeBtn.addEventListener("click", closeProfile);
    const overlay = el("profile-overlay");
    if (overlay) {
      overlay.addEventListener("click", (event) => {
        if (event.target === overlay) closeProfile();
      });
    }
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeProfile();
    });
  }

  // Deep link: /#player/592450 opens straight to a profile, so one can be
  // shared or bookmarked.
  function openProfileFromHash() {
    const match = /^#player\/(\d+)$/.exec(window.location.hash || "");
    if (match) openProfile(Number(match[1]));
  }

  function renderMeta(data) {
    el("disclaimer-text").textContent = data.disclaimer || "";
    const footer = el("generated-at-footer");
    if (data.generated_at) {
      const d = new Date(data.generated_at);
      footer.textContent = `Data last generated: ${d.toLocaleString()} · Source: ${data.source || "unknown"}`;
    } else {
      footer.textContent = `Source: ${data.source || "unknown"}`;
    }
  }

  function init(data) {
    // The compact index stores rows as arrays; the full database and the
    // embedded fallback store them as objects. Detect which arrived rather
    // than assuming, so an older service_time.json still renders.
    const rows = data.players || [];
    allPlayers = Array.isArray(rows[0]) ? hydrate(data) : rows;
    if (data.coverage_start_year) coverageStartYear = data.coverage_start_year;
    superTwoCutoff = data.super_two_cutoff || null;
    renderMeta(data);
    renderStatTiles(allPlayers);
    populateTeamFilter(allPlayers);
    updateSortIndicators();
    wireSorting();
    wireFilters();
    wirePagination();
    wireProfile();
    renderTable();
    openProfileFromHash();
  }

  wireThemeToggle();

  // Prefer the compact index; fall back to the full database so a deployment
  // that hasn't regenerated index.json yet still works, then to the embedded
  // sample for file:// use.
  fetchJson(DATA_URL)
    .catch(() => fetchJson(FULL_DATA_URL))
    .then(init)
    .catch(() => init(FALLBACK_DATA));
})();
