# apex_gcs.spec
import os
import sys

# ── Point PyInstaller to your site-packages explicitly ──
_site = r"C:\Users\tgvis\AppData\Local\Programs\Python\Python311\Lib\site-packages"
if _site not in sys.path:
    sys.path.insert(0, _site)

from PyInstaller.utils.hooks import collect_all

# torch and torchvision are gone: detection runs on onnxruntime, which is
# ~15 MB against torch's 530 MB in the build.
_ort_d, _ort_b, _ort_h = collect_all('onnxruntime')
_cv_d,  _cv_b,  _cv_h  = collect_all('cv2')
# numpy must be collected explicitly now. collect_all('torch') used to drag in
# the BLAS/OpenMP DLLs numpy also loads, so numpy worked by accident; without
# torch the frozen build died at startup with "DLL load failed while importing
# _multiarray_umath".
_np_d,  _np_b,  _np_h  = collect_all('numpy')

added_files = [
    ("images",                      "images"),
    # Named individually, not the whole models/ folder: a developer tree also
    # holds the 175 MB .pth that tools/export_onnx.py converts from, and
    # copying the folder shipped it to customers even though the app only ever
    # loads the .onnx.
    ("models/fasterrcnn_resnet50_fpn_v2_coco.onnx", "models"),
    ("models/space_club_cubesat.glb",               "models"),
    # Silicon Labs CP210x driver, so a customer with no internet on the bench
    # is not stuck hunting for it. Unmodified package, catalog included: the
    # .cat signs a hash of silabser.inf, so editing the INF would invalidate
    # the signature and Windows would then refuse to load it.
    ("drivers",                     "drivers"),
]

a = Analysis(
    ["main.py"],
    pathex=[os.path.abspath("."), _site],
    binaries=_ort_b + _cv_b + _np_b,
    datas=added_files + _ort_d + _cv_d + _np_d,
    hiddenimports=list(set(
        _ort_h + _cv_h + _np_h
        + [
            "PyQt5.QtWebEngineWidgets",
            "PyQt5.QtWebEngine",
            "PyQt5.QtOpenGL",
            "OpenGL.GL",
            "OpenGL.GLU",
            "OpenGL.arrays.vbo",
            "OpenGL.platform.win32",

            "serial",
            "serial.tools",
            "serial.tools.list_ports",

            "trimesh",
            "trimesh.visual",
            "trimesh.visual.color",

            "openpyxl",
            "openpyxl.styles",
            "openpyxl.utils",

            "PIL",
            "PIL.Image",
            "PIL.ImageDraw",
            "PIL.ImageFont",
            "PIL.ImageFile",

            "numpy",
            "matplotlib",

            "zlib",
            "struct",
            "pickle",
            "webbrowser",

            # widgets/driver_install.py — imported lazily, so PyInstaller's
            # analysis does not see them.
            "ctypes",
            "ctypes.wintypes",
            "winreg",

            "requests",


            # ===== Detection dependencies =====
            "yaml",
            "cv2",
            "onnxruntime",
        ]
    )),
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "ultralytics",   # AGPL-3.0 — must not ship in a commercial build
        "gevent",
        "tensorflow",
        "keras",
        "sklearn",
        "scipy",
        "torch",
        "torchvision",
        # Swept in from site-packages, not imported anywhere in the app:
        # jaxlib alone was 195 MB of a 1.5 GB build, pandas another 18 MB.
        "jax",
        "jaxlib",
        "pandas",
        "IPython",
        "jupyter",
        "notebook",
        "tkinter",
        "PyQt4",
        "PySide2",
        "PySide6",
        "wx",
        "gi",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    # True because this is a one-folder build: the binaries belong to COLLECT
    # below. With False, PyInstaller also wrote a self-contained-looking
    # APEX_GCS.exe straight into dist\ whose DLLs were never beside it, so
    # running that one died with "Error loading Python DLL python311.dll".
    # The real executable is dist\APEX_GCS\APEX_GCS.exe.
    exclude_binaries=True,
    name="APEX_GCS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    icon="icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="APEX_GCS",
)
