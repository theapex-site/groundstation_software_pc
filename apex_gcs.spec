# apex_gcs.spec
import os
import sys

# ── Point PyInstaller to your site-packages explicitly ──
_site = r"C:\Users\tgvis\AppData\Local\Programs\Python\Python311\Lib\site-packages"
if _site not in sys.path:
    sys.path.insert(0, _site)

from PyInstaller.utils.hooks import collect_all, collect_submodules

_tor_d, _tor_b, _tor_h = collect_all('torch')
_tv_d,  _tv_b,  _tv_h  = collect_all('torchvision')
_cv_d,  _cv_b,  _cv_h  = collect_all('cv2')

added_files = [
    ("images",                      "images"),
    ("models",                      "models"),
    # Silicon Labs CP210x driver, so a customer with no internet on the bench
    # is not stuck hunting for it. Unmodified package, catalog included: the
    # .cat signs a hash of silabser.inf, so editing the INF would invalidate
    # the signature and Windows would then refuse to load it.
    ("drivers",                     "drivers"),
]

a = Analysis(
    ["main.py"],
    pathex=[os.path.abspath("."), _site],
    binaries=_tor_b + _tv_b + _cv_b,
    datas=added_files + _tor_d + _tv_d + _cv_d,
    hiddenimports=list(set(
        _tor_h + _tv_h + _cv_h
        + collect_submodules('torch')
        + collect_submodules('torchvision')
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
            "torch",
            "torchvision",
            "torchvision.transforms",
            "torchvision.ops",
            "torchvision.models",
            "torchvision.models.detection",
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
