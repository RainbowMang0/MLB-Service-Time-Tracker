"""
cba.py
------
Loads versioned CBA rulesets from config/cba/*.json.

WHY THIS EXISTS
===============
The 2022 Basic Agreement expires 11:59 PM ET on 2026-12-01, and service time,
arbitration and free agency are the central issues in the dispute. Every
threshold this project computes against could move. Before this module those
numbers were literals scattered across four files (172 alone appeared in
service_time.py, super_two.py, write_player_pages.py and docs/app.js), so a
new agreement would have meant hunting them down by hand in two languages.

Now they are data. A new agreement is one JSON file.

THE RULES THIS MODULE ENFORCES
==============================
1. A value that is absent or null raises. It never defaults to something
   plausible -- a ruleset that quietly credits a player zero days under an
   agreement nobody has negotiated is worse than a crash.
2. A ruleset marked `usable: false` (2027.json today) raises on any value
   read. It is loadable so it can be inspected and diffed; it is not
   computable.
3. Every value carries a `sources` entry saying how it was established.
   `unverified` values exist so the shape is complete, and
   `require_verified()` refuses them -- nothing should silently depend on a
   number nobody has checked. This mirrors the "status": "unverified"
   treatment in the state tax table.

USAGE
=====
    import cba
    rules = cba.default()                     # the 2022 agreement
    rules.require("service_time.days_per_credited_year")   # -> 172

    cba.load("2027").require("free_agency.credited_years_required")
    # -> UnusableRuleset: ruleset '2027' is a placeholder
"""

from __future__ import annotations

import functools
import json
import pathlib

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config" / "cba"

# The agreement in force. Bump this when a successor is signed and filled in.
DEFAULT_VERSION = "2022"


class RulesetError(Exception):
    """Base class for every way a ruleset can refuse to answer."""


class UnusableRuleset(RulesetError):
    """The ruleset exists but is a placeholder, so it cannot be computed with."""


class MissingRulesetValue(RulesetError):
    """The ruleset has no value (or a null value) at the requested path."""


class UnverifiedRulesetValue(RulesetError):
    """The value exists but its source status says nobody has checked it."""


class Ruleset:
    """One CBA ruleset. Read values by dotted path; missing values raise."""

    def __init__(self, data: dict, version: str, source_path: pathlib.Path):
        self._data = data
        self._version = version
        self._path = source_path

    # --- identity ----------------------------------------------------------

    @property
    def version(self) -> str:
        return self._version

    @property
    def label(self) -> str:
        return self._data.get("label") or self._version

    @property
    def usable(self) -> bool:
        """False for a placeholder. Reading any value from one raises."""
        return self._data.get("usable", True) is not False

    @property
    def effective_end(self) -> str | None:
        return self._data.get("effective_end")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        state = "usable" if self.usable else "placeholder"
        return f"<Ruleset {self._version} ({state})>"

    # --- reading values ----------------------------------------------------

    def get(self, path: str, default=None):
        """Return the value at a dotted path, or `default` if absent/null.

        Does not check `usable`, so a placeholder can still be inspected and
        diffed. Use require() for anything that will drive a number.
        """
        node = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return default if node is None else node

    def require(self, path: str):
        """Return the value at a dotted path. Raise if it is missing or null.

        This is the accessor engine code should use. There is deliberately no
        `default` parameter: a fallback would reintroduce exactly the silent
        hardcoded constant this module exists to remove.
        """
        if not self.usable:
            raise UnusableRuleset(
                f"ruleset {self._version!r} is a placeholder "
                f"({self._path.name}); it has no values to compute with. "
                "Fill it in and set usable: true."
            )
        sentinel = object()
        value = self.get(path, sentinel)
        if value is sentinel:
            raise MissingRulesetValue(
                f"ruleset {self._version!r} has no value at {path!r} "
                f"({self._path})"
            )
        return value

    def require_verified(self, path: str):
        """require(), but also refuse a value whose source status is unverified.

        For numbers that would be published to a user before anyone has
        checked them against the CBA text -- option counts today. A wrong
        number is worse than a missing one.
        """
        value = self.require(path)
        if self.status(path) == "unverified":
            raise UnverifiedRulesetValue(
                f"{path!r} in ruleset {self._version!r} is marked unverified: "
                f"{self.source_note(path) or 'no note'}. Verify it against the "
                "CBA text and update its sources entry before relying on it."
            )
        return value

    # --- provenance --------------------------------------------------------

    def status(self, path: str) -> str | None:
        """The source status for a path: verified / documented / unverified..."""
        entry = self._data.get("sources", {}).get(path)
        if isinstance(entry, dict):
            return entry.get("status")
        return None

    def source_note(self, path: str) -> str | None:
        entry = self._data.get("sources", {}).get(path)
        if isinstance(entry, dict):
            return entry.get("note")
        return None

    def unverified_paths(self) -> list[str]:
        """Every path whose source status is 'unverified', sorted."""
        sources = self._data.get("sources", {})
        return sorted(
            path
            for path, entry in sources.items()
            if isinstance(entry, dict) and entry.get("status") == "unverified"
        )

    # --- derived helpers ---------------------------------------------------

    def shortened_seasons(self) -> dict[int, int]:
        """{year: span to scale to} for seasons credited on a prorated basis.

        JSON object keys are strings; the engine keys seasons by int year, so
        the conversion happens here rather than at every call site.
        """
        raw = self.get("service_time.shortened_seasons", {}) or {}
        out: dict[int, int] = {}
        for year, spec in raw.items():
            span = spec.get("scale_to_span_days") if isinstance(spec, dict) else spec
            if span is not None:
                out[int(year)] = int(span)
        return out


@functools.lru_cache(maxsize=None)
def load(version: str = DEFAULT_VERSION) -> Ruleset:
    """Load and cache one ruleset by version string."""
    path = CONFIG_DIR / f"{version}.json"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in CONFIG_DIR.glob("*.json"))) or "none"
        raise MissingRulesetValue(
            f"no CBA ruleset {version!r} at {path} (available: {available})"
        )
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return Ruleset(data, version, path)


def default() -> Ruleset:
    """The agreement currently in force."""
    return load(DEFAULT_VERSION)


def available_versions() -> list[str]:
    return sorted(p.stem for p in CONFIG_DIR.glob("*.json"))
