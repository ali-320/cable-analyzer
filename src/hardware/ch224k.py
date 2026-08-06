"""CH224K USB-PD sink trigger controller (WIRING_SCHEMATICS.md §3).

The CH224K negotiates with the charger over the CC wires of the test cable
and requests a fixed PDO voltage (5/9/12/15/20 V) selected by the SEL
strap pins. Driving the straps from Pi GPIO lets the software step the
negotiated voltage mid-test — this is what makes multi-voltage probing
possible.
"""
from __future__ import annotations

import time
from typing import Optional

from src.hardware.gpio_map import PinMap
from src.hardware.sim import SimulatedCH224K, SimState


class CH224KController:
    """Controls CH224K voltage selection + status. Safe default = 5 V."""

    def __init__(
        self,
        pins: PinMap,
        cfg: dict,
        simulate: Optional[bool] = None,
        sim_state: Optional[SimState] = None,
    ) -> None:
        self.pins = pins
        self.simulate = cfg.get("hardware", {}).get("simulate", False) if simulate is None else simulate
        ch = cfg.get("ch224k", {})
        self.renegotiate_wait = float(ch.get("renegotiate_wait_s", 1.5))
        self.verify_tol = float(ch.get("verify_tolerance", 0.05))
        self._truth = {int(float(k)): int(v) for k, v in ch.get("sel_truth", {}).items()}
        self._pwr_ok_active_high = str(ch.get("pwr_ok_polarity", "high")).lower() != "low"
        self._gpio = None
        self.voltage = None

        if self.simulate:
            self._sim = SimulatedCH224K(sim_state if sim_state is not None else SimState())
        else:
            self._init_gpio()

    def _init_gpio(self) -> None:  # pragma: no cover - requires Pi
        try:
            import RPi.GPIO as GPIO
        except ImportError as exc:
            raise RuntimeError(
                "RPi.GPIO not available. On the Pi: sudo apt install python3-rpi.gpio "
                "(or python3-lgpio on newer images) — see dependencies.md"
            ) from exc
        self._gpio = GPIO
        GPIO.setmode(GPIO.BCM)
        for pin in self.pins.sel_pins:
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)  # 000 -> 5 V safe default
        GPIO.setup(self.pins.en, GPIO.OUT, initial=GPIO.HIGH)  # enable output
        pull = GPIO.PUD_DOWN if self._pwr_ok_active_high else GPIO.PUD_UP
        GPIO.setup(self.pins.pwr_ok, GPIO.IN, pull_up_down=pull)

    # ------------------------------------------------------------------ API
    def set_voltage(self, target_v: float) -> bool:
        """Request a PDO voltage; returns True if negotiation is expected.

        The actual success is confirmed by the caller reading ``read_pwr_ok()``
        and verifying the measured bus voltage (probe logic in main.py).
        """
        code = self._truth.get(round(float(target_v)))
        if code is None:
            raise ValueError(f"CH224K cannot request {target_v} V; supported: {sorted(self._truth)}")
        if self.simulate:
            ok = self._sim.set_voltage(target_v)
            self.voltage = self._sim.voltage
            time.sleep(0.01)  # keep timing behavior comparable
            return ok
        # real hardware: write strap bits (SEL0 = LSB)
        self._gpio.output(self.pins.sel0, self._gpio.HIGH if code & 1 else self._gpio.LOW)
        self._gpio.output(self.pins.sel1, self._gpio.HIGH if code & 2 else self._gpio.LOW)
        self._gpio.output(self.pins.sel2, self._gpio.HIGH if code & 4 else self._gpio.LOW)
        time.sleep(self.renegotiate_wait)  # VBUS dips ~0.3-1 s during re-negotiation
        self.voltage = float(target_v)
        return True

    def read_pwr_ok(self) -> bool:
        if self.simulate:
            return self._sim.read_pwr_ok()
        value = self._gpio.input(self.pins.pwr_ok)  # pragma: no cover - Pi
        return bool(value) if self._pwr_ok_active_high else not bool(value)

    def enable(self, on: bool) -> None:
        if self.simulate:
            self._sim.enable(on)
        else:  # pragma: no cover - Pi
            self._gpio.output(self.pins.en, self._gpio.HIGH if on else self._gpio.LOW)

    def close(self) -> None:
        if self._gpio is not None and not self.simulate:  # pragma: no cover - Pi
            try:
                self._gpio.cleanup()
            except Exception:
                pass
