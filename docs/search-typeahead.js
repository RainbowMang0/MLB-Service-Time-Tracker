/*
 * search-typeahead.js -- player/club name suggestions for the shared header
 * search box (docs/app.js owns the home table's own search-input; this is
 * the header's .top-search box, present on every page type).
 *
 * Pure logic (slugify, decodeIndex, buildSuggestions) is exported for
 * `node --test`; the DOM wiring below only runs in a browser and only
 * fetches data/index.json on the input's first focus, so a visitor who
 * never touches search pays nothing for this file beyond its own bytes.
 */
(function (global) {
  "use strict";

  // Same table write_player_pages.py's slug() uses for characters NFKD does
  // not split into letter + combining mark.
  var TRANSLITERATE = {
    "ø": "o", "Ø": "O",
    "ł": "l", "Ł": "L",
    "æ": "ae", "Æ": "AE",
    "ß": "ss",
    "đ": "d", "Đ": "D",
    "þ": "th", "Þ": "Th",
  };

  // Mirrors slug() in write_player_pages.py so a suggestion's href matches
  // the page that generator actually wrote, without shipping the slug
  // itself in the published index.
  function slugify(name) {
    var s = String(name || "");
    var out = "";
    for (var i = 0; i < s.length; i++) {
      out += TRANSLITERATE[s[i]] || s[i];
    }
    out = out.normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
    out = out.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    return out || "player";
  }

  // index.json's `players` rows are [id, name, team_ix, pos_ix, days,
  // on_40_man, missing_seasons, super_two, has_page, birth_year] -- see
  // write_index() in scripts/update_service_time.py. Only id/name/team/
  // has_page are needed here.
  function decodeIndex(payload) {
    var teams = (payload && payload.teams) || [];
    var rows = (payload && payload.players) || [];
    return {
      teams: teams,
      players: rows.map(function (row) {
        return {
          id: row[0],
          name: row[1],
          team: teams[row[2]] || "",
          hasPage: row[8] === 1,
        };
      }),
    };
  }

  // A player with no page yet still gets a real destination: the home
  // table filtered to his name, the same place the phone search icon and
  // the no-JS fallback already point.
  function playerHref(p) {
    if (p.hasPage) return "p/" + p.id + "-" + slugify(p.name) + ".html";
    return "?q=" + encodeURIComponent(p.name);
  }

  function clubHref(team) {
    return "t/" + slugify(team) + ".html";
  }

  // Players rank ahead of clubs (players are required, clubs a nice-to-
  // have), and within each an exact start-of-name match ranks ahead of a
  // mid-name one. `limit` caps the combined list, not each tier.
  function buildSuggestions(query, decoded, limit) {
    limit = limit || 8;
    var q = String(query || "").trim().toLowerCase();
    if (!q) return [];

    var players = (decoded && decoded.players) || [];
    var teams = (decoded && decoded.teams) || [];
    var startsWithPlayers = [];
    var containsPlayers = [];
    for (var i = 0; i < players.length; i++) {
      var p = players[i];
      var name = (p.name || "").toLowerCase();
      if (!name) continue;
      var idx = name.indexOf(q);
      if (idx < 0) continue;
      var entry = { type: "player", id: p.id, name: p.name, team: p.team, href: playerHref(p) };
      if (idx === 0) startsWithPlayers.push(entry);
      else containsPlayers.push(entry);
    }

    var startsWithClubs = [];
    var containsClubs = [];
    for (var j = 0; j < teams.length; j++) {
      var team = teams[j];
      if (!team) continue;
      var tIdx = team.toLowerCase().indexOf(q);
      if (tIdx < 0) continue;
      var clubEntry = { type: "club", id: null, name: team, team: team, href: clubHref(team) };
      if (tIdx === 0) startsWithClubs.push(clubEntry);
      else containsClubs.push(clubEntry);
    }

    return startsWithPlayers
      .concat(containsPlayers, startsWithClubs, containsClubs)
      .slice(0, limit);
  }

  var api = {
    slugify: slugify,
    decodeIndex: decodeIndex,
    buildSuggestions: buildSuggestions,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
    return;
  }

  // --- browser wiring ----------------------------------------------------
  if (typeof document === "undefined") return;

  function ready(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  ready(function () {
    var input = document.querySelector(".top-search input[name=\"q\"]");
    if (!input) return;
    var form = input.closest("form");
    var base = (form && form.getAttribute("action")) || "/";

    var listId = "search-suggest-list";
    var list = document.createElement("ul");
    list.id = listId;
    list.className = "search-suggest";
    list.setAttribute("role", "listbox");
    list.hidden = true;
    // A child of the form, not a sibling: .search-suggest is positioned
    // absolutely (so it drops out of the form's own flex layout) relative
    // to .top-search, which is what lines its right edge up with the input.
    if (form) form.appendChild(list);

    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", listId);
    input.setAttribute("autocomplete", "off");

    var decodedPromise = null;
    function loadDecoded() {
      if (!decodedPromise) {
        decodedPromise = fetch(base + "data/index.json")
          .then(function (r) { return r.json(); })
          .then(decodeIndex)
          .catch(function () { return { teams: [], players: [] }; });
      }
      return decodedPromise;
    }

    var items = [];
    var activeIndex = -1;

    function closeList() {
      list.hidden = true;
      list.innerHTML = "";
      items = [];
      activeIndex = -1;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
    }

    function labelFor(item) {
      if (item.type === "club") return item.name;
      return item.team ? item.name + " — " + item.team : item.name;
    }

    function renderList(suggestions) {
      items = suggestions;
      activeIndex = -1;
      list.innerHTML = "";
      if (!suggestions.length) {
        closeList();
        return;
      }
      suggestions.forEach(function (item, i) {
        var li = document.createElement("li");
        li.id = listId + "-opt-" + i;
        li.setAttribute("role", "option");
        li.textContent = labelFor(item);
        li.addEventListener("mousedown", function (e) {
          // mousedown, not click: fires before the input's blur, so the
          // list is still open when the navigation below reads activeIndex.
          e.preventDefault();
          navigateTo(item);
        });
        list.appendChild(li);
      });
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }

    function setActive(i) {
      if (!items.length) return;
      activeIndex = (i + items.length) % items.length;
      Array.prototype.forEach.call(list.children, function (li, idx) {
        li.classList.toggle("active", idx === activeIndex);
      });
      input.setAttribute("aria-activedescendant", listId + "-opt-" + activeIndex);
    }

    function navigateTo(item) {
      window.location.href = base + item.href;
    }

    var debounceTimer = null;
    function onInput() {
      var q = input.value;
      if (debounceTimer) clearTimeout(debounceTimer);
      if (!q.trim()) {
        closeList();
        return;
      }
      debounceTimer = setTimeout(function () {
        loadDecoded().then(function (decoded) {
          renderList(buildSuggestions(q, decoded));
        });
      }, 120);
    }

    input.addEventListener("focus", loadDecoded);
    input.addEventListener("input", onInput);
    input.addEventListener("keydown", function (e) {
      if (list.hidden && e.key !== "ArrowDown") return;
      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          if (list.hidden) onInput();
          else setActive(activeIndex + 1);
          break;
        case "ArrowUp":
          e.preventDefault();
          setActive(activeIndex - 1);
          break;
        case "Enter":
          if (activeIndex >= 0 && items[activeIndex]) {
            e.preventDefault();
            navigateTo(items[activeIndex]);
          }
          // Otherwise let the form submit normally to base + "?q=...".
          break;
        case "Escape":
          closeList();
          break;
        default:
          break;
      }
    });
    input.addEventListener("blur", function () {
      // A delay, not an immediate close: the option's own mousedown
      // handler needs the list to still be in the DOM when it runs.
      setTimeout(closeList, 150);
    });
    document.addEventListener("click", function (e) {
      if (e.target !== input && !list.contains(e.target)) closeList();
    });
  });
})(typeof window !== "undefined" ? window : this);
