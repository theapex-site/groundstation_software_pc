"""
widgets/space_bg.py
───────────────────
The animated space scene from theapex.site, ported to Qt so APEX GCS and the
website look like one product.

Ported from initSpaceScene() in the site's js/script.js, keeping its constants:

  * four nebula clouds as radial gradients (buildNebula)
  * 320 warp stars per megapixel that fly toward the viewer, each drawn as a
    streak from its previous position to its new one, with twinkle
  * the ringed planet in the top-right corner
  * a shooting star every 2.6 s
  * four satellites on elliptical orbits, solar panels and glow

Not ported: the tripod dish rotator the site parks in the bottom-left corner.
In the GCS that corner is the telemetry panel, so it would never be visible.

The nebulae and the planet never change, so they are rendered once per window
size and cached; only the stars, shooters and satellites are redrawn each
frame. Set APEX_STATIC_BG=1 to freeze the scene if the animation is ever
unwelcome on a slow machine.
"""

from __future__ import annotations

import math
import os
import random
import time

from PyQt5 import QtCore, QtGui, QtWidgets

import theme

# ── Scene constants, as the website has them ────────────────────────────────
BASE = QtGui.QColor("#000000")          # css --navy-deep

NEBULAE = [
    (0.10, 0.15, 0.22, 0.18, (90, 10, 150)),     # purple, top left
    (0.82, 0.75, 0.28, 0.20, (10, 50, 160)),     # blue, bottom right
    (0.50, 0.92, 0.35, 0.13, (160, 10, 70)),     # magenta, bottom centre
    (0.88, 0.12, 0.18, 0.14, (10, 110, 140)),    # teal, top right
]

STAR_FOCAL = 130.0
STAR_SPEED = 2.1
STAR_COUNT_PER_MPX = 320
STAR_COLOURS = ("#b0c8ff", "#ffd6a5", "#ffffff")   # blue 7%, warm 5%, else white

SHOOTER_EVERY_S = 2.6

SATS = [
    # ox, oy, phase, speed, size, orbitW, orbitH
    (0.35, 0.22, 0.0, 0.0007, 12, 0.55, 0.09),
    (0.62, 0.60, 2.1, 0.0005, 10, 0.40, 0.07),
    (0.75, 0.35, 3.5, 0.0006, 11, 0.45, 0.08),
    (0.87, 0.18, 0.6, 0.0006, 18, 0.07, 0.04),
]

# The site runs at 60 fps; this window also has a live map, a 3D view and a
# 50 ms serial poll on the same core, so it asks for 30 and gets about 17.
# Measured against a 33% baseline, the animation costs roughly a further
# 13% of one core. Asking for less did not measurably save anything, so 30
# it is. APEX_BG_FPS overrides it; APEX_STATIC_BG=1 stops it entirely.
FRAME_MS = max(16, int(1000 / float(os.environ.get("APEX_BG_FPS", "30") or 30)))
STATIC = os.environ.get("APEX_STATIC_BG", "") not in ("", "0")


# ── Static layer: nebulae + planet, cached per size ─────────────────────────

_static_cache: dict[tuple[int, int], QtGui.QPixmap] = {}


def _draw_nebulae(p: QtGui.QPainter, w: int, h: int) -> None:
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
        p.save()
        p.translate(cx, cy)
        p.scale(rx / max_r, ry / max_r)
        p.translate(-cx, -cy)
        p.setBrush(QtGui.QBrush(grad))
        p.setPen(QtCore.Qt.NoPen)
        p.drawEllipse(QtCore.QPointF(cx, cy), max_r, max_r)
        p.restore()


