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

# Must be set before the QApplication is constructed, or they do nothing.
# Without them the whole UI is bitmap-upscaled on any display at 125% or 150%
# scaling, which is the default on most current Windows laptops.
QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
# QtWebEngineWidgets (right_panel.py's embedded map) now gets imported
# lazily, after QApplication already exists — GroundStation only gets
# built once a COM port is picked, from inside LoginWindow, so it's no
# longer imported at module load time here. Qt requires this attribute
# set before QApplication() in that case, or QtWebEngineWidgets refuses
# to import at all ("must be imported or Qt.AA_ShareOpenGLContexts must
# be set before a QCoreApplication instance is created").
QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_ShareOpenGLContexts, True)


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Loads any bundled faces, resolves the display/body/mono families against
    # what is installed, and sets the window icon from icon.ico.
    theme.init(app)
    app.setStyleSheet(theme.qss())

    # Show the Login + COM Port window. LoginWindow builds and shows
    # GroundStation itself (see _launch_main_window) once a port is picked,
    # so its own "Opening Ground Control Station…" page stays on screen
    # for that synchronous construction instead of the window just
    # collapsing until the main window appears.
    login = LoginWindow()
    if login.exec_() == QtWidgets.QDialog.Accepted:
        sys.exit(app.exec_())


if __name__ == "__main__":
    main()
