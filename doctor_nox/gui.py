"""Doctor NoX desktop window (PySide6 Qt Widgets).

Presentation only: scanning lives in engine.py, filtering/ordering/detail text
in viewmodel.py, the doctor's words and behaviour in clinic.py, colours in
theme.py. A checkup runs on a QThread; the window never blocks on a subprocess,
and it never closes while a scan thread is still alive.
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback

from PySide6.QtCore import (QEasingCurve, QEvent, QPointF, QProcess, QPropertyAnimation, QRect, QRectF, QSize,
                            QStandardPaths, Qt, QThread, QTimer, QUrl, Signal, Slot)
from PySide6.QtGui import (QAction, QActionGroup, QBrush, QColor, QDesktopServices, QFont, QFontDatabase, QFontMetrics,
                           QGuiApplication, QIcon, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPalette,
                           QPen, QPixmap, QPolygonF, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar,
                               QPushButton, QRadioButton, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QTextBrowser, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from . import APP_ID, VERSION
from . import (clinic, engine, record, referral, report as reports, settings as settings_store, theme as themes,
               viewmodel as vm)
from .context import Context
from .model import CATEGORIES, CATEGORY_TITLE, SCHEMA_VERSION, SEVERITIES
from .theme import STATUS_TEXT, Theme

# Doctor's own identity (the NoX icon), independent of any distribution logo.
ICON_NAMES = (APP_ID,)
ICON_PATHS = (f"/usr/share/icons/hicolor/512x512/apps/{APP_ID}.png",)
NARROW = 1180  # below this width the detail pane moves under the list
SEV_ROLE = Qt.ItemDataRole.UserRole + 1         # results table: severity key of the row
NAV_BADGE_ROLE = Qt.ItemDataRole.UserRole + 2   # sidebar: pill text ("3", "OK", "")
NAV_SEV_ROLE = Qt.ItemDataRole.UserRole + 3     # sidebar: pill severity ("" for none)
CHANGE_COLUMN = 4                               # results table: "Since last checkup"
ENV_FRAME = "DOCTOR_NOX_FRAME"                  # own | system: overrides the title bar setting (a way back)
SWEEP_SECONDS = 1.6                             # the heart monitor: one pass across the strip
SUBTITLE = "A doctor for your PC. Examines and prescribes; never operates."
SAFETY_NOTE = "Examines only · no root · no network requests · nothing uploaded or changed"
QT_MESSAGES: list[str] = []  # Qt warnings captured during self-tests (stylesheet parse errors fail the test)


# ── identity: icon and fonts ────────────────────────────────────────────────
def app_icon() -> QIcon:
    """Doctor's icon from the icon theme or its installed file; never a generic/Tux fallback.

    If neither exists, the NoX mark is drawn on a night tile (always the dark colours, so the
    result never depends on the theme).
    """
    for name in ICON_NAMES:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    for path in ICON_PATHS:
        if Path(path).is_file():
            icon = QIcon(path)
            if not icon.isNull():
                return icon
    return QIcon(nox_mark(themes.DARK_THEME, 64, tile=True))


def nox_mark(t: Theme, size: int, ratio: float = 1.0, tile: bool = False) -> QPixmap:
    """The NoX mark: a crescent moon (accent) cradling a medical cross (text colour).

    Drawn from theme.py's geometry, so it is sharp at any size and needs no file. With
    tile=True it is the app icon as tools/build_doctor_icon.py draws it, on the
    indigo night tile with the bolder mark at small sizes, but without the faint glow and edge
    light the tool adds from 48 px up.
    """
    pix = QPixmap(max(1, round(size * ratio)), max(1, round(size * ratio)))
    pix.setDevicePixelRatio(ratio)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.scale(size / 100, size / 100)
    mark = themes.mark_for(size) if tile else themes.NOX_MARK
    if tile:
        side = 100 * (1 - 2 * themes.ICON_MARGIN)
        edge = 100 * themes.ICON_MARGIN
        gradient = QLinearGradient(0, edge, 0, edge + side)
        gradient.setColorAt(0, QColor(themes.ICON_TILE[0]))
        gradient.setColorAt(1, QColor(themes.ICON_TILE[1]))
        painter.setBrush(QBrush(gradient))
        corner = themes.ICON_RADIUS * side
        painter.drawRoundedRect(QRectF(edge, edge, side, side), corner, corner)
        offset = edge + (1 - mark.scale) / 2 * side
        painter.translate(offset, offset)
        painter.scale(mark.scale * side / 100, mark.scale * side / 100)
    (mx, my, mr), (cx, cy, cr), (x, y, length, thick) = mark.moon, mark.cut, mark.cross
    moon = QPainterPath()
    moon.addEllipse(QPointF(mx, my), mr, mr)
    cut = QPainterPath()
    cut.addEllipse(QPointF(cx, cy), cr, cr)
    crescent = moon.subtracted(cut)
    for (fx, fy), quad in themes.crescent_tips(mark):  # round both tips
        beyond = QPainterPath()
        beyond.addPolygon(QPolygonF([QPointF(px, py) for px, py in quad]))
        beyond.closeSubpath()
        fillet = QPainterPath()
        fillet.addEllipse(QPointF(fx, fy), mark.tip, mark.tip)
        crescent = crescent.subtracted(beyond).united(fillet)
    painter.setBrush(QColor(t.accent))
    painter.drawPath(crescent)
    painter.setBrush(QColor(t.text))
    for arm in (QRectF(x - thick / 2, y - length / 2, thick, length), QRectF(x - length / 2, y - thick / 2, length, thick)):
        painter.drawRoundedRect(arm, thick / 4, thick / 4)
    painter.end()
    return pix


def choose_font() -> QFont:
    """The desktop's UI font (so Doctor matches KDE), or DOCTOR_NOX_FONT when that family exists."""
    base = QFont(QGuiApplication.font())
    family = os.environ.get("DOCTOR_NOX_FONT", "").strip()
    if family and family in set(QFontDatabase.families()):
        size = base.pointSizeF()
        base = QFont(family)
        if size > 0:
            base.setPointSizeF(size)
    if 0 < base.pointSizeF() < 9.0:
        base.setPointSizeF(9.0)
    return base


def mono_family() -> str:
    """The desktop's fixed-width font, used only for check IDs and evidence."""
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family() or "monospace"


def scaled_font(base: QFont, factor: float, weight=None) -> QFont:
    font = QFont(base)
    if font.pointSizeF() > 0:
        font.setPointSizeF(font.pointSizeF() * factor)
    elif font.pixelSize() > 0:
        font.setPixelSize(max(1, round(font.pixelSize() * factor)))
    if weight is not None:
        font.setWeight(weight)
    return font


# ── theme: palette, stylesheet, painted pills ───────────────────────────────
def system_prefers_dark(system_palette: QPalette | None = None) -> bool:
    """Qt's colour-scheme hint (KDE reports it), else the lightness of the desktop's own palette."""
    hints = QGuiApplication.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    if callable(scheme):
        value = scheme()
        if value == Qt.ColorScheme.Dark:
            return True
        if value == Qt.ColorScheme.Light:
            return False
    palette = system_palette if system_palette is not None else QGuiApplication.palette()
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def build_palette(t: Theme) -> QPalette:
    """Matching QPalette so native dialogs, menus and text selection follow the theme too."""
    role = QPalette.ColorRole
    palette = QPalette()
    for r, colour in ((role.Window, t.window), (role.WindowText, t.text), (role.Base, t.surface),
                      (role.AlternateBase, t.surface_alt), (role.Text, t.text), (role.Button, t.surface),
                      (role.ButtonText, t.text), (role.Highlight, t.accent), (role.HighlightedText, t.on_accent),
                      (role.ToolTipBase, t.surface), (role.ToolTipText, t.text), (role.PlaceholderText, t.subtle),
                      (role.Link, t.accent), (role.LinkVisited, t.accent), (role.BrightText, t.on_accent),
                      (role.Light, t.hover), (role.Midlight, t.border), (role.Mid, t.border_strong),
                      (role.Dark, t.border_strong), (role.Shadow, "#000000")):
        palette.setColor(r, QColor(colour))
    for r in (role.WindowText, role.Text, role.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, r, QColor(t.subtle))
    return palette


def stylesheet(t: Theme, own_frame: bool = False) -> str:
    chips = "".join(
        f"""QToolButton[sev="{sev}"]:checked {{ background: {s.bg}; color: {s.fg}; border-color: {s.border}; }}
    """ for sev, s in t.status.items())
    edge = f"QWidget#host {{ border: 1px solid {t.border_strong}; }}" if own_frame else ""
    return f"""
    QMainWindow, QWidget#host {{ background: {t.window}; }}
    {edge}
    QFrame#titlebar {{ background: {t.surface}; border-bottom: 1px solid {t.border}; }}
    QToolButton[windowButton="true"] {{ background: transparent; border: none; border-radius: 6px;
                 padding: 6px 8px; }}
    QToolButton[windowButton="true"]:hover {{ background: {t.hover}; }}
    QToolButton[windowButton="true"]:pressed {{ background: {t.border}; }}
    QWidget {{ color: {t.text}; }}
    QLabel#subtitle, QLabel#snapshot, QLabel#safety, QLabel#section {{ color: {t.muted}; }}
    QLabel#verdict {{ border-radius: 13px; padding: 4px 14px; font-weight: 600; }}
    QFrame#empty {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 8px; }}
    QFrame#vital {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 8px; }}
    QLabel#vitalLabel {{ color: {t.muted}; }}
    QLabel#emptyText {{ color: {t.muted}; }}
    QLineEdit {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong};
                 border-radius: 6px; padding: 6px 10px; selection-background-color: {t.accent};
                 selection-color: {t.on_accent}; }}
    QLineEdit:focus {{ border-color: {t.accent}; }}
    QPushButton, QToolButton {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong};
                 border-radius: 6px; padding: 6px 14px; }}
    QPushButton:hover, QToolButton:hover {{ background: {t.hover}; }}
    QPushButton:pressed, QToolButton:pressed {{ background: {t.border}; }}
    QPushButton:focus, QToolButton:focus {{ border-color: {t.accent}; }}
    QPushButton:disabled, QToolButton:disabled {{ background: {t.surface_alt}; color: {t.subtle};
                 border-color: {t.border}; }}
    QPushButton#primary {{ background: {t.accent}; color: {t.on_accent}; border-color: {t.accent};
                 font-weight: 600; padding: 6px 18px; }}
    QPushButton#primary:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
    QPushButton#primary:pressed {{ background: {t.accent_pressed}; border-color: {t.accent_pressed}; }}
    QPushButton#primary:focus {{ border-color: {t.text}; }}
    QPushButton#primary:disabled {{ background: {t.surface_alt}; color: {t.subtle}; border-color: {t.border}; }}
    QToolButton#menuButton {{ padding-right: 24px; }}
    QToolButton#menuButton::menu-indicator {{ subcontrol-origin: padding; subcontrol-position: right center;
                 right: 8px; }}
    QToolButton[chip="true"] {{ background: transparent; color: {t.muted}; border: 1px solid {t.border};
                 border-radius: 14px; padding: 4px 11px; }}
    QToolButton[chip="true"]:hover {{ background: {t.hover}; }}
    QToolButton[chip="true"]:focus {{ border-color: {t.accent}; }}
    {chips}
    QListWidget#nav {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 8px;
                 padding: 6px; outline: 0; }}
    QListWidget#nav::item {{ border-radius: 6px; padding: 0 4px; margin: 1px 0; min-height: 32px; }}
    QListWidget#nav::item:hover {{ background: {t.hover}; }}
    QListWidget#nav::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}
    QListWidget#nav:focus, QTreeWidget:focus {{ border-color: {t.accent}; }}
    QTreeWidget {{ background: {t.surface}; alternate-background-color: {t.surface_alt};
                 border: 1px solid {t.border}; border-radius: 8px; outline: 0; }}
    QTreeWidget::item {{ padding: 5px 6px; border: none; }}
    QTreeWidget::item:hover {{ background: {t.hover}; }}
    QTreeWidget::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}
    QHeaderView {{ background: transparent; border: none; }}
    QHeaderView::section {{ background: {t.surface_alt}; color: {t.muted}; border: none;
                 border-bottom: 1px solid {t.border}; padding: 7px 8px; font-weight: 600; }}
    QHeaderView::section:first {{ border-top-left-radius: 8px; }}
    QHeaderView::section:last {{ border-top-right-radius: 8px; }}
    QTextBrowser#details {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 8px; }}
    QTextBrowser#details:focus {{ border-color: {t.accent}; }}
    QSplitter::handle {{ background: transparent; }}
    QSplitter::handle:horizontal {{ width: 10px; }}
    QSplitter::handle:vertical {{ height: 10px; }}
    QProgressBar {{ background: {t.surface_alt}; border: none; border-radius: 3px; color: transparent; }}
    QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}
    QStatusBar {{ background: {t.surface}; color: {t.muted}; border-top: 1px solid {t.border}; }}
    QStatusBar::item {{ border: none; }}
    QMenu {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong}; padding: 4px; }}
    QMenu::item {{ padding: 6px 20px 6px 24px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}
    QMenu::item:disabled {{ color: {t.subtle}; }}
    QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 8px; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: {t.border_strong};
                 border-radius: 3px; min-height: 28px; min-width: 28px; }}
    QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {t.subtle}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QToolTip {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong}; padding: 4px 6px; }}
    """


