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

# Same import order as main.py: the detection runtime before PyQt5. Creating
# an onnxruntime session after QApplication exists segfaults the process.
try:
    import onnxruntime  # noqa: F401
except Exception as _e:
    print("[WARN] onnxruntime pre-load failed:", _e)

from PyQt5 import QtWidgets

import theme
from widgets.object_detection import ObjectDetectionDialog

app = QtWidgets.QApplication(sys.argv)
theme.init(app)
app.setStyleSheet(theme.qss())

dlg = ObjectDetectionDialog()
dlg.setModal(False)
dlg.show()
sys.exit(app.exec_())
