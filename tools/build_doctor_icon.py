#!/usr/bin/env python3
"""Build the Doctor NoX app icon from RoYoshi's original badge artwork.

Developer tool only (needs Pillow); never runs in the image build. After
replacing the source art, update its checksum below deliberately and run:

    python3 tools/build_doctor_icon.py

Doctor has its own identity (the magenta medical badge), independent of the
FE-XicoOS top hat. The artwork is never redrawn, recoloured, sharpened or
stretched: the script trims fully transparent margins, centres the badge on a
square transparent canvas and scales it down proportionally. Raster stays raster.

Outputs (committed):
  data/icons/hicolor/<N>x<N>/apps/io.github.royoshi.doctornox.png
  (installed under /usr/share/icons/hicolor by the RPM)
"""
import hashlib
from pathlib import Path
import sys

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ICON_NAME = "io.github.royoshi.doctornox"
SOURCE = ROOT / "tools/doctor-nox-icon-source.png"
SOURCE_SHA256 = "4a0a76039a3e586b0b638ec95121e4532c754611112b4422e516466d275fa642"
HICOLOR = ROOT / "data/icons/hicolor"
PNG_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)
MARGIN = 1 / 32   # transparent margin on each side of the square icon


def main() -> int:
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != SOURCE_SHA256:
        sys.exit(f"{SOURCE.name} changed; update SOURCE_SHA256 deliberately before regenerating")
    art = Image.open(SOURCE)
    if art.mode != "RGBA":
        sys.exit("the Doctor source art must be RGBA with real transparency")
    art = art.crop(art.getchannel("A").getbbox())
    side = round(max(art.size) / (1 - 2 * MARGIN))
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(art, ((side - art.width) // 2, (side - art.height) // 2))
    for size in PNG_SIZES:
        if size > side:
            sys.exit(f"refusing to upscale the {side}px badge to {size}px")
        target = HICOLOR / f"{size}x{size}/apps/{ICON_NAME}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        square.resize((size, size), Image.LANCZOS).save(target, optimize=True)
        print(target.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
