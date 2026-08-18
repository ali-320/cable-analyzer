"""Tests for the CH224K-removed (--voltage) passive architecture."""
import unittest
from unittest.mock import patch

from tests.helpers import make_samples

from src.analysis.rules import evaluate
from src.features.metrics import compute_features_by_class, voltage_class_center
from src.hardware.ch224k import CH224KController
from src.hardware.gpio_map import PinMap
from src.hardware.ina219_reader import INA219Reader
from src.hardware.load_ctrl import LoadController
from src.hardware.sim import SimState
from src.main import run_charge, run_probe, run_self_check


def _cfg(phone_current_a: float = 1.2) -> dict:
    return {
        "hardware": {"simulate": True, "sample_rate_hz": 10.0},
        "ch224k": {"control_mode": "voltage", "sel_truth": {5: 0, 9: 1, 12: 2}},
        "voltage": {
            "enabled": True,
            "class_width_v": 1.0,
            "v_present_min_v": 3.0,
            "measurement_readings": 40,
        },
        "measurement": {"r_fixture_ohm": 0.05, "i_min_compute": 0.1, "length_m": 1.0},
        "session": {
            "i_no_phone_max": 0.010,
            "i_fully_charged_min": 0.010,
            "i_fully_charged_max": 0.10,
            "i_charge_start": 0.10,
            "i_no_load": 0.05,
            "debounce_state_s": 0.2,
            "debounce_start_s": 0.2,
            "debounce_end_s": 0.2,
            "debounce_finish_s": 1.0,
            "v_present_min_v": 3.0,
        },
        "probe": {},
        "load": {},
        "sim": {
            "phone_plug_s": 0.0,
            "phone_current_a": phone_current_a,
            "phone_taper_s": 60.0,
            "phone_charged_current_a": 0.03,
        },
    }


class TestVoltageClassBucketing(unittest.TestCase):
    def test_class_center_rounds_half_up(self):
        self.assertEqual(voltage_class_center(4.67), 5.0)
        self.assertEqual(voltage_class_center(5.8), 6.0)
        self.assertEqual(voltage_class_center(5.0), 5.0)
        self.assertEqual(voltage_class_center(8.7), 9.0)

    def test_compute_features_by_class_splits_and_combines(self):
        a = make_samples(v_target=5.0, r_cable=0.10, current=1.0, n=20)  # ~4.9 V -> class 5
        b = make_samples(v_target=9.0, r_cable=0.10, current=1.0, n=20)  # ~8.9 V -> class 9
        feature_sets, combined = compute_features_by_class(a + b, 1.0, r_fixture=0.0, i_min=0.1)
        self.assertEqual(set(feature_sets.keys()), {5.0, 9.0})
        self.assertAlmostEqual(feature_sets[5.0]["r_mean"], 0.1, places=6)
        self.assertAlmostEqual(feature_sets[9.0]["r_mean"], 0.1, places=6)
        self.assertAlmostEqual(combined["r_mean"], 0.1, places=6)
        self.assertEqual(combined["n_busy"], 40)

    def test_no_class_without_enough_busy_samples(self):
        samples = make_samples(v_target=5.0, current=0.005, n=20)  # leakage only
        feature_sets, combined = compute_features_by_class(samples, 1.0)
        self.assertEqual(feature_sets, {})
        self.assertIsNone(combined)


class TestVoltageModeHardware(unittest.TestCase):
    def test_voltage_ch224k_is_a_noop_without_gpio(self):
        cfg = {
            "hardware": {"simulate": False},
            "ch224k": {"control_mode": "voltage", "sel_truth": {5: 0}},
            "load": {},
        }
        controller = CH224KController(PinMap(), cfg, simulate=False)
        self.assertTrue(controller.voltage_mode)
        self.assertFalse(controller.manual)
        self.assertTrue(controller.set_voltage(9.0))  # always succeeds, no negotiation
        self.assertIsNone(controller.voltage)
        self.assertIsNone(controller.read_pwr_ok())
        controller.enable(True)  # no-op, must not raise
        controller.close()

    def test_voltage_load_controller_has_no_gpio(self):
        cfg = {
            "hardware": {"simulate": False},
            "ch224k": {"control_mode": "voltage", "sel_truth": {5: 0}},
            "load": {},
        }
        load = LoadController(PinMap(), cfg, simulate=False)
        self.assertTrue(load.manual)
        load.set_current(1.0)
        load.phone_switch(True)
        self.assertEqual(load.current, 1.0)
        self.assertTrue(load.phone_on)
        load.close()


