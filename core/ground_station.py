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
import random
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
from widgets.rtg              import RTGWindow
from widgets.dialogs          import (Toast, PixieBubble, ThemedMessageBox,
                                     UserProfileDialog, SettingsDialog)

if getattr(sys, 'frozen', False):
    BASE_PATH = sys._MEIPASS
else:
    BASE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

ImageFile.LOAD_TRUNCATED_IMAGES = True

# Pixie's one-per-launch greeting, picked by time of day — "{name}" becomes
# ", <first name>" when one is known, or nothing at all otherwise, so every
# line still reads naturally either way.
_WELCOME_MORNING = [
    "Good morning{name}! Ready when you are.",
    "Morning{name}. Link's warm, coffee's on you.",
    "Rise and shine{name} — mission control's up.",
]
_WELCOME_AFTERNOON = [
    "Good afternoon{name}! Systems nominal.",
    "Afternoon{name}. Let's catch some signal.",
    "Back at it{name} — all green here.",
]
_WELCOME_EVENING = [
    "Good evening{name}! Everything's quiet and ready.",
    "Evening shift{name}. I've got the board.",
    "Welcome back{name} — link's holding steady.",
]
_WELCOME_NIGHT = [
    "Burning the midnight oil{name}?",
    "Still up{name}? So am I.",
    "Late one{name} — I'll keep watch with you.",
]
_WELCOME_FLAVOR = [
    "Houston, we have a Pixie{name}.",
    "Mission control is online{name}.",
    "All systems green{name}. Let's fly.",
    "Standing by for your orders{name}.",
]



