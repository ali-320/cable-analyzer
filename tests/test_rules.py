"""Unit tests for src/analysis/rules.py (DEVELOPMENT_PLAN.md §6)."""
import unittest

from src.analysis.rules import evaluate, grade_from_r

CFG = {"rules": {"grade_limits_mohm": [150, 250, 300, 500], "sigma_v_marginal_mv": 25.0, "confidence_resistance_floor_ohm": 0.02}}


def base_features(r_mean: float = 0.2, sigma_v: float = 0.004, drdt: float = 0.0,
                  inter_frac: float = 0.0, spikes: int = 0, v_min: float = 4.8,
                  idle_i: float | None = 0.0, r_p95: float = 0.21, r_p5: float = 0.19):
    return {
        "r_mean": r_mean, "r_std": r_mean * 0.02, "r_max": r_mean * 1.05,
        "r_p95": r_p95, "r_p5": r_p5, "r_dvdi": r_mean, "dV_dI_slope": -r_mean,
        "sigma_V": sigma_v, "V_min": v_min, "eta": v_min / 5.0,
        "mean_I": 1.0, "max_I": 1.2, "mean_P_loss": 0.05, "E_wh": 0.5,
        "dR_dt_mOhm_per_min": drdt, "interruption_frac": inter_frac,
        "spike_count": spikes, "idle_I": idle_i, "n_busy": 100, "n_total": 110,
        "valid_frac": 0.95, "duration_s": 60.0, "v_target": 5.0,
        "r_fixture": 0.05, "length_m": 1.0,
    }


META = {"session_id": "T1", "v_present": True, "phone_expected": True, "fault_reason": None}


class TestGrades(unittest.TestCase):
    def test_grade_boundaries(self):
        # strict upper bounds: A<150, B<250, C<300, D<500, else F
        cases = [(0.10, "A"), (0.14, "A"), (0.16, "B"), (0.24, "B"), (0.26, "C"),
                 (0.29, "C"), (0.35, "D"), (0.49, "D"), (0.55, "F")]
        for r_mohm, expected in cases:
            letter, _ = grade_from_r(r_mohm * 1000, [150, 250, 300, 500])
            self.assertEqual(letter, expected, f"R={r_mohm}")

    def test_verdict_shapes(self):
        v = evaluate(base_features(), dict(META), CFG)
        for key in ("verdict", "grade", "tags", "confidence", "evidence", "limitations", "session_id"):
            self.assertIn(key, v)
        self.assertEqual(v["grade"], "B")
        details = v["confidence_details"]
        self.assertAlmostEqual(details["r_mean"], 0.2, places=6)
        self.assertAlmostEqual(details["r_dvdi"], 0.2, places=6)
        self.assertEqual(details["n_busy"], 100)
        self.assertAlmostEqual(details["r_std"], 0.004, places=6)
        self.assertAlmostEqual(details["scale"], 0.2, places=6)
        self.assertAlmostEqual(details["agreement"], 1.0, places=6)
        self.assertEqual(details["voltage_penalty"], 1.0)

    def test_confidence_uses_configured_resistance_floor(self):
        features = base_features(r_mean=0.005)
        cfg = {"rules": {**CFG["rules"], "confidence_resistance_floor_ohm": 0.03}}
        verdict = evaluate(features, dict(META), cfg)
        self.assertAlmostEqual(verdict["confidence_details"]["scale"], 0.03, places=6)

    def test_no_zero_confidence_from_noisy_long_measurement(self):
        # Raw spread may exceed the mean after fixture subtraction, but a long
        # dataset can still provide a useful estimate of its mean resistance.
        features = base_features(r_mean=0.05)
        features["r_std"] = 0.10
        features["r_dvdi"] = None
        features["n_busy"] = 400
        verdict = evaluate(features, dict(META), CFG)
        self.assertGreater(verdict["confidence"], 0.0)
        self.assertEqual(verdict["confidence_details"]["r_mean"], 0.05)
        self.assertIsNone(verdict["confidence_details"]["r_dvdi"])
        self.assertIsNone(verdict["confidence_details"]["agreement"])

    def test_no_source_verdict(self):
        v = evaluate(None, {"session_id": "T2", "v_present": False, "phone_expected": False}, CFG)
        self.assertEqual(v["verdict"], "NO_SOURCE")

    def test_open_verdict(self):
        v = evaluate(None, {"session_id": "T3", "v_present": True, "phone_expected": True}, CFG)
        self.assertEqual(v["verdict"], "OPEN")
        self.assertIn("OPEN", v["tags"])

    def test_no_charge_verdict(self):
        v = evaluate(None, {"session_id": "T4", "v_present": True, "phone_expected": False}, CFG)
        self.assertEqual(v["verdict"], "NO_CHARGE")

    def test_no_current_all_voltages_verdict(self):
        v = evaluate(
            None,
            {
                "session_id": "T5",
                "v_present": True,
                "probe": {
                    "no_current_all_voltages": True,
                    "support_flags": {"5": False, "9": False, "12": False},
                },
            },
            CFG,
        )
        self.assertEqual(v["verdict"], "NO_CURRENT_ALL_VOLTAGES")
        self.assertIn("NO_CURRENT_ALL_VOLTAGES", v["tags"])

    def test_fault_override(self):
        v = evaluate(base_features(), dict(META, fault_reason="short_condition"), CFG)
        self.assertEqual(v["grade"], "F")
        self.assertEqual(v["verdict"], "FAULT/ABORT")
        self.assertIn("FAULT", v["tags"])


