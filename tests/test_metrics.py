"""Unit tests for src/features/metrics.py (DEVELOPMENT_PLAN.md §5)."""
import unittest

from tests.helpers import make_samples

from src.features.metrics import compute_features, linreg, percentile, stdev
from src.telemetry.models import Sample


class TestBasicStats(unittest.TestCase):
    def test_stdev_and_percentile(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertAlmostEqual(percentile(xs, 50), 3.0, places=6)
        self.assertAlmostEqual(percentile(xs, 0), 1.0, places=6)
        self.assertAlmostEqual(percentile(xs, 100), 5.0, places=6)
        self.assertAlmostEqual(stdev(xs), 1.5811388, places=5)

    def test_linreg_slope(self):
        xs = [float(i) for i in range(10)]
        ys = [2.0 * x + 1.0 for x in xs]
        slope, intercept, r2 = linreg(xs, ys)
        self.assertAlmostEqual(slope, 2.0, places=6)
        self.assertAlmostEqual(intercept, 1.0, places=6)
        self.assertAlmostEqual(r2, 1.0, places=6)


class TestFeatures(unittest.TestCase):
    def test_recovers_known_resistance(self):
        # V = 5 - I*0.2  ->  R_loop should come back as ~0.2 ohm
        samples = make_samples(r_cable=0.2, current=1.0, noise=0.0)
        f = compute_features(samples, v_target=5.0, r_fixture=0.0)
        self.assertIsNotNone(f)
        self.assertAlmostEqual(f["r_mean"], 0.2, places=3)
        self.assertAlmostEqual(f["r_loop_mean"], 0.2, places=3)
        self.assertAlmostEqual(f["eta"], 4.8 / 5.0, places=3)
        self.assertAlmostEqual(f["V_min"], 4.8, places=3)

    def test_fixture_subtraction(self):
        samples = make_samples(r_cable=0.2, current=1.0, noise=0.0)
        f = compute_features(samples, v_target=5.0, r_fixture=0.05)
        self.assertAlmostEqual(f["r_mean"], 0.15, places=3)  # R_cable = 0.2 - 0.05

    def test_dvdi_slope_equals_minus_r(self):
        # V vs I load line: both current levels must be in the steady band
        # (>= 50% of the peak busy current) to be included
        from tests.helpers import make_samples as mk

        all_samples = mk(r_cable=0.25, current=1.0, n=20) + mk(r_cable=0.25, current=1.5, n=20)
        f = compute_features(all_samples, v_target=5.0)
        self.assertAlmostEqual(f["dV_dI_slope"], -0.25, places=3)
        self.assertAlmostEqual(f["r_dvdi"], 0.25, places=3)

    def test_heating_trend_detected(self):
        # R grows 0.002 ohm/s over 10 s -> ~120 mOhm/min
        samples = make_samples(r_cable=0.1, current=1.0, n=250, period=0.04, r_growth_per_s=0.002)
        f = compute_features(samples, v_target=5.0)
        self.assertAlmostEqual(f["dR_dt_mOhm_per_min"], 120.0, delta=1.0)

    def test_insufficient_data_returns_none(self):
        samples = make_samples(current=0.01)  # below i_min -> no busy samples
        f = compute_features(samples, v_target=5.0, min_busy_samples=5)
        self.assertIsNone(f)

    def test_probe_samples_are_included_after_idle_removal(self):
        samples = make_samples(state="PROBE", n=20)
        features = compute_features(samples, v_target=5.0)
        self.assertIsNotNone(features)
        self.assertEqual(features["n_busy"], 20)

    def test_verification_samples_are_excluded_from_features(self):
        verification = [
            Sample(t=k * 0.04, voltage=4.0, current=1.0, power=4.0, state="VERIFICATION")
            for k in range(20)
        ]
        measurement = [
            Sample(t=1.0 + k * 0.04, voltage=4.8, current=1.0, power=4.8, state="CHARGING")
            for k in range(20)
        ]
        f = compute_features(verification + measurement, v_target=5.0)
        self.assertIsNotNone(f)
        self.assertEqual(f["n_total"], len(measurement))
        self.assertEqual(f["n_busy"], len(measurement))
        self.assertAlmostEqual(f["r_mean"], 0.2, places=3)

    def test_full_phone_current_is_not_leakage(self):
        samples = make_samples(current=1.0, n=20)
        samples += [
            Sample(t=1.0 + k * 0.04, voltage=5.0, current=0.03, power=0.15, state="CHARGING")
            for k in range(20)
        ]
        f = compute_features(samples, v_target=5.0, i_no_phone=0.01)
        self.assertIsNotNone(f)
        self.assertIsNone(f["idle_I"])

    def test_full_phone_current_is_not_counted_as_interruption(self):
        samples = make_samples(current=1.0, n=20)
        samples[10].current = 0.03
        f = compute_features(samples, v_target=5.0, i_no_phone=0.01)
        self.assertIsNotNone(f)
        self.assertEqual(f["interruption_frac"], 0.0)

    def test_interruption_frac_counts_recovered_dips_only(self):
        samples = make_samples(current=1.0, n=200, period=0.04)
        # make a 5-sample dip that recovers
        for k in range(10, 15):
            samples[k].current = 0.005
        # trailing 40 samples near zero (charge completion tail) - must NOT count
        for k in range(160, 200):
            samples[k].current = 0.005
        f = compute_features(samples, v_target=5.0)
        self.assertAlmostEqual(f["interruption_frac"], 5 / 200, places=3)


if __name__ == "__main__":
    unittest.main()
