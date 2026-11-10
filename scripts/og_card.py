"""Draw the site-wide share image, docs/og.png, at 1200 by 630.

Run this when the card should be redrawn. The page generator does not call
it: the PNG is a static file, and a daily rebuild must not need a font
library. The picture uses the same dark plane, wordmark, and baseball mark
as the header.
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "og.png"
WIDTH = 1200
HEIGHT = 630

PLANE = (10, 12, 16)
TEXT = (233, 238, 245)
MUTED = (159, 176, 196)
ACCENT = (57, 135, 229)
SEAM = (230, 103, 103)
HAIR = (30, 36, 46)

_FONTS = (
    "/usr/share/fonts/truetype/macos/Inter-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)
_FONTS_REGULAR = (
    "/usr/share/fonts/truetype/macos/Inter-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)


def _font(candidates: tuple[str, ...], size: int) -> ImageFont.ImageFont:
    for path in candidates:
        if pathlib.Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _seams(draw: ImageDraw.ImageDraw, cx: int, cy: int, radius: int) -> None:
    """Two seams that stay inside the ball and bow toward the middle."""
    width = max(4, radius // 18)

    def curve(sign: int) -> None:
        points = []
        for step in range(0, 33):
            t = step / 32
            y_frac = (t - 0.5) * 1.65
            y = cy + y_frac * radius
            room = (radius * radius - (y - cy) ** 2) ** 0.5
            bow = 1 - (2 * t - 1) ** 2
            # Near the rim at the ends, closer to the center in the middle.
            x = cx + sign * room * (0.78 - 0.58 * bow)
            points.append((x, y))
        draw.line(points, fill=SEAM, width=width, joint="curve")

    curve(1)
    curve(-1)


def draw() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), PLANE)
    pen = ImageDraw.Draw(image)
    for y in range(0, HEIGHT, 46):
        pen.line([(0, y), (WIDTH, y)], fill=HAIR, width=1)
    pen.rectangle([0, 0, 14, HEIGHT], fill=ACCENT)

    cx, cy, radius = 250, 315, 132
    pen.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        outline=TEXT,
        width=8,
    )
    _seams(pen, cx, cy, radius)

    title = _font(_FONTS, 78)
    sub = _font(_FONTS_REGULAR, 36)
    note = _font(_FONTS_REGULAR, 26)
    pen.text((460, 200), "Big League", font=title, fill=TEXT)
    pen.text((460, 290), "Service Time", font=title, fill=TEXT)
    pen.text((460, 400), "MLB service time for every 40-man player", font=sub, fill=MUTED)
    pen.text((460, 470), "Estimates from public data", font=note, fill=MUTED)
    return image


def write(path: pathlib.Path = OUT) -> bool:
    """Write the PNG only when the bytes would change. Returns True if written."""
    image = draw()
    import io
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    try:
        current = path.read_bytes()
    except OSError:
        current = None
    if current == data:
        return False
    path.write_bytes(data)
    return True


if __name__ == "__main__":
    changed = write()
    print(f"{'wrote' if changed else 'unchanged'} {OUT}")
