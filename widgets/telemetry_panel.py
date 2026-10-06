"""
widgets/telemetry_panel.py
──────────────────────────
TelemetryPanel – left-side panel that displays all incoming sensor values,
flag monitor, and colour-coded health indicators.
"""
import math
from datetime import datetime
from PyQt5 import QtWidgets, QtCore

import theme
from config import DEGREE


class TelemetryPanel(QtWidgets.QFrame):
    """Scrollable left panel showing live CubeSat telemetry."""

    # Emitted when the user clicks the settings (⚙) button
    settings_clicked = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("TelemetryPanel")
        layout = QtWidgets.QVBoxLayout(self)

        # ── Heading row (title + settings icon) ──────────────────────────────
        heading_row = QtWidgets.QHBoxLayout()
        heading_row.setContentsMargins(0, 0, 0, 0)

        title = QtWidgets.QLabel("📡 TELEMETRY STATUS")
        title.setObjectName("mainTitle")
        title.setAlignment(QtCore.Qt.AlignCenter)

        settings_btn = QtWidgets.QPushButton("⚙")
        # Set before the stylesheet: an ID rule applied to a widget that does
        # not carry the id yet is not guaranteed to be re-evaluated.
        settings_btn.setObjectName("settingsBtn")
        settings_btn.setFixedSize(34, 34)
        settings_btn.setToolTip("User Profile & Settings")
        settings_btn.setCursor(QtCore.Qt.PointingHandCursor)
        settings_btn.setStyleSheet(f"""
            QPushButton#settingsBtn {{
                background: {theme.RAISED};
                color: {theme.TEAL};
                border: 1px solid {theme.LINE};
                border-radius: 17px;
                font-size: 16px;
                padding: 0px;
                min-width: 34px;
                text-align: center;
            }}
            QPushButton#settingsBtn:hover {{
                background: {theme.TEAL};
                color: {theme.DEEP};
                border-color: {theme.TEAL};
            }}
        """)
        settings_btn.clicked.connect(self.settings_clicked.emit)

        # How long ago the newest packet arrived. Without this every value
        # keeps its last reading indefinitely and a frozen panel is
        # indistinguishable from a live one.
        self.age_pill = QtWidgets.QLabel("NO DATA")
        self.age_pill.setObjectName("agePill")
        self.age_pill.setAlignment(QtCore.Qt.AlignCenter)
        self.age_pill.setToolTip("Time since the last telemetry packet.")

        # A 34px spacer opposite the settings button, so the title is centred
        # in the panel rather than in the space left beside it.
        spacer = QtWidgets.QWidget()
        spacer.setFixedWidth(34)

        heading_row.addWidget(spacer)
        heading_row.addWidget(title, 1)
        heading_row.addWidget(settings_btn)
        layout.addLayout(heading_row)

        age_row = QtWidgets.QHBoxLayout()
        age_row.setContentsMargins(0, 0, 0, 0)
        age_row.addStretch()
        age_row.addWidget(self.age_pill)
        age_row.addStretch()
        layout.addLayout(age_row)

        self.stale_banner = QtWidgets.QLabel()
        self.stale_banner.setObjectName("staleBanner")
        self.stale_banner.setWordWrap(True)
        self.stale_banner.hide()
        layout.addWidget(self.stale_banner)

        # Every value label, so staleness can sweep all of them at once.
        self._rows: list[dict] = []

        # ── Helper builders ───────────────────────────────────────────────────
        def group(name: str) -> QtWidgets.QGroupBox:
            g = QtWidgets.QGroupBox(name)
            g.setObjectName("groupBox")
            g.setLayout(QtWidgets.QGridLayout())
            return g

        def add_row(grid, row: int, name: str, unit: str = "", desc: str = ""):
            """
            One telemetry row: stripe | name | glyph | value | unit.

            The unit is its own column so the digits line up down the panel.
            Right-aligning "3.92 V" and "1013 hPa" together aligns the unit
            and lets the decimal points wander, which is what the old single
            label did.
            """
            stripe = QtWidgets.QFrame()
            stripe.setObjectName("rowStripe")

            lbl = QtWidgets.QLabel(name)
            lbl.setObjectName("nameLabel")

            glyph = QtWidgets.QLabel("")
            glyph.setObjectName("rowGlyph")
            glyph.setFixedWidth(12)
            glyph.setAlignment(QtCore.Qt.AlignCenter)

            val = QtWidgets.QLabel("—")
            val.setObjectName("valueLabel")
            val.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            val.unit = unit

            unit_lbl = QtWidgets.QLabel(unit)
            unit_lbl.setObjectName("unitLabel")
            unit_lbl.setFixedWidth(36)
            unit_lbl.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

            if desc:
                for w in (lbl, val, unit_lbl):
                    w.setToolTip(desc)

            grid.addWidget(stripe,   row, 0)
            grid.addWidget(lbl,      row, 1)
            grid.addWidget(glyph,    row, 2)
            grid.addWidget(val,      row, 3)
            grid.addWidget(unit_lbl, row, 4)
            grid.setColumnStretch(1, 1)

            self._rows.append({"stripe": stripe, "glyph": glyph,
                               "value": val, "unit": unit_lbl})
            val._row = self._rows[-1]
            return val

        # ── PACKET METADATA ───────────────────────────────────────────────────
        meta = group("🛰️ PACKET METADATA")
        self.cubesat_id    = add_row(meta.layout(), 0, "CubeSat ID",   "", "Unique identifier of the CubeSat mission.")
        self.date_label    = add_row(meta.layout(), 1, "Date (IST)",   "", "Telemetry packet date in Indian Standard Time.")
        self.time_label    = add_row(meta.layout(), 2, "Time (IST)",   "", "Time when the telemetry packet was generated.")
        self.azimuth_label = add_row(meta.layout(), 3, "Azimuth",      DEGREE, "The azimuth angle of the CubeSat.")
        self.elevation_label=add_row(meta.layout(), 4, "Elevation",    DEGREE, "The elevation angle of the CubeSat.")
        layout.addWidget(meta)

        # ── POWER SYSTEM ──────────────────────────────────────────────────────
        power = group("⚡ POWER SYSTEM")
        self.batt_v    = add_row(power.layout(), 0, "Battery Voltage", "V",  "Voltage level of the onboard battery.")
        self.batt_soc  = add_row(power.layout(), 1, "Battery SOC",     "%",  "State of Charge of the battery.")
        self.solar_v   = add_row(power.layout(), 2, "Solar Voltage",   "V",  "Voltage generated by solar panels.")
        self.current   = add_row(power.layout(), 3, "Current",         "A",  "Electrical current drawn from the system.")
        self.power_val = add_row(power.layout(), 4, "Power",           "W",  "Total power generated or consumed.")
        layout.addWidget(power)

        # ── GPS STATUS ────────────────────────────────────────────────────────
        gps = group("🌍 GPS STATUS")
        self.sats = add_row(gps.layout(), 0, "Satellites Connected", "", "Number of GPS satellites currently connected.")
        self.lat  = add_row(gps.layout(), 1, "Latitude",  DEGREE, "Geographical latitude position.")
        self.lon  = add_row(gps.layout(), 2, "Longitude", DEGREE, "Geographical longitude position.")
        self.hdop = add_row(gps.layout(), 3, "HDOP", "", "Horizontal Dilution of Precision – GPS accuracy indicator.")
        layout.addWidget(gps)

        # ── VITALS ────────────────────────────────────────────────────────────
        vitals = group("💓 VITALS")
        self.alt   = add_row(vitals.layout(), 0, "Altitude",    "m",   "Height above sea level.")
        self.speed = add_row(vitals.layout(), 1, "Speed",       "m/s", "Current velocity of the CubeSat.")
        self.temp  = add_row(vitals.layout(), 2, "Temperature", "°C",  "Measured onboard temperature.")
        self.press = add_row(vitals.layout(), 3, "Pressure",    "hPa", "Atmospheric pressure measurement.")
        self.hum   = add_row(vitals.layout(), 4, "Humidity",    "%",   "Relative humidity level.")
        layout.addWidget(vitals)

        # ── AIR QUALITY ───────────────────────────────────────────────────────
        air = group("☁️ AIR QUALITY")
        self.iaq  = add_row(air.layout(), 0, "AQI Index",  "",    "Indoor Air Quality index.")
        self.eco2 = add_row(air.layout(), 1, "eCO2",       "ppm", "Estimated equivalent CO2 concentration.")
        self.voc  = add_row(air.layout(), 2, "VOC Index",  "",    "Volatile Organic Compound level.")
        layout.addWidget(air)

        # ── FLAG MONITOR ──────────────────────────────────────────────────────
        flag_group = QtWidgets.QGroupBox("🚩 FLAG MONITOR")
        flag_group.setObjectName("groupBox")
        flag_group.setToolTip(
            "Mission flag log window.\n"
            "Displays CubeSat and Ground Station event messages."
        )
        flag_group.setToolTipDuration(2000)

        flag_layout = QtWidgets.QVBoxLayout()
        flag_group.setLayout(flag_layout)

        self.flag_monitor = QtWidgets.QTextEdit()
        self.flag_monitor.setReadOnly(True)
        self.flag_monitor.setToolTip(
            "Displays mission flags and system alerts.\n"
            "Shows CubeSat events, warnings, and commands.\n"
            "Used for monitoring mission status in real time."
        )
        self.flag_monitor.setToolTipDuration(2000)
        flag_layout.addWidget(self.flag_monitor)
        layout.addWidget(flag_group, 1)

    # ── public helpers ────────────────────────────────────────────────────────
    def update_flag(self, line: str):
        if line.startswith("CS_") or line.startswith("GCS_"):
            t = datetime.now().strftime("%H:%M:%S")
            self.flag_monitor.append(f"[{t}] {line}")

            # Bubble up to GroundStation to also write to Excel
            parent = self.parent()
            while parent and not hasattr(parent, "_append_flag_row"):
                parent = parent.parent()
            if parent:
                parent._append_flag_row(t, line)

    @staticmethod
    def calculate_air_quality(gas_res: float) -> tuple[int, int, int]:
        gas_res = max(5_000, min(gas_res, 500_000))
        iaq  = max(0, min(500, int(500 - (gas_res / 500_000) * 400)))
        eco2 = int(400 + (500_000 - gas_res) / 500_000 * 1_600)
        voc  = max(0, min(500, int((500_000 - gas_res) / 1_000)))
        return iaq, eco2, voc

    def update_telemetry(
        self,
        cubesat_id, date, time, azimuth, elevation,
        batt_v, batt_soc, solar_v, current, power_val,
        sats, lat, lon, hdop,
        alt, speed, temp, press, hum,
        gas_res,
    ):
        # A packet has arrived, so drop any stale treatment before the new
        # readings are written; set_data_age() keeps it that way.
        self._restore_live()

        # Metadata
        self.cubesat_id.setText(cubesat_id)
        self.date_label.setText(date)
        self.time_label.setText(time)
        self.azimuth_label.setText(str(azimuth))
        self.elevation_label.setText(str(elevation))

        # Power
        self.batt_v.setText(str(batt_v))
        self._color(self.batt_v,   float(batt_v),   3.7, 4.2, 3.5, 3.7)
        self.batt_soc.setText(str(batt_soc))
        self._color(self.batt_soc, float(batt_soc), 40, 100, 20, 40)
        self.solar_v.setText(str(solar_v))
        self._color(self.solar_v,  float(solar_v),  5, 8, 3, 5)
        self.current.setText(str(current))
        self._color(self.current,  float(current),  0.2, 1.2, 0.1, 0.2)
        self.power_val.setText(str(power_val))
        self._color(self.power_val,float(power_val),1, 8, 0.5, 1)

        # GPS
        self.sats.setText(str(sats))
        self.hdop.setText(str(hdop))
        self._color(self.hdop, float(hdop), 0.5, 1.5, 1.5, 3)
        self.lat.setText(str(lat))
        self.lon.setText(str(lon))

        # Vitals
        self.alt.setText(str(alt))
        self.speed.setText(str(speed))
        self.temp.setText(str(temp))
        self._color(self.temp,  float(temp),  0, 45, 45, 60)
        self.press.setText(str(press))
        self._color(self.press, float(press), 980, 1030, 950, 980)
        self.hum.setText(str(hum))
        self._color(self.hum,   float(hum),   30, 70, 70, 85)

        # Air quality (derived from raw gas resistance)
        iaq, eco2, voc = self.calculate_air_quality(float(gas_res))
        self.iaq.setText(str(iaq))
        self._color(self.iaq,  iaq,  0, 100, 100, 200)
        self.eco2.setText(str(eco2))
        self._color(self.eco2, eco2, 400, 800, 800, 1500)
        self.voc.setText(str(voc))
        self._color(self.voc,  voc,  0, 150, 150, 300)

    # ── staleness ─────────────────────────────────────────────────────────────
    # Seconds since the last packet before readings are dimmed, and before
    # they are withdrawn altogether.
    DIM_AFTER_S   = 5
    BLANK_AFTER_S = 30

    def set_data_age(self, seconds: float | None):
        """
        Say how old the readings on screen are.

        Called once a second from GroundStation. `None` means nothing has
        arrived at all this session.

        This is the whole point of the panel being trustworthy: when packets
        stop, every value used to keep its last reading in its last colour
        indefinitely, so a figure from four minutes ago looked exactly like
        one from two seconds ago.
        """
        if seconds is None:
            self._set_age_state("", "NO DATA", "")
            self._apply_stale(True)
            return

        mins, secs = divmod(int(seconds), 60)
        clock = f"{mins}:{secs:02d}"

        if seconds >= self.BLANK_AFTER_S:
            self._set_age_state(
                "lost", f"NO SIGNAL {clock}",
                f"No packet for {clock}. The last readings have been withdrawn.")
            self._apply_stale(True)
        elif seconds >= self.DIM_AFTER_S:
            self._set_age_state(
                "stale", f"STALE {clock}",
                f"Frozen at the reading from {clock} ago. Do not act on these values.")
            self._apply_stale(False)
        else:
            self._set_age_state("live", f"LIVE  T+{clock}", "")
            self._restore_live()

    def _set_age_state(self, state: str, pill_text: str, banner: str):
        self.age_pill.setText(pill_text)
        self.age_pill.setProperty("state", state)
        theme.repolish(self.age_pill)
        if banner:
            self.stale_banner.setText(banner)
            self.stale_banner.setProperty("state", state)
            theme.repolish(self.stale_banner)
            self.stale_banner.show()
        else:
            self.stale_banner.hide()

    def _apply_stale(self, blank: bool):
        """Dim every reading, and withdraw it entirely once past BLANK_AFTER_S."""
        for row in self._rows:
            if blank:
                if not row.get("held"):
                    row["held"] = row["value"].text()
                row["value"].setText("—")
            row["glyph"].setText("")
            for key in ("value", "unit", "stripe", "glyph"):
                row[key].setProperty("state", "stale")
            theme.repolish(row["value"], row["unit"], row["stripe"], row["glyph"])
        self._stale = True

    def _restore_live(self):
        """Undo the stale treatment; the next update_telemetry repaints states."""
        if not getattr(self, "_stale", False):
            return
        for row in self._rows:
            row.pop("held", None)
            for key in ("value", "unit", "stripe", "glyph"):
                row[key].setProperty("state", "")
            theme.repolish(row["value"], row["unit"], row["stripe"], row["glyph"])
        self._stale = False

    # ── internal ──────────────────────────────────────────────────────────────
    @staticmethod
    def _color(label, value: float, safe_min, safe_max, warn_min, warn_max):
        """
        Mark a reading safe / warning / alarm.

        Sets a property the global sheet styles, rather than writing a
        stylesheet onto the widget. A widget stylesheet *replaces* the
        #valueLabel rule instead of merging with it, so the old version
        silently dropped font-size and every thresholded value shrank to the
        default a second after launch, leaving two sizes in one column.

        The state also drives a stripe down the left of the row and a
        direction glyph, so health is never carried by colour alone.
        """
        try:
            value = float(value)
        except (ValueError, TypeError):
            return
        if safe_min <= value <= safe_max:
            state, glyph = "ok", ""
        elif warn_min <= value <= warn_max:
            state, glyph = "warn", "▲" if value > safe_max else "▼"
        else:
            state, glyph = "alarm", "▲" if value > safe_max else "▼"

        row = getattr(label, "_row", None)
        if row is None:
            return
        if label.property("state") == state and row["glyph"].text() == glyph:
            return

        row["glyph"].setText(glyph)
        for key in ("value", "stripe", "glyph"):
            row[key].setProperty("state", state)
        row["unit"].setProperty("state", "")
        theme.repolish(row["value"], row["stripe"], row["glyph"], row["unit"])
