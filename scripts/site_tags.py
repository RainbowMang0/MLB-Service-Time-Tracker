"""Google Analytics and AdSense tags that do not run during first paint.

The tags used to sit in ``<head>`` with ``async``, which still downloads and
executes them while the page is loading. They now wait until the load event
plus a few seconds, or until the first tap, key, or scroll, whichever comes
first. The measurement id and the publisher id are unchanged.

Ad units use the auto format. Slot ids are blank until an ad unit exists;
fill ``ADSENSE_SLOT_PLAYER`` and ``ADSENSE_SLOT_LIST`` and every page picks
them up. There is no consent banner here. That is turned on in the AdSense
account, not in the page.
"""

from __future__ import annotations

GA_MEASUREMENT_ID = "G-G04P969TLX"
ADSENSE_CLIENT_ID = "ca-pub-8748967682918425"

# Owner to-do: create the units in AdSense and paste the slot ids here.
ADSENSE_SLOT_PLAYER = ""
ADSENSE_SLOT_LIST = ""

_LIST_RELS = frozenset({
    "arbitration-eligible.html",
    "super-two.html",
    "options.html",
    "non-tender-watch.html",
    "free-agents.html",
})


def head_tags(*, adsense: bool = True) -> str:
    """Analytics, and ads unless this is the error page."""
    lines = [
        "<!-- Google Analytics -->",
        "<script>",
        "(function () {",
        "  var started = false;",
        "  function loadTags() {",
        "    if (started) return;",
        "    started = true;",
        "    window.dataLayer = window.dataLayer || [];",
        "    function gtag(){dataLayer.push(arguments);}",
        "    window.gtag = gtag;",
        "    gtag('js', new Date());",
        f"    gtag('config', '{GA_MEASUREMENT_ID}');",
        "    var ga = document.createElement('script');",
        "    ga.async = true;",
        f"    ga.src = 'https://www.googletagmanager.com/gtag/js?id={GA_MEASUREMENT_ID}';",
        "    document.head.appendChild(ga);",
    ]
    if adsense:
        lines += [
            "    var ads = document.createElement('script');",
            "    ads.async = true;",
            "    ads.crossOrigin = 'anonymous';",
            (
                "    ads.src = 'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js"
                f"?client={ADSENSE_CLIENT_ID}';"
            ),
            "    ads.onload = function () {",
            "      var slots = document.querySelectorAll('ins.adsbygoogle');",
            "      for (var i = 0; i < slots.length; i++) {",
            "        (window.adsbygoogle = window.adsbygoogle || []).push({});",
            "      }",
            "    };",
            "    document.head.appendChild(ads);",
        ]
    lines += [
        "  }",
        "  function arm() {",
        "    var timer = setTimeout(loadTags, 5000);",
        "    function early() { clearTimeout(timer); loadTags(); }",
        "    ['pointerdown', 'keydown', 'touchstart', 'scroll'].forEach(function (name) {",
        "      window.addEventListener(name, early, { once: true, passive: true });",
        "    });",
        "  }",
        "  if (document.readyState === 'complete') arm();",
        "  else window.addEventListener('load', arm, { once: true });",
        "})();",
        "</script>",
        "<!-- End Google Analytics -->",
    ]
    if adsense:
        lines += [
            "<!-- Google AdSense -->",
            f'<meta name="google-adsense-account" content="{ADSENSE_CLIENT_ID}">',
            "<!-- End Google AdSense -->",
        ]
    return "\n".join(lines) + "\n"


def ad_slot(kind: str) -> str:
    """One reserved auto ad. ``kind`` is ``player`` or ``list``.

    An empty slot id emits nothing, so the page does not keep a blank box.
    Once an id is set, the box keeps a fixed height so a filled ad does not
    move the page.
    """
    slot = ADSENSE_SLOT_PLAYER if kind == "player" else ADSENSE_SLOT_LIST
    if not slot:
        return ""
    slot_attr = f' data-ad-slot="{slot}"'
    return (
        '<aside class="ad-slot" aria-label="Advertisement">'
        '<ins class="adsbygoogle" style="display:block;min-height:280px" '
        f'data-ad-client="{ADSENSE_CLIENT_ID}" data-ad-format="auto" '
        f'data-full-width-responsive="true"{slot_attr}></ins>'
        "</aside>"
    )


def place_ads(html: str, rel: str) -> str:
    """Reserve one ad on a player page and on a list page. Not on the error page."""
    if 'class="ad-slot"' in html or rel == "404.html":
        return html
    if rel.startswith("p/") and "<h2>Season by season</h2>" in html:
        return html.replace(
            "<h2>Season by season</h2>",
            ad_slot("player") + "<h2>Season by season</h2>",
            1,
        )
    is_list = rel.startswith("t/") or rel.startswith("alumni/") or rel in _LIST_RELS
    if is_list and "<table" in html:
        return html.replace("<table", ad_slot("list") + "<table", 1)
    return html
