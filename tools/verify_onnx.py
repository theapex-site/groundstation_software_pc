"""
tools/verify_onnx.py
────────────────────
Checks that the exported .onnx gives the same detections as the .pth it came
from. Run after tools/export_onnx.py, before shipping a build:

    python tools/verify_onnx.py

Needs torch, torchvision and onnxruntime, so it runs on a development machine
only — never on a customer's.

Compares the whole output, not just detections above the app's threshold, so a
disagreement anywhere shows up. Sweeps several resolutions too, because the
export only ever saw 480x640 and the dynamic axes are what make other sizes
work. Exits non-zero if anything disagrees.
"""
import os
import sys

import cv2
import numpy as np
import torch
from torchvision.models.detection import (
    fasterrcnn_resnet50_fpn_v2,
    FasterRCNN_ResNet50_FPN_V2_Weights,
)
import onnxruntime as ort

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PTH  = os.path.join(HERE, "models", "fasterrcnn_resnet50_fpn_v2_coco.pth")
ONNX = os.path.join(HERE, "models", "fasterrcnn_resnet50_fpn_v2_coco.onnx")
CAM  = os.path.join(HERE, "APEX_GCS_Saved", "images", "camera")

# A box may differ by this much and still be the same detection. Export is
# float32 throughout, so the real figure observed is ~0.006 px.
BOX_TOL   = 1.0      # pixels
SCORE_TOL = 0.01

SIZES = [(320, 240), (640, 480), (800, 600), (1024, 768), (1280, 960), (500, 900)]


def read(path):
    bgr = cv2.imread(path)
    return None if bgr is None else cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def main() -> int:
    for p in (PTH, ONNX):
        if not os.path.exists(p):
            print("Missing {}\nRun tools/fetch_model.py then tools/export_onnx.py.".format(p))
            return 1

    categories = list(FasterRCNN_ResNet50_FPN_V2_Weights.COCO_V1.meta["categories"])
    model = fasterrcnn_resnet50_fpn_v2(
        weights=None, weights_backbone=None, num_classes=len(categories))
    model.load_state_dict(torch.load(PTH, map_location="cpu"))
    model.eval()
    sess = ort.InferenceSession(ONNX, providers=["CPUExecutionProvider"])

    cases = []
    if os.path.isdir(CAM):
        for f in sorted(os.listdir(CAM)):
            if f.lower().endswith((".jpg", ".jpeg", ".png")):
                rgb = read(os.path.join(CAM, f))
                # A truncated downlink that cv2 cannot decode is skipped here;
                # the app reports it as a decode error, which is tested elsewhere.
                if rgb is not None:
                    cases.append((f, rgb))
    if not cases:
        print("No readable images in {} — nothing to compare.".format(CAM))
        return 1

    biggest = max(cases, key=lambda c: c[1].size)[1]
    for (w, h) in SIZES:
        cases.append(("resized {}x{}".format(w, h), cv2.resize(biggest, (w, h))))

    print("comparing {} cases\n".format(len(cases)))
    print("  {:<34} {:>5} {:>5} {:>10} {:>11}".format(
        "case", "torch", "onnx", "max dbox", "max dscore"))
    print("  " + "-" * 70)

    worst_box = worst_score = 0.0
    bad = []
    for name, rgb in cases:
        x = torch.from_numpy(np.ascontiguousarray(rgb)).permute(2, 0, 1).float() / 255.0
        with torch.inference_mode():
            t = model([x])[0]
        o_boxes, o_labels, o_scores = sess.run(None, {"image": x.numpy()})

        tb, tl, ts = t["boxes"].numpy(), t["labels"].numpy(), t["scores"].numpy()
        note = ""
        if len(ts) != len(o_scores):
            note = "COUNT {} vs {}".format(len(ts), len(o_scores))
            bad.append(name)
            db = ds = float("nan")
        else:
            db = float(np.abs(tb - o_boxes).max()) if len(ts) else 0.0
            ds = float(np.abs(ts - o_scores).max()) if len(ts) else 0.0
            if len(ts) and not (tl == o_labels).all():
                note = "LABELS DIFFER"
                bad.append(name)
            elif db > BOX_TOL or ds > SCORE_TOL:
                note = "OUTSIDE TOLERANCE"
                bad.append(name)
            worst_box = max(worst_box, db)
            worst_score = max(worst_score, ds)

        print("  {:<34} {:>5} {:>5} {:>10.4f} {:>11.6f}  {}".format(
            name[:34], len(ts), len(o_scores), db, ds, note))

    print("\n  worst box difference:   {:.4f} px (tolerance {})".format(worst_box, BOX_TOL))
    print("  worst score difference: {:.6f} (tolerance {})".format(worst_score, SCORE_TOL))
    if bad:
        print("\n  DISAGREES on: " + ", ".join(bad))
        print("  Do not ship this export.")
        return 1
    print("\n  ONNX matches torch — safe to ship.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
