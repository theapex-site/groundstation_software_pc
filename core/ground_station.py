"""
core/ground_station.py
──────────────────────
GroundStation – the main QMainWindow that wires together:
  • TelemetryPanel  (left)
  • Centre column   (header, image downlink, mini-graphs)
  • RightPanel      (right)

It also owns:
  • Serial read loop
  • Image-downlink thread + progress
  • Excel mission-log writer
  • Auto-reconnect logic
  • Close-event save prompt
"""

import atexit
import io
import os
import struct
import sys
import threading
import time
import zlib
from datetime import datetime

import widgets.firebase_backend as fb

import openpyxl
import serial
import serial.tools.list_ports
from PIL import Image, ImageFile

from PyQt5 import QtWidgets, QtCore, QtGui

import settings
import theme
from config import (
    BAUD_RATE, SERIAL_TIMEOUT, SERIAL_POLL_MS,
    PACKET_SIZE, IMAGE_RX_TIMEOUT, FILE_INFO_TIMEOUT, IMAGE_MODE_TIMEOUT,
)
from widgets.telemetry_panel import TelemetryPanel
from widgets.right_panel      import RightPanel
from widgets.graphs           import MiniGraph, GraphPopup
from widgets.dialogs          import (Toast, ThemedMessageBox, UserProfileDialog,
                                     SettingsDialog)

if getattr(sys, 'frozen', False):
    BASE_PATH = sys._MEIPASS
else:
    BASE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

ImageFile.LOAD_TRUNCATED_IMAGES = True




