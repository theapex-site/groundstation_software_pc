"""
tools/demo_station.py
─────────────────────
Run the real GroundStation window against a simulated CubeSat.

    py -3.12 tools/demo_station.py

No hardware, no COM port, no login. A fake serial port feeds the same
packet format the firmware sends, so everything downstream — telemetry,
graphs, the 3-D view, the map, the downlink — is the real code path.

The run narrates itself on a timeline so the staleness work is visible
without waiting for a real dropout:

    0s   telemetry starts at 1 Hz
    25s  signal lost — watch the panel dim, then withdraw its readings
    55s  signal returns, values repopulate

Press "Transmit Image" at any point for a simulated downlink: the progress
bar goes indeterminate while it waits for the header, then counts real
packets. Cancel works.
"""
import io
import math
import os
import random
import struct
import sys
import threading
import time
import zlib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PyQt5 import QtCore, QtWidgets

QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)

import serial                      # noqa: E402  (Serial is patched in main)
import theme                       # noqa: E402
from config import PACKET_SIZE     # noqa: E402

# Must be imported before any QApplication exists, which is why main.py does
# it at module level too — QtWebEngineWidgets refuses to load afterwards.
from core.ground_station import GroundStation   # noqa: E402

DROPOUT_AT_S = 25.0
RECOVER_AT_S = 55.0


