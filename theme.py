"""
theme.py
────────
The single source of truth for how APEX GCS looks.

Replaces four separate hardcoded copies of the same palette — config.STYLE,
dialogs._BG/_SURFACE/..., ground_station._BG/..., and
ObjectDetectionDialog._BG/... — which had already drifted apart from each
other and from the website.

Colours are the tokens in theapex.site/css/style.css, verbatim. Status
colours (green/amber/red) are deliberately NOT site tokens: a coloured
telemetry value has to mean something, so they stay semantic.

Usage:
    import theme
    theme.init(app)                  # after QApplication exists
    app.setStyleSheet(theme.qss())
"""
from __future__ import annotations

import os
import sys

from PyQt5 import QtCore, QtGui, QtSvg, QtWidgets

# ══════════════════════════════════════════════════════════════════════════════
#  Colour — theapex.site/css/style.css
# ══════════════════════════════════════════════════════════════════════════════
DEEP    = "#000000"   # --navy-deep    page ground
PANEL   = "#08080f"   # --navy-panel   chrome behind cards
RAISED  = "#0d0d18"   # --navy-raised  the cards themselves
LINE    = "#1a1a2e"   # --navy-line    every border
CREAM   = "#f5f0e6"   # --cream        primary text
SLATE   = "#94a3b8"   # --slate        secondary text
DIM     = "#5b6b85"   # --slate-dim    tertiary / stale
TEAL    = "#2dd4bf"   # --teal         accent
BLUE    = "#3b82f6"   # --blue
MAGENTA = "#d946ef"   # --magenta
PINK    = "#ec4899"   # --pink
AMBER   = "#f59e0b"   # --orange

# Semantic status. Not from the site, and not to be rebranded.
OK      = "#22c55e"
WARN    = "#facc15"
ALARM   = "#ef4444"

# Derived, for fills and hovers that need to sit on DEEP.
TEAL_SOFT  = "rgba(45, 212, 191, 0.12)"
TEAL_EDGE  = "rgba(45, 212, 191, 0.35)"
ALARM_EDGE = "#44202c"
ALARM_FILL = "#4a1218"
DISABLED   = "#3f4a60"

GRADIENT = (
    "qlineargradient(x1:0, y1:0, x2:1, y2:0, "
    f"stop:0 {TEAL}, stop:0.45 {BLUE}, stop:0.8 {MAGENTA}, stop:1 {PINK})"
)
GRADIENT_COOL = (
    f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {TEAL}, stop:1 {BLUE})"
)

# ══════════════════════════════════════════════════════════════════════════════
#  Radius — the site uses four values; this app had eleven
# ══════════════════════════════════════════════════════════════════════════════
R_CARD = 16
R_CTRL = 10
R_SM   = 6
R_PILL = 999

# ══════════════════════════════════════════════════════════════════════════════
#  Type
# ══════════════════════════════════════════════════════════════════════════════
# Resolved by init() against what is actually installed. Drop the real TTFs
# into fonts/ and they are picked up automatically; until then these fall back
# to faces every Windows machine has, which still beats Consolas for prose.
_DISPLAY_STACK = ["Space Grotesk", "Segoe UI Semibold", "Segoe UI", "Arial"]
_BODY_STACK    = ["Inter", "Segoe UI", "Arial"]
_MONO_STACK    = ["JetBrains Mono", "Cascadia Mono", "Consolas", "Courier New"]

DISPLAY = _DISPLAY_STACK[-1]
BODY    = _BODY_STACK[-1]
MONO    = _MONO_STACK[-1]

# Scale. The old app ran 20/18/12/11 with no ratio and put data at the same
# size as its own labels.
FS_TITLE  = 26   # window / page title
FS_HEAD   = 19   # panel heading
FS_VALUE  = 15   # telemetry readout — the number is the point
FS_BODY   = 13
FS_LABEL  = 11   # uppercase caption
FS_MICRO  = 10

TRACK_LABEL = "1.6px"   # the site's 0.18em at label size

# ══════════════════════════════════════════════════════════════════════════════
#  Spacing
# ══════════════════════════════════════════════════════════════════════════════
GAP   = 14
PAD   = 16
GUTTER = 22