class TestDefectTags(unittest.TestCase):
    def test_unstable_contact_override(self):
        v = evaluate(base_features(sigma_v=0.030), dict(META), CFG)  # 30 mV
        self.assertIn("UNSTABLE_CONTACT", v["tags"])
        self.assertIn(v["grade"], ("C", "D", "F"))  # capped at C

    def test_self_heating_override(self):
        v = evaluate(base_features(drdt=6.0), dict(META), CFG)
        self.assertIn("SELF_HEATING", v["tags"])
        self.assertEqual(v["grade"], "D")
        self.assertEqual(v["verdict"], "Grade D: Poor")

    def test_intermittent_tag(self):
        v = evaluate(base_features(inter_frac=0.02, spikes=15), dict(META), CFG)
        self.assertIn("INTERMITTENT", v["tags"])

    def test_pd_blocked_tag(self):
        meta = dict(META, probe={"pd_blocked": [9, 12]})
        v = evaluate(base_features(), meta, CFG)
        self.assertIn("PD_BLOCKED", v["tags"])
        self.assertTrue(any("PD negotiation failed" in e for e in v["evidence"]))

    def test_fallback_voltage_reference_tag(self):
        meta = dict(
            META,
            probe={
                "quality_reference_voltage": 9,
                "support_flags": {"5": False, "9": True, "12": False},
            },
        )
        v = evaluate(base_features(), meta, CFG)
        self.assertIn("FALLBACK_VOLTAGE_REFERENCE", v["tags"])
        self.assertEqual(v["confidence_details"]["voltage_penalty"], 0.85)

    def test_leaky_tag(self):
        v = evaluate(base_features(idle_i=0.05), dict(META), CFG)  # 50 mA idle
        self.assertIn("LEAKY", v["tags"])

    def test_length_normalization(self):
        # 0.4 ohm raw over 2 m -> 200 mOhm/m -> grade B
        feats = base_features(r_mean=0.4)
        v = evaluate(feats, dict(META, length_m=2.0), CFG)
        self.assertEqual(v["grade"], "B")
        # same raw value with length 1 m -> 400 mOhm/m -> D
        v1 = evaluate(feats, dict(META, length_m=1.0), CFG)
        self.assertEqual(v1["grade"], "D")


if __name__ == "__main__":
    unittest.main()
