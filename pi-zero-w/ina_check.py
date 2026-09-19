#!/usr/bin/env python3
"""
Quick INA219 accessibility check using the pi-ina219 library.
Install with:  pip3 install pi-ina219
"""

from ina219 import INA219, DeviceRangeError

INA219_ADDR   = 0x40
SHUNT_OHMS    = 0.10   # typical breakout-board shunt resistance


def main():
    try:
        ina = INA219(SHUNT_OHMS, address=INA219_ADDR)
        ina.configure()
    except OSError:
        print("ERROR – INA219 not responding at 0x40.")
        print("Check: I2C enabled, wiring (SDA/SCL), and address jumper.")
        return

    print(f"\nINA219 @ {hex(INA219_ADDR)} — connected OK\n" + "-" * 40)
    try:
        bus_v = ina.voltage()
        print(f"Bus Voltage : {bus_v:.3f} V")

        try:
            shunt_mv = ina.shunt_voltage()
            print(f"Shunt V     : {shunt_mv:.3f} mV")
        except DeviceRangeError:
            print("Shunt V     : out of range")

        try:
            current_ma = ina.current()
            print(f"Current     : {current_ma:.3f} mA")
        except DeviceRangeError:
            print("Current     : out of range")

        try:
            power_mw = ina.power()
            print(f"Power       : {power_mw:.3f} mW")
        except DeviceRangeError:
            print("Power       : out of range")

        print("-" * 40)
        print("Data read successfully — sensor is accessible.")

    except OSError:
        print("ERROR – Lost communication with INA219 during read.")


if __name__ == "__main__":
    main()