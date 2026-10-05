"""
widgets/object_detection.py
────────────────────────────
APEX GCS — Object Detection Dialog

A PyQt5 popup dialog that performs object detection on downlinked images.

Features:
  - Triggered from "📤 Start Object Detection" button in RightPanel
  - "Last Captured" button auto-loads latest image from APEX_GCS_Saved/images/camera/
  - Upload button for manual image selection
  - Live annotated preview with coloured bounding boxes
  - Full metrics panel: confidence, class, area, bbox coords, centroid, aspect ratio, etc.
  - "Test" button runs detection
  - "Save" button saves annotated image → APEX_GCS_Saved/images/object_detect/
    and a matching .txt report (same stem) in the same folder

Model:
  torchvision Faster R-CNN ResNet50-FPN v2, COCO-pretrained (80 classes),
  exported to ONNX and run with onnxruntime. Bundled at
  models/fasterrcnn_resnet50_fpn_v2_coco.onnx, so the app never needs to
  download anything at runtime.

  onnxruntime rather than torch purely for size: torch was 530 MB of a 1.5 GB
  build against roughly 15 MB here, for output verified identical to within
  0.006 px. tools/export_onnx.py produces the graph and tools/verify_onnx.py
  proves it still matches before a build ships.

  torchvision is BSD-3-Clause. The previous Ultralytics YOLO backend was
  AGPL-3.0, which is incompatible with shipping APEX GCS as a commercial
  closed-source product without an Ultralytics Enterprise Licence.

Save structure:
  APEX_GCS_Saved/
    images/
      camera/          ← camera captures (existing)
      object_detect/   ← object detection outputs
        IMG_xxx_detect.jpg
        IMG_xxx_detect.txt   ← plain-text report (same stem)
"""

from __future__ import annotations

import datetime
import os
import re
import sys
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap

from widgets.dialogs import ThemedMessageBox

# ── BASE_PATH (mirrors config.py / right_panel.py pattern) ───────────────────
if getattr(sys, "frozen", False):
    BASE_PATH = sys._MEIPASS
else:
    BASE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

MODEL_PATH   = os.path.join(BASE_PATH, "models", "fasterrcnn_resnet50_fpn_v2_coco.onnx")
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_NUM_THREADS"] = "1"
CAMERA_DIR   = os.path.join(BASE_PATH, "APEX_GCS_Saved", "images", "camera")
DETECT_DIR   = os.path.join(BASE_PATH, "APEX_GCS_Saved", "images", "object_detect")
_LEGACY_DIR  = os.path.join(BASE_PATH, "APEX_GCS_Saved", "images", "debrie_detect")

# ── COCO class names ────────────────────────────────────────────────────────
# These used to come from torchvision's weight metadata. torchvision is no
# longer in the build, so the list is embedded here. The order is the model's
# label index, so it must not be reordered: "N/A" entries are gaps in COCO's
# numbering and the index of every later class depends on them staying put.
# tools/export_onnx.py asserts this list still matches torchvision's.
COCO_CATEGORIES = (
    "__background__", "person", "bicycle", "car", "motorcycle",
    "airplane", "bus", "train", "truck", "boat", "traffic light",
    "fire hydrant", "N/A", "stop sign", "parking meter", "bench", "bird",
    "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "N/A", "backpack", "umbrella", "N/A", "N/A", "handbag",
    "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "N/A", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "N/A", "dining table", "N/A", "N/A",
    "toilet", "N/A", "tv", "laptop", "mouse", "remote", "keyboard",
    "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator",
    "N/A", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
)

# ── Confidence palette ──────────────────────────────────────────────────────
# Replaces the old debris risk grading: with a generic COCO model there is no
# collision risk to score, so detections are banded by detector confidence.
# This colours the confidence TEXT only — box colour identifies the object.
CONF_COLOR_QT = {
    "HIGH":   "#44cc44",
    "MEDIUM": "#ffdd00",
    "LOW":    "#ff8800",
    "NONE":   "#44cc44",
}

CONF_BANDS = {"HIGH": 0.70, "MEDIUM": 0.45}

# Per-object palette. Colour identifies WHICH object a box is, so a box in the
# image can be matched to its entry in the report; the confidence band is
# carried by the text instead. Eight hues that stay distinguishable against
# photographic backgrounds, cycled when there are more detections than that.
OBJECT_COLORS = [
    "#38bdf8",  # sky
    "#f97316",  # orange
    "#22c55e",  # green
    "#e879f9",  # fuchsia
    "#facc15",  # yellow
    "#f43f5e",  # rose
    "#2dd4bf",  # teal
    "#a78bfa",  # violet
]


def _object_color(obj_id: int) -> str:
    """Hex colour for an object, by its 1-based id."""
    return OBJECT_COLORS[(obj_id - 1) % len(OBJECT_COLORS)]


def _hex_to_bgr(hex_color: str) -> tuple:
    """#rrggbb -> OpenCV BGR, so the image and the report cannot drift apart."""
    h = hex_color.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


def _text_on(bgr) -> tuple:
    """Black or white label text, whichever actually reads on the given fill."""
    b, g, r = bgr
    return (0, 0, 0) if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else (255, 255, 255)


