/*
 * lockout.js -- the CBA & lockout tracker's countdown/day-count logic, plus
 * the one pure helper (matchesFilter) the topic filter's inline DOM-wiring
 * script uses to decide which entries stay visible.
 *
 * Keeps the status shown on first load in agreement with what a visitor
 * sees live, correcting it for a visitor whose "today" has moved on since
 * the page was built. Everything here reads the ET (America/New_York)
 * calendar via Intl.DateTimeFormat with an explicit timeZone, never the
 * visitor's own local date -- a visitor in Tokyo or Los Angeles must see
 * the same status a visitor in New York does.
 */
(function (global) {
  "use strict";

  var ET_ZONE = "America/New_York";

  // "YYYY-MM-DD" for `instant` (a Date) in the America/New_York zone,
  // independent of the host device's own time zone.
  function etDateStr(instant) {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: ET_ZONE,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(instant);
  }

  // Whole calendar days between two "YYYY-MM-DD" strings. Parsed as UTC
  // midnight on both sides so the subtraction can't pick up a DST hour.
  function daysBetween(aStr, bStr) {
    function toUTC(s) {
      var p = s.split("-").map(Number);
      return Date.UTC(p[0], p[1] - 1, p[2]);
    }
    return Math.round((toUTC(bStr) - toUTC(aStr)) / 86400000);
  }

  // `data` is the JSON blob build_lockout_page.py embeds in the page
  // (cba_expires, cba_expires_at, lockout_start, lockout_end,
  // regular_season_delayed_from). `now` defaults to the real clock; tests
  // pass a fixed Date to exercise a specific instant.
  function computeStatus(data, now) {
    now = now || new Date();
    var today = etDateStr(now);
    var nowInstant = now.getTime();
    var expiresInstant = Date.parse(data.cba_expires_at);

    var lockoutStart = data.lockout_start;
    var lockoutEnd = data.lockout_end;
    var delayedFrom = data.regular_season_delayed_from;

    var kind;
    var day = null;

    if (lockoutStart && lockoutStart <= today) {
      // A future lockout_start never reaches here -- it falls through to the
      // countdown/expiry branches below, because the lockout hasn't actually
      // started yet.
      if (lockoutEnd && today > lockoutEnd) {
        kind = "lockout_ended";
        day = daysBetween(lockoutStart, lockoutEnd) + 1;
      } else {
        kind = "lockout";
        day = daysBetween(lockoutStart, today) + 1;
        if (lockoutEnd) {
          day = Math.min(day, daysBetween(lockoutStart, lockoutEnd) + 1);
        }
      }
    } else if (nowInstant < expiresInstant) {
      kind = today === data.cba_expires ? "expiring_today" : "countdown";
      if (kind === "countdown") {
        day = daysBetween(today, data.cba_expires);
      }
    } else {
      kind = "expired_no_lockout";
    }

    var daysAtStake = null;
    if (delayedFrom && today >= delayedFrom) {
      daysAtStake = daysBetween(delayedFrom, today) + 1;
    }

    return { kind: kind, day: day, daysAtStake: daysAtStake };
  }

  function tileValue(status) {
    switch (status.kind) {
      case "countdown":
        return status.day + (status.day === 1 ? " day" : " days");
      case "expiring_today":
        return "Expires tonight";
      case "lockout":
        return "Day " + status.day;
      case "lockout_ended":
        return "Lockout ended";
      default:
        return "Expired";
    }
  }

  // `labels` carries pre-formatted date text (cbaExpiresLabel,
  // lockoutEndLabel) from the page's own JSON data, so this file never needs
  // its own month-name table that could drift from the Python one.
  function sentence(status, labels) {
    var text;
    switch (status.kind) {
      case "countdown":
        text =
          status.day +
          (status.day === 1 ? " day" : " days") +
          " until the CBA expires (" + labels.cbaExpiresLabel + ")";
        break;
      case "expiring_today":
        text = "The CBA expires tonight at 11:59 p.m. ET";
        break;
      case "lockout":
        text = "Day " + status.day + " of the lockout";
        break;
      case "lockout_ended":
        text =
          "Lockout ended " + labels.lockoutEndLabel +
          " after " + status.day + (status.day === 1 ? " day" : " days");
        break;
      default:
        text =
          "The CBA expired at 11:59 p.m. ET on " + labels.cbaExpiresLabel +
          ". This page will update when a lockout or new agreement is confirmed.";
    }
    if (status.daysAtStake !== null) {
      text +=
        " — an estimated " + status.daysAtStake +
        (status.daysAtStake === 1 ? " service-time day" : " service-time days") +
        " at stake so far";
    }
    return text;
  }

  // Topic filter above "Latest updates": true if `category` (an entry's
  // data-category) belongs under the selected `filterValue` ("all" always
  // matches; anything else is an exact match against the category).
  function matchesFilter(category, filterValue) {
    return filterValue === "all" || category === filterValue;
  }

  var api = {
    etDateStr: etDateStr,
    daysBetween: daysBetween,
    computeStatus: computeStatus,
    tileValue: tileValue,
    sentence: sentence,
    matchesFilter: matchesFilter,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.LockoutStatus = api;
  }
})(typeof window !== "undefined" ? window : this);
