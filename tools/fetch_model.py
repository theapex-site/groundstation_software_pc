"""
tools/fetch_model.py
────────────────────
Downloads the COCO-pretrained Faster R-CNN weights that APEX GCS bundles at
models/fasterrcnn_resnet50_fpn_v2_coco.pth.

The file is ~175 MB, so it is kept out of git. Run this once after cloning,
before building with apex_gcs.spec:

    python tools/fetch_model.py

torchvision is BSD-3-Clause. Do NOT swap these weights for an Ultralytics
YOLO checkpoint: those are AGPL-3.0 and cannot ship in a closed-source
commercial build without an Ultralytics Enterprise Licence.
"""
import os
import sys

import torch
from torchvision.models.detection import FasterRCNN_ResNet50_FPN_V2_Weights as W

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT  = os.path.join(HERE, "models", "fasterrcnn_resnet50_fpn_v2_coco.pth")


def main() -> int:
    if os.path.exists(OUT):
        print(f"Already present: {OUT} ({os.path.getsize(OUT) / 1e6:.1f} MB)")
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    print(f"Downloading {W.COCO_V1.url}")
    torch.save(W.COCO_V1.get_state_dict(progress=True), OUT)
    print(f"Saved {OUT} ({os.path.getsize(OUT) / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
