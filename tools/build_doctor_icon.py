#!/usr/bin/env python3
"""Build Doctor NoX's placeholder app icon: the NoX mark on an indigo night tile.

Developer tool only (needs Pillow); never runs in a package build. The icon is a
placeholder until RoYoshi draws the Doctor NoX icon. It is drawn from the mark's
geometry in doctor_nox/theme.py, the same geometry the window uses
(gui.nox_mark), so the icon and the in-app mark never drift apart:

    python3 tools/build_doctor_icon.py

Each size is drawn 16 times larger and box-filtered down, which gives exact
anti-aliasing at every size; sizes up to theme.SMALL_ICON use the bolder mark.

Outputs (committed, installed under /usr/share/icons/hicolor by every package):
  data/icons/hicolor/<N>x<N>/apps/io.github.royoshi.doctornox.png
"""
from pathlib import Path
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from doctor_nox.theme import DARK_THEME, ICON_MARGIN, ICON_RADIUS, ICON_TILE, mark_for  # noqa: E402

ICON_NAME = "io.github.royoshi.doctornox"
HICOLOR = ROOT / "data/icons/hicolor"
PNG_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)
SUPERSAMPLE = 16


def rgb(hex_colour: str) -> tuple:
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def draw(size: int) -> Image.Image:
    big = size * SUPERSAMPLE
    edge = ICON_MARGIN * big
    side = big - 2 * edge
    # The tile: a vertical gradient, clipped to a rounded square.
    top, bottom = rgb(ICON_TILE[0]), rgb(ICON_TILE[1])
    gradient = Image.new("RGBA", (big, big))
    pen = ImageDraw.Draw(gradient)
    for row in range(big):
        f = min(1.0, max(0.0, (row - edge) / side))
        pen.line([(0, row), (big, row)], fill=tuple(round(a + (b - a) * f) for a, b in zip(top, bottom)) + (255,))
    tile = Image.new("L", (big, big), 0)
    ImageDraw.Draw(tile).rounded_rectangle([edge, edge, big - edge, big - edge], radius=ICON_RADIUS * side, fill=255)
    icon = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    icon.paste(gradient, (0, 0), tile)
    # The mark, on its 100-unit grid inside the tile.
    mark = mark_for(size)
    unit = mark.scale * side / 100
    origin = edge + mark.inset * side

    def at(x, y):
        return origin + x * unit, origin + y * unit

    def disc(pen, centre_x, centre_y, radius, fill):
        x, y = at(centre_x, centre_y)
        pen.ellipse([x - radius * unit, y - radius * unit, x + radius * unit, y + radius * unit], fill=fill)

    crescent = Image.new("L", (big, big), 0)
    pen = ImageDraw.Draw(crescent)
    disc(pen, *mark.moon, 255)
    disc(pen, *mark.cut, 0)
    icon.paste(Image.new("RGBA", (big, big), rgb(DARK_THEME.accent) + (255,)), (0, 0), crescent)
    cross = Image.new("L", (big, big), 0)
    pen = ImageDraw.Draw(cross)
    x, y, length, thick = mark.cross
    for left, top_, width, height in ((x - thick / 2, y - length / 2, thick, length),
                                      (x - length / 2, y - thick / 2, length, thick)):
        a, b = at(left, top_)
        pen.rounded_rectangle([a, b, a + width * unit, b + height * unit], radius=thick / 4 * unit, fill=255)
    icon.paste(Image.new("RGBA", (big, big), rgb(DARK_THEME.text) + (255,)), (0, 0), cross)
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