class FakeCubeSat:
    """
    Stands in for serial.Serial.

    Emits the same '##,...,$$' telemetry and '@@,...,$$' link frames the
    firmware does, and performs a full chunked image downlink — header line,
    binary packets with a CRC, then CS_TRANSFER_DONE — so the downlink UI is
    exercised for real rather than mocked at the Qt layer.
    """

    def __init__(self, port=None, baudrate=None, timeout=None, **kw):
        self.port = port
        self.is_open = True
        self.dtr = False
        self.rts = False

        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sending_image = False
        self._cancel_image = threading.Event()
        self._t0 = time.monotonic()

        threading.Thread(target=self._run, daemon=True).start()

    # ── the serial.Serial surface GroundStation actually uses ────────────────
    @property
    def in_waiting(self):
        with self._lock:
            return len(self._buf)

    def readline(self):
        with self._lock:
            idx = self._buf.find(b"\n")
            if idx == -1:
                out, self._buf = bytes(self._buf), bytearray()
                return out
            out = bytes(self._buf[:idx + 1])
            del self._buf[:idx + 1]
            return out

    def read(self, n=1):
        with self._lock:
            out = bytes(self._buf[:n])
            del self._buf[:n]
            return out

    def write(self, data):
        text = bytes(data).decode(errors="ignore").strip()
        if text.startswith("ACK") or text.startswith("NACK"):
            return len(data)
        print(f"[cubesat] command received: {text!r}")
        if text == "S" and not self._sending_image:
            self._cancel_image.clear()
            threading.Thread(target=self._send_image, daemon=True).start()
        return len(data)

    def reset_input_buffer(self):
        with self._lock:
            self._buf.clear()

    def close(self):
        self.is_open = False
        self._stop.set()

    # ── emission ─────────────────────────────────────────────────────────────
    def _push(self, data: bytes):
        with self._lock:
            self._buf.extend(data)

    def _elapsed(self):
        return time.monotonic() - self._t0

    def _linked(self):
        return not (DROPOUT_AT_S <= self._elapsed() < RECOVER_AT_S)

    def _run(self):
        last_link = 0.0
        announced = set()
        while not self._stop.is_set():
            t = self._elapsed()

            for mark, msg in ((DROPOUT_AT_S, "signal lost — the panel should dim, then withdraw its readings"),
                              (RECOVER_AT_S, "signal back — values should repopulate")):
                if t >= mark and mark not in announced:
                    announced.add(mark)
                    print(f"[timeline] {int(mark):>3}s  {msg}")

            if self._sending_image or not self._linked():
                time.sleep(0.2)
                continue

            self._push(self._telemetry_frame(t).encode())
            if t - last_link > 3.0:
                last_link = t
                rssi = -68 - int(6 * math.sin(t / 7))
                self._push(f"@@,1,{rssi},9,$$\n".encode())

            time.sleep(1.0)

    def _telemetry_frame(self, t: float) -> str:
        """One '##' frame. Values drift so the graphs have something to draw."""
        wobble = math.sin(t / 9)
        fields = [
            "##",
            "PIXIE-1",
            time.strftime("%d-%m-%Y"),
            time.strftime("%H:%M:%S"),
            f"{134.2 + 8 * wobble:.1f}",                  # azimuth
            f"{41.8 + 4 * wobble:.1f}",                   # elevation
            f"{4.05 - t * 0.002:.2f}",                    # battery V  (drifts down)
            f"{max(0, 92 - t * 0.08):.0f}",               # SOC
            f"{5.6 + 0.9 * wobble:.2f}",                  # solar V
            f"{0.52 + 0.06 * wobble:.2f}",                # current
            f"{2.1 + 0.3 * wobble:.2f}",                  # power
            "11",                                          # sats
            f"{13.017812 + t * 0.00004:.6f}",             # lat
            f"{77.514813 + t * 0.00006:.6f}",             # lon
            "0.9",                                         # hdop
            f"{330 + t * 1.6:.0f}",                       # altitude (climbs)
            f"{1.8 + 0.4 * wobble:.1f}",                  # speed
            f"{31 + t * 0.32:.1f}",                       # temperature (into warn)
            f"{1013 + 2 * wobble:.0f}",                   # pressure
            f"{41 + 3 * wobble:.0f}",                     # humidity
            f"{-2.4 + 6 * wobble:.1f}",                   # pitch
            f"{0.8 + 9 * wobble:.1f}",                    # roll
            f"{(t * 7) % 360:.1f}",                       # yaw
            f"{320000 + 40000 * wobble:.0f}",             # gas resistance
            "$$",
        ]
        return ",".join(fields) + "\n"

    # ── simulated image downlink ─────────────────────────────────────────────
    def _make_jpeg(self) -> bytes:
        from PIL import Image
        img = Image.new("RGB", (320, 240))
        px = img.load()
        for y in range(240):
            for x in range(320):
                px[x, y] = (20 + x // 3, 40 + y // 4, 90 + (x + y) // 6)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=70)
        return buf.getvalue()

    def _send_image(self):
        self._sending_image = True
        try:
            data = self._make_jpeg()
            total = (len(data) // PACKET_SIZE) + 1
            print(f"[cubesat] downlink starting: {len(data)} bytes, {total} packets")

            # The real firmware takes a moment to prepare the frame. This is
            # the gap the progress bar used to show as a hardcoded 0/100.
            time.sleep(3.0)
            self._push(f"CS_FILE_INFO:{len(data)}\n".encode())

            for pid in range(total):
                if self._cancel_image.is_set() or self._stop.is_set():
                    break
                chunk = data[pid * PACKET_SIZE:(pid + 1) * PACKET_SIZE]
                crc = zlib.crc32(chunk) & 0xFFFF_FFFF
                packet = (bytes([pid & 0xFF, (pid >> 8) & 0xFF, len(chunk)])
                          + chunk + struct.pack("<I", crc))
                self._push(packet)
                time.sleep(0.06)

            self._push(b"CS_TRANSFER_DONE\n")
            print("[cubesat] downlink finished")
        finally:
            self._sending_image = False


def main():
    serial.Serial = FakeCubeSat

    # No network calls in a demo run.
    import core.updater
    core.updater.check_for_updates = lambda parent=None: None

    app = QtWidgets.QApplication(sys.argv)
    theme.init(app)
    app.setStyleSheet(theme.qss())

    win = GroundStation(com_port="COM-DEMO", user_email="")
    win.show()

    # Headless capture: pass an output path to save frames at the interesting
    # moments and exit, instead of sitting there.
    if len(sys.argv) > 1:
        out = sys.argv[1]
        for at, tag in ((8, "live"), (38, "stale"), (60, "recovered")):
            QtCore.QTimer.singleShot(
                at * 1000,
                lambda p=out, t=tag: win.grab().save(p.replace(".png", f"_{t}.png")))
        QtCore.QTimer.singleShot(63 * 1000, app.quit)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
