"""
config.py
─────────
Global constants and shared settings for APEX GCS.
Edit this file to change thresholds, timeouts or the baud rate.

Colours, fonts, radii and the stylesheet live in theme.py.
"""
import os
import sys

APP_VERSION = "1.1.2"
GITHUB_REPO = "theapex-site/groundstation_software_pc"

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS   # PyInstaller temp folder
    except AttributeError:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


IMAGES_DIR = resource_path("images")

APEX_LOGO  = os.path.join(IMAGES_DIR, "Apex Logo.png")
PIXIE_LOGO  = os.path.join(IMAGES_DIR, "pixie_thinking.png")
PIXIE_BUBBLE = os.path.join(IMAGES_DIR, "pixie_bubble.png")
DOWNLOAD_ANIMATION = os.path.join(IMAGES_DIR, "download_animation.gif")
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

# ── Appearance ────────────────────────────────────────────────────────────────
# The stylesheet used to live here, as a fourth copy of a palette that also
# existed in dialogs.py, ground_station.py and object_detection.py. It is now
# in theme.py, which all four read from. See theme.qss().
