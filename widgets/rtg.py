"""
RTG — Real-Time Graphs.

A blank canvas, not an auto-arranged mosaic: the operator picks which
channels matter to them from "+ Add Graph", drags each one wherever it is
useful, and resizes it — width and height independently — by dragging its
corner. Placement snaps to an invisible grid so tiles line up without the
operator having to line them up by eye.

The layout (which channels, where, at what size) is remembered between
sessions, via settings.get_rtg_layout()/set_rtg_layout() — a fresh install
starts with a genuinely empty canvas, the way an empty canvas should start.
"""

from __future__ import annotations

from PyQt5 import QtCore, QtGui, QtWidgets

import settings
import theme
from widgets.graphs import MiniGraph, GraphPopup, _fmt


# (key, label, unit, section, primary). `primary` marks the four the dashboard
# used to show; they keep a teal outline here so the familiar ones stand out.
# `section` is now just where a channel sits in the "+ Add Graph" menu and
# which accent colour its trace plots in — it no longer groups tiles on the
# canvas, which has no fixed sections of its own.
CHANNELS: list[tuple[str, str, str, str, bool]] = [
    ("batt_v",   "Battery Voltage", "V",    "POWER",  True),
    ("batt_soc", "Battery SOC",     "%",    "POWER",  False),
    ("solar_v",  "Solar Voltage",   "V",    "POWER",  False),
    ("current",  "Current",         "A",    "POWER",  False),
    ("power",    "Power",           "W",    "POWER",  False),

    ("altitude", "Altitude",        "m",    "FLIGHT", True),
    ("speed",    "Speed",           "m/s",  "FLIGHT", False),
    ("azimuth",  "Azimuth",         "°",    "FLIGHT", False),
    ("elevation", "Elevation",      "°",    "FLIGHT", False),

    ("temp",     "Temperature",     "°C",   "ENV",    True),
    ("pressure", "Pressure",        "hPa",  "ENV",    False),
    ("humidity", "Humidity",        "%",    "ENV",    False),

    ("aqi",      "AQI Index",       "",     "AIR",    True),
    ("eco2",     "eCO2",            "ppm",  "AIR",    False),
    ("voc",      "VOC Index",       "",     "AIR",    False),
    ("gas_res",  "Gas Resistance",  "Ω",    "AIR",    False),
]
CHANNEL_INFO: dict[str, tuple[str, str, str, bool]] = {
    key: (label, unit, section, primary)
    for key, label, unit, section, primary in CHANNELS
}

SECTIONS = ["POWER", "FLIGHT", "ENV", "AIR"]

# One accent per section instead of every channel plotting in the same teal —
# a wall of sixteen identical-looking traces had nothing but the label to
# tell them apart.
SECTION_ACCENT: dict[str, str] = {
    "POWER":  theme.TEAL,     # the brand accent, kept for the section
                              # people look at first
    "FLIGHT": theme.BLUE,
    "ENV":    theme.AMBER,
    "AIR":    "#c084fc",      # violet — no existing theme token this cool
}


