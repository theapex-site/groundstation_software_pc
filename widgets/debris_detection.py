"""
widgets/debris_detection.py
────────────────────────────
APEX GCS — Debris Detection Dialog

A PyQt5 popup dialog that performs YOLO-based debris detection on images.

Features:
  - Triggered from "📤 Start Debris Detection" button in RightPanel
  - "Last Captured" button auto-loads latest image from APEX_GCS_Saved/images/camera/
  - Upload button for manual image selection
  - Live annotated preview with coloured bounding boxes
  - Full metrics panel: confidence, risk, area, bbox coords, centroid, aspect ratio, etc.
  - "Test" button runs YOLO detection
  - "Save" button saves annotated image → APEX_GCS_Saved/images/debrie_detect/
    and a matching .txt report (same stem as image) in the same folder
  - Model loaded from:  models/best.pt

Save structure:
  APEX_GCS_Saved/
    images/
      camera/          ← camera captures (existing)
      debrie_detect/   ← debris detection outputs (new)
        IMG_xxx_debris.jpg
        IMG_xxx_debris.txt   ← plain-text report (same stem)
"""

from __future__ import annotations

import datetime
import math
import os
import sys
import threading
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap

# ── BASE_PATH (mirrors config.py / right_panel.py pattern) ───────────────────
if getattr(sys, "frozen", False):
    BASE_PATH = sys._MEIPASS
else:
    BASE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

MODEL_PATH   = os.path.join(BASE_PATH, "models", "best.pt")
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_NUM_THREADS"] = "1"
CAMERA_DIR   = os.path.join(BASE_PATH, "APEX_GCS_Saved", "images", "camera")
DEBRIS_DIR   = os.path.join(BASE_PATH, "APEX_GCS_Saved", "images", "debrie_detect")

# ── Risk palette (matches GCS stylesheet accent colours) ─────────────────────
RISK_COLOR_BGR = {
    "CRITICAL": (0,   0,   220),
    "HIGH":     (0,  120,  255),
    "MEDIUM":   (0,  200,  255),
    "LOW":      (50, 200,   50),
    "NONE":     (80, 200,   80),
}
RISK_COLOR_QT = {
    "CRITICAL": "#ff4444",
    "HIGH":     "#ff8800",
    "MEDIUM":   "#ffdd00",
    "LOW":      "#44cc44",
    "NONE":     "#44cc44",
}

RISK_THRESHOLDS = {"CRITICAL": 0.80, "HIGH": 0.60, "MEDIUM": 0.40, "LOW": 0.20}

