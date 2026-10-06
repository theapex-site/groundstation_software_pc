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


# ══════════════════════════════════════════════════════════════════════════════
#  MiniGraph
# ══════════════════════════════════════════════════════════════════════════════
class MiniGraph(QtWidgets.QWidget):
    """Compact real-time line graph displayed on the main dashboard."""

    clicked = QtCore.pyqtSignal(object)

    def __init__(self, title: str, unit: str = "", max_points: int = MINI_GRAPH_MAX_POINTS):
        super().__init__()
        self.setMinimumHeight(160)
        self.title = title
        self.unit  = unit
        self.max_points = max_points
        self.data: list[tuple[str, float]] = []   # [(timestamp, value), ...]

        self.setToolTip("Double-click to expand graph in a new window.")
        self.setToolTipDuration(2000)

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

    # ── painting ──────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = QtCore.QRectF(self.rect())

        # Background + border
        bg_path = QtGui.QPainterPath()
        bg_path.addRoundedRect(rect, theme.R_CARD, theme.R_CARD)
        painter.fillPath(bg_path, QtGui.QColor("#000000"))
        pen = QtGui.QPen(QtGui.QColor("#1a1a2e"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawPath(bg_path)
        painter.setClipPath(bg_path)

        # Title
        painter.setPen(QtGui.QColor("#5eead4"))
        painter.drawText(12, 20, self.title)

        if len(self.data) < 2:
            return

        w = rect.width()
        top_margin    = 30
        bottom_margin = 20
        h = rect.height() - top_margin - bottom_margin

        values    = [v for _, v in self.data]
        max_val   = max(values)
        min_val   = min(values)
        val_range = max_val - min_val or 1

        # Grid
        grid_pen = QtGui.QPen(QtGui.QColor("#1a1a2e"))
        grid_pen.setStyle(QtCore.Qt.DashLine)
        painter.setPen(grid_pen)
        for i in range(5):
            y = top_margin + (h / 4) * i
            painter.drawLine(0, int(y), int(w), int(y))

        # Min/Max label
        painter.setPen(QtGui.QColor("#94a3b8"))
        text     = f"Min: {round(min_val,2)}   Max: {round(max_val,2)}"
        metrics  = painter.fontMetrics()
        x_pos    = rect.width() - metrics.horizontalAdvance(text) - 10
        painter.drawText(int(x_pos), int(top_margin + h + 15), text)

        # Line
        line_pen = QtGui.QPen(QtGui.QColor("#2dd4bf"))
        line_pen.setWidth(2)
        painter.setPen(line_pen)
        step = (w - 5) / (len(self.data) - 1)
        for i in range(len(self.data) - 1):
            x1 = i * step
            y1 = top_margin + h - ((self.data[i][1]     - min_val) / val_range) * h
            x2 = (i + 1) * step
            y2 = top_margin + h - ((self.data[i + 1][1] - min_val) / val_range) * h
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))


