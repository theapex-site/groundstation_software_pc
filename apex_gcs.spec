# apex_gcs.spec
import os
import sys

# ── Point PyInstaller to your site-packages explicitly ──
_site = r"C:\Users\tgvis\AppData\Local\Programs\Python\Python311\Lib\site-packages"
if _site not in sys.path:
    sys.path.insert(0, _site)

from PyInstaller.utils.hooks import collect_all, collect_submodules

_ul_d,  _ul_b,  _ul_h  = collect_all('ultralytics')
_tor_d, _tor_b, _tor_h = collect_all('torch')
_tv_d,  _tv_b,  _tv_h  = collect_all('torchvision')
_cv_d,  _cv_b,  _cv_h  = collect_all('cv2')

added_files = [
    ("images",                      "images"),
    ("models",                      "models"),
    ("widgets/credentials.json",    "widgets"),
    ("widgets/drive_oauth.json",    "widgets"),
    ("widgets/drive_token.pickle",  "widgets"),
]

a = Analysis(
    ["main.py"],
    pathex=[os.path.abspath("."), _site],
    binaries=_ul_b + _tor_b + _tv_b + _cv_b,
    datas=added_files + _ul_d + _tor_d + _tv_d + _cv_d,
    hiddenimports=list(set(
        _ul_h + _tor_h + _tv_h + _cv_h
        + collect_submodules('ultralytics')
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

            "gspread",
            "gspread.exceptions",

            "oauth2client",
            "oauth2client.service_account",
            "oauth2client.crypt",

            "googleapiclient",
            "googleapiclient.discovery",
            "googleapiclient.http",

            "google.auth",
            "google.auth.transport.requests",

            "google_auth_oauthlib",
            "google_auth_oauthlib.flow",

            "httplib2",

            "smtplib",
            "email.mime.text",

            # ===== ULTRALYTICS =====
            "ultralytics",
            "ultralytics.nn",
            "ultralytics.nn.tasks",
            "ultralytics.models",
            "ultralytics.models.yolo",
            "ultralytics.models.yolo.detect",
            "ultralytics.models.yolo.detect.predict",
            "ultralytics.utils",
            "ultralytics.utils.ops",
            "ultralytics.utils.torch_utils",
            "ultralytics.cfg",
            "ultralytics.engine",
            "ultralytics.engine.predictor",
            "ultralytics.engine.results",
            "ultralytics.data",
            "ultralytics.data.augment",

            # ===== YOLO dependencies =====
            "yaml",
            "cv2",
            "torch",
            "torchvision",
            "torchvision.transforms",
            "torchvision.ops",
        ]
    )),
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "gevent",
        "tensorflow",
        "keras",
        "sklearn",
        "scipy",        
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
    exclude_binaries=False,
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
