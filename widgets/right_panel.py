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

import trimesh
from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtOpenGL import QGLWidget
from OpenGL.GL import *
from OpenGL.GLU import *

from config import DEFAULT_LAT, DEFAULT_LON
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

class _MapView(QtWidgets.QWidget):
    """
    The embedded map.

    There used to be a fully transparent overlay stretched across this widget,
    with WA_TransparentForMouseEvents off, purely so a double-click could be
    caught. That swallowed every mouse event, so the map could not be panned,
    zoomed or scrolled at all — while its tooltip claimed it was interactive.
    The overlay is gone; "Open in browser" is an explicit button instead.
    """

    def __init__(self):
        super().__init__()
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self._web = QWebEngineView(self)
        self._web.setStyleSheet("background: transparent;")
        self._layout.addWidget(self._web)

    def setHtml(self, html):
        self._web.setHtml(html)

    def setToolTip(self, text):
        self._web.setToolTip(text)

    def setToolTipDuration(self, ms):
        self._web.setToolTipDuration(ms)
# ══════════════════════════════════════════════════════════════════════════════
#  CubeSat3D
# ══════════════════════════════════════════════════════════════════════════════
class CubeSat3D(QGLWidget):
    """Renders the CubeSat GLB model with real-time orientation updates."""

    def __init__(self):
        super().__init__()
        self.pitch = self.roll = self.yaw = 0.0

        model_path = os.path.join(BASE_PATH, "models", "space_club_cubesat.glb")
        model = trimesh.load(model_path)

        self.meshes = list(model.geometry.values()) if isinstance(model, trimesh.Scene) else [model]

        for mesh in self.meshes:
            if hasattr(mesh.visual, "to_color"):
                mesh.visual = mesh.visual.to_color()
            mesh.vertices -= mesh.center_mass
            scale = 1.3 / max(mesh.extents)
            mesh.vertices *= scale

    def update_orientation(self, pitch: float, roll: float, yaw: float):
        self.pitch, self.roll, self.yaw = pitch, roll, yaw
        self.update()

    # ── OpenGL overrides ──────────────────────────────────────────────────────
    def initializeGL(self):
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glEnable(GL_COLOR_MATERIAL)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
        glEnable(GL_NORMALIZE)
        glShadeModel(GL_SMOOTH)
        glLightfv(GL_LIGHT0, GL_POSITION, [5, 5, 5, 1])
        glLightfv(GL_LIGHT0, GL_DIFFUSE,  [1, 1, 1, 1])
        glEnable(GL_TEXTURE_2D)

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
        glBegin(GL_TRIANGLES)
        for mesh in self.meshes:
            colors = mesh.visual.vertex_colors if hasattr(mesh.visual, "vertex_colors") else None
            for i, face in enumerate(mesh.faces):
                n = mesh.face_normals[i]
                glNormal3f(n[0], n[1], n[2])
                for v in face:
                    if colors is not None:
                        c = colors[v] / 255.0
                        glColor3f(c[0], c[1], c[2])
                    else:
                        glColor3f(0.8, 0.8, 0.8)
                    vert = mesh.vertices[v]
                    glVertex3f(vert[0], vert[1], vert[2])
        glEnd()