def pill_font(base: QFont) -> QFont:
    return scaled_font(base, 0.9, QFont.Weight.DemiBold)


def pill_size(font: QFont, text: str) -> QSize:
    fm = QFontMetrics(font)
    return QSize(fm.horizontalAdvance(text) + 18, fm.height() + 6)


def paint_pill(painter: QPainter, rect: QRectF, text: str, font: QFont, status) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(QColor(status.border), 1))
    painter.setBrush(QBrush(QColor(status.bg)))
    radius = rect.height() / 2
    painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    painter.setPen(QColor(status.fg))
    painter.setFont(font)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
    painter.restore()


def window_icon(kind: str, colour: str, ratio: float = 1.0) -> QIcon:
    """The title bar's own buttons, drawn rather than taken from a font or a theme: a line
    (minimize), a square (maximize) or a cross (close), sharp at any screen scale."""
    pix = QPixmap(round(12 * ratio), round(12 * ratio))
    pix.setDevicePixelRatio(ratio)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    pen = QPen(QColor(colour), 1.3)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "minimize":
        painter.drawLine(QPointF(2.5, 9.5), QPointF(9.5, 9.5))
    elif kind == "maximize":
        painter.drawRect(QRectF(2.5, 2.5, 7, 7))
    else:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.drawLine(QPointF(2.5, 2.5), QPointF(9.5, 9.5))
        painter.drawLine(QPointF(9.5, 2.5), QPointF(2.5, 9.5))
    painter.end()
    return QIcon(pix)


def dot_icon(on_colour: str, off_colour: str, ratio: float = 1.0) -> QIcon:
    """Status dot for the filter chips: coloured when shown, grey when filtered out."""
    icon = QIcon()
    for colour, state in ((on_colour, QIcon.State.On), (off_colour, QIcon.State.Off)):
        pix = QPixmap(round(12 * ratio), round(12 * ratio))
        pix.setDevicePixelRatio(ratio)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(colour)))
        painter.drawEllipse(QRectF(2, 2, 8, 8))
        painter.end()
        icon.addPixmap(pix, QIcon.Mode.Normal, state)
    return icon


