"""Unit tests for src/features/metrics.py (DEVELOPMENT_PLAN.md §5)."""
import unittest

from tests.helpers import make_samples

from src.features.metrics import combine_features, compute_features, compute_features_by_class, linreg, percentile, stdev
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

    def test_dvdi_slope_subtracts_fixture_resistance(self):
        samples = make_samples(r_cable=0.25, current=1.0, n=20)
        samples += make_samples(r_cable=0.25, current=1.5, n=20)
        features = compute_features(samples, v_target=5.0, r_fixture=0.05)
        self.assertAlmostEqual(features["r_dvdi"], 0.20, places=3)

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

    def test_combines_features_from_multiple_supported_voltages(self):
        def voltage_samples(target):
            return [
                Sample(
                    t=index * 0.04,
                    voltage=target - current * 0.25,
                    current=current,
                    power=(target - current * 0.25) * current,
                    state="PROBE",
                    valid=True,
                )
                for index, current in enumerate((0.5, 0.8, 1.1, 1.4, 1.7, 2.0))
            ]

        low = compute_features(voltage_samples(5.0), v_target=5.0)
        high = compute_features(voltage_samples(9.0), v_target=9.0)
        combined = combine_features([low, high], reference_voltage=5.0)
        self.assertAlmostEqual(combined["r_mean"], 0.25, places=3)
        self.assertAlmostEqual(combined["r_dvdi"], 0.25, places=3)
        self.assertEqual(combined["n_busy"], low["n_busy"] + high["n_busy"])

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


class TestVoltageModeResistanceCalculation(unittest.TestCase):
    """Regression tests for the intercept-based resistance formula.

    In voltage mode the class center (v_target) is NOT the true source voltage;
    the V-vs-I regression intercept must be used instead.
    """

    def test_r_mean_nonzero_when_class_center_differs_from_source(self):
        """Simulate an 8.1 V charger; class center = 8.0 V.

        V_load = 8.1 - I * 0.2, but compute_features sees v_target = 8.0.
        Old formula: (8.0 - V_load)/I ≈ (8.0 - 8.1 + I*0.2)/I = -0.1/I + 0.2
          → negative for low current, clamped to 0 → r_mean ≈ 0.
        New formula: intercept ≈ 8.1, so (8.1 - V_load)/I = 0.2 always.
        """
        r_cable = 0.2
        source_v = 8.1
        samples = [
            Sample(
                t=i * 0.04,
                voltage=source_v - current * r_cable,
                current=current,
                power=(source_v - current * r_cable) * current,
                state="CHARGING",
                valid=True,
            )
            for i, current in enumerate([0.5, 0.8, 1.0, 1.2, 1.5, 1.8, 2.0])
        ]
        f = compute_features(samples, v_target=8.0, r_fixture=0.0)
        self.assertIsNotNone(f)
        self.assertAlmostEqual(f["r_mean"], r_cable, places=3)
        self.assertGreater(f["r_mean"], 0.0)

    def test_r_dvdi_nonzero_with_varying_current_and_offset_target(self):
        """The dV/dI slope should give R_cable regardless of v_target offset."""
        r_cable = 0.25
        source_v = 9.15
        samples = [
            Sample(
                t=i * 0.04,
                voltage=source_v - current * r_cable,
                current=current,
                power=(source_v - current * r_cable) * current,
                state="CHARGING",
                valid=True,
            )
            for i, current in enumerate([0.5, 0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.5])
        ]
        f = compute_features(samples, v_target=9.0, r_fixture=0.0)
        self.assertIsNotNone(f)
        self.assertAlmostEqual(f["r_dvdi"], r_cable, places=2)
        self.assertGreater(f["r_dvdi"], 0.0)

    def test_fixture_subtraction_with_offset_target(self):
        """Fixture subtraction should work with the intercept path too."""
        r_cable = 0.3
        r_fixture = 0.05
        source_v = 5.15
        samples = [
            Sample(
                t=i * 0.04,
                voltage=source_v - current * r_cable,
                current=current,
                power=(source_v - current * r_cable) * current,
                state="CHARGING",
                valid=True,
            )
            for i, current in enumerate([0.5, 0.8, 1.0, 1.2, 1.5, 1.8, 2.0])
        ]
        f = compute_features(samples, v_target=5.0, r_fixture=r_fixture)
        self.assertIsNotNone(f)
        self.assertAlmostEqual(f["r_mean"], r_cable - r_fixture, places=3)

    def test_fallback_to_v_target_when_current_is_constant(self):
        """When current is constant (i_spread < 0.1), the regression is
        unreliable so the code falls back to v_target.  r_loop may then be
        near-zero if v_target ≈ measured voltage — this is expected and honest.
        """
        r_cable = 0.2
        source_v = 8.1
        # All samples at exactly the same current → i_spread = 0
        samples = [
            Sample(
                t=i * 0.04,
                voltage=source_v - 1.0 * r_cable,
                current=1.0,
                power=(source_v - 1.0 * r_cable) * 1.0,
                state="CHARGING",
                valid=True,
            )
            for i in range(30)
        ]
        f = compute_features(samples, v_target=8.0, r_fixture=0.0)
        self.assertIsNotNone(f)
        # With constant current, regression fallback uses v_target; the
        # per-sample r_loop = (8.0 - (8.1 - 0.2)) / 1.0 = 0.1, which is
        # the difference between class center and (source - drop), not the
        # true cable resistance.  This is acceptable — we cannot measure R
        # without current variation.
        self.assertIsNotNone(f["r_mean"])

    def test_compute_features_by_class_with_offset_source(self):
        """Voltage-class bucketing should produce non-zero r_mean for each
        class when current varies within each class.
        """
        r_cable = 0.15
        # Simulate a 5.15 V source and an 8.15 V source mixed together
        samples_5v = [
            Sample(
                t=i * 0.04,
                voltage=5.15 - c * r_cable,
                current=c,
                power=(5.15 - c * r_cable) * c,
                state="CHARGING",
                valid=True,
            )
            for i, c in enumerate([0.5, 0.8, 1.0, 1.2, 1.5])
        ]
        samples_9v = [
            Sample(
                t=0.2 + i * 0.04,
                voltage=8.15 - c * r_cable,
                current=c,
                power=(8.15 - c * r_cable) * c,
                state="CHARGING",
                valid=True,
            )
            for i, c in enumerate([0.5, 0.8, 1.0, 1.2, 1.5])
        ]
        feature_sets, combined = compute_features_by_class(
            samples_5v + samples_9v, 1.0, r_fixture=0.0,
        )
        self.assertIn(5.0, feature_sets)
        # 8.15V source with 0.15Ω cable: measured V ranges 7.925-8.075V,
        # which rounds to class 8.0 (not 9.0)
        self.assertIn(8.0, feature_sets)
        self.assertEqual(len(feature_sets), 2,
                         msg=f"Expected 2 classes, got {list(feature_sets.keys())}")
        for center, feat in feature_sets.items():
            self.assertGreater(feat["r_mean"], 0.0,
                               msg=f"r_mean should be non-zero for class {center}")
        self.assertIsNotNone(combined)
        self.assertGreater(combined["r_mean"], 0.0)


if __name__ == "__main__":
    unittest.main()