# ══════════════════════════════════════════════════════════════════════════════
#  RightPanel
# ══════════════════════════════════════════════════════════════════════════════
class RightPanel(QtWidgets.QFrame):
    """Right-side panel with 3-D viewer, gas sensor, controls, and map."""

    command_signal = QtCore.pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)

        def group(name: str) -> QtWidgets.QGroupBox:
            g = QtWidgets.QGroupBox(name)
            g.setObjectName("groupBox")
            g.setLayout(QtWidgets.QGridLayout())
            return g

        # ── CubeSat Orientation ───────────────────────────────────────────────
        imu = group("🧭 CUBESAT ORIENTATION")
        self.cubesat_3d = CubeSat3D()
        self.cubesat_3d.setMinimumHeight(250)
        imu.layout().addWidget(self.cubesat_3d, 0, 0, 1, 2)

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

        # ── Raw Gas Sensor ────────────────────────────────────────────────────
        gas = group("🫧 RAW GAS SENSOR VALUES")
        gas.setToolTip(
            "Raw gas resistance reading from the onboard gas sensor.\n"
            "Used to estimate air quality values like IAQ, eCO2, and VOC.\n"
            "Higher resistance generally indicates cleaner air."
        )
        gas.setToolTipDuration(2000)

        lbl = QtWidgets.QLabel("Gas Resistance")
        lbl.setObjectName("nameLabel")
        self.gas_resistance = QtWidgets.QLabel("--")
        self.gas_resistance.setObjectName("valueLabel")
        self.gas_resistance.setAlignment(QtCore.Qt.AlignRight)
        self.gas_resistance.unit = "Ω"
        self.gas_resistance.setToolTip(
            "Gas sensor resistance value measured in Ohms.\n"
            "Used internally to calculate IAQ, eCO2, and VOC levels."
        )
        self.gas_resistance.setToolTipDuration(2000)
        gas.layout().addWidget(lbl, 0, 0)
        gas.layout().addWidget(self.gas_resistance, 0, 1)
        # Keep old dict interface so ground_station.py doesn't need changes
        self.gas_labels = {"Gas Resistance": self.gas_resistance}
        layout.addWidget(gas)

        # ── Control Panel ─────────────────────────────────────────────────────
        control = group("CONTROL PANEL")
        buttons = [
            ("📸 Capture Image",          "C\n",  "Command CubeSat camera to capture a new image.",      False),
            ("📤 Transmit Image",          "S\n",  "Transmit stored payload image to ground station.",    False),
            ("📤 Transmit CW Signal",      "B\n",  "Transmit Morse CW beacon signal.",                    False),
            ("📤 Start Object Detection",  "",     "Run object detection on a downlinked image.",          False),
            ("🛜 Send Telemetry",          "T\n",  "Request current telemetry packet.",                   False),
            ("🔄 Restart System",          "R\n",  "Reboot onboard computer.",                            True),
            ("🔌 Reconnect Serial",         "A\n",  "Disconnect and reconnect the serial port.",            False),
            ("💾 Save Mission",            None,   "Save current mission logs.",                          False),
        ]
        for i, (text, command, tip, confirm) in enumerate(buttons):
            btn = QtWidgets.QPushButton(text)
            btn.setToolTip(tip)
            btn.setToolTipDuration(2000)

            if text == "📤 Start Object Detection":
                btn.clicked.connect(self._open_object_detection)
            elif command:
                if confirm:
                    btn.clicked.connect(
                        lambda _, c=command, t=text: self._confirm_command(c, t)
                    )
                else:
                    btn.clicked.connect(
                        lambda _, c=command, t=text: self.command_signal.emit(c, t)
                    )
            if text == "💾 Save Mission":
                btn.clicked.connect(self._save_mission)

            control.layout().addWidget(btn, i, 0, 1, 2)
        layout.addWidget(control)

        # ── Map ───────────────────────────────────────────────────────────────
        map_group = QtWidgets.QGroupBox("📍 CUBESAT LOCATION")
        map_group.setObjectName("groupBox")
        map_group.setToolTip(
            "Displays the real-time geographical location of the CubeSat.\n"
            "Position is updated using GPS latitude and longitude telemetry.\n"
            "Map view helps visualise the CubeSat ground track."
        )
        map_group.setToolTipDuration(2000)
        map_layout = QtWidgets.QVBoxLayout()
        map_group.setLayout(map_layout)

        self._map_lat = DEFAULT_LAT
        self._map_lon = DEFAULT_LON
        # What the embedded frame is currently showing, as opposed to the
        # latest position received. load_map() compares the two.
        self._map_shown_lat = DEFAULT_LAT
        self._map_shown_lon = DEFAULT_LON
        self._map_loaded_at = None

        open_row = QtWidgets.QHBoxLayout()
        open_row.setContentsMargins(0, 0, 0, 0)
        open_row.addStretch()
        open_btn = QtWidgets.QPushButton("Open in browser")
        open_btn.setObjectName("ghostBtn")
        open_btn.setCursor(QtCore.Qt.PointingHandCursor)
        open_btn.setToolTip("Open this position in your browser and copy the link.")
        open_btn.clicked.connect(self._open_map_in_browser)
        open_row.addWidget(open_btn)
        map_layout.addLayout(open_row)

        self.map_view = _MapView()
        self.map_view.setToolTip("CubeSat position from GPS telemetry. Drag to pan, scroll to zoom.")
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

        The position is always recorded so "Open in browser" stays current,
        but the frame is only rebuilt when the CubeSat has actually moved far
        enough to matter, and never more than once every MAP_RELOAD_MIN_S.
        """
        try:
            lat_f, lon_f = float(lat), float(lon)
        except (TypeError, ValueError):
            return

        self._map_lat = lat_f
        self._map_lon = lon_f

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
        url = f"https://www.google.com/maps?q={self._map_lat},{self._map_lon}&z=13"
        QtWidgets.QApplication.clipboard().setText(url)
        webbrowser.open(url)

    # ── internal ──────────────────────────────────────────────────────────────
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
        save_dir = os.path.join(BASE_PATH, "APEX_GCS_Saved", "data")
        os.makedirs(save_dir, exist_ok=True)
        full_path = os.path.join(save_dir, filename)

        try:
            shutil.copy2(parent.temp_path, full_path)
            parent._mission_saved = True
            if os.path.exists(parent.temp_path):
                os.remove(parent.temp_path)
                print("Temp file deleted after save.")
            ThemedMessageBox.information(
                self, "Mission Saved",
                "Mission data saved successfully.",
                informative=full_path,
            )
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