def _draw_planet(p: QtGui.QPainter, w: int, h: int) -> None:
    px, py = w * 0.87, h * 0.12
    pr = min(w, h) * 0.065
    if pr <= 0:
        return
    p.setPen(QtCore.Qt.NoPen)

    body = QtGui.QRadialGradient(
        QtCore.QPointF(px - pr * 0.3, py - pr * 0.3), pr * 1.05)
    body.setColorAt(0.0, QtGui.QColor("#221540"))
    body.setColorAt(0.6, QtGui.QColor("#130c26"))
    body.setColorAt(1.0, QtGui.QColor("#080514"))
    p.setBrush(QtGui.QBrush(body))
    p.drawEllipse(QtCore.QPointF(px, py), pr, pr)

    # The ring: a wide stroked ellipse squashed to 28% height, as on the site.
    p.save()
    p.translate(px, py)
    p.scale(1.0, 0.28)
    pen = QtGui.QPen(QtGui.QColor(150, 80, 220, int(0.22 * 255)))
    pen.setWidthF(pr * 0.22)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.NoBrush)
    p.drawEllipse(QtCore.QPointF(0, 0), pr * 1.65, pr * 1.65)
    p.restore()

    atmos = QtGui.QRadialGradient(QtCore.QPointF(px, py), pr * 1.4)
    atmos.setColorAt(0.5, QtGui.QColor(110, 50, 190, 0))      # pr*0.7 of pr*1.4
    atmos.setColorAt(1.0, QtGui.QColor(110, 50, 190, int(0.12 * 255)))
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QBrush(atmos))
    p.drawEllipse(QtCore.QPointF(px, py), pr * 1.4, pr * 1.4)


def static_layer(width: int, height: int) -> QtGui.QPixmap:
    """Black base, nebulae and planet. Unchanging, so cached per size."""
    width, height = max(1, int(width)), max(1, int(height))
    key = (width, height)
    hit = _static_cache.get(key)
    if hit is not None:
        return hit

    # Drawn once and cached (see the module docstring), then blitted onto
    # the live widget-bound painter every frame — that painter already
    # scales correctly for the screen's devicePixelRatio since Qt does
    # that automatically for anything painted straight onto a QWidget, but
    # this standalone QPixmap does not get that for free: left at the
    # default ratio of 1, it would hold only `width`x`height` real pixels
    # and then get stretched up to fill however many physical pixels the
    # screen actually has, blurring just the nebulae/planet layer while
    # the stars drawn straight onto the widget stayed sharp.
    ratio = theme.device_ratio()
    pix = QtGui.QPixmap(int(width * ratio), int(height * ratio))
    pix.setDevicePixelRatio(ratio)
    pix.fill(BASE)
    p = QtGui.QPainter(pix)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    try:
        _draw_nebulae(p, width, height)
        _draw_planet(p, width, height)
    finally:
        p.end()

    if len(_static_cache) > 8:          # a drag-resize walks many sizes
        _static_cache.clear()
    _static_cache[key] = pix
    return pix


# ── Animated layer ──────────────────────────────────────────────────────────

class _Star:
    # __slots__ because there is one of these per star per megapixel and the
    # list is walked every frame; psx/psy are the previous screen position,
    # which the streak is drawn from.
    __slots__ = ("wx", "wy", "z", "sx", "sy", "psx", "psy", "ts", "to", "col")


