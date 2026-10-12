# apex_gcs.spec
#
# ── Release history ──────────────────────────────────────────────────────
# To rebuild an old version exactly: `git checkout <commit>`, then run
# PyInstaller + ISCC against this same spec/installer.iss as normal.
#
#   v1.1.0  b4e1318  2026-10-11  Fixed frozen build missing assets/ and
#                                fonts/ (icons/custom fonts were blank in
#                                the installed app).
#   v1.1.1  49ab1a2  2026-10-11  Version bump only, no code change.
#   v1.1.2  9051ec5  2026-10-11  Pixie thought-bubble messaging (replaces
#                                the toast when she's on screen); Firestore
#                                profile cached after login instead of
#                                refetched every time Settings opens.
#   v1.2.0  121133c  2026-10-11  In-app self-updater: download progress
#                                dialog with Pixie's animation, Cancel,
#                                silent elevated install + auto-relaunch
#                                (feature landed in a67e6df, just under
#                                this).
#   v1.2.1  087deab  2026-10-11  Fixed a startup crash for non-admin users
#                                (app data moved to %LOCALAPPDATA%); fixed
#                                the self-updater not auto-relaunching;
#                                licence key shown masked in profile;
#                                COM port pickers filtered to Silicon Labs
#                                devices only; no-internet / back-online
#                                screens on sign-in (features landed in
#                                77b4d86, just under this).
#   v1.2.2  13f6f32  2026-10-11  GroundStation now launches maximized;
#                                app icon rebuilt at full resolution (was
#                                blurry upscaled from 48x48 everywhere
#                                Windows shows it larger); manual sign-in
#                                now plays the "Signing you in..." gif
#                                instead of just a wait cursor; new update
#                                dialog shows each release's GitHub notes,
#                                auto-sized to the text, before download;
#                                fixed Settings' "Check for Updates" button
#                                silently doing nothing (now also reports
#                                "already latest" / errors, not just a
#                                found update).
#   v1.2.3  c852c9a  2026-10-11  Self-updater no longer skips the
#                                auto-relaunch when the silent installer
#                                exits non-zero, and now logs the install
#                                so a partial update is diagnosable
#                                instead of silent; login window shows an
#                                "Opening Ground Control Station..." page
#                                while the main window builds instead of
#                                collapsing with nothing shown; fixed a
#                                QtWebEngineWidgets import-order crash
#                                from building GroundStation lazily.
#   v1.2.5  9bd8ac7  2026-10-12  Link status no longer false-"Signal Lost"
#                                during normal telemetry or an image
#                                downlink (was only ever refreshed by '@@'
#                                frames, which the firmware sends once at
#                                connect and not again); fixed CubeSat
#                                orientation fields being unpacked in the
#                                wrong order (yaw/pitch/roll were rotated
#                                one slot); new Compass Calibration dialog,
#                                auto-opened on first connect if the board
#                                has never been calibrated, reachable any
#                                time after from Settings; COM port picker
#                                no longer filters to Silicon Labs/CP210x
#                                devices only.
# ─────────────────────────────────────────────────────────────────────────
import os
import sys

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
    ("assets",                      "assets"),
    ("fonts",                       "fonts"),
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
    pathex=[os.path.abspath(".")],
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
            "packaging",
            "packaging.version",


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
