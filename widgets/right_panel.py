"""
widgets/right_panel.py
──────────────────────
CubeSat3D   – OpenGL widget that renders the 3-D CubeSat model with
              live pitch / roll / yaw rotation.

RightPanel  – right-side panel containing:
              • CubeSat orientation viewer (3D + numeric labels)
              • Raw gas sensor values
              • Control-panel buttons (with confirmation for dangerous cmds)
              • Embedded Google Maps view
"""

import math
import os
import sys
import time
from datetime import datetime

import numpy as np
import trimesh
from PIL import Image as PILImage
from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtOpenGL import QGLWidget
from OpenGL.GL import *
from OpenGL.GLU import *

import theme
from config import DEFAULT_LAT, DEFAULT_LON, DATA_ROOT
from widgets.dialogs import ThemedMessageBox
from widgets.object_detection import ObjectDetectionDialog

if getattr(sys, 'frozen', False):
    BASE_PATH = sys._MEIPASS
else:
    BASE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# How far the CubeSat must move, and how long must pass, before the embedded
# map is rebuilt. 0.0005° is roughly 55 m at the equator — below the zoom
# level the map is shown at, so a smaller step would not be visible anyway.
MAP_MOVE_MIN_DEG = 0.0005
MAP_RELOAD_MIN_S = 15.0

class _ClickCatcher(QtWidgets.QWidget):
    """A transparent pane whose only job is to see a double-click."""

    doubleClicked = QtCore.pyqtSignal()

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)


class _MapView(QtWidgets.QWidget):
    """
    The embedded map.

    A fully transparent overlay stretched across this widget used to exist
    purely so a double-click could be caught, and was removed because it
    swallowed every mouse event — the map could not be panned, zoomed or
    scrolled at all, while its tooltip still claimed it was interactive.

    It is back, because double-click-to-open-in-browser replaces the
    explicit button that did the same thing. A QWebEngineView consumes
    mouse input itself and never lets a parent's mouseDoubleClickEvent see
    it, so there is no way to catch the gesture without something in front
    of the frame — which means the same trade-off applies again: panning,
    zooming and scrolling the embedded frame no longer work. The tooltip
    says so now instead of promising otherwise.
    """

    doubleClicked = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__()
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self._web = QWebEngineView(self)
        self._web.setStyleSheet("background: transparent;")
        self._layout.addWidget(self._web)

        self._overlay = _ClickCatcher(self)
        self._overlay.setCursor(QtCore.Qt.PointingHandCursor)
        self._overlay.doubleClicked.connect(self.doubleClicked)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._overlay.setGeometry(self.rect())
        self._overlay.raise_()

    def setHtml(self, html):
        self._web.setHtml(html)

    def setToolTip(self, text):
        self._overlay.setToolTip(text)

    def setToolTipDuration(self, ms):
        self._overlay.setToolTipDuration(ms)