class TestVoltageModeSelfCheck(unittest.TestCase):
    def test_self_check_passes_when_source_voltage_present(self):
        cfg = {
            "hardware": {"simulate": True},
            "ch224k": {"control_mode": "voltage"},
            "session": {"v_present_min_v": 3.0},
            "measurement": {},
        }
        state = SimState(v_target=5.0, current=0.5)
        reader = INA219Reader(cfg, simulate=True, sim_state=state)
        ch224k = CH224KController(PinMap(), cfg, simulate=True, sim_state=state)
        try:
            results = run_self_check(cfg, reader, ch224k)
        finally:
            ch224k.close()
            reader.shutdown()
        self.assertTrue(results["voltage_mode"])
        self.assertTrue(results["voltage"])
        self.assertEqual(results["v_present_min_v"], 3.0)

    def test_self_check_fails_when_no_source(self):
        cfg = {
            "hardware": {"simulate": True},
            "ch224k": {"control_mode": "voltage"},
            "session": {"v_present_min_v": 3.0},
            "measurement": {},
        }
        state = SimState(powered=False)
        reader = INA219Reader(cfg, simulate=True, sim_state=state)
        ch224k = CH224KController(PinMap(), cfg, simulate=True, sim_state=state)
        try:
            results = run_self_check(cfg, reader, ch224k)
        finally:
            ch224k.close()
            reader.shutdown()
        self.assertFalse(results["voltage"])


class TestVoltageModeProbe(unittest.TestCase):
    def test_probe_reads_charger_voltage_without_prompts(self):
        state = SimState(r_cable_ohm=0.15, r_fixture_ohm=0.05, v_noise=0.0, i_noise=0.0,
                         current=0.0, v_target=5.0)
        cfg = _cfg()
        reader = INA219Reader(cfg, simulate=True, sim_state=state)
        ch224k = CH224KController(PinMap(), cfg, simulate=True, sim_state=state)
        load = LoadController(PinMap(), cfg, simulate=True, sim_state=state)
        try:
            # Any manual prompt would raise: voltage mode must never ask.
            with patch("builtins.input", side_effect=AssertionError("no manual voltage prompt expected")):
                probe = run_probe(cfg, reader, ch224k, load, state)
        finally:
            ch224k.close()
            load.close()
            reader.shutdown()

        self.assertTrue(probe["voltage_mode"])
        self.assertFalse(probe["manual_voltage_mode"])
        self.assertTrue(probe["v_present"])
        self.assertFalse(probe["no_current_all_voltages"])
        self.assertTrue(probe["voltage_classes"])
        self.assertIsNotNone(probe.get("quality_features"))
        self.assertEqual(probe["quality_reference_voltage"], 5)
        self.assertGreater(probe["quality_features"]["n_busy"], 5)
        self.assertAlmostEqual(probe["quality_features"]["r_mean"], 0.15, places=2)
        samples = probe.pop("_samples")
        self.assertEqual(len(samples), 40)

    def test_probe_without_charging_current(self):
        state = SimState(r_cable_ohm=0.15, r_fixture_ohm=0.05, v_noise=0.0, i_noise=0.0,
                         current=0.0, v_target=5.0)
        cfg = _cfg(phone_current_a=0.0)
        reader = INA219Reader(cfg, simulate=True, sim_state=state)
        ch224k = CH224KController(PinMap(), cfg, simulate=True, sim_state=state)
        load = LoadController(PinMap(), cfg, simulate=True, sim_state=state)
        try:
            with patch("builtins.input", side_effect=AssertionError("no manual voltage prompt expected")):
                probe = run_probe(cfg, reader, ch224k, load, state)
        finally:
            ch224k.close()
            load.close()
            reader.shutdown()

        self.assertTrue(probe["v_present"])  # the rail exists...
        self.assertTrue(probe["no_current_all_voltages"])  # ...but nothing charges
        self.assertNotIn("quality_features", probe)