class GroundStation(QtWidgets.QMainWindow):
    """Top-level application window for the APEX Ground Control Station."""

    # Seconds of '@@' silence before the link is called lost.
    LINK_SILENCE_S = 8.0

    # Signals emitted from the background image-receive thread
    image_done_signal = QtCore.pyqtSignal(bool, int, int)
    progress_signal   = QtCore.pyqtSignal(int, str)
    # _display_image() runs on that same background thread. A bare
    # QTimer.singleShot there — which is what this replaced — silently
    # never fires: the timer belongs to whatever thread creates it, and a
    # thread with no Qt event loop of its own never comes back to fire it.
    # A signal crossing threads auto-queues onto the receiver's thread,
    # which is the whole reason the other two above exist.
    payload_empty_signal = QtCore.pyqtSignal(bool)

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
        self.payload_empty_signal.connect(self.set_payload_empty)

        # ── Session accounting ────────────────────────────────────────────────
        self._session_start    = time.monotonic()
        self._packet_count     = 0
        self._last_packet_time = None     # None until the first packet lands
        self._recent_packets   = []       # monotonic stamps, for the rate
        self._last_link_time   = None     # last '@@' link frame

        # Commands sent since the last telemetry packet actually came back —
        # a run of these with silence in between usually means the CubeSat
        # itself is unpowered, not just a weak link, so it gets its own hint.
        self._cmds_since_reply = 0
        self.NO_REPLY_CMDS = 3
        self._last_sats = None    # None until the first GPS reading lands

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

        # ── Pixie's welcome message ───────────────────────────────────────────
        # Delayed past __init__: the window isn't shown yet (main.py calls
        # .show() right after this returns), and Pixie needs her own first
        # paint before a bubble can anchor to her.
        QtCore.QTimer.singleShot(1200, self._show_welcome_message)

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
        rtg_btn = QtWidgets.QPushButton("RTG")
        rtg_btn.setObjectName("rtgBtn")
        rtg_btn.setCursor(QtCore.Qt.PointingHandCursor)
        rtg_btn.setToolTip("Real-Time Graphs — every sensor channel on one "
                           "screen.\nShortcut: Ctrl+G")
        rtg_btn.setToolTipDuration(theme.TIP_MS)
        rtg_btn.setShortcut(QtGui.QKeySequence("Ctrl+G"))
        rtg_btn.setAccessibleName("Real-Time Graphs")
        rtg_btn.clicked.connect(self.open_rtg)
        row.addWidget(rtg_btn)

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

        # ── Right panel ───────────────────────────────────────────────────────
        # Built here, ahead of the centre column, so its map card can be
        # detached into the centre strip below. It is still placed in the
        # body layout after the centre column, so the visual order (left,
        # centre, right) is unaffected by construction order.
        self.right = RightPanel()
        self.right.command_signal.connect(self._send_command)
        self.right.setMinimumWidth(380)
        self.right.setMaximumWidth(480)

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

        # Image display: a stack, so the idle state lives inside the canvas
        # itself instead of a header collapsing above it — the canvas runs
        # full height from the top, like before that header existed.
        self._canvas = _Canvas()
        self._canvas.setToolTip(
            "Payload image downlink window.\n"
            "Displays images captured by the CubeSat camera.\n"
            "Updates when new image packets are received."
        )
        self._canvas_stack = self._canvas.stack

        self.image_label = QtWidgets.QLabel()
        self.image_label.setScaledContents(False)
        self.image_label.setAlignment(QtCore.Qt.AlignCenter)
        self.image_label.setStyleSheet("background: transparent; border: none;")

        self._payload_marks = _PayloadMarks()
        self._canvas_stack.addWidget(self.image_label)    # shown once a picture lands
        self._canvas_stack.addWidget(self._payload_marks)  # shown while empty
        self._canvas_stack.setCurrentWidget(self._payload_marks)

        # Clear button: top-right, only while a picture is actually on
        # screen — there is nothing to clear otherwise. A plain child of
        # the canvas, not the column like the progress bar, because this
        # sits fully inside the canvas's own rect (no straddling an edge,
        # so no clipping to work around).
        self._clear_image_btn = _ClearButton(self._canvas)
        self._clear_image_btn.setToolTip("Clear the displayed image")
        self._clear_image_btn.clicked.connect(lambda: self.set_payload_empty(True))
        self._clear_image_btn.hide()

        # Pixie: sits in the canvas's other corner while idle, fading out
        # and back in with the marks — same empty-state cast, different
        # spot so it doesn't crowd the clear button above it.
        self._pixie = _FadingImage(
            os.path.join(BASE_PATH, "images", "pixie.png"), 76, self._canvas)
        self._pixie.setToolTip("Pixie")

        self._canvas.resized.connect(self._position_canvas_overlays)
        self._position_canvas_overlays()

        cl.addWidget(self._canvas, 5)
        # Just enough clearance for the progress bar to straddle the
        # canvas's bottom border.
        cl.addSpacing(14)

        # The transfer progress bar floats on that border instead of sitting
        # in a row of its own — parented to the column, not the canvas, so
        # the half that overlaps outside the canvas's rect actually paints
        # (a widget can't render past its own parent's edge in Qt).
        self.progress = QtWidgets.QProgressBar(centre)
        self.progress.setObjectName("downlinkBar")
        # Qt's own bar text goes blank for an indeterminate range (0, 0) —
        # the header-wait phase — no matter what setFormat() says. A plain
        # label on top, which we drive ourselves, shows the caption in both
        # phases alike.
        self.progress.setTextVisible(False)
        self.progress.setMaximum(100)
        self.progress.setValue(0)
        self.progress.hide()
        self.progress_caption = QtWidgets.QLabel(centre)
        self.progress_caption.setAlignment(QtCore.Qt.AlignCenter)
        self.progress_caption.setStyleSheet(
            f"background: transparent; border: none; color: {theme.CREAM};"
            f" font-family: '{theme.MONO}'; font-size: {theme.FS_MICRO}px;"
        )
        self.progress_caption.hide()
        self._canvas.resized.connect(self._position_progress_bar)

        # Under the payload frame: the map and the event log. GPS stays on
        # the telemetry panel where it belongs; this is its old spot, now
        # held by the card that actually reads as a location at a glance.
        # No titles above them — headless cards reading as plain panels,
        # given more height since there's nothing else competing for it.
        self.graph_popups: list[GraphPopup] = []
        strip = QtWidgets.QHBoxLayout()
        strip.setSpacing(theme.GAP)
        map_card = self.right.detach_map_card()
        flag_card = self.telemetry.detach_flag_card()
        strip.addWidget(map_card, 1)
        strip.addWidget(flag_card, 1)
        cl.addLayout(strip, 2)

        body.addWidget(self.right, 0)

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
        # Every sensor channel's history, kept whether or not RTG is open.
        self._channels: dict[str, list[tuple[str, float]]] = {}
        self.rtg_window = None

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
                        if line == "CS_READY":
                            self.show_toast("CubeSat is online!", "success")
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

            try:
                sats = int(float(parts[11]))
                if self._last_sats and sats == 0:
                    self.show_toast("GPS fix lost", "warn")
                self._last_sats = sats
            except ValueError:
                pass

            pitch, roll, yaw = float(parts[20]), float(parts[21]), float(parts[22])
            self.right.cubesat_3d.update_orientation(pitch, roll, yaw)
            self.right.imu_labels["Pitch"].setText(f"{pitch:.2f} °")
            self.right.imu_labels["Roll"].setText(f"{roll:.2f} °")
            self.right.imu_labels["Yaw"].setText(f"{yaw:.2f} °")

            gas_res = float(parts[23])
            iaq, eco2, voc = TelemetryPanel.calculate_air_quality(gas_res)
            self._record_channels({
                "batt_v":   float(parts[6]),
                "batt_soc": float(parts[7]),
                "solar_v":  float(parts[8]),
                "current":  float(parts[9]),
                "power":    float(parts[10]),
                "altitude": float(parts[15]),
                "speed":    float(parts[16]),
                "azimuth":  float(parts[4]),
                "elevation": float(parts[5]),
                "temp":     float(parts[17]),
                "pressure": float(parts[18]),
                "humidity": float(parts[19]),
                "aqi":      float(iaq),
                "eco2":     float(eco2),
                "voc":      float(voc),
                "gas_res":  gas_res,
            }, parts[3])

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

                # Telemetry is expected to pause during an image downlink —
                # silence there is normal, not a sign anything is wrong.
                if not self.image_mode:
                    self._cmds_since_reply += 1
                    if self._cmds_since_reply == self.NO_REPLY_CMDS:
                        self.show_toast(
                            "No Heartbeat from CS — check CubeSat power", "warn")

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
        self._position_progress_bar()
        self.progress.show()
        self.progress_caption.show()
        self.progress.raise_()
        self.progress_caption.raise_()
        # The branding fades out the moment the operator presses Transmit
        # Image, not only once a picture actually lands — a transfer can
        # take over two minutes, and the canvas reads as busy/declutterd
        # for the whole wait rather than keeping the idle marks up as if
        # nothing were happening. It comes back in _show_image_result() if
        # the transfer ends without ever producing a picture.
        self.image_label.clear()

        def _reveal_blank_canvas():
            self._canvas_stack.setCurrentWidget(self.image_label)
            self._position_canvas_overlays()

        self._payload_marks.fade(False, on_finished=_reveal_blank_canvas)
        self._pixie.fade(False)
        # Transmit Image becomes Cancel for the duration of the transfer —
        # the same button the operator just pressed, rather than a separate
        # control next to the progress bar.
        self.right.set_button_override(
            "tx_image", text="Cancel Transmit Image", icon_name="cross",
            danger=True, slot=self._cancel_downlink)
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
        self.right.command_buttons["tx_image"].setEnabled(False)
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
            # A picture has arrived, so the branding gets out of its way.
            # This runs on the background receive thread — emitting the
            # signal, not calling set_payload_empty() directly, is what
            # actually gets it onto the GUI thread where the widgets live.
            self.payload_empty_signal.emit(False)
        except Exception as e:
            print("Display render error:", e)

    def _show_image_result(self, success: bool, received: int, expected: int):
        self.progress.hide()
        self.progress_caption.hide()
        # Back to Transmit Image now that the transfer is over, whatever
        # the outcome — success, partial, failed or cancelled.
        self.right.set_button_override("tx_image")
        elapsed = time.time() - (self.image_start_time or time.time())

        if not success or received == 0:
            # No picture came out of this transfer — cancelled, timed out,
            # or nothing arrived — so there is nothing for the canvas to
            # show. Bring the branding back rather than leaving it blank.
            self.set_payload_empty(True)

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
            self.show_toast("Image transfer failed — no data received", "error")

    def _update_progress(self, value: int, text: str):
        if self.progress.maximum() == 0 and self.total_packets:
            self.progress.setRange(0, self.total_packets)
        self.progress.setValue(value)
        self.progress_caption.setText(text)

    # ══════════════════════════════════════════════════════════════════════════
    #  Link status
    # ══════════════════════════════════════════════════════════════════════════
    def update_link_status(self, connected: bool, snr=None, rssi=None):
        was_connected = self.is_connected
        self.is_connected = connected
        if was_connected and not connected:
            # Only on the actual drop, not every frame that arrives while
            # already disconnected — otherwise this would fire repeatedly.
            self.show_toast("Signal lost — check your link", "error")
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
        self._cmds_since_reply = 0

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
        # While Pixie is actually on screen, she says it herself instead —
        # the same message, as a speech bubble over her corner of the
        # canvas rather than a toast in the window's.
        if self._pixie.is_visible():
            PixieBubble(self._pixie, text, kind)
        else:
            Toast(self, text, kind)

    def _welcome_name(self) -> str:
        """First name for the greeting — the cached profile's real name if
        there is one, else a best-effort guess from the login email, else
        nothing (the templates all read fine with no name too)."""
        profile = fb.current_profile()
        name = (profile or {}).get("name", "")
        if isinstance(name, str) and name.strip():
            return name.strip().split()[0]

        local = (self.user_email or "").split("@")[0]
        cleaned = local.replace(".", " ").replace("_", " ").strip()
        if cleaned and cleaned.replace(" ", "").isalpha():
            return cleaned.split()[0].capitalize()
        return ""

    def _show_welcome_message(self):
        hour = datetime.now().hour
        if 5 <= hour < 12:
            pool = _WELCOME_MORNING
        elif 12 <= hour < 17:
            pool = _WELCOME_AFTERNOON
        elif 17 <= hour < 21:
            pool = _WELCOME_EVENING
        else:
            pool = _WELCOME_NIGHT
        if random.random() < 0.2:
            pool = _WELCOME_FLAVOR

        name = self._welcome_name()
        name_part = f", {name}" if name else ""
        self.show_toast(random.choice(pool).format(name=name_part), "info")

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

    # ── payload frame ─────────────────────────────────────────────────────────
    def set_payload_empty(self, empty: bool):
        """Swap the canvas between the picture and the idle brand marks,
        fading the marks (and Pixie) out before the swap and back in after
        it, rather than the stack just cutting between pages. Also what the
        clear button calls — "empty" is exactly the state it puts the
        canvas back into."""
        if empty:
            self.image_label.clear()
            self._canvas_stack.setCurrentWidget(self._payload_marks)
            self._payload_marks.fade(True)
            self._pixie.fade(True)
            self._clear_image_btn.hide()
            self._position_canvas_overlays()
        else:
            def _reveal_image():
                self._canvas_stack.setCurrentWidget(self.image_label)
                self._position_canvas_overlays()
            self._payload_marks.fade(False, on_finished=_reveal_image)
            self._pixie.fade(False)
            self._clear_image_btn.show()
            self._position_canvas_overlays()
            self._clear_image_btn.raise_()

    def _position_progress_bar(self):
        """Keep the downlink bar pinned to the canvas's bottom border.

        The bar is parented to the centre column, not the canvas (Qt would
        clip it to the canvas's rect otherwise), so it needs the canvas's
        geometry — already in the column's own coordinate space, since both
        are direct children of it — rather than its own.
        """
        geo = self._canvas.geometry()
        h = self.progress.sizeHint().height() or 26
        bar_rect = QtCore.QRect(
            geo.x(), geo.y() + geo.height() - h // 2, geo.width(), h)
        self.progress.setGeometry(bar_rect)
        self.progress_caption.setGeometry(bar_rect)
        self.progress.raise_()
        self.progress_caption.raise_()

    def _position_canvas_overlays(self):
        """Pin the clear button and Pixie to the canvas's own corners —
        both are its children, so this is just the canvas's local rect,
        no coordinate conversion needed.

        Also re-raises both every time: the canvas's QStackedLayout raises
        whichever page is current whenever it switches (same thing that
        buried the RTG progress bar under a stack page once before), and
        it switches pages well after set_payload_empty() first shows these
        — asserting the raise here, instead of once at switch time, is
        what actually survives that.
        """
        margin = 10
        w, h = self._canvas.width(), self._canvas.height()
        self._clear_image_btn.move(
            w - self._clear_image_btn.width() - margin, margin)
        self._pixie.move(
            w - self._pixie.width() - margin, h - self._pixie.height() - margin)
        self._clear_image_btn.raise_()
        self._pixie.raise_()

    # ── channels / RTG ────────────────────────────────────────────────────────
    def _record_channels(self, values: dict, timestamp: str):
        """Keep every channel's history whether or not RTG is open.

        Recording only while the window exists would mean opening RTG mid-
        mission showed four empty-looking graphs and threw away everything
        before that point.
        """
        cap = settings.get("graphs/popup_points")
        for key, value in values.items():
            series = self._channels.setdefault(key, [])
            series.append((timestamp, value))
            if len(series) > cap:
                del series[:-cap]
        if self.rtg_window is not None:
            self.rtg_window.add_values(values, timestamp)

    def open_rtg(self):
        # Maximized, not showFullScreen(): fullscreen takes over the whole
        # display — no window chrome, no taskbar entry, nothing to alt-tab
        # to — which read as RTG replacing the main window rather than
        # opening as its own screen alongside it.
        if self.rtg_window is None:
            self.rtg_window = RTGWindow(self)
            self.rtg_window.seed(self._channels)
            self.rtg_window.destroyed.connect(self._forget_rtg)
            self.rtg_window.setAttribute(QtCore.Qt.WA_DeleteOnClose)
            self.rtg_window.showMaximized()
        else:
            self.rtg_window.showMaximized()
            self.rtg_window.raise_()
            self.rtg_window.activateWindow()

    def _forget_rtg(self, *_):
        self.rtg_window = None

    # ── settings ──────────────────────────────────────────────────────────────
    def _apply_display_settings(self):
        """Start, stop or re-rate the background animation."""
        self._bg_timer.stop()
        if settings.animate():
            self._bg_timer.start(settings.frame_ms())
        else:
            self.update()

    def _apply_graph_settings(self):
        """Resize the sample windows on the RTG tiles and any open popup."""
        mini = settings.get("graphs/mini_points")
        popup = settings.get("graphs/popup_points")
        for series in self._channels.values():
            if len(series) > popup:
                del series[:-popup]
        if self.rtg_window is not None:
            for tile in self.rtg_window.tiles.values():
                tile.max_points = mini
                if len(tile.data) > mini:
                    del tile.data[:-mini]
                tile.update()
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


class _Canvas(QtWidgets.QFrame):
    """The payload image frame: just the two-page stack (picture / idle
    marks).

    The downlink progress bar is NOT a child of this frame — Qt clips a
    child's painting to its parent's rectangle, so a bar meant to straddle
    this frame's own bottom border would have the straddling half silently
    cut off. GroundStation parents the bar one level up instead, on the
    column that holds both this canvas and the strip below it, and listens
    to `resized` to keep it pinned to the border.
    """

    resized = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"background-color:{theme.DEEP}; border:1px solid {theme.LINE};"
            f" border-radius:{theme.R_CARD}px;"
        )
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Expanding)

        self.stack = QtWidgets.QStackedLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class _ClearButton(QtWidgets.QWidget):
    """A small square "x" chip — paints itself rather than using
    QPushButton's icon, because that icon-layout logic is built around an
    icon sitting next to text and visibly mis-centres a lone icon on a
    button this small. Drawing the cross from the widget's own centre
    guarantees it actually lands there."""

    clicked = QtCore.pyqtSignal()
    SIZE = 30
    ARM = 5.0   # half-length of each stroke of the ×

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._hover = False

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = QtCore.QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, theme.R_SM, theme.R_SM)
        p.fillPath(path, QtGui.QColor(theme.ALARM_FILL if self._hover else "#0a0a10"))
        pen = QtGui.QPen(QtGui.QColor(theme.ALARM))
        pen.setWidthF(1.5)
        p.strokePath(path, pen)

        cx, cy, s = rect.center().x(), rect.center().y(), self.ARM
        pen = QtGui.QPen(QtGui.QColor(theme.ALARM))
        pen.setWidthF(1.6)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QtCore.QPointF(cx - s, cy - s), QtCore.QPointF(cx + s, cy + s))
        p.drawLine(QtCore.QPointF(cx + s, cy - s), QtCore.QPointF(cx - s, cy + s))
        p.end()


