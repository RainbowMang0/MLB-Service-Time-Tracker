(() => {
  "use strict";

  const DATA_URL = "data/service_time.json";

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

  const el = (id) => document.getElementById(id);

  // Player and team names come from an external API and are injected via
  // innerHTML, so escape them rather than trusting the feed.
  const esc = (value) =>
    String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  function classify(p) {
    if (p.free_agent_eligible) return { label: "Free Agent Eligible", cls: "badge-good" };
    if (p.super_two_candidate) return { label: "Possible Super Two", cls: "badge-serious" };
    if (p.arbitration_eligible) return { label: "Arbitration Eligible", cls: "badge-warning" };
    return { label: "Pre-Arbitration", cls: "badge-neutral" };
  }

  function statusMatches(p, filterValue) {
    if (!filterValue) return true;
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
    const current = players.filter((p) => p.on_40_man).length;
    const previous = total - current;
    const fa = players.filter((p) => p.free_agent_eligible).length;
    const arb = players.filter((p) => p.arbitration_eligible && !p.free_agent_eligible).length;
    const superTwo = players.filter((p) => p.super_two_candidate).length;
    const partial = players.filter((p) => !isComplete(p)).length;

    const tiles = [
      { label: "Tracked players", value: total, accent: "" },
      { label: "Currently on a 40-man", value: current, accent: "" },
      { label: "Previous players logged", value: previous, accent: "" },
      { label: "Free agency eligible", value: fa, accent: "accent-good" },
      { label: "Arbitration eligible", value: arb, accent: "accent-warning" },
      { label: "Possible Super Two", value: superTwo, accent: "accent-serious" },
    ];
    if (partial > 0) {
      tiles.push({ label: "Incomplete history", value: partial, accent: "accent-critical" });
    }

    el("stat-tiles").innerHTML = tiles
      .map(
        (t) => `
      <div class="stat-tile ${t.accent}">
        <div class="value">${t.value}</div>
        <div class="label">${t.label}</div>
      </div>`
      )
      .join("");
  }

  function populateTeamFilter(players) {
    const teams = Array.from(new Set(players.map((p) => p.team).filter(Boolean))).sort();
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
      tbody.innerHTML = `<tr><td colspan="7" class="empty-state">No players match these filters.</td></tr>`;
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
        const partial = !isComplete(p)
          ? ` <abbr class="partial-flag" title="Debuted before ${coverageStartYear}, when the transaction feed begins. Earlier seasons are invisible to the data source, so this figure is a floor, not an estimate.">partial</abbr>`
          : "";
        return `
        <tr>
          <td class="player-name">${esc(p.name) || "—"}${partial}</td>
          <td>${esc(p.team) || "—"}</td>
          <td>${esc(p.position) || "—"}</td>
          <td class="num-col">${esc(p.service_time) || "—"}</td>
          <td><span class="badge ${status.cls}">${status.label}</span></td>
          <td><span class="pill ${p.on_40_man ? "pill-yes" : "pill-no"}">${p.on_40_man ? "Yes" : "No"}</span></td>
          <td>${esc(p.last_updated) || "—"}</td>
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

  function updateSortIndicators() {
    document.querySelectorAll("#players-table thead th[data-key]").forEach((th) => {
      if (th.dataset.key === sortKey) {
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
        const effectiveKey = key === "service_time" ? "service_days_total" : key;
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
    allPlayers = data.players || [];
    if (data.coverage_start_year) coverageStartYear = data.coverage_start_year;
    renderMeta(data);
    renderStatTiles(allPlayers);
    populateTeamFilter(allPlayers);
    updateSortIndicators();
    wireSorting();
    wireFilters();
    wirePagination();
    renderTable();
  }

  wireThemeToggle();

  fetch(DATA_URL, { cache: "no-store" })
    .then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    })
    .then(init)
    .catch(() => init(FALLBACK_DATA));
})();