class RTGTile(MiniGraph):
    """A canvas tile: drag the body to move it, the bottom-right corner to
    resize it, the bottom-left x to remove it, double-click to detach it.

    Position and size are kept in grid cells (`grid_col`, `grid_row`,
    `col_span`, `row_span`), not pixels — RTGWindow converts those to
    geometry against its own current cell size, so the canvas rescales
    cleanly with the window instead of the operator's layout being pinned
    to one window size.
    """

    interaction_finished = QtCore.pyqtSignal(object)  # mouse released after a drag/resize
    remove_requested     = QtCore.pyqtSignal(object)

    GRIP = 16          # resize hit target, bottom-right
    CLOSE = 26         # remove hit target, top-left beside the title
    DRAG_THRESHOLD = 6  # px before a press becomes a move, not a click
    MAX_ROW_SPAN = 4
    MIN_PX = 60        # floor while freely dragging/resizing, before snap

    def __init__(self, title: str, unit: str = "", primary: bool = False,
                 accent: str = None):
        super().__init__(title, unit, accent=accent)
        self.primary = primary
        self.grid_col = 0
        self.grid_row = 0
        self.col_span = 1
        self.row_span = 1
        self.setMinimumHeight(theme.sc(150))
        self.setMouseTracking(True)
        self.setToolTip(
            "Drag to move · drag the corner to resize\n"
            "Double-click to open in a window · the × removes it")

        self._resizing = False
        self._dragging = False
        self._drag_armed = False
        self._press_origin = None
        self._start_geom = QtCore.QRect()

    # ── hit regions ───────────────────────────────────────────────────────────
    def _grip_rect(self) -> QtCore.QRectF:
        g = self.GRIP
        return QtCore.QRectF(self.width() - g, self.height() - g, g, g)

    def _close_rect(self) -> QtCore.QRectF:
        # Top-left, beside the title — a generous box around the dot
        # _paint_header draws there, not just the dot's own small radius.
        g = self.CLOSE
        return QtCore.QRectF(2, 2, g, g)

    # ── mouse: resize the corner, move the body, remove the × ───────────────
    #
    # Both drag and resize move the widget in raw pixels while the mouse is
    # down — no grid math, no signal to the window, nothing but a cheap
    # QWidget.setGeometry() — which is what made this smooth. It used to
    # snap to a grid cell on every single mouse-move tick and tell the
    # window about it, which meant a full reflow of every tile on the
    # canvas (and, originally, a registry write) dozens of times a second.
    # The window only finds out once, in interaction_finished, when it
    # converts wherever the tile ended up back into a grid cell, resolves
    # it against whatever else is on the canvas, and snaps it into place.
    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            super().mousePressEvent(event)
            return
        if self._grip_rect().contains(event.pos()):
            self._resizing = True
            self._press_origin = event.globalPos()
            self._start_geom = self.geometry()
            event.accept()
            return
        if self._close_rect().contains(event.pos()):
            # Acted on here rather than on release — nothing to drag once
            # the tile is about to disappear.
            self.remove_requested.emit(self)
            event.accept()
            return
        # A plain press starts neither a move nor a click; mouseMoveEvent
        # decides which once it sees whether the cursor actually travelled,
        # so a double-click's first press never gets mistaken for a drag.
        self._press_origin = event.globalPos()
        self._start_geom = self.geometry()
        self._drag_armed = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing:
            delta = event.globalPos() - self._press_origin
            w = max(theme.sc(self.MIN_PX), self._start_geom.width() + delta.x())
            h = max(theme.sc(self.MIN_PX), self._start_geom.height() + delta.y())
            self.resize(int(w), int(h))
            event.accept()
            return

        if self._drag_armed or self._dragging:
            delta = event.globalPos() - self._press_origin
            if not self._dragging:
                if (abs(delta.x()) < self.DRAG_THRESHOLD
                        and abs(delta.y()) < self.DRAG_THRESHOLD):
                    super().mouseMoveEvent(event)
                    return
                self._dragging = True
                self.raise_()
            x = self._start_geom.x() + delta.x()
            y = max(0, self._start_geom.y() + delta.y())
            self.move(int(x), int(y))
            event.accept()
            return

        # Hover feedback when nothing is pressed.
        if self._grip_rect().contains(event.pos()):
            self.setCursor(QtCore.Qt.SizeFDiagCursor)
        elif self._close_rect().contains(event.pos()):
            self.setCursor(QtCore.Qt.PointingHandCursor)
        else:
            self.setCursor(QtCore.Qt.SizeAllCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and (self._resizing or self._dragging):
            self._resizing = False
            self._dragging = False
            self._drag_armed = False
            self.interaction_finished.emit(self)
            event.accept()
            return
        self._drag_armed = False
        super().mouseReleaseEvent(event)

    # ── painting ──────────────────────────────────────────────────────────────
    def _paint_card(self, p: QtGui.QPainter) -> QtCore.QRectF:
        rect = super()._paint_card(p)
        if self.primary:
            # The four the dashboard used to carry, marked so they are still
            # findable among sixteen. A 1px outline in the same hue as the
            # base card border was never going to read as different at a
            # glance — this is a heavier line plus a small corner dot that
            # survives even while hovering (which already turns every
            # tile's own outline teal).
            if not self._hover:
                pen = QtGui.QPen(QtGui.QColor(theme.TEAL))
                pen.setWidthF(1.8)
                path = QtGui.QPainterPath()
                r_card = theme.sc(theme.R_CARD)
                path.addRoundedRect(rect, r_card, r_card)
                p.strokePath(path, pen)
            # Top-right corner, clear of the title on the left and the
            # right-aligned value/unit, which stop short of the rounded
            # corner's own inset.
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(theme.TEAL))
            p.drawEllipse(QtCore.QPointF(rect.right() - 7, rect.top() + 8), 2.5, 2.5)
            p.setBrush(QtCore.Qt.NoBrush)

        # Resize grip: three short diagonal strokes, bottom-right.
        pen = QtGui.QPen(QtGui.QColor(theme.DIM))
        pen.setWidthF(1.2)
        p.setPen(pen)
        for off in (4, 8, 12):
            p.drawLine(QtCore.QPointF(rect.right() - off, rect.bottom() - 2),
                      QtCore.QPointF(rect.right() - 2, rect.bottom() - off))
        return rect

    def _header_left_inset(self) -> float:
        return 18.0   # room for the remove dot before the title

    def _paint_header(self, p: QtGui.QPainter, rect: QtCore.QRectF,
                      head_h: float) -> None:
        super()._paint_header(p, rect, head_h)
        # A dim × tucked in a corner nobody looks at was too easy to miss.
        # A filled red dot beside the title is the same affordance every
        # tabbed browser and editor already trained the operator to read as
        # "close this".
        cx = rect.left() + self.INSET_X + 6
        cy = rect.top() + head_h / 2
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(theme.ALARM))
        p.drawEllipse(QtCore.QPointF(cx, cy), 5.5, 5.5)
        p.setBrush(QtCore.Qt.NoBrush)

    # ── axes: without these a small trace is just a shape, not a reading ────
    AXIS_LM = 46   # left margin reserved for Y-axis value labels
    AXIS_BM = 20   # bottom margin reserved for X-axis time labels

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self._paint_card(p)
        head_h = self._header_height()
        self._paint_header(p, rect, head_h)

        plot = QtCore.QRectF(
            rect.left() + self.AXIS_LM,
            rect.top() + head_h,
            rect.width() - self.AXIS_LM - self.INSET_X,
            rect.height() - head_h - self.AXIS_BM - 6,
        )
        if plot.height() <= 0 or plot.width() <= 0:
            p.end()
            return

        self._paint_grid(p, plot)

        if len(self.data) < 2:
            self._paint_empty(p, plot)
            p.end()
            return

        values = [v for _, v in self.data]
        min_val, max_val = min(values), max(values)
        if max_val == min_val:
            pad = abs(max_val) * 0.05 or 1.0
            min_val, max_val = min_val - pad, max_val + pad

        self._paint_y_axis(p, rect, plot, min_val, max_val)
        self._paint_x_axis(p, rect, plot)
        self._paint_trace(p, plot, min_val, max_val)
        p.end()

    @staticmethod
    def _axis_fmt(v: float) -> str:
        """A tick label has a fixed ~40px to live in — a raw gas-resistance
        reading in the hundreds of thousands cannot spell itself out there
        the way the header's full-precision reading can afford to."""
        a = abs(v)
        if a >= 100_000:
            return f"{v / 1000:.0f}k"
        if a >= 10_000:
            return f"{v / 1000:.1f}k"
        return _fmt(v)

    def _paint_y_axis(self, p: QtGui.QPainter, rect: QtCore.QRectF,
                      plot: QtCore.QRectF, min_val: float, max_val: float) -> None:
        p.setFont(self._f_micro)
        for i in range(3):
            frac = i / 2
            v = max_val - (max_val - min_val) * frac
            y = plot.top() + plot.height() * frac
            p.setPen(QtGui.QColor(theme.LINE))
            p.drawLine(QtCore.QPointF(plot.left() - 4, y), QtCore.QPointF(plot.left(), y))
            p.setPen(QtGui.QColor(theme.DIM))
            p.drawText(QtCore.QRectF(rect.left() + 2, y - 7, self.AXIS_LM - 6, 14),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, self._axis_fmt(v))
        p.setPen(QtGui.QPen(QtGui.QColor(theme.LINE), 1.0))
        p.drawLine(QtCore.QPointF(plot.left(), plot.top()),
                  QtCore.QPointF(plot.left(), plot.bottom()))

    def _paint_x_axis(self, p: QtGui.QPainter, rect: QtCore.QRectF,
                      plot: QtCore.QRectF) -> None:
        n = len(self.data)
        p.setFont(self._f_micro)
        p.setPen(QtGui.QPen(QtGui.QColor(theme.LINE), 1.0))
        p.drawLine(QtCore.QPointF(plot.left(), plot.bottom()),
                  QtCore.QPointF(plot.right(), plot.bottom()))
        # First tick left-aligned and the last right-aligned, rather than
        # all three centred on their tick — a centred first label spills
        # left into the Y-axis's own margin, and a centred last label
        # spills right into the resize grip's corner.
        ticks = ((0.0, 0, QtCore.Qt.AlignLeft),
                (0.5, (n - 1) // 2, QtCore.Qt.AlignHCenter),
                (1.0, n - 1, QtCore.Qt.AlignRight))
        for frac, idx, align in ticks:
            x = plot.left() + plot.width() * frac
            p.setPen(QtGui.QColor(theme.LINE))
            p.drawLine(QtCore.QPointF(x, plot.bottom()), QtCore.QPointF(x, plot.bottom() + 3))
            p.setPen(QtGui.QColor(theme.DIM))
            label_w = 70
            label_x = x - label_w / 2
            if align == QtCore.Qt.AlignLeft:
                label_x = x
            elif align == QtCore.Qt.AlignRight:
                label_x = x - label_w
            p.drawText(QtCore.QRectF(label_x, plot.bottom() + 5, label_w, 14),
                       align, self.data[idx][0])


class _FreeCanvas(QtWidgets.QWidget):
    """A plain widget with no layout — tiles are positioned by RTGWindow
    directly, in grid cells converted to this widget's own current size.

    Paints a faint dot at every snap point so the grid tiles line up
    against is something the operator can actually see, instead of a grid
    that's only inferred from where things happen to land."""

    resized = QtCore.pyqtSignal()
    DOT = QtGui.QColor(255, 255, 255, 40)

    def __init__(self, columns: int, row_h: int, parent=None):
        super().__init__(parent)
        self._columns = columns
        self._row_h = row_h

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(self.DOT)
        cell_w = max(self.width() / self._columns, 1)
        y = 0.0
        while y <= self.height() + 1:
            x = 0.0
            while x <= self.width() + 1:
                p.drawEllipse(QtCore.QPointF(x, y), 1.4, 1.4)
                x += cell_w
            y += self._row_h
        p.end()


class RTGWindow(QtWidgets.QMainWindow):
    """A blank, scrollable canvas of every sensor channel the operator
    chooses to place on it."""

    COLUMNS = 12
    ROW_H = 170

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RTG — Real-Time Graphs · APEX GCS")
        self.setStyleSheet(f"QMainWindow {{ background: {theme.DEEP}; }}")

        self.tiles: dict[str, RTGTile] = {}
        self._popups: list[GraphPopup] = []

        root = QtWidgets.QWidget()
        root.setObjectName("rtgRoot")
        root.setStyleSheet(f"QWidget#rtgRoot {{ background: {theme.DEEP}; }}")
        self.setCentralWidget(root)
        # Scaled once per instance so every self.ROW_H read below (grid math,
        # tile placement) agrees — class-level ROW_H stays the raw design
        # value so this still reads as "170px at 1920x1080" at a glance.
        self.ROW_H = theme.sc(RTGWindow.ROW_H)
        self._gap = theme.sc(theme.GAP)

        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(self._gap, self._gap, self._gap, self._gap)
        outer.setSpacing(self._gap)

        outer.addWidget(self._build_header())

        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._canvas = _FreeCanvas(self.COLUMNS, self.ROW_H)
        self._canvas.setObjectName("rtgCanvas")
        self._scroll.viewport().setAutoFillBackground(False)
        self._canvas.setAutoFillBackground(False)
        self._canvas.resized.connect(self._reflow)
        self._scroll.setWidget(self._canvas)
        outer.addWidget(self._scroll, 1)

        self._empty_hint = QtWidgets.QLabel(
            "Canvas is empty.\nUse “+ Add Graph” above to place a channel.",
            self._canvas)
        self._empty_hint.setAlignment(QtCore.Qt.AlignCenter)
        self._empty_hint.setStyleSheet(
            f"color: {theme.DIM}; font-size: {theme.sc_font(theme.FS_HEAD)}px; background: transparent;")

        self._load_layout()

        QtWidgets.QShortcut(QtGui.QKeySequence("Esc"), self, self.close)

    # ── chrome ────────────────────────────────────────────────────────────────
    def _build_header(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        bar.setObjectName("appBar")
        bar.setFixedHeight(theme.sc(52))
        row = QtWidgets.QHBoxLayout(bar)
        pad = theme.sc(theme.PAD)
        row.setContentsMargins(pad, 0, pad, 0)
        row.setSpacing(theme.sc(10))

        title = QtWidgets.QLabel("RTG")
        title.setObjectName("appBarMark")
        sub = QtWidgets.QLabel("REAL-TIME GRAPHS")
        sub.setObjectName("appBarSub")
        row.addWidget(title)
        row.addWidget(sub)

        # A QPushButton, not QToolButton — the app's #ghostBtn style only
        # ever targeted QPushButton, so the tool button fell through to the
        # platform's own white default with nothing themed about it at all.
        add_btn = QtWidgets.QPushButton("+ Add Graph")
        add_btn.setObjectName("ghostBtn")
        add_btn.setCursor(QtCore.Qt.PointingHandCursor)
        menu = QtWidgets.QMenu(add_btn)
        menu.aboutToShow.connect(lambda m=menu: self._fill_add_menu(m))
        add_btn.setMenu(menu)
        row.addWidget(add_btn)
        row.addStretch()

        hint = QtWidgets.QLabel(
            "Drag to move · drag a corner to resize · double-click to open in a window")
        hint.setObjectName("appBarSub")
        row.addWidget(hint)

        close = QtWidgets.QPushButton("Close")
        close.setObjectName("ghostBtn")
        close.setToolTip("Close RTG (Esc)")
        close.clicked.connect(self.close)
        row.addWidget(close)
        return bar

    def _fill_add_menu(self, menu: QtWidgets.QMenu):
        menu.clear()
        for section in SECTIONS:
            sub = menu.addMenu(section)
            for key, label, unit, sec, primary in CHANNELS:
                if sec != section:
                    continue
                act = sub.addAction(label)
                act.setEnabled(key not in self.tiles)
                act.triggered.connect(lambda _=False, k=key: self._add_tile(k))

    # ── tiles ─────────────────────────────────────────────────────────────────
    def _add_tile(self, key: str):
        if key in self.tiles:
            return
        label, unit, section, primary = CHANNEL_INFO[key]
        tile = self._make_tile(key, label, unit, section, primary)
        tile.col_span, tile.row_span = 4, 1
        tile.grid_row, tile.grid_col = self._free_cell(4, 1)
        self.tiles[key] = tile
        self._reflow()
        self._save_layout()

    def _make_tile(self, key, label, unit, section, primary) -> RTGTile:
        tile = RTGTile(label, unit, primary, accent=SECTION_ACCENT[section])
        # The tile moves/resizes itself freely in pixels while the mouse is
        # down (smooth, and costs nothing — see RTGTile's own comment); the
        # window only hears about it once, here, when the drag ends, and
        # that is also the only point it needs to check for and resolve an
        # overlap with whatever else is on the canvas.
        tile.interaction_finished.connect(self._on_tile_settled)
        tile.remove_requested.connect(self._remove_tile)
        tile.clicked.connect(self._pop_out)
        tile.setParent(self._canvas)
        tile.show()
        return tile

    def _remove_tile(self, tile: RTGTile):
        key = next((k for k, t in self.tiles.items() if t is tile), None)
        if key is None:
            return
        for popup in list(self._popups):
            if popup.source_graph is tile:
                popup.close()
        del self.tiles[key]
        tile.setParent(None)
        tile.deleteLater()
        self._reflow()
        self._save_layout()

    def _on_tile_settled(self, tile: RTGTile):
        """A drag or resize just ended — snap the tile's freehand pixel
        geometry to the nearest grid cell, and keep it there only if that
        does not land it on top of another tile (otherwise it goes back to
        where it started, same as it would overlapping anything on a real
        desktop)."""
        cell_w = max(self._canvas.width() / self.COLUMNS, 1)
        geo = tile.geometry()

        col_span = max(1, min(round((geo.width() + self._gap) / cell_w), self.COLUMNS))
        row_span = max(1, min(round((geo.height() + self._gap) / self.ROW_H),
                              RTGTile.MAX_ROW_SPAN))
        col = max(0, min(round(geo.x() / cell_w), self.COLUMNS - col_span))
        row = max(0, round(geo.y() / self.ROW_H))

        occ = self._occupied(exclude=tile)
        block = {(row + dr, col + dc)
                for dr in range(row_span) for dc in range(col_span)}
        if not block & occ:
            tile.grid_col, tile.grid_row = col, row
            tile.col_span, tile.row_span = col_span, row_span
        # else: leave the tile's last-committed placement untouched — the
        # reflow below snaps it straight back to it.

        self._reflow()
        self._save_layout()

    # ── grid math ─────────────────────────────────────────────────────────────
    def _occupied(self, exclude: RTGTile = None) -> set[tuple[int, int]]:
        occ: set[tuple[int, int]] = set()
        for tile in self.tiles.values():
            if tile is exclude:
                continue
            for dr in range(tile.row_span):
                for dc in range(tile.col_span):
                    occ.add((tile.grid_row + dr, tile.grid_col + dc))
        return occ

    def _free_cell(self, col_span: int, row_span: int,
                   exclude: RTGTile = None) -> tuple[int, int]:
        """First free (row, col) a block this size fits in, scanning top to
        bottom, left to right."""
        occ = self._occupied(exclude)
        r = 0
        while True:
            for c in range(self.COLUMNS - col_span + 1):
                block = {(r + dr, c + dc)
                         for dr in range(row_span) for dc in range(col_span)}
                if not block & occ:
                    return r, c
            r += 1

    def _reflow(self):
        """Convert every tile's grid cell into real geometry against the
        canvas's current width, and size the canvas to fit every row used."""
        self._empty_hint.setVisible(not self.tiles)
        if not self.tiles:
            self._canvas.setMinimumHeight(max(self._scroll.viewport().height(), 1))
            self._empty_hint.setGeometry(self._canvas.rect())
            return

        cell_w = max(self._canvas.width() / self.COLUMNS, 1)
        max_row = 0
        for tile in self.tiles.values():
            x = tile.grid_col * cell_w
            y = tile.grid_row * self.ROW_H
            w = max(tile.col_span * cell_w - self._gap, theme.sc(40))
            h = max(tile.row_span * self.ROW_H - self._gap, theme.sc(40))
            tile.setGeometry(int(x), int(y), int(w), int(h))
            max_row = max(max_row, tile.grid_row + tile.row_span)

        needed = max_row * self.ROW_H + self._gap
        self._canvas.setMinimumHeight(max(needed, self._scroll.viewport().height()))

    # ── pop-out ───────────────────────────────────────────────────────────────
    def _pop_out(self, tile: RTGTile):
        for existing in self._popups:
            if existing.source_graph is tile:
                existing.raise_()
                existing.activateWindow()
                return
        popup = GraphPopup(tile)
        popup.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        popup.destroyed.connect(
            lambda _=None, p=popup: self._popups.remove(p)
            if p in self._popups else None)
        popup.show()
        self._popups.append(popup)

    # ── persistence ───────────────────────────────────────────────────────────
    def _save_layout(self):
        settings.set_rtg_layout([
            {"key": key, "col": tile.grid_col, "row": tile.grid_row,
             "col_span": tile.col_span, "row_span": tile.row_span}
            for key, tile in self.tiles.items()
        ])

    def _load_layout(self):
        for entry in settings.get_rtg_layout():
            key = entry.get("key")
            info = CHANNEL_INFO.get(key)
            if info is None or key in self.tiles:
                continue
            label, unit, section, primary = info
            tile = self._make_tile(key, label, unit, section, primary)
            tile.col_span = max(1, min(int(entry.get("col_span", 4)), self.COLUMNS))
            tile.row_span = max(1, min(int(entry.get("row_span", 1)), RTGTile.MAX_ROW_SPAN))
            tile.grid_col = max(0, min(int(entry.get("col", 0)),
                                       self.COLUMNS - tile.col_span))
            tile.grid_row = max(0, int(entry.get("row", 0)))
            self.tiles[key] = tile
        self._reflow()

    # ── data ──────────────────────────────────────────────────────────────────
    def add_values(self, values: dict[str, float], timestamp: str):
        for key, value in values.items():
            tile = self.tiles.get(key)
            if tile is not None:
                tile.add_value(value, timestamp)

    def seed(self, history: dict[str, list[tuple[str, float]]]):
        """Fill the tiles with whatever was recorded before RTG was opened."""
        for key, data in history.items():
            tile = self.tiles.get(key)
            if tile is not None:
                tile.data = list(data)
                tile.update()

    def closeEvent(self, event):
        for popup in list(self._popups):
            popup.close()
        super().closeEvent(event)
