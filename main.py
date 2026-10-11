"""
main.py
───────
Entry point for the APEX Ground Control Station.

Run:
    python main.py

Build EXE (Windows):
    pip install pyinstaller
    pyinstaller --noconfirm apex_gcs.spec
"""

import sys
import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["PYTORCH_JIT"] = "0"

# Pre-load the detection runtime BEFORE any PyQt5 import.
#
# This was torch, for a c10.dll conflict. onnxruntime has the same problem:
# creating an InferenceSession after QApplication exists segfaults the process
# outright (exit 139, no traceback), while importing onnxruntime first makes it
# reliable. The conflict is in the load order, not in torch, so the workaround
# outlived the library it was written for. Do not move this below PyQt5.
try:
    import onnxruntime  # noqa: F401
except Exception as _e:
    print("[WARN] onnxruntime pre-load failed:", _e)

from PyQt5 import QtCore, QtWidgets

import theme
from widgets.dialogs     import LoginWindow
from core.ground_station import GroundStation

# Must be set before the QApplication is constructed, or they do nothing.
# Without them the whole UI is bitmap-upscaled on any display at 125% or 150%
# scaling, which is the default on most current Windows laptops.
QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Loads any bundled faces, resolves the display/body/mono families against
    # what is installed, and sets the window icon from icon.ico.
    theme.init(app)
    app.setStyleSheet(theme.qss())

    # Show the Login + COM Port window
    login = LoginWindow()
    if login.exec_() == QtWidgets.QDialog.Accepted:
        selected_port  = login.get_port()
        logged_in_email = login.get_email()          # ← pass email through
        window = GroundStation(selected_port, user_email=logged_in_email)
        # Maximized, not showFullScreen(): fullscreen takes over the whole
        # display (no taskbar), same convention as the RTG popup window.
        window.showMaximized()
        sys.exit(app.exec_())


if __name__ == "__main__":
    main()
