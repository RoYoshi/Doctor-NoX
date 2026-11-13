"""Doctor NoX desktop window (PySide6 Qt Widgets).

Presentation only: scanning lives in engine.py, filtering/ordering/detail text
in viewmodel.py, colours in theme.py. Scans run on a QThread; the window never
blocks on a subprocess, and it never closes while a scan thread is still alive.
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path
import sys
import tempfile
import traceback

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, QStandardPaths, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import (QAction, QActionGroup, QBrush, QColor, QFont, QFontDatabase, QFontMetrics,
                           QGuiApplication, QIcon, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPalette,
                           QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QProgressBar, QPushButton, QSplitter, QStackedWidget, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem, QTextBrowser, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from . import APP_ID, VERSION
from . import engine, report as reports, theme as themes, viewmodel as vm
from .context import Context
from .model import CATEGORY_TITLE, SCHEMA_VERSION, SEVERITIES
from .theme import STATUS_TEXT, Theme

# Doctor's own identity (the NoX icon), independent of any distribution logo.
ICON_NAMES = (APP_ID,)
ICON_PATHS = (f"/usr/share/icons/hicolor/512x512/apps/{APP_ID}.png",)
NARROW = 1180  # below this width the detail pane moves under the list
SEV_ROLE = Qt.ItemDataRole.UserRole + 1         # results table: severity key of the row
NAV_BADGE_ROLE = Qt.ItemDataRole.UserRole + 2   # sidebar: pill text ("3", "OK", "")
NAV_SEV_ROLE = Qt.ItemDataRole.UserRole + 3     # sidebar: pill severity ("" for none)
SAFETY_NOTE = "Read-only: needs no root, makes no network requests, uploads nothing"
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
    tile=True it is the placeholder app icon exactly as tools/build_doctor_icon.py draws it:
    on the indigo night tile, with the bolder mark at small sizes.
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
        painter.translate(edge + mark.inset * side, edge + mark.inset * side)
        painter.scale(mark.scale * side / 100, mark.scale * side / 100)
    (mx, my, mr), (cx, cy, cr), (x, y, length, thick) = mark.moon, mark.cut, mark.cross
    moon = QPainterPath()
    moon.addEllipse(QPointF(mx, my), mr, mr)
    cut = QPainterPath()
    cut.addEllipse(QPointF(cx, cy), cr, cr)
    painter.setBrush(QColor(t.accent))
    painter.drawPath(moon.subtracted(cut))
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


def stylesheet(t: Theme) -> str:
    chips = "".join(
        f"""QToolButton[sev="{sev}"]:checked {{ background: {s.bg}; color: {s.fg}; border-color: {s.border}; }}
    """ for sev, s in t.status.items())
    return f"""
    QMainWindow, QWidget#host {{ background: {t.window}; }}
    QWidget {{ color: {t.text}; }}
    QLabel#subtitle, QLabel#snapshot, QLabel#safety, QLabel#section {{ color: {t.muted}; }}
    QLabel#verdict {{ border-radius: 13px; padding: 4px 14px; font-weight: 600; }}
    QFrame#empty {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 8px; }}
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


def dot_icon(on_colour: str, off_colour: str) -> QIcon:
    """Status dot for the filter chips: coloured when shown, grey when filtered out."""
    icon = QIcon()
    for colour, state in ((on_colour, QIcon.State.On), (off_colour, QIcon.State.Off)):
        pix = QPixmap(12, 12)
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

    def __init__(self, generation: int, scan_func, context_factory, parent=None):
        super().__init__(parent)
        self.generation = generation
        self._scan = scan_func
        self.ctx = context_factory()

    def cancel(self):
        self.ctx.cancel.set()  # the runner kills in-flight commands; the engine wraps up

    def run(self):
        try:
            # This callback runs on the scan thread; it only emits (thread-safe), never touches widgets.
            result = self._scan(self.ctx, progress=self._emit_progress)
            self.completed.emit(result, self.generation)
        except Exception as exc:  # surfaced in the UI, never a crash
            self.failed.emit(f"{type(exc).__name__}: {exc}", self.generation)
        finally:
            self.done.emit(self.generation)

    def _emit_progress(self, done, total, title):
        self.progressed.emit(done, total, title)