class _PillDelegate(QStyledItemDelegate):
    """Shared base: draws the themed row background, then a pill. Paint errors never crash the
    window; they are recorded on the window (the image-build self-test asserts there are none)
    and the row falls back to Qt's default text rendering."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window

    def _base(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        return opt, text

    def _failed(self, painter, option, index):
        self._window.paint_errors.append(traceback.format_exc())
        print(f"Doctor NoX: paint error\n{self._window.paint_errors[-1]}", file=sys.stderr, flush=True)
        super().paint(painter, option, index)


class StatusDelegate(_PillDelegate):
    """Results table, Status column: one fixed-width pill per row so every row lines up."""

    def paint(self, painter, option, index):
        severity = index.data(SEV_ROLE)
        if not severity:
            super().paint(painter, option, index)
            return
        try:
            opt, text = self._base(painter, option, index)
            font = pill_font(opt.font)
            size = QSize(self._window.pill_width, pill_size(font, text).height())
            rect = QRectF(opt.rect.left() + 8, opt.rect.top() + (opt.rect.height() - size.height()) / 2,
                          size.width(), size.height())
            paint_pill(painter, rect, text, font, self._window.theme.status[severity])
        except Exception:
            self._failed(painter, option, index)

    def sizeHint(self, option, index):
        base = super().sizeHint(option, index)
        height = pill_size(pill_font(option.font), "Warning").height() + 12
        return QSize(max(base.width(), self._window.pill_width + 16), max(base.height(), height))


class NavDelegate(_PillDelegate):
    """Sidebar: group title on the left, problem count (or OK) pill on the right."""

    def paint(self, painter, option, index):
        try:
            opt, title = self._base(painter, option, index)
            t = self._window.theme
            rect = opt.rect.adjusted(10, 0, -8, 0)
            right = rect.right()
            badge = index.data(NAV_BADGE_ROLE) or ""
            severity = index.data(NAV_SEV_ROLE) or ""
            if badge and severity:
                font = pill_font(opt.font)
                size = pill_size(font, badge)
                size.setWidth(max(size.width(), size.height() + 4))
                pill = QRectF(right - size.width() + 1, rect.top() + (rect.height() - size.height()) / 2,
                              size.width(), size.height())
                paint_pill(painter, pill, badge, font, t.status[severity])
                right -= size.width() + 8
            painter.save()
            selected = bool(opt.state & QStyle.StateFlag.State_Selected)
            painter.setFont(scaled_font(opt.font, 1.0, QFont.Weight.DemiBold) if selected else opt.font)
            painter.setPen(QColor(t.text))
            text_rect = QRect(rect.left(), rect.top(), max(0, right - rect.left()), rect.height())
            elided = QFontMetrics(painter.font()).elidedText(title, Qt.TextElideMode.ElideRight, text_rect.width())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)
            painter.restore()
        except Exception:
            self._failed(painter, option, index)

    def sizeHint(self, option, index):
        base = super().sizeHint(option, index)
        return QSize(base.width(), max(base.height(), QFontMetrics(option.font).height() + 16))


# ── worker ───────────────────────────────────────────────────────────────────
class VitalTile(QFrame):
    """One vital sign: a caption over a short reading, edged in its condition's colour.

    A click opens the test the reading comes from. Tiles are a shortcut for the pointer; the
    same tests are in the findings list, which is where the keyboard reaches them.
    """
    clicked = Signal(str)

    def __init__(self, label: str, mono: str = "", parent=None):
        super().__init__(parent, objectName="vital")
        self.label = label
        self.finding_id = ""
        self.reading = clinic.NO_VALUE
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 8)
        layout.setSpacing(1)
        self.caption = QLabel(label, objectName="vitalLabel")
        self.caption.setFont(scaled_font(self.font(), 0.92))
        self.value = QLabel(self.reading)
        reading_font = scaled_font(self.font(), 1.1, QFont.Weight.DemiBold)
        if mono:
            reading_font.setFamily(mono)  # figures line up, as on a bedside monitor
        self.value.setFont(reading_font)
        # The reading may be clipped, never a reason for the window to grow.
        self.value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.value.setMinimumWidth(40)
        layout.addWidget(self.caption)
        layout.addWidget(self.value)

    def show_vital(self, vital: dict, t: Theme):
        self.finding_id = vital["finding_id"]
        self.reading = vital["value"]
        severity = vital["severity"]
        edge = t.status[severity].fg if severity else t.border_strong
        self.setStyleSheet(f"QFrame#vital {{ border-left: 3px solid {edge}; }}")
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.finding_id else Qt.CursorShape.ArrowCursor)
        condition = STATUS_TEXT[severity] if severity else ""
        self.setToolTip("\n".join(x for x in (vital["detail"], condition) if x))
        self.setAccessibleName(f"{self.label}: {self.reading}" + (f". {condition}" if condition else ""))
        self._fit()

    def _fit(self):
        room = max(0, self.width() - 27)  # the margins and the coloured edge
        metrics = QFontMetrics(self.value.font())
        self.value.setText(metrics.elidedText(self.reading, Qt.TextElideMode.ElideRight, room))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.finding_id and self.rect().contains(event.pos()):
            self.clicked.emit(self.finding_id)
        super().mouseReleaseEvent(event)


class PulseLine(QWidget):
    """The heart monitor in the header: an ECG trace in the overall condition's colour.

    While a checkup runs, a bright head sweeps along it as on a bedside monitor; otherwise it is
    still. It is decoration: the condition is always written beside it. Paint errors never crash
    the window; they are recorded like the delegates' (the self-test asserts there are none).
    """

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self.colour = ""
        self.running = False
        self.motion = True
        self.phase = 0.0
        self.settling = False   # one last sweep in the condition's colour after a checkup ends
        self._since = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(16)  # about 60 frames a second; the phase follows the clock, not the ticks
        self.timer.timeout.connect(self._tick)
        self.setMinimumSize(60, 34)
        self.setMaximumHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Heart monitor")

    def sizeHint(self):
        return QSize(240, 36)

    def set_state(self, colour: str, running: bool, motion: bool):
        ended = self.running and not running
        self.colour, self.running, self.motion = colour, running, motion
        if running and motion:
            self.settling = False
            if not self.timer.isActive():
                self._since = time.monotonic()
                self.timer.start()
        elif ended and motion:
            self.settling, self._since, self.phase = True, time.monotonic(), 0.0
            self.timer.start()
        elif not self.settling or not motion:
            self.settling = False
            self.timer.stop()
            self.phase = 0.0
        self.setAccessibleDescription("Examining" if running else "At rest")
        self.update()

    def _tick(self):
        passed = (time.monotonic() - self._since) / SWEEP_SECONDS
        if self.settling and passed >= 1.0:
            self.settling = False
            self.timer.stop()
            self.phase = 0.0
        else:
            self.phase = passed % 1.0
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        try:
            points = clinic.pulse(self.width(), self.height())
            if len(points) < 2 or not self.colour:
                return
            path = QPainterPath(QPointF(*points[0]))
            for x, y in points[1:]:
                path.lineTo(x, y)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            colour = QColor(self.colour)
            pen = QPen(colour, 1.6)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            if not ((self.running or self.settling) and self.motion):
                painter.setPen(pen)
                painter.drawPath(path)
                return
            head = self.phase * self.width()
            faint = QColor(colour)
            faint.setAlphaF(0.28)
            old = QPen(pen)
            old.setColor(faint)
            painter.setClipRect(QRectF(head + 18, 0, self.width(), self.height()))  # the last sweep, fading
            painter.setPen(old)
            painter.drawPath(path)
            painter.setClipRect(QRectF(0, 0, head, self.height()))                  # this sweep
            painter.setPen(pen)
            painter.drawPath(path)
            painter.setClipping(False)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(colour))
            painter.drawEllipse(QPointF(head, clinic.pulse_y(points, head)), 2.4, 2.4)
        except Exception:
            self._window.paint_errors.append(traceback.format_exc())
            print(f"Doctor NoX: paint error\n{self._window.paint_errors[-1]}", file=sys.stderr, flush=True)
        finally:
            painter.end()


class TitleBar(QFrame):
    """The window's header, which is also its title bar when Doctor NoX draws its own frame:
    drag it to move the window, double-click it to maximize."""

    def __init__(self, window):
        super().__init__(window, objectName="titlebar")
        self._window = window

    def mousePressEvent(self, event):
        handle = self._window.windowHandle()
        if self._window.own_frame and event.button() == Qt.MouseButton.LeftButton and handle is not None:
            handle.startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self._window.own_frame and event.button() == Qt.MouseButton.LeftButton:
            self._window.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class SettingsDialog(QDialog):
    """Your settings. Nothing changes until OK; Cancel leaves everything as it was."""

    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle("Doctor NoX settings")
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(8)

        def heading(text):
            label = QLabel(text)
            label.setFont(scaled_font(self.font(), 1.05, QFont.Weight.DemiBold))
            layout.addSpacing(6)
            layout.addWidget(label)

        def hint(text):
            label = QLabel(text, objectName="subtitle", wordWrap=True)
            layout.addWidget(label)

        heading("Appearance")
        row = QHBoxLayout()
        self.theme_buttons = {}
        group = QButtonGroup(self)
        for mode in themes.MODES:
            button = QRadioButton(themes.MODE_LABEL[mode])
            group.addButton(button)
            row.addWidget(button)
            self.theme_buttons[mode] = button
        row.addStretch(1)
        layout.addLayout(row)
        hint("Match system follows your desktop's light or dark setting, and switches when it does.")
        if window.theme_locked:
            hint(f"{themes.ENV_MODE} is set in the environment, so it decides the theme until it is removed.")
        self.motion = QCheckBox("Animate the heart monitor during a checkup")
        layout.addWidget(self.motion)
        self.own_frame = QCheckBox("Use Doctor NoX's own title bar instead of the desktop's window frame")
        layout.addWidget(self.own_frame)
        hint("Applies the next time Doctor NoX opens. With its own title bar: drag the header to move, "
             "double-click it to maximize, and resize from the bottom-right corner.")

        heading("Checkups")
        self.at_launch = QCheckBox("Start a checkup when Doctor NoX opens")
        layout.addWidget(self.at_launch)
        self.remember = QCheckBox("Remember my checkups (the last 30), for follow-ups and a prognosis")
        layout.addWidget(self.remember)
        hint("Turning this off deletes them.")
        limit = QHBoxLayout()
        limit.addWidget(QLabel("Stop a checkup after"))
        self.deadline = QSpinBox()
        self.deadline.setRange(*settings_store.DEADLINE_RANGE)
        self.deadline.setSuffix(" seconds")
        self.deadline.setAccessibleName("Stop a checkup after this many seconds")
        limit.addWidget(self.deadline)
        limit.addStretch(1)
        layout.addLayout(limit)

        heading("Areas to examine")
        grid = QGridLayout()
        self.area_boxes = {}
        for i, (key, title) in enumerate(CATEGORIES):
            box = QCheckBox(title)
            grid.addWidget(box, i // 2, i % 2)
            self.area_boxes[key] = box
        layout.addLayout(grid)
        hint("An area you turn off is left out of every checkup. With all of them off, all are examined.")

        layout.addSpacing(8)
        standard = QDialogButtonBox.StandardButton
        buttons = QDialogButtonBox(standard.Ok | standard.Cancel | standard.RestoreDefaults)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(standard.RestoreDefaults).clicked.connect(self.restore_defaults)
        layout.addWidget(buttons)
        self.set_values(window.settings)

    def restore_defaults(self):
        self.set_values(settings_store.clean(None))

    def set_values(self, values: dict):
        values = settings_store.clean(values)
        for mode, button in self.theme_buttons.items():
            button.setChecked(mode == values["theme"])
        self.motion.setChecked(values["monitor_motion"])
        self.own_frame.setChecked(values["own_frame"])
        self.at_launch.setChecked(values["checkup_at_launch"])
        self.remember.setChecked(values["remember"])
        self.deadline.setValue(values["deadline"])
        for key, box in self.area_boxes.items():
            box.setChecked(key not in values["skip_areas"])

    def values(self) -> dict:
        theme = next((mode for mode, button in self.theme_buttons.items() if button.isChecked()), themes.SYSTEM)
        return settings_store.clean({
            "theme": theme, "monitor_motion": self.motion.isChecked(), "own_frame": self.own_frame.isChecked(),
            "checkup_at_launch": self.at_launch.isChecked(), "remember": self.remember.isChecked(),
            "deadline": self.deadline.value(),
            "skip_areas": [key for key, box in self.area_boxes.items() if not box.isChecked()]})


class ScanThread(QThread):
    """Runs engine.scan off the GUI thread.

    Thread rule: every signal below is emitted from THIS thread and must be
    connected to a @Slot method of a QObject living in the GUI thread (the
    Window), never to a Python lambda. PySide6 dispatches lambda receivers on
    the emitting thread, which would run widget code off the GUI thread
    (a native crash with no Python traceback — the v1.0 build failure).
    """
    progressed = Signal(int, int, str)
    completed = Signal(object, int)
    failed = Signal(str, int)
    done = Signal(int)  # always the last thing run() does, after completed/failed

    def __init__(self, generation: int, scan_func, context_factory, parent=None, options=None):
        super().__init__(parent)
        self.generation = generation
        self._scan = scan_func
        self._options = dict(options or {})  # areas and time limit from your settings; {} for the defaults
        self.ctx = context_factory()

    def cancel(self):
        self.ctx.cancel.set()  # the runner kills in-flight commands; the engine wraps up

    def run(self):
        try:
            # This callback runs on the scan thread; it only emits (thread-safe), never touches widgets.
            result = self._scan(self.ctx, progress=self._emit_progress, **self._options)
            self.completed.emit(result, self.generation)
        except Exception as exc:  # surfaced in the UI, never a crash
            self.failed.emit(f"{type(exc).__name__}: {exc}", self.generation)
        finally:
            self.done.emit(self.generation)

    def _emit_progress(self, done, total, title):
        self.progressed.emit(done, total, title)


# ── window ───────────────────────────────────────────────────────────────────
class Window(QMainWindow):
    def __init__(self, *, scan_func=engine.scan, context_factory=Context, autoscan=True, interactive=True,
                 record_path=None, settings_path=None, probe_ids=None, which=None, environ=None):
        super().__init__()
        self.interactive = interactive        # False in self-tests: no modal dialogs, nothing opened or copied
        self._probe_ids = probe_ids           # the tests that can be run again alone (None: the real ones)
        self._which, self._environ = which, environ  # for referrals; None: the real PATH and environment
        self._partial = ""                    # the test being checked again, while that runs
        self.rechecked: dict[str, str] = {}   # test ID: what its re-check found, for the details pane
        self.last_copied = ""                 # what "Copy" last copied
        self.last_opened: tuple = ()          # the referral or address last opened (kept in self-tests)
        # Your settings (None: the defaults, and nothing is written).
        self.settings_path = settings_path
        self.settings = settings_store.load(settings_path)
        self.report_skipped: list[str] = []   # areas left out of the checkup on screen
        self._starting_skipped: list[str] = []
        # Follow-ups: where the last checkup is remembered (None: nowhere, nothing is written).
        self.record_file = record_path
        self.history = record.load(self.record_path)  # your chart, oldest first; the last one is compared with
        self.follow = None                    # clinic.follow_up() for the report on screen
        self.symptom = clinic.GENERAL         # "What's wrong?"
        self.workers: dict[int, ScanThread] = {}  # every thread not yet joined, by generation
        self.thread_violations: list[str] = []    # UI code that ran off the GUI thread (must stay empty)
        self.paint_errors: list[str] = []         # delegate paint failures (must stay empty)
        self.last_export_error = ""
        self._scan_func, self._context_factory = scan_func, context_factory
        self.report = None
        self.worker: ScanThread | None = None
        self.busy = False          # cleared only when the CURRENT worker's finished() arrives
        self.last_error = ""
        self.identity = ("", "")   # user/host of the last scan, for redaction
        self.generation = 0
        self.close_requested = False
        self.category = vm.OVERVIEW
        self.severities = set(SEVERITIES)
        self.selected_id = ""
        # The desktop's palette before Doctor applies its own; used to follow the system scheme.
        self._system_palette = QPalette(QGuiApplication.palette())
        # DOCTOR_NOX_THEME, when set, decides the theme; otherwise your setting does.
        frame = os.environ.get(ENV_FRAME, "").strip().lower()
        self.own_frame = frame == "own" if frame in ("own", "system") else self.settings["own_frame"]
        if self.own_frame:
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        asked = os.environ.get(themes.ENV_MODE, "").strip().lower()
        self.theme_locked = asked in themes.MODES
        self.theme_mode = asked if self.theme_locked else self.settings["theme"]
        self.theme: Theme = themes.resolve(self.theme_mode, system_prefers_dark(self._system_palette))
        self.mono = mono_family()
        self.pill_width = 0
        self.setObjectName("doctorWindow")
        self.setWindowTitle("Doctor NoX")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(QSize(720, 480))
        self.resize(1240, 780)
        self._build()
        self._shortcuts()
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._system_scheme_changed)
        self.apply_theme()
        if autoscan and self.settings["checkup_at_launch"]:
            QTimer.singleShot(0, self.start_scan)

    @property
    def record_path(self):
        """Where the last checkup is remembered, or None when that is off."""
        return self.record_file if self.settings["remember"] else None

    # layout ------------------------------------------------------------------
    def _build(self):
        host = QWidget(objectName="host")
        self.setCentralWidget(host)
        outer = QVBoxLayout(host)
        outer.setContentsMargins(1, 1, 1, 0) if self.own_frame else outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.titlebar = TitleBar(self)
        outer.addWidget(self.titlebar)
        root = QVBoxLayout()
        root.setContentsMargins(18, 12, 18, 12)
        root.setSpacing(12)
        outer.addLayout(root, 1)

        # Header: identity on the left, overall state on the right.
        head = QHBoxLayout(self.titlebar)
        head.setContentsMargins(18, 10, 10 if self.own_frame else 18, 10)
        head.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(QSize(40, 40), self.devicePixelRatioF()))
        logo.setAccessibleName("Doctor NoX icon")
        head.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        self.title = QLabel("Doctor NoX", objectName="title")
        self.title.setFont(scaled_font(self.font(), 1.45, QFont.Weight.DemiBold))
        self.subtitle = QLabel(SUBTITLE, objectName="subtitle")
        self.subtitle.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles)
        self.pulse = PulseLine(self)
        head.addWidget(self.pulse, 1)
        right = QVBoxLayout()
        right.setSpacing(4)
        self.verdict = QLabel("—", objectName="verdict", alignment=Qt.AlignmentFlag.AlignCenter)
        self.verdict.setAccessibleName("Overall condition")
        self.snapshot = QLabel("No checkup yet", objectName="snapshot", alignment=Qt.AlignmentFlag.AlignRight)
        right.addWidget(self.verdict, 0, Qt.AlignmentFlag.AlignRight)
        right.addWidget(self.snapshot, 0, Qt.AlignmentFlag.AlignRight)
        head.addLayout(right)
        # Its own window buttons, when Doctor NoX draws its own frame.
        self.window_buttons = {}
        controls = QHBoxLayout()
        controls.setSpacing(2)
        for kind, name, slot in (("minimize", "Minimize", self.showMinimized),
                                 ("maximize", "Maximize or restore", self.toggle_maximized),
                                 ("close", "Close", self.close)):
            button = QToolButton()
            button.setProperty("windowButton", True)
            button.setIconSize(QSize(12, 12))
            button.setToolTip(name)
            button.setAccessibleName(name)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the desktop's own shortcuts cover these
            button.clicked.connect(slot)
            button.setVisible(self.own_frame)
            self.window_buttons[kind] = button
            controls.addWidget(button)
        head.addLayout(controls)

        # The consultation: what's wrong on the left, the checkup and its paperwork on the right.
        consult = QHBoxLayout()
        consult.setSpacing(8)
        self.complaint = QLineEdit(placeholderText="What's wrong? Type it (“no sound”, “wifi keeps dropping”) "
                                                   "and press Enter")
        self.complaint.setClearButtonEnabled(True)
        self.complaint.setMinimumWidth(200)
        self.complaint.setAccessibleName("What's wrong? Type it in your own words")
        self.complaint.returnPressed.connect(self.describe)
        consult.addWidget(self.complaint, 1)
        self.symptom_btn = QToolButton(objectName="menuButton")
        self.symptom_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.symptom_btn.setToolTip("Or pick what's wrong: the tests that bear on it are shown, "
                                    "and the verdict speaks to it")
        self.symptom_btn.setAccessibleName("Pick what's wrong")
        complaints = QMenu(self.symptom_btn)
        complaint_group = QActionGroup(complaints)
        complaint_group.setExclusive(True)
        self.symptom_actions = {}
        for symptom in clinic.SYMPTOMS:
            act = QAction(symptom.label, complaints, checkable=True)
            act.triggered.connect(lambda _=False, key=symptom.key: self.set_symptom(key))
            complaint_group.addAction(act)
            complaints.addAction(act)
            self.symptom_actions[symptom.key] = act
        self.symptom_btn.setMenu(complaints)
        consult.addWidget(self.symptom_btn)
        self.progress = QProgressBar(maximumWidth=140, visible=False)
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Checkup progress")
        self.progress_glide = QPropertyAnimation(self.progress, b"value", self)  # the bar fills, never jumps
        self.progress_glide.setDuration(220)
        self.progress_glide.setEasingCurve(QEasingCurve.Type.OutCubic)
        consult.addWidget(self.progress)
        self.cancel_btn = QPushButton("Cancel", visible=False)
        self.cancel_btn.setToolTip("Stop the checkup (Esc)")
        self.cancel_btn.clicked.connect(self.cancel_scan)
        consult.addWidget(self.cancel_btn)
        self.scan_btn = QPushButton("Start checkup", objectName="primary")
        self.scan_btn.setToolTip("Examine this PC: every test only looks, none changes anything (F5)")
        self.scan_btn.clicked.connect(self.start_scan)
        consult.addWidget(self.scan_btn)
        self.copy_btn = QPushButton("Copy doctor's note")
        self.copy_btn.setToolTip("Copy the verdict and findings, redacted for sharing (Ctrl+Shift+C)")
        self.copy_btn.clicked.connect(self.copy_note)
        consult.addWidget(self.copy_btn)
        self.export_btn = QToolButton(text="Export", objectName="menuButton")
        self.export_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.export_btn.setToolTip("Save or copy the full report. Redacted reports hide user, host and "
                                   "network details.")
        menu = QMenu(self.export_btn)
        for label, redacted, fmt in (("Save redacted report for sharing (.txt)…", True, "text"),
                                     ("Save redacted report for sharing (.json)…", True, "json"),
                                     (None, None, None),
                                     ("Save full local report (.json)…", False, "json"),
                                     ("Save full local report (.txt)…", False, "text"),
                                     ("Copy full local report to the clipboard", False, "clipboard")):
            if label is None:
                menu.addSeparator()
                continue
            act = QAction(label, menu)
            act.triggered.connect(lambda _=False, r=redacted, f=fmt: self.export(r, f))
            menu.addAction(act)
        self.export_btn.setMenu(menu)
        self.export_btn.setAccessibleName("Export report")
        consult.addWidget(self.export_btn)
        self.more_btn = QToolButton(text="Menu", objectName="menuButton")
        self.more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_btn.setToolTip("Settings, appearance and information about Doctor NoX")
        self.more_btn.setAccessibleName("Menu")
        more = QMenu(self.more_btn)
        preferences = QAction("Settings…", more)
        preferences.triggered.connect(self.open_settings)
        more.addAction(preferences)
        appearance = more.addMenu("Appearance")
        group = QActionGroup(appearance)
        group.setExclusive(True)
        self.theme_actions = {}
        for mode in themes.MODES:
            act = QAction(themes.MODE_LABEL[mode], appearance, checkable=True)
            act.triggered.connect(lambda _=False, m=mode: self.set_theme_mode(m))
            group.addAction(act)
            appearance.addAction(act)
            self.theme_actions[mode] = act
        more.addSeparator()
        about = QAction("About Doctor NoX", more)
        about.triggered.connect(self.show_about)
        more.addAction(about)
        self.more_btn.setMenu(more)
        consult.addWidget(self.more_btn)
        root.addLayout(consult)

        # Vital signs: four readings from the last checkup; a click opens the test behind one.
        vital_row = QHBoxLayout()
        vital_row.setSpacing(8)
        self.vital_tiles = {}
        for vital in clinic.vitals(None):
            tile = VitalTile(vital["label"], self.mono)
            tile.clicked.connect(self.open_finding)
            self.vital_tiles[vital["key"]] = tile
            vital_row.addWidget(tile, 1)
        root.addLayout(vital_row)

        # Above the findings: search and which conditions are shown.
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit(placeholderText="Find in the findings…  (Ctrl+F, try is:problem)")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(160)
        self.search.setAccessibleName("Find in the findings")
        self.search.textChanged.connect(self.render_list)
        bar.addWidget(self.search, 1)
        self.sev_buttons = {}
        for sev in SEVERITIES:
            b = QToolButton(text=STATUS_TEXT[sev])
            b.setCheckable(True)
            b.setChecked(True)
            b.setProperty("chip", True)
            b.setProperty("sev", sev)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setIconSize(QSize(12, 12))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(f"Show or hide the findings that are “{STATUS_TEXT[sev]}”")
            b.setAccessibleName(f"Show findings: {STATUS_TEXT[sev]}")
            b.toggled.connect(lambda on, s=sev: self._toggle_severity(s, on))
            self.sev_buttons[sev] = b
            bar.addWidget(b)
        root.addLayout(bar)

        # Body: areas | findings | details (details move below the findings when narrow).
        self.body = QSplitter(Qt.Orientation.Horizontal)
        self.body.setChildrenCollapsible(False)
        nav_panel = QWidget()
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(6)
        section = QLabel("Areas examined", objectName="section")
        section.setFont(scaled_font(self.font(), 0.92, QFont.Weight.DemiBold))
        nav_layout.addWidget(section)
        self.nav = QListWidget(objectName="nav")
        self.nav.setAccessibleName("Areas examined")
        self.nav.setMinimumWidth(190)
        self.nav.setUniformItemSizes(True)
        self.nav.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.nav_delegate = NavDelegate(self)
        self.nav.setItemDelegate(self.nav_delegate)
        self.nav.currentItemChanged.connect(self._category_changed)
        nav_layout.addWidget(self.nav, 1)
        self.body.addWidget(nav_panel)

        self.inner = QSplitter(Qt.Orientation.Horizontal)
        self.inner.setChildrenCollapsible(False)
        self.stack = QStackedWidget()
        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Condition", "Area", "Test", "Finding", "Since last checkup"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setAccessibleName("Findings")
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)  # glides, not row jumps
        self.status_delegate = StatusDelegate(self)
        self.tree.setItemDelegateForColumn(0, self.status_delegate)
        hdr = self.tree.header()
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hdr.setHighlightSections(False)
        hdr.setStretchLastSection(False)  # the Finding column takes the room, not the last one
        for col, mode in enumerate((QHeaderView.ResizeMode.ResizeToContents, QHeaderView.ResizeMode.ResizeToContents,
                                    QHeaderView.ResizeMode.ResizeToContents, QHeaderView.ResizeMode.Stretch,
                                    QHeaderView.ResizeMode.ResizeToContents)):
            hdr.setSectionResizeMode(col, mode)
        self.tree.currentItemChanged.connect(self._finding_changed)
        # Empty state: the NoX mark (before the first checkup) above a short explanation.
        self.empty_box = QFrame(objectName="empty")
        empty_layout = QVBoxLayout(self.empty_box)
        empty_layout.setContentsMargins(32, 32, 32, 32)
        empty_layout.setSpacing(14)
        empty_layout.addStretch(1)
        self.empty_mark = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.empty_mark.setAccessibleName("Doctor NoX")
        empty_layout.addWidget(self.empty_mark)
        self.empty = QLabel(objectName="emptyText", alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        empty_layout.addWidget(self.empty)
        empty_layout.addStretch(1)
        self.stack.addWidget(self.tree)
        self.stack.addWidget(self.empty_box)
        self.inner.addWidget(self.stack)
        self.details = QTextBrowser(objectName="details")
        self.details.setOpenLinks(False)
        self.details.anchorClicked.connect(self._detail_link)
        self.details.setAccessibleName("Diagnosis and treatment")
        self.details.document().setDocumentMargin(16)
        self.inner.addWidget(self.details)
        self.inner.setStretchFactor(0, 3)
        self.inner.setStretchFactor(1, 2)
        self.body.addWidget(self.inner)
        self.body.setStretchFactor(1, 1)
        self.body.setSizes([220, 1000])
        root.addWidget(self.body, 1)

        self.safety = QLabel(SAFETY_NOTE, objectName="safety")
        self.safety.setAccessibleName("Doctor NoX only examines: it needs no root, makes no network requests, "
                                      "uploads nothing and changes nothing on your system")
        self.statusBar().addPermanentWidget(self.safety)
        self.statusBar().setSizeGripEnabled(self.own_frame)  # its own frame has no edges to drag
        self.statusBar().showMessage(clinic.greeting(datetime.datetime.now().hour))

    # theme -------------------------------------------------------------------
    def set_theme_mode(self, mode: str):
        self.theme_mode = mode if mode in themes.MODES else themes.SYSTEM
        if self.settings["theme"] != self.theme_mode:
            self.settings["theme"] = self.theme_mode
            settings_store.save(self.settings_path, self.settings)
        self.apply_theme()

    def open_settings(self):
        """The settings dialog. In self-tests it is returned unopened, to be driven without a modal loop."""
        dialog = SettingsDialog(self)
        if not self.interactive:
            return dialog
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_settings(dialog.values())
        dialog.deleteLater()
        return None

    def apply_settings(self, values: dict):
        was, new = self.settings, settings_store.clean(values)
        self.settings = new
        saved = settings_store.save(self.settings_path, new)
        if was["remember"] and not new["remember"]:
            record.forget(self.record_file)
            self.history, self.follow = [], None
        elif new["remember"] and not was["remember"] and self.report and record.save(self.record_path, self.report):
            self.history = record.load(self.record_path)
        if not self.theme_locked:
            self.theme_mode = new["theme"]
        self.apply_theme()  # renders everything again, the heart monitor included
        message = ("Settings saved." if saved or self.settings_path is None
                   else "Settings apply now, but they couldn't be saved.")
        if (new["skip_areas"], new["deadline"]) != (was["skip_areas"], was["deadline"]):
            message += " Areas and the time limit apply from the next checkup."
        self.statusBar().showMessage(message)

    def _system_scheme_changed(self, *_args):
        if self.theme_mode == themes.SYSTEM:
            self.apply_theme()

    def apply_theme(self):
        """Resolve and apply the palette, stylesheet, chip icons and HTML colours, then re-render."""
        self.theme = themes.resolve(self.theme_mode, system_prefers_dark(self._system_palette))
        t = self.theme
        app = QApplication.instance()
        if app is not None:
            app.setPalette(build_palette(t))
        self.setStyleSheet(stylesheet(t, self.own_frame))
        font = pill_font(self.font())
        self.pill_width = max(pill_size(font, text).width() for text in STATUS_TEXT.values())
        for sev, button in self.sev_buttons.items():
            button.setIcon(dot_icon(t.status[sev].fg, t.subtle, self.devicePixelRatioF()))
        for kind, button in self.window_buttons.items():
            button.setIcon(window_icon(kind, t.muted, self.devicePixelRatioF()))
        self.empty_mark.setPixmap(nox_mark(t, 96, self.devicePixelRatioF()))
        for mode, action in self.theme_actions.items():
            action.setChecked(mode == self.theme_mode)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.tree.header().resizeSection(0, self.pill_width + 20)
        self.render_all()

    def _shortcuts(self):
        for keys, slot in ((QKeySequence.StandardKey.Find, self.search.setFocus),
                           (QKeySequence(Qt.Key.Key_F5), self.start_scan),
                           (QKeySequence("Ctrl+R"), self.start_scan),
                           (QKeySequence(Qt.Key.Key_Escape), self._escape),
                           (QKeySequence("Ctrl+Shift+C"), self.copy_note),
                           (QKeySequence.StandardKey.Save, lambda: self.export(True, "text")),
                           (QKeySequence("Ctrl+,"), self.open_settings),
                           (QKeySequence("Ctrl+L"), self.complaint.setFocus),
                           (QKeySequence.StandardKey.HelpContents, self.show_about),
                           (QKeySequence.StandardKey.Quit, self.close)):
            QShortcut(keys, self, activated=slot)

    def resizeEvent(self, event):
        narrow = event.size().width() < NARROW
        want = Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
        if self.inner.orientation() != want:
            self.inner.setOrientation(want)
        super().resizeEvent(event)

    # scanning ----------------------------------------------------------------
    def scanning(self) -> bool:
        # Not QThread.isRunning(): that turns False before finished() is delivered.
        return self.busy

    def start_scan(self):
        self._begin("")

    def probe_ids(self):
        if self._probe_ids is None:
            from . import probes
            self._probe_ids = [p.id for p in probes.load_all()]
        return self._probe_ids

    def recheck(self, finding_id: str) -> bool:
        """Run one test again (after a treatment, say) and put its result into the checkup on screen."""
        probe_id = clinic.probe_for(finding_id, self.probe_ids()) if self.report else None
        if not probe_id or self.scanning() or self.close_requested:
            return False
        self._begin(probe_id)
        return True

    def _begin(self, only: str):
        if self.scanning() or self.close_requested:
            return  # rapid refreshes are ignored, never queued
        self.generation += 1
        self.busy = True
        self.last_error = ""
        self._partial = only
        options = settings_store.scan_options(self.settings)
        if only:
            options["selected"] = {only}
        else:
            self._starting_skipped = list(self.settings["skip_areas"])
            self.rechecked = {}
        worker = ScanThread(self.generation, self._scan_func, self._context_factory, self, options=options)
        queued = Qt.ConnectionType.QueuedConnection  # receiver: this window, in the GUI thread
        worker.progressed.connect(self._progress, queued)
        worker.completed.connect(self._completed, queued)
        worker.failed.connect(self._failed, queued)
        worker.done.connect(self._scan_thread_done, queued)
        self.workers[self.generation] = worker
        self.worker = worker
        self.scan_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.statusBar().showMessage("Checking that one test again…" if only
                                     else "Examining… every test only looks; nothing is changed.")
        self.worker.start()
        self.render_all()

    def cancel_scan(self):
        if self.scanning() and self.worker is not None:
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.statusBar().showMessage("Stopping the checkup… ending the tests that are running.")

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def _escape(self):
        if self.scanning():
            self.cancel_scan()
        elif self.search.text():
            self.search.clear()

    def _on_gui_thread(self, where: str) -> bool:
        app = QApplication.instance()
        if app is not None and QThread.currentThread() is not app.thread():
            self.thread_violations.append(where)
            print(f"Doctor NoX: BUG: {where} ran off the GUI thread", file=sys.stderr, flush=True)
            return False
        return True

    @Slot(int, int, str)
    def _progress(self, done, total, title):
        if not self._on_gui_thread("_progress"):
            return
        if self.progress.maximum() != max(total, 1) * 100:
            self.progress.setRange(0, max(total, 1) * 100)
            self.progress.setValue(0)
        self.progress_glide.stop()
        self.progress_glide.setStartValue(self.progress.value())
        self.progress_glide.setEndValue(done * 100)
        self.progress_glide.start()
        self.statusBar().showMessage(f"Examining {done} of {total}: {title}")

    @Slot(object, int)
    def _completed(self, result, generation):
        if not self._on_gui_thread("_completed"):
            return
        if generation != self.generation:
            return  # a stale scan never overwrites a newer one
        when = datetime.datetime.now().strftime("%H:%M")
        if self._partial and self.report:
            if not result["complete"]:
                self.statusBar().showMessage("The re-check was stopped; the checkup is as it was.")
            else:
                self.report, changes = clinic.merge_recheck(self.report, result, self._partial)
                said = clinic.recheck_text(changes)
                for f in self.report["findings"]:
                    if clinic.probe_for(f["id"], [self._partial]):
                        self.rechecked[f["id"]] = f"Checked again at {when}."
                        if self.follow:
                            self.follow["changes"].pop(f["id"], None)  # that comparison is out of date now
                self.statusBar().showMessage(said)
            self.render_all()
            return
        # A checkup that was stopped early has unfinished tests; comparing it would call them changes.
        self.follow = clinic.follow_up(result, record.last(self.history)) if result["complete"] else None
        self.report = result
        self.report_skipped = self._starting_skipped
        if record.save(self.record_path, result):
            self.history = record.load(self.record_path)
        self.statusBar().showMessage(
            f"Checkup finished at {when}, took {vm.duration_text(result['duration_ms'])}"
            + ("" if result["complete"] else " · stopped early: cancelled or out of time"))
        self.render_all()

    @Slot(str, int)
    def _failed(self, message, generation):
        if not self._on_gui_thread("_failed"):
            return
        if generation == self.generation:
            self.last_error = message[:500]
            self.statusBar().showMessage(f"The checkup couldn't finish: {message[:200]}")

    @Slot(int)
    def _scan_thread_done(self, generation):
        if not self._on_gui_thread("_scan_thread_done"):
            return
        worker = self.workers.pop(generation, None)
        if worker is None:
            return
        worker.wait()  # done is run()'s last act; this joins the thread before it is deleted
        if worker is not self.worker:
            worker.deleteLater()  # a superseded scan: never touch the UI state of the current one
            return
        self.identity = (worker.ctx.user, worker.ctx.hostname)
        self.busy = False
        self.worker = None
        worker.deleteLater()
        self.scan_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setVisible(False)
        self.render_all()
        if self.close_requested:
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event):
        if self.scanning():
            # Never destroy a running QThread: cancel, then close once it has finished.
            self.close_requested = True
            self.cancel_scan()
            self.statusBar().showMessage("Closing after the running tests stop…")
            event.ignore()
            return
        event.accept()

    def shutdown(self, timeout_ms=15000):
        """Called on application quit/logout: cancel and join every scan thread."""
        for worker in list(self.workers.values()):
            worker.cancel()
        for worker in list(self.workers.values()):
            worker.wait(timeout_ms)

    # rendering ---------------------------------------------------------------
    def _set_verdict(self, text: str, severity: str | None, accessible: str):
        t = self.theme
        if severity:
            s = t.status[severity]
            colours = f"background: {s.bg}; color: {s.fg}; border: 1px solid {s.border};"
        else:
            colours = f"background: {t.surface_alt}; color: {t.muted}; border: 1px solid {t.border};"
        self.verdict.setText(text)
        self.verdict.setStyleSheet(f"QLabel#verdict {{ {colours} }}")
        self.verdict.setAccessibleDescription(accessible)

    def render_all(self):
        host, counts = vm.header_text(self.report)
        self.subtitle.setText(host or SUBTITLE)
        if self.report:
            state = self.report["overall"]
            complete = self.report["complete"]
            self._set_verdict(themes.HEADLINE[state] + ("" if complete else " (stopped early)"), state,
                              f"{themes.HEADLINE[state]}: {counts}")
            stamp = datetime.datetime.fromisoformat(self.report["generated_at"]).astimezone()
            self.setWindowTitle(f"{themes.HEADLINE[state]} - Doctor NoX")
            self.snapshot.setText(f"Last checkup {stamp:%Y-%m-%d %H:%M}, "
                                  f"took {vm.duration_text(self.report['duration_ms'])}")
            totals = self.report["totals"]
            for sev, button in self.sev_buttons.items():
                button.setText(f"{STATUS_TEXT[sev]}  {totals[sev]}")
        else:
            self.setWindowTitle("Doctor NoX")
            self._set_verdict("Examining…" if self.scanning() else "No checkup yet", None, "No findings yet")
            self.snapshot.setText("")
            for sev, button in self.sev_buttons.items():
                button.setText(STATUS_TEXT[sev])
        self.snapshot.setVisible(self.report is not None)
        self.scan_btn.setText("New checkup" if self.report else "Start checkup")
        self.copy_btn.setEnabled(self.report is not None)
        self.export_btn.setEnabled(self.report is not None)
        self.symptom_btn.setText(clinic.SYMPTOM[self.symptom].label)
        t = self.theme
        resting = t.status[self.report["overall"]].fg if self.report else t.subtle
        self.pulse.set_state(t.accent if self.scanning() else resting, self.scanning(),
                             self.settings["monitor_motion"])
        for key, action in self.symptom_actions.items():
            action.setChecked(key == self.symptom)
        for vital in clinic.vitals(self.report):
            self.vital_tiles[vital["key"]].show_vital(vital, self.theme)
        self.render_nav()
        self.render_list()

    def render_nav(self):
        self.nav.blockSignals(True)
        self.nav.clear()
        for key, title, total, counts, _worst in vm.category_rows(self.report):
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, key)
            badge, severity = vm.category_badge(counts, total) if self.report else ("", None)
            item.setData(NAV_BADGE_ROLE, badge)
            item.setData(NAV_SEV_ROLE, severity or "")
            item.setData(Qt.ItemDataRole.AccessibleTextRole,
                         vm.category_accessible(title, counts, total) if self.report else title)
            if self.report and total:
                item.setToolTip(vm.category_accessible(title, counts, total))
            self.nav.addItem(item)
            if key == self.category:
                self.nav.setCurrentItem(item)
        self.nav.blockSignals(False)

    def render_list(self):
        query = self.search.text()
        sevs = frozenset(self.severities)
        items = vm.visible(self.report, self.category, query, sevs, self.symptom)
        changes = self.follow["changes"] if self.follow else {}
        self.tree.blockSignals(True)
        self.tree.clear()
        keep = None
        for f in items:
            sev = f["severity"]
            change = clinic.CHANGE_TEXT[changes[f["id"]][0]] if f["id"] in changes else ""
            row = QTreeWidgetItem([STATUS_TEXT[sev], CATEGORY_TITLE[f["category"]], f["title"], f["summary"],
                                   change])
            row.setData(0, Qt.ItemDataRole.UserRole, f["id"])
            row.setData(0, SEV_ROLE, sev)
            row.setToolTip(2, f["title"])
            row.setToolTip(3, f["summary"])
            row.setData(0, Qt.ItemDataRole.AccessibleTextRole, f"{STATUS_TEXT[sev]}: {f['title']}")
            self.tree.addTopLevelItem(row)
            if f["id"] == self.selected_id:
                keep = row
        self.tree.setColumnHidden(1, self.category != vm.OVERVIEW)
        # Shown only when something did change since the last checkup.
        self.tree.setColumnHidden(CHANGE_COLUMN, not any(clinic.CHANGE_TEXT[kind] for kind, _ in changes.values()))
        self.tree.blockSignals(False)
        message = vm.empty_state(self.report, self.scanning(), self.category, query, sevs, self.symptom)
        self.empty.setText(message)
        self.empty_mark.setVisible(self.report is None)
        self.stack.setCurrentWidget(self.empty_box if message else self.tree)
        if keep is not None:
            self.tree.setCurrentItem(keep)  # emits currentItemChanged → _show
        else:
            self.selected_id = ""
            if self.last_error:
                self.details.setHtml(vm.error_html(self.last_error, self.theme))
            else:
                self.details.setHtml(vm.overview_html(
                    self.report, self.scanning(), self.theme, self.follow, self.symptom,
                    skipped_areas=self.report_skipped, history=self.history,
                    help_place=self._help_place() if self.report else None))

    def _toggle_severity(self, sev, on):
        (self.severities.add if on else self.severities.discard)(sev)
        self.render_list()

    def describe(self, text: str | None = None):
        """What's wrong, in the person's own words: find the complaint they mean."""
        typed = (self.complaint.text() if text is None else text).strip()
        key = clinic.guess(typed)
        if key:
            self.set_symptom(key, typed=True)
            self.statusBar().showMessage(f"I took that as “{clinic.SYMPTOM[key].label}”. "
                                         "Pick another from the list if I got it wrong.")
        else:
            self.set_symptom(clinic.GENERAL, typed=True)
            if typed:
                self.statusBar().showMessage("I have no tests for that, so here is the whole checkup. "
                                             + clinic.ASK_FOR_HELP)

    def set_symptom(self, key: str, typed: bool = False):
        """What's wrong? Shows the tests that bear on it, and the verdict speaks to it."""
        self.symptom = key if key in clinic.SYMPTOM else clinic.GENERAL
        if not typed:
            self.complaint.clear()
        if self.symptom == clinic.GENERAL:
            self.statusBar().showMessage("Showing the whole checkup.")
        else:
            self.statusBar().showMessage(f"Showing the tests for “{clinic.SYMPTOM[self.symptom].label}”. "
                                         "Choose Just a checkup to see them all.")
        self.selected_id = ""  # back to the diagnosis, which now speaks to the complaint
        self.render_all()
        if self.report is None and self.interactive and not self.scanning():
            self.start_scan()

    def open_finding(self, finding_id: str):
        """Show one test whatever is filtered out (a vital sign was clicked)."""
        if not self.report or not any(f["id"] == finding_id for f in self.report["findings"]):
            return
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        for button in self.sev_buttons.values():
            button.blockSignals(True)
            button.setChecked(True)
            button.blockSignals(False)
        self.severities = set(SEVERITIES)
        self.category = vm.OVERVIEW
        self.symptom = clinic.GENERAL
        self.complaint.clear()
        self.selected_id = finding_id
        self.render_all()
        if self.tree.currentItem() is not None:
            self.tree.scrollToItem(self.tree.currentItem())

    def _category_changed(self, current, _previous):
        if current is not None:
            self.category = current.data(Qt.ItemDataRole.UserRole)
            self.render_list()

    def _finding_changed(self, current, _previous):
        if current is not None:
            self._show(current)

    def _show(self, item):
        fid = item.data(0, Qt.ItemDataRole.UserRole)
        finding = next((f for f in (self.report or {}).get("findings", []) if f["id"] == fid), None)
        if finding:
            self.selected_id = fid
            change = self.follow["changes"].get(fid) if self.follow else None
            tool = self._tool(fid)
            actions = {"recheck": bool(clinic.probe_for(fid, self.probe_ids())),
                       "referral": tool["label"] if tool else None, "rechecked": self.rechecked.get(fid)}
            self.details.setHtml(vm.detail_html(finding, self.theme, self.mono, change, actions))

    # the diagnosis pane's links ------------------------------------------------
    def _detail_link(self, url):
        link = url.toString()
        if not link.startswith(vm.LINK):
            return
        action = link[len(vm.LINK):]
        finding = next((f for f in (self.report or {}).get("findings", []) if f["id"] == self.selected_id), None)
        if action == "help":
            self.get_help()
        elif action == "recheck" and finding:
            self.recheck(finding["id"])
        elif action == "open" and finding:
            self.open_referral(finding["id"])
        elif action.startswith("copy:") and finding:
            known = clinic.commands(finding)
            index = int(action[5:]) if action[5:].isdigit() else -1
            if 0 <= index < len(known):
                self._copy(known[index])
                self.statusBar().showMessage(f"Copied: {known[index]}")

    def _tool(self, finding_id: str):
        """The desktop's own tool for a finding, when one is installed."""
        injected = {"which": self._which} if self._which else {}
        return referral.for_finding(finding_id, self._environ, **injected)

    def _copy(self, text: str):
        self.last_copied = text
        if self.interactive:
            QApplication.clipboard().setText(text)

    def open_referral(self, finding_id: str) -> bool:
        """Open the desktop's own tool for a finding. Doctor NoX only opens it; any change there is yours."""
        tool = self._tool(finding_id)
        if not tool:
            return False
        self.last_opened = tool["argv"]
        if self.interactive and not QProcess.startDetached(tool["argv"][0], list(tool["argv"][1:])):
            self.statusBar().showMessage(f"Couldn't open {tool['argv'][0]}.")
            return False
        self.statusBar().showMessage("Opened. Whatever you change there is yours to change; "
                                     "then use Check this again.")
        return True

    def _os_release(self) -> dict:
        from . import flatpak
        try:
            if flatpak.sandbox_app_path():  # Doctor's own Flatpak: the computer's os-release, not the runtime's
                return referral.parse_os_release(Path("/run/host/os-release").read_text(errors="replace")[:65536])
            return self._context_factory().os_release()
        except OSError:
            return {}

    def _help_url(self) -> str:
        if not hasattr(self, "_help_address"):
            self._help_address = referral.help_url(self._os_release())
        return self._help_address

    def _help_place(self) -> str:
        """Where Get help leads, as a host name; "" when only the note can be copied."""
        return QUrl(self._help_url()).host() if self._help_url() else ""

    def get_help(self):
        """Copy the doctor's note and open where this distribution says to ask for help."""
        if not self.report:
            return
        self._copy(self.note_text())
        url = self._help_url()
        if url:
            self.last_opened = (url,)
            if self.interactive:
                QDesktopServices.openUrl(QUrl(url))
            self.statusBar().showMessage(f"Doctor's note copied. Paste it at {QUrl(url).host()}, which is opening; "
                                         "read it before you post it.")
        else:
            self.statusBar().showMessage("Doctor's note copied. Paste it where you ask for help; "
                                         "read it before you post it.")

    # output ------------------------------------------------------------------
    def _identity(self):
        user, host = self.identity
        return user or os.environ.get("USER", ""), host

    def note_text(self) -> str:
        """The doctor's note for the checkup on screen, redacted for sharing."""
        if not self.report:
            return ""
        user, host = self._identity()
        return clinic.note(reports.redact_report(self.report, user, host), self.follow)

    def copy_note(self):
        if not self.report:
            return
        self._copy(self.note_text())
        self.statusBar().showMessage("Doctor's note copied. It is redacted for sharing; read it before you post it.")

    def export(self, redacted, fmt):
        snapshot = self.report  # the dialog runs a nested event loop; a finishing scan must not swap reports
        if not snapshot:
            return
        user, host = self._identity()
        if fmt == "clipboard":
            QApplication.clipboard().setText(reports.format_text(snapshot))
            self.statusBar().showMessage("Full local report copied. It contains identifying details.")
            return
        kind = "redacted" if redacted else "full"
        ext = "json" if fmt == "json" else "txt"
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
        start = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
        caption = ("Save redacted report for sharing" if redacted
                   else "Save full local report (contains identifying details)")
        path, _ = QFileDialog.getSaveFileName(self, caption, str(Path(start) / f"doctor-nox-{kind}-{stamp}.{ext}"),
                                              "JSON (*.json)" if ext == "json" else "Text (*.txt)")
        if not path:
            return
        self.write_export(path, redacted, fmt, user, host, snapshot)

    def write_export(self, path, redacted, fmt, user="", host="", snapshot=None):
        try:
            reports.export(path, snapshot or self.report, redacted=redacted, fmt=fmt, user=user, hostname=host)
        except OSError as exc:
            self.last_export_error = f"{path}: {exc.strerror or exc}"
            if self.interactive:
                QMessageBox.warning(self, "Export failed", f"Could not write {path}:\n{exc.strerror or exc}")
            else:
                print(f"Doctor NoX: export failed: {self.last_export_error}", file=sys.stderr, flush=True)
            self.statusBar().showMessage(f"Export failed: {self.last_export_error}")
            return False
        self.statusBar().showMessage(f"Saved {'redacted' if redacted else 'full'} report to {path}")
        return True

    def show_about(self):
        try:
            import PySide6
            from PySide6.QtCore import qVersion
            qt = f"PySide6 {PySide6.__version__} with Qt {qVersion()}"
        except Exception:
            qt = "PySide6"
        t = self.theme
        from . import flatpak
        sandboxed = ("<p>This is the Flatpak: its checks run on this computer through the "
                     "org.freedesktop.Flatpak permission, not inside the sandbox.</p>"
                     if flatpak.sandbox_app_path() else "")
        text = (f"<h3 style='margin-bottom:4px'>Doctor NoX {VERSION}</h3>"
                f"<p style='margin-top:0;margin-bottom:4px'><i>{clinic.MOTTO}</i></p>"
                f"<p style='color:{t.muted};margin-top:0'>NoX is Latin for night, and the name is a play on "
                f"&ldquo;Doctor Noooo&rdquo;.</p>"
                f"<p>A doctor for your Linux PC. It examines, diagnoses and prescribes, and never operates: "
                f"every treatment is yours to carry out. For Fedora (including Atomic and bootc), Debian and "
                f"Ubuntu, Arch and openSUSE, with general tests everywhere else.</p>"
                f"<p>No root, no repairs, no network requests and no telemetry. Reports are written only where "
                f"you choose. It keeps two small files of its own: your settings and, unless you turn that off, "
                f"a chart of your last 30 checkups.</p>{sandboxed}"
                f"<p style='color:{t.muted}'>Report schema v{SCHEMA_VERSION}, {qt}, "
                f"Python {sys.version.split()[0]}<br>License: Apache-2.0</p>")
        QMessageBox.about(self, "About Doctor NoX", text)


