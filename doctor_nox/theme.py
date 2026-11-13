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