# ── window ───────────────────────────────────────────────────────────────────
class Window(QMainWindow):
    def __init__(self, *, scan_func=engine.scan, context_factory=Context, autoscan=True, interactive=True):
        super().__init__()
        self.interactive = interactive        # False in self-tests: never open modal dialogs
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
        self.theme_mode = themes.requested_mode()
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
        if autoscan:
            QTimer.singleShot(0, self.start_scan)

    # layout ------------------------------------------------------------------
    def _build(self):
        host = QWidget(objectName="host")
        self.setCentralWidget(host)
        root = QVBoxLayout(host)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(12)

        # Header: identity on the left, overall state on the right.
        head = QHBoxLayout()
        head.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(40, 40))
        logo.setAccessibleName("Doctor NoX icon")
        head.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        self.title = QLabel("Doctor NoX", objectName="title")
        self.title.setFont(scaled_font(self.font(), 1.45, QFont.Weight.DemiBold))
        self.subtitle = QLabel("Read-only system health diagnostics", objectName="subtitle")
        self.subtitle.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles)
        head.addStretch(1)
        right = QVBoxLayout()
        right.setSpacing(4)
        self.verdict = QLabel("—", objectName="verdict", alignment=Qt.AlignmentFlag.AlignCenter)
        self.verdict.setAccessibleName("Overall system state")
        self.snapshot = QLabel("No scan yet", objectName="snapshot", alignment=Qt.AlignmentFlag.AlignRight)
        right.addWidget(self.verdict, 0, Qt.AlignmentFlag.AlignRight)
        right.addWidget(self.snapshot, 0, Qt.AlignmentFlag.AlignRight)
        head.addLayout(right)
        root.addLayout(head)

        # Toolbar: search and status filters on the left, actions on the right.
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit(placeholderText="Filter checks…  (Ctrl+F, try is:problem)")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(220)
        self.search.setAccessibleName("Filter checks")
        self.search.textChanged.connect(self.render_list)
        bar.addWidget(self.search, 2)
        chips = QHBoxLayout()
        chips.setSpacing(6)
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
            b.setToolTip(f"Show or hide {STATUS_TEXT[sev]} results")
            b.setAccessibleName(f"Show {STATUS_TEXT[sev]} results")
            b.toggled.connect(lambda on, s=sev: self._toggle_severity(s, on))
            self.sev_buttons[sev] = b
            chips.addWidget(b)
        bar.addLayout(chips)
        bar.addStretch(1)
        self.progress = QProgressBar(maximumWidth=140, visible=False)
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Scan progress")
        bar.addWidget(self.progress)
        self.cancel_btn = QPushButton("Cancel", visible=False)
        self.cancel_btn.setToolTip("Stop the running scan (Esc)")
        self.cancel_btn.clicked.connect(self.cancel_scan)
        bar.addWidget(self.cancel_btn)
        self.scan_btn = QPushButton("Scan", objectName="primary")
        self.scan_btn.setToolTip("Run all read-only checks (F5)")
        self.scan_btn.clicked.connect(self.start_scan)
        bar.addWidget(self.scan_btn)
        self.copy_btn = QPushButton("Copy summary")
        self.copy_btn.setToolTip("Copy a redacted summary for sharing (Ctrl+Shift+C)")
        self.copy_btn.clicked.connect(self.copy_summary)
        bar.addWidget(self.copy_btn)
        self.export_btn = QToolButton(text="Export", objectName="menuButton")
        self.export_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.export_btn.setToolTip("Save or copy a report. Redacted reports hide user, host and network details.")
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
        bar.addWidget(self.export_btn)
        self.more_btn = QToolButton(text="View", objectName="menuButton")
        self.more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_btn.setToolTip("Appearance and information about Doctor")
        self.more_btn.setAccessibleName("View options")
        more = QMenu(self.more_btn)
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
        bar.addWidget(self.more_btn)
        root.addLayout(bar)

        # Body: groups | results | details (details move below the results when narrow).
        self.body = QSplitter(Qt.Orientation.Horizontal)
        self.body.setChildrenCollapsible(False)
        nav_panel = QWidget()
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(6)
        section = QLabel("Check groups", objectName="section")
        section.setFont(scaled_font(self.font(), 0.92, QFont.Weight.DemiBold))
        nav_layout.addWidget(section)
        self.nav = QListWidget(objectName="nav")
        self.nav.setAccessibleName("Check groups")
        self.nav.setMinimumWidth(190)
        self.nav.setUniformItemSizes(True)
        self.nav_delegate = NavDelegate(self)
        self.nav.setItemDelegate(self.nav_delegate)
        self.nav.currentItemChanged.connect(self._category_changed)
        nav_layout.addWidget(self.nav, 1)
        self.body.addWidget(nav_panel)

        self.inner = QSplitter(Qt.Orientation.Horizontal)
        self.inner.setChildrenCollapsible(False)
        self.stack = QStackedWidget()
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Status", "Group", "Check", "Result"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setAccessibleName("Check results")
        self.status_delegate = StatusDelegate(self)
        self.tree.setItemDelegateForColumn(0, self.status_delegate)
        hdr = self.tree.header()
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hdr.setHighlightSections(False)
        for col, mode in enumerate((QHeaderView.ResizeMode.ResizeToContents, QHeaderView.ResizeMode.ResizeToContents,
                                    QHeaderView.ResizeMode.ResizeToContents, QHeaderView.ResizeMode.Stretch)):
            hdr.setSectionResizeMode(col, mode)
        self.tree.currentItemChanged.connect(self._finding_changed)
        # Empty state: the NoX mark (before the first scan) above a short explanation.
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
        self.details.setAccessibleName("Check details")
        self.details.document().setDocumentMargin(16)
        self.inner.addWidget(self.details)
        self.inner.setStretchFactor(0, 3)
        self.inner.setStretchFactor(1, 2)
        self.body.addWidget(self.inner)
        self.body.setStretchFactor(1, 1)
        self.body.setSizes([220, 1000])
        root.addWidget(self.body, 1)

        self.safety = QLabel(SAFETY_NOTE, objectName="safety")
        self.safety.setAccessibleName("Doctor is read-only, needs no root, makes no network requests "
                                      "and uploads nothing")
        self.statusBar().addPermanentWidget(self.safety)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().showMessage("Ready")

    # theme -------------------------------------------------------------------
    def set_theme_mode(self, mode: str):
        self.theme_mode = mode if mode in themes.MODES else themes.SYSTEM
        self.apply_theme()

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
        self.setStyleSheet(stylesheet(t))
        font = pill_font(self.font())
        self.pill_width = max(pill_size(font, text).width() for text in STATUS_TEXT.values())
        for sev, button in self.sev_buttons.items():
            button.setIcon(dot_icon(t.status[sev].fg, t.subtle))
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
                           (QKeySequence("Ctrl+Shift+C"), self.copy_summary),
                           (QKeySequence.StandardKey.Save, lambda: self.export(True, "text")),
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
        if self.scanning() or self.close_requested:
            return  # rapid refreshes are ignored, never queued
        self.generation += 1
        self.busy = True
        self.last_error = ""
        worker = ScanThread(self.generation, self._scan_func, self._context_factory, self)
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
        self.statusBar().showMessage("Scanning… read-only checks are running.")
        self.worker.start()
        self.render_all()

    def cancel_scan(self):
        if self.scanning() and self.worker is not None:
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.statusBar().showMessage("Cancelling… stopping running checks.")

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
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        self.statusBar().showMessage(f"Scanning {done}/{total} · {title}")

    @Slot(object, int)
    def _completed(self, result, generation):
        if not self._on_gui_thread("_completed"):
            return
        if generation != self.generation:
            return  # a stale scan never overwrites a newer one
        self.report = result
        when = datetime.datetime.now().strftime("%H:%M:%S")
        self.statusBar().showMessage(
            f"Scan finished at {when} in {result['duration_ms']} ms"
            + ("" if result["complete"] else " · incomplete: cancelled or timed out"))
        self.render_all()

    @Slot(str, int)
    def _failed(self, message, generation):
        if not self._on_gui_thread("_failed"):
            return
        if generation == self.generation:
            self.last_error = message[:500]
            self.statusBar().showMessage(f"Scan failed: {message[:200]}")

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
            self.statusBar().showMessage("Closing after the running checks stop…")
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
        self.subtitle.setText(host or "Read-only system health diagnostics")
        if self.report:
            state = self.report["overall"]
            complete = self.report["complete"]
            self._set_verdict(themes.HEADLINE[state] + ("" if complete else " (incomplete)"), state,
                              f"Overall state {STATUS_TEXT[state]}. {counts}")
            stamp = datetime.datetime.fromisoformat(self.report["generated_at"]).astimezone()
            self.snapshot.setText(f"Last scan {stamp:%Y-%m-%d %H:%M}, took {vm.duration_text(self.report['duration_ms'])}")
            totals = self.report["totals"]
            for sev, button in self.sev_buttons.items():
                button.setText(f"{STATUS_TEXT[sev]}  {totals[sev]}")
        else:
            self._set_verdict("Scanning…" if self.scanning() else "Not scanned yet", None, "No results yet")
            self.snapshot.setText("")
            for sev, button in self.sev_buttons.items():
                button.setText(STATUS_TEXT[sev])
        self.snapshot.setVisible(self.report is not None)
        self.scan_btn.setText("Scan again" if self.report else "Scan")
        self.copy_btn.setEnabled(self.report is not None)
        self.export_btn.setEnabled(self.report is not None)
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
        items = vm.visible(self.report, self.category, query, sevs)
        self.tree.blockSignals(True)
        self.tree.clear()
        keep = None
        for f in items:
            sev = f["severity"]
            row = QTreeWidgetItem([STATUS_TEXT[sev], CATEGORY_TITLE[f["category"]], f["title"], f["summary"]])
            row.setData(0, Qt.ItemDataRole.UserRole, f["id"])
            row.setData(0, SEV_ROLE, sev)
            row.setToolTip(2, f["title"])
            row.setToolTip(3, f["summary"])
            row.setData(0, Qt.ItemDataRole.AccessibleTextRole, f"{STATUS_TEXT[sev]}: {f['title']}")
            self.tree.addTopLevelItem(row)
            if f["id"] == self.selected_id:
                keep = row
        self.tree.setColumnHidden(1, self.category != vm.OVERVIEW)
        self.tree.blockSignals(False)
        message = vm.empty_state(self.report, self.scanning(), self.category, query, sevs)
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
                self.details.setHtml(vm.overview_html(self.report, self.scanning(), self.theme))

    def _toggle_severity(self, sev, on):
        (self.severities.add if on else self.severities.discard)(sev)
        self.render_list()

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
            self.details.setHtml(vm.detail_html(finding, self.theme, self.mono))

    # output ------------------------------------------------------------------
    def _identity(self):
        user, host = self.identity
        return user or os.environ.get("USER", ""), host

    def copy_summary(self):
        if not self.report:
            return
        user, host = self._identity()
        shared = reports.redact_report(self.report, user, host)
        QApplication.clipboard().setText(reports.format_summary(shared))
        self.statusBar().showMessage("Redacted summary copied to the clipboard.")

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
                f"<p style='color:{t.muted};margin-top:0'>NoX is Latin for night, and the name is a play on "
                f"&ldquo;Doctor Noooo&rdquo;.</p>"
                f"<p>Read-only system health checks for Linux: Fedora (including Atomic and bootc), "
                f"Debian and Ubuntu, Arch and openSUSE, with general checks everywhere else.</p>"
                f"<p>No root, no repairs, no network requests and no telemetry. "
                f"Reports are written only where you choose.</p>{sandboxed}"
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
    window = Window()
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
        F("hardware.graphics", "hardware", "Graphics", "pass", "AMD: amdgpu driver"),
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
                Finding("network.link", "network", "Network links", "info", "Offline")]

    def slow_probe(ctx):
        while not ctx.cancel.is_set() and not gate.is_set():
            time.sleep(0.02)
        return Finding("logs.boot", "logs", "Current boot errors", "unknown", "slow fixture")

    probes = [Probe("fixture.fast", "system", "fixture", fixture_probe),
              Probe("fixture.slow", "logs", "slow", slow_probe)]

    def scan(ctx, progress=None):
        return engine.scan(ctx, progress=progress, probes=probes, deadline=30)

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

    window = Window(scan_func=scan, context_factory=fixture_context, autoscan=False, interactive=False)
    window.show()
    app.processEvents()
    log("window shown")

    # 1. scanning
    gate.set()
    window.start_scan()
    log("scan 1 started")
    check(pump(lambda: window.report is not None and not window.scanning()), "scan 1 completes")
    check(window.workers == {} and window.worker is None, "scan 1 thread joined and released")
    check(window.tree.topLevelItemCount() == 5, "overview lists all 5 findings")
    check(window.tree.topLevelItemCount() > 0 and window.tree.topLevelItem(0).data(0, SEV_ROLE) == "error",
          "most severe finding sorts first")
    check(bool(probe_threads) and all(n.startswith("doctor-probe") for n in probe_threads),
          "probes ran on engine worker threads")

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
    check(window.thread_violations == [], f"UI updates ran only on the GUI thread {window.thread_violations}")
    bad_css = [m for m in QT_MESSAGES if "stylesheet" in m.lower()]
    check(bad_css == [], f"every stylesheet parsed without Qt warnings {bad_css[:3]}")
    _teardown(app, window)
    log("teardown done")

    if failures:
        print("Doctor NoX GUI self-test FAILED: " + "; ".join(failures), flush=True)
        return 1
    print("Doctor NoX GUI self-test: OK (synthetic probes; no host commands)", flush=True)
    return 0
