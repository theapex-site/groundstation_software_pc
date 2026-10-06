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

from PyQt5 import QtGui, QtWidgets

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


def repolish(*widgets: QtWidgets.QWidget) -> None:
    """Re-apply the stylesheet after a property a selector depends on changed."""
    for w in widgets:
        if w is None:
            continue
        w.style().unpolish(w)
        w.style().polish(w)


def shadow(widget: QtWidgets.QWidget, blur: int = 34, dy: int = 10,
           alpha: int = 130) -> None:
    """
    Qt stylesheets have no box-shadow; this is the supported equivalent.

    The site puts 0 20px 50px rgba(0,0,0,.5) under its modals, which is what
    gives the panels their lift. Flat 1px borders everywhere is why the old
    build read as a utility rather than a product.
    """
    effect = QtWidgets.QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setXOffset(0)
    effect.setYOffset(dy)
    effect.setColor(QtGui.QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)


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
    margin-top: 14px;
    padding: 12px 6px 6px 6px;
    font-family: "{MONO}";
    font-size: {FS_LABEL}px;
    font-weight: 600;
    letter-spacing: {TRACK_LABEL};
    color: {SLATE};
}}
/* Without this the title is drawn straight across its own border. The
   object-detection dialog already carried a local copy of this rule, which
   is why that one window looked right and the nine groups here did not. */
#groupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 3px 10px;
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: {R_PILL}px;
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

/* Session chips beside the payload heading: port, signal, clock, packets. */
#statChip {{
    font-family: "{MONO}";
    font-size: {FS_LABEL}px;
    color: {SLATE};
    background: {RAISED};
    border: 1px solid {LINE};
    border-radius: {R_PILL}px;
    padding: 5px 12px;
}}
#linkChip {{
    font-family: "{MONO}";
    font-size: {FS_LABEL}px;
    font-weight: 600;
    border-radius: {R_PILL}px;
    padding: 5px 12px;
    background: {RAISED};
    border: 1px solid {LINE};
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

QPushButton#ghostBtn {{ background: transparent; color: {SLATE}; }}
QPushButton#ghostBtn:hover {{ border-color: {TEAL}; color: {TEAL}; }}

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
"""
