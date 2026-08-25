"""INA219 driver wrapper (DEVELOPMENT_PLAN.md §9).

Reads bus voltage / current / power from a real INA219 through ``pi-ina219``,
or from the simulator when ``hardware.simulate`` is true. Every read returns
``(V, I, P, ok)``; the caller decides how to treat ``ok=False`` samples.
"""
from __future__ import annotations

import math
from typing import Optional

from src.hardware.sim import SimulatedINA219, SimState


class INA219Reader:
    """Small, validated interface to the INA219 current/voltage monitor."""

    def __init__(
        self,
        cfg: dict,
        simulate: Optional[bool] = None,
        sim_state: Optional[SimState] = None,
    ) -> None:
        hw = cfg.get("hardware", {})
        self.simulate = hw.get("simulate", False) if simulate is None else simulate
        self.shunt_ohms = float(hw.get("shunt_ohms", 0.1))
        self.max_amps = float(hw.get("max_expected_amps", 3.0))
        self.bus = int(hw.get("i2c_bus", 1))
        self.address = int(str(hw.get("ina219_address", "0x40")), 0)
        measurement = cfg.get("measurement", {})
        # INA219 reports positive current from VIN+ to VIN-. The current
        # hardware description feeds source VBUS to VIN- and the phone from
        # VIN+, so reverse the sign to obtain positive charging current.
        self.bus_voltage_side = str(
            measurement.get("bus_voltage_side", "load")
        ).lower()
        self._ina = None
        self._last_error: Optional[str] = None
        self.read_count = 0
        self.error_count = 0

        if self.simulate:
            self._sim = SimulatedINA219(sim_state if sim_state is not None else SimState())
        else:
            self._init_real()

    def _init_real(self) -> None:
        try:
            from ina219 import INA219
        except ImportError as exc:  # pragma: no cover - hardware path
            raise RuntimeError(
                "pi-ina219 not installed. Create the venv and run: "
                "pip install -r requirements.txt"
            ) from exc
        self._ina = INA219(self.shunt_ohms, self.max_amps, address=self.address, busnum=self.bus)
        # GAIN_8_320MV => +/-320 mV shunt FSR => +/-3.2 A with a 0.1 ohm shunt.
        # The pi-ina219 API exposes GAIN_8_320MV, not GAIN_1_X.
        self._ina.configure(self._ina.RANGE_32V, self._ina.GAIN_8_320MV)
        self._ina.wake()

    @property
    def ok(self) -> bool:
        return not self.simulate and self._ina is not None

    def read_sample(self, t: float) -> tuple[float, float, float, bool]:
        """Return ``(voltage_V, current_A, power_W, ok)``."""
        self.read_count += 1
        if self.simulate:
            return self._sim.read(t)
        try:
            v_bus = float(self._ina.voltage())
            i = abs(float(self._ina.current())) / 1000.0
            # If the source is connected to VIN- and the phone to VIN+, the
            # INA219 bus reading is source-side. Subtract the shunt drop to
            # expose the phone-side voltage used by metrics/rules.
            v = v_bus
            if self.bus_voltage_side == "source":
                v = v_bus - abs(i) * self.shunt_ohms
            # Use the corrected current direction so power is positive for
            # power delivered to the phone.
            p = v * i
            ok = not (math.isnan(v) or math.isnan(i) or v < 0.0)
            if not ok:
                self.error_count += 1
            return v, i, p, ok
        except OSError as exc:  # I2C hiccup -> flag, keep streaming
            self.error_count += 1
            self._last_error = str(exc)
            return float("nan"), float("nan"), float("nan"), False

    def shutdown(self) -> None:
        if self._ina is not None:  # pragma: no cover - hardware path
            try:
                self._ina.sleep()
            except Exception:
                pass
