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

from config import (
    BAUD_RATE, SERIAL_TIMEOUT, SERIAL_POLL_MS,
    PACKET_SIZE, IMAGE_RX_TIMEOUT, FILE_INFO_TIMEOUT, IMAGE_MODE_TIMEOUT,
)
from widgets.telemetry_panel import TelemetryPanel
from widgets.right_panel      import RightPanel
from widgets.graphs           import MiniGraph, GraphPopup
from widgets.dialogs          import Toast, ThemedMessageBox, UserProfileDialog

if getattr(sys, 'frozen', False):
    BASE_PATH = sys._MEIPASS
else:
    BASE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

ImageFile.LOAD_TRUNCATED_IMAGES = True




class GroundStation(QtWidgets.QMainWindow):
    """Top-level application window for the APEX Ground Control Station."""

    # Signals emitted from the background image-receive thread
    image_done_signal = QtCore.pyqtSignal(bool, int, int)
    progress_signal   = QtCore.pyqtSignal(int, str)

    def __init__(self, com_port: str = None, user_email: str = ""):
        super().__init__()
        self.com_port   = com_port
        self.user_email = user_email
        self.setWindowTitle("APEX Ground Control Station")
        self.resize(1900, 1080)

        # ── Background image ──────────────────────────────────────────────────
        self.bg_pixmap = QtGui.QPixmap(
            os.path.join(BASE_PATH, "images", "BG1.jpg")
        )

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

        # ── Build UI ──────────────────────────────────────────────────────────
        self._build_ui()

        # ── Link status blink ─────────────────────────────────────────────────
        self.is_connected = True
        self.blink_state  = True
        self.blink_timer  = QtCore.QTimer()
        self.blink_timer.timeout.connect(self._blink_link_status)

        # ── Serial connection ─────────────────────────────────────────────────
        self._open_serial()
        self.serial_timer = QtCore.QTimer()
        self.serial_timer.timeout.connect(self._read_serial)
        self.serial_timer.start(SERIAL_POLL_MS)

    # ══════════════════════════════════════════════════════════════════════════
    #  UI construction
    # ══════════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        root = QtWidgets.QHBoxLayout(central)

        # ── Left: telemetry panel ─────────────────────────────────────────────
        self.telemetry = TelemetryPanel()
        self.telemetry.setFixedWidth(420)
        self.telemetry.settings_clicked.connect(self._open_user_profile)
        root.addWidget(self.telemetry)

        # ── Centre ────────────────────────────────────────────────────────────
        centre = QtWidgets.QFrame()
        cl = QtWidgets.QVBoxLayout(centre)
        cl.setSpacing(5)
        cl.setContentsMargins(10, 5, 10, 5)
        root.addWidget(centre, 1)

        _base = BASE_PATH

        # Header row
        header_row = QtWidgets.QHBoxLayout()
        logo = QtWidgets.QLabel()
        logo.setPixmap(
            QtGui.QPixmap(os.path.join(_base, "images", "Apex Logo.png"))
            .scaled(250, 250, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        )
        logo.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        gcs_img_lbl = QtWidgets.QLabel()
        gcs_img_lbl.setAlignment(QtCore.Qt.AlignCenter)
        gcs_img_lbl.setPixmap(
            QtGui.QPixmap(os.path.join(_base, "images", "GCS_img.png"))
            .scaledToWidth(420, QtCore.Qt.SmoothTransformation)
        )
        gcs_img_lbl.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)

        cubesat_lbl = QtWidgets.QLabel()
        cubesat_lbl.setPixmap(
            QtGui.QPixmap(os.path.join(_base, "images", "cubesat_img.png"))
            .scaled(250, 250, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        )
        cubesat_lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        header_row.addWidget(logo)
        header_row.addStretch()
        header_row.addWidget(gcs_img_lbl)
        header_row.addStretch()
        header_row.addWidget(cubesat_lbl)
        cl.addLayout(header_row)

        # COM label
        self.com_label = QtWidgets.QLabel(f"COM: {self.com_port}")
        self.com_label.setAlignment(QtCore.Qt.AlignCenter)
        self.com_label.setStyleSheet("color:#38bdf8; font-size:12px; font-weight:bold;")
        cl.addWidget(self.com_label)

        # Signal row
        signal_row = QtWidgets.QHBoxLayout()
        self.rssi_label  = QtWidgets.QLabel("RSSI: -- dBm")
        self.link_status = QtWidgets.QLabel("● LINK NOT CONNECTED")
        self.snr_label   = QtWidgets.QLabel("SNR: -- dB")
        self.rssi_label.setStyleSheet("color:#94a3b8; font-size:11px;")
        self.snr_label.setStyleSheet("color:#94a3b8; font-size:11px;")
        self.link_status.setStyleSheet("color:#ef4444; font-size:11px; font-weight:bold;")
        signal_row.setSpacing(20)
        for w in (self.rssi_label, self.link_status, self.snr_label):
            signal_row.addWidget(w)
        sig_container = QtWidgets.QWidget()
        sig_container.setLayout(signal_row)
        cl.addWidget(sig_container, alignment=QtCore.Qt.AlignCenter)

        # Payload heading
        payload_title = QtWidgets.QLabel("PAYLOAD IMAGE DOWNLINK")
        payload_title.setAlignment(QtCore.Qt.AlignCenter)
        payload_title.setStyleSheet("font-size:18px; color:#7dd3fc; font-weight:bold;")
        cl.addWidget(payload_title)

        # Progress bar
        self.progress = QtWidgets.QProgressBar()
        self.progress.setMaximum(100)
        self.progress.setFormat("Packets: 0/100 (0%)")
        cl.addWidget(self.progress)

        # Image display
        self.image_label = QtWidgets.QLabel("NO IMAGE RECEIVED")
        self.image_label.setScaledContents(False)
        self.image_label.setAlignment(QtCore.Qt.AlignCenter)
        self.image_label.setStyleSheet(
            "background-color:#000000; border:2px solid #1e293b; border-radius:15px;"
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

        # Mini graphs
        graph_row = QtWidgets.QHBoxLayout()
        graph_row.setSpacing(5)
        self.alt_graph  = MiniGraph("Altitude",         "m")
        self.temp_graph = MiniGraph("Temperature",      "°C")
        self.batt_graph = MiniGraph("Battery Voltage",  "V")
        self.iaq_graph  = MiniGraph("AQI Index",        "")
        self.graph_popups: list[GraphPopup] = []
        for g in (self.alt_graph, self.temp_graph, self.batt_graph, self.iaq_graph):
            g.clicked.connect(self._open_graph_popup)
            graph_row.addWidget(g)
        cl.addLayout(graph_row, 2)

        # ── Right panel ───────────────────────────────────────────────────────
        self.right = RightPanel()
        self.right.command_signal.connect(self._send_command)
        self.right.setFixedWidth(450)
        root.addWidget(self.right)

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
    #  Serial
    # ══════════════════════════════════════════════════════════════════════════
    def _open_serial(self):
        try:
            self.serial_port = serial.Serial(
                port=self.com_port, baudrate=BAUD_RATE, timeout=SERIAL_TIMEOUT
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
            port=self.com_port, baudrate=BAUD_RATE, timeout=SERIAL_TIMEOUT
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
            self.show_toast("Serial reconnected ✓")
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
        self.progress.setValue(0)
        self.serial_port.reset_input_buffer()
        threading.Thread(target=self._receive_image_chunked, daemon=True).start()

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
                    self.progress_signal.emit(
                        self.received_packets,
                        f"Packets: {self.received_packets}/{self.total_packets} | {total_bytes} bytes",
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
        if success:            
            if expected > 0 and received < expected * 0.95:
                ThemedMessageBox.warning(
                    self, "Partial Image Received",
                    "Only part of the image was received.",
                    informative=f"Received: {received} bytes  /  Expected: {expected} bytes",
                )                
        else:
            ThemedMessageBox.warning(
                self, "Image Transfer Failed",
                "No image data received from CubeSat.",
                informative="Check link quality or retry transmission.",
            )

    def _update_progress(self, value: int, text: str):
        self.progress.setValue(value)
        self.progress.setFormat(text)

    # ══════════════════════════════════════════════════════════════════════════
    #  Link status
    # ══════════════════════════════════════════════════════════════════════════
    def update_link_status(self, connected: bool, snr=None, rssi=None):
        self.is_connected = connected
        if connected:
            self.blink_timer.stop()
            self.link_status.setText("● LINK STABLE")
            self.link_status.setStyleSheet("color:#22c55e; font-size:11px; font-weight:bold;")
            if snr  is not None: self.snr_label.setText(f"SNR: {snr} dB")
            if rssi is not None: self.rssi_label.setText(f"RSSI: {rssi} dBm")
        else:
            self.link_status.setText("● SIGNAL LOST  ")
            self.blink_timer.start(500)
            self.snr_label.setText("SNR: --")
            self.rssi_label.setText("RSSI: --")
        ts = datetime.now().strftime("%H:%M:%S")
        self._append_link_row(ts, connected,
                              rssi if rssi is not None else "--",
                              snr  if snr  is not None else "--")

    def _blink_link_status(self):
        if not self.is_connected:
            colour = "#ef4444" if self.blink_state else "#020617"
            self.link_status.setStyleSheet(f"color:{colour}; font-size:11px; font-weight:bold;")
            self.blink_state = not self.blink_state

    # ══════════════════════════════════════════════════════════════════════════
    #  Misc
    # ══════════════════════════════════════════════════════════════════════════

    # ── User profile / settings ───────────────────────────────────────────────
    def _open_user_profile(self):
        """Open the UserProfileDialog when the ⚙ button is clicked."""
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

    def show_toast(self, text: str):
        self.toast = Toast(self, text)

    def _open_graph_popup(self, graph_widget: MiniGraph):
        popup = GraphPopup(graph_widget)
        popup.show()
        self.graph_popups.append(popup)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        if not self.bg_pixmap.isNull():
            scaled = self.bg_pixmap.scaled(
                self.size(),
                QtCore.Qt.KeepAspectRatioByExpanding,
                QtCore.Qt.SmoothTransformation,
            )
            painter.drawPixmap(0, 0, scaled)

    def closeEvent(self, event):
            has_data = (
                self._telemetry_row_count > 0
                or self._flag_row_count > 0
                or self._link_row_count > 0
            )
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
_BG      = "#020617"
_SURFACE = "#0f172a"
_BORDER  = "#1e293b"
_ACCENT  = "#38bdf8"
_TEXT    = "#e2e8f0"
_MUTED   = "#94a3b8"
_RED     = "#ef4444"
_GREEN   = "#22c55e"


class _ReconnectDialog(QtWidgets.QDialog):
    """
    Non-modal themed dialog shown when the serial link drops.
    Call set_reconnected() to switch it to a success state.
    """

    _STYLE = f"""
        QDialog {{
            background-color: {_BG};
            border: 1px solid {_BORDER};
            border-radius: 10px;
        }}
        QLabel {{
            font-family: Consolas;
            color: {_TEXT};
            font-size: 12px;
        }}
        QPushButton {{
            background: {_SURFACE};
            color: {_ACCENT};
            border: 1px solid {_ACCENT};
            padding: 6px 18px;
            border-radius: 5px;
            font-size: 12px;
            min-width: 80px;
            font-family: Consolas;
        }}
        QPushButton:hover {{
            background: {_ACCENT};
            color: {_BG};
        }}
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
        self._icon = QtWidgets.QLabel("⚠")
        self._icon.setStyleSheet(f"color:{_RED}; font-size:28px; margin-right:10px;")
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
        self._icon.setText("✅")
        self._icon.setStyleSheet(f"color:{_GREEN}; font-size:28px; margin-right:10px;")
        self._title.setText("LINK RESTORED")
        self._title.setStyleSheet(f"color:{_GREEN}; font-size:14px; font-weight:bold;")
        self._msg.setText("CubeSat connection successfully re-established.")
        self._sub.setText("Telemetry stream resuming.")
        QtCore.QTimer.singleShot(2000, self.close)
