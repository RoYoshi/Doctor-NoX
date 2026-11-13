#!/usr/bin/env python3
"""Build Doctor NoX's app icon: the NoX mark on an indigo night tile.

Developer tool only (needs Pillow); never runs in a package build. The icon is
drawn from the mark's geometry in doctor_nox/theme.py, the same geometry the
window uses (gui.nox_mark), so the icon and the in-app mark never drift apart:

    python3 tools/build_doctor_icon.py

How each size is drawn:
- 16 times larger (4-8 times for 256 and 512 px), then box-filtered down: exact
  anti-aliasing at every size.
- The tile's margin and the cross's arms land on whole pixels, and the cross's
  arm length and thickness have the same parity, so it stays symmetric. Up to
  32 px the crescent's outer edge is aligned to the pixel grid as well.
- Sizes up to theme.SMALL_ICON use the larger, bolder mark.
- From FINISH_FROM px up, a faint moonglow behind the crescent and a faint light
  along the tile's top edge; smaller sizes stay flat, where they would only blur.

Outputs (committed, installed under /usr/share/icons/hicolor by every package):
  data/icons/hicolor/<N>x<N>/apps/io.github.royoshi.doctornox.png
"""
from pathlib import Path
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from doctor_nox.theme import (DARK_THEME, ICON_MARGIN, ICON_RADIUS, ICON_TILE,  # noqa: E402
                              crescent_tips, mark_for)

ICON_NAME = "io.github.royoshi.doctornox"
HICOLOR = ROOT / "data/icons/hicolor"
PNG_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)
SUPERSAMPLE = 16
FINISH_FROM = 48
GLOW = 0.06        # moonglow strength (alpha of the lavender halo)
EDGE_LIGHT = 0.07  # strength of the light along the tile's top edge


def rgb(hex_colour: str) -> tuple:
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def solid(colour, big):
    return Image.new("RGBA", (big, big), colour + (255,))


def draw(size: int) -> Image.Image:
    k = SUPERSAMPLE if size <= 128 else max(4, 2048 // size)  # big icons need fewer samples per pixel
    big = size * k
    edge_px = max(1, round(ICON_MARGIN * size))  # on whole pixels
    edge, side = edge_px * k, (size - 2 * edge_px) * k
    tile = Image.new("L", (big, big), 0)
    ImageDraw.Draw(tile).rounded_rectangle([edge, edge, big - edge - 1, big - edge - 1],
                                           radius=ICON_RADIUS * side, fill=255)
    # The tile: a vertical gradient from ICON_TILE[0] to ICON_TILE[1].
    top, bottom = rgb(ICON_TILE[0]), rgb(ICON_TILE[1])
    icon = Image.new("RGBA", (big, big))
    pen = ImageDraw.Draw(icon)
    for row in range(big):
        f = min(1.0, max(0.0, (row - edge) / side))
        pen.line([(0, row), (big, row)], fill=tuple(round(a + (b - a) * f) for a, b in zip(top, bottom)) + (255,))
    mark = mark_for(size)
    unit = mark.scale * side / 100
    ox = oy = edge + (1 - mark.scale) / 2 * side
    if size <= 32:  # put the crescent's left and bottom edges on whole output pixels
        mx, my, mr = mark.moon
        left, bottom_edge = ox + (mx - mr) * unit, oy + (my + mr) * unit
        ox += round(left / k) * k - left
        oy += round(bottom_edge / k) * k - bottom_edge

    def at(x, y):
        return ox + x * unit, oy + y * unit

    def disc(pen, cx, cy, r, fill):
        x, y = at(cx, cy)
        pen.ellipse([x - r * unit, y - r * unit, x + r * unit, y + r * unit], fill=fill)

    finish = size >= FINISH_FROM
    if finish:
        # Moonglow: a soft lavender halo centred on the moon.
        halo = Image.new("L", (big, big), 0)
        mx, my, mr = mark.moon
        disc(ImageDraw.Draw(halo), mx, my, 1.25 * mr, round(255 * GLOW))
        halo = halo.filter(ImageFilter.GaussianBlur(0.22 * mr * unit))
        icon = Image.composite(solid(rgb(DARK_THEME.accent), big), icon, halo)
        # Edge light: a thin rim inside the tile, brightest at the top and gone by mid-height.
        rim = max(k, round(0.012 * side))
        inner = Image.new("L", (big, big), 0)
        ImageDraw.Draw(inner).rounded_rectangle([edge + rim, edge + rim, big - edge - rim - 1, big - edge - rim - 1],
                                                radius=ICON_RADIUS * side - rim, fill=255)
        ring = ImageChops.subtract(tile, inner)
        fade = Image.new("L", (big, big), 0)
        fpen = ImageDraw.Draw(fade)
        for row in range(big):
            fpen.line([(0, row), (big, row)],
                      fill=round(255 * EDGE_LIGHT * max(0.0, 1 - (row - edge) / (0.45 * side))))
        icon = Image.composite(solid((255, 255, 255), big), icon, ImageChops.multiply(ring, fade))
    # The crescent: moon minus cut-out, with both tips rounded.
    crescent = Image.new("L", (big, big), 0)
    pen = ImageDraw.Draw(crescent)
    disc(pen, *mark.moon, 255)
    disc(pen, *mark.cut, 0)
    for (fx, fy), quad in crescent_tips(mark):
        pen.polygon([at(x, y) for x, y in quad], fill=0)
        disc(pen, fx, fy, mark.tip, 255)
    icon = Image.composite(solid(rgb(DARK_THEME.accent), big), icon, crescent)
    # The cross, on whole output pixels; equal parity keeps both arms centred on each other.
    x, y, length, thick = mark.cross
    cx, cy = at(x, y)
    t_px = max(2, round(thick * unit / k))
    l_px = round(length * unit / k)
    if (l_px - t_px) % 2:
        l_px += 1 if length * unit / k > l_px else -1
    left, top_ = round(cx / k - t_px / 2), round(cy / k - t_px / 2)
    reach = (l_px - t_px) // 2
    cross = Image.new("L", (big, big), 0)
    pen = ImageDraw.Draw(cross)
    for x0, y0, w, h in ((left, top_ - reach, t_px, l_px), (left - reach, top_, l_px, t_px)):
        pen.rounded_rectangle([x0 * k, y0 * k, (x0 + w) * k - 1, (y0 + h) * k - 1],
                              radius=t_px * k / 4, fill=255)
    icon = Image.composite(solid(rgb(DARK_THEME.text), big), icon, cross)
    icon.putalpha(tile)
    return icon.resize((size, size), Image.BOX)


def main() -> int:
    for size in PNG_SIZES:
        target = HICOLOR / f"{size}x{size}/apps/{ICON_NAME}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        draw(size).save(target, optimize=True)
        print(target.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