# ── entry points ─────────────────────────────────────────────────────────────
def _application():
    QGuiApplication.setDesktopFileName(APP_ID)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Doctor NoX")
    app.setApplicationVersion(VERSION)
    app.setWindowIcon(app_icon())
    app.setFont(choose_font())
    return app


def run() -> int:
    import signal
    app = _application()
    window = Window(record_path=record.default_path(), settings_path=settings_store.default_path())
    app.aboutToQuit.connect(window.shutdown)
    # Ctrl+C in the launching terminal: close like the user would (deferred while scanning).
    signal.signal(signal.SIGINT, lambda *_: window.close())
    tick = QTimer(window, interval=250)
    tick.timeout.connect(lambda: None)  # lets Python run its signal handlers inside app.exec()
    tick.start()
    window.show()
    return app.exec()


def _teardown(app, window):
    """Join every scan thread, then delete the window before the interpreter shuts down."""
    window.shutdown()
    window.hide()
    window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    app.processEvents()


def smoke_test() -> int:
    """Build every widget headlessly. Runs NO host probes."""
    app = _application()
    window = Window(autoscan=False, interactive=False)
    window.show()
    app.processEvents()
    window.close()
    app.processEvents()
    _teardown(app, window)
    print("Doctor NoX GUI smoke test: OK", flush=True)
    return 0