# ══════════════════════════════════════════════════════════════════════════════
#  CubeSat3D
# ══════════════════════════════════════════════════════════════════════════════
class CubeSat3D(QGLWidget):
    """Renders the CubeSat GLB model with real-time orientation updates."""

    def __init__(self):
        super().__init__()
        self.pitch = self.roll = self.yaw = 0.0

        model_path = os.path.join(BASE_PATH, "models", "APEX_CubeSat.glb")
        model = trimesh.load(model_path)

        # scene.geometry.values() hands back each part in its own local mesh
        # space, ignoring the scene graph's per-node transforms. dump()
        # bakes those in, which is required here: the body, solar panels,
        # antenna and the two textured decals (logo plate, solar sheen) are
        # separate parts placed by those transforms, each with its own
        # material — they are kept separate rather than concatenated so the
        # textured ones can be drawn with their own image bound and the
        # untextured ones with their own flat material colour.
        raw_parts = model.dump() if isinstance(model, trimesh.Scene) else [model]

        # One centre/scale for the whole assembly, not per part — a part as
        # small as the antenna would otherwise be blown up to fill the same
        # 1.3-unit box on its own instead of sitting at its true relative
        # size and position.
        all_bounds = np.array([p.bounds for p in raw_parts])
        center = np.vstack([all_bounds[:, 0, :].min(axis=0),
                             all_bounds[:, 1, :].max(axis=0)]).mean(axis=0)
        extents = all_bounds[:, 1, :].max(axis=0) - all_bounds[:, 0, :].min(axis=0)
        scale = 1.3 / max(extents)

        # Each entry: vertex/normal/index arrays for glDrawElements, plus
        # either a texture (uv + PIL image, resolved to a GL texture id in
        # initializeGL, which is the first point a GL context is current)
        # or a flat material colour for parts with no image.
        #
        # Not a single per-vertex Python loop over all this, nor trimesh
        # properties read live inside one (mesh.vertices[v],
        # mesh.face_normals[i]) — that was the old approach, and at a face
        # count in the tens of thousands (vs. a few hundred on the model
        # this replaced) it took minutes per frame instead of milliseconds.
        self._parts = []
        for p in raw_parts:
            verts = (p.vertices - center) * scale
            part = {
                "vertices": np.ascontiguousarray(verts, dtype=np.float32),
                "normals":  np.ascontiguousarray(p.vertex_normals, dtype=np.float32),
                "indices":  np.ascontiguousarray(p.faces, dtype=np.uint32).reshape(-1),
                "uv": None,
                "image": None,
                "texture_id": None,
                "color": (0.8, 0.8, 0.8),
            }
            material = getattr(p.visual, "material", None)
            if material is not None:
                base = getattr(material, "baseColorFactor", None)
                if base is not None:
                    part["color"] = tuple(float(c) / 255.0 for c in base[:3])
                image = getattr(material, "baseColorTexture", None)
                uv = getattr(p.visual, "uv", None)
                if image is not None and uv is not None:
                    # glTF UV origin is the image's top-left corner;
                    # OpenGL's texture sampling origin is bottom-left, so
                    # the image is flipped once here rather than flipping V
                    # per-vertex on every draw.
                    part["image"] = image.convert("RGB").transpose(PILImage.FLIP_TOP_BOTTOM)
                    part["uv"] = np.ascontiguousarray(uv, dtype=np.float32)
            self._parts.append(part)

    def update_orientation(self, pitch: float, roll: float, yaw: float):
        self.pitch, self.roll, self.yaw = pitch, roll, yaw
        self.update()

    def resizeEvent(self, event):
        # A GL viewport is a hard rectangle; every other surface in the app
        # takes a radius token, so mask it to the same curve.
        super().resizeEvent(event)
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(self.rect()),
                            theme.sc(theme.R_CTRL), theme.sc(theme.R_CTRL))
        rounded = QtGui.QRegion(path.toFillPolygon().toPolygon())
        if not rounded.isEmpty():
            self.setMask(rounded)

    # ── OpenGL overrides ──────────────────────────────────────────────────────
    def initializeGL(self):
        # Without this the buffer clears to GL's default black, which sat as a
        # visibly darker rectangle inside a {RAISED} card. Match the card.
        c = QtGui.QColor(theme.RAISED)
        glClearColor(c.redF(), c.greenF(), c.blueF(), 1.0)
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glEnable(GL_COLOR_MATERIAL)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
        glEnable(GL_NORMALIZE)
        glShadeModel(GL_SMOOTH)
        glLightfv(GL_LIGHT0, GL_POSITION, [5, 5, 5, 1])
        glLightfv(GL_LIGHT0, GL_DIFFUSE,  [1, 1, 1, 1])

        # Texture upload needs a current GL context, which only exists from
        # here on — __init__ just collected the decoded PIL images. The
        # image itself is dropped once uploaded; only the GL texture id is
        # needed to draw.
        for part in self._parts:
            image = part.pop("image")
            if image is None:
                continue
            tex_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, tex_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, image.width, image.height,
                         0, GL_RGB, GL_UNSIGNED_BYTE, image.tobytes())
            part["texture_id"] = tex_id

    def resizeGL(self, w, h):
        glViewport(0, 0, w, h)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(45, (w / h) if h else 1, 0.1, 100)
        glMatrixMode(GL_MODELVIEW)

    def paintGL(self):
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()
        glTranslatef(0, 0, -4)
        glRotatef(self.yaw,   0, 0, 1)
        glRotatef(self.pitch, 0, 1, 0)
        glRotatef(self.roll,  1, 0, 0)
        self._draw_axes()
        self._draw_model()

    def _draw_axes(self, length: float = 1.0):
        glLineWidth(2.0)
        glBegin(GL_LINES)
        glColor3f(1, 0, 0); glVertex3f(0,0,0); glVertex3f(length,0,0)  # X red
        glColor3f(0, 1, 0); glVertex3f(0,0,0); glVertex3f(0,length,0)  # Y green
        glColor3f(0, 0, 1); glVertex3f(0,0,0); glVertex3f(0,0,length)  # Z blue
        glEnd()

    def _draw_model(self):
        # Vertex arrays per part, not a per-vertex glBegin/glVertex3f Python
        # loop: see the note in __init__ — at this model's face count the
        # old loop's per-element property access took over a second per
        # frame. Each part is drawn separately since they carry different
        # materials: the two with an actual texture (logo plate, solar
        # panel sheen) are drawn textured and white so the image shows at
        # full brightness under scene lighting; the rest have no image, so
        # they draw as a flat GL_COLOR_MATERIAL colour from the glTF
        # material instead.
        glEnableClientState(GL_VERTEX_ARRAY)
        glEnableClientState(GL_NORMAL_ARRAY)
        for part in self._parts:
            glVertexPointer(3, GL_FLOAT, 0, part["vertices"])
            glNormalPointer(GL_FLOAT, 0, part["normals"])
            if part["texture_id"] is not None:
                glEnable(GL_TEXTURE_2D)
                glBindTexture(GL_TEXTURE_2D, part["texture_id"])
                glColor3f(1.0, 1.0, 1.0)
                glEnableClientState(GL_TEXTURE_COORD_ARRAY)
                glTexCoordPointer(2, GL_FLOAT, 0, part["uv"])
            else:
                glDisable(GL_TEXTURE_2D)
                glColor3f(*part["color"])
            glDrawElements(GL_TRIANGLES, len(part["indices"]), GL_UNSIGNED_INT, part["indices"])
            if part["texture_id"] is not None:
                glDisableClientState(GL_TEXTURE_COORD_ARRAY)
        glDisableClientState(GL_VERTEX_ARRAY)
        glDisableClientState(GL_NORMAL_ARRAY)


