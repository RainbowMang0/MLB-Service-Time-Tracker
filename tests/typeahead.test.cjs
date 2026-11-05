/*
 * Tests for docs/search-typeahead.js -- the header search box's
 * suggestion helper (slugify, decodeIndex, buildSuggestions). Pure
 * functions only; the DOM wiring in that file only runs in a browser.
 *
 * Run: node --test tests/typeahead.test.cjs
 */

const test = require("node:test");
const assert = require("node:assert");

const T = require("../docs/search-typeahead.js");

// -------------------------------------------------------------------------
// slugify -- must match write_player_pages.py's slug() exactly, since a
// suggestion's href is built from this rather than a slug shipped in
// index.json.
// -------------------------------------------------------------------------

test("slugify matches scripts/write_player_pages.py's slug() on plain names", () => {
  assert.equal(T.slugify("Giancarlo Stanton"), "giancarlo-stanton");
  assert.equal(T.slugify("Mike Trout"), "mike-trout");
});

test("slugify transliterates an accent rather than dropping it", () => {
  assert.equal(T.slugify("José Berríos"), "jose-berrios");
  assert.equal(T.slugify("Adolis García"), "adolis-garcia");
});

test("slugify handles the characters NFKD does not split", () => {
  assert.equal(T.slugify("Félix Peña"), "felix-pena");
});

test("slugify never returns empty", () => {
  assert.equal(T.slugify(""), "player");
  assert.equal(T.slugify(null), "player");
});

// -------------------------------------------------------------------------
// decodeIndex -- the array-row shape write_index() in
// scripts/update_service_time.py actually publishes.
// -------------------------------------------------------------------------

function samplePayload() {
  return {
    teams: ["Atlanta Braves", "Boston Red Sox", ""],
    // [id, name, team_ix, pos_ix, days, on_40_man, missing_seasons, super_two, has_page, birth_year]
    players: [
      [1, "Aaron Judge", 1, 0, 2000, 1, 0, 0, 1, 1992],
      [2, "Aaron Nola", 0, 0, 1800, 1, 0, 0, 0, 1993],
      [3, "Retired Guy", 2, 0, 900, 0, 0, 0, 1, 1980],
    ],
  };
}

test("decodeIndex resolves the team index into a name, and the has_page flag into a boolean", () => {
  const decoded = T.decodeIndex(samplePayload());
  assert.deepEqual(decoded.players[0], {
    id: 1, name: "Aaron Judge", team: "Boston Red Sox", hasPage: true,
  });
  assert.deepEqual(decoded.players[1], {
    id: 2, name: "Aaron Nola", team: "Atlanta Braves", hasPage: false,
  });
});

// -------------------------------------------------------------------------
// buildSuggestions
// -------------------------------------------------------------------------

test("an empty query returns no suggestions", () => {
  const decoded = T.decodeIndex(samplePayload());
  assert.deepEqual(T.buildSuggestions("", decoded), []);
  assert.deepEqual(T.buildSuggestions("   ", decoded), []);
});

test("a player with a page links to it; one without falls back to a filtered search", () => {
  const decoded = T.decodeIndex(samplePayload());
  const out = T.buildSuggestions("aaron", decoded);
  assert.equal(out.length, 2);
  assert.deepEqual(out[0], {
    type: "player", id: 1, name: "Aaron Judge", team: "Boston Red Sox",
    href: "p/1-aaron-judge.html",
  });
  assert.deepEqual(out[1], {
    type: "player", id: 2, name: "Aaron Nola", team: "Atlanta Braves",
    href: "?q=Aaron%20Nola",
  });
});

test("a name match at the start of the string ranks ahead of a mid-name match", () => {
  const decoded = T.decodeIndex({
    teams: ["Atlanta Braves"],
    players: [
      [1, "Dansby Swanson", 0, 0, 1000, 1, 0, 0, 1, 1994], // "son" mid-name
      [2, "Sonny Gray", 0, 0, 1000, 1, 0, 0, 1, 1989],     // "son" at the start
    ],
  });
  const out = T.buildSuggestions("son", decoded);
  assert.equal(out[0].name, "Sonny Gray", "starts-with beats contains");
  assert.equal(out[1].name, "Dansby Swanson");
});

test("a club match is included, but ranks behind every player match", () => {
  const decoded = T.decodeIndex({
    teams: ["Boston Red Sox"],
    players: [[1, "Bo Sox Fan", 0, 0, 100, 1, 0, 0, 1, 2000]],
  });
  const out = T.buildSuggestions("bo", decoded);
  assert.equal(out.length, 2);
  assert.equal(out[0].type, "player");
  assert.deepEqual(out[1], {
    type: "club", id: null, name: "Boston Red Sox", team: "Boston Red Sox",
    href: "t/boston-red-sox.html",
  });
});

test("the result is capped at 8 even with more matches available", () => {
  const players = [];
  for (let i = 0; i < 20; i++) {
    players.push([i, `Player Number ${i}`, 0, 0, 100, 1, 0, 0, 1, 2000]);
  }
  const decoded = T.decodeIndex({ teams: ["Atlanta Braves"], players });
  const out = T.buildSuggestions("player", decoded);
  assert.equal(out.length, 8);
});

test("matching is case-insensitive", () => {
  const decoded = T.decodeIndex(samplePayload());
  const out = T.buildSuggestions("JUDGE", decoded);
  assert.equal(out.length, 1);
  assert.equal(out[0].name, "Aaron Judge");
});

test("an empty team (a player with no current club) is never offered as a club suggestion", () => {
  const decoded = T.decodeIndex(samplePayload());
  const out = T.buildSuggestions("retired", decoded);
  // "Retired Guy" matches by name; the empty team string must not also
  // surface as a club match.
  assert.equal(out.length, 1);
  assert.equal(out[0].type, "player");
});
