"""
try_object_detection.py  —  throwaway launcher, not part of the app.

Opens ONLY the Object Detection dialog, so it can be tested without a
CubeSat on a COM port (the normal route needs login + port selection).

    python try_object_detection.py

Delete this file whenever you like.
"""
import os
import sys

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["PYTORCH_JIT"] = "0"

# Same import order as main.py: torch before PyQt5, or c10.dll conflicts.
try:
    import torch
    import torch.nn
    import torchvision
except Exception as _e:
    print("[WARN] torch pre-load failed:", _e)

from PyQt5 import QtWidgets, QtGui

from config import STYLE
from widgets.object_detection import ObjectDetectionDialog

app = QtWidgets.QApplication(sys.argv)
QtWidgets.QToolTip.setFont(QtGui.QFont("Consolas", 10))
app.setStyleSheet(STYLE)

dlg = ObjectDetectionDialog()
dlg.setModal(False)
dlg.show()
sys.exit(app.exec_())
