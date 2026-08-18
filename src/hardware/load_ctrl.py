"""Controlled load + phone isolation switch (WIRING_SCHEMATICS.md §5).

* Q1 (P-MOSFET) isolates the phone during probe mode — phone only ever sees
  5 V in charge mode.
* Q2 (N-MOSFET) switches the controlled load used to draw precise current
  steps during probe mode.
* The e-load setpoint is a PWM on GPIO18 filtered into a 0-0.30 V reference
  for the LM358 constant-current sink: I_load = V_set / R_sense.
"""
from __future__ import annotations

from typing import Optional

from src.hardware.gpio_map import PinMap
from src.hardware.sim import SimulatedLoadController, SimState


class LoadController:
    """Phone switch (Q1), load switch (Q2) and e-load current setpoint (PWM)."""

    def __init__(
        self,
        pins: PinMap,
        cfg: dict,
        simulate: Optional[bool] = None,
        sim_state: Optional[SimState] = None,
    ) -> None:
        self.pins = pins
        self.simulate = cfg.get("hardware", {}).get("simulate", False) if simulate is None else simulate
        ld = cfg.get("load", {})
        self.max_probe_amps = float(ld.get("max_probe_amps", 2.5))
        self.pwm_freq = int(ld.get("pwm_freq_hz", 1000))
        self.r_sense = float(ld.get("r_sense_ohm", 0.1))
        r1 = float(ld.get("divider_r1_ohm", 100000.0))
        r2 = float(ld.get("divider_r2_ohm", 10000.0))
        self._setpoint_vmax = 3.3 * r2 / (r1 + r2)  # ~0.30 V -> ~3 A
        # In manual (Y-junction) and voltage (CH224K removed) wiring there is
        # no controlled-load or phone-isolation GPIO; the phone is the load.
        self.manual = (
            str(cfg.get("ch224k", {}).get("control_mode", "gpio")).lower()
            in ("manual", "voltage")
            and not self.simulate
        )
        self._gpio = None
        self._pwm = None
        self.phone_on = False
        self.load_on = False
        self.current = 0.0

        if self.simulate:
            self._sim = SimulatedLoadController(sim_state if sim_state is not None else SimState())
        elif not self.manual:
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
        GPIO.setup(self.pins.q1_phone_switch, GPIO.OUT, initial=GPIO.HIGH)  # P-MOS: HIGH = OFF
        GPIO.setup(self.pins.q2_load_switch, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.pins.load_pwm, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.pins.status_led, GPIO.OUT, initial=GPIO.LOW)
        self._pwm = GPIO.PWM(self.pins.load_pwm, self.pwm_freq)
        self._pwm.start(0.0)

    # ------------------------------------------------------------------ API
    def duty_for_current(self, amps: float) -> float:
        """PWM duty (%) that makes the e-load draw ``amps`` from the rail."""
        amps = min(max(amps, 0.0), self.max_probe_amps)
        v_set = amps * self.r_sense
        return max(0.0, min(100.0, v_set / self._setpoint_vmax * 100.0))

    def set_current(self, amps: float, enable: bool = True) -> None:
        """Command the e-load to draw ``amps`` (clamped to max_probe_amps)."""
        self.load_on = bool(enable)
        self.current = min(max(amps, 0.0), self.max_probe_amps) if enable else 0.0
        if self.simulate:
            self._sim.set_current(self.current, enable)
        elif self.manual:
            return  # No controlled-load GPIO is connected in manual-voltage mode.
        else:  # pragma: no cover - Pi
            if self._pwm is not None:
                self._pwm.ChangeDutyCycle(self.duty_for_current(self.current) if enable else 0.0)
            self._gpio.output(self.pins.q2_load_switch, self._gpio.HIGH if enable else self._gpio.LOW)

    def phone_switch(self, on: bool) -> None:
        """Connect/disconnect the phone path (Q1). P-MOSFET: LOW = ON."""
        self.phone_on = bool(on)
        if self.simulate:
            self._sim.phone_switch(on)
        elif self.manual:
            return  # The phone is directly attached to the Y-junction.
        else:  # pragma: no cover - Pi
            self._gpio.output(self.pins.q1_phone_switch, self._gpio.LOW if on else self._gpio.HIGH)

    def set_led(self, on: bool) -> None:
        if self._gpio is not None and not self.simulate:  # pragma: no cover - Pi
            self._gpio.output(self.pins.status_led, self._gpio.HIGH if on else self._gpio.LOW)

    def close(self) -> None:
        if self._pwm is not None and not self.simulate:  # pragma: no cover - Pi
            try:
                self._pwm.stop()
            except Exception:
                pass
        if self._gpio is not None and not self.simulate:  # pragma: no cover - Pi
            try:
                self._gpio.cleanup()
            except Exception:
                pass
