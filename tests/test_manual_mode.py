"""Tests for the temporary manual-voltage hardware mode."""
import unittest
from unittest.mock import patch

from src.hardware.ch224k import CH224KController
from src.hardware.gpio_map import PinMap
from src.hardware.load_ctrl import LoadController


CFG = {
    "hardware": {"simulate": False},
    "ch224k": {
        "control_mode": "manual",
        "sel_truth": {5: 0, 9: 1, 12: 2},
    },
    "load": {},
}


class TestManualMode(unittest.TestCase):
    def test_manual_ch224k_does_not_import_gpio(self):
        controller = CH224KController(PinMap(), CFG, simulate=False)
        self.assertTrue(controller.manual)
        self.assertIsNone(controller.read_pwr_ok())
        self.assertTrue(controller.set_voltage(9.0))
        self.assertEqual(controller.voltage, 9.0)

    def test_manual_load_does_not_control_gpio(self):
        load = LoadController(PinMap(), CFG, simulate=False)
        self.assertTrue(load.manual)
        load.set_current(1.0)
        load.phone_switch(True)
        self.assertEqual(load.current, 1.0)
        self.assertTrue(load.phone_on)


if __name__ == "__main__":
    unittest.main()
