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

# Pre-load torch BEFORE any PyQt5 import to avoid c10.dll conflict
try:
    import torch
    import torch.nn
    from ultralytics import YOLO
except Exception as _e:
    print("[WARN] torch pre-load failed:", _e)

from PyQt5 import QtWidgets, QtGui

from config import STYLE
from widgets.dialogs     import LoginWindow
from core.ground_station import GroundStation


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    QtWidgets.QToolTip.setFont(QtGui.QFont("Consolas", 10))
    QtWidgets.QToolTip.setPalette(QtGui.QPalette())
    app.setStyleSheet(STYLE)

    # Show the Login + COM Port window
    login = LoginWindow()
    if login.exec_() == QtWidgets.QDialog.Accepted:
        selected_port  = login.get_port()
        logged_in_email = login.get_email()          # ← pass email through
        window = GroundStation(selected_port, user_email=logged_in_email)
        window.show()
        sys.exit(app.exec_())


if __name__ == "__main__":
    main()
