"""
widgets/space_bg.py
───────────────────
Draws the same space backdrop the website uses, so APEX GCS and theapex.site
look like one product.

Ported from the starfield canvas in the site's js/script.js: a black base, four
nebula clouds as radial gradients, and a field of stars with the same colour
mix (mostly white, a few blue-white, a few warm). The site also flies the
stars toward the viewer and throws in shooting stars; none of that is here on
purpose. Motion behind live telemetry is a distraction, and in a ground station
anything that moves should mean something changed.

Rendered once per size and cached, because this is painted on every resize.
"""

from __future__ import annotations

import random

from PyQt5 import QtCore, QtGui

# css/style.css --navy-deep. The clouds and stars are drawn over this.
BASE = QtGui.QColor("#000000")

# Positions and colours as the website's buildNebula() has them: fractions of
# the surface, with the same rgba stops.
NEBULAE = [
    (0.10, 0.15, 0.22, 0.18, (90, 10, 150)),     # purple, top left
    (0.82, 0.75, 0.28, 0.20, (10, 50, 160)),     # blue, bottom right
    (0.50, 0.92, 0.35, 0.13, (160, 10, 70)),     # magenta, bottom centre
    (0.88, 0.12, 0.18, 0.14, (10, 110, 140)),    # teal, top right
]

STAR_COUNT_PER_MPX = 320          # the site's density, at roughly one megapixel
STAR_BLUE  = QtGui.QColor("#b0c8ff")
STAR_WARM  = QtGui.QColor("#ffd6a5")
STAR_WHITE = QtGui.QColor("#ffffff")

_cache: dict[tuple[int, int], QtGui.QPixmap] = {}


def _draw_nebulae(painter: QtGui.QPainter, w: int, h: int) -> None:
    for fx, fy, frx, fry, (r, g, b) in NEBULAE:
        cx, cy = fx * w, fy * h
        rx, ry = frx * w, fry * h
        max_r = max(rx, ry)
        if max_r <= 0:
            continue

        grad = QtGui.QRadialGradient(QtCore.QPointF(cx, cy), max_r)
        grad.setColorAt(0.0, QtGui.QColor(r, g, b, int(0.14 * 255)))
        grad.setColorAt(0.5, QtGui.QColor(r, g, b, int(0.06 * 255)))
        grad.setColorAt(1.0, QtGui.QColor(r, g, b, 0))

        # Squashed into an ellipse the way the site scales its canvas, so the
        # clouds are wide and flat rather than circular.
        painter.save()
        painter.translate(cx, cy)
        painter.scale(rx / max_r, ry / max_r)
        painter.translate(-cx, -cy)
        painter.setBrush(QtGui.QBrush(grad))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawEllipse(QtCore.QPointF(cx, cy), max_r, max_r)
        painter.restore()


def _draw_stars(painter: QtGui.QPainter, w: int, h: int) -> None:
    # Seeded, so the same window size always gives the same sky. A field that
    # reshuffled on every resize would read as flicker.
    rnd = random.Random(20261005)
    count = max(120, int(STAR_COUNT_PER_MPX * (w * h) / 1_000_000))

    for _ in range(count):
        x = rnd.uniform(0, w)
        y = rnd.uniform(0, h)
        roll = rnd.random()
        if roll < 0.07:
            col = QtGui.QColor(STAR_BLUE)
        elif roll < 0.12:
            col = QtGui.QColor(STAR_WARM)
        else:
            col = QtGui.QColor(STAR_WHITE)

        # Most stars faint, a few bright, which is what gives the field depth
        # rather than looking like evenly scattered dots.
        col.setAlpha(rnd.randint(90, 255))

        # Small stars are drawn as a single hard pixel, not a sub-pixel circle:
        # an antialiased 0.7px ellipse spreads its brightness over four pixels
        # and comes out invisible against black. Only the few big ones get a
        # circle, where the soft edge actually helps them glow.
        if rnd.random() < 0.12:
            painter.setBrush(col)
            painter.setPen(QtCore.Qt.NoPen)
            painter.drawEllipse(QtCore.QPointF(x, y), 1.4, 1.4)
        else:
            pen = QtGui.QPen(col)
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawPoint(int(x), int(y))


def backdrop(width: int, height: int) -> QtGui.QPixmap:
    """The space backdrop at this size. Cached; safe to call from paintEvent."""
    width = max(1, int(width))
    height = max(1, int(height))
    key = (width, height)
    hit = _cache.get(key)
    if hit is not None:
        return hit

    pix = QtGui.QPixmap(width, height)
    pix.fill(BASE)
    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    try:
        _draw_nebulae(painter, width, height)
        _draw_stars(painter, width, height)
    finally:
        painter.end()

    # One window and one login panel is all this is ever asked for, but a drag
    # resize walks through many sizes, so the cache is bounded.
    if len(_cache) > 8:
        _cache.clear()
    _cache[key] = pix
    return pix
