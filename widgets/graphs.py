"""
widgets/graphs.py
─────────────────
MiniGraph   – compact inline graph shown on the main dashboard.
PopupGraph  – expanded version with axes, hover, and freeze-scale.
GraphPopup  – QMainWindow wrapper that hosts a PopupGraph and syncs it
              live with its source MiniGraph.

Double-click any MiniGraph to open its GraphPopup.
"""

from datetime import datetime
from PyQt5 import QtWidgets, QtCore, QtGui
import theme
from config import POPUP_GRAPH_MAX_POINTS, MINI_GRAPH_MAX_POINTS, POPUP_SYNC_MS


def _fmt(value: float) -> str:
    """Readable at a glance, without throwing away a small signal.

    Battery volts move in hundredths and altitude in metres; one fixed
    rounding made one of them look frozen and the other look noisy.
    """
    a = abs(value)
    if a >= 1000:
        return f"{value:,.0f}"
    if a >= 100:
        return f"{value:.1f}"
    if a >= 1:
        return f"{value:.2f}"
    return f"{value:.3f}"


def _font(family: str, px: int, weight=QtGui.QFont.Normal,
          tracking: float = 0.0) -> QtGui.QFont:
    f = QtGui.QFont(family)
    f.setPixelSize(px)
    f.setWeight(weight)
    if tracking:
        f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, tracking)
    return f