class TestVoltageModeCharge(unittest.TestCase):
    def test_charge_mode_runs_without_voltage_prompt(self):
        state = SimState(r_cable_ohm=0.15, r_fixture_ohm=0.05, v_noise=0.0, i_noise=0.0,
                         current=0.0, v_target=5.0)
        cfg = _cfg()
        cfg["sim"]["phone_taper_s"] = 2.0
        cfg["session"]["charge_timeout_s"] = 20.0
        reader = INA219Reader(cfg, simulate=True, sim_state=state)
        ch224k = CH224KController(PinMap(), cfg, simulate=True, sim_state=state)
        load = LoadController(PinMap(), cfg, simulate=True, sim_state=state)
        try:
            with patch("builtins.input", side_effect=AssertionError("no manual voltage prompt expected")):
                features, meta, samples, tracker = run_charge(
                    cfg, reader, ch224k, load, state, duration=5.0, phone_expected=True
                )
        finally:
            ch224k.close()
            load.close()
            reader.shutdown()

        self.assertIsNotNone(features)
        self.assertTrue(meta.v_present)
        self.assertAlmostEqual(meta.v_target, 5.0, places=1)
        self.assertTrue(any(s.state == "CHARGING" for s in samples))


class TestVoltageModeRules(unittest.TestCase):
    FEATURES = {
        "r_mean": 0.1, "r_std": 0.01, "r_max": 0.11, "r_p95": 0.11, "r_p5": 0.09,
        "r_dvdi": None, "dV_dI_slope": 0.0, "sigma_V": 0.0, "V_min": 8.9, "eta": 0.98,
        "mean_I": 1.0, "max_I": 1.0, "mean_P_loss": 0.01, "E_wh": 0.0,
        "dR_dt_mOhm_per_min": 0.0, "interruption_frac": 0.0, "spike_count": 0,
        "idle_I": None, "n_busy": 20, "n_total": 20, "valid_frac": 1.0,
        "duration_s": 1.0, "v_target": 9.0, "r_fixture": 0.0, "length_m": 1.0,
    }
    CFG = {"rules": {"confidence_resistance_floor_ohm": 0.02}}

    def test_voltage_mode_skips_fallback_reference(self):
        meta = {
            "session_id": "T1", "v_present": True, "phone_expected": True,
            "fault_reason": None, "length_m": 1.0, "voltage_mode": True,
            "probe": {"quality_reference_voltage": 9, "support_flags": {"5": False, "9": True}},
        }
        verdict = evaluate(self.FEATURES, meta, self.CFG)
        self.assertNotIn("FALLBACK_VOLTAGE_REFERENCE", verdict["tags"])
        self.assertNotIn("VOLTAGE_MISMATCH", verdict["tags"])
        self.assertNotIn("RAIL_VERIFICATION_FAILED", verdict["tags"])
        self.assertEqual(verdict["confidence_details"]["voltage_penalty"], 1.0)
        self.assertTrue(any("fixture path" in line for line in verdict["limitations"]))
        self.assertFalse(any("CH224K path" in line for line in verdict["limitations"]))

    def test_non_voltage_mode_still_applies_fallback_penalty(self):
        meta = {
            "session_id": "T2", "v_present": True, "phone_expected": True,
            "fault_reason": None, "length_m": 1.0, "voltage_mode": False,
            "probe": {"quality_reference_voltage": 9, "support_flags": {"5": False, "9": True}},
        }
        verdict = evaluate(self.FEATURES, meta, self.CFG)
        self.assertIn("FALLBACK_VOLTAGE_REFERENCE", verdict["tags"])
        self.assertEqual(verdict["confidence_details"]["voltage_penalty"], 0.85)


if __name__ == "__main__":
    unittest.main()