def _demo_report():
    """Synthetic, realistic-looking results for screenshots. Nothing here is read from the host."""
    from .model import Finding, build_report
    F = Finding
    findings = [
        F("system.identity", "system", "Operating system", "info", "Debian GNU/Linux 13 (trixie) · kernel 6.12.48+deb13-amd64",
          evidence=["ID=debian", "family: Debian / Ubuntu family", "package tools: apt, dpkg, flatpak"]),
        F("updates.reboot", "updates", "Pending restart", "info", "Restart needed: apt/dpkg asked for a restart today",
          evidence=["/run/reboot-required: present (since today)", "packages asking for it: linux-image-amd64"],
          guidance=["Restart when convenient so the update takes full effect. Doctor never restarts."]),
        F("updates.packages", "updates", "Package database", "pass", "No broken or half-installed packages"),
        F("updates.config_files", "updates", "Config files to review", "info",
          "2 config file(s) left by package updates to review",
          evidence=["/etc/ssh/sshd_config.dpkg-dist", "/etc/default/grub.ucf-dist"]),
        F("updates.last_update", "updates", "Last update", "pass", "Packages last changed 2 days ago"),
        F("security.mac", "security", "SELinux / AppArmor", "pass", "AppArmor is enabled"),
        F("security.firewall", "security", "Firewall", "info", "No firewalld or ufw firewall is on"),
        F("security.failed_units", "security", "Failed services", "warn", "1 failed unit(s): example-sync.service",
          evidence=["system: example-sync.service  — Example sync daemon"],
          guidance=["Inspect: `systemctl status example-sync.service` and "
                    "`journalctl -b -u example-sync.service`."]),
        F("hardware.cpu_memory", "hardware", "CPU & memory", "pass", "16 threads · 31.3 GiB RAM (22.4 GiB available)",
          evidence=["CPU: AMD Ryzen 7 5800X 8-Core Processor · 16 threads",
                    "memory: 31.3 GiB total, 22.4 GiB available now"]),
        F("hardware.graphics", "hardware", "Graphics", "pass", "AMD: amdgpu driver"),
        F("hardware.sensors", "hardware", "Temperatures", "pass",
          "Hottest sensor 47 °C; none at a reported critical limit",
          evidence=["k10temp/temp1: 47 °C (crit 95 °C)", "amdgpu/temp1: 41 °C (crit 100 °C)"]),
        F("hardware.controllers", "hardware", "Game controllers", "warn",
          "Xbox-protocol controller connected but not bound to a driver"),
        F("storage.space", "storage", "Free space", "error", "Low free space: /boot",
          evidence=["/boot (kernels; ext4): 61 MiB free of 943 MiB (6%) — ERROR",
                    "/ + /var + /home (system, your home; ext4): 182.4 GiB free of 465.2 GiB (39%) — PASS"],
          guidance=["A full /boot stops kernel updates. Removing old kernels with your package manager frees it."]),
        F("software.flatpak", "software", "Flatpak", "pass", "12 app(s) from 1 source(s) including Flathub"),
        F("network.link", "network", "Network links", "pass", "Connected on enp5s0"),
        F("logs.boot", "logs", "Current boot errors", "unknown", "Journal not readable",
          reason="Members of the systemd-journal, adm or wheel groups can read the system journal."),
    ]
    finished = datetime.datetime.now(datetime.timezone.utc)
    host = {"name": "Debian GNU/Linux 13 (trixie)", "id": "debian", "version": "13", "family": "debian",
            "kernel": "6.12.48+deb13-amd64", "init": "systemd", "security": "apparmor",
            "package_managers": ["apt", "dpkg", "flatpak"]}
    skipped = [{"id": "system.image", "category": "system", "title": "Image deployments",
                "applies_to": "image-based OSTree systems (bootc, Fedora Atomic, Universal Blue…)"},
               {"id": "software.snap", "category": "software", "title": "Snap", "applies_to": "systems with Snap"}]
    return build_report(findings, host, started=finished - datetime.timedelta(milliseconds=1840),
                        finished=finished, complete=True, not_applicable=skipped)


def _demo_history(report):
    """A made-up chart for the screenshots: eight earlier checkups over a month, with free space
    falling, then the one on screen. Last time free space was better and the controller worse."""
    now = datetime.datetime.fromisoformat(report["generated_at"])
    conditions = {f["id"]: f["severity"] for f in report["findings"]}
    conditions.update({"storage.space": "warn", "hardware.controllers": "error"})
    history = []
    for n, (days, overall, free) in enumerate(((30, "pass", 221.0), (26, "pass", 214.5), (22, "warn", 209.9),
                                               (18, "pass", 204.0), (14, "pass", 198.2), (10, "warn", 193.1),
                                               (7, "warn", 188.0), (4, "error", 185.6))):
        history.append({"generated_at": (now - datetime.timedelta(days=days)).isoformat(timespec="seconds"),
                        "overall": overall, "findings": dict(conditions) if n == 7 else {},
                        "metrics": {"disk": {"/": [free, 465.2]}, "temperature": 45 + n % 3}})
    return history + [record.snapshot(report)]


def screenshots(out_dir: str) -> int:
    """Save PNGs of the real window in both themes, with synthetic results. Runs NO host probes.

    Developer/QA aid (used by the VM workflow); writes only into out_dir.
    """
    fh = _install_diagnostics(print, hang_after=120)
    app = _application()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    window = Window(autoscan=False, interactive=False)
    demo = _demo_report()
    saved, failed = [], []

    def settle():
        for _ in range(6):
            app.processEvents()

    for mode in (themes.DARK, themes.LIGHT):
        window.set_theme_mode(mode)
        for name, size, report, select in (("welcome", QSize(1280, 800), None, False),
                                           ("overview", QSize(1280, 800), demo, False),
                                           ("details", QSize(1280, 800), demo, True),
                                           ("narrow", QSize(980, 760), demo, True)):
            window.report = report
            window._help_address = "https://www.debian.org/support"  # the demo is a Debian; nothing is read
            window.history = _demo_history(report) if report else []
            window.follow = clinic.follow_up(report, window.history[-2]) if report else None
            window.category = vm.OVERVIEW
            window.selected_id = ""
            window.resize(size)
            window.show()
            window.render_all()
            if select and window.tree.topLevelItemCount():
                window.tree.setCurrentItem(window.tree.topLevelItem(0))
            settle()
            path = out / f"doctor-nox-{mode}-{name}.png"
            (saved if window.grab().save(str(path)) else failed).append(path)
    errors = list(window.paint_errors) + [m for m in QT_MESSAGES if "stylesheet" in m.lower()]
    window.hide()
    _teardown(app, window)
    fh.cancel_dump_traceback_later()
    for path in saved:
        print(f"saved {path}", flush=True)
    for path in failed:
        print(f"FAILED to save {path}", flush=True)
    for error in errors:
        print(f"paint error:\n{error}", file=sys.stderr, flush=True)
    return 1 if failed or errors else 0


