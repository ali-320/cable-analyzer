"""Unit tests for src/telemetry/sampler.py (simulate mode only)."""
import unittest

from src.hardware.sim import SimState, SimulatedINA219
from src.telemetry.sampler import Sampler, validate_sample


class FakeReader:
    """Minimal reader stand-in for sampler tests."""

    def __init__(self, sim: SimulatedINA219):
        self._sim = sim
        self.t0 = 0.0

    def read_sample(self, t):
        return self._sim.read(t)


class TestSampler(unittest.TestCase):
    def test_sim_mode_returns_expected_count(self):
        reader = FakeReader(SimulatedINA219(SimState(r_cable_ohm=0.2, current=1.0)))
        sampler = Sampler(reader, rate_hz=25.0, simulate=True)
        samples = sampler.run(duration_s=1.0)  # 25 samples, no wall-clock sleep
        self.assertEqual(len(samples), 25)
        self.assertTrue(all(s.valid for s in samples))
        self.assertAlmostEqual(samples[-1].t, 0.96, places=6)

    def test_on_sample_can_stop_early(self):
        reader = FakeReader(SimulatedINA219(SimState(r_cable_ohm=0.2, current=1.0)))
        sampler = Sampler(reader, rate_hz=25.0, simulate=True)

        def stop(s):
            return False  # stop after the first sample

        samples = sampler.run(duration_s=5.0, on_sample=stop)
        self.assertEqual(len(samples), 1)

    def test_validation(self):
        ok, flags = validate_sample(5.0, 1.0, 0.04)
        self.assertTrue(ok)
        ok, flags = validate_sample(float("nan"), 1.0, 0.04)
        self.assertFalse(ok)
        ok, flags = validate_sample(28.0, 1.0, 0.04)  # > INA219 26 V limit
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
