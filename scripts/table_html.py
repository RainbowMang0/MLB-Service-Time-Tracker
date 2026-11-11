"""Wrap bare tables so a phone can scroll them without scrolling the page."""

from __future__ import annotations

import re

_TABLE_RE = re.compile(r"<table\b.*?</table>", re.DOTALL | re.IGNORECASE)
_ALREADY_WRAPPED_RE = re.compile(
    r"class=\"[^\"]*\b(?:tbl-wrap|tbl-scroll|table-wrap|table-scroll|season-table-wrap)\b[^\"]*\"[^>]*>\s*$",
    re.DOTALL,
)


def wrap_bare_tables(html: str) -> str:
    """Put each bare ``<table>`` in a scroll container.

    Tables that already sit in a scroll wrapper are left alone. Stats tables
    use ``tbl-wrap``; the homepage uses ``table-wrap``.
    """

    def repl(match: re.Match) -> str:
        prefix = html[max(0, match.start() - 160):match.start()]
        if _ALREADY_WRAPPED_RE.search(prefix):
            return match.group(0)
        return (
            '<div class="tbl-scroll">'
            '<p class="swipe-cue">Swipe sideways for more</p>'
            f"{match.group(0)}</div>"
        )

    return _TABLE_RE.sub(repl, html)