def _install_diagnostics(log, hang_after: float):
    """Make build failures identifiable: Qt warnings, native crashes and hangs all reach stderr."""
    import faulthandler
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    faulthandler.enable(file=sys.stderr, all_threads=True)          # SIGSEGV/SIGABRT/SIGBUS → stacks
    faulthandler.dump_traceback_later(hang_after, repeat=False, file=sys.stderr, exit=False)
    names = {QtMsgType.QtDebugMsg: "debug", QtMsgType.QtInfoMsg: "info", QtMsgType.QtWarningMsg: "warning",
             QtMsgType.QtCriticalMsg: "critical", QtMsgType.QtFatalMsg: "FATAL"}

    def qt_messages(kind, _context, message):
        QT_MESSAGES.append(f"{names.get(kind, kind)}: {message}")
        print(f"[doctor self-test] Qt {names.get(kind, kind)}: {message}", file=sys.stderr, flush=True)
    qInstallMessageHandler(qt_messages)
    return faulthandler


def self_test() -> int:
    """Drive the real window with synthetic probes only. Never runs host probes or commands.

    Covers: scan → results, filtering (text + severity), selection/details, redacted and full
    export, rapid refresh ignored, cancellation, close-while-scanning, and that every UI update
    ran on the GUI thread and every scan thread was joined. Each step prints a flushed checkpoint
    so a crash or hang in the image build shows exactly where it happened.
    """
    import threading
    import time
    import traceback
    from .model import Finding
    from .probes import Probe

    def log(message):
        print(f"[doctor self-test] {message}", flush=True)

    log(f"start · python {sys.version.split()[0]} · pid {os.getpid()} · "
        f"QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM', '(unset)')}")
    try:
        free = os.statvfs(tempfile.gettempdir())
        log(f"tmp {tempfile.gettempdir()}: {free.f_bavail * free.f_frsize / 2**20:.0f} MiB free")
    except OSError as exc:
        log(f"tmp free space unknown: {exc}")
    fh = None
    try:
        fh = _install_diagnostics(log, hang_after=60)
        app = _application()
        log("QApplication ready")
        result = _self_test_steps(app, log, Finding, Probe, threading, time)
    except Exception:
        log("UNEXPECTED EXCEPTION")
        traceback.print_exc()
        sys.stderr.flush()
        result = 2
    finally:
        if fh is not None:
            fh.cancel_dump_traceback_later()
    return result


