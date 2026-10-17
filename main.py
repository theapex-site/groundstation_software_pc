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

# Per-Monitor-V2 DPI awareness for the dev run (`python main.py`). The
# frozen .exe gets this from app.manifest instead (see apex_gcs.spec) — a
# manifest baked into the binary is what Windows actually trusts; this is
# only here because python.exe/pythonw.exe carry no such manifest, so
# without this call Windows silently falls back to DPI virtualization
# (bitmap-stretching the window to fit the monitor) for a plain dev run,
# which is the same stretch bug the manifest fixes for the shipped build.
# Must run before any window handle exists, so before the PyQt5 import.
if sys.platform == "win32":
    import ctypes
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, Windows 10 1703+.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass

# Raw bytes off a serial link get print()ed verbatim all over this app
# (boot banners, noise, anything that arrived where text was expected),
# and Windows' default console codepage (cp1252) can't encode a lot of
# valid Unicode — one stray character used to crash whatever thread was
# printing it with UnicodeEncodeError. The image-downlink receiver runs
# on a daemon thread with nothing else watching it, so that crash killed
# the transfer invisibly and left the app stuck in image mode forever.
# errors="replace" is what matters here; utf-8 is just the easiest
# encoding to ask Python to swap in for a case this is sidestepping.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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

from PyQt5 import QtCore, QtGui, QtWidgets

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

    # One scale factor for the whole app, from the real screen it's about
    # to open on — see theme.set_scale(). Screen-under-the-cursor, not
    # primaryScreen(): same reasoning as GroundStation's own window sizing
    # (core/ground_station.py) — closest guess at which monitor the user
    # is actually on before any window has been shown. Must run before
    # theme.qss() (bakes the factor into font-size/radius px) and before
    # LoginWindow is built (its dialogs are fixed-size).
    screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
    if screen is None:
        screen = QtWidgets.QApplication.primaryScreen()
    avail = screen.availableGeometry()
    factor = theme.set_scale(avail)
    print(f"[SCALE] screen={screen.name()!r} availableGeometry={avail.width()}x{avail.height()} "
          f"devicePixelRatio={screen.devicePixelRatio()} -> theme._SCALE={factor}")

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
