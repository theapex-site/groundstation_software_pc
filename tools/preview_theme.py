"""
tools/preview_theme.py
──────────────────────
Render the themed widgets without a CubeSat, a COM port or a login.

    python tools/preview_theme.py

Shows the real TelemetryPanel fed with sample values, every button and input
variant, and the themed dialogs. Use it to judge a theme.py change in a
second rather than by building and flying the whole app.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PyQt5 import QtCore, QtWidgets

QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)

import theme
from widgets.dialogs import ThemedMessageBox
from widgets.telemetry_panel import TelemetryPanel


SAMPLE = dict(
    cubesat_id="PIXIE-1", date="07-10-2026", time="12:09:02",
    azimuth="134.2", elevation="41.8",
    batt_v="3.92", batt_soc="87", solar_v="4.21", current="0.54",
    power_val="2.14",
    sats="11", lat="13.017812", lon="77.514813", hdop="0.9",
    alt="412", speed="1.8", temp="47.2", press="1013", hum="41",
    gas_res="320000", core_temp="52.3",
)


def _group(title, *widgets):
    box = QtWidgets.QGroupBox(title)
    box.setObjectName("groupBox")
    lay = QtWidgets.QVBoxLayout(box)
    lay.setSpacing(9)
    for w in widgets:
        lay.addWidget(w)
    return box


def build(window):
    root = QtWidgets.QHBoxLayout(window)
    root.setContentsMargins(22, 22, 22, 22)
    root.setSpacing(16)

    # Two panels side by side: the same readings live, and frozen. The point
    # of the staleness work is that these must not look alike.
    for age, caption in ((0, "live"), (47, "no signal · readings withdrawn")):
        wrap = QtWidgets.QVBoxLayout()
        tag = QtWidgets.QLabel(caption.upper())
        tag.setObjectName("statChip")
        tag.setAlignment(QtCore.Qt.AlignCenter)
        wrap.addWidget(tag)

        panel = TelemetryPanel()
        panel.setFixedWidth(400)
        panel.update_telemetry(**SAMPLE)
        for line in ("CS_BOOT_OK", "GCS_CMD_C", "CS_IMG_READY", "CS_GPS_TIMEOUT"):
            panel.update_flag(line)
        panel.set_data_age(age)
        theme.shadow(panel)
        wrap.addWidget(panel)
        root.addLayout(wrap)

    right = QtWidgets.QWidget()
    col = QtWidgets.QVBoxLayout(right)
    col.setSpacing(14)

    title = QtWidgets.QLabel("PAYLOAD DOWNLINK")
    title.setObjectName("mainTitle")
    col.addWidget(title)

    # As the real downlink shows it: caption beside the bar, not under the
    # chunk where it was half-legible across the fill boundary.
    prog = QtWidgets.QHBoxLayout()
    bar = QtWidgets.QProgressBar()
    bar.setTextVisible(False)
    bar.setValue(63)
    caption = QtWidgets.QLabel("Packet 79/126  ·  16.5 KB  ·  about 14s left")
    caption.setObjectName("statChip")
    cancel = QtWidgets.QPushButton("Cancel downlink")
    cancel.setObjectName("dangerBtn")
    prog.addWidget(bar, 1)
    prog.addWidget(caption)
    prog.addWidget(cancel)
    col.addLayout(prog)

    # ── buttons, every state the sheet defines ────────────────────────────────
    primary = QtWidgets.QPushButton("Connect to COM4")
    primary.setObjectName("primaryBtn")
    normal = QtWidgets.QPushButton("Capture image")
    ghost = QtWidgets.QPushButton("Open in browser")
    ghost.setObjectName("ghostBtn")
    danger = QtWidgets.QPushButton("Restart system")
    danger.setObjectName("dangerBtn")
    off = QtWidgets.QPushButton("Disabled while busy")
    off.setEnabled(False)
    col.addWidget(_group("CONTROLS", primary, normal, ghost, danger, off))

    # ── inputs ────────────────────────────────────────────────────────────────
    email = QtWidgets.QLineEdit()
    email.setPlaceholderText("your@email.com")
    combo = QtWidgets.QComboBox()
    combo.addItems(["COM4  —  Silicon Labs CP210x USB to UART Bridge",
                    "COM1  —  Communications Port"])
    stay = QtWidgets.QCheckBox("Stay signed in on this computer")
    stay.setChecked(True)
    conf = QtWidgets.QDoubleSpinBox()
    conf.setRange(0.01, 1.0)
    conf.setSingleStep(0.05)
    conf.setValue(0.50)
    col.addWidget(_group("INPUTS", email, combo, stay, conf))

    # ── dialogs ───────────────────────────────────────────────────────────────
    info = QtWidgets.QPushButton("Show information dialog")
    info.clicked.connect(lambda: ThemedMessageBox.information(
        window, "Mission Saved", "Mission data saved successfully.",
        r"K:\APEX\apex_gcs_src\APEX_GCS_Saved\data\MissionInfo_2026-10-07_12-09-02.xlsx"))
    warn = QtWidgets.QPushButton("Show warning dialog")
    warn.clicked.connect(lambda: ThemedMessageBox.warning(
        window, "Partial Image Received", "Only part of the image was received.",
        "Received: 16,512 bytes  /  Expected: 26,304 bytes"))
    ask = QtWidgets.QPushButton("Show confirm dialog")
    ask.clicked.connect(lambda: ThemedMessageBox.question(
        window, "Restart System", "Reboot the onboard computer?",
        informative="Telemetry stops until PIXIE-1 finishes booting.",
        buttons=["Yes, restart", "Cancel"], danger_buttons=["Yes, restart"]))
    col.addWidget(_group("DIALOGS", info, warn, ask))

    col.addStretch()
    theme.shadow(right)
    root.addWidget(right, 1)


def main():
    app = QtWidgets.QApplication(sys.argv)
    theme.init(app)
    app.setStyleSheet(theme.qss())
    print("display: {}\nbody:    {}\nmono:    {}".format(
        theme.DISPLAY, theme.BODY, theme.MONO))

    window = QtWidgets.QWidget()
    window.setObjectName("previewRoot")
    # Scoped to the id: an unqualified background here would cascade onto every
    # child and outrank the application sheet.
    window.setStyleSheet(f"QWidget#previewRoot {{ background: {theme.DEEP}; }}")
    window.setWindowTitle("APEX GCS — theme preview")
    build(window)
    window.resize(940, 880)
    window.show()

    if len(sys.argv) > 1:          # headless capture: pass an output path
        QtCore.QTimer.singleShot(
            900, lambda: (window.grab().save(sys.argv[1]), app.quit()))

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
