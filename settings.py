"""
User preferences, persisted between sessions.

Before this, baud rate, the graph sample windows and the background animation
were either hardcoded in config.py or reachable only through the APEX_STATIC_BG
and APEX_BG_FPS environment variables, which no customer was ever going to
find. config.py keeps the defaults; this module is what the app reads.

Values live in the registry under HKCU\\Software\\APEX\\GroundStation via
QSettings, so there is no file to ship or clean up.
"""

from __future__ import annotations

import json
import os

from PyQt5 import QtCore

import config

ORG = "APEX"
APP = "GroundStation"

# key -> (default, type). The type matters because QSettings on Windows
# returns every value as a string.
_DEFAULTS: dict[str, tuple[object, type]] = {
    "serial/baud":        (config.BAUD_RATE, int),
    "display/animate":    (True, bool),
    "display/fps":        (30, int),
    "graphs/mini_points": (config.MINI_GRAPH_MAX_POINTS, int),
    "graphs/popup_points": (config.POPUP_GRAPH_MAX_POINTS, int),
}

BAUD_CHOICES = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]


def _store() -> QtCore.QSettings:
    return QtCore.QSettings(ORG, APP)


def get(key: str):
    default, kind = _DEFAULTS[key]
    raw = _store().value(key, default)
    if kind is bool:
        # QSettings hands back the string "false" for a stored False, and a
        # non-empty string is truthy, so this cannot go through bool().
        if isinstance(raw, str):
            return raw.lower() in ("1", "true", "yes", "on")
        return bool(raw)
    try:
        return kind(raw)
    except (TypeError, ValueError):
        return default


def set_(key: str, value) -> None:
    if key not in _DEFAULTS:
        raise KeyError(key)
    _store().setValue(key, value)


def reset() -> None:
    s = _store()
    for key in _DEFAULTS:
        s.remove(key)


# ── resolved accessors ───────────────────────────────────────────────────────
def baud() -> int:
    return get("serial/baud")


def animate() -> bool:
    """The env var still wins, so an operator on a slow machine can override
    the stored preference without opening the app's own settings."""
    if os.environ.get("APEX_STATIC_BG", "") not in ("", "0"):
        return False
    return get("display/animate")


def frame_ms() -> int:
    env = os.environ.get("APEX_BG_FPS", "")
    fps = float(env) if env else get("display/fps")
    return max(16, int(1000 / max(fps, 1)))


# ── RTG canvas layout ─────────────────────────────────────────────────────────
# A list of {"key", "col", "row", "col_span", "row_span"} dicts — which
# channels the operator put on the RTG canvas, where, and at what size. This
# is structured data, not a scalar, so it doesn't fit the typed _DEFAULTS
# table above; it gets its own JSON-encoded key in the same QSettings store.
def get_rtg_layout() -> list[dict]:
    raw = _store().value("rtg/layout", "")
    if not raw:
        return []
    try:
        layout = json.loads(raw)
        return layout if isinstance(layout, list) else []
    except (TypeError, ValueError):
        return []


def set_rtg_layout(layout: list[dict]) -> None:
    _store().setValue("rtg/layout", json.dumps(layout))
