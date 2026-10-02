"""
config.py
─────────
Global constants, style sheet, and shared settings for APEX GCS.
Edit this file to change colours, fonts, thresholds, or baud rate.
"""
import os
import sys

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS   # PyInstaller temp folder
    except AttributeError:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


IMAGES_DIR = resource_path("images")

APEX_LOGO  = os.path.join(IMAGES_DIR, "Apex Logo.png")
BG_IMAGE   = os.path.join(IMAGES_DIR, "BG1.jpg")
title      = os.path.join(IMAGES_DIR, "GCS_img.png")


# ── Unicode helpers ────────────────────────────────────────────────────────────
DEGREE = "\u00B0"

# ── Serial ────────────────────────────────────────────────────────────────────
BAUD_RATE      = 115200
SERIAL_TIMEOUT = 0.1      # seconds
SERIAL_POLL_MS = 50       # QTimer interval for reading serial

# ── Image downlink ────────────────────────────────────────────────────────────
PACKET_SIZE          = 209   # bytes (must match ESP32 buffer size)
IMAGE_RX_TIMEOUT     = 30    # seconds of silence before giving up (CubeSat timeout is 120s)
FILE_INFO_TIMEOUT    = 40    # seconds to wait for CS_FILE_INFO header
IMAGE_MODE_TIMEOUT   = 150   # hard ceiling for entire image transfer (seconds)

# ── Graph ─────────────────────────────────────────────────────────────────────
MINI_GRAPH_MAX_POINTS   = 50
POPUP_GRAPH_MAX_POINTS  = 200
POPUP_SYNC_MS           = 200  # how often the popup mirrors the mini graph

# ── Default map centre (Bengaluru) ────────────────────────────────────────────
DEFAULT_LAT = 13.017812
DEFAULT_LON = 77.514813

# ── Qt stylesheet ─────────────────────────────────────────────────────────────
STYLE = """
QMainWindow { background-color: #020617; color: #e2e8f0; font-family: Consolas; }

#TelemetryPanel { border:1px solid #1e293b; border-radius:10px; }

#mainTitle {
    color:#38bdf8;
    font-size:20px;
    font-weight:bold;
    padding:6px;
}

#groupBox {
    border:1px solid #1e293b;
    border-radius:8px;
    background:#020617;
    margin-top:8px;
    padding:6px;
    color:#7dd3fc;
    font-weight:bold;
}

#nameLabel { color:#94a3b8; font-size:12px; }
#valueLabel { color:#e2e8f0; font-size:12px; font-weight:bold; }

QTextEdit {
    background:#000;
    color:#22c55e;
    border:1px solid #1e293b;
    font-size:11px;
}

QPushButton {
    background:#1e293b;
    color:#38bdf8;
    border:1px solid #38bdf8;
    padding:5px;
    border-radius:5px;
}
QPushButton:hover {
    background:#38bdf8;
    color:#020617;
}

QProgressBar {
    border: 1px solid #1e293b;
    border-radius: 6px;
    text-align: center;
    background-color: #0f172a;
    color: #e2e8f0;
    height: 18px;
    font-size: 11px;
}
QProgressBar::chunk {
    background-color: #38bdf8;
    border-radius: 6px;
}

QToolTip {
    background-color: #020617;
    color: #38bdf8;
    border: 1px solid #38bdf8;
    padding: 4px;
    font-size: 11px;
}
"""