class _FadingImage(QtWidgets.QLabel):
    """A static image with its own fade(shown) — Pixie, sitting in the
    payload canvas's corner, fading out in step with the marks when a
    downlink starts and back in when the canvas empties.

    A plain sibling of _PayloadMarks rather than living inside it: Qt does
    not composite a child widget's own opacity effect correctly once an
    ancestor carries one too (see _PayloadMarks's own comment on this) —
    keeping this at the same level, not nested under something else with
    an effect, is what keeps it simple.
    """

    def __init__(self, image_path: str, max_side: int, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent; border: none;")
        src = QtGui.QPixmap(image_path)
        if not src.isNull():
            self.setPixmap(src.scaled(
                max_side, max_side,
                QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            self.setFixedSize(self.pixmap().size())
        else:
            self.setFixedSize(0, 0)

        self._fx = QtWidgets.QGraphicsOpacityEffect(self)
        self._fx.setOpacity(1.0)
        self.setGraphicsEffect(self._fx)
        self._fade = QtCore.QPropertyAnimation(self._fx, b"opacity", self)
        self._fade.setDuration(280)
        self._fade.setEasingCurve(QtCore.QEasingCurve.InOutCubic)

    def fade(self, shown: bool, on_finished=None):
        try:
            self._fade.finished.disconnect()
        except TypeError:
            pass   # nothing was connected
        if on_finished is not None:
            self._fade.finished.connect(on_finished)
        self._fade.stop()
        self._fade.setStartValue(self._fx.opacity())
        self._fade.setEndValue(1.0 if shown else 0.0)
        self._fade.start()

    def is_visible(self) -> bool:
        """Whether Pixie is actually on screen right now, as opposed to
        mid-fade-out during a downlink — the cue for whether a status
        message should speak through her or fall back to a plain Toast."""
        return self._fx.opacity() > 0.5


class _PayloadMarks(QtWidgets.QWidget):
    """The three brand marks and the idle caption, shown while the payload
    canvas is empty.

    These used to sit in a header above the canvas that collapsed out of the
    way when a picture arrived. That cost the canvas its full height even
    once empty again — and put the marks outside the frame they were meant
    to announce. Living inside the canvas, as one of its two stacked pages,
    they take exactly the space the picture would, disappear the instant a
    downlink starts, and reappear the instant the frame goes empty again.
    They sit near the top of the canvas rather than centred in it, so the
    lower half stays clear for the progress bar overlapping the border.
    """

    MAX_MARK = 220
    TOP_PAD = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        col = QtWidgets.QVBoxLayout(self)
        # No left/right margin here: the row below supplies its own edge
        # gaps as stretches, the same as the gaps between the three marks.
        # A fixed margin here on top of those stretches made the edges read
        # wider than the gaps between the logos — "even" only by accident.
        col.setContentsMargins(0, self.TOP_PAD, 0, 24)
        col.setSpacing(18)

        # Translucent at rest, like a watermark sitting behind content
        # rather than competing with it. The alpha is baked straight into
        # each pixmap (see _faded()) rather than a QGraphicsOpacityEffect on
        # this row: Qt does not composite a child widget's own opacity
        # effect correctly once an ancestor (this whole widget, for the
        # fade in/out below) carries one too — nested effects render as a
        # blank widget, not a doubly-faded one.
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(0)
        self._apex = QtWidgets.QLabel()
        self._gcs = QtWidgets.QLabel()
        self._cube = QtWidgets.QLabel()
        for lbl in (self._apex, self._gcs, self._cube):
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            lbl.setStyleSheet("background: transparent; border: none;")
        row.addStretch(1)
        row.addWidget(self._apex, 0, QtCore.Qt.AlignVCenter)
        row.addStretch(1)
        row.addWidget(self._gcs, 0, QtCore.Qt.AlignVCenter)
        row.addStretch(1)
        row.addWidget(self._cube, 0, QtCore.Qt.AlignVCenter)
        row.addStretch(1)
        col.addLayout(row, 0)

        # The old empty-state line, back under the marks instead of being
        # the only thing in the frame.
        self._caption = QtWidgets.QLabel(
            f"<div align='center' style='color:{theme.SLATE}'>"
            f"<span style='font-size:{theme.FS_HEAD}px; font-weight:600;'>"
            "No payload image yet</span><br><br>"
            f"<span style='font-size:{theme.FS_BODY}px;'>Press "
            f"<span style='color:{theme.TEAL}'>Transmit Image</span> to downlink "
            "the latest capture</span></div>"
        )
        self._caption.setAlignment(QtCore.Qt.AlignCenter)
        self._caption.setStyleSheet("background: transparent; border: none;")
        col.addWidget(self._caption, 0)
        col.addStretch(1)

        self._apex_src = QtGui.QPixmap(
            os.path.join(BASE_PATH, "images", "Apex Logo.png"))
        self._gcs_src = QtGui.QPixmap(
            os.path.join(BASE_PATH, "images", "GCS_img.png"))
        # The supplied cut-out, matted off its white background so it does
        # not sit in a white square on a black frame.
        self._cube_src = QtGui.QPixmap(
            os.path.join(BASE_PATH, "images", "cubesat_nobg.png"))

        # Fades the whole widget out when a downlink starts and back in
        # once the canvas is empty again — instead of the stack just
        # snapping between pages, which this sits in front of (see
        # GroundStation.set_payload_empty).
        self._fx = QtWidgets.QGraphicsOpacityEffect(self)
        self._fx.setOpacity(1.0)
        self.setGraphicsEffect(self._fx)
        self._fade = QtCore.QPropertyAnimation(self._fx, b"opacity", self)
        self._fade.setDuration(280)
        self._fade.setEasingCurve(QtCore.QEasingCurve.InOutCubic)

        self._rescale()

    def fade(self, shown: bool, on_finished=None):
        """Animate to fully shown or fully hidden. `on_finished` fires once,
        after the animation completes — GroundStation uses it to only swap
        the canvas's stacked page once this has actually faded out, rather
        than cutting to the picture mid-fade."""
        try:
            self._fade.finished.disconnect()
        except TypeError:
            pass   # nothing was connected
        if on_finished is not None:
            self._fade.finished.connect(on_finished)
        self._fade.stop()
        self._fade.setStartValue(self._fx.opacity())
        self._fade.setEndValue(1.0 if shown else 0.0)
        self._fade.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    MARK_OPACITY = 0.5

    @staticmethod
    def _faded(pix: QtGui.QPixmap, opacity: float) -> QtGui.QPixmap:
        """Bake `opacity` into the pixmap's own alpha, painted via a plain
        QPainter rather than a QGraphicsEffect — see the comment above
        where this is used for why."""
        if pix.isNull():
            return pix
        img = QtGui.QImage(pix.size(), QtGui.QImage.Format_ARGB32_Premultiplied)
        img.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(img)
        p.setOpacity(opacity)
        p.drawPixmap(0, 0, pix)
        p.end()
        result = QtGui.QPixmap.fromImage(img)
        result.setDevicePixelRatio(pix.devicePixelRatio())
        return result

    def _rescale(self):
        """Size the three marks to the canvas, floored and capped so they
        read at both a cramped and a wide window."""
        side = max(min(self.height() - 48, self.MAX_MARK), 40)

        for label, source in ((self._apex, self._apex_src),
                              (self._cube, self._cube_src)):
            if not source.isNull():
                scaled = source.scaled(
                    side, side,
                    QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                label.setPixmap(self._faded(scaled, self.MARK_OPACITY))
        if not self._gcs_src.isNull():
            scaled = self._gcs_src.scaledToHeight(
                int(side * 0.9), QtCore.Qt.SmoothTransformation)
            self._gcs.setPixmap(self._faded(scaled, self.MARK_OPACITY))


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