def _self_test_steps(app, log, Finding, Probe, threading, time) -> int:
    gate = threading.Event()
    probe_threads: list[str] = []

    def fixture_probe(ctx):
        probe_threads.append(threading.current_thread().name)
        return [Finding("system.identity", "system", "Operating system", "pass", "Fixture Linux",
                        evidence=["fixture"]),
                Finding("updates.packages", "updates", "Package database", "error",
                        "The package database could not be read", guidance=["fixture guidance"]),
                Finding("hardware.controllers", "hardware", "Game controllers", "warn",
                        "Xbox-protocol controller connected", evidence=["user alice on 10.1.2.3"]),
                Finding("hardware.sensors", "hardware", "Temperatures", "pass",
                        "Hottest sensor 47 °C; none at a reported critical limit"),
                Finding("network.link", "network", "Network links", "info", "Offline")]

    def slow_probe(ctx):
        while not ctx.cancel.is_set() and not gate.is_set():
            time.sleep(0.02)
        return Finding("logs.boot", "logs", "Current boot errors", "unknown", "slow fixture")

    space_runs = []

    def space_probe(ctx):  # needs treatment the first time, healthy once it is checked again
        space_runs.append(1)
        if len(space_runs) == 1:
            return Finding("storage.space", "storage", "Free space", "error", "Low free space: /",
                           guidance=["Look at `journalctl --disk-usage`, and ask someone in `wheel`."])
        return Finding("storage.space", "storage", "Free space", "pass", "Enough free space")

    probes = [Probe("fixture.fast", "system", "fixture", fixture_probe),
              Probe("fixture.slow", "logs", "slow", slow_probe),
              Probe("storage.space", "storage", "Free space", space_probe)]

    def scan(ctx, progress=None, selected=None, deadline=30):
        return engine.scan(ctx, progress=progress, probes=probes, deadline=min(deadline, 30), selected=selected)

    def fixture_context():
        return Context(root=Path(tempfile.gettempdir()) / "doctor-nox-no-host", user="alice", hostname="ws")

    failures = []

    def check(cond, label):
        log(("ok    " if cond else "FAIL  ") + label)
        if not cond:
            failures.append(label)

    def pump(until, timeout=10.0):
        end = time.monotonic() + timeout
        while not until() and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        return until()

    # Follow-ups are tried against a remembered checkup in a temporary directory, never the user's own.
    import json
    records = tempfile.TemporaryDirectory(prefix="doctor-nox-selftest-record-")
    record_path = Path(records.name) / "state" / record.FILE_NAME
    record_path.parent.mkdir(parents=True)
    record_path.write_text(json.dumps({"version": record.VERSION, "checkups": [{
        "generated_at": "2026-01-01T00:00:00+00:00", "overall": "error",
        "findings": {"updates.packages": "pass", "hardware.controllers": "error", "network.link": "info"}}]}))

    settings_path = Path(records.name) / "config" / settings_store.FILE_NAME
    window = Window(scan_func=scan, context_factory=fixture_context, autoscan=False, interactive=False,
                    record_path=record_path, settings_path=settings_path,
                    probe_ids=[probe.id for probe in probes], which=lambda name: f"/usr/bin/{name}",
                    environ={"XDG_CURRENT_DESKTOP": "KDE"})
    window.show()
    app.processEvents()
    log("window shown")
    check(window.scan_btn.text() == "Start checkup" and window.verdict.text() == "No checkup yet",
          "before a checkup the window says so")
    check(all(tile.reading == clinic.NO_VALUE for tile in window.vital_tiles.values()),
          "vital signs are blank before a checkup")

    # 1. scanning
    gate.set()
    window.start_scan()
    log("scan 1 started")
    check(pump(lambda: window.report is not None and not window.scanning()), "scan 1 completes")
    check(window.workers == {} and window.worker is None, "scan 1 thread joined and released")
    check(window.tree.topLevelItemCount() == 7, "the diagnosis lists all 7 findings")
    check(window.tree.topLevelItemCount() > 0 and window.tree.topLevelItem(0).data(0, SEV_ROLE) == "error",
          "most severe finding sorts first")
    check(bool(probe_threads) and all(n.startswith("doctor-probe") for n in probe_threads),
          "probes ran on engine worker threads")

    # 1b. the doctor: wording, verdict, vital signs, what's wrong, follow-ups
    check(window.scan_btn.text() == "New checkup" and window.verdict.text() == "Needs treatment",
          "after a checkup the headline and the button are in the doctor's words")
    said = window.details.toPlainText()
    check("Your PC needs treatment." in said and "Package database" in said, "the diagnosis opens with the verdict")
    check(window.vital_tiles["temperature"].reading == "47 °C" and window.vital_tiles["memory"].reading == "Not examined",
          "vital signs show a reading, or that nothing measured them")
    check("Worse since your last checkup" in said and "Better" in said, "the diagnosis says what changed")
    check(not window.tree.isColumnHidden(CHANGE_COLUMN)
          and window.tree.topLevelItem(0).text(CHANGE_COLUMN) == "Worse", "the findings mark what got worse")
    saved = record.load(record_path)
    check(len(saved) == 2 and saved[-1]["generated_at"] == window.report["generated_at"]
          and oct(record_path.stat().st_mode & 0o777) == "0o600", "the finished checkup joins the chart, privately")
    check("Your chart" in said and "2 checkups" in said and "Get help" in said,
          "the diagnosis shows the chart and offers help")

    # 1b'. treat, then check again: copy a command, open the desktop's tool, re-run one test
    window.open_finding("storage.space")
    app.processEvents()
    shown = window.details.toPlainText()
    check("journalctl --disk-usage" in shown and "Changes nothing." in shown and "Copy" in shown,
          "a treatment's command is explained and can be copied")
    check("See what is using the space" in shown and "Check this again" in shown,
          "a finding offers the desktop's own tool and a re-check")
    window._detail_link(QUrl(vm.LINK + "copy:0"))
    window._detail_link(QUrl(vm.LINK + "open"))
    check(window.last_copied == "journalctl --disk-usage" and window.last_opened == ("/usr/bin/filelight",),
          "the links copy the command and name the tool (nothing is started in a self-test)")
    window._detail_link(QUrl(vm.LINK + "recheck"))
    check(pump(lambda: not window.scanning() and len(space_runs) == 2), "one test runs again on its own")
    again = {f["id"]: f["severity"] for f in window.report["findings"]}
    check(len(again) == 7 and again["storage.space"] == "pass" and window.report["totals"]["error"] == 1
          and "Healthy now (was Needs treatment)" in window.statusBar().currentMessage(),
          "the re-check replaces that test's result and says what changed")
    check("Checked again at" in window.details.toPlainText() and "None needed." in window.details.toPlainText(),
          "the details show the re-checked result")
    window._detail_link(QUrl(vm.LINK + "help"))
    check("doctor's note" in window.last_copied and "alice" not in window.last_copied,
          "get help copies the redacted note")
    window.vital_tiles["temperature"].clicked.emit("hardware.sensors")
    app.processEvents()
    check("Temperatures" in window.details.toPlainText() and "None needed." in window.details.toPlainText(),
          "a vital sign opens its test")
    window.set_symptom("network")
    app.processEvents()
    check(window.tree.topLevelItemCount() == 1 and "No internet" in window.details.toPlainText()
          and "No internet" in window.symptom_btn.text(), "what's wrong narrows the findings and the verdict")
    window.set_symptom(clinic.GENERAL)
    app.processEvents()
    check(window.tree.topLevelItemCount() == 7, "just a checkup shows every finding again")
    note = window.note_text()
    check("doctor's note" in note and "NEEDS TREATMENT" in note and "alice" not in note and "10.1.2.3" not in note,
          "the doctor's note is in the doctor's words and redacted")
    window.describe("my wifi keeps dropping")
    app.processEvents()
    check(window.symptom == "network" and window.tree.topLevelItemCount() == 1,
          "what's wrong can be typed in your own words")
    window.describe("my printer is haunted")
    app.processEvents()
    check(window.symptom == clinic.GENERAL and window.tree.topLevelItemCount() == 7,
          "a complaint with no tests shows the whole checkup")
    check(pump(lambda: not window.pulse.timer.isActive(), 5) and not window.pulse.running
          and window.pulse.colour == window.theme.status["error"].fg,
          "the heart monitor gives one last beat, then rests in the condition's colour")
    check(window.windowTitle() == "Needs treatment - Doctor NoX", "the window title carries the condition")

    # 1b''. its own frame: the header is the title bar
    frameless = bool(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
    check(window.own_frame and frameless and all(b.isVisible() and not b.icon().isNull()
                                                 for b in window.window_buttons.values())
          and window.statusBar().isSizeGripEnabled(), "Doctor NoX draws its own title bar, buttons and resize grip")
    window.toggle_maximized()
    app.processEvents()
    grown = window.isMaximized()
    window.toggle_maximized()
    app.processEvents()
    check(grown and not window.isMaximized(), "its maximize button maximizes and restores")

    # 1c. settings: they apply, are saved, and can be put back
    dialog = window.open_settings()
    check(dialog is not None and dialog.values() == window.settings == settings_store.clean(None),
          "settings open with the defaults")
    dialog.theme_buttons["light"].setChecked(True)
    dialog.area_boxes["logs"].setChecked(False)
    dialog.remember.setChecked(False)
    dialog.motion.setChecked(False)
    dialog.own_frame.setChecked(False)
    dialog.deadline.setValue(20)
    window.apply_settings(dialog.values())
    app.processEvents()
    kept = settings_store.load(settings_path)
    check(window.theme is themes.LIGHT_THEME and kept["theme"] == "light" and kept["skip_areas"] == ["logs"]
          and kept["deadline"] == 20 and kept["own_frame"] is False and window.own_frame
          and oct(settings_path.stat().st_mode & 0o777) == "0o600",
          "settings apply at once and are saved, privately")
    check(not record_path.exists() and window.follow is None and window.record_path is None,
          "turning off remembering deletes the remembered checkup")
    window.start_scan()
    check(pump(lambda: not window.scanning() and window.report is not None
               and len(window.report["findings"]) == 6), "an area turned off is left out of the checkup")
    check("Left out by your settings" in window.details.toPlainText() and not record_path.exists(),
          "the diagnosis says what was left out, and nothing is remembered")
    dialog.restore_defaults()
    window.apply_settings(dialog.values())
    dialog.deleteLater()
    app.processEvents()
    check(window.settings == settings_store.clean(None) and settings_store.load(settings_path) == window.settings
          and record_path.exists(), "restoring the defaults puts everything back, remembering included")
    log("settings done")

    remembered_at = record_path.stat().st_mtime_ns
    window.resize(1240, 780)
    app.processEvents()
    inside = window.rect()
    parts = [window.complaint, window.symptom_btn, window.scan_btn, window.copy_btn, window.export_btn,
             window.more_btn, window.pulse, window.search, *window.sev_buttons.values(),
             *window.vital_tiles.values(), *window.window_buttons.values()]
    outside = [w.accessibleName() or w.text() for w in parts if not w.isVisible() or not inside.contains(
        QRect(w.mapTo(window, w.rect().topLeft()), w.size()))]
    needs = window.minimumSizeHint().width()
    check(window.width() == 1240 and not outside,
          f"every control fits in the default 1240 px window (window {window.width()} px, outside: {outside})")
    check(needs < NARROW, f"the window can be narrow enough for the stacked layout (needs {needs} px)")
    log("doctor behaviour done")

    # 2. filtering and selection
    window.search.setText("controller")
    app.processEvents()
    check(window.tree.topLevelItemCount() == 1, "text filter narrows to 1 result")
    window.search.clear()
    window.sev_buttons["pass"].setChecked(False)
    app.processEvents()
    check(all(window.tree.topLevelItem(i).data(0, SEV_ROLE) != "pass"
              for i in range(window.tree.topLevelItemCount())), "severity filter hides PASS")
    window.sev_buttons["pass"].setChecked(True)
    window.tree.setCurrentItem(window.tree.topLevelItem(0))
    app.processEvents()
    check("Package database" in window.details.toPlainText(), "selection shows details")
    window.nav.setCurrentRow(3)  # Security & Services: empty in the fixture
    app.processEvents()
    check(window.stack.currentWidget() is window.empty_box, "empty group shows the empty state")
    window.nav.setCurrentRow(0)
    log("filtering done")

    # 2b. themes: both palettes apply and every custom-painted row renders without errors
    window.tree.setCurrentItem(window.tree.topLevelItem(0))
    for mode, expected in (("light", themes.LIGHT_THEME), ("dark", themes.DARK_THEME)):
        window.set_theme_mode(mode)
        app.processEvents()
        check(window.theme is expected and expected.window in window.styleSheet()
              and app.palette().color(QPalette.ColorRole.Window).name() == expected.window,
              f"{mode} theme applied to stylesheet and palette")
        shot = window.grab()  # paints the header, chips, sidebar pills, status pills and details
        check(not shot.isNull() and shot.width() > 0, f"{mode} theme renders")
        check("Package database" in window.details.toPlainText(), f"{mode} theme keeps the selection")
    window.set_theme_mode("system")
    app.processEvents()
    check(window.theme in (themes.LIGHT_THEME, themes.DARK_THEME) and not window.grab().isNull(),
          "system theme resolves and renders")
    check(window.paint_errors == [], f"custom painting raised no errors ({len(window.paint_errors)})")
    log("themes done")

    # 3. report export (no dialogs in self-test mode)
    try:
        with tempfile.TemporaryDirectory(prefix="doctor-nox-selftest-") as tmp:
            target = Path(tmp) / "shared.txt"
            check(window.write_export(target, True, "text", "alice", "ws"),
                  f"redacted export written {window.last_export_error}".strip())
            text = target.read_text() if target.exists() else ""
            check("alice" not in text and "10.1.2.3" not in text and "REDACTED" in text, "export is redacted")
            full = Path(tmp) / "full.json"
            check(window.write_export(full, False, "json"), f"full export written {window.last_export_error}".strip())
            check(full.exists() and oct(full.stat().st_mode & 0o777) == "0o600", "full export is private (0600)")
    except OSError as exc:
        check(False, f"temporary export directory usable ({exc})")
    log("export done")

    # 4. rescan, rapid refresh, cancellation, close while scanning, thread shutdown
    gate.clear()
    window.start_scan()
    check(pump(window.scanning, 5), "scan 2 running")
    check(window.pulse.running and window.pulse.timer.isActive() and window.pulse.colour == window.theme.accent,
          "the heart monitor sweeps during a checkup")
    check(not window.grab().isNull() and window.paint_errors == [], "the sweeping monitor paints without errors")
    first_worker = window.worker
    window.start_scan()
    check(window.worker is first_worker and len(window.workers) == 1, "refresh during a scan is ignored")
    window.close()
    check(window.isVisible() and window.close_requested, "close is deferred while scanning")
    log("waiting for cancellation")
    check(pump(lambda: not window.scanning(), 15), "cancel stops scan 2")
    check(pump(lambda: not window.isVisible(), 5), "window closes after the scan stops")
    check(window.workers == {} and window.worker is None, "every scan thread joined")
    check(pump(lambda: not [t for t in threading.enumerate() if t.name.startswith("doctor-probe")], 5),
          "engine probe threads exited")
    check(window.report is not None and window.report["complete"] is False, "cancelled scan reported INCOMPLETE")
    check(window.follow is None and record_path.stat().st_mtime_ns == remembered_at,
          "a checkup that was stopped early is neither compared nor remembered")
    check(window.thread_violations == [], f"UI updates ran only on the GUI thread {window.thread_violations}")
    bad_css = [m for m in QT_MESSAGES if "stylesheet" in m.lower()]
    check(bad_css == [], f"every stylesheet parsed without Qt warnings {bad_css[:3]}")
    _teardown(app, window)
    records.cleanup()
    log("teardown done")

    if failures:
        print("Doctor NoX GUI self-test FAILED: " + "; ".join(failures), flush=True)
        return 1
    print("Doctor NoX GUI self-test: OK (synthetic probes; no host commands)", flush=True)
    return 0
