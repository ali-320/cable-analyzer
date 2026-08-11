"""Regression tests for manual-probe CSV sample collection."""
import tempfile
import unittest
from unittest.mock import patch

from src.hardware.ch224k import CH224KController
from src.hardware.gpio_map import PinMap
from src.hardware.ina219_reader import INA219Reader
from src.hardware.load_ctrl import LoadController
from src.hardware.sim import SimState
from src.main import run_probe
from src.telemetry.storage import Storage
from src.ui import cli


class TestProbeCsvCollection(unittest.TestCase):
    def test_manual_probe_labels_verification_and_measurement_states(self):
        state = SimState(current=0.5, v_target=5.0)
        cfg = {
            "hardware": {"simulate": False, "sample_rate_hz": 10.0},
            "ch224k": {"control_mode": "manual", "sel_truth": {5: 0}},
            "probe": {"voltages": [5], "manual_support_readings": 12,
                      "manual_total_readings": 20, "manual_min_measurement_readings": 5},
            "measurement": {"r_fixture_ohm": 0.0, "i_min_compute": 0.1},
            "session": {"i_no_phone_max": 0.01, "i_fully_charged_min": 0.01,
                        "i_fully_charged_max": 0.1, "i_charge_start": 0.1,
                        "i_no_load": 0.05, "debounce_start_s": 0.2,
                        "debounce_end_s": 0.2, "debounce_finish_s": 10.0},
        }
        reader = INA219Reader(cfg, simulate=True, sim_state=state)
        ch224k = CH224KController(PinMap(), cfg, simulate=False, sim_state=state)
        load = LoadController(PinMap(), cfg, simulate=False, sim_state=state)
        try:
            with patch("builtins.input", return_value="5"), patch.object(cli, "print_live") as live:
                probe = run_probe(cfg, reader, ch224k, load, state)
            samples = probe.pop("_samples")
            self.assertTrue(samples)
            self.assertTrue(any(s.state == "VERIFICATION" for s in samples))
            measurement_states = {s.state for s in samples if s.state != "VERIFICATION"}
            self.assertTrue(measurement_states & {"IDLE", "CHARGING"})
            self.assertGreaterEqual(live.call_count, 1)
        finally:
            ch224k.close()
            load.close()
            reader.shutdown()

    def test_verification_supports_active_and_fully_charged_current(self):
        """Any phone current above leakage proves voltage support."""
        for current, expected in ((0.50, True), (0.05, True), (0.005, False)):
            with self.subTest(current=current):
                state = SimState(current=current, v_target=5.0, i_noise=0.0)
                cfg = {
                    "hardware": {"simulate": False, "sample_rate_hz": 10.0},
                    "ch224k": {"control_mode": "manual", "sel_truth": {5: 0}},
                    "probe": {"voltages": [5], "manual_support_readings": 4,
                              "manual_total_readings": 5, "manual_min_measurement_readings": 1},
                    "measurement": {"r_fixture_ohm": 0.0, "i_min_compute": 0.1},
                    "session": {"i_no_phone_max": 0.01, "i_fully_charged_min": 0.01,
                                "i_fully_charged_max": 0.1, "i_charge_start": 0.1,
                                "i_no_load": 0.05, "debounce_start_s": 0.1,
                                "debounce_end_s": 0.1, "debounce_finish_s": 1.0},
                }
                reader = INA219Reader(cfg, simulate=True, sim_state=state)
                ch224k = CH224KController(PinMap(), cfg, simulate=False, sim_state=state)
                load = LoadController(PinMap(), cfg, simulate=False, sim_state=state)
                try:
                    with patch("builtins.input", return_value="5"):
                        probe = run_probe(cfg, reader, ch224k, load, state)
                    self.assertEqual(probe["support_flags"]["5"], expected)
                finally:
                    ch224k.close()
                    load.close()
                    reader.shutdown()

    def test_manual_probe_returns_samples_for_export(self):
        state = SimState(current=0.5, v_target=5.0)
        cfg = {
            "hardware": {"simulate": False, "sample_rate_hz": 10.0},
            "ch224k": {
                "control_mode": "manual",
                "sel_truth": {5: 0, 9: 1, 12: 2},
            },
            "probe": {
                "voltages": [5],
                "manual_5v_hold_s": 0.2,
                "manual_hold_s": 0.2,
                "recovery_hold_s": 0.2,
            },
            "measurement": {
                "r_fixture_ohm": 0.0,
                "i_min_compute": 0.1,
            },
            "session": {"i_charge_start": 0.1, "i_no_load": 0.05},
        }
        reader = INA219Reader(cfg, simulate=True, sim_state=state)
        ch224k = CH224KController(PinMap(), cfg, simulate=False, sim_state=state)
        load = LoadController(PinMap(), cfg, simulate=False, sim_state=state)
        try:
            with patch("builtins.input", return_value="5"):
                probe = run_probe(cfg, reader, ch224k, load, state)
            samples = probe.pop("_samples")
            self.assertGreater(len(samples), 0)
            self.assertNotIn("_samples", probe)

            with tempfile.TemporaryDirectory() as tmp:
                storage = Storage(tmp)
                try:
                    sid = storage.new_session(__import__("src.telemetry.models", fromlist=["SessionMeta"]).SessionMeta(mode="probe"))
                    csv_path = storage.export_csv(sid, samples)
                    self.assertTrue(csv_path.exists())
                    self.assertGreater(len(csv_path.read_text().splitlines()), 1)
                finally:
                    storage.close()
        finally:
            ch224k.close()
            load.close()
            reader.shutdown()


if __name__ == "__main__":
    unittest.main()
