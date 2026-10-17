"""
config.py
─────────
Global constants and shared settings for APEX GCS.
Edit this file to change thresholds, timeouts or the baud rate.

Colours, fonts, radii and the stylesheet live in theme.py.
"""
import os
import sys

APP_VERSION = "1.2.8"
GITHUB_REPO = "theapex-site/groundstation_software_pc"

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS   # PyInstaller temp folder
    except AttributeError:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


# Where APEX_GCS_Saved/ (mission logs, captured images, detection output)
# actually gets written. Deliberately NOT resource_path()/sys._MEIPASS: that
# resolves to the install folder, and a standard (non-admin) Windows account
# cannot write inside Program Files — every such write crashed the app
# outright on first launch for anyone who hadn't happened to run it elevated.
# %LOCALAPPDATA% is always writable by the signed-in user, survives
# reinstalls/updates since it's outside the install folder entirely, and is
# the standard place Windows apps keep per-user working data.
if getattr(sys, "frozen", False):
    DATA_ROOT = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "APEX GCS")
else:
    DATA_ROOT = os.path.abspath(".")

IMAGES_DIR = resource_path("images")

APEX_LOGO  = os.path.join(IMAGES_DIR, "Apex Logo.png")
PIXIE_LOGO  = os.path.join(IMAGES_DIR, "pixie_thinking.png")
PIXIE_BUBBLE = os.path.join(IMAGES_DIR, "pixie_bubble.png")
DOWNLOAD_ANIMATION = os.path.join(IMAGES_DIR, "download_animation.gif")
SIGNIN_ANIMATION = os.path.join(IMAGES_DIR, "signin_pixie.gif")
NO_INTERNET_IMAGE = os.path.join(IMAGES_DIR, "no_internet.png")
BACK_ONLINE_IMAGE = os.path.join(IMAGES_DIR, "back_online.png")
OPENING_STATION_IMAGE = os.path.join(IMAGES_DIR, "opening_station.png")
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
