"""Charge-loop regression tests for the post-CHARGED finish window."""
import tempfile
import unittest
from pathlib import Path

from src.hardware.ch224k import CH224KController
from src.hardware.gpio_map import PinMap
from src.hardware.ina219_reader import INA219Reader
from src.hardware.load_ctrl import LoadController
from src.hardware.sim import SimState
from src.main import run_charge
from src.telemetry.models import Sample
from src.telemetry.session import CHARGED, FAULT, SessionTracker
from src.telemetry.storage import Storage


class TestChargeFinishWindow(unittest.TestCase):
    def test_charge_loop_logs_charged_samples_before_stopping(self):
        state = SimState(current=0.0, v_target=5.0)
        cfg = {
            "hardware": {"simulate": True, "sample_rate_hz": 10.0},
            "ch224k": {"sel_truth": {5: 0, 9: 1, 12: 2}},
            "measurement": {"v_target_5v": 5.0, "r_fixture_ohm": 0.0, "i_min_compute": 0.1},
            "session": {
                "i_no_load": 0.05,
                "i_charge_start": 0.10,
                "debounce_start_s": 0.2,
                "debounce_end_s": 0.4,
                "debounce_finish_s": 0.4,
                "charge_timeout_s": 20.0,
            },
            "sim": {"phone_plug_s": 0.0, "phone_taper_s": 1.0, "phone_current_a": 1.2},
        }
        reader = INA219Reader(cfg, simulate=True, sim_state=state)
        ch224k = CH224KController(PinMap(), cfg, simulate=True, sim_state=state)
        load = LoadController(PinMap(), cfg, simulate=True, sim_state=state)
        try:
            features, meta, samples, tracker = run_charge(
                cfg, reader, ch224k, load, state, duration=20.0, phone_expected=True
            )
        finally:
            ch224k.close()
            load.close()
            reader.shutdown()

        self.assertIsNotNone(tracker.ended_at)
        charged = [sample for sample in samples if sample.state == CHARGED]
        self.assertGreaterEqual(len(charged), 2)
        self.assertEqual(charged[0].t, tracker.ended_at)
        self.assertGreaterEqual(samples[-1].t - tracker.ended_at, 0.4)
        self.assertLessEqual(samples[-1].t - tracker.ended_at, 0.4 + 0.1)
        self.assertEqual(meta.ended_at, samples[-1].t)

        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            try:
                csv_path = storage.export_csv("finish-window", samples)
                rows = Path(csv_path).read_text().splitlines()
                self.assertIn("state", rows[0])
                self.assertTrue(any(",CHARGED," in row for row in rows[1:]))
            finally:
                storage.close()

    def test_safety_fault_is_not_ignored_after_charged(self):
        cfg = {
            "session": {
                "i_no_load": 0.05,
                "i_charge_start": 0.10,
                "debounce_start_s": 0.0,
                "debounce_end_s": 0.0,
                "debounce_finish_s": 10.0,
            }
        }
        tracker = SessionTracker(cfg)
        tracker.state = CHARGED
        sample = Sample(t=1.0, voltage=28.0, current=0.1, power=2.8)

        state, event = tracker.update(sample)

        self.assertEqual(state, FAULT)
        self.assertEqual(event, "fault")
        self.assertEqual(sample.state, FAULT)


if __name__ == "__main__":
    unittest.main()