# Tooltips. Every call site hardcoded 2000ms on tooltips that run to three
# lines — they expired mid-read. Qt's own default scales with the length of
# the text; this is that, with a floor that clears a three-line tip.
TIP_MS = 12000


def _resource(*parts: str) -> str:
    base = getattr(sys, "_MEIPASS", None) or os.path.abspath(os.path.dirname(__file__))
    return os.path.join(base, *parts)


def _asset_url(name: str) -> str:
    """
    Path to an assets/ file, in the form Qt stylesheets want.

    Qt does not render the CSS border-triangle trick for sub-controls like
    QComboBox::down-arrow — it paints the borders as a solid block instead.
    Arrows and ticks have to be real images, so they live in assets/.
    """
    return _resource("assets", name).replace("\\", "/")


def _first_installed(stack: list[str]) -> str:
    """The first family in `stack` the system can actually render."""
    available = set(QtGui.QFontDatabase().families())
    for family in stack:
        if family in available:
            return family
    return stack[-1]


def load_fonts() -> int:
    """
    Register any .ttf/.otf in fonts/ with Qt.

    Returns how many faces were loaded. Nothing here fails the app: with no
    fonts/ directory the stacks above fall back to system faces.
    """
    folder = _resource("fonts")
    if not os.path.isdir(folder):
        return 0
    loaded = 0
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith((".ttf", ".otf")):
            if QtGui.QFontDatabase.addApplicationFont(os.path.join(folder, name)) != -1:
                loaded += 1
    return loaded


def init(app: QtWidgets.QApplication) -> None:
    """Load bundled fonts, resolve the three families, set the app base font."""
    global DISPLAY, BODY, MONO
    load_fonts()
    DISPLAY = _first_installed(_DISPLAY_STACK)
    BODY    = _first_installed(_BODY_STACK)
    MONO    = _first_installed(_MONO_STACK)

    base = QtGui.QFont(BODY, 10)
    base.setStyleStrategy(QtGui.QFont.PreferAntialias)
    app.setFont(base)

    icon_path = _resource("icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QtGui.QIcon(icon_path))


class TabCard(QtWidgets.QGroupBox):
    """
    A card whose heading is part of its outline rather than a chip on top.

    Qt stylesheets can only curve a corner inwards, so a QGroupBox::title
    always meets the card's top edge at a hard right angle — the heading
    reads as a separate tile pasted over the border. The join needs a
    concave fillet, which means painting card and heading as one path.

    Everything is drawn here, so the stylesheet does not style this widget.
    """

    PAD_X = 14          # inside the heading, left and right of the text
    PAD_Y = 6           # above and below the heading text
    NOTCH = 11          # radius of the fillet into the card's top edge
    ICON = 14           # heading glyph, square
    ICON_GAP = 8        # between that glyph and the text

    def __init__(self, title: str = "", parent=None, icon_name: str = "",
                 headless: bool = False):
        super().__init__(title, parent)
        self._icon_name = icon_name
        # A card with no heading tab at all — just the rounded outline.
        # Used where the card title would be redundant with context (the
        # map and the flag log both sit under a heading row of their own
        # in the centre column).
        self._headless = headless
        self._title_font = QtGui.QFont(MONO, pointSize=-1)
        self._title_font.setPixelSize(FS_LABEL)
        self._title_font.setWeight(QtGui.QFont.DemiBold)
        self._title_font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 1.4)

    def _icon_span(self) -> int:
        return self.ICON + self.ICON_GAP if self._icon_name else 0

    def tab_height(self) -> int:
        if self._headless:
            return 0
        return QtGui.QFontMetrics(self._title_font).height() + self.PAD_Y * 2

    def tab_width(self) -> int:
        fm = QtGui.QFontMetrics(self._title_font)
        return min(fm.horizontalAdvance(self.title())
                   + self._icon_span() + self.PAD_X * 2,
                   max(self.width() - R_CARD - self.NOTCH, 40))

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)

        w, h = self.width(), self.height()
        r = float(R_CARD)

        if self._headless:
            # Plain rounded rect — no tab, no fillet, nothing to join.
            path = QtGui.QPainterPath()
            path.addRoundedRect(QtCore.QRectF(0, 0, w, h), r, r)
            p.fillPath(path, QtGui.QColor(RAISED))
            pen = QtGui.QPen(QtGui.QColor(LINE))
            pen.setWidthF(1.0)
            p.strokePath(path, pen)
            p.end()
            return

        n = float(self.NOTCH)
        th = float(self.tab_height())
        tw = float(self.tab_width())

        path = QtGui.QPainterPath()
        # Heading: up the left edge and across the top.
        path.moveTo(0, r)
        path.arcTo(0, 0, 2 * r, 2 * r, 180, -90)        # heading top-left
        path.lineTo(tw - r, 0)
        path.arcTo(tw - 2 * r, 0, 2 * r, 2 * r, 90, -90)  # heading top-right
        path.lineTo(tw, th - n)
        # The fillet: curves away from the heading into the card's top edge,
        # which is the join a border-radius cannot express.
        path.arcTo(tw, th - 2 * n, 2 * n, 2 * n, 180, 90)
        # Card: across the top, then round the remaining three corners.
        path.lineTo(w - r, th)
        path.arcTo(w - 2 * r, th, 2 * r, 2 * r, 90, -90)
        path.lineTo(w, h - r)
        path.arcTo(w - 2 * r, h - 2 * r, 2 * r, 2 * r, 0, -90)
        path.lineTo(r, h)
        path.arcTo(0, h - 2 * r, 2 * r, 2 * r, 270, -90)
        path.closeSubpath()

        p.fillPath(path, QtGui.QColor(RAISED))
        pen = QtGui.QPen(QtGui.QColor(LINE))
        pen.setWidthF(1.0)
        p.strokePath(path, pen)

        text_x = self.PAD_X + self._icon_span()
        if self._icon_name:
            pix = icon(self._icon_name, SLATE, self.ICON).pixmap(
                self.ICON, self.ICON)
            p.drawPixmap(QtCore.QPoint(self.PAD_X,
                                       int((th - self.ICON) / 2)), pix)

        p.setFont(self._title_font)
        p.setPen(QtGui.QColor(SLATE))
        p.drawText(QtCore.QRectF(text_x, 0, tw - text_x, th),
                   QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, self.title())
        p.end()


