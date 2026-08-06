"""Timed acquisition loop (DEVELOPMENT_PLAN.md §9 ``telemetry/sampler.py``).

Reads the INA219 at a fixed rate with monotonic timestamps, validates every
sample, tolerates I2C errors without killing the stream, and hands each
sample to a callback that can stop the run early (e.g. when a charging
session completes).
"""
from __future__ import annotations

import math
import time
from typing import Callable, Optional

from src.telemetry.models import Sample


def validate_sample(voltage: float, current: float, period_s: float) -> tuple[bool, list[str]]:
    """Range-check a reading. Returns (ok, flags)."""
    flags: list[str] = []
    ok = True
    if math.isnan(voltage) or math.isinf(voltage) or math.isnan(current) or math.isinf(current):
        return False, ["invalid_read"]
    if voltage < 0.0 or voltage > 26.0:  # INA219 bus limit (0-26 V)
        flags.append("voltage_out_of_range")
        ok = False
    if current < -1.0 or current > 3.5:  # shunt FSR +/-3.2 A, small margin
        flags.append("current_out_of_range")
        ok = False
    if period_s <= 0:
        flags.append("bad_period")
    return ok, flags


class Sampler:
    """Acquisition loop. In simulate mode no wall-clock sleeping occurs."""

    def __init__(self, reader, rate_hz: float, simulate: bool = False) -> None:
        self.reader = reader
        self.period = 1.0 / float(rate_hz)
        self.simulate = simulate
        self.gap_count = 0

    def run(
        self,
        duration_s: float,
        on_sample: Optional[Callable[[Sample], bool]] = None,
        tick: Optional[Callable[[float], None]] = None,
        state: str = "UNKNOWN",
        t0: Optional[float] = None,
    ) -> list[Sample]:
        """Collect samples for ``duration_s``.

        * ``on_sample`` - called with each sample; return False to stop early.
        * ``tick`` - optional pre-read hook (sim mode: phone demand driver).
        * ``state`` - state string stamped on samples (e.g. "PROBE").
        """
        if t0 is None:
            t0 = time.monotonic()
        samples: list[Sample] = []
        last_good_t: Optional[float] = None
        n_max = max(1, int(duration_s / self.period)) if self.simulate else None

        while True:
            if self.simulate:
                if n_max is not None and len(samples) >= n_max:
                    break
                t_abs = t0 + len(samples) * self.period
            else:
                t_abs = time.monotonic()
                if t_abs - t0 >= duration_s:
                    break

            if tick is not None:
                tick(t_abs - t0)

            v, i, p, read_ok = self.reader.read_sample(t_abs)
            ok, flags = validate_sample(v, i, self.period)
            sample = Sample(
                t=t_abs - t0,
                voltage=v,
                current=i,
                power=p,
                state=state,
                valid=read_ok and ok,
                flags=flags,
            )
            if sample.valid:
                if last_good_t is not None and (sample.t - last_good_t) > 3.0 * self.period:
                    self.gap_count += 1
                    sample.flags.append("gap")
                last_good_t = sample.t
            samples.append(sample)

            cont = True
            if on_sample is not None:
                cont = bool(on_sample(sample))
            if not cont:
                break

            if not self.simulate:
                # maintain the nominal rate on real hardware
                while time.monotonic() < t0 + len(samples) * self.period:
                    time.sleep(0.001)
        return samples