# ══════════════════════════════════════════════════════════════════════════════
#  MiniGraph
# ══════════════════════════════════════════════════════════════════════════════
class MiniGraph(QtWidgets.QWidget):
    """Compact real-time line graph displayed on the main dashboard.

    The reading itself is the point, so the latest value is drawn large in
    the header with its unit beside it; the trace behind it is context. The
    plot is inset from the card's rounded corners, because a line drawn to
    the full width gets sliced off by the corner radius at both ends.
    """

    clicked = QtCore.pyqtSignal(object)

    INSET_X = 12        # keeps the trace clear of the rounded corners
    FOOT_H  = 16        # min/max strip under the plot

    def __init__(self, title: str, unit: str = "", max_points: int = MINI_GRAPH_MAX_POINTS,
                 accent: str = None):
        super().__init__()
        self.setMinimumHeight(160)
        self.title = title
        self.unit  = unit
        self.max_points = max_points
        # The trace, fill, hover outline and dot all key off this one colour.
        # Every graph defaulted to the same teal regardless of what it was
        # plotting; RTG hands each section its own so sixteen tiles don't
        # read as one undifferentiated wall.
        self.accent = accent or theme.TEAL
        self.data: list[tuple[str, float]] = []   # [(timestamp, value), ...]

        self._f_label = _font(theme.MONO, theme.FS_LABEL,
                              QtGui.QFont.DemiBold, 1.4)
        self._f_value = _font(theme.MONO, theme.FS_VALUE, QtGui.QFont.Bold)
        self._f_unit  = _font(theme.MONO, theme.FS_LABEL)
        self._f_micro = _font(theme.MONO, theme.FS_MICRO)

        # Nothing used to say these were interactive: the only hint was a
        # tooltip the operator had to hover and wait for. A pointer cursor and
        # a lit outline say it before the tooltip arrives.
        self._hover = False
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Double-click to expand graph in a new window.")
        self.setToolTipDuration(theme.TIP_MS)

    # ── public API ────────────────────────────────────────────────────────────
    def add_value(self, value: float, timestamp: str = None):
        if timestamp is None:
            timestamp = datetime.now().strftime("%H:%M:%S")
        self.data.append((timestamp, value))
        if len(self.data) > self.max_points:
            self.data.pop(0)
        self.update()

    # ── events ────────────────────────────────────────────────────────────────
    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self)
        super().mouseDoubleClickEvent(event)

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    # ── painting helpers ──────────────────────────────────────────────────────
    def _header_height(self) -> int:
        return QtGui.QFontMetrics(self._f_value).height() + 16

    def _paint_card(self, p: QtGui.QPainter) -> QtCore.QRectF:
        """Fill, outline and return the card rect (half-pixel inset so the
        1px border lands on a pixel instead of straddling two)."""
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, theme.R_CARD, theme.R_CARD)
        p.fillPath(path, QtGui.QColor(theme.RAISED))
        # The border always carries this tile's own accent now, not just on
        # hover — a wall of identically neutral-grey-bordered cards gave the
        # colour coding nothing to land on until the cursor was already over
        # one of them.
        border = QtGui.QColor(self.accent)
        if not self._hover:
            border.setAlpha(110)
        pen = QtGui.QPen(border)
        pen.setWidthF(1.0)
        p.strokePath(path, pen)
        return rect

    def _header_left_inset(self) -> float:
        """Room to leave clear before the title text starts.

        Zero for a plain MiniGraph. A subclass that draws something of its
        own into the header's left edge (RTG's remove dot) overrides this
        so the title doesn't start underneath it.
        """
        return 0.0

    def _paint_header(self, p: QtGui.QPainter, rect: QtCore.QRectF,
                      head_h: float) -> None:
        """Title on the left, the live reading on the right."""
        left = rect.left() + self.INSET_X + self._header_left_inset()
        p.setFont(self._f_label)
        p.setPen(QtGui.QColor(theme.SLATE))
        p.drawText(QtCore.QRectF(left, rect.top(),
                                 rect.width(), head_h),
                   QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                   self.title.upper())

        if not self.data:
            return

        # The unit sits right-aligned against the card edge and the value
        # right-aligned against the unit, so the digits stay put as the
        # reading changes width.
        value  = _fmt(self.data[-1][1])
        unit   = f" {self.unit}" if self.unit else ""
        unit_w = QtGui.QFontMetrics(self._f_unit).horizontalAdvance(unit)
        right  = rect.right() - self.INSET_X

        if unit:
            p.setFont(self._f_unit)
            p.setPen(QtGui.QColor(theme.SLATE))
            p.drawText(QtCore.QRectF(right - unit_w, rect.top(), unit_w, head_h),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, unit)

        p.setFont(self._f_value)
        p.setPen(QtGui.QColor(theme.CREAM))
        p.drawText(QtCore.QRectF(rect.left(), rect.top(),
                                 right - unit_w - rect.left(), head_h),
                   QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, value)

    def _paint_trace(self, p: QtGui.QPainter, plot: QtCore.QRectF,
                     min_val: float, max_val: float,
                     dot: bool = True) -> None:
        """The line plus the soft fill under it, clipped to the plot only."""
        val_range = (max_val - min_val) or 1.0
        n = len(self.data)
        step = plot.width() / (n - 1)

        def y_of(v: float) -> float:
            return plot.bottom() - ((v - min_val) / val_range) * plot.height()

        pts = [QtCore.QPointF(plot.left() + i * step, y_of(self.data[i][1]))
               for i in range(n)]

        line = QtGui.QPainterPath()
        line.moveTo(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)

        area = QtGui.QPainterPath(line)
        area.lineTo(pts[-1].x(), plot.bottom())
        area.lineTo(pts[0].x(),  plot.bottom())
        area.closeSubpath()

        accent = QtGui.QColor(self.accent)
        grad = QtGui.QLinearGradient(0, plot.top(), 0, plot.bottom())
        grad.setColorAt(0.0, QtGui.QColor(accent.red(), accent.green(), accent.blue(), 56))
        grad.setColorAt(1.0, QtGui.QColor(accent.red(), accent.green(), accent.blue(), 0))

        p.save()
        p.setClipRect(plot)
        p.fillPath(area, QtGui.QBrush(grad))
        pen = QtGui.QPen(accent)
        pen.setWidthF(1.8)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(pen)
        p.drawPath(line)
        p.restore()

        if dot:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(accent)
            p.drawEllipse(pts[-1], 3.0, 3.0)
            p.setBrush(QtCore.Qt.NoBrush)

    @staticmethod
    def _paint_grid(p: QtGui.QPainter, plot: QtCore.QRectF,
                    rows: int = 4) -> None:
        pen = QtGui.QPen(QtGui.QColor(theme.LINE))
        pen.setStyle(QtCore.Qt.DashLine)
        pen.setWidthF(1.0)
        p.setPen(pen)
        for i in range(rows + 1):
            y = plot.top() + (plot.height() / rows) * i
            p.drawLine(QtCore.QPointF(plot.left(), y),
                       QtCore.QPointF(plot.right(), y))

    def _paint_empty(self, p: QtGui.QPainter, plot: QtCore.QRectF) -> None:
        p.setFont(self._f_micro)
        p.setPen(QtGui.QColor(theme.DIM))
        p.drawText(plot, QtCore.Qt.AlignCenter, "awaiting telemetry")

    def _footer_insets(self) -> tuple[float, float]:
        """Room to leave clear at the left/right ends of the footer row.

        Zero for a plain MiniGraph, whose corners have nothing else in
        them. A subclass that draws its own controls into the card's
        corners (RTG's remove/resize handles) overrides this so the footer
        text doesn't grow straight into them — aligned text has no natural
        margin the way a short right-aligned value does.
        """
        return 0.0, 0.0

    def _paint_footer(self, p: QtGui.QPainter, plot: QtCore.QRectF,
                      values: list[float]) -> None:
        """The span the trace covers, and the window of time it took."""
        left_pad, right_pad = self._footer_insets()
        p.setFont(self._f_micro)
        p.setPen(QtGui.QColor(theme.DIM))
        foot = QtCore.QRectF(plot.left() + left_pad, plot.bottom() + 4,
                             plot.width() - left_pad - right_pad, self.FOOT_H)
        p.drawText(foot, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                   f"{self.data[0][0]} → {self.data[-1][0]}")
        p.drawText(foot, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight,
                   f"{_fmt(min(values))} / {_fmt(max(values))}")

    # ── painting ──────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        rect   = self._paint_card(p)
        head_h = self._header_height()
        self._paint_header(p, rect, head_h)

        plot = QtCore.QRectF(
            rect.left()  + self.INSET_X,
            rect.top()   + head_h,
            rect.width() - self.INSET_X * 2,
            rect.height() - head_h - self.FOOT_H - 8,
        )
        if plot.height() <= 0 or plot.width() <= 0:
            p.end()
            return

        self._paint_grid(p, plot)

        if len(self.data) < 2:
            self._paint_empty(p, plot)
            p.end()
            return

        values  = [v for _, v in self.data]
        min_val, max_val = min(values), max(values)
        if max_val == min_val:
            # A dead-flat signal otherwise divides by its own zero range and
            # pins the trace to the bottom edge; give it room and centre it.
            pad = abs(max_val) * 0.05 or 1.0
            min_val, max_val = min_val - pad, max_val + pad

        self._paint_trace(p, plot, min_val, max_val)
        self._paint_footer(p, plot, values)
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  PopupGraph
# ══════════════════════════════════════════════════════════════════════════════
class PopupGraph(MiniGraph):
    """Expanded graph with Y/X axes, tick labels, hover inspection, and
    freeze-scale while the cursor is over a data point.

    The scale freezes on hover so the point under the cursor does not slide
    away as new samples arrive — and it has to let go again, which the
    earlier version never did: it matched the nearest index by rounding, so
    every pixel of the plot counted as a hover and the axis stayed frozen
    for the rest of the window's life.
    """

    LM, RM, BM = 64, 24, 52     # left / right / bottom margins
    SNAP_PX    = 36             # how close the cursor must be to grab a point

    def __init__(self, title: str, unit: str = "", max_points: int = POPUP_GRAPH_MAX_POINTS,
                 accent: str = None):
        super().__init__(title, unit, max_points, accent)
        self.setMouseTracking(True)
        self.frozen_min  = None
        self.frozen_max  = None
        self.freeze_scale = False
        self.hover_locked = False
        self.hover_index  = None
        self.hover_value  = None
        self.hover_time   = None
        self._f_tick = _font(theme.MONO, theme.FS_MICRO)
        self._f_axis = _font(theme.BODY, theme.FS_LABEL, QtGui.QFont.DemiBold, 1.2)

        # The mini graph's pointer cursor means "double-click to expand". This
        # one is already expanded; the gesture here is reading a sample off
        # the trace, so it gets a crosshair and no lit outline.
        self.setCursor(QtCore.Qt.CrossCursor)
        self.setToolTip("")

    def enterEvent(self, event):
        QtWidgets.QWidget.enterEvent(self, event)

    # ── geometry shared by hover and paint ────────────────────────────────────
    def _plot_rect(self) -> QtCore.QRectF:
        rect   = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        head_h = self._header_height()
        return QtCore.QRectF(
            rect.left() + self.LM,
            rect.top()  + head_h,
            max(rect.width() - self.LM - self.RM, 1.0),
            max(rect.height() - head_h - self.BM, 1.0),
        )

    def _scale(self) -> tuple[float, float]:
        values = [v for _, v in self.data]
        if self.freeze_scale and self.frozen_min is not None:
            min_val, max_val = self.frozen_min, self.frozen_max
        else:
            min_val, max_val = min(values), max(values)
        if max_val == min_val:
            pad = abs(max_val) * 0.05 or 1.0
            min_val, max_val = min_val - pad, max_val + pad
        return min_val, max_val

    # ── hover ─────────────────────────────────────────────────────────────────
    def _clear_hover(self):
        if self.hover_index is None and not self.freeze_scale:
            return
        self.hover_index  = None
        self.hover_value  = None
        self.hover_time   = None
        self.hover_locked = False
        self.freeze_scale = False
        self.update()

    def mouseMoveEvent(self, event):
        if len(self.data) < 2:
            self._clear_hover()
            return

        plot = self._plot_rect()
        step = plot.width() / (len(self.data) - 1)
        i    = int(round((event.pos().x() - plot.left()) / step))
        i    = max(0, min(i, len(self.data) - 1))

        # Distance in pixels, not in indexes: with a few hundred samples the
        # index gap is well under a pixel, so rounding alone always "hits".
        near_x = abs(plot.left() + i * step - event.pos().x()) <= self.SNAP_PX
        if not (near_x and plot.adjusted(-8, -8, 8, 8).contains(
                QtCore.QPointF(event.pos()))):
            self._clear_hover()
            return

        self.hover_index  = i
        self.hover_value  = self.data[i][1]
        self.hover_time   = self.data[i][0]
        self.hover_locked = True

        if not self.freeze_scale:
            values = [v for _, v in self.data]
            self.frozen_min, self.frozen_max = min(values), max(values)
            self.freeze_scale = True
        self.update()

    def leaveEvent(self, event):
        self._clear_hover()
        super().leaveEvent(event)

    # ── painting ──────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self._paint_card(p)
        self._paint_header(p, rect, self._header_height())

        plot = self._plot_rect()
        if len(self.data) < 2:
            self._paint_grid(p, plot)
            self._paint_empty(p, plot)
            p.end()
            return

        min_val, max_val = self._scale()
        val_range = max_val - min_val

        self._paint_grid(p, plot)

        # Axes
        axis_pen = QtGui.QPen(QtGui.QColor(theme.LINE))
        axis_pen.setWidthF(1.0)
        p.setPen(axis_pen)
        p.drawLine(QtCore.QPointF(plot.left(), plot.top()),
                   QtCore.QPointF(plot.left(), plot.bottom()))
        p.drawLine(QtCore.QPointF(plot.left(), plot.bottom()),
                   QtCore.QPointF(plot.right(), plot.bottom()))

        # Y ticks
        p.setFont(self._f_tick)
        for i in range(5):
            v = min_val + (val_range / 4) * i
            y = plot.bottom() - (plot.height() / 4) * i
            p.setPen(QtGui.QColor(theme.LINE))
            p.drawLine(QtCore.QPointF(plot.left() - 4, y),
                       QtCore.QPointF(plot.left(), y))
            p.setPen(QtGui.QColor(theme.SLATE))
            p.drawText(QtCore.QRectF(rect.left() + 6, y - 8,
                                     self.LM - 12, 16),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, _fmt(v))

        # X ticks — the sample's own clock time. Labelling these with the
        # array index told the operator nothing: the timestamp was already
        # in the data and simply never used.
        n = len(self.data)
        fm_tick = QtGui.QFontMetrics(self._f_tick)
        label_w = fm_tick.horizontalAdvance("00:00:00") + 18
        x_ticks = max(1, min(8, int(plot.width() // label_w)))
        step = plot.width() / (n - 1)
        for i in range(x_ticks + 1):
            frac = i / x_ticks
            x    = plot.left() + plot.width() * frac
            idx  = int((n - 1) * frac)
            p.setPen(QtGui.QColor(theme.LINE))
            p.drawLine(QtCore.QPointF(x, plot.bottom()),
                       QtCore.QPointF(x, plot.bottom() + 4))
            p.setPen(QtGui.QColor(theme.SLATE))
            p.drawText(QtCore.QRectF(x - label_w / 2, plot.bottom() + 6,
                                     label_w, 14),
                       QtCore.Qt.AlignCenter, self.data[idx][0])

        # Axis captions
        p.setFont(self._f_axis)
        p.setPen(QtGui.QColor(theme.DIM))
        p.drawText(QtCore.QRectF(plot.left(), plot.bottom() + 22,
                                 plot.width(), 16),
                   QtCore.Qt.AlignCenter, "TIME")
        p.save()
        p.translate(rect.left() + 16, plot.center().y())
        p.rotate(-90)
        p.drawText(QtCore.QRectF(-60, -8, 120, 16),
                   QtCore.Qt.AlignCenter,
                   (self.unit or "VALUE").upper())
        p.restore()

        self._paint_trace(p, plot, min_val, max_val,
                          dot=self.hover_index is None)

        # Hover marker + readout
        if self.hover_index is not None:
            x = plot.left() + self.hover_index * step
            y = plot.bottom() - ((self.hover_value - min_val) / (val_range or 1)) * plot.height()

            guide = QtGui.QPen(QtGui.QColor(theme.DIM))
            guide.setStyle(QtCore.Qt.DashLine)
            p.setPen(guide)
            p.drawLine(QtCore.QPointF(x, plot.top()),
                       QtCore.QPointF(x, plot.bottom()))

            p.setPen(QtGui.QPen(QtGui.QColor(theme.DEEP), 2.0))
            p.setBrush(QtGui.QColor(theme.WARN))
            p.drawEllipse(QtCore.QPointF(x, y), 4.5, 4.5)
            p.setBrush(QtCore.Qt.NoBrush)

            val_str = _fmt(self.hover_value) + (f" {self.unit}" if self.unit else "")
            lines   = [self.hover_time, val_str]
            fm      = QtGui.QFontMetrics(self._f_tick)
            box_w   = max(fm.horizontalAdvance(s) for s in lines) + 16
            box_h   = fm.height() * 2 + 12
            bx = min(x + 14, plot.right() - box_w)
            by = max(y - box_h - 10, plot.top())

            tip = QtGui.QPainterPath()
            tip.addRoundedRect(QtCore.QRectF(bx, by, box_w, box_h),
                               theme.R_SM, theme.R_SM)
            p.fillPath(tip, QtGui.QColor(8, 8, 15, 235))
            p.strokePath(tip, QtGui.QPen(QtGui.QColor(theme.LINE), 1.0))

            p.setFont(self._f_tick)
            p.setPen(QtGui.QColor(theme.SLATE))
            p.drawText(QtCore.QRectF(bx + 8, by + 6, box_w - 16, fm.height()),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, lines[0])
            p.setPen(QtGui.QColor(theme.CREAM))
            p.drawText(QtCore.QRectF(bx + 8, by + 6 + fm.height(),
                                     box_w - 16, fm.height()),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, lines[1])
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  GraphPopup
# ══════════════════════════════════════════════════════════════════════════════
class GraphPopup(QtWidgets.QMainWindow):
    """Floating window that mirrors a MiniGraph with live updates."""

    def __init__(self, source_graph: MiniGraph):
        super().__init__()
        self.setWindowTitle(f"{source_graph.title} — APEX GCS")
        self.resize(900, 450)
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {theme.PANEL}; }}
            QStatusBar  {{
                background-color: {theme.PANEL};
                color: {theme.SLATE};
                font-family: "{theme.MONO}";
                font-size: {theme.FS_MICRO}px;
                border-top: 1px solid {theme.LINE};
            }}
            QStatusBar::item {{ border: 0; }}
        """)

        self.source_graph = source_graph
        self.graph = PopupGraph(source_graph.title, source_graph.unit,
                                accent=getattr(source_graph, "accent", None))
        self.setCentralWidget(self.graph)
        self.setContentsMargins(theme.GAP, theme.GAP, theme.GAP, 0)

        self.statusBar().showMessage("hover the trace to inspect a sample")

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._sync)
        self._timer.start(POPUP_SYNC_MS)
        self._sync()

    def _sync(self):
        self.graph.data = self.source_graph.data.copy()
        if self.graph.data:
            unit = f" {self.source_graph.unit}" if self.source_graph.unit else ""
            self.statusBar().showMessage(
                f"current {_fmt(self.graph.data[-1][1])}{unit}"
                f"   ·   {len(self.graph.data)} samples"
                f"   ·   hover the trace to inspect a sample"
            )
        self.graph.update()

    def closeEvent(self, event):
        # The timer kept firing against a hidden window and repainting it
        # forever once the operator closed the popup.
        self._timer.stop()
        super().closeEvent(event)
