"""The one site-wide share image, and the tags that point at it.

Every page except the parked duty-day page uses this. The image file itself
is docs/og.png, drawn by scripts/og_card.py.
"""

from __future__ import annotations

IMAGE_PATH = "og.png"
IMAGE_WIDTH = 1200
IMAGE_HEIGHT = 630


def tags(site_url: str, path: str = IMAGE_PATH) -> str:
    """og:image plus a large Twitter card. Absolute URL, so a share works from any page."""
    url = f"{site_url.rstrip('/')}/{path.lstrip('/')}"
    return (
        f'<meta property="og:image" content="{url}" />'
        f'<meta property="og:image:width" content="{IMAGE_WIDTH}" />'
        f'<meta property="og:image:height" content="{IMAGE_HEIGHT}" />'
        '<meta name="twitter:card" content="summary_large_image" />'
        f'<meta name="twitter:image" content="{url}" />'
    )