# ══════════════════════════════════════════════════════════════════════════════
#  PopupGraph
# ══════════════════════════════════════════════════════════════════════════════
class PopupGraph(MiniGraph):
    """Expanded graph with Y/X axes, tick labels, hover inspection, and
    freeze-scale while the cursor is over a data point."""

    def __init__(self, title: str, unit: str = "", max_points: int = POPUP_GRAPH_MAX_POINTS):
        super().__init__(title, unit, max_points)
        self.setMouseTracking(True)
        self.frozen_min  = None
        self.frozen_max  = None
        self.freeze_scale = False
        self.hover_locked = False
        self.hover_index  = None
        self.hover_value  = None
        self.hover_time   = None

    # ── hover ─────────────────────────────────────────────────────────────────
    def mouseMoveEvent(self, event):
        if len(self.data) < 1:
            return

        left_margin = 60
        w = self.rect().width() - left_margin - 20
        step = w / (len(self.data) - 1) if len(self.data) > 1 else w

        t = (event.pos().x() - left_margin) / step
        i = round(t)

        if abs(t - i) > 0.5 or not (0 <= i < len(self.data)):
            self.hover_index  = None
            self.hover_value  = None
            self.hover_locked = False
            self.freeze_scale = False
            self.update()
            return

        self.hover_index  = i
        self.hover_value  = self.data[i][1]
        self.hover_time   = self.data[i][0]
        self.hover_locked = True

        values = [v for _, v in self.data]
        self.frozen_min  = min(values)
        self.frozen_max  = max(values)
        self.freeze_scale = True
        self.update()

    # ── painting ──────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = QtCore.QRectF(self.rect())

        # Background
        bg_path = QtGui.QPainterPath()
        bg_path.addRoundedRect(rect, theme.R_CARD, theme.R_CARD)
        painter.fillPath(bg_path, QtGui.QColor("#000000"))
        pen = QtGui.QPen(QtGui.QColor("#1a1a2e"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawPath(bg_path)
        painter.setClipPath(bg_path)

        painter.setPen(QtGui.QColor("#5eead4"))
        painter.drawText(12, 20, self.title)

        if len(self.data) < 2:
            return

        lm, rm, tm, bm = 60, 20, 30, 50
        w = rect.width()  - lm - rm
        h = rect.height() - tm - bm

        values = [v for _, v in self.data]
        if self.freeze_scale and self.frozen_min is not None:
            min_val, max_val = self.frozen_min, self.frozen_max
        else:
            min_val, max_val = min(values), max(values)
        val_range = max_val - min_val or 1

        # Axes
        axis_pen = QtGui.QPen(QtGui.QColor("#64748b"))
        axis_pen.setWidth(2)
        painter.setPen(axis_pen)
        painter.drawLine(lm, int(tm),     lm,       int(tm + h))
        painter.drawLine(lm, int(tm + h), int(lm + w), int(tm + h))

        # Y ticks
        painter.setPen(QtGui.QColor("#94a3b8"))
        for i in range(5):
            v = min_val + (val_range / 4) * i
            y = tm + h - (h / 4) * i
            painter.drawLine(lm - 5, int(y), lm, int(y))
            painter.drawText(5, int(y + 4), f"{round(v,1)}")

        # X ticks
        x_ticks = min(8, max(4, len(self.data) // 5))
        for i in range(x_ticks + 1):
            x   = lm + (w / x_ticks) * i
            idx = int((len(self.data) - 1) * i / x_ticks)
            painter.drawLine(int(x), int(tm + h), int(x), int(tm + h + 5))
            painter.drawText(int(x - 10), int(tm + h + 20), str(idx))

        # Axis labels
        painter.setPen(QtGui.QColor("#5eead4"))
        painter.drawText(int(lm + w / 2 - 30), int(tm + h + 40), "Time (samples)")
        painter.save()
        painter.translate(20, int(tm + h / 2 + 40))
        painter.rotate(-90)
        painter.drawText(0, 0, "Value")
        painter.restore()

        # Grid
        grid_pen = QtGui.QPen(QtGui.QColor("#334155"))
        grid_pen.setStyle(QtCore.Qt.DashLine)
        painter.setPen(grid_pen)
        for i in range(5):
            y = tm + (h / 4) * i
            painter.drawLine(lm, int(y), int(lm + w), int(y))

        # Line
        line_pen = QtGui.QPen(QtGui.QColor("#2dd4bf"))
        line_pen.setWidth(2)
        painter.setPen(line_pen)
        step = w / (len(self.data) - 1)
        for i in range(len(self.data) - 1):
            x1 = lm + i * step
            y1 = tm + h - ((self.data[i][1]     - min_val) / val_range) * h
            x2 = lm + (i + 1) * step
            y2 = tm + h - ((self.data[i + 1][1] - min_val) / val_range) * h
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Stats box
        painter.setPen(QtGui.QColor("#22c55e"))
        stats = [
            f"Current: {round(self.data[-1][1], 2)}",
            f"Min: {round(min_val, 2)}",
            f"Max: {round(max_val, 2)}",
        ]
        mh = painter.fontMetrics().height()
        for i, line in enumerate(stats):
            painter.drawText(int(lm + w - 160), int(tm + 20 + i * mh), line)

        # Hover marker + tooltip
        if self.hover_index is not None:
            x = lm + self.hover_index * step
            y = tm + h - ((self.hover_value - min_val) / val_range) * h

            mk_pen = QtGui.QPen(QtGui.QColor("#facc15"))
            mk_pen.setWidth(6)
            painter.setPen(mk_pen)
            painter.drawPoint(int(x), int(y))

            painter.setPen(QtGui.QColor("#f5f0e6"))
            painter.drawText(int(x + 12), int(y - 20), f"Time : {self.hover_time}")
            val_str = f"Value: {round(self.hover_value, 2)}"
            if self.unit:
                val_str += f" {self.unit}"
            painter.drawText(int(x + 12), int(y - 5), val_str)


# ══════════════════════════════════════════════════════════════════════════════
#  GraphPopup
# ══════════════════════════════════════════════════════════════════════════════
class GraphPopup(QtWidgets.QMainWindow):
    """Floating window that mirrors a MiniGraph with live updates."""

    _STYLE = """
        QMainWindow {
            background-color: #000000;
        }
        QStatusBar {
            background-color: #000000;
            color: #2dd4bf;
            font-family: Consolas;
            font-size: 11px;
        }
    """

    def __init__(self, source_graph: MiniGraph):
        super().__init__()
        self.setWindowTitle(f"📈  {source_graph.title}")
        self.resize(900, 450)
        self.setStyleSheet(self._STYLE)

        self.source_graph = source_graph
        self.graph = PopupGraph(source_graph.title, source_graph.unit)
        self.setCentralWidget(self.graph)

        # Status bar shows live current value
        self.statusBar().showMessage(f"{source_graph.title}  —  double-click graph to inspect values")

        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(self._sync)
        self._timer.start(POPUP_SYNC_MS)
        self._sync()

    def _sync(self):
        self.graph.data = self.source_graph.data.copy()
        if self.graph.data:
            current = self.graph.data[-1][1]
            self.statusBar().showMessage(
                f"{self.source_graph.title}  ·  "
                f"Current: {round(current, 2)} {self.source_graph.unit}  ·  "
                f"Points: {len(self.graph.data)}"
            )
        self.graph.update()