def repolish(*widgets: QtWidgets.QWidget) -> None:
    """Re-apply the stylesheet after a property a selector depends on changed."""
    for w in widgets:
        if w is None:
            continue
        w.style().unpolish(w)
        w.style().polish(w)


def shadow(widget: QtWidgets.QWidget, blur: int = 34, dy: int = 10,
           alpha: int = 130) -> QtWidgets.QGraphicsDropShadowEffect:
    """
    Qt stylesheets have no box-shadow; this is the supported equivalent.

    The site puts 0 20px 50px rgba(0,0,0,.5) under its modals, which is what
    gives the panels their lift. Flat 1px borders everywhere is why the old
    build read as a utility rather than a product.

    The layout gap around a panel is only GAP px, so the falloff is tuned to
    be read within that rather than copying the site's 50px blur, which would
    simply be clipped.
    """
    effect = QtWidgets.QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setXOffset(0)
    effect.setYOffset(dy)
    effect.setColor(QtGui.QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)
    return effect


class _HoverGlow(QtCore.QObject):
    """Blooms a teal glow behind a widget while the cursor is over it.

    The site lifts its buttons 2px on hover and sits its cards on a dark
    shadow. Neither transfers here. Animating `pos` fights the layout, which
    owns the geometry and puts it back on the next relayout; and a black
    shadow over a #000000 ground is invisible — measured at a 1% change in the
    gap beside a panel. What the site actually reads as depth on dark is the
    radial glow on its cards, so that is what this animates.
    """

    def __init__(self, widget: QtWidgets.QWidget, radius: int):
        super().__init__(widget)
        self._radius = radius
        self._fx = QtWidgets.QGraphicsDropShadowEffect(widget)
        self._fx.setOffset(0, 0)
        self._fx.setBlurRadius(0)
        self._fx.setColor(QtGui.QColor(45, 212, 191, 150))   # TEAL
        widget.setGraphicsEffect(self._fx)

        self._anim = QtCore.QPropertyAnimation(self._fx, b"blurRadius", self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        widget.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Enter:
            self._to(self._radius)
        elif event.type() == QtCore.QEvent.Leave:
            self._to(0)
        return False

    def _to(self, radius: int):
        self._anim.stop()
        self._anim.setStartValue(self._fx.blurRadius())
        self._anim.setEndValue(radius)
        self._anim.start()


def hover_lift(widget: QtWidgets.QWidget, radius: int = 22) -> None:
    """Give `widget` a teal glow that blooms under the cursor."""
    _HoverGlow(widget, radius)


# ══════════════════════════════════════════════════════════════════════════════
#  Icons
# ══════════════════════════════════════════════════════════════════════════════
_ICON_CACHE: dict[tuple[str, str, int], QtGui.QIcon] = {}


def icon(name: str, color: str = SLATE, size: int = 16) -> QtGui.QIcon:
    """
    A monochrome line icon from assets/icons, stroked in `color`.

    The app used ~50 colour emoji, which render in the system's own palette at
    whatever weight the emoji font decides — four unrelated hues in a heading
    row, and a different set again on the buttons. These are one stroke weight
    on one grid, tinted from the tokens above, so a heading reads as a heading
    rather than as a sticker.
    """
    key = (name, color, size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]

    path = _resource("assets", "icons", f"{name}.svg")
    if not os.path.exists(path):
        return QtGui.QIcon()

    with open(path, "r", encoding="utf-8") as fh:
        svg = fh.read().replace("CURRENTCOLOR", color)

    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8")))
    ratio = QtWidgets.QApplication.instance().devicePixelRatio() \
        if QtWidgets.QApplication.instance() else 1.0
    pix = QtGui.QPixmap(int(size * ratio), int(size * ratio))
    pix.setDevicePixelRatio(ratio)
    pix.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()

    result = QtGui.QIcon(pix)
    _ICON_CACHE[key] = result
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  The global stylesheet
# ══════════════════════════════════════════════════════════════════════════════
def qss() -> str:
    """Application-wide stylesheet, built from the tokens above."""
    return f"""
QMainWindow {{ background: transparent; }}
QWidget {{ font-family: "{BODY}"; color: {CREAM}; }}
QToolTip {{
    background-color: {RAISED};
    color: {CREAM};
    border: 1px solid {LINE};
    border-radius: {R_SM}px;
    padding: 6px 9px;
    font-size: {FS_BODY}px;
}}

/* No rule here at all meant QMenu — RTG's "+ Add Graph" among others —
   fell through to the platform's own menu style: white, system font, no
   relation to anything else on screen. */
QMenu {{
    background-color: {RAISED};
    color: {CREAM};
    border: 1px solid {LINE};
    border-radius: {R_SM}px;
    padding: 4px;
    font-size: {FS_BODY}px;
}}
QMenu::item {{
    padding: 6px 24px 6px 12px;
    border-radius: {R_SM}px;
}}
QMenu::item:selected {{ background: {TEAL_SOFT}; color: {CREAM}; }}
QMenu::item:disabled {{ color: {DIM}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 8px; }}
QMenu::right-arrow {{ width: 10px; height: 10px; }}

/* ── Panels ─────────────────────────────────────────────────────────────── */
#TelemetryPanel, #RightPanel {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: {R_CARD}px;
}}

#groupBox {{
    border: 1px solid {LINE};
    border-radius: {R_CARD}px;
    background: {RAISED};
    margin-top: 18px;
    padding: 12px 6px 6px 6px;
    font-family: "{MONO}";
    font-size: {FS_LABEL}px;
    font-weight: 600;
    letter-spacing: {TRACK_LABEL};
    color: {SLATE};
}}
/* The title is a tab, not a floating chip: rounded along the top, open at
   the bottom, and carrying the card's own fill so the two read as one
   shape. It overlaps the card's top border by a few pixels, which is what
   hides the line underneath and makes them join.
   Without a ::title rule at all, Qt draws the text straight across the
   border — object_detection.py already carried a local fix, which is why
   that one dialog looked right and the nine groups here did not. */
#groupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 0px;
    padding: 5px 14px 8px 14px;
    background: {RAISED};
    border: 1px solid {LINE};
    border-bottom: none;
    border-top-left-radius: {R_CARD}px;
    border-top-right-radius: {R_CARD}px;
    border-bottom-left-radius: 0px;
    border-bottom-right-radius: 0px;
    color: {SLATE};
}}

#mainTitle {{
    font-family: "{DISPLAY}";
    color: {CREAM};
    font-size: {FS_HEAD}px;
    font-weight: 700;
    padding: 4px 2px;
}}

#nameLabel {{
    color: {SLATE};
    font-size: {FS_LABEL}px;
    font-weight: 500;
}}
#valueLabel {{
    font-family: "{MONO}";
    color: {CREAM};
    font-size: {FS_VALUE}px;
    font-weight: 600;
}}
/* Health state is set as a property, so the rule below replaces only the
   colour and leaves the family and size intact. Writing a whole stylesheet
   onto the label instead — which is what the old _color() did — dropped
   font-size and shrank every thresholded value a second after launch. */
#valueLabel[state="ok"]    {{ color: {OK}; }}
#valueLabel[state="warn"]  {{ color: {WARN}; }}
#valueLabel[state="alarm"] {{ color: {ALARM}; }}
#valueLabel[state="stale"] {{ color: {DIM}; }}

#unitLabel {{
    font-family: "{MONO}";
    color: {SLATE};
    font-size: {FS_LABEL}px;
}}
#unitLabel[state="stale"] {{ color: {DIM}; }}

/* Health is carried twice: a stripe down the left of the row and a
   direction glyph beside the value. Colour alone fails for red-green
   colour blindness, in a greyscale screenshot and on a projector. */
#rowStripe {{
    min-width: 3px; max-width: 3px;
    border-radius: 2px;
    background: {LINE};
}}
#rowStripe[state="ok"]    {{ background: {OK}; }}
#rowStripe[state="warn"]  {{ background: {WARN}; }}
#rowStripe[state="alarm"] {{ background: {ALARM}; }}
#rowStripe[state="stale"] {{ background: #2a3044; }}

#rowGlyph {{
    font-family: "{MONO}";
    font-size: {FS_MICRO}px;
    color: transparent;
}}
#rowGlyph[state="warn"]  {{ color: {WARN}; }}
#rowGlyph[state="alarm"] {{ color: {ALARM}; }}

/* Data age, in the telemetry panel heading. */
#agePill {{
    font-family: "{MONO}";
    font-size: {FS_MICRO}px;
    font-weight: 600;
    padding: 4px 10px;
    border-radius: {R_PILL}px;
    background: {RAISED};
    border: 1px solid {LINE};
    color: {SLATE};
}}
#agePill[state="live"]  {{ color: {OK};    border-color: {OK}; }}
#agePill[state="stale"] {{ color: {WARN};  border-color: {WARN}; }}
#agePill[state="lost"]  {{ color: {ALARM}; border-color: {ALARM}; }}

/* Application bar, in place of the old 250px logo header. */
#appBar {{
    background: {PANEL};
    border-bottom: 1px solid {LINE};
}}
#appBarMark {{
    font-family: "{DISPLAY}";
    font-size: {FS_HEAD}px;
    font-weight: 700;
    letter-spacing: 1px;
    color: {CREAM};
}}
#appBarSub {{
    font-family: "{BODY}";
    font-size: {FS_LABEL}px;
    font-weight: 500;
    letter-spacing: {TRACK_LABEL};
    color: {SLATE};
}}

/* Caption over a group of commands, so a flat stack of buttons reads as
   Payload / Comms / System rather than eight identical rows. */
#groupCaption {{
    font-family: "{MONO}";
    font-size: {FS_MICRO}px;
    font-weight: 600;
    letter-spacing: {TRACK_LABEL};
    color: {DIM};
    padding: 8px 2px 2px 2px;
}}

/* Session chips in the application bar: port, signal, clock, packets.
   Same tab shape as the group titles — rounded along the top, open at the
   bottom — so they sit on the bar's bottom border rather than floating in
   the middle of it. They are added with AlignBottom for that to land. */
#statChip {{
    font-family: "{MONO}";
    font-size: {FS_MICRO}px;
    color: {SLATE};
    background: {RAISED};
    border: 1px solid {LINE};
    border-bottom: none;
    border-top-left-radius: {R_CTRL}px;
    border-top-right-radius: {R_CTRL}px;
    border-bottom-left-radius: 0px;
    border-bottom-right-radius: 0px;
    padding: 5px 11px 6px 11px;
}}
#linkChip {{
    font-family: "{MONO}";
    font-size: {FS_MICRO}px;
    font-weight: 600;
    background: {RAISED};
    border: 1px solid {LINE};
    border-bottom: none;
    border-top-left-radius: {R_CTRL}px;
    border-top-right-radius: {R_CTRL}px;
    border-bottom-left-radius: 0px;
    border-bottom-right-radius: 0px;
    padding: 5px 11px 6px 11px;
    color: {SLATE};
}}
#linkChip[state="stable"]    {{ color: {OK};    border-color: {OK}; }}
#linkChip[state="acquiring"] {{ color: {SLATE}; border-color: {LINE}; }}
#linkChip[state="lost"]      {{ color: {ALARM}; border-color: {ALARM}; }}
#linkChip[state="blink"]     {{ color: {ALARM_EDGE}; border-color: {ALARM_EDGE}; }}

/* The banner that says why a reading cannot be trusted. */
#staleBanner {{
    background: {RAISED};
    border: 1px solid {LINE};
    border-radius: {R_CTRL}px;
    color: {SLATE};
    font-size: {FS_LABEL}px;
    padding: 8px 11px;
}}
#staleBanner[state="stale"] {{ color: {WARN}; border-color: {WARN}; }}
#staleBanner[state="lost"]  {{ color: {ALARM}; border-color: {ALARM}; }}

/* ── Text areas ─────────────────────────────────────────────────────────── */
QTextEdit {{
    background: {DEEP};
    color: {CREAM};
    border: 1px solid {LINE};
    border-radius: {R_CTRL}px;
    padding: 8px;
    font-family: "{MONO}";
    font-size: {FS_LABEL}px;
    selection-background-color: {TEAL};
    selection-color: {DEEP};
}}

/* ── Buttons ────────────────────────────────────────────────────────────── */
QPushButton {{
    background: {RAISED};
    color: {CREAM};
    border: 1px solid {LINE};
    padding: 9px 14px;
    border-radius: {R_CTRL}px;
    font-size: {FS_BODY}px;
    font-weight: 500;
    text-align: left;
}}
QPushButton:hover  {{ border-color: {TEAL}; color: {TEAL}; }}
QPushButton:pressed {{ background: {PANEL}; }}
QPushButton:focus  {{ border: 2px solid {TEAL}; }}
QPushButton:disabled {{
    background: {PANEL};
    color: {DISABLED};
    border-color: {LINE};
}}

/* The site's primary button: gradient fill, dark text. */
QPushButton#primaryBtn {{
    background: {GRADIENT};
    color: {DEEP};
    border: none;
    font-family: "{DISPLAY}";
    font-weight: 700;
    text-align: center;
    padding: 11px 20px;
}}
QPushButton#primaryBtn:hover    {{ background: {GRADIENT_COOL}; }}
QPushButton#primaryBtn:disabled {{ background: {LINE}; color: {DISABLED}; }}

QPushButton#dangerBtn {{ border-color: {ALARM_EDGE}; color: {ALARM}; }}
QPushButton#dangerBtn:hover {{ background: {ALARM_FILL}; color: {CREAM}; border-color: {ALARM}; }}

QPushButton#ghostBtn, QToolButton#ghostBtn {{ background: transparent; color: {SLATE}; }}
QPushButton#ghostBtn:hover, QToolButton#ghostBtn:hover {{ border-color: {TEAL}; color: {TEAL}; }}

/* Round icon button. Needs its own font: the glyph is not in the body face,
   and the shared QPushButton rule left-aligns its text. */
QPushButton#settingsBtn {{
    background: {RAISED};
    color: {TEAL};
    border: 1px solid {LINE};
    border-radius: 17px;
    font-family: "Segoe UI Symbol", "Segoe UI";
    font-size: 15px;
    padding: 0px;
    text-align: center;
}}

/* RTG — the way into every sensor channel. Reads as a destination rather
   than a command, so it carries the accent rather than a card outline. */
QPushButton#rtgBtn {{
    background: transparent;
    color: {TEAL};
    border: 1px solid {TEAL};
    border-radius: {R_CTRL}px;
    font-family: "{MONO}";
    font-size: {FS_LABEL}px;
    font-weight: 700;
    letter-spacing: {TRACK_LABEL};
    padding: 7px 14px;
    text-align: center;
}}
QPushButton#rtgBtn:hover  {{ background: {TEAL}; color: {DEEP}; }}
QPushButton#rtgBtn:focus  {{ border: 2px solid {CREAM}; }}

/* The profile, as the operator's initials in a teal disc. A gear here meant
   preferences to everyone and opened a profile instead. */
QPushButton#avatarBtn {{
    background: {TEAL};
    color: {DEEP};
    border: 1px solid {TEAL};
    border-radius: 17px;
    font-family: "{BODY}";
    font-size: {FS_BODY}px;
    font-weight: 600;
    letter-spacing: 0.5px;
    padding: 0px;
    text-align: center;
}}
QPushButton#avatarBtn:hover {{
    background: {RAISED};
    color: {TEAL};
}}
QPushButton#avatarBtn:focus {{ border: 2px solid {CREAM}; }}
QPushButton#settingsBtn:hover {{
    background: {TEAL};
    color: {DEEP};
    border-color: {TEAL};
}}

/* ── Progress ───────────────────────────────────────────────────────────── */
QProgressBar {{
    border: 1px solid {LINE};
    border-radius: {R_SM}px;
    text-align: center;
    background-color: {DEEP};
    color: {CREAM};
    height: 18px;
    font-family: "{MONO}";
    font-size: {FS_MICRO}px;
}}
QProgressBar::chunk {{ background: {GRADIENT_COOL}; border-radius: {R_SM}px; }}

/* The downlink bar overlaps the payload canvas's own border rather than
   sitting in a row of its own, so it is set to a specific height by its
   parent's geometry — the generic height:18px above would otherwise
   squash its caption text against that fixed box. No border or radius at
   all: a boxed outline straddling the canvas's own edge read as a stray
   line sitting above the bar, rather than the bar reading as the canvas's
   own edge lighting up for the transfer. */
QProgressBar#downlinkBar {{
    border: none;
    border-radius: 0px;
    text-align: center;
    background-color: {DEEP};
    color: {CREAM};
    font-family: "{MONO}";
    font-size: {FS_MICRO}px;
}}
QProgressBar#downlinkBar::chunk {{ background: {GRADIENT_COOL}; }}

/* ── Inputs ─────────────────────────────────────────────────────────────── */
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
    background: {RAISED};
    color: {CREAM};
    border: 1px solid {LINE};
    border-radius: {R_CTRL}px;
    padding: 8px 11px;
    font-size: {FS_BODY}px;
    selection-background-color: {TEAL};
    selection-color: {DEEP};
}}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {{
    border: 1px solid {TEAL};
    background: {PANEL};
}}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({_asset_url("caret-down.svg")}); width: 10px; height: 6px; }}
QComboBox QAbstractItemView {{
    background: {RAISED};
    color: {CREAM};
    border: 1px solid {LINE};
    border-radius: {R_SM}px;
    selection-background-color: {TEAL};
    selection-color: {DEEP};
    outline: none;
}}
/* Native spin arrows render light grey against the dark chrome. */
QDoubleSpinBox::up-button, QSpinBox::up-button,
QDoubleSpinBox::down-button, QSpinBox::down-button {{
    background: transparent; border: none; width: 18px;
}}
QDoubleSpinBox::up-arrow, QSpinBox::up-arrow {{
    image: url({_asset_url("caret-up.svg")}); width: 10px; height: 6px;
}}
QDoubleSpinBox::down-arrow, QSpinBox::down-arrow {{
    image: url({_asset_url("caret-down.svg")}); width: 10px; height: 6px;
}}

QCheckBox {{ color: {SLATE}; font-size: {FS_BODY}px; spacing: 9px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {LINE};
    border-radius: 4px;
    background: {RAISED};
}}
QCheckBox::indicator:hover {{ border-color: {TEAL}; }}
QCheckBox::indicator:checked {{
    background: {TEAL};
    border-color: {TEAL};
    image: url({_asset_url("check.svg")});
}}

/* ── Scrollbars ─────────────────────────────────────────────────────────── */
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {LINE}; border-radius: 4px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {DIM}; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {LINE}; border-radius: 4px; min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: {DIM}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QScrollArea {{ background: transparent; border: none; }}
/* The viewport and the widget inside it are separate QWidgets, and both
   default to the palette's window colour — a light slab behind the cards. */
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollArea > QWidget > QScrollBar {{ background: transparent; }}
"""
