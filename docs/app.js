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
  // Matches SUPER_TWO_HEURISTIC_MIN_DAYS in scripts/service_time.py.
  const SUPER_TWO_MIN_DAYS = 86;

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
      const [id, name, teamIx, posIx, days, on40, missing] = row;
      const years = Math.floor(days / FULL_YEAR_DAYS);
      const rem = days % FULL_YEAR_DAYS;
      const frac = years + rem / FULL_YEAR_DAYS;
      const superTwo = frac >= 2 && frac < 3 && rem >= SUPER_TWO_MIN_DAYS;
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
    // No reconstructable history at all -- calling these players
    // "Pre-Arbitration" is flatly wrong (Arthur Rhodes pitched 20 seasons).
    if (p.service_days_total === 0 && p.history_complete === false) {
      return { label: "Unknown", cls: "badge-neutral" };
    }
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
        // How much of the career is missing, measured per player. A bare
        // "partial" flag keyed to a cutoff year said nothing about the size
        // of the gap -- and flagged plenty of complete figures, since the
        // feed does carry some pre-2009 history.
        const gap = Number(p.missing_seasons) || 0;
        const partial = !isComplete(p)
          ? ` <abbr class="partial-flag" title="The transaction feed's coverage of this player starts ${
              gap ? `${gap} season${gap === 1 ? "" : "s"} after his debut` : "after his debut"
            }. Those earlier seasons are invisible to the data source, so this figure is a floor, not an estimate.">${
              gap ? `−${gap} season${gap === 1 ? "" : "s"}` : "partial"
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
        const serviceCell = noData
          ? `<abbr class="no-data" title="This player's entire career predates ${coverageStartYear}, when the transaction feed begins, so no service time can be reconstructed. This is missing data, not zero service time.">no data</abbr>`
          : esc(p.service_time) || "—";
        return `
        <tr>
          <td class="player-name"><button type="button" class="player-link" data-player-id="${esc(
            p.id
          )}">${esc(p.name) || "—"}</button>${partial}</td>
          <td>${esc(p.team) || "—"}</td>
          <td>${esc(p.position) || "—"}</td>
          <td class="num-col">${serviceCell}</td>
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

    const missing = Number(profile.missing_seasons) || 0;
    const gapNote = missing
      ? `<p class="profile-gap">The transaction feed's first record for this player is
         ${esc(profile.first_transaction) || "later than his debut"}, which is
         ${missing} season${missing === 1 ? "" : "s"} after he debuted. Those seasons are
         presumed rather than read, so this total is best treated as a floor.</p>`
      : "";

    body.innerHTML = `
      <header class="profile-header">
        <h2 id="profile-title">${esc(profile.name)}</h2>
        <p class="profile-sub">
          ${esc(profile.team) || "No current club"}${
            profile.position ? ` · ${esc(profile.position)}` : ""
          } · ${profile.on_40_man ? "On a 40-man roster" : "Not on a 40-man roster"}
        </p>
      </header>
      <dl class="profile-facts">
        <div><dt>Service time</dt><dd class="profile-total">${esc(profile.service_time)}</dd></div>
        <div><dt>Total days</dt><dd>${esc(profile.days)}</dd></div>
        <div><dt>MLB debut</dt><dd>${esc(profile.mlb_debut) || "—"}</dd></div>
        <div><dt>Last played</dt><dd>${esc(profile.last_played) || "Active"}</dd></div>
      </dl>
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
