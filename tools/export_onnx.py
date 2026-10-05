"""
tools/export_onnx.py
────────────────────
Produces models/fasterrcnn_resnet50_fpn_v2_coco.onnx, which is what APEX GCS
actually ships and loads. Run this once after cloning:

    python tools/fetch_model.py      # downloads the .pth weights (~175 MB)
    python tools/export_onnx.py      # converts them to .onnx (~175 MB)

Only this script needs torch and torchvision. The app itself runs the exported
graph on onnxruntime, which is about 15 MB in the build against torch's 530 MB,
so neither is installed on a customer's machine.

The exported graph includes the model's own normalisation, resizing and NMS, so
the app feeds it exactly what torch was fed: RGB, CHW, float 0..1. Height and
width are dynamic axes, so detection still works on images of any size, not
just the ESP32-CAM's 480x640.

torchvision is BSD-3-Clause. Do NOT swap these weights for an Ultralytics YOLO
checkpoint: those are AGPL-3.0 and cannot ship in a closed-source commercial
build without an Ultralytics Enterprise Licence.
"""
import os
import sys
import time

import torch
from torchvision.models.detection import (
    fasterrcnn_resnet50_fpn_v2,
    FasterRCNN_ResNet50_FPN_V2_Weights,
)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PTH  = os.path.join(HERE, "models", "fasterrcnn_resnet50_fpn_v2_coco.pth")
ONNX = os.path.join(HERE, "models", "fasterrcnn_resnet50_fpn_v2_coco.onnx")


def check_categories(categories):
    """
    The class names are embedded in widgets/object_detection.py, because
    torchvision is not in the build to supply them. If torchvision ever
    renumbers COCO, the app would mislabel every detection silently, so the
    two lists are compared here rather than left to drift.
    """
    sys.path.insert(0, HERE)
    from widgets.object_detection import COCO_CATEGORIES
    if list(COCO_CATEGORIES) != list(categories):
        print("ERROR: COCO_CATEGORIES in widgets/object_detection.py no longer "
              "matches torchvision's list.")
        for i, (a, b) in enumerate(zip(COCO_CATEGORIES, categories)):
            if a != b:
                print("  index {}: app has {!r}, torchvision has {!r}".format(i, a, b))
        if len(COCO_CATEGORIES) != len(categories):
            print("  length: app has {}, torchvision has {}".format(
                len(COCO_CATEGORIES), len(categories)))
        return False
    print("class names match the app's embedded list ({} entries)".format(len(categories)))
    return True


def main() -> int:
    if not os.path.exists(PTH):
        print("Weights not found: {}\nRun tools/fetch_model.py first.".format(PTH))
        return 1

    categories = list(FasterRCNN_ResNet50_FPN_V2_Weights.COCO_V1.meta["categories"])
    if not check_categories(categories):
        return 1

    model = fasterrcnn_resnet50_fpn_v2(
        weights=None, weights_backbone=None, num_classes=len(categories))
    model.load_state_dict(torch.load(PTH, map_location="cpu"))
    model.eval()

    dummy = [torch.rand(3, 480, 640)]
    t0 = time.time()
    torch.onnx.export(
        model, (dummy,), ONNX,
        opset_version=16,
        input_names=["image"],
        output_names=["boxes", "labels", "scores"],
        dynamic_axes={
            "image":  {1: "height", 2: "width"},
            "boxes":  {0: "n"},
            "labels": {0: "n"},
            "scores": {0: "n"},
        },
        do_constant_folding=True,
        # The TorchScript exporter, not the newer torch.export one: detection
        # models rely on data-dependent control flow that the latter rejects.
        dynamo=False,
    )
    print("exported in {:.0f}s -> {} ({:.0f} MB)".format(
        time.time() - t0, ONNX, os.path.getsize(ONNX) / 1e6))
    print("\nVerify against torch before shipping:  python tools/verify_onnx.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