def _migrate_legacy_dir():
    """Move results saved by older builds out of the misspelt debrie_detect folder."""
    try:
        if not os.path.isdir(_LEGACY_DIR):
            return
        os.makedirs(DETECT_DIR, exist_ok=True)
        for name in os.listdir(_LEGACY_DIR):
            src = os.path.join(_LEGACY_DIR, name)
            dst = os.path.join(DETECT_DIR, name.replace("_debris.", "_detect."))
            if os.path.isfile(src) and not os.path.exists(dst):
                os.replace(src, dst)
        if not os.listdir(_LEGACY_DIR):
            os.rmdir(_LEGACY_DIR)
    except Exception as exc:          # never block the dialog on migration
        print(f"[ObjectDetect] legacy folder migration skipped: {exc}")


_migrate_legacy_dir()


# ─────────────────────────────────────────────────────────────────────────────
#  Pure-logic helpers (no Qt dependency)
# ─────────────────────────────────────────────────────────────────────────────

def _confidence_band(conf: float) -> str:
    if conf >= CONF_BANDS["HIGH"]:   return "HIGH"
    if conf >= CONF_BANDS["MEDIUM"]: return "MEDIUM"
    return "LOW"


def _dead_mask(img_bgr):
    """
    Boolean mask of pixels lost to an incomplete downlink.

    The camera buffer is pre-filled with mid-grey, so packets that never
    arrived decode as flat BGR(128,128,128). Measured on real captures:
    IMG_20260625_140414 and IMG_20260626_020822 are each ~43% dead.

    A row is only treated as dropped when almost all of it is that fill
    value, so a genuinely grey object in a fully received row is not
    mistaken for missing data.
    """
    import numpy as np
    near     = np.all(np.abs(img_bgr.astype(np.int16) - 128) <= 2, axis=2)
    row_dead = near.mean(axis=1) > 0.95
    return near & row_dead[:, None]


def _received_pct(dead_mask) -> float:
    """Percentage of the frame that actually arrived."""
    return float((1.0 - dead_mask.mean()) * 100.0)


def _compute_metrics(box_xyxy, conf, cls_name, img_w, img_h, obj_id,
                     dead_mask=None) -> dict:
    x1, y1, x2, y2 = box_xyxy
    bw       = x2 - x1
    bh       = y2 - y1
    area_pct = (bw * bh) / (img_w * img_h) * 100
    cx_norm  = ((x1 + x2) / 2) / img_w
    cy_norm  = ((y1 + y2) / 2) / img_h
    band     = _confidence_band(conf)

    # How much of this box sits on packets that never arrived. A detection
    # mostly inside the dead region is the model hallucinating on flat grey.
    dead_pct = 0.0
    if dead_mask is not None:
        cx1, cy1 = max(0, int(x1)), max(0, int(y1))
        cx2, cy2 = min(img_w, int(x2)), min(img_h, int(y2))
        if cx2 > cx1 and cy2 > cy1:
            dead_pct = float(dead_mask[cy1:cy2, cx1:cx2].mean() * 100.0)

    # Box touching a frame edge means the object is cut off, so its size
    # is a lower bound rather than a measurement.
    clipped = bool(x1 <= 1 or y1 <= 1 or x2 >= img_w - 1 or y2 >= img_h - 1)

    return {
        "id":         obj_id,
        "class":      cls_name,
        "confidence": round(conf, 4),
        "band":       band,
        "bbox_px":    [round(x1), round(y1), round(x2), round(y2)],
        "width_px":   round(bw, 1),
        "height_px":  round(bh, 1),
        "area_pct":   round(area_pct, 2),
        "centre":     [round(cx_norm, 4), round(cy_norm, 4)],
        "dead_pct":   round(dead_pct, 1),
        "clipped":    clipped,
    }


