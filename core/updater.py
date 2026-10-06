"""
Auto-update checker using GitHub Releases API.
"""

import threading
import webbrowser
from packaging.version import Version

import requests
from PyQt5 import QtWidgets, QtCore

from config import APP_VERSION, GITHUB_REPO


_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def check_for_updates(parent=None):
    """Spawn a background thread that checks GitHub for a newer release."""
    t = threading.Thread(target=_check, args=(parent,), daemon=True)
    t.start()


def _check(parent):
    try:
        resp = requests.get(_API_URL, timeout=10)
        if resp.status_code != 200:
            return
        data = resp.json()
        latest = data.get("tag_name", "").lstrip("v")
        if not latest:
            return
        if Version(latest) <= Version(APP_VERSION):
            return

        download_url = None
        for asset in data.get("assets", []):
            if asset["name"].endswith(".exe"):
                download_url = asset["browser_download_url"]
                break

        if not download_url:
            download_url = data.get("html_url", "")

        QtCore.QMetaObject.invokeMethod(
            parent, "_show_update_dialog",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(str, latest),
            QtCore.Q_ARG(str, download_url),
        )
    except Exception:
        pass
