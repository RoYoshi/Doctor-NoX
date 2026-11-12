"""Light and dark palettes for the Doctor NoX window (Qt-free, unit-tested).

Design rules
------------
* One accent, a calm diagnostic cyan, is used for interaction only: focus,
  selection, the primary button and progress. It never marks a result.
* Status colours are used for results only, never for interaction, so a selected
  row can never be mistaken for a PASS. Each status has a text colour, a soft
  tint for pills and callouts, and a border, and its label is always shown as
  text as well: colour is never the only signal.
* Both themes are designed separately (not inverted). Every text/background pair
  is checked against WCAG 2.x contrast in tests: body and status text >= 4.5:1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os

from .model import ERROR, INFO, PASS, UNKNOWN, WARN

SYSTEM, LIGHT, DARK = "system", "light", "dark"
MODES = (SYSTEM, LIGHT, DARK)
MODE_LABEL = {SYSTEM: "Match system", LIGHT: "Light", DARK: "Dark"}
ENV_MODE = "DOCTOR_NOX_THEME"

# Window wording for each status (reports and the CLI keep the uppercase SEVERITY_LABEL).
STATUS_TEXT = {ERROR: "Error", WARN: "Warning", UNKNOWN: "Unknown", INFO: "Info", PASS: "Pass"}
STATUS_PLURAL = {ERROR: "errors", WARN: "warnings", UNKNOWN: "unknown", INFO: "info", PASS: "passed"}
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


DARK_THEME = Theme(
    name=DARK,
    window="#121519", surface="#181c21", surface_alt="#1e232a", hover="#252b33",
    border="#2c333c", border_strong="#3d4651",
    text="#e4e8ed", muted="#a3acb8", subtle="#737d8a",
    accent="#3fb4cf", accent_hover="#5ec4db", accent_pressed="#2f9cb6", on_accent="#071317",
    accent_soft="#1b3640",
    status={
        ERROR: Status("#f47c74", "#3a1e1d", "#6e302c"),
        WARN: Status("#e8b84a", "#352b14", "#6a5221"),
        UNKNOWN: Status("#a9b2be", "#262b32", "#454e59"),
        INFO: Status("#78b6f0", "#182b40", "#2d4d70"),
        PASS: Status("#5fca91", "#16302a", "#2a5a42"),
    },
)

LIGHT_THEME = Theme(
    name=LIGHT,
    window="#f3f5f7", surface="#ffffff", surface_alt="#f6f8fa", hover="#edf0f3",
    border="#d8dde3", border_strong="#bcc4ce",
    text="#1b222b", muted="#536070", subtle="#8a94a0",
    accent="#0b7a93", accent_hover="#096a80", accent_pressed="#08596b", on_accent="#ffffff",
    accent_soft="#dcf0f5",
    status={
        ERROR: Status("#b3261e", "#fce8e6", "#f0b7b1"),
        WARN: Status("#875200", "#fdf0d5", "#ecce88"),
        UNKNOWN: Status("#56616d", "#eceff2", "#cdd3da"),
        INFO: Status("#1f5fa8", "#e3eefb", "#b5cdee"),
        PASS: Status("#1b7a46", "#e2f3e8", "#a9d8bb"),
    },
)

THEMES = {DARK: DARK_THEME, LIGHT: LIGHT_THEME}


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