class SpaceScene:
    """
    The moving scene. One instance per window; call draw(painter, w, h) from
    paintEvent and advance() from a timer.

    Stars live in world space (wx, wy, z); z only shrinks, so screen position
    is centre + (wx, wy) * (FOCAL / z). Because z starts large a star spawns
    near dead centre, then accelerates outward along a straight line through
    the centre, which is what makes the field read as flying forward.
    """

    def __init__(self, seed: int = 20261005):
        self._rnd = random.Random(seed)
        self._w = self._h = 0
        self._stars: list[_Star] = []
        self._shooters: list[dict] = []
        self._sats = [dict(ox=o[0], oy=o[1], r=o[2], speed=o[3], size=o[4],
                           orbitW=o[5], orbitH=o[6], sx=0.0, sy=0.0)
                      for o in SATS]
        self._t = 0.0
        self._last_shooter = time.monotonic()
        self._z_max = 1000.0

    # ── star lifecycle ──
    def _reset_star(self, s: _Star, spawn_far: bool) -> None:
        r = self._rnd
        s.wx = (r.random() * 2 - 1) * self._w
        s.wy = (r.random() * 2 - 1) * self._h
        s.z = self._z_max * (0.4 + r.random() * 0.6) if spawn_far else self._z_max
        k = STAR_FOCAL / s.z
        s.sx = self._w / 2 + s.wx * k
        s.sy = self._h / 2 + s.wy * k

    def _make_star(self) -> _Star:
        r = self._rnd
        s = _Star()
        s.ts = r.random() * 0.014 + 0.003
        s.to = r.random() * math.pi * 2
        roll = r.random()
        s.col = (STAR_COLOURS[0] if roll < 0.07
                 else STAR_COLOURS[1] if roll < 0.12
                 else STAR_COLOURS[2])
        self._reset_star(s, True)
        s.psx, s.psy = s.sx, s.sy
        return s

    def resize(self, w: int, h: int) -> None:
        if (w, h) == (self._w, self._h):
            return
        self._w, self._h = max(1, w), max(1, h)
        self._z_max = max(self._w, self._h) * 1.1
        count = max(120, int(STAR_COUNT_PER_MPX * (self._w * self._h) / 1_000_000))
        self._stars = [self._make_star() for _ in range(count)]

    # ── per-frame state ──
    def advance(self) -> None:
        if STATIC:
            return
        self._t += 0.016 * (FRAME_MS / 16.0)     # keep the site's apparent speed

        now = time.monotonic()
        if now - self._last_shooter >= SHOOTER_EVERY_S:
            self._last_shooter = now
            r = self._rnd
            self._shooters.append(dict(
                x=r.random() * self._w, y=r.random() * self._h * 0.45,
                length=r.random() * 200 + 80, speed=r.random() * 16 + 10,
                angle=math.pi / 5 + r.random() * 0.35, life=1.0))

        # Stars: advance z, recompute screen position, recycle off-screen.
        step = STAR_SPEED * (FRAME_MS / 16.0)
        w, h = self._w, self._h
        for s in self._stars:
            s.psx, s.psy = s.sx, s.sy
            s.z -= step
            if s.z <= 1:
                self._reset_star(s, False)
                s.psx, s.psy = s.sx, s.sy
                continue
            k = STAR_FOCAL / s.z
            s.sx = w / 2 + s.wx * k
            s.sy = h / 2 + s.wy * k
            if s.sx < -60 or s.sx > w + 60 or s.sy < -60 or s.sy > h + 60:
                self._reset_star(s, False)
                s.psx, s.psy = s.sx, s.sy

        for sh in self._shooters:
            sh["x"] += math.cos(sh["angle"]) * sh["speed"]
            sh["y"] += math.sin(sh["angle"]) * sh["speed"]
            sh["life"] -= 0.025 * (FRAME_MS / 16.0)
        self._shooters = [s for s in self._shooters if s["life"] > 0]

        for sat in self._sats:
            sat["r"] += sat["speed"] * (FRAME_MS / 16.0)


    # ── drawing ──
    def _draw_stars(self, p: QtGui.QPainter) -> None:
        zmax = self._z_max
        t = self._t
        for s in self._stars:
            psx, psy = s.psx, s.psy
            depth = 1.0 - s.z / zmax              # 0 far away, 1 about to pass
            tw = 0.5 + 0.5 * math.sin(t * s.ts * 60 + s.to)
            rad = 0.3 + depth * depth * 2.2
            alpha = (0.2 + 0.8 * depth) * (0.6 + 0.4 * tw)

            col = QtGui.QColor(s.col)
            col.setAlphaF(max(0.0, min(1.0, alpha)))
            pen = QtGui.QPen(col)
            pen.setWidthF(max(0.6, rad))
            pen.setCapStyle(QtCore.Qt.RoundCap)
            p.setPen(pen)
            # A streak from the last position to the new one: ~0 long for
            # distant stars, so they read as dots, and a motion trail up close.
            p.drawLine(QtCore.QLineF(psx, psy, s.sx, s.sy))

    def _draw_shooters(self, p: QtGui.QPainter) -> None:
        for sh in self._shooters:
            tx = sh["x"] - math.cos(sh["angle"]) * sh["length"]
            ty = sh["y"] - math.sin(sh["angle"]) * sh["length"]
            grad = QtGui.QLinearGradient(tx, ty, sh["x"], sh["y"])
            grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, 0))
            grad.setColorAt(1.0, QtGui.QColor(200, 230, 255,
                                              int(max(0.0, sh["life"]) * 0.9 * 255)))
            pen = QtGui.QPen(QtGui.QBrush(grad), 1.5)
            p.setPen(pen)
            p.drawLine(QtCore.QLineF(tx, ty, sh["x"], sh["y"]))

    def _draw_sat(self, p: QtGui.QPainter, x: float, y: float, sz: float,
                  tracked: bool) -> None:
        body   = QtGui.QColor("#5a4a22" if tracked else "#3a4060")
        panel  = QtGui.QColor("#e8b923" if tracked else "#2a5fa0")
        grid   = (QtGui.QColor(255, 220, 100, int(0.6 * 255)) if tracked
                  else QtGui.QColor(80, 160, 255, int(0.5 * 255)))
        glow   = (250, 204, 21) if tracked else (60, 130, 255)
        glow_a = 0.24 if tracked else 0.12

        p.save()
        p.translate(x, y)

        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(body)
        p.drawRect(QtCore.QRectF(-sz * 0.6, -sz * 0.35, sz * 1.2, sz * 0.7))

        p.setBrush(panel)
        p.drawRect(QtCore.QRectF(-sz * 1.8, -sz * 0.15, sz * 0.9, sz * 0.3))
        p.drawRect(QtCore.QRectF(sz * 0.9, -sz * 0.15, sz * 0.9, sz * 0.3))

        pen = QtGui.QPen(grid)
        pen.setWidthF(0.8)
        p.setPen(pen)
        for i in (1, 2):
            p.drawLine(QtCore.QLineF(-sz * 1.8 + sz * 0.3 * i, -sz * 0.15,
                                     -sz * 1.8 + sz * 0.3 * i, sz * 0.15))
            p.drawLine(QtCore.QLineF(sz * 0.9 + sz * 0.3 * i, -sz * 0.15,
                                     sz * 0.9 + sz * 0.3 * i, sz * 0.15))

        pen = QtGui.QPen(QtGui.QColor(255, 255, 255, int(0.7 * 255)))
        pen.setWidthF(1.0)
        p.setPen(pen)
        p.drawLine(QtCore.QLineF(0, -sz * 0.35, 0, -sz * 0.9))
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor("#ffffff"))
        p.drawEllipse(QtCore.QPointF(0, -sz * 0.9), 1.5, 1.5)

        g = QtGui.QRadialGradient(QtCore.QPointF(0, 0), sz * 2.5)
        g.setColorAt(0.0, QtGui.QColor(*glow, int(glow_a * 255)))
        g.setColorAt(1.0, QtGui.QColor(*glow, 0))
        p.setBrush(QtGui.QBrush(g))
        p.drawEllipse(QtCore.QPointF(0, 0), sz * 2.5, sz * 2.5)
        p.restore()

    def _draw_sats(self, p: QtGui.QPainter) -> None:
        for sat in self._sats:
            sat["sx"] = sat["ox"] * self._w + math.cos(sat["r"]) * sat["orbitW"] * self._w
            sat["sy"] = sat["oy"] * self._h + math.sin(sat["r"]) * sat["orbitH"] * self._h
        # The site highlights whichever satellite its corner dish is tracking.
        # The dish is not drawn here, but the highlight is kept so one
        # satellite still stands out in APEX gold as it does on the site.
        tracked = min(self._sats,
                      key=lambda s: math.hypot(s["sx"] - 0.07 * self._w,
                                               s["sy"] - 0.95 * self._h))
        for sat in self._sats:
            self._draw_sat(p, sat["sx"], sat["sy"], sat["size"], sat is tracked)

    def draw(self, p: QtGui.QPainter, w: int, h: int) -> None:
        self.resize(w, h)
        p.drawPixmap(0, 0, static_layer(w, h))
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self._draw_stars(p)
        self._draw_shooters(p)
        self._draw_sats(p)


# ── Back-compatible helper ──────────────────────────────────────────────────

def backdrop(width: int, height: int) -> QtGui.QPixmap:
    """A single still frame. Used where there is no timer to drive animation."""
    # This pixmap is the final image (set straight onto a QLabel via
    # setPixmap), not blitted through an already-HiDPI-aware widget
    # painter like SpaceScene.draw() normally is — see static_layer()'s
    # comment for why it still needs its own devicePixelRatio set.
    ratio = theme.device_ratio()
    pix = QtGui.QPixmap(max(1, int(width * ratio)), max(1, int(height * ratio)))
    pix.setDevicePixelRatio(ratio)
    pix.fill(BASE)
    p = QtGui.QPainter(pix)
    try:
        scene = SpaceScene()
        scene.draw(p, int(width), int(height))
    finally:
        p.end()
    return pix
