"""
Auto-update checker using GitHub Releases API.
"""

import threading
import webbrowser
from packaging.version import Version

import requests
from PyQt5 import QtCore

from config import APP_VERSION, GITHUB_REPO


_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


class _UpdateSignal(QtCore.QObject):
    # (latest_version, download_url, is_installer, notes)
    found     = QtCore.pyqtSignal(str, str, bool, str)
    not_found = QtCore.pyqtSignal()
    failed    = QtCore.pyqtSignal(str)


def check_for_updates(parent=None, silent=True):
    """Spawn a background thread that checks GitHub for a newer release.

    silent=True (the automatic check on launch) only ever surfaces a found
    update — nothing is shown for "already up to date" or a failed check,
    since nobody asked. silent=False (the Settings "Check for Updates"
    button) is a deliberate, user-initiated action, so it always answers
    one of the three ways.

    The signal object, not QMetaObject.invokeMethod on a named slot:
    invokeMethod-by-string required `parent` to actually define a
    `_show_update_dialog` slot, which only GroundStation did — the
    Settings dialog passing itself as parent silently did nothing. Signals
    work with any QObject (or none), and the explicit QueuedConnection
    below still guarantees the dialog is built on the GUI thread even
    though the emit happens from the background thread.
    """
    sig = _UpdateSignal(parent)
    sig.found.connect(
        lambda *a: _on_found(parent, *a), QtCore.Qt.QueuedConnection)
    if not silent:
        sig.not_found.connect(
            lambda: _on_not_found(parent), QtCore.Qt.QueuedConnection)
        sig.failed.connect(
            lambda msg: _on_failed(parent, msg), QtCore.Qt.QueuedConnection)
    t = threading.Thread(target=_check, args=(sig, silent), daemon=True)
    t.start()


def _check(sig: _UpdateSignal, silent: bool):
    try:
        resp = requests.get(_API_URL, timeout=10)
        if resp.status_code != 200:
            if not silent:
                sig.failed.emit(f"GitHub returned HTTP {resp.status_code}.")
            return
        data = resp.json()
        latest = data.get("tag_name", "").lstrip("v")
        if not latest:
            if not silent:
                sig.failed.emit("The latest release has no version tag.")
            return
        if Version(latest) <= Version(APP_VERSION):
            if not silent:
                sig.not_found.emit()
            return

        # The release notes typed when this version was published — the
        # dialog shows these so an update reads as "here's what changed",
        # not just a version number to trust blindly.
        notes = data.get("body", "") or ""

        download_url = None
        for asset in data.get("assets", []):
            if asset["name"].endswith(".exe"):
                download_url = asset["browser_download_url"]
                break

        # A direct .exe asset can be streamed and installed in-app; without
        # one, the release page is the best we can do, and that only makes
        # sense opened in a browser.
        is_installer = download_url is not None
        if not download_url:
            download_url = data.get("html_url", "")

        sig.found.emit(latest, download_url, is_installer, notes)
    except Exception as exc:
        if not silent:
            sig.failed.emit(str(exc))


def _on_found(parent, latest_version, download_url, is_installer, notes):
    from widgets.dialogs import UpdateNotesDialog, UpdateDownloadDialog

    dlg = UpdateNotesDialog(parent, APP_VERSION, latest_version, notes)
    if dlg.exec_() != dlg.Accepted:
        return
    if is_installer:
        UpdateDownloadDialog(parent, download_url, latest_version).exec_()
    else:
        webbrowser.open(download_url)


def _on_not_found(parent):
    from widgets.dialogs import ThemedMessageBox
    ThemedMessageBox.information(
        parent, "No Updates",
        "You're on the latest version.",
        informative=f"APEX GCS v{APP_VERSION}",
    )


def _on_failed(parent, message):
    from widgets.dialogs import ThemedMessageBox
    ThemedMessageBox.warning(
        parent, "Update Check Failed",
        "Couldn't check for updates right now.",
        informative=message,
    )
