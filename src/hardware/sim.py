"""Synthetic hardware for hardware.simulate = true (tests, demos, CI).

The simulator models the measurement physics of DEVELOPMENT_PLAN.md §3:

    V_load = V_target - I * (R_cable + R_fixture) + noise

and lets you script cable defects (heating, intermittent contact, open
circuit, short, PD-blocked CC) so the whole pipeline can be exercised
without touching a live 9 V rail.
"""
from __future__ import annotations

import random


class SimState:
    """Shared mutable state mutated by the simulated CH224K / load / phone."""

    def __init__(
        self,
        r_cable_ohm: float = 0.15,
        r_fixture_ohm: float = 0.05,
        v_noise: float = 0.004,
        i_noise: float = 0.008,
        heating_k: float = 0.0,
        pd_blocked_above: float | None = None,
        short: bool = False,
        open_circuit: bool = False,
        intermittent: bool = False,
        powered: bool = True,
        v_target: float = 5.0,
        current: float = 0.0,
    ) -> None:
        self.r_cable = r_cable_ohm
        self.r_fixture = r_fixture_ohm
        self.v_noise = v_noise
        self.i_noise = i_noise
        self.heating_k = heating_k
        self.pd_blocked_above = pd_blocked_above  # negotiations above this fail
        self.short = short
        self.open_circuit = open_circuit
        self.intermittent = intermittent
        self.powered = powered
        self.v_target = v_target
        self.current = current
        self.pwr_ok = True
        self.phone_on = False
        self.load_on = False


class SimulatedINA219:
    """Drop-in stand-in for the INA219 used by :class:`INA219Reader`."""

    def __init__(self, state: SimState, seed: int = 1) -> None:
        self.state = state
        self._rng = random.Random(seed)
        self._last_t: float | None = None

    def read(self, t: float) -> tuple[float, float, float, bool]:
        """Return (V, I, P, ok) as the real INA219 would."""
        st = self.state
        if self._last_t is not None and t > self._last_t:
            dt = t - self._last_t
            if st.heating_k > 0 and st.current > 0:
                # I^2*R self-heating: resistance rises while current flows
                st.r_cable = min(st.r_cable + st.heating_k * st.current**2 * dt, 2.0)
        self._last_t = t

        if not st.powered:
            return 0.0, 0.0, 0.0, True
        if st.short:
            return 0.15, 3.0, 0.45, True
        if st.open_circuit:
            return st.v_target, 0.0, 0.0, True

        i = st.current
        if st.intermittent and i > 0 and self._rng.random() < 0.02:
            i = 0.0  # brief open bursts -> interruption_frac / spikes
        i += self._rng.gauss(0.0, st.i_noise)
        v = st.v_target - i * (st.r_cable + st.r_fixture) + self._rng.gauss(0.0, st.v_noise)
        v = max(0.0, v)
        i = max(0.0, i)
        return v, i, v * i, True

    @property
    def ok(self) -> bool:
        return True


class SimulatedCH224K:
    """Simulates USB-PD negotiation through the test cable's CC wires."""

    def __init__(self, state: SimState) -> None:
        self.state = state
        self.voltage = state.v_target

    def set_voltage(self, target_v: float) -> bool:
        st = self.state
        if st.pd_blocked_above is not None and target_v > st.pd_blocked_above:
            st.pwr_ok = False  # negotiation fails -> charger never raises VBUS
            return False
        st.v_target = float(target_v)
        st.pwr_ok = True
        self.voltage = st.v_target
        return True

    def read_pwr_ok(self) -> bool:
        return self.state.pwr_ok

    def enable(self, on: bool) -> None:
        self.state.powered = on


class SimulatedLoadController:
    """Simulates the controlled load + phone isolation switch."""

    def __init__(self, state: SimState) -> None:
        self.state = state

    def set_current(self, amps: float, enable: bool = True) -> None:
        self.state.load_on = enable
        if not enable:
            self.state.current = 0.0
        elif self.state.load_on:
            self.state.current = float(amps)

    def phone_switch(self, on: bool) -> None:
        self.state.phone_on = bool(on)

    def close(self) -> None:
        pass


def phone_charge_curve(
    t: float,
    plug_s: float = 3.0,
    ramp_s: float = 6.0,
    current_a: float = 1.2,
    taper_s: float = 60.0,
    noise_a: float = 0.02,
    seed: int = 7,
) -> float:
    """Simulated phone charging current vs elapsed time (A).

    I=0 before plug -> phone not charging; ramps to constant current while
    the battery charges; tapers to 0 once full -> "phone has been charged".
    """
    rng = random.Random(seed)
    if t < plug_s:
        return 0.0
    t2 = t - plug_s
    if t2 < ramp_s:
        return current_a * t2 / ramp_s
    if t2 < taper_s:
        return current_a + rng.gauss(0.0, noise_a)
    frac = min(1.0, (t2 - taper_s) / max(taper_s * 0.25, 1e-6))
    return max(0.0, current_a * (1.0 - frac))
