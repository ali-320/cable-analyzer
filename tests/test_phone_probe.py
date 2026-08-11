"""Tests for manual phone-inline probe workflow."""
import unittest
from unittest.mock import patch

from src.main import _manual_voltage_confirmation
from src.main import run_probe
from src.hardware.ch224k import CH224KController
from src.hardware.gpio_map import PinMap
from src.hardware.ina219_reader import INA219Reader
from src.hardware.load_ctrl import LoadController
from src.hardware.sim import SimState


def _cfg(voltages=(5, 9, 12)) -> dict:
    return {
        "hardware": {"simulate": False, "sample_rate_hz": 10.0},
        "ch224k": {
            "control_mode": "manual",
            "sel_truth": {5: 0, 9: 1, 12: 2},
        },
        "probe": {
            "voltages": list(voltages),
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


class TestPhoneInlineProbe(unittest.TestCase):
    def test_manual_confirmation_accepts_decimal_voltage(self):
        with patch("builtins.input", return_value="5.0"):
            _manual_voltage_confirmation(5.0)

    def test_reverse_current_configuration(self):
        cfg = {
            "hardware": {"simulate": True},
            "measurement": {
                "current_direction": "reverse",
                "bus_voltage_side": "source",
            },
        }
        reader = INA219Reader(cfg, simulate=True)
        self.assertEqual(reader.current_sign, -1.0)
        self.assertEqual(reader.bus_voltage_side, "source")

    def test_manual_probe_accepts_rail_below_requested_pdo(self):
        """A 9 V step that reads ~8.4 V must NOT trigger recovery or mismatch.

        The phone is the load, so a lossy cable pulls the rail below the
        requested PDO; verification must accept the step up from 5 V.
        """
        state = SimState(current=0.5, v_target=5.0)
        cfg = _cfg()
        reader = INA219Reader(cfg, simulate=True, sim_state=state)
        ch224k = CH224KController(PinMap(), cfg, simulate=False, sim_state=state)
        load = LoadController(PinMap(), cfg, simulate=False, sim_state=state)
        try:
            with patch("builtins.input", side_effect=["5", "9", "12", "5", "9", "12", "5"]):
                def step_v(target_v: float) -> bool:
                    # operator manually changed the SEL straps: simulate the
                    # rail stepping up but sagging below target (lossy cable)
                    state.v_target = {
                        5: 5.0, 9: 8.4, 12: 11.3,
                    }[int(target_v)]
                    return True
                with patch.object(ch224k, "set_voltage", side_effect=step_v, return_value=True):
                    probe = run_probe(cfg, reader, ch224k, load, state)
            self.assertEqual(probe.get("manual_voltage_mismatch"), None)
            self.assertEqual(probe.get("unsupported_voltages"), [])
            self.assertEqual(probe.get("support_flags"), {"5": True, "9": True, "12": True})
            self.assertEqual(probe.get("measurement_allocations"), {"5": 1000, "9": 1000, "12": 1000})
            self.assertEqual(len(probe.get("manual_readings", [])), 3)
        finally:
            ch224k.close()
            load.close()
            reader.shutdown()

    def test_manual_probe_recovers_when_rail_did_not_step_up(self):
        """A 12 V step that stays at 5 V is recorded as a rail mismatch."""
        state = SimState(current=0.5, v_target=5.0)
        cfg = _cfg()
        reader = INA219Reader(cfg, simulate=True, sim_state=state)
        ch224k = CH224KController(PinMap(), cfg, simulate=False, sim_state=state)
        load = LoadController(PinMap(), cfg, simulate=False, sim_state=state)
        try:
            with patch("builtins.input", side_effect=["5", "9", "12", "5", "9", "5"]):
                def step_v(target_v: float) -> bool:
                    # 5 and 9 rise normally; the phone rejects 12 V, so the
                    # rail stays where the operator actually left it (5 V)
                    state.v_target = {
                        5: 5.0, 9: 9.0, 12: 5.0,
                    }[int(target_v)]
                    return True
                with patch.object(ch224k, "set_voltage", side_effect=step_v, return_value=True):
                    probe = run_probe(cfg, reader, ch224k, load, state)
            self.assertIn(12, probe.get("manual_voltage_mismatch", []))
            self.assertEqual(probe.get("support_flags", {}).get("12"), False)
            self.assertEqual(probe.get("measurement_allocations"), {"5": 1500, "9": 1500})
            self.assertEqual(probe.get("recovery_checks"), [])
        finally:
            ch224k.close()
            load.close()
            reader.shutdown()

    def test_5v_unsupported_but_9v_and_12v_supported(self):
        """A device may draw at higher PDOs even when it does not draw at 5 V."""
        state = SimState(current=0.5, v_target=5.0)
        cfg = _cfg()
        cfg["_manual_requested"] = True
        cfg["probe"].update({"manual_support_readings": 5, "manual_total_readings": 12})
        reader = INA219Reader(cfg, simulate=True, sim_state=state)
        ch224k = CH224KController(PinMap(), cfg, simulate=True, sim_state=state)
        load = LoadController(PinMap(), cfg, simulate=True, sim_state=state)
        try:
            with patch("builtins.input", side_effect=["5", "9", "12", "9", "12", "5"]):
                def step_v(target_v: float) -> bool:
                    state.v_target = {5: 5.0, 9: 9.0, 12: 12.0}[int(target_v)]
                    state.current = 0.0 if int(target_v) == 5 else 0.5
                    return True
                with patch.object(ch224k, "set_voltage", side_effect=step_v, return_value=True):
                    probe = run_probe(cfg, reader, ch224k, load, state)
            self.assertEqual(probe["support_flags"], {"5": False, "9": True, "12": True})
            self.assertEqual(probe["measurement_allocations"], {"9": 6, "12": 6})
            self.assertEqual(probe["quality_reference_voltage"], 9)
            self.assertIsNotNone(probe.get("quality_features"))
        finally:
            ch224k.close()
            load.close()
            reader.shutdown()

    def test_all_voltage_ranges_without_current(self):
        state = SimState(current=0.0, v_target=5.0)
        cfg = _cfg()
        cfg["_manual_requested"] = True
        cfg["probe"].update({"manual_support_readings": 5, "manual_total_readings": 12})
        reader = INA219Reader(cfg, simulate=True, sim_state=state)
        ch224k = CH224KController(PinMap(), cfg, simulate=True, sim_state=state)
        load = LoadController(PinMap(), cfg, simulate=True, sim_state=state)
        try:
            with patch("builtins.input", side_effect=["5", "9", "12", "5"]):
                def step_v(target_v: float) -> bool:
                    state.v_target = float(target_v)
                    state.current = 0.0
                    return True
                with patch.object(ch224k, "set_voltage", side_effect=step_v, return_value=True):
                    probe = run_probe(cfg, reader, ch224k, load, state)
            self.assertTrue(probe["no_current_all_voltages"])
            self.assertEqual(probe["support_flags"], {"5": False, "9": False, "12": False})
            self.assertNotIn("quality_features", probe)
        finally:
            ch224k.close()
            load.close()
            reader.shutdown()


if __name__ == "__main__":
    unittest.main()