PRECAUTIONS = {
    "CRITICAL": [
        "IMMEDIATE MANEUVER REQUIRED",
        "Initiate avoidance burn NOW",
        "Alert mission control",
        "Log event + switch to safe-mode",
    ],
    "HIGH": [
        "Monitor closely — high-risk zone",
        "Prepare avoidance maneuver",
        "Log position + trajectory estimate",
        "Notify ground station next pass",
    ],
    "MEDIUM": [
        "Track on next image capture",
        "Log for conjunction analysis",
        "No immediate action required",
    ],
    "LOW": [
        "Low-confidence detection",
        "Capture additional images",
        "No action required",
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
#  Pure-logic helpers (no Qt dependency)
# ─────────────────────────────────────────────────────────────────────────────

def _score_risk(conf: float, area_pct: float) -> str:
    size_bonus = min(area_pct / 10, 0.25)
    combined   = conf + size_bonus
    if combined >= RISK_THRESHOLDS["CRITICAL"]: return "CRITICAL"
    if combined >= RISK_THRESHOLDS["HIGH"]:     return "HIGH"
    if combined >= RISK_THRESHOLDS["MEDIUM"]:   return "MEDIUM"
    return "LOW"


def _compute_metrics(box_xyxy, conf, cls_name, img_w, img_h, obj_id) -> dict:
    x1, y1, x2, y2 = box_xyxy
    bw      = x2 - x1
    bh      = y2 - y1
    area_px = bw * bh
    area_pct= area_px / (img_w * img_h) * 100
    cx_px   = (x1 + x2) / 2
    cy_px   = (y1 + y2) / 2
    cx_norm = cx_px / img_w
    cy_norm = cy_px / img_h
    diag_px = math.sqrt(bw ** 2 + bh ** 2)
    aspect  = bw / bh if bh > 0 else 0
    risk    = _score_risk(conf, area_pct)

    if area_pct >= 5:     proximity = "VERY CLOSE  (<50 km est.)"
    elif area_pct >= 1:   proximity = "CLOSE       (~50-200 km)"
    elif area_pct >= 0.3: proximity = "MODERATE    (~200-500 km)"
    else:                 proximity = "DISTANT     (>500 km est.)"

    fov_deg    = 5.0
    px_per_deg = img_w / fov_deg
    ang_w_deg  = bw / px_per_deg
    ang_h_deg  = bh / px_per_deg

    return {
        "id":           obj_id,
        "class":        cls_name,
        "confidence":   round(conf, 4),
        "risk":         risk,
        "bbox_px":      [round(x1), round(y1), round(x2), round(y2)],
        "bbox_norm":    [round(cx_norm, 4), round(cy_norm, 4),
                         round(bw / img_w, 4), round(bh / img_h, 4)],
        "centroid_px":  [round(cx_px, 1), round(cy_px, 1)],
        "width_px":     round(bw, 1),
        "height_px":    round(bh, 1),
        "area_px":      round(area_px),
        "area_pct":     round(area_pct, 4),
        "diag_px":      round(diag_px, 1),
        "aspect_ratio": round(aspect, 3),
        "angular_w_deg":round(ang_w_deg, 4),
        "angular_h_deg":round(ang_h_deg, 4),
        "proximity":    proximity,
        "precautions":  PRECAUTIONS[risk],
    }


def _annotate_image(img_bgr, detections):
    """Draw bounding boxes + centroid dots on a copy of img_bgr."""
    import cv2
    import numpy as np
    out = img_bgr.copy()
    img_h, img_w = out.shape[:2]
    for d in detections:
        x1, y1, x2, y2 = d["bbox_px"]
        color = RISK_COLOR_BGR[d["risk"]]
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        label = f"#{d['id']} {d['risk']} {d['confidence']:.2f}"
        (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
        ly = max(y1 - lh - 6, 0)
        cv2.rectangle(out, (x1, ly), (x1 + lw + 4, ly + lh + 6), color, -1)
        cv2.putText(out, label, (x1 + 2, ly + lh + 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1,
                    cv2.LINE_AA)
        cx, cy = d["centroid_px"]
        cv2.circle(out, (int(cx), int(cy)), 4, color, -1)
    # Watermark — exact same style as CubeSat camera images (PIL overlay)
    from PIL import Image as _Image, ImageDraw as _ImageDraw, ImageFont as _ImageFont
    pil_img  = _Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB)).convert("RGBA")
    overlay  = _Image.new("RGBA", pil_img.size, (0, 0, 0, 0))
    draw     = _ImageDraw.Draw(overlay)
    text     = f"APEX  |  Debris Detection  |  {datetime.datetime.now().strftime('%d-%m-%Y - %H:%M:%S')}"
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


def _build_txt_report(img_path: str, detections: list, overall: str,
                      elapsed_ms: float, conf_thresh: float,
                      img_w: int, img_h: int) -> str:
    """Return a plain-text report string matching the original log style."""
    ts  = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    sep = "=" * 52
    thn = "-" * 52
    lines = [
        sep,
        "  APEX GCS — DEBRIS DETECTION REPORT",
        sep,
        f"  Timestamp       : {ts}",
        f"  Image           : {Path(img_path).name}",
        f"  Image size      : {img_w} x {img_h} px",
        f"  Inference time  : {elapsed_ms:.1f} ms",
        f"  Conf threshold  : {conf_thresh}",
        f"  Total debris    : {len(detections)}",
        f"  Overall risk    : {overall}",
        thn,
    ]

    if detections:
        rs = {lvl: sum(1 for d in detections if d["risk"] == lvl)
              for lvl in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}
        lines += [
            f"  CRITICAL : {rs['CRITICAL']}",
            f"  HIGH     : {rs['HIGH']}",
            f"  MEDIUM   : {rs['MEDIUM']}",
            f"  LOW      : {rs['LOW']}",
            thn,
        ]

    for d in detections:
        lines += [
            f"  OBJECT #{d['id']:02d}  {d['class'].upper()}",
            f"  Risk            : {d['risk']}",
            f"  Confidence      : {d['confidence']:.4f}",
            f"  BBox (px)       : [{d['bbox_px'][0]},{d['bbox_px'][1]}] -> [{d['bbox_px'][2]},{d['bbox_px'][3]}]",
            f"  Centroid        : ({d['centroid_px'][0]:.0f}, {d['centroid_px'][1]:.0f}) px",
            f"  Width x Height  : {d['width_px']} x {d['height_px']} px",
            f"  Area            : {d['area_pct']:.4f}%  ({d['area_px']:,} px2)",
            f"  Diagonal        : {d['diag_px']} px",
            f"  Aspect ratio    : {d['aspect_ratio']:.3f}",
            f"  Norm cx,cy      : {d['bbox_norm'][0]:.4f}, {d['bbox_norm'][1]:.4f}",
            f"  Norm w,h        : {d['bbox_norm'][2]:.4f}, {d['bbox_norm'][3]:.4f}",
            f"  Angular size    : {d['angular_w_deg']:.4f} deg x {d['angular_h_deg']:.4f} deg",
            f"  Proximity       : {d['proximity']}",
            "  Precautions     :",
        ]
        for p in d["precautions"]:
            lines.append(f"    * {p}")
        lines.append(thn)

    if not detections:
        lines.append("  Clear — no debris detected.")
        lines.append(sep)

    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
#  Worker thread
# ─────────────────────────────────────────────────────────────────────────────

class _DetectWorker(QtCore.QThread):
    """Runs YOLO inference off the main thread."""

    finished  = pyqtSignal(object, list, str, float)   # annotated_bgr, detections, overall, ms
    errored   = pyqtSignal(str)

    def __init__(self, detector, img_path: str, conf: float):
        super().__init__()
        self.detector = detector
        self.img_path = img_path
        self.conf     = conf

    def run(self):
        try:
            import cv2, time
            img_bgr = cv2.imread(self.img_path)
            if img_bgr is None:
                raise FileNotFoundError(f"Cannot read image: {self.img_path}")
            H, W = img_bgr.shape[:2]

            t0 = time.time()
            results = self.detector(self.img_path, conf=self.conf, iou=0.45,
                                    verbose=False)
            elapsed = (time.time() - t0) * 1000

            detections = []
            for r in results:
                for i, box in enumerate(r.boxes):
                    y1, x1, y2, x2 = box.xyxy[0].tolist()
                    c  = float(box.conf[0])
                    ci = int(box.cls[0])
                    nm = self.detector.names.get(ci, f"class_{ci}")
                    detections.append(_compute_metrics([x1, y1, x2, y2],
                                                       c, nm, W, H, i + 1))

            overall = "NONE"
            for lvl in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
                if any(d["risk"] == lvl for d in detections):
                    overall = lvl
                    break

            annotated = _annotate_image(img_bgr, detections)
            self.finished.emit(annotated, detections, overall, elapsed)

        except Exception as exc:
            self.errored.emit(str(exc))


# ─────────────────────────────────────────────────────────────────────────────
#  Main dialog
# ─────────────────────────────────────────────────────────────────────────────

class DebrisDetectionDialog(QtWidgets.QDialog):
    """
    Pop-up dialog opened from RightPanel's "Start Debris Detection" button.
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
        self.setWindowTitle("APEX GCS — Debris Detection")
        self.setMinimumSize(1060, 680)
        self.setModal(True)
        self.setStyleSheet(self._build_stylesheet())

        self._img_path:       str | None = None
        self._annotated_bgr              = None   # numpy array
        self._detections:     list       = []
        self._overall_risk:   str        = "NONE"
        self._elapsed_ms:     float      = 0.0
        self._conf_thresh:    float      = 0.25
        self._detector                   = None
        self._worker: _DetectWorker | None = None

        self._build_ui()
        self._load_model_async()

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
        sub_lbl   = QtWidgets.QLabel("  DEBRIS DETECTION")
        sub_lbl.setStyleSheet(f"color:{self._FG}; font-size:20px; font-weight:bold; font-family:Consolas;")
        hdr.addWidget(title_lbl)
        hdr.addWidget(sub_lbl)
        hdr.addStretch()
        self._status_lbl = QtWidgets.QLabel("● LOADING MODEL…")
        self._status_lbl.setStyleSheet(f"color:#f0c040; font-family:Consolas; font-size:11px;")
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
        self._card_total = self._make_stat_card("TOTAL",   "—")
        self._card_risk  = self._make_stat_card("RISK",    "—")
        self._card_ms    = self._make_stat_card("INF. ms", "—")
        for c in (self._card_total, self._card_risk, self._card_ms):
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

    def _load_model_async(self):
        try:
            from ultralytics import YOLO
            mp = Path(MODEL_PATH)
            if not mp.exists():
                self._on_model_load_fail(f"Model not found: {mp}")
                return
            self._detector = YOLO(str(mp))
            self._on_model_ready(mp.name)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._on_model_load_fail(str(e))

    def _load_main_thread(self):
        pass

    @QtCore.pyqtSlot(str)
    def _on_model_ready(self, name: str):
        self._set_status(f"● READY  [{name}]", "#44cc44")
        self._btn_test.setEnabled(True)

    @QtCore.pyqtSlot(str)
    def _on_model_load_fail(self, msg: str):
        self._set_status(f"● MODEL ERROR", "#ff4444")
        self._btn_test.setEnabled(False)
        self._bottom_lbl.setText(f"Model error: {msg}")

    # ─────────────────────────────────────────────────────────────────────────
    #  Image loading
    # ─────────────────────────────────────────────────────────────────────────

    def _load_last_captured(self):
        """Load the most recently saved camera image from APEX_GCS_Saved/images/camera/."""
        cam_dir = Path(CAMERA_DIR)
        if not cam_dir.exists():
            QtWidgets.QMessageBox.warning(
                self, "No Camera Images",
                f"Camera directory not found:\n{CAMERA_DIR}"
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
            QtWidgets.QMessageBox.information(
                self, "No Images",
                "No captured images found in:\n" + str(cam_dir)
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
        self._overall_risk   = "NONE"
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
                raise ValueError("Cannot read image")
            self._show_bgr(bgr)
        except Exception as e:
            self._img_label.setText(f"<span style='color:#ff4444;'>Error: {e}</span>")

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
            QtWidgets.QMessageBox.information(
                self, "No Image",
                "Please load an image first (Last Captured or Upload)."
            )
            return
        if self._detector is None:
            QtWidgets.QMessageBox.warning(
                self, "Model Not Ready",
                "The YOLO model is still loading or failed to load.\n"
                f"Expected path: {MODEL_PATH}"
            )
            return

        self._btn_test.setEnabled(False)
        self._progress.setVisible(True)
        self._conf_thresh = self._conf_spin.value()
        self._bottom_lbl.setText("Running inference…")

        self._worker = _DetectWorker(self._detector, self._img_path, self._conf_thresh)
        self._worker.finished.connect(self._on_detect_finished)
        self._worker.errored.connect(self._on_detect_error)
        self._worker.start()

    @QtCore.pyqtSlot(object, list, str, float)
    def _on_detect_finished(self, annotated_bgr, detections, overall, elapsed_ms):
        self._annotated_bgr  = annotated_bgr
        self._detections     = detections
        self._overall_risk   = overall
        self._elapsed_ms     = elapsed_ms

        self._show_bgr(annotated_bgr)
        self._update_report(detections, overall, elapsed_ms)
        self._progress.setVisible(False)
        self._btn_test.setEnabled(True)
        self._bottom_lbl.setText(
            f"Detection complete — {len(detections)} object(s) found  |  {elapsed_ms:.0f} ms"
        )

    @QtCore.pyqtSlot(str)
    def _on_detect_error(self, msg: str):
        self._progress.setVisible(False)
        self._btn_test.setEnabled(True)
        self._bottom_lbl.setText(f"Error: {msg}")
        QtWidgets.QMessageBox.critical(self, "Detection Error", msg)

    # ─────────────────────────────────────────────────────────────────────────
    #  Report panel
    # ─────────────────────────────────────────────────────────────────────────

    def _update_report(self, detections, overall, elapsed_ms):
        risk_color = RISK_COLOR_QT.get(overall, self._FG)
        self._set_card(self._card_total, str(len(detections)))
        self._set_card(self._card_risk,  overall, risk_color)
        self._set_card(self._card_ms,    f"{elapsed_ms:.0f}")

        self._log_clear()
        self._log_line("header",  "=" * 44 + "\n")
        self._log_line("header",  "  DETECTION REPORT\n")
        self._log_line("header",  "=" * 44 + "\n")
        self._log_pair("Time",      datetime.datetime.utcnow().strftime("%H:%M:%S UTC"))
        self._log_pair("Image",     Path(self._img_path).name)
        self._log_pair("Inference", f"{elapsed_ms:.1f} ms")
        self._log_pair("Debris",    str(len(detections)))
        tag = overall.lower() if overall in RISK_COLOR_QT else "none"
        self._log_html(f"<span style='color:{risk_color};'>  Overall   : {overall}</span><br>")

        if detections:
            rs = {lvl: sum(1 for d in detections if d["risk"] == lvl)
                  for lvl in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}
            self._log_line("dim", "─" * 44 + "\n")
            for lvl in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
                c = RISK_COLOR_QT[lvl]
                self._log_html(
                    f"<span style='color:{c};'>  {lvl:<10}: {rs[lvl]}</span><br>"
                )

        for d in detections:
            rc = RISK_COLOR_QT.get(d["risk"], self._FG)
            self._log_line("dim", "─" * 44 + "\n")
            self._log_html(
                f"<span style='color:{self._ORANGE}; font-weight:bold;'>"
                f"  OBJECT #{d['id']:02d}  {d['class'].upper()}</span><br>"
            )
            self._log_html(f"<span style='color:{rc};'>  Risk       : {d['risk']}</span><br>")
            pairs = [
                ("Confidence",   f"{d['confidence']:.4f}"),
                ("BBox (px)",    f"[{d['bbox_px'][0]},{d['bbox_px'][1]}]->[{d['bbox_px'][2]},{d['bbox_px'][3]}]"),
                ("Centroid",     f"({d['centroid_px'][0]:.0f}, {d['centroid_px'][1]:.0f}) px"),
                ("Width x H",    f"{d['width_px']} x {d['height_px']} px"),
                ("Area",         f"{d['area_pct']:.4f}%  ({d['area_px']:,} px2)"),
                ("Diagonal",     f"{d['diag_px']} px"),
                ("Aspect ratio", f"{d['aspect_ratio']:.3f}"),
                ("Norm cx,cy",   f"{d['bbox_norm'][0]:.4f}, {d['bbox_norm'][1]:.4f}"),
                ("Norm w,h",     f"{d['bbox_norm'][2]:.4f}, {d['bbox_norm'][3]:.4f}"),
                ("Angular",      f"{d['angular_w_deg']:.4f} x {d['angular_h_deg']:.4f} deg"),
                ("Proximity",    d["proximity"]),
            ]
            for lbl, val in pairs:
                self._log_html(
                    f"<span style='color:{self._DIM};'>  {lbl:<14}: </span>"
                    f"<span style='color:#7ecfff;'>{val}</span><br>"
                )
            self._log_html(f"<span style='color:{self._DIM};'>  Precautions:</span><br>")
            for p in d["precautions"]:
                self._log_html(f"<span style='color:{rc};'>    * {p}</span><br>")

        self._log_line("dim", "=" * 44 + "\n")
        if not detections:
            self._log_html(f"<span style='color:#44cc44;'>  Clear — no debris detected.</span><br>")

    # ─────────────────────────────────────────────────────────────────────────
    #  Save
    # ─────────────────────────────────────────────────────────────────────────

    def _save_result(self):
        if self._annotated_bgr is None:
            QtWidgets.QMessageBox.information(
                self, "Nothing to Save",
                "Run detection first (click ▶ Test)."
            )
            return

        import cv2
        os.makedirs(DEBRIS_DIR, exist_ok=True)

        # Build stem from source image name (preserves IMG_ naming)
        src_stem = Path(self._img_path).stem
        stem     = f"{src_stem}_debris"

        img_path = os.path.join(DEBRIS_DIR, f"{stem}.jpg")
        txt_path = os.path.join(DEBRIS_DIR, f"{stem}.txt")

        # Save annotated image
        cv2.imwrite(img_path, self._annotated_bgr)

        # Save plain-text report (same stem)
        import cv2 as _cv2
        img_bgr = _cv2.imread(self._img_path)
        H, W = img_bgr.shape[:2] if img_bgr is not None else (0, 0)
        report_txt = _build_txt_report(
            self._img_path, self._detections, self._overall_risk,
            self._elapsed_ms, self._conf_thresh, W, H,
        )
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(report_txt)

        try:
            import widgets.auth_backend as auth
            import threading as _t
            if hasattr(self, "_user_email") and self._user_email:
                _t.Thread(target=auth.upload_file, args=(self._user_email, img_path, "debris detection"), daemon=True).start()
                _t.Thread(target=auth.upload_file, args=(self._user_email, txt_path, "debris detection"), daemon=True).start()
        except Exception as _ue:
            print(f"[Drive] debris upload error: {_ue}")

        self._bottom_lbl.setText(f"Saved → {img_path}")
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("Saved")
        msg.setText(f"Annotated image saved to:\n{img_path}\n\nReport saved to:\n{txt_path}")
        msg.setIcon(QtWidgets.QMessageBox.Information)
        msg.setStyleSheet("""
            QMessageBox { background:#020617; color:#e2e8f0; font-family:Consolas; }
            QLabel { color:#e2e8f0; font-family:Consolas; font-size:11px; }
            QPushButton { background:#1e293b; color:#38bdf8; border:1px solid #38bdf8;
                          padding:6px 20px; border-radius:5px; font-family:Consolas; }
            QPushButton:hover { background:#38bdf8; color:#020617; }
        """)
        msg.exec_()

    # ─────────────────────────────────────────────────────────────────────────
    #  Clear
    # ─────────────────────────────────────────────────────────────────────────

    def _clear(self):
        self._img_path      = None
        self._annotated_bgr = None
        self._detections    = []
        self._overall_risk  = "NONE"
        self._elapsed_ms    = 0.0
        self._img_label.setPixmap(QPixmap())
        self._img_label.setText(
            f"<span style='color:{self._DIM}; font-family:Consolas; font-size:13px;'>"
            "Click  📷 Last Captured  or  📂 Upload Image  to begin"
            "</span>"
        )
        self._img_name_lbl.setText("")
        self._set_card(self._card_total, "—")
        self._set_card(self._card_risk,  "—", self._FG)
        self._set_card(self._card_ms,    "—")
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
