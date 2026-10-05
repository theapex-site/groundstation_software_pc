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
# BG1.jpg is no longer used: widgets/space_bg.py generates the website's
# starfield instead. The file is left in images/ for now rather than deleted.
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
QMainWindow { background: transparent; color: #f5f0e6; font-family: Consolas; }

#TelemetryPanel {
    border:1px solid #1a1a2e;
    border-radius:10px;
    background: rgba(8,8,15,0.78);
}

#mainTitle {
    color:#2dd4bf;
    font-size:20px;
    font-weight:bold;
    padding:6px;
}

#groupBox {
    border:1px solid #1a1a2e;
    border-radius:8px;
    background: rgba(13,13,24,0.72);
    margin-top:8px;
    padding:6px;
    color:#5eead4;
    font-weight:bold;
}

#nameLabel { color:#94a3b8; font-size:12px; }
#valueLabel { color:#f5f0e6; font-size:12px; font-weight:bold; }

QTextEdit {
    background: rgba(0,0,0,0.80);
    color:#22c55e;
    border:1px solid #1a1a2e;
    font-size:11px;
}

QPushButton {
    background:#1a1a2e;
    color:#2dd4bf;
    border:1px solid #2dd4bf;
    padding:5px;
    border-radius:5px;
}
QPushButton:hover {
    background:#2dd4bf;
    color:#000000;
}

QProgressBar {
    border: 1px solid #1a1a2e;
    border-radius: 6px;
    text-align: center;
    background-color: #08080f;
    color: #f5f0e6;
    height: 18px;
    font-size: 11px;
}
QProgressBar::chunk {
    background-color: #2dd4bf;
    border-radius: 6px;
}

QToolTip {
    background-color: #000000;
    color: #2dd4bf;
    border: 1px solid #2dd4bf;
    padding: 4px;
    font-size: 11px;
}
"""
