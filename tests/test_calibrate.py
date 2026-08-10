"""Tests for reference calibration paths."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.calibrate import _manual_passive_calibration
from src.hardware.ch224k import CH224KController
from src.hardware.gpio_map import PinMap
from src.hardware.ina219_reader import INA219Reader
from src.hardware.load_ctrl import LoadController
from src.hardware.sim import SimState


class TestManualPassiveCalibration(unittest.TestCase):
    def test_manual_calibration_uses_phone_current_and_writes_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = SimState(
                r_cable_ohm=0.02,
                r_fixture_ohm=0.08,
                current=1.2,
                v_noise=0.0,
                i_noise=0.0,
            )
            cfg = {
                "hardware": {"simulate": True, "sample_rate_hz": 10.0},
                "ch224k": {
                    "control_mode": "manual",
                    "sel_truth": {5: 0, 9: 1, 12: 2},
                    "renegotiate_wait_s": 0.0,
                },
                "probe": {"manual_5v_hold_s": 1.0},
                "measurement": {"v_target_5v": 5.0, "i_min_compute": 0.1},
                "sim": {"phone_current_a": 1.2},
                "paths": {"data_dir": tmp},
            }
            reader = INA219Reader(cfg, simulate=True, sim_state=state)
            ch224k = CH224KController(PinMap(), cfg, simulate=True, sim_state=state)
            load = LoadController(PinMap(), cfg, simulate=True, sim_state=state)
            try:
                status, mean_mohm, output = _manual_passive_calibration(
                    cfg, reader, ch224k, load, state, 10.0, True, 5.0, 0.1
                )
            finally:
                load.close()
                ch224k.close()
                reader.shutdown()

            self.assertEqual(status, 0)
            self.assertIsNotNone(output)
            self.assertAlmostEqual(mean_mohm, 100.0, places=1)
            data = json.loads(Path(output).read_text())
            self.assertEqual(data["method"], "known-good reference cable with passive phone load at 5 V")
            self.assertEqual(data["steps"][0]["mode"], "manual_phone_load")
            self.assertAlmostEqual(data["steps"][0]["i_mean_a"], 1.2, places=2)

    def test_manual_calibration_rejects_low_voltage_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = SimState(
                r_cable_ohm=0.30,
                r_fixture_ohm=0.08,
                current=1.2,
                v_noise=0.0,
                i_noise=0.0,
            )
            cfg = {
                "hardware": {"simulate": True, "sample_rate_hz": 10.0},
                "ch224k": {
                    "control_mode": "manual",
                    "sel_truth": {5: 0, 9: 1, 12: 2},
                    "renegotiate_wait_s": 0.0,
                },
                "probe": {"manual_5v_hold_s": 0.5},
                "measurement": {
                    "v_target_5v": 5.0,
                    "v_min_compliance_5v": 4.75,
                    "i_min_compute": 0.1,
                },
                "paths": {"data_dir": tmp},
            }
            reader = INA219Reader(cfg, simulate=True, sim_state=state)
            ch224k = CH224KController(PinMap(), cfg, simulate=True, sim_state=state)
            load = LoadController(PinMap(), cfg, simulate=True, sim_state=state)
            try:
                status, mean_mohm, output = _manual_passive_calibration(
                    cfg, reader, ch224k, load, state, 10.0, True, 5.0, 0.1
                )
            finally:
                load.close()
                ch224k.close()
                reader.shutdown()

            self.assertEqual(status, 1)
            self.assertIsNone(mean_mohm)
            self.assertIsNone(output)
            self.assertFalse(list(Path(tmp).glob("calibration.json")))

    def test_manual_calibration_requires_real_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = SimState(
                r_cable_ohm=0.02,
                r_fixture_ohm=0.08,
                current=0.0,
                v_noise=0.0,
                i_noise=0.0,
            )
            cfg = {
                "hardware": {"simulate": True, "sample_rate_hz": 10.0},
                "ch224k": {
                    "control_mode": "manual",
                    "sel_truth": {5: 0, 9: 1, 12: 2},
                    "renegotiate_wait_s": 0.0,
                },
                "probe": {"manual_5v_hold_s": 0.5},
                "measurement": {"v_target_5v": 5.0, "i_min_compute": 0.1},
                "sim": {"phone_current_a": 0.0},
                "paths": {"data_dir": tmp},
            }
            reader = INA219Reader(cfg, simulate=True, sim_state=state)
            ch224k = CH224KController(PinMap(), cfg, simulate=True, sim_state=state)
            load = LoadController(PinMap(), cfg, simulate=True, sim_state=state)
            try:
                status, mean_mohm, output = _manual_passive_calibration(
                    cfg, reader, ch224k, load, state, 10.0, True, 5.0, 0.1
                )
            finally:
                load.close()
                ch224k.close()
                reader.shutdown()

            self.assertEqual(status, 1)
            self.assertIsNone(mean_mohm)
            self.assertIsNone(output)
            self.assertFalse(list(Path(tmp).glob("calibration.json")))


if __name__ == "__main__":
    unittest.main()
