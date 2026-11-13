"""Light and dark palettes for the Doctor NoX window (Qt-free, unit-tested).

The NoX look
------------
NoX is Latin for night, so the dark theme is the night shift in a clinic: deep
night-blue surfaces, moonlight text, and status colours that glow softly like
monitor lights. The light theme is first light: cool paper and indigo ink.

Design rules
------------
* One accent, a moonlit lavender (indigo in light), is used for interaction
  only: focus, selection, the primary button and progress. It never marks a
  result, and no status colour is close to it.
* Status colours are used for results only, never for interaction, so a selected
  row can never be mistaken for a PASS. Each status has a text colour, a soft
  tint for pills and callouts, and a border, and its label is always shown as
  text as well: colour is never the only signal.
* Both themes are designed separately (not inverted). Every text/background pair
  is checked against WCAG 2.x contrast in tests: body text >= 7:1, secondary and
  status text >= 4.5:1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import os

from .model import ERROR, INFO, PASS, UNKNOWN, WARN

SYSTEM, LIGHT, DARK = "system", "light", "dark"
MODES = (SYSTEM, LIGHT, DARK)
MODE_LABEL = {SYSTEM: "Match system", LIGHT: "Light", DARK: "Dark"}
ENV_MODE = "DOCTOR_NOX_THEME"

# Window wording for each status (reports and the CLI keep the uppercase SEVERITY_LABEL).
STATUS_TEXT = {ERROR: "Error", WARN: "Warning", UNKNOWN: "Unknown", INFO: "Info", PASS: "Pass"}
# How a number of results is written: "1 error", "2 errors", "3 passed".
STATUS_COUNT = {ERROR: ("error", "errors"), WARN: ("warning", "warnings"), UNKNOWN: ("unknown", "unknown"),
                INFO: ("info", "info"), PASS: ("passed", "passed")}
# One-line headline for the overall state of a scan.
HEADLINE = {ERROR: "Problems found", WARN: "Needs attention", UNKNOWN: "Partly verified",
            INFO: "Healthy", PASS: "Healthy"}


@dataclass(frozen=True)
class Status:
    fg: str      # text and icons; >= 4.5:1 on surface and on bg
    bg: str      # soft tint behind pills and callouts
    border: str  # 1 px outline of pills


@dataclass(frozen=True)
class Theme:
    name: str
    window: str          # window background
    surface: str         # panels, lists, inputs
    surface_alt: str     # alternating rows, code blocks, header strip
    hover: str           # hovered rows and buttons
    border: str          # panel and control outlines
    border_strong: str   # input outlines, emphasised separators
    text: str
    muted: str           # secondary text, >= 4.5:1
    subtle: str          # placeholders and disabled text
    accent: str          # interaction only
    accent_hover: str
    accent_pressed: str
    on_accent: str       # text on an accent fill
    accent_soft: str     # selection background
    status: dict[str, Status] = field(default_factory=dict)


# Night shift: night-blue ink, moonlight text, lavender for interaction.
DARK_THEME = Theme(
    name=DARK,
    window="#0e1120", surface="#151a2c", surface_alt="#1a2034", hover="#212842",
    border="#262e48", border_strong="#39425f",
    text="#e9e7f4", muted="#a9abc6", subtle="#747897",
    accent="#b5a8ff", accent_hover="#c7bdff", accent_pressed="#9d8ff2", on_accent="#15112e",
    accent_soft="#2a2650",
    status={
        ERROR: Status("#ff8e8b", "#3b1c26", "#6f3141"),
        WARN: Status("#f1c35b", "#342915", "#6b5424"),
        UNKNOWN: Status("#b1b4cc", "#22263a", "#444b69"),
        INFO: Status("#7db4ff", "#172845", "#2c4b7e"),
        PASS: Status("#6cd4a1", "#14302b", "#295b49"),
    },
)

# First light: cool paper, indigo ink.
LIGHT_THEME = Theme(
    name=LIGHT,
    window="#f1f2f8", surface="#ffffff", surface_alt="#f6f6fb", hover="#ecebf6",
    border="#dadbea", border_strong="#bbbdd4",
    text="#181932", muted="#52557a", subtle="#8a8ca8",
    accent="#4a3cb5", accent_hover="#3e319d", accent_pressed="#332886", on_accent="#ffffff",
    accent_soft="#e8e5fb",
    status={
        ERROR: Status("#b3261e", "#fce8e6", "#f0b7b1"),
        WARN: Status("#875200", "#fdf0d5", "#ecce88"),
        UNKNOWN: Status("#54587a", "#ececf4", "#cdcedf"),
        INFO: Status("#1f5fa8", "#e3eefb", "#b5cdee"),
        PASS: Status("#18723f", "#e2f3e8", "#a9d8bb"),
    },
)

THEMES = {DARK: DARK_THEME, LIGHT: LIGHT_THEME}


# ── The NoX mark: a crescent moon cradling a medical cross ──
# Geometry on a 100-unit grid, shared by the window (gui.nox_mark) and the app
# icon (tools/build_doctor_icon.py), so the two never drift apart.
#
# Optical centring: the composition is symmetric about its diagonal, and the
# crescent carries most of its weight low and to the left. Centring the outline
# (shift 4) still looks low-left, and centring the weight (shift 8.6) pushes the
# cross into the corner; shifting 6 up-right along the diagonal looks centred.
@dataclass(frozen=True)
class MarkGeometry:
    moon: tuple   # the moon's disc: centre x, centre y, radius
    cut: tuple    # the circle cut out of it to leave a crescent: centre x, centre y, radius
    cross: tuple  # centre x, centre y, arm length, arm thickness (corners round by a quarter of it)
    tip: float    # radius that rounds the crescent's two tips, matching the cross's corners
    scale: float  # on an icon tile: the grid's size as a fraction of the tile (centred on it)


NOX_MARK = MarkGeometry(moon=(52, 48, 38), cut=(68, 32, 29), cross=(72, 28, 28, 10), tip=1.6, scale=0.84)
# Icons of 24 px and smaller: a larger mark, thicker crescent and bolder cross, so the cross survives.
NOX_MARK_SMALL = MarkGeometry(moon=(51, 49, 38), cut=(67, 33, 27), cross=(71, 29, 32, 13), tip=1.6, scale=0.94)
SMALL_ICON = 24                     # sizes up to this use NOX_MARK_SMALL
ICON_TILE = ("#3a2f8f", "#1a1640")  # the app icon's indigo night tile, top to bottom
ICON_MARGIN = 0.03                  # transparent margin around the tile, as a fraction of the icon
ICON_RADIUS = 0.22                  # the tile's corner radius, as a fraction of the tile


def mark_for(size: int) -> MarkGeometry:
    return NOX_MARK_SMALL if size <= SMALL_ICON else NOX_MARK


def crescent_tips(mark: MarkGeometry, reach: float = 20.0, overlap: float = 2.0):
    """How to round the crescent's two sharp tips, as (fillet centre, cut-away quad) per tip.

    The rounded crescent is: the moon's disc, minus the cut-out disc, minus each cut-away quad,
    plus each fillet disc (radius mark.tip). The fillet touches the moon's edge from inside and
    the cut-out's edge from outside; the quad covers everything beyond the line between those two
    touching points, on the side of the sharp tip.
    """
    (mx, my, big), (cx, cy, small), rho = mark.moon, mark.cut, mark.tip
    d = math.hypot(cx - mx, cy - my)
    ux, uy = (cx - mx) / d, (cy - my) / d

    def meet(r1, r2):  # where the circle (moon centre, r1) crosses the circle (cut centre, r2)
        a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
        h = math.sqrt(max(0.0, r1 * r1 - a * a))
        px, py = mx + a * ux, my + a * uy
        return (px - h * uy, py + h * ux), (px + h * uy, py - h * ux)

    tips = []
    for (fx, fy), (tx, ty) in zip(meet(big - rho, small + rho), meet(big, small)):
        fm, fc = math.hypot(fx - mx, fy - my), math.hypot(fx - cx, fy - cy)
        ax, ay = mx + (fx - mx) / fm * big, my + (fy - my) / fm * big       # touches the moon's edge
        bx, by = cx + (fx - cx) / fc * small, cy + (fy - cy) / fc * small   # touches the cut-out's edge
        lx, ly = (bx - ax), (by - ay)
        length = math.hypot(lx, ly)
        lx, ly = lx / length, ly / length
        nx, ny = ly, -lx                                                    # normal, towards the tip
        if (tx - ax) * nx + (ty - ay) * ny < 0:
            nx, ny = -nx, -ny
        a0 = (ax - lx * overlap, ay - ly * overlap)
        b0 = (bx + lx * overlap, by + ly * overlap)
        quad = (a0, b0, (b0[0] + nx * reach, b0[1] + ny * reach), (a0[0] + nx * reach, a0[1] + ny * reach))
        tips.append(((fx, fy), quad))
    return tips


def status_count(severity: str, n: int) -> str:
    one, many = STATUS_COUNT[severity]
    return f"{n} {one if n == 1 else many}"


def requested_mode(environ=None) -> str:
    """DOCTOR_NOX_THEME=light|dark|system; anything else means follow the system."""
    value = (environ if environ is not None else os.environ).get(ENV_MODE, "").strip().lower()
    return value if value in MODES else SYSTEM


def resolve(mode: str, system_prefers_dark: bool) -> Theme:
    if mode == LIGHT:
        return LIGHT_THEME
    if mode == DARK:
        return DARK_THEME
    return DARK_THEME if system_prefers_dark else LIGHT_THEME


# ── WCAG contrast (used by tests and available for future palette tweaks) ──
def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)