def _annotate_image(img_bgr, detections):
    """Draw bounding boxes + centroid dots on a copy of img_bgr."""
    import cv2
    import numpy as np
    out = img_bgr.copy()
    img_h, img_w = out.shape[:2]
    # Label boxes already drawn, to avoid stacking them. Seeded with the strip
    # the watermark is composited into further down, which would otherwise be
    # painted straight over any label placed near the top edge.
    placed: list = [(0, 0, img_w, 22)]

    def _free_slot(lx, ly, lw, lh):
        """Nudge a label down until it clears the ones already placed."""
        for _ in range(12):
            box = (lx, ly, lx + lw, ly + lh)
            if not any(box[0] < p[2] and p[0] < box[2] and
                       box[1] < p[3] and p[1] < box[3] for p in placed):
                break
            ly += lh + 2
        return min(ly, img_h - lh)

    for d in detections:
        x1, y1, x2, y2 = d["bbox_px"]
        color = _hex_to_bgr(_object_color(d["id"]))
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        label = f"#{d['id']} {d['class']} {d['confidence']:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
        lw, lh = tw + 4, th + 6
        lx = min(x1, img_w - lw)
        # Prefer just above the box; drop inside it when there is no room,
        # then slide down past any label already occupying that spot.
        ly = _free_slot(lx, y1 - lh if y1 - lh >= 0 else y1, lw, lh)
        cv2.rectangle(out, (lx, ly), (lx + lw, ly + lh), color, -1)
        cv2.putText(out, label, (lx + 2, ly + lh - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, _text_on(color), 1,
                    cv2.LINE_AA)
        placed.append((lx, ly, lx + lw, ly + lh))
        cv2.circle(out, ((x1 + x2) // 2, (y1 + y2) // 2), 4, color, -1)
    # Watermark — exact same style as CubeSat camera images (PIL overlay)
    from PIL import Image as _Image, ImageDraw as _ImageDraw, ImageFont as _ImageFont
    pil_img  = _Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB)).convert("RGBA")
    overlay  = _Image.new("RGBA", pil_img.size, (0, 0, 0, 0))
    draw     = _ImageDraw.Draw(overlay)
    text     = f"APEX  |  Object Detection  |  {datetime.datetime.now().strftime('%d-%m-%Y - %H:%M:%S')}"
    font     = None
    for fn in ("consola.ttf", "Consolas.ttf", "arial.ttf", "Arial.ttf"):
        try:
            font = _ImageFont.truetype(fn, 12)
            break
        except Exception:
            continue
    if font is None:
        font = _ImageFont.load_default()
    bbox    = draw.textbbox((0, 0), text, font=font)
    tw, th  = bbox[2] - bbox[0], bbox[3] - bbox[1]
    px, py  = 6, 3
    bw, bh  = tw + px * 2, th + py * 2
    bx, by  = 0, 0
    draw.rectangle([bx, by, bx + bw, by + bh], fill=(0, 0, 0, 210))
    draw.text((bx + px - bbox[0], by + py - bbox[1]), text, font=font, fill=(255, 215, 0, 255))
    result  = _Image.alpha_composite(pil_img, overlay).convert("RGB")
    out     = cv2.cvtColor(np.array(result), cv2.COLOR_RGB2BGR)
    return out


def _local_timestamp(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    Local wall-clock time with an explicit UTC offset, e.g.
    "2026-10-02 21:58:06 UTC+05:30".

    Everything else in the GCS stamps local time — camera filenames, the
    image watermark, flag-monitor and graph entries — so the report matches
    rather than sitting 5.5 hours behind it in UTC. The offset is shown
    instead of hardcoding "IST" because this is the operator's PC clock, not
    a value the CubeSat sent.
    """
    now = datetime.datetime.now().astimezone()
    off = now.strftime("%z")                       # e.g. +0530
    return f"{now.strftime(fmt)} UTC{off[:3]}:{off[3:]}" if off else now.strftime(fmt)


def _class_counts(detections) -> dict:
    counts: dict[str, int] = {}
    for d in detections:
        counts[d["class"]] = counts.get(d["class"], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _build_txt_report(img_path: str, detections: list, summary: str,
                      elapsed_ms: float, conf_thresh: float,
                      img_w: int, img_h: int, received_pct: float = 100.0) -> str:
    """Return a plain-text report string matching the original log style."""
    ts  = _local_timestamp()
    sep = "=" * 52
    thn = "-" * 52
    counts = _class_counts(detections)
    lines = [
        sep,
        "  APEX GCS OBJECT DETECTION REPORT",
        sep,
        f"  Timestamp       : {ts}",
        f"  Image           : {Path(img_path).name}",
        f"  Image size      : {img_w} x {img_h} px",
        f"  Frame received  : {received_pct:.1f}%",
        f"  Inference time  : {elapsed_ms:.1f} ms",
        f"  Conf threshold  : {conf_thresh}",
        f"  Total objects   : {len(detections)}",
        f"  Distinct classes: {len(counts)}",
        thn,
    ]

    if received_pct < 99.5:
        lines += [
            f"  NOTE: {100 - received_pct:.1f}% of this frame was lost in the downlink.",
            "        Detections overlapping the lost region are unreliable.",
            thn,
        ]

    if counts:
        for name, n in counts.items():
            lines.append(f"  {name:<16}: {n}")
        lines.append(thn)

    for d in detections:
        lines += [
            f"  OBJECT #{d['id']:02d}  {d['class'].upper()}",
            f"  Confidence      : {d['confidence']:.4f}  ({d['band']})",
            f"  BBox (px)       : [{d['bbox_px'][0]},{d['bbox_px'][1]}] -> [{d['bbox_px'][2]},{d['bbox_px'][3]}]",
            f"  Size            : {d['width_px']:.0f} x {d['height_px']:.0f} px  ({d['area_pct']:.2f}% of frame)",
            f"  Centre          : {d['centre'][0]:.4f}, {d['centre'][1]:.4f}  (normalised)",
            f"  In lost region  : {d['dead_pct']:.1f}%"
            + ("   <-- unreliable" if d["dead_pct"] >= 50 else ""),
            f"  Clipped at edge : {'yes  (size is a lower bound)' if d['clipped'] else 'no'}",
            thn,
        ]

    if not detections:
        lines.append("  No objects detected.")
        lines.append(sep)

    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
#  Worker thread
# ─────────────────────────────────────────────────────────────────────────────

class _DetectWorker(QtCore.QThread):
    """Runs detection inference off the main thread."""

    finished  = pyqtSignal(object, list, str, float, float)   # annotated, detections, summary, ms, received%
    errored   = pyqtSignal(str)

    def __init__(self, detector, categories: list, img_path: str, conf: float):
        super().__init__()
        self.detector   = detector
        self.categories = categories
        self.img_path   = img_path
        self.conf       = conf

    def run(self):
        try:
            import cv2, time
            import numpy as np
            img_bgr = cv2.imread(self.img_path)
            if img_bgr is None:
                raise ValueError(
                    "Could not decode this image. The downlink was most likely "
                    "incomplete, leaving a truncated JPEG."
                )
            H, W = img_bgr.shape[:2]

            # The exported graph carries the model's own normalisation and
            # resizing, so it wants exactly what torch was given: RGB, CHW,
            # float 0..1. Thread count is set once on the session in
            # _load_model, not per call.
            rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            inp = np.ascontiguousarray(
                rgb.transpose(2, 0, 1).astype(np.float32) / 255.0)

            t0 = time.time()
            boxes, labels, scores = self.detector.run(None, {"image": inp})
            elapsed = (time.time() - t0) * 1000

            dead = _dead_mask(img_bgr)
            received = _received_pct(dead)

            detections = []
            keep = scores >= self.conf
            for box, score, label in zip(boxes[keep], scores[keep], labels[keep]):
                x1, y1, x2, y2 = (float(v) for v in box)
                ci = int(label)
                nm = self.categories[ci] if 0 <= ci < len(self.categories) else f"class_{ci}"
                detections.append(_compute_metrics([x1, y1, x2, y2],
                                                   float(score), nm, W, H,
                                                   len(detections) + 1, dead))

            counts  = _class_counts(detections)
            summary = ", ".join(f"{k} x{v}" if v > 1 else k
                                for k, v in counts.items()) or "none"

            annotated = _annotate_image(img_bgr, detections)
            self.finished.emit(annotated, detections, summary, elapsed, received)

        except Exception as exc:
            self.errored.emit(str(exc))


# ─────────────────────────────────────────────────────────────────────────────
#  Main dialog
# ─────────────────────────────────────────────────────────────────────────────

class ObjectDetectionDialog(QtWidgets.QDialog):
    """
    Pop-up dialog opened from RightPanel's "Start Object Detection" button.
    Styled to match the APEX GCS dark theme.
    """

    # GCS stylesheet colours
    _BG       = "#020617"
    _PANEL    = "#0f172a"
    _BORDER   = "#1e293b"
    _ACCENT   = "#38bdf8"
    _FG       = "#e2e8f0"
    _DIM      = "#94a3b8"
    _BTN_BG   = "#1e293b"
    _BTN_HOV  = "#38bdf8"
    _ORANGE   = "#e05a2b"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("APEX GCS Object Detection")
        self.setMinimumSize(1060, 680)
        self.setModal(True)
        self.setStyleSheet(self._build_stylesheet())

        self._img_path:       str | None = None
        self._annotated_bgr              = None   # numpy array
        self._detections:     list       = []
        self._summary:        str        = "none"
        self._received_pct:   float      = 100.0
        self._elapsed_ms:     float      = 0.0
        # 0.50 suits Faster R-CNN, which emits many low-scoring boxes; the
        # old 0.25 default came from YOLO and leaves the frame cluttered.
        self._conf_thresh:    float      = 0.50
        self._detector                   = None
        self._categories:     list       = []
        self._worker: _DetectWorker | None = None

        self._build_ui()

    # ─────────────────────────────────────────────────────────────────────────
    #  Stylesheet
    # ─────────────────────────────────────────────────────────────────────────

    def _build_stylesheet(self) -> str:
        B  = self._BG
        P  = self._PANEL
        BD = self._BORDER
        AC = self._ACCENT
        FG = self._FG
        DM = self._DIM
        BB = self._BTN_BG
        BH = self._BTN_HOV
        OR = self._ORANGE
        return f"""
        QDialog           {{ background:{B}; color:{FG}; font-family:Consolas; }}
        QGroupBox         {{
            border:1px solid {BD}; border-radius:8px;
            background:{B}; margin-top:10px; padding:6px;
            color:{AC}; font-weight:bold; font-size:11px;
        }}
        QGroupBox::title  {{ subcontrol-origin:margin; left:10px; padding:0 4px; color:{AC}; }}
        QLabel#nameLabel  {{ color:{DM}; font-size:11px; }}
        QLabel#valueLabel {{ color:{FG}; font-size:11px; font-weight:bold; }}
        QTextEdit         {{
            background:#000; color:#b0c4de; border:1px solid {BD};
            font-family:Consolas; font-size:10px;
        }}
        QPushButton {{
            background:{BB}; color:{AC}; border:1px solid {AC};
            padding:6px 14px; border-radius:5px; font-family:Consolas;
            font-size:11px; font-weight:bold;
        }}
        QPushButton:hover {{ background:{BH}; color:{B}; }}
        QPushButton:disabled {{ background:#0a0a14; color:#334; border-color:#222; }}
        QPushButton#accentBtn {{
            background:{OR}; color:#fff; border:1px solid {OR};
        }}
        QPushButton#accentBtn:hover {{ background:#c04015; color:#fff; }}
        QProgressBar {{
            border:1px solid {BD}; border-radius:5px; text-align:center;
            background:{P}; color:{FG}; height:14px; font-size:10px;
        }}
        QProgressBar::chunk {{ background:{AC}; border-radius:5px; }}
        QScrollBar:vertical {{
            background:{P}; width:8px; border-radius:4px;
        }}
        QScrollBar::handle:vertical {{
            background:{BD}; border-radius:4px; min-height:20px;
        }}
        QScrollBar:horizontal {{
            background:{P}; height:8px; border-radius:4px;
        }}
        QScrollBar::handle:horizontal {{
            background:{BD}; border-radius:4px; min-width:20px;
        }}
        """

    # ─────────────────────────────────────────────────────────────────────────
    #  UI construction
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        root.setSpacing(8)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QtWidgets.QHBoxLayout()
        title_lbl = QtWidgets.QLabel("APEX")
        title_lbl.setStyleSheet(f"color:{self._ORANGE}; font-size:20px; font-weight:bold; font-family:Consolas;")
        sub_lbl   = QtWidgets.QLabel("  OBJECT DETECTION")
        sub_lbl.setStyleSheet(f"color:{self._FG}; font-size:20px; font-weight:bold; font-family:Consolas;")
        hdr.addWidget(title_lbl)
        hdr.addWidget(sub_lbl)
        hdr.addStretch()
        self._status_lbl = QtWidgets.QLabel("● MODEL NOT LOADED")
        self._status_lbl.setStyleSheet(f"color:{self._DIM}; font-family:Consolas; font-size:11px;")
        hdr.addWidget(self._status_lbl)
        root.addLayout(hdr)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setStyleSheet(f"color:{self._BORDER};")
        root.addWidget(sep)

        # ── Button bar ────────────────────────────────────────────────────────
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)

        self._btn_last    = self._make_btn("📷  Last Captured",  self._load_last_captured, accent=True)
        self._btn_upload  = self._make_btn("📂  Upload Image",   self._upload_image)
        self._btn_test    = self._make_btn("▶  Test",            self._run_detect)
        self._btn_save    = self._make_btn("💾  Save",           self._save_result)
        self._btn_clear   = self._make_btn("✕  Clear",           self._clear)
        self._btn_close   = self._make_btn("✖  Close",           self.close)

        for b in (self._btn_last, self._btn_upload, self._btn_test,
                  self._btn_save, self._btn_clear, self._btn_close):
            btn_row.addWidget(b)
        btn_row.addStretch()

        # Confidence row (right-aligned in same bar)
        conf_lbl = QtWidgets.QLabel("Confidence:")
        conf_lbl.setStyleSheet(f"color:{self._DIM}; font-family:Consolas; font-size:10px;")
        self._conf_spin = QtWidgets.QDoubleSpinBox()
        self._conf_spin.setRange(0.01, 1.00)
        self._conf_spin.setSingleStep(0.05)
        self._conf_spin.setValue(self._conf_thresh)
        self._conf_spin.setDecimals(2)
        self._conf_spin.setFixedWidth(68)
        self._conf_spin.setStyleSheet(
            f"background:#0f172a; color:{self._FG}; border:1px solid {self._BORDER};"
            f"font-family:Consolas; font-size:10px; border-radius:4px; padding:2px;"
        )
        btn_row.addWidget(conf_lbl)
        btn_row.addWidget(self._conf_spin)
        root.addLayout(btn_row)

        # Progress bar
        self._progress = QtWidgets.QProgressBar()
        self._progress.setRange(0, 0)   # indeterminate by default
        self._progress.setFixedHeight(6)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        # ── Main split: image | report ────────────────────────────────────────
        splitter = QtWidgets.QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet(f"QSplitter::handle {{ background:{self._BORDER}; }}")

        # Left: image preview
        left_frame = QtWidgets.QFrame()
        left_frame.setStyleSheet(f"background:{self._PANEL}; border:1px solid {self._BORDER}; border-radius:6px;")
        left_layout = QtWidgets.QVBoxLayout(left_frame)
        left_layout.setContentsMargins(4, 4, 4, 4)

        self._img_label = QtWidgets.QLabel()
        self._img_label.setAlignment(Qt.AlignCenter)
        self._img_label.setMinimumSize(400, 300)
        self._img_label.setStyleSheet(f"background:{self._BG}; border-radius:4px;")
        self._img_label.setText(
            f"<span style='color:{self._DIM}; font-family:Consolas; font-size:13px;'>"
            "Click  📷 Last Captured  or  📂 Upload Image  to begin"
            "</span>"
        )
        left_layout.addWidget(self._img_label, 1)

        # Image filename label
        self._img_name_lbl = QtWidgets.QLabel("")
        self._img_name_lbl.setAlignment(Qt.AlignCenter)
        self._img_name_lbl.setStyleSheet(f"color:{self._DIM}; font-family:Consolas; font-size:9px;")
        left_layout.addWidget(self._img_name_lbl)

        splitter.addWidget(left_frame)

        # Right: report panel
        right_frame = QtWidgets.QFrame()
        right_frame.setFixedWidth(360)
        right_frame.setStyleSheet(f"background:{self._PANEL}; border:1px solid {self._BORDER}; border-radius:6px;")
        right_layout = QtWidgets.QVBoxLayout(right_frame)
        right_layout.setContentsMargins(10, 8, 10, 8)
        right_layout.setSpacing(6)

        rep_title = QtWidgets.QLabel("DETECTION REPORT")
        rep_title.setStyleSheet(f"color:{self._ORANGE}; font-family:Consolas; font-size:11px; font-weight:bold;")
        right_layout.addWidget(rep_title)

        sep2 = QtWidgets.QFrame()
        sep2.setFrameShape(QtWidgets.QFrame.HLine)
        sep2.setStyleSheet(f"color:{self._BORDER};")
        right_layout.addWidget(sep2)

        # Stat cards row
        stat_row = QtWidgets.QHBoxLayout()
        self._card_total   = self._make_stat_card("OBJECTS", "—")
        self._card_classes = self._make_stat_card("CLASSES", "—")
        self._card_ms      = self._make_stat_card("INF. ms", "—")
        self._card_rx      = self._make_stat_card("FRAME RX", "—")
        for c in (self._card_total, self._card_classes, self._card_ms, self._card_rx):
            stat_row.addWidget(c, 1)
        right_layout.addLayout(stat_row)

        sep3 = QtWidgets.QFrame()
        sep3.setFrameShape(QtWidgets.QFrame.HLine)
        sep3.setStyleSheet(f"color:{self._BORDER};")
        right_layout.addWidget(sep3)

        # Log text area
        self._log = QtWidgets.QTextEdit()
        self._log.setReadOnly(True)
        self._log.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        right_layout.addWidget(self._log, 1)

        splitter.addWidget(right_frame)
        splitter.setSizes([680, 360])
        root.addWidget(splitter, 1)

        # Status bar
        self._bottom_lbl = QtWidgets.QLabel("")
        self._bottom_lbl.setStyleSheet(f"color:{self._DIM}; font-family:Consolas; font-size:9px;")
        root.addWidget(self._bottom_lbl)

    # ─────────────────────────────────────────────────────────────────────────
    #  Widget helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _make_btn(self, text: str, slot, accent=False) -> QtWidgets.QPushButton:
        b = QtWidgets.QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        if accent:
            b.setObjectName("accentBtn")
        b.clicked.connect(slot)
        return b

    def _make_stat_card(self, label: str, value: str) -> QtWidgets.QFrame:
        f = QtWidgets.QFrame()
        f.setStyleSheet(
            f"background:#1a2030; border:1px solid {self._BORDER}; border-radius:5px;"
        )
        vbox = QtWidgets.QVBoxLayout(f)
        vbox.setContentsMargins(6, 4, 6, 4)
        vbox.setSpacing(1)

        lbl = QtWidgets.QLabel(label)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setObjectName("nameLabel")
        lbl.setStyleSheet(f"color:{self._DIM}; font-size:8px; font-family:Consolas; font-weight:bold;")
        vbox.addWidget(lbl)

        val = QtWidgets.QLabel(value)
        val.setAlignment(Qt.AlignCenter)
        val.setObjectName("valueLabel")
        val.setStyleSheet(f"color:{self._FG}; font-size:13px; font-family:Consolas; font-weight:bold;")
        vbox.addWidget(val)

        f._val_lbl = val
        return f

    def _set_card(self, card: QtWidgets.QFrame, value: str, color: str = ""):
        c = color or self._FG
        card._val_lbl.setStyleSheet(
            f"color:{c}; font-size:13px; font-family:Consolas; font-weight:bold;"
        )
        card._val_lbl.setText(value)

    def _set_status(self, text: str, color: str = "#44cc44"):
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(
            f"color:{color}; font-family:Consolas; font-size:11px;"
        )

    # ─────────────────────────────────────────────────────────────────────────
    #  Model loading
    # ─────────────────────────────────────────────────────────────────────────

    def _ensure_model(self) -> bool:
        """
        Load the detector on the MAIN thread, on first use.

        Kept on the main thread because the torch build crashed natively when
        the network was built inside a QThread (exit 127, no traceback).
        onnxruntime has no such problem, but the arrangement is already proven
        and loading is fast, so it stays. Inference itself is threaded in
        _DetectWorker.
        """
        if self._detector is not None:
            return True
        try:
            import onnxruntime as ort
            if not Path(MODEL_PATH).exists():
                self._on_model_load_fail(f"Model not found: {MODEL_PATH}")
                return False

            self._set_status("● LOADING MODEL…", "#f0c040")
            self._progress.setVisible(True)
            QtWidgets.QApplication.processEvents()      # let the UI paint first

            # main.py sets OMP_NUM_THREADS=1, which would leave this
            # single-threaded and take ~9 s per image. Setting the session's
            # own thread count brings it back to ~3 s with identical output.
            # Capped rather than maxed so the 50 ms serial telemetry poll on
            # the main thread is not starved.
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = max(1, min(6, (os.cpu_count() or 2) // 2))
            opts.log_severity_level = 3          # warnings only, not every node

            session = ort.InferenceSession(
                MODEL_PATH, sess_options=opts,
                providers=["CPUExecutionProvider"])

            categories = list(COCO_CATEGORIES)
            self._detector   = session
            self._categories = categories
            usable = len([c for c in categories if c not in ("N/A", "__background__")])
            self._set_status("● READY  [Faster R-CNN R50-FPN v2 (COCO)]", "#44cc44")
            self._bottom_lbl.setText(f"Model ready, {usable} detectable classes")
            return True
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self._on_model_load_fail(str(exc))
            return False
        finally:
            self._progress.setVisible(False)

    def _on_model_load_fail(self, msg: str):
        self._set_status("● MODEL ERROR", "#ff4444")
        self._bottom_lbl.setText(f"Model error: {msg}")

    # ─────────────────────────────────────────────────────────────────────────
    #  Image loading
    # ─────────────────────────────────────────────────────────────────────────

    def _load_last_captured(self):
        """Load the most recently saved camera image from APEX_GCS_Saved/images/camera/."""
        cam_dir = Path(CAMERA_DIR)
        if not cam_dir.exists():
            ThemedMessageBox.warning(
                self, "No Camera Images",
                "Camera directory not found.", CAMERA_DIR
            )
            return
        images = sorted(
            cam_dir.glob("*.jpg"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ) + sorted(
            cam_dir.glob("*.png"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not images:
            ThemedMessageBox.information(
                self, "No Images",
                "No captured images found.", str(cam_dir)
            )
            return
        self._set_image_path(str(images[0]))

    def _upload_image(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select Image",
            CAMERA_DIR if Path(CAMERA_DIR).exists() else "",
            "Images (*.jpg *.jpeg *.png *.bmp *.tiff *.tif);;All Files (*.*)",
        )
        if path:
            self._set_image_path(path)

    def _set_image_path(self, path: str):
        self._img_path       = path
        self._annotated_bgr  = None
        self._detections     = []
        self._summary        = "none"
        self._log_clear()
        self._show_image_from_path(path)
        self._img_name_lbl.setText(Path(path).name)
        self._log_line("header", f"Loaded: {Path(path).name}\n")
        self._bottom_lbl.setText(f"Loaded: {path}")

    def _show_image_from_path(self, path: str):
        try:
            import cv2
            bgr = cv2.imread(path)
            if bgr is None:
                raise ValueError(
                    "Could not decode this image. The downlink was most likely "
                    "incomplete, leaving a truncated JPEG."
                )
            self._show_bgr(bgr)
        except Exception as e:
            self._img_label.setPixmap(QPixmap())
            self._img_label.setText(
                f"<span style='color:#ff4444; font-family:Consolas; font-size:12px;'>{e}</span>"
            )

    def _show_bgr(self, bgr):
        """Convert OpenCV BGR image to QPixmap and display it, scaled to fit."""
        import cv2
        rgb    = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w   = rgb.shape[:2]
        qimg   = QImage(rgb.data, w, h, w * 3, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)

        lw = max(self._img_label.width(),  400)
        lh = max(self._img_label.height(), 300)
        pixmap = pixmap.scaled(lw, lh, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._img_label.setPixmap(pixmap)

    def resizeEvent(self, event):
        """Re-scale displayed image on dialog resize."""
        super().resizeEvent(event)
        if self._annotated_bgr is not None:
            self._show_bgr(self._annotated_bgr)
        elif self._img_path:
            self._show_image_from_path(self._img_path)

    # ─────────────────────────────────────────────────────────────────────────
    #  Detection
    # ─────────────────────────────────────────────────────────────────────────

    def _run_detect(self):
        if not self._img_path:
            ThemedMessageBox.information(
                self, "No Image",
                "Please load an image first (Last Captured or Upload)."
            )
            return
        if not self._ensure_model():
            ThemedMessageBox.warning(
                self, "Model Not Available",
                "The detection model could not be loaded.", MODEL_PATH
            )
            return

        self._btn_test.setEnabled(False)
        self._progress.setVisible(True)
        self._conf_thresh = self._conf_spin.value()
        self._bottom_lbl.setText("Running inference… (a few seconds on CPU)")

        self._worker = _DetectWorker(self._detector, self._categories,
                                     self._img_path, self._conf_thresh)
        self._worker.finished.connect(self._on_detect_finished)
        self._worker.errored.connect(self._on_detect_error)
        self._worker.start()

    @QtCore.pyqtSlot(object, list, str, float, float)
    def _on_detect_finished(self, annotated_bgr, detections, summary, elapsed_ms,
                            received_pct=100.0):
        self._annotated_bgr  = annotated_bgr
        self._detections     = detections
        self._summary        = summary
        self._elapsed_ms     = elapsed_ms
        self._received_pct   = received_pct

        self._show_bgr(annotated_bgr)
        self._update_report(detections, summary, elapsed_ms, received_pct)
        self._progress.setVisible(False)
        self._btn_test.setEnabled(True)
        self._bottom_lbl.setText(
            f"Detection complete, {len(detections)} object(s) found  |  {elapsed_ms:.0f} ms"
        )

    @QtCore.pyqtSlot(str)
    def _on_detect_error(self, msg: str):
        self._progress.setVisible(False)
        self._btn_test.setEnabled(True)
        self._bottom_lbl.setText(f"Error: {msg}")
        ThemedMessageBox.critical(self, "Detection Error", msg)

    # ─────────────────────────────────────────────────────────────────────────
    #  Report panel
    # ─────────────────────────────────────────────────────────────────────────

    def _update_report(self, detections, summary, elapsed_ms, received_pct=100.0):
        counts = _class_counts(detections)
        self._set_card(self._card_total,   str(len(detections)))
        self._set_card(self._card_classes, str(len(counts)))
        self._set_card(self._card_ms,      f"{elapsed_ms:.0f}")
        rx_col = ("#44cc44" if received_pct >= 99.5 else
                  "#ffdd00" if received_pct >= 75 else "#ff8800")
        self._set_card(self._card_rx, f"{received_pct:.0f}%", rx_col)

        self._log_clear()
        self._log_line("header",  "=" * 44 + "\n")
        self._log_line("header",  "  DETECTION REPORT\n")
        self._log_line("header",  "=" * 44 + "\n")
        self._log_pair("Time",      _local_timestamp("%H:%M:%S"))
        self._log_pair("Image",     Path(self._img_path).name)
        self._log_pair("Inference", f"{elapsed_ms:.1f} ms")
        self._log_pair("Objects",   str(len(detections)))
        self._log_pair("Classes",   str(len(counts)))
        self._log_html(
            f"<span style='color:{self._DIM};'>  Frame RX  : </span>"
            f"<span style='color:{rx_col};'>{received_pct:.1f}%</span><br>"
        )
        if received_pct < 99.5:
            self._log_html(
                f"<span style='color:#ff8800;'>  {100 - received_pct:.1f}% of this frame "
                f"was lost in the downlink.</span><br>"
            )

        if counts:
            self._log_line("dim", "─" * 44 + "\n")
            for name, n in counts.items():
                self._log_html(
                    f"<span style='color:{self._DIM};'>  {name:<16}: </span>"
                    f"<span style='color:#7ecfff;'>{n}</span><br>"
                )

        for d in detections:
            bc = CONF_COLOR_QT.get(d["band"], self._FG)
            oc = _object_color(d["id"])
            self._log_line("dim", "─" * 44 + "\n")
            self._log_html(
                f"<span style='color:{oc}; font-weight:bold;'>"
                f"  &#9632; OBJECT #{d['id']:02d}  {d['class'].upper()}</span><br>"
            )
            self._log_html(
                f"<span style='color:{self._DIM};'>  Confidence    : </span>"
                f"<span style='color:{bc};'>{d['confidence']:.4f}  ({d['band']})</span><br>"
            )
            pairs = [
                ("BBox (px)",  f"[{d['bbox_px'][0]},{d['bbox_px'][1]}]->[{d['bbox_px'][2]},{d['bbox_px'][3]}]"),
                ("Size",       f"{d['width_px']:.0f} x {d['height_px']:.0f} px ({d['area_pct']:.2f}%)"),
                ("Centre",     f"{d['centre'][0]:.4f}, {d['centre'][1]:.4f}"),
            ]
            for lbl, val in pairs:
                self._log_html(
                    f"<span style='color:{self._DIM};'>  {lbl:<12}: </span>"
                    f"<span style='color:#7ecfff;'>{val}</span><br>"
                )
            dead_col = "#ff8800" if d["dead_pct"] >= 50 else "#7ecfff"
            self._log_html(
                f"<span style='color:{self._DIM};'>  In lost rgn : </span>"
                f"<span style='color:{dead_col};'>{d['dead_pct']:.1f}%"
                f"{'  &lt;-- unreliable' if d['dead_pct'] >= 50 else ''}</span><br>"
            )
            if d["clipped"]:
                self._log_html(
                    f"<span style='color:{self._DIM};'>  Clipped     : </span>"
                    f"<span style='color:#ffdd00;'>yes (size is a lower bound)</span><br>"
                )

        self._log_line("dim", "=" * 44 + "\n")
        if not detections:
            self._log_html(
                f"<span style='color:{self._DIM};'>  No objects detected above the "
                f"confidence threshold.</span><br>"
            )

    # ─────────────────────────────────────────────────────────────────────────
    #  Save
    # ─────────────────────────────────────────────────────────────────────────

    def _save_result(self):
        if self._annotated_bgr is None:
            ThemedMessageBox.information(
                self, "Nothing to Save",
                "Run detection first (click ▶ Test)."
            )
            return

        import cv2
        os.makedirs(DETECT_DIR, exist_ok=True)

        # Build stem from source image name (preserves IMG_ naming)
        # Detection output always carries the same IMG_<date>_<time> shape as a
        # camera downlink, whatever was fed in. A run on an uploaded file used
        # to inherit that file's name, producing things like images_detect.jpg,
        # which made the saved set inconsistent and left the backup service
        # unable to tell APEX output from anything else by name alone.
        #
        # A downlink's own stem is reused where there is one, so the result
        # still pairs obviously with the image it came from; anything else gets
        # the current time.
        src_stem = Path(self._img_path).stem
        if re.fullmatch(r"IMG_\d{8}_\d{6}", src_stem):
            stem = f"{src_stem}_detect"
        else:
            stem = datetime.datetime.now().strftime("IMG_%Y%m%d_%H%M%S") + "_detect"

        img_path = os.path.join(DETECT_DIR, f"{stem}.jpg")
        txt_path = os.path.join(DETECT_DIR, f"{stem}.txt")

        # Save annotated image
        cv2.imwrite(img_path, self._annotated_bgr)

        # Save plain-text report (same stem)
        img_bgr = cv2.imread(self._img_path)
        H, W = img_bgr.shape[:2] if img_bgr is not None else (0, 0)
        report_txt = _build_txt_report(
            self._img_path, self._detections, self._summary,
            self._elapsed_ms, self._conf_thresh, W, H, self._received_pct,
        )
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(report_txt)

        try:
            import widgets.auth_backend as auth
            import threading as _t
            if getattr(self, "_user_email", ""):
                _t.Thread(target=auth.upload_file, args=(self._user_email, img_path, "object detection"), daemon=True).start()
                _t.Thread(target=auth.upload_file, args=(self._user_email, txt_path, "object detection"), daemon=True).start()
        except Exception as _ue:
            print(f"[Backup] object detection upload error: {_ue}")

        self._bottom_lbl.setText(f"Saved → {img_path}")
        ThemedMessageBox.information(
            self, "Saved",
            "Detection result saved.",
            f"{img_path}\n{txt_path}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    #  Clear
    # ─────────────────────────────────────────────────────────────────────────

    def _clear(self):
        self._img_path      = None
        self._annotated_bgr = None
        self._detections    = []
        self._summary       = "none"
        self._elapsed_ms    = 0.0
        self._img_label.setPixmap(QPixmap())
        self._img_label.setText(
            f"<span style='color:{self._DIM}; font-family:Consolas; font-size:13px;'>"
            "Click  📷 Last Captured  or  📂 Upload Image  to begin"
            "</span>"
        )
        self._img_name_lbl.setText("")
        self._set_card(self._card_total,   "—")
        self._set_card(self._card_classes, "—", self._FG)
        self._set_card(self._card_ms,      "—")
        self._set_card(self._card_rx,      "—", self._FG)
        self._log_clear()
        self._bottom_lbl.setText("")

    # ─────────────────────────────────────────────────────────────────────────
    #  Log helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _log_clear(self):
        self._log.clear()

    def _log_line(self, _tag: str, text: str):
        """Append plain text (colour handled by HTML methods)."""
        cursor = self._log.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        cursor.insertText(text)
        self._log.setTextCursor(cursor)
        self._log.ensureCursorVisible()

    def _log_html(self, html: str):
        cursor = self._log.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        cursor.insertHtml(html)
        self._log.setTextCursor(cursor)
        self._log.ensureCursorVisible()

    def _log_pair(self, label: str, value: str):
        self._log_html(
            f"<span style='color:{self._DIM};'>  {label:<10}: </span>"
            f"<span style='color:#7ecfff;'>{value}</span><br>"
        )
