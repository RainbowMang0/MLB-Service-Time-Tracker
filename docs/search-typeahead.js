/*
 * Player/club name suggestions for the shared header search box.
 * The home table keeps its own search; this file only wires the header box.
 * Suggestion helpers are exported for unit tests. Data loads on first focus.
 */
(function (global) {
  "use strict";

  // Characters NFKD does not split into letter + combining mark.
  var TRANSLITERATE = {
    "ø": "o", "Ø": "O",
    "ł": "l", "Ł": "L",
    "æ": "ae", "Æ": "AE",
    "ß": "ss",
    "đ": "d", "Đ": "D",
    "þ": "th", "Þ": "Th",
  };

  // Keep suggestion hrefs aligned with published player/club page URLs.
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

  // Pull only id, name, team, and whether a page exists from the players index.
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
    var phone = document.querySelector(".phone-search");
    if (phone) {
      phone.addEventListener("click", function (event) {
        event.preventDefault();
        var top = document.querySelector(".site-top");
        var box = document.querySelector(".top-search input[name=\"q\"]");
        if (!top || !box) return;
        var open = top.classList.toggle("search-open");
        if (open) box.focus({ preventScroll: true });
      });
    }
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