class GroundStation(QtWidgets.QMainWindow):
    """Top-level application window for the APEX Ground Control Station."""

    # Seconds of '@@' silence before the link is called lost.
    LINK_SILENCE_S = 8.0

    # Signals emitted from the background image-receive thread
    image_done_signal = QtCore.pyqtSignal(bool, int, int)
    progress_signal   = QtCore.pyqtSignal(int, str)

    def __init__(self, com_port: str = None, user_email: str = ""):
        super().__init__()
        self.com_port   = com_port
        self.user_email = user_email
        self.setWindowTitle("APEX Ground Control Station")
        # resize(1900, 1080) was taller than the usable area of a 1080p screen
        # once the title bar and taskbar are counted, so the window opened
        # already clipped with no way to see the bottom of either panel.
        self.setMinimumSize(1180, 700)
        screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
        self.resize(min(1900, screen.width()), min(1080, screen.height()))
        if screen.width() <= 1600 or screen.height() <= 900:
            self.setWindowState(self.windowState() | QtCore.Qt.WindowMaximized)

        # ── Background ────────────────────────────────────────────────────────
        # The website's animated space scene, drawn in paintEvent. Was
        # images/BG1.jpg, a stock space photo that did not match the site.
        from widgets.space_bg import SpaceScene
        self._scene = SpaceScene()
        # Rate and on/off come from Settings now rather than only from the
        # APEX_STATIC_BG / APEX_BG_FPS environment variables, which a customer
        # was never going to discover. The env vars still override.
        self._bg_timer = QtCore.QTimer(self)
        self._bg_timer.timeout.connect(self._tick_background)
        self._apply_display_settings()

        # ── Mission log (Excel) ───────────────────────────────────────────────
        self._init_mission_log()

        # ── Image-downlink state ──────────────────────────────────────────────
        self.receiving_image      = False
        self.image_mode           = False
        self.expected_image_size  = 0
        self.image_buffer         = bytearray()
        self.total_packets        = 0
        self.received_packets     = 0
        self.image_start_time     = None

        self.image_done_signal.connect(self._show_image_result)
        self.progress_signal.connect(self._update_progress)

        # ── Session accounting ────────────────────────────────────────────────
        self._session_start    = time.monotonic()
        self._packet_count     = 0
        self._last_packet_time = None     # None until the first packet lands
        self._recent_packets   = []       # monotonic stamps, for the rate
        self._last_link_time   = None     # last '@@' link frame

        # ── Build UI ──────────────────────────────────────────────────────────
        self._build_ui()

        # ── Link status blink ─────────────────────────────────────────────────
        # Starts disconnected. It used to start True while the label read
        # "LINK NOT CONNECTED", so the window opened asserting both at once.
        self.is_connected = False
        self.blink_state  = True
        self.blink_timer  = QtCore.QTimer()
        self.blink_timer.timeout.connect(self._blink_link_status)

        # Drives the mission clock, the packet rate and — the point of it —
        # the data-age readout that tells the operator whether what is on
        # screen is still worth believing.
        self.heartbeat = QtCore.QTimer(self)
        self.heartbeat.timeout.connect(self._tick_session)
        self.heartbeat.start(1000)

        # ── Serial connection ─────────────────────────────────────────────────
        self._open_serial()
        self.serial_timer = QtCore.QTimer()
        self.serial_timer.timeout.connect(self._read_serial)
        self.serial_timer.start(SERIAL_POLL_MS)

        # ── Check for updates ─────────────────────────────────────────────────
        from core.updater import check_for_updates
        check_for_updates(parent=self)

    # ══════════════════════════════════════════════════════════════════════════
    #  UI construction
    # ══════════════════════════════════════════════════════════════════════════
    def _build_app_bar(self) -> QtWidgets.QWidget:
        """
        The 56px bar across the top.

        Replaces a header row that scaled the APEX logo to 250x250, the
        CubeSat render to 250x250 and the title plate to 420px wide — roughly
        a quarter of the vertical space on a 1080p screen spent on decoration,
        directly above data set at 11px. That space goes to telemetry now, and
        the bar carries the session state instead.
        """
        bar = QtWidgets.QWidget()
        bar.setObjectName("appBar")
        bar.setFixedHeight(56)

        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(16, 0, 20, 0)
        row.setSpacing(9)

        logo = QtWidgets.QLabel()
        pix = QtGui.QPixmap(os.path.join(BASE_PATH, "images", "Apex Logo.png"))
        if not pix.isNull():
            logo.setPixmap(pix.scaled(32, 32, QtCore.Qt.KeepAspectRatio,
                                      QtCore.Qt.SmoothTransformation))
        row.addWidget(logo)

        wordmark = QtWidgets.QLabel("APEX")
        wordmark.setObjectName("appBarMark")
        row.addWidget(wordmark)

        sub = QtWidgets.QLabel("GROUND CONTROL")
        sub.setObjectName("appBarSub")
        row.addWidget(sub)

        row.addStretch()

        # Session chips. Mission clock, packet count and rate were tracked
        # already and never shown, so a link degrading from 1 Hz to 0.2 Hz
        # looked identical to a healthy one until it dropped out.
        self.link_status = QtWidgets.QLabel("● ACQUIRING")
        self.link_status.setObjectName("linkChip")
        self.link_status.setProperty("state", "acquiring")
        self.link_status.setFixedWidth(180)
        self.link_status.setAlignment(QtCore.Qt.AlignCenter)

        self.com_label     = QtWidgets.QLabel(f"COM {self.com_port}")
        self.rssi_label    = QtWidgets.QLabel("RSSI —")
        self.snr_label     = QtWidgets.QLabel("SNR —")
        self.clock_label   = QtWidgets.QLabel("T+00:00:00")
        self.packets_label = QtWidgets.QLabel("0 PKT")
        self.rate_label    = QtWidgets.QLabel("— Hz")
        for w in (self.com_label, self.rssi_label, self.snr_label,
                  self.clock_label, self.packets_label, self.rate_label):
            w.setObjectName("statChip")

        # Bottom-aligned so each chip's open edge lands on the bar's own
        # bottom border and the two join, the way the group titles do.
        row.addWidget(self.link_status, 0, QtCore.Qt.AlignBottom)
        for w in (self.com_label, self.rssi_label, self.snr_label,
                  self.clock_label, self.packets_label, self.rate_label):
            row.addWidget(w, 0, QtCore.Qt.AlignBottom)

        # A gear means preferences everywhere else, and here it opened the
        # user's profile. The profile is now the user's initials; the gear
        # opens actual settings.
        settings_btn = QtWidgets.QPushButton()
        settings_btn.setIcon(theme.icon("gear", theme.SLATE, 17))
        settings_btn.setIconSize(QtCore.QSize(17, 17))
        settings_btn.setObjectName("settingsBtn")
        settings_btn.setFixedSize(34, 34)
        settings_btn.setCursor(QtCore.Qt.PointingHandCursor)
        settings_btn.setToolTip("Settings")
        settings_btn.setShortcut(QtGui.QKeySequence("Ctrl+,"))
        settings_btn.setAccessibleName("Settings")
        settings_btn.clicked.connect(self.open_settings)
        row.addWidget(settings_btn)

        initials = self._user_initials()
        profile_btn = QtWidgets.QPushButton(initials if initials else "")
        if not initials:
            # Signed out, or launched by the demo harness: a generic figure
            # reads better than a question mark in a disc.
            profile_btn.setIcon(theme.icon("user", theme.DEEP, 16))
            profile_btn.setIconSize(QtCore.QSize(16, 16))
        profile_btn.setObjectName("avatarBtn")
        profile_btn.setFixedSize(34, 34)
        profile_btn.setCursor(QtCore.Qt.PointingHandCursor)
        profile_btn.setToolTip(self.user_email or "User profile")
        profile_btn.setAccessibleName("User profile")
        profile_btn.clicked.connect(self._open_user_profile)
        row.addWidget(profile_btn)

        return bar

    def _user_initials(self) -> str:
        """Up to two letters from the signed-in address, for the avatar."""
        local = (self.user_email or "").split("@")[0]
        parts = [p for p in local.replace("_", ".").split(".") if p]
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[1][0]).upper()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        # Application bar across the top, panels beneath it.
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_app_bar())

        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(theme.GAP, theme.GAP, theme.GAP, theme.GAP)
        body.setSpacing(theme.GAP)
        root.addLayout(body, 1)

        # ── Left: telemetry panel ─────────────────────────────────────────────
        # minimumWidth rather than fixed: 420 + 450 of hard-fixed panels left
        # only ~480px of a 1366-wide laptop for the centre column.
        self.telemetry = TelemetryPanel()
        self.telemetry.setMinimumWidth(360)
        self.telemetry.setMaximumWidth(460)
        # No drop shadow here: the ground is #000000, so a black shadow in the
        # GAP beside the panel measured a 1% luminance change — invisible, and
        # it costs an offscreen render of the whole panel on every repaint.
        # Depth on this background comes from the raised card tone instead.
        body.addWidget(self.telemetry, 0)

        # ── Centre ────────────────────────────────────────────────────────────
        centre = QtWidgets.QFrame()
        cl = QtWidgets.QVBoxLayout(centre)
        cl.setSpacing(theme.GAP)
        cl.setContentsMargins(0, 0, 0, 0)
        body.addWidget(centre, 1)

        # Payload heading
        payload_title = QtWidgets.QLabel("PAYLOAD IMAGE DOWNLINK")
        payload_title.setObjectName("mainTitle")
        payload_title.setAlignment(QtCore.Qt.AlignCenter)
        cl.addWidget(payload_title)

        # Progress bar. Hidden when idle rather than left showing the last
        # transfer's state, and the caption sits beside it rather than under
        # the chunk, where it was half-legible on the fill boundary.
        prog_row = QtWidgets.QHBoxLayout()
        prog_row.setSpacing(12)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximum(100)
        self.progress.setValue(0)
        self.progress_caption = QtWidgets.QLabel("")
        self.progress_caption.setObjectName("statChip")
        self.cancel_btn = QtWidgets.QPushButton("Cancel downlink")
        self.cancel_btn.setObjectName("dangerBtn")
        self.cancel_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._cancel_downlink)
        prog_row.addWidget(self.progress, 1)
        prog_row.addWidget(self.progress_caption)
        prog_row.addWidget(self.cancel_btn)
        self.progress_row = QtWidgets.QWidget()
        self.progress_row.setLayout(prog_row)
        self.progress_row.hide()
        cl.addWidget(self.progress_row)

        # Image display
        # An empty state that says what to do next, rather than a bare
        # "NO IMAGE RECEIVED" in whatever the default font was.
        self._image_empty_html = (
            f"<div align='center' style='color:{theme.SLATE}'>"
            f"<span style='font-size:{theme.FS_HEAD}px; font-weight:600;'>"
            "No payload image yet</span><br><br>"
            f"<span style='font-size:{theme.FS_BODY}px;'>Press "
            f"<span style='color:{theme.TEAL}'>Transmit Image</span> to downlink "
            "the latest capture</span></div>"
        )
        self.image_label = QtWidgets.QLabel(self._image_empty_html)
        self.image_label.setScaledContents(False)
        self.image_label.setAlignment(QtCore.Qt.AlignCenter)
        self.image_label.setStyleSheet(
            f"background-color:{theme.DEEP}; border:1px solid {theme.LINE};"
            f" border-radius:{theme.R_CARD}px;"
        )
        self.image_label.setToolTip(
            "Payload image downlink window.\n"
            "Displays images captured by the CubeSat camera.\n"
            "Updates when new image packets are received."
        )
        self.image_label.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
        cl.addWidget(self.image_label, 5)
        cl.addSpacing(10)

        # Mini graphs. Four across needs about 700px to stay readable; below
        # that the grid reflows to 2x2 rather than crushing them.
        self.alt_graph  = MiniGraph("Altitude",         "m")
        self.temp_graph = MiniGraph("Temperature",      "°C")
        self.batt_graph = MiniGraph("Battery Voltage",  "V")
        self.iaq_graph  = MiniGraph("AQI Index",        "")
        self.graph_popups: list[GraphPopup] = []
        self._graphs = (self.alt_graph, self.temp_graph,
                        self.batt_graph, self.iaq_graph)
        for g in self._graphs:
            g.clicked.connect(self._open_graph_popup)
            g.setMinimumWidth(168)

        self.graph_grid = QtWidgets.QGridLayout()
        self.graph_grid.setSpacing(theme.GAP)
        self._graph_columns = 0
        self._layout_graphs(4)
        cl.addLayout(self.graph_grid, 2)

        # ── Right panel ───────────────────────────────────────────────────────
        self.right = RightPanel()
        self.right.command_signal.connect(self._send_command)
        self.right.setMinimumWidth(380)
        self.right.setMaximumWidth(480)
        body.addWidget(self.right, 0)

    def _layout_graphs(self, columns: int):
        """Re-flow the mini graphs into `columns` columns."""
        if columns == self._graph_columns:
            return
        self._graph_columns = columns
        for g in self._graphs:
            self.graph_grid.removeWidget(g)
        for i, g in enumerate(self._graphs):
            self.graph_grid.addWidget(g, i // columns, i % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "graph_grid"):
            centre_width = self.width() - self.telemetry.width() - self.right.width()
            self._layout_graphs(4 if centre_width >= 700 else 2)

    # ══════════════════════════════════════════════════════════════════════════
    #  Mission log helpers
    # ══════════════════════════════════════════════════════════════════════════
    def _init_mission_log(self):
        temp_dir = os.path.join(BASE_PATH, "APEX_GCS_Saved", "temp")
        os.makedirs(temp_dir, exist_ok=True)
        self.temp_path = os.path.join(
            temp_dir,
            f"temp_mission_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        )

        self._wb     = openpyxl.Workbook()
        self._ws_tel = self._wb.active
        self._ws_tel.title = "Telemetry"
        self._ws_tel.append([
            "CubeSat ID","Date","Time","Azimuth","Elevation",
            "Battery Voltage","Battery SOC","Solar Voltage","Current","Power",
            "Satellites","Latitude","Longitude","HDOP",
            "Altitude","Speed","Temperature","Pressure","Humidity",
            "Yaw","Pitch","Roll","Gas Resistance",
        ])
        self._ws_flag = self._wb.create_sheet("Flags")
        self._ws_flag.append(["Timestamp", "Flag"])
        self._ws_link = self._wb.create_sheet("Link Status")
        self._ws_link.append(["Timestamp", "Connected", "RSSI", "SNR"])
        self._wb.save(self.temp_path)

        self._mission_saved      = False
        self._telemetry_row_count = 0
        self._flag_row_count      = 0
        self._link_row_count      = 0
        atexit.register(self._cleanup_temp)

    def _append_telemetry_row(self, parts):
        try:
            self._ws_tel.append([
                parts[1],  parts[2],  parts[3],  parts[4],  parts[5],
                parts[6],  parts[7],  parts[8],  parts[9],  parts[10],
                parts[11], parts[12], parts[13], parts[14],
                parts[15], parts[16], parts[17], parts[18], parts[19],
                parts[22], parts[20], parts[21], parts[23],
            ])
            self._telemetry_row_count += 1
            self._wb.save(self.temp_path)
        except Exception as e:
            print("Telemetry write error:", e)

    def _append_flag_row(self, timestamp: str, flag: str):
        try:
            self._ws_flag.append([timestamp, flag])
            self._flag_row_count += 1
            self._wb.save(self.temp_path)
        except Exception as e:
            print("Flag write error:", e)

    def _append_link_row(self, timestamp, connected, rssi, snr):
        try:
            self._ws_link.append([timestamp, connected, rssi, snr])
            self._link_row_count += 1
            self._wb.save(self.temp_path)
        except Exception as e:
            print("Link write error:", e)

    def _cleanup_temp(self):
        if not self._mission_saved:
            try:
                if os.path.exists(self.temp_path):
                    os.remove(self.temp_path)
                    print("Temp mission file deleted.")
            except Exception as e:
                print("Cleanup error:", e)

    # ══════════════════════════════════════════════════════════════════════════
    #  Auto-update
    # ══════════════════════════════════════════════════════════════════════════
    @QtCore.pyqtSlot(str, str)
    def _show_update_dialog(self, latest_version, download_url):
        import webbrowser
        from config import APP_VERSION
        msg = ThemedMessageBox(self)
        msg.setWindowTitle("Update Available")
        msg.setText(
            f"A new version of APEX GCS is available!\n\n"
            f"Current: v{APP_VERSION}\n"
            f"Latest:  v{latest_version}\n\n"
            f"Would you like to download it now?"
        )
        msg.setStandardButtons(
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if msg.exec_() == QtWidgets.QMessageBox.Yes:
            webbrowser.open(download_url)

    # ══════════════════════════════════════════════════════════════════════════
    #  Serial
    # ══════════════════════════════════════════════════════════════════════════
    def _open_serial(self):
        try:
            self.serial_port = serial.Serial(
                port=self.com_port, baudrate=settings.baud(), timeout=SERIAL_TIMEOUT
            )
            self.serial_port.dtr = False
            self.serial_port.rts = False
            print("Connected to:", self.com_port)
        except Exception as e:
            ThemedMessageBox.critical(
                self, "Serial Error",
                f"Could not open port {self.com_port}",
                informative=str(e),
            )
            sys.exit()

    def _read_serial(self):
        if self.image_mode:
            return
        try:
            if self.serial_port.in_waiting:
                line = self.serial_port.readline().decode(errors="ignore").strip()
                if line:
                    print("Received:", line)
                    if line.startswith("##"):
                        self._parse_packet(line)
                    elif line.startswith("CS_") or line.startswith("GCS_"):
                        self.telemetry.update_flag(line)
                    elif line.startswith("@@"):
                        self._parse_link_packet(line)
        except serial.SerialException:
            print("Serial disconnected")
            self._handle_serial_disconnect()
        except Exception as e:
            print("Serial Error:", e)

    def _handle_serial_disconnect(self):
        """Called whenever the serial link is lost unexpectedly."""
        self.serial_timer.stop()

        # If we were in image mode, force-exit it cleanly first
        if self.image_mode or self.receiving_image:
            self.receiving_image = False
            self.image_mode      = False
            print("[GCS] Image mode force-cleared due to disconnect")

        try:
            self.serial_port.close()
        except Exception:
            pass

        time.sleep(0.1)   # 100 ms pause before reconnect attempt

        self.com_label.setText(f"COM: {self.com_port} (DISCONNECTED)")
        self.reconnect_timer = QtCore.QTimer()
        self.reconnect_timer.timeout.connect(self._auto_reconnect)
        self.reconnect_timer.start(2000)

        self.reconnect_dialog = _ReconnectDialog(self)
        self.reconnect_dialog.show()
        # Shown without these it can open behind the main window, leaving the
        # operator watching telemetry stop with no visible explanation.
        self.reconnect_dialog.raise_()
        self.reconnect_dialog.activateWindow()

    def _do_serial_reconnect(self):
        """
        Close the port, wait 100 ms, then re-open it.
        Called both by _auto_reconnect (background timer) and by
        the manual Reconnect Serial button.
        """
        try:
            self.serial_port.close()
        except Exception:
            pass
        time.sleep(0.1)
        self.serial_port = serial.Serial(
            port=self.com_port, baudrate=settings.baud(), timeout=SERIAL_TIMEOUT
        )
        self.serial_port.dtr = False
        self.serial_port.rts = False

    def _auto_reconnect(self):
        try:
            self._do_serial_reconnect()
            print("Auto reconnect successful")
            self.serial_timer.start(SERIAL_POLL_MS)
            self.reconnect_timer.stop()
            self.com_label.setText(f"COM: {self.com_port}")
            # The toast fires regardless of the dialog. Reporting recovery
            # only while the dialog happened to be visible meant anyone who
            # dismissed it was never told the link came back.
            self.show_toast("Serial link restored", "success")
            if hasattr(self, "reconnect_dialog") and self.reconnect_dialog.isVisible():
                self.reconnect_dialog.set_reconnected()
        except Exception:
            pass  # will retry on next timer tick

    def manual_reconnect_serial(self):
        """
        Triggered by the Reconnect Serial button.
        Asks the user to confirm, then disconnect → 100 ms → reconnect.
        """
        reply = ThemedMessageBox.question(
            self,
            "Reconnect Serial",
            "Disconnect and reconnect the serial port?",
            informative=f"Port: {self.com_port}  —  the link will be briefly interrupted.",
            buttons=["Reconnect", "Cancel"],
            danger_buttons=[],
        )
        if reply != "Reconnect":
            return

        self.serial_timer.stop()
        if self.image_mode or self.receiving_image:
            self.receiving_image = False
            self.image_mode      = False

        try:
            self._do_serial_reconnect()
            self.com_label.setText(f"COM: {self.com_port}")
            self.serial_timer.start(SERIAL_POLL_MS)
            self.show_toast("Serial reconnected", "success")
            print("[GCS] Manual reconnect successful")
        except Exception as e:
            ThemedMessageBox.critical(
                self, "Reconnect Failed",
                f"Could not reopen {self.com_port}",
                informative=str(e),
            )

    # ══════════════════════════════════════════════════════════════════════════
    #  Packet parsers
    # ══════════════════════════════════════════════════════════════════════════
    def _parse_packet(self, packet: str):
        parts = packet.split(",")
        if parts[0] != "##" or parts[-1] != "$$":
            print("Invalid Packet")
            return
        try:
            self._append_telemetry_row(parts)
            self._note_packet()

            self.telemetry.update_telemetry(
                cubesat_id=parts[1],  date=parts[2],    time=parts[3],
                azimuth=parts[4],     elevation=parts[5],
                batt_v=parts[6],      batt_soc=parts[7], solar_v=parts[8],
                current=parts[9],     power_val=parts[10],
                sats=parts[11],       lat=parts[12],     lon=parts[13],
                hdop=parts[14],       alt=parts[15],     speed=parts[16],
                temp=parts[17],       press=parts[18],   hum=parts[19],
                gas_res=parts[23],
            )

            pitch, roll, yaw = float(parts[20]), float(parts[21]), float(parts[22])
            self.right.cubesat_3d.update_orientation(pitch, roll, yaw)
            self.right.imu_labels["Pitch"].setText(f"{pitch:.2f} °")
            self.right.imu_labels["Roll"].setText(f"{roll:.2f} °")
            self.right.imu_labels["Yaw"].setText(f"{yaw:.2f} °")

            self.alt_graph.add_value(float(parts[15]), parts[3])
            self.temp_graph.add_value(float(parts[17]), parts[3])
            self.batt_graph.add_value(float(parts[6]),  parts[3])

            gas_res = float(parts[23])
            iaq, _,  _ = TelemetryPanel.calculate_air_quality(gas_res)
            self.iaq_graph.add_value(iaq, parts[3])

            gas_val = self.right.gas_labels["Gas Resistance"]
            gas_val.setText(f"{parts[23]} {gas_val.unit}")

            self.right.load_map(parts[12], parts[13])

        except Exception as e:
            print("Parse Error:", e)

    def _parse_link_packet(self, packet: str):
        parts = packet.split(",")
        if parts[0] != "@@" or parts[-1] != "$$":
            return
        try:
            self._last_link_time = time.monotonic()
            connected = bool(int(parts[1]))
            rssi      = int(parts[2])
            snr       = int(parts[3])
            self.update_link_status(connected, snr, rssi)
        except Exception as e:
            print("Link Parse Error:", e)

    # ══════════════════════════════════════════════════════════════════════════
    #  Commands
    # ══════════════════════════════════════════════════════════════════════════
    def _send_command(self, cmd: str, name: str):
        # "Abort Mission" button is re-purposed as "Reconnect Serial"
        if cmd.strip() == "A":
            self.manual_reconnect_serial()
            return

        # A downlink takes the serial port for up to IMAGE_MODE_TIMEOUT with
        # telemetry stopped. That is worth saying out loud before it starts.
        if cmd.strip() == "S":
            mins, secs = divmod(IMAGE_MODE_TIMEOUT, 60)
            if ThemedMessageBox.question(
                    self, "Transmit Image",
                    "Start the payload image downlink?",
                    informative=(f"Live telemetry pauses for the transfer — up to "
                                 f"{mins}m {secs:02d}s. You can cancel at any point."),
                    buttons=["Start downlink", "Cancel"]) != "Start downlink":
                return

        try:
            if self.serial_port and self.serial_port.is_open:
                self.serial_port.write(cmd.encode())
                print("Command sent:", name)
                self.telemetry.update_flag(f"GCS_CMD_{cmd.strip()}")
                self.show_toast(f"Command {name} sent")
                if cmd.strip() == "S":
                    self._start_image_receiver()
        except Exception as e:
            print("Command send error:", e)

    # ══════════════════════════════════════════════════════════════════════════
    #  Image downlink
    # ══════════════════════════════════════════════════════════════════════════
    def _start_image_receiver(self):
        self.serial_timer.stop()
        if self.receiving_image:
            return
        print("Switching to IMAGE MODE")
        self.image_mode          = True
        self.receiving_image     = True
        self.image_buffer        = bytearray()
        self.received_packets    = 0
        self.expected_image_size = 0
        self.image_start_time    = time.time()
        self._downlink_cancelled = False

        # Phase one can wait up to FILE_INFO_TIMEOUT seconds for the header.
        # The bar used to sit at zero showing a hardcoded "Packets: 0/100",
        # which both claimed a transfer size nothing had announced yet and
        # gave no way to tell waiting from hung.
        self.progress.setRange(0, 0)          # indeterminate
        self.progress_caption.setText("Waiting for file header…")
        self.progress_row.show()
        self.cancel_btn.setEnabled(True)
        self.serial_port.reset_input_buffer()
        threading.Thread(target=self._receive_image_chunked, daemon=True).start()

    def _cancel_downlink(self):
        """
        Abort a transfer in progress.

        A downlink holds the serial port for up to IMAGE_MODE_TIMEOUT — two
        and a half minutes — with telemetry stopped, and there was no way to
        get out of it. The receive loop already checks self.receiving_image
        every iteration, so clearing it is all cancellation needs.
        """
        if not self.receiving_image:
            return
        self.cancel_btn.setEnabled(False)
        self.progress_caption.setText("Cancelling…")
        self._downlink_cancelled = True
        self.receiving_image = False
        self.telemetry.update_flag("GCS_DOWNLINK_CANCELLED")

    def _exit_image_mode(self, final_image: bytearray):
        """
        Always called at the end of _receive_image_chunked — regardless of
        success, partial data, or timeout — to guarantee we return to normal
        telemetry operations and never freeze.
        """
        self.receiving_image = False
        self.image_mode      = False
        # Restart the normal serial poll timer (safe to call from any thread)
        QtCore.QMetaObject.invokeMethod(
            self.serial_timer, "start", QtCore.Qt.QueuedConnection
        )
        print(f"[GCS] Image mode exited — {len(final_image)} bytes received")
        if final_image:
            self._save_received_image(final_image)
            self.image_done_signal.emit(True, len(final_image), self.expected_image_size)
        else:
            self.image_done_signal.emit(False, 0, 0)

    def _receive_image_chunked(self):
        mode_start = time.time()   # hard wall-clock ceiling for the whole transfer

        # ── Phase 1: wait for CS_FILE_INFO ───────────────────────────────────
        print("Waiting for CS_FILE_INFO...")
        deadline = time.time() + FILE_INFO_TIMEOUT
        got_file_info = False
        while True:
            # Hard ceiling check
            if time.time() - mode_start > IMAGE_MODE_TIMEOUT:
                print("[GCS] Hard timeout waiting for CS_FILE_INFO — returning to normal")
                self._exit_image_mode(bytearray())
                return

            if time.time() > deadline:
                print("[GCS] FILE_INFO timeout — returning to normal ops")
                self._exit_image_mode(bytearray())
                return

            if not self.serial_port.in_waiting:
                time.sleep(0.01)
                continue

            try:
                line = self.serial_port.readline().decode(errors="ignore").strip()
            except Exception as e:
                print(f"[GCS] Serial error during FILE_INFO hunt: {e}")
                self._exit_image_mode(bytearray())
                return

            if not line:
                continue

            print("FILE_INFO hunt:", line)

            if line.startswith("CS_FILE_INFO:"):
                try:
                    size = int(line.split(":")[1])
                    self.expected_image_size = size
                    self.total_packets       = (size // PACKET_SIZE) + 1
                    self.received_packets    = 0
                    QtCore.QMetaObject.invokeMethod(
                        self.progress, "setMaximum", QtCore.Qt.QueuedConnection,
                        QtCore.Q_ARG(int, self.total_packets)
                    )
                    QtCore.QMetaObject.invokeMethod(
                        self.progress, "setValue", QtCore.Qt.QueuedConnection,
                        QtCore.Q_ARG(int, 0)
                    )
                    print(f"Image size: {size}, Expected packets: {self.total_packets}")
                    got_file_info = True
                    break
                except Exception as e:
                    print("Invalid FILE_INFO:", e)

            # CS_TRANSFER_DONE before any packets → CubeSat already gave up
            if line == "CS_TRANSFER_DONE":
                print("[GCS] CS_TRANSFER_DONE received before FILE_INFO — aborting")
                self._exit_image_mode(bytearray())
                return

        if not got_file_info:
            self._exit_image_mode(bytearray())
            return

        # ── Phase 2: receive binary packets ──────────────────────────────────
        final_image     = bytearray()
        expected_packet = 0
        last_pkt_time   = time.time()
        total_bytes     = 0

        while self.receiving_image:
            # Hard ceiling — if total transfer exceeds IMAGE_MODE_TIMEOUT, bail out
            if time.time() - mode_start > IMAGE_MODE_TIMEOUT:
                print(f"[GCS] Hard IMAGE_MODE_TIMEOUT ({IMAGE_MODE_TIMEOUT}s) — exiting image mode")
                break

            # Per-packet silence timeout — wait longer than CubeSat's gap
            if time.time() - last_pkt_time > IMAGE_RX_TIMEOUT:
                print(f"[GCS] No packets for {IMAGE_RX_TIMEOUT}s — exiting image mode")
                break

            # Check for CS_TRANSFER_DONE or other text line during binary phase
            # Text lines are short; binary packets are always >= 7 bytes
            # Read speculatively if there's data but not enough for a full binary packet
            avail = self.serial_port.in_waiting
            if avail > 0 and avail < 7:
                time.sleep(0.02)   # let more bytes arrive
                avail = self.serial_port.in_waiting

            if avail == 0:
                time.sleep(0.005)
                continue

            # Peek: if the first byte could be a text line (printable ASCII) read it as text
            # A valid binary packet starts with a 2-byte LE packet ID (could be anything)
            # The safest heuristic: if in_waiting < 7, read as text line
            if self.serial_port.in_waiting < 7:
                try:
                    line = self.serial_port.readline().decode(errors="ignore").strip()
                    if line:
                        print(f"[GCS][img text] {line}")
                        if line == "CS_TRANSFER_DONE":
                            print("[GCS] CS_TRANSFER_DONE received — clean image mode exit")
                            self.receiving_image = False
                            break
                except Exception as e:
                    print(f"[GCS] Text read error: {e}")
                continue

            # Binary packet path
            try:
                header = self.serial_port.read(3)
                if len(header) < 3:
                    continue
                packet_id   = header[0] | (header[1] << 8)
                payload_len = header[2]
                payload_crc = self.serial_port.read(payload_len + 4)
                if len(payload_crc) < payload_len + 4:
                    continue
                payload      = payload_crc[:payload_len]
                crc_received = struct.unpack('<I', payload_crc[payload_len:])[0]
                crc_calc     = zlib.crc32(payload) & 0xFFFF_FFFF

                if crc_received == crc_calc and packet_id == expected_packet:
                    final_image.extend(payload)
                    expected_packet  += 1
                    total_bytes      += payload_len
                    self.received_packets += 1
                    self.serial_port.write(f"ACK:{packet_id}\n".encode())
                    last_pkt_time = time.time()
                    print(f"✅ Packet {packet_id} OK — {total_bytes} bytes total")
                    spent = time.time() - mode_start
                    remaining = self.total_packets - self.received_packets
                    eta = (spent / self.received_packets) * remaining if self.received_packets else 0
                    self.progress_signal.emit(
                        self.received_packets,
                        f"Packet {self.received_packets}/{self.total_packets}  ·  "
                        f"{total_bytes / 1024:.1f} KB  ·  about {eta:.0f}s left",
                    )
                    if self.received_packets >= self.total_packets:
                        print("✅ All packets received!")
                        self.receiving_image = False
                        break
                else:
                    print(f"❌ Packet {packet_id} bad (crc_ok={crc_received==crc_calc}, "
                          f"id_ok={packet_id==expected_packet})")
                    self.serial_port.write(f"NACK:{packet_id}\n".encode())

            except Exception as e:
                print(f"[GCS] Binary read error: {e}")
                break

        # ── Always exit cleanly ───────────────────────────────────────────────
        self._exit_image_mode(final_image)

    def _save_received_image(self, image_bytes: bytes):
        folder = os.path.join(BASE_PATH, "APEX_GCS_Saved", "images", "camera")
        os.makedirs(folder, exist_ok=True)
        filename    = datetime.now().strftime("IMG_%Y%m%d_%H%M%S.jpg")
        path        = os.path.join(folder, filename)
        cubesat_id  = self.telemetry.cubesat_id.text().strip() or "APEX"
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
            wm  = self._add_watermark(img, cubesat_id)
            wm.convert("RGB").save(path, "JPEG", quality=95)
        except Exception as e:
            print("Watermark error, saving original:", e)
            with open(path, "wb") as f:
                f.write(image_bytes)
        print("Image saved:", path)
        self._display_image(image_bytes, cubesat_id)

        # ── Auto-backup to the customer's APEX backup ──────────────────────────
        if self.user_email:
            import widgets.auth_backend as auth
            threading.Thread(
                target=auth.upload_file,
                args=(self.user_email, path, "received images"),
                daemon=True,
            ).start()

    @staticmethod
    def _add_watermark(image: Image.Image, cubesat_id: str, timestamp: str = None) -> Image.Image:
        from PIL import ImageDraw, ImageFont
        if timestamp is None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        img     = image.copy().convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw    = ImageDraw.Draw(overlay)
        text    = f"APEX | {cubesat_id} | {datetime.now().strftime('%d-%m-%Y - %H:%M:%S')}"
        font    = None
        for fn in ("consola.ttf", "Consolas.ttf", "arial.ttf", "Arial.ttf"):
            try:
                font = ImageFont.truetype(fn, 12)
                break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default()
        bbox   = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        px, py = 6, 3
        bw, bh = tw + px * 2, th + py * 2
        bx, by = 0, img.height - bh
        draw.rectangle([bx, by, bx + bw, by + bh], fill=(0, 0, 0, 210))
        draw.text((bx + px - bbox[0], by + py - bbox[1]), text, font=font, fill=(56, 189, 248, 255))
        return Image.alpha_composite(img, overlay)

    def _display_image(self, image_bytes: bytes, cubesat_id: str = "APEX"):
        try:
            image = Image.open(io.BytesIO(image_bytes))
            image.load()
        except Exception as e:
            print("Display error:", e)
            return
        try:
            image = self._add_watermark(image.convert("RGBA"), cubesat_id)
            data  = image.tobytes("raw", "RGBA")
            qi    = QtGui.QImage(data, image.width, image.height, QtGui.QImage.Format_RGBA8888)
            pm    = QtGui.QPixmap.fromImage(qi).scaled(
                self.image_label.size(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
            QtCore.QMetaObject.invokeMethod(
                self.image_label, "setPixmap",
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(QtGui.QPixmap, pm),
            )
        except Exception as e:
            print("Display render error:", e)

    def _show_image_result(self, success: bool, received: int, expected: int):
        self.progress_row.hide()
        elapsed = time.time() - (self.image_start_time or time.time())

        if getattr(self, "_downlink_cancelled", False):
            self.show_toast("Downlink cancelled", kind="warn")
            return

        if success:
            if expected > 0 and received < expected * 0.95:
                pct = received * 100 / expected
                ThemedMessageBox.warning(
                    self, "Partial Image Received",
                    f"Only {pct:.0f}% of the image arrived.",
                    informative=f"Received {received:,} of {expected:,} bytes in "
                                f"{elapsed:.0f}s. The missing rows show as flat grey.",
                )
            else:
                # The happy path used to be completely silent: the picture
                # simply appeared, with no confirmation of what had been
                # received or how long it took.
                self.show_toast(
                    f"Image received · {received / 1024:.1f} KB · {elapsed:.0f}s",
                    kind="success")
        else:
            ThemedMessageBox.warning(
                self, "Image Transfer Failed",
                "No image data was received.",
                informative="Check the link quality and try the downlink again.",
            )

    def _update_progress(self, value: int, text: str):
        if self.progress.maximum() == 0 and self.total_packets:
            self.progress.setRange(0, self.total_packets)
        self.progress.setValue(value)
        self.progress_caption.setText(text)

    # ══════════════════════════════════════════════════════════════════════════
    #  Link status
    # ══════════════════════════════════════════════════════════════════════════
    def update_link_status(self, connected: bool, snr=None, rssi=None):
        self.is_connected = connected
        if connected:
            self.blink_timer.stop()
            self.link_status.setText("● LINK STABLE")
            self.link_status.setProperty("state", "stable")
            if snr  is not None: self.snr_label.setText(f"SNR {snr} dB")
            if rssi is not None: self.rssi_label.setText(f"RSSI {rssi} dBm")
        else:
            self.link_status.setText("● SIGNAL LOST")
            self.link_status.setProperty("state", "lost")
            self.blink_timer.start(500)
            self.snr_label.setText("SNR —")
            self.rssi_label.setText("RSSI —")
        theme.repolish(self.link_status)
        ts = datetime.now().strftime("%H:%M:%S")
        self._append_link_row(ts, connected,
                              rssi if rssi is not None else "--",
                              snr  if snr  is not None else "--")

    def _blink_link_status(self):
        """
        Pulse the lost-signal chip.

        The old version alternated the text to #000000, which against the
        animated starfield read as a rendering glitch rather than an alarm.
        It now alternates between the alarm colour and a dim red, so the
        glyph stays legible throughout.
        """
        if self.is_connected:
            return
        self.blink_state = not self.blink_state
        self.link_status.setProperty("state", "lost" if self.blink_state else "blink")
        theme.repolish(self.link_status)

    # ══════════════════════════════════════════════════════════════════════════
    #  Session heartbeat
    # ══════════════════════════════════════════════════════════════════════════
    def _tick_session(self):
        """Once a second: mission clock, packet rate, and the data-age readout."""
        elapsed = int(time.monotonic() - self._session_start)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        self.clock_label.setText(f"T+{h:02d}:{m:02d}:{s:02d}")

        self.packets_label.setText(f"{self._packet_count} PKT")

        # Rate over the trailing ten seconds, so a slowdown shows up long
        # before the link actually drops.
        now = time.monotonic()
        self._recent_packets = [t for t in self._recent_packets if now - t <= 10.0]
        if len(self._recent_packets) >= 2:
            span = self._recent_packets[-1] - self._recent_packets[0]
            rate = (len(self._recent_packets) - 1) / span if span > 0 else 0.0
            self.rate_label.setText(f"{rate:.2f} Hz")
        elif self._last_packet_time is None:
            self.rate_label.setText("— Hz")
        else:
            self.rate_label.setText("0.00 Hz")

        age = None if self._last_packet_time is None else now - self._last_packet_time
        self.telemetry.set_data_age(age)

        # Link state used to change only when an '@@' frame arrived — but a
        # lost link stops those too, so the chip sat on LINK STABLE while the
        # panel beside it was withdrawing its readings. Silence is the signal.
        if (self.is_connected and self._last_link_time is not None
                and now - self._last_link_time > self.LINK_SILENCE_S):
            self.update_link_status(False)

    def _note_packet(self):
        """Record that a telemetry packet just arrived."""
        now = time.monotonic()
        self._packet_count += 1
        self._last_packet_time = now
        self._recent_packets.append(now)

    # ══════════════════════════════════════════════════════════════════════════
    #  Misc
    # ══════════════════════════════════════════════════════════════════════════

    # ── User profile / settings ───────────────────────────────────────────────
    def _open_user_profile(self):
        """Open the UserProfileDialog when the gear button is clicked."""
        dlg = UserProfileDialog(email=self.user_email, parent=self)
        dlg.logout_requested.connect(self._do_logout)
        dlg.exec_()

    def _do_logout(self):
        """Clear saved session and restart the application to show login."""
        from widgets.dialogs import _clear_session
        _clear_session()
        if self.user_email:
            import widgets.auth_backend as auth
            fb.log_logout()
        # Stop serial so we don't leave the port open
        try:
            self.serial_timer.stop()
            if hasattr(self, "serial_port") and self.serial_port and self.serial_port.is_open:
                self.serial_port.close()
        except Exception:
            pass

        # Restart the process so the login screen appears again
        import sys, os
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def show_toast(self, text: str, kind: str = "info"):
        # Toast keeps its own stack per window, so no reference is held here.
        # Assigning to self.toast used to drop the previous one without
        # closing it, leaving two drawn exactly on top of each other.
        Toast(self, text, kind)

    def _open_graph_popup(self, graph_widget: MiniGraph):
        # One window per graph: double-clicking the same mini graph used to
        # stack another popup behind the last one.
        for existing in self.graph_popups:
            if existing.source_graph is graph_widget:
                existing.raise_()
                existing.activateWindow()
                return
        popup = GraphPopup(graph_widget)
        popup.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        popup.destroyed.connect(lambda _=None, p=popup: self._forget_popup(p))
        popup.show()
        self.graph_popups.append(popup)

    def _forget_popup(self, popup):
        if popup in self.graph_popups:
            self.graph_popups.remove(popup)

    # ── settings ──────────────────────────────────────────────────────────────
    def _apply_display_settings(self):
        """Start, stop or re-rate the background animation."""
        self._bg_timer.stop()
        if settings.animate():
            self._bg_timer.start(settings.frame_ms())
        else:
            self.update()

    def _apply_graph_settings(self):
        """Resize the sample windows on the live graphs and any open popup."""
        mini = settings.get("graphs/mini_points")
        popup = settings.get("graphs/popup_points")
        for g in self._graphs:
            g.max_points = mini
            if len(g.data) > mini:
                del g.data[:-mini]
            g.update()
        for p in self.graph_popups:
            p.graph.max_points = popup

    def open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self._apply_display_settings()
            self._apply_graph_settings()
            self.show_toast("Settings saved", "success")

    # Object names the application stylesheet gives a solid background to.
    _OPAQUE_NAMES = frozenset({"TelemetryPanel", "RightPanel", "appBar",
                               "groupBox"})

    def _is_opaque(self, w: QtWidgets.QWidget) -> bool:
        """Whether this widget actually paints over the sky behind it."""
        if isinstance(w, (theme.TabCard, MiniGraph)):
            return True          # both fill their own rounded card
        if w.objectName() in self._OPAQUE_NAMES:
            return True
        if w.autoFillBackground() or w.testAttribute(QtCore.Qt.WA_OpaquePaintEvent):
            return True
        return "background" in w.styleSheet()

    def _sky_region(self) -> QtGui.QRegion:
        """The part of the window where sky is actually visible.

        A plain update() repaints the whole window, which made Qt recomposite
        every panel and label thirty times a second: 62% of a core to achieve
        13 fps, measured. Subtracting the opaque surfaces leaves only the gaps
        and margins the animation can be seen through, and repainting just
        those is cheap.

        The test has to be opacity, not parentage. Subtracting every direct
        child of `central` also removed the centre column — a bare QFrame with
        no background at all — so the stars inside its gaps were painted once
        and then never advanced again. Transparent containers are descended
        into instead, so their opaque children still earn the saving.
        """
        region = QtGui.QRegion(self.rect())
        central = self.centralWidget()
        if central is None:
            return region

        stack = [c for c in central.children()
                 if isinstance(c, QtWidgets.QWidget)]
        while stack:
            w = stack.pop()
            if not w.isVisible():
                continue
            if self._is_opaque(w):
                top_left = w.mapTo(self, QtCore.QPoint(0, 0))
                region -= QtGui.QRegion(QtCore.QRect(top_left, w.size()))
            else:
                stack.extend(c for c in w.children()
                             if isinstance(c, QtWidgets.QWidget))
        return region

    def _tick_background(self):
        self._scene.advance()
        self.update(self._sky_region())

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        # Drawn at the exact window size, so the nebulae and the planet sit
        # where the website puts them rather than being cropped by a scale.
        self._scene.draw(painter, self.width(), self.height())

    def closeEvent(self, event):
            # Telemetry rows only. A link row is written on every @@ packet,
            # so counting those meant the save prompt appeared after about a
            # second of runtime every single time, with nothing worth saving;
            # a prompt that always fires is one the operator learns to dismiss
            # without reading.
            has_data = self._telemetry_row_count > 0
            if has_data and not self._mission_saved:
                event.ignore()
                reply = ThemedMessageBox.question(
                    self,
                    "Save Mission Data?",
                    "You have unsaved mission data.",
                    informative="Do you want to save before closing?",
                    buttons=["Save & Exit", "Discard & Exit", "Cancel"],
                    danger_buttons=["Discard & Exit"],
                )
                if reply == "Save & Exit":
                    if self.user_email:
                        import widgets.auth_backend as auth
                        fb.log_logout()
                    self.right._save_mission()
                    QtWidgets.QApplication.quit()
                elif reply == "Discard & Exit":
                    if self.user_email:
                        import widgets.auth_backend as auth
                        fb.log_logout()
                    self._mission_saved = True
                    try:
                        if os.path.exists(self.temp_path):
                            os.remove(self.temp_path)
                    except Exception as e:
                        print("Temp delete error:", e)
                    QtWidgets.QApplication.quit()
                # if reply == "Cancel" → do nothing, no logout logged
            else:
                if self.user_email:
                    import widgets.auth_backend as auth
                    fb.log_logout()
                event.accept()
                QtWidgets.QApplication.quit()


# ══════════════════════════════════════════════════════════════════════════════
#  _ReconnectDialog  (private — used only by GroundStation)
# ══════════════════════════════════════════════════════════════════════════════
# A third copy of the palette used to be declared here, for one dialog.
_BG      = theme.DEEP
_SURFACE = theme.PANEL
_BORDER  = theme.LINE
_ACCENT  = theme.TEAL
_TEXT    = theme.CREAM
_MUTED   = theme.SLATE
_RED     = theme.ALARM
_GREEN   = theme.OK


class _ReconnectDialog(QtWidgets.QDialog):
    """
    Non-modal themed dialog shown when the serial link drops.
    Call set_reconnected() to switch it to a success state.
    """

    _STYLE = f"""
        QDialog {{
            background-color: {_BG};
            border: 1px solid {_BORDER};
            border-radius:{theme.R_CARD}px;
        }}
        QLabel {{
            color: {_TEXT};
            font-size: {theme.FS_BODY}px;
        }}
        QPushButton {{ min-width: 92px; text-align: center; }}
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Connection Status")
        self.setFixedSize(380, 220)
        self.setModal(False)
        self.setStyleSheet(self._STYLE)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(12)

        # Icon + title
        hdr = QtWidgets.QHBoxLayout()
        self._icon = QtWidgets.QLabel()
        self._icon.setPixmap(theme.icon("warning", theme.ALARM, 26).pixmap(26, 26))
        self._icon.setStyleSheet("margin-right:10px;")
        self._icon.setFixedWidth(38)
        self._title = QtWidgets.QLabel("COM PORT DISCONNECTED")
        self._title.setStyleSheet(f"color:{_RED}; font-size:14px; font-weight:bold;")
        hdr.addWidget(self._icon)
        hdr.addWidget(self._title)
        hdr.addStretch()
        root.addLayout(hdr)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet(f"color:{_BORDER};")
        root.addWidget(line)

        self._msg = QtWidgets.QLabel("The CubeSat connection was lost.")
        self._msg.setWordWrap(True)
        self._msg.setStyleSheet(f"color:{_TEXT}; font-size:13px;")
        root.addWidget(self._msg)

        self._sub = QtWidgets.QLabel("Trying to reconnect automatically…")
        self._sub.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        root.addWidget(self._sub)

        # Animated dots timer
        self._dot_count = 0
        self._dot_timer = QtCore.QTimer(self)
        self._dot_timer.timeout.connect(self._tick_dots)
        self._dot_timer.start(500)

        root.addStretch()

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _tick_dots(self):
        self._dot_count = (self._dot_count + 1) % 4
        dots = "." * self._dot_count
        self._sub.setText(f"Trying to reconnect automatically{dots}")

    def set_reconnected(self):
        self._dot_timer.stop()
        self._icon.setPixmap(theme.icon("check", theme.OK, 26).pixmap(26, 26))
        self._title.setText("LINK RESTORED")
        self._title.setStyleSheet(f"color:{_GREEN}; font-size:14px; font-weight:bold;")
        self._msg.setText("CubeSat connection successfully re-established.")
        self._sub.setText("Telemetry stream resuming.")
        QtCore.QTimer.singleShot(2000, self.close)