# ══════════════════════════════════════════════════════════════════════════════
#  RightPanel
# ══════════════════════════════════════════════════════════════════════════════
class RightPanel(QtWidgets.QFrame):
    """Right-side panel with 3-D viewer, gas sensor, controls, and map."""

    command_signal = QtCore.pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        # theme.qss() styles "#TelemetryPanel, #RightPanel", and an #id
        # selector matches on objectName — which this never set, so the rule
        # only ever reached the left panel. That is why the left side had a
        # panel surface and the right side's cards floated bare on the sky.
        self.setObjectName("RightPanel")
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # The same QScrollArea treatment the telemetry panel got in H-03.
        # Grouping the commands added three captions and two dividers, which
        # pushed this past the window height; with nowhere to go, Qt squeezed
        # every button to 21px against 18px of text and 18px of padding, so
        # the labels rendered as a one-pixel smear. Content taller than the
        # panel has to scroll, not compress.
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        content = QtWidgets.QWidget()
        # Both the viewport and the content widget default to the palette's
        # light window colour; both have to stay transparent.
        scroll.viewport().setAutoFillBackground(False)
        content.setAutoFillBackground(False)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        layout = QtWidgets.QVBoxLayout(content)
        self._layout = layout

        def group(name: str, icon_name: str = "") -> theme.TabCard:
            g = theme.TabCard(name, icon_name=icon_name)
            grid = QtWidgets.QGridLayout()
            # Room for the heading, which is painted as part of the outline.
            grid.setContentsMargins(theme.sc(12), g.tab_height() + theme.sc(6), theme.sc(12), theme.sc(10))
            g.setLayout(grid)
            return g

        # ── CubeSat Orientation ───────────────────────────────────────────────
        imu = group("CUBESAT ORIENTATION", "compass")
        self.cubesat_3d = CubeSat3D()
        # Taller now that the gas card below it is gone — the model was
        # letterboxed into 170px while a one-row card sat underneath.
        #
        # No maximum originally: inside a setWidgetResizable(True) scroll
        # area, an Expanding child doesn't just grow to fill its own card —
        # it grows to soak up however much taller the viewport is than the
        # rest of the content needs, which scales with the screen. On a
        # tall enough window that pushed the Control Panel's buttons below
        # the fold, reachable only by scrolling, which on a genuinely big
        # monitor meant the whole card list no longer fit without scrolling
        # at all. Capped so it still grows on a bigger screen (nice to
        # have) but never past the point of crowding out the controls
        # below it.
        self.cubesat_3d.setMinimumHeight(theme.sc(200))
        self.cubesat_3d.setMaximumHeight(theme.sc(420))
        self.cubesat_3d.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                      QtWidgets.QSizePolicy.Expanding)
        imu.layout().addWidget(self.cubesat_3d, 0, 0, 1, 2)
        imu.layout().setRowStretch(0, 1)

        self.imu_labels: dict[str, QtWidgets.QLabel] = {}
        for i, (axis, unit) in enumerate([("Pitch", "°"), ("Roll", "°"), ("Yaw", "°")], start=1):
            lbl = QtWidgets.QLabel(axis)
            lbl.setObjectName("nameLabel")
            val = QtWidgets.QLabel("--")
            val.setObjectName("valueLabel")
            val.setAlignment(QtCore.Qt.AlignRight)
            val.unit = unit
            imu.layout().addWidget(lbl, i, 0)
            imu.layout().addWidget(val, i, 1)
            self.imu_labels[axis] = val
        layout.addWidget(imu)

        # The Raw Gas Sensor card used to sit here: one row showing a raw
        # resistance in ohms, which is an input to the air-quality maths
        # rather than something an operator acts on. The value still arrives
        # in every packet, still drives AQI / eCO2 / VOC, and is still
        # graphable in RTG — it just no longer takes a card on the dashboard.

        # ── Control Panel ─────────────────────────────────────────────────────
        control = group("CONTROL PANEL")
        # Grouped by what the action acts on, because a flat stack of eight
        # identical buttons put "Restart System" — which reboots the
        # spacecraft — in the same shape and weight as "Capture Image".
        #
        # Each row is (id, label, icon, command, shortcut, tooltip, kind).
        # The id is what the handler keys on: two buttons used to be wired by
        # comparing their own caption, so editing a label silently unwired it.
        self.command_buttons: dict[str, QtWidgets.QPushButton] = {}
        # Each button's resting state, so a temporary override (Transmit
        # Image becoming Cancel mid-downlink) has something to restore.
        self._button_defaults: dict[str, dict] = {}
        sections = [
            ("PAYLOAD", [
                ("capture",  "Capture Image",          "camera",    "C\n", "Ctrl+Shift+C",
                 "Command CubeSat camera to capture a new image.", "normal"),
                ("tx_image", "Transmit Image",         "upload",    "S\n", "Ctrl+Shift+I",
                 "Downlink the stored payload image. Telemetry pauses during the transfer.", "normal"),
                ("detect",   "Start Object Detection", "scan",      None,  "Ctrl+Shift+D",
                 "Run object detection on a downlinked image. Sends nothing to the CubeSat.", "normal"),
            ]),
            ("COMMS", [
                ("telemetry", "Send Telemetry",     "antenna",   "T\n", "Ctrl+Shift+T",
                 "Request a single telemetry packet now.", "normal"),
                ("tx_cw",     "Transmit CW Signal", "broadcast", "B\n", "Ctrl+Shift+B",
                 "Transmit the Morse CW beacon.", "normal"),
                ("reconnect", "Reconnect Serial",   "plug",      "A\n", "Ctrl+Shift+R",
                 "Drop the serial port and open it again.", "normal"),
            ]),
            ("SYSTEM", [
                ("save",    "Save Mission",   "save",  None,  "Ctrl+S",
                 "Write the mission logs to disk.", "normal"),
                ("restart", "Restart System", "power", "R\n", None,
                 "Reboot the CubeSat's onboard computer. Asks for confirmation.", "danger"),
            ]),
        ]

        row = 0
        for section, items in sections:
            if row:
                control.layout().addWidget(self._divider(), row, 0, 1, 2)
                row += 1
            cap = QtWidgets.QLabel(section)
            cap.setObjectName("groupCaption")
            control.layout().addWidget(cap, row, 0, 1, 2)
            row += 1

            for btn_id, text, icon_name, command, shortcut, tip, kind in items:
                btn = QtWidgets.QPushButton(text)
                tint = theme.ALARM if kind == "danger" else theme.SLATE
                icon_px = theme.sc(16)
                btn.setIcon(theme.icon(icon_name, tint, icon_px))
                btn.setIconSize(QtCore.QSize(icon_px, icon_px))
                if kind == "danger":
                    btn.setObjectName("dangerBtn")
                if shortcut:
                    btn.setShortcut(QtGui.QKeySequence(shortcut))
                    tip = f"{tip}\nShortcut: {shortcut}"
                btn.setToolTip(tip)
                btn.setToolTipDuration(theme.TIP_MS)
                btn.setAccessibleName(text)
                # A floor, so a cramped panel scrolls instead of flattening
                # the text into its own padding.
                btn.setMinimumHeight(theme.sc(34))
                theme.hover_lift(btn)

                if btn_id == "detect":
                    default_slot = self._open_object_detection
                elif btn_id == "save":
                    default_slot = self._save_mission
                elif kind == "danger":
                    default_slot = lambda _, c=command, t=text: self._confirm_command(c, t)
                else:
                    default_slot = lambda _, c=command, t=text: self.command_signal.emit(c, t)
                btn.clicked.connect(default_slot)

                self.command_buttons[btn_id] = btn
                self._button_defaults[btn_id] = {
                    "text": text, "icon_name": icon_name,
                    "danger": kind == "danger", "slot": default_slot,
                }
                control.layout().addWidget(btn, row, 0, 1, 2)
                row += 1

        layout.addWidget(control)

        # ── Map ───────────────────────────────────────────────────────────────
        # Headless: GroundStation detaches this into the centre strip below
        # the payload frame, next to the flag monitor — GPS's old spot —
        # where a "CUBESAT LOCATION" heading would repeat the page's own
        # "GPS STATUS" card right next to it.
        map_group = theme.TabCard(headless=True)
        self.map_card = map_group
        map_group.setToolTip(
            "Displays the real-time geographical location of the CubeSat.\n"
            "Position is updated using GPS latitude and longitude telemetry.\n"
            "Map view helps visualise the CubeSat ground track."
        )
        map_layout = QtWidgets.QVBoxLayout()
        map_layout.setContentsMargins(theme.sc(12), map_group.tab_height() + theme.sc(6), theme.sc(12), theme.sc(10))
        map_group.setLayout(map_layout)

        # What the embedded frame is currently showing, as opposed to the
        # latest position received. load_map() compares the two.
        self._map_shown_lat = DEFAULT_LAT
        self._map_shown_lon = DEFAULT_LON
        self._map_loaded_at = None

        # No "Open in browser" row — headless and detached into the centre
        # strip, this card is just the map now. Double-click it instead.
        self.map_view = _MapView()
        self.map_view.setToolTip(
            "CubeSat position from GPS telemetry.\nDouble-click to open it in your browser.")
        self.map_view.doubleClicked.connect(self._open_map_in_browser)
        map_layout.addWidget(self.map_view)
        layout.addWidget(map_group, 1)

        self.load_map(DEFAULT_LAT, DEFAULT_LON)

    # ── public API ────────────────────────────────────────────────────────────
    def load_map(self, lat, lon):
        """
        Point the map at a new position.

        This is called from _parse_packet, so at 1 Hz it ran once a second —
        and it used to rebuild the whole document and hand it to setHtml()
        every time. The frame never finished loading: it flashed white, lost
        its tiles and reset any pan or zoom the operator had made.

        The frame is only rebuilt when the CubeSat has actually moved far
        enough to matter, and never more than once every MAP_RELOAD_MIN_S.
        """
        try:
            lat_f, lon_f = float(lat), float(lon)
        except (TypeError, ValueError):
            return

        now = time.monotonic()
        if self._map_loaded_at is not None:
            moved = math.hypot(lat_f - self._map_shown_lat,
                               lon_f - self._map_shown_lon)
            if (moved < MAP_MOVE_MIN_DEG
                    or now - self._map_loaded_at < MAP_RELOAD_MIN_S):
                return

        self._map_shown_lat = lat_f
        self._map_shown_lon = lon_f
        self._map_loaded_at = now

        html = (
            "<html><body style='margin:0;background:#000'>"
            f'<iframe width="100%" height="100%" frameborder="0" '
            f'style="border:0;display:block" '
            f'src="https://maps.google.com/maps?q={lat_f},{lon_f}&z=13&output=embed">'
            "</iframe></body></html>"
        )
        self.map_view.setHtml(html)

    def _open_map_in_browser(self):
        import webbrowser
        url = (f"https://www.google.com/maps?q="
               f"{self._map_shown_lat},{self._map_shown_lon}&z=13")
        webbrowser.open(url)

    def detach_map_card(self) -> QtWidgets.QWidget:
        """Hand the map card to whoever wants to place it.

        GroundStation puts it in the centre strip, under the payload frame,
        next to the flag monitor — where GPS used to sit before it moved
        back to the telemetry panel. load_map() and the rest of the map's
        state stay on this class; only the card's parent changes. Used
        standalone — in the preview tool — the panel keeps it and this is
        simply never called.
        """
        self._layout.removeWidget(self.map_card)
        self.map_card.setParent(None)
        return self.map_card

    def set_button_override(self, btn_id: str, *, text: str = None,
                             icon_name: str = None, danger: bool = False,
                             slot=None) -> None:
        """Temporarily swap a command button's label, icon, colour and handler.

        Transmit Image becomes Cancel for the duration of a downlink — the
        same slot in the panel the operator was already looking at, rather
        than a separate cancel control somewhere else. Call with no keyword
        arguments to put the button back exactly as it was built.
        """
        btn = self.command_buttons[btn_id]
        default = self._button_defaults[btn_id]
        btn.setText(text if text is not None else default["text"])
        use_icon = icon_name if icon_name is not None else default["icon_name"]
        use_danger = danger or (text is None and default["danger"])
        btn.setIcon(theme.icon(use_icon, theme.ALARM if use_danger else theme.SLATE, theme.sc(16)))
        btn.setObjectName("dangerBtn" if use_danger else "")
        theme.repolish(btn)
        btn.clicked.disconnect()
        btn.clicked.connect(slot if slot is not None else default["slot"])

    # ── internal ──────────────────────────────────────────────────────────────
    @staticmethod
    def _divider() -> QtWidgets.QFrame:
        """A hairline between command groups."""
        line = QtWidgets.QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.LINE}; border: none;")
        return line

    def _open_object_detection(self):
        dlg = ObjectDetectionDialog(parent=self)
        user_email = getattr(self.parent(), "user_email", "") or \
                     getattr(self.parent().parent() if self.parent() else None, "user_email", "") or ""
        dlg._user_email = user_email
        dlg.exec_()

    def _confirm_command(self, cmd: str, name: str):
        reply = ThemedMessageBox.question(
            self,
            f"Confirm: {name}",
            f"Are you sure you want to execute this command?",
            informative=name,
            buttons=["Yes, Execute", "Cancel"],
            danger_buttons=["Yes, Execute"],
        )
        if reply == "Yes, Execute":
            self.command_signal.emit(cmd, name)

    def _save_mission(self):
        """Walk up to GroundStation and trigger its save logic."""
        parent = self.parent()
        while parent and not hasattr(parent, "temp_path"):
            parent = parent.parent()

        if not parent:
            ThemedMessageBox.critical(self, "Error", "Main window not found.")
            return

        import shutil
        now      = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"MissionInfo_{now}.xlsx"
        save_dir = os.path.join(DATA_ROOT, "APEX_GCS_Saved", "data")
        full_path = os.path.join(save_dir, filename)

        try:
            os.makedirs(save_dir, exist_ok=True)
            shutil.copy2(parent.temp_path, full_path)
            parent._mission_saved = True
            if os.path.exists(parent.temp_path):
                os.remove(parent.temp_path)
                print("Temp file deleted after save.")
            # "Show in folder" rather than an unconditional os.startfile:
            # saving mid-mission used to throw an Explorer window in front of
            # the console, taking focus off live telemetry without asking.
            choice = ThemedMessageBox.question(
                self, "Mission Saved",
                "Mission data saved successfully.",
                informative=full_path,
                buttons=["Show in folder", "Close"],
            )
            if choice == "Show in folder":
                os.startfile(save_dir)

            # ── Auto-backup xlsx to the customer's APEX backup ──────────────────
            user_email = getattr(parent, "user_email", "")
            if user_email:
                import widgets.auth_backend as auth
                import threading
                threading.Thread(
                    target=auth.upload_file,
                    args=(user_email, full_path, "files"),
                    daemon=True,
                ).start()
        except Exception as e:
            ThemedMessageBox.critical(self, "Save Error", "Could not save mission data.", informative=str(e))
