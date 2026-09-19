# Wiring — `wiring.md`

How the hardware is wired in the current `--voltage --continuous` build.
No CH224K, no PD-trigger GPIOs, no switches or e-load — the charger's own
voltage powers the phone directly, and the INA219 measures what flows.

## Power path (inline measurement)

```
Charger ──► Cable under test ──► INA219 VIN+ ──[0.1 Ω shunt]── VIN− ──► Phone
```

- The shunt sits in the **VBUS line** (high-side sensing); GND stays common.
- Raw INA219 current reads negative with this direction
  (`current_direction = "reverse"` in `config.toml` corrects it).
- With source VBUS on VIN− and phone on VIN+, `voltage()` reads source-side
  (`bus_voltage_side = "source"`).

## Signal wiring (mermaid)

```mermaid
flowchart LR
    subgraph POWER["Power path"]
        CHG["Charger"] --> CBL["Cable under test"]
        CBL --> VIn["INA219 VIN+"]
        VIn -- "0.1 Ω shunt" --> VOut["VIN−"] --> PH["Phone"]
    end

    subgraph PI0["Raspberry Pi Zero 2 W"]
        P3["Pin 3 · GPIO2 SDA"]
        P5["Pin 5 · GPIO3 SCL"]
        P1["Pin 1 · 3V3"]
        P6["Pin 6 · GND"]
        P21["Pin 40 · GPIO21"]
        PSPI["SPI0: Pin 19 MOSI · Pin 23 SCLK · Pin 24 CE0"]
        P24["Pin 18 · GPIO24 DC"]
        P25["Pin 22 · GPIO25 RST"]
    end

    subgraph INA["INA219 (I²C addr 0x40)"]
        SDA["SDA"]
        SCL["SCL"]
        VCC["VCC"]
        GNDI["GND"]
        A01["A0, A1 → GND"]
    end

    SDA -- "I²C data" --> P3
    SCL -- "I²C clock" --> P5
    VCC -- "3.3 V supply" --> P1
    GNDI -- "common ground" --> P6

    subgraph DISP["ST7735S TFT 128×160"]
        CS["CS/CE0"]
        SDA2["MOSI"]
        SCK["SCLK"]
        DCP["DC"]
        RSTP["RST"]
        VCCD["VCC 3.3 V"]
        GNDD["GND"]
    end

    CS --> PSPI
    SDA2 --> PSPI
    SCK --> PSPI
    DCP --> P24
    RSTP --> P25
    VCCD --> P1
    GNDD --> P6

    subgraph LED["Blue internet LED"]
        AN["Anode (+)"]
        CA["Cathode (−) via resistor to GND"]
    end

    AN -- "GPIO21, on = internet" --> P21
```

## Raspberry Pi Zero 2 W — GPIO map (current build)

| Function | GPIO (BCM) | Header pin | Direction | Notes |
|---|---|---|---|---|
| I²C SDA | GPIO2 | 3 | in/out | INA219 SDA |
| I²C SCL | GPIO3 | 5 | in/out | INA219 SCL |
| 3V3 | — | 1 (or 17) | out | INA219 VCC, display VCC |
| 5V | — | 2 (or 4) | in | Pi power |
| GND | — | 6 (or 9, 14, 20…) | — | common ground |
| SPI MOSI | GPIO10 | 19 | out | display data |
| SPI SCLK | GPIO11 | 23 | out | display clock |
| SPI CE0 | GPIO8 | 24 | out | display chip select |
| Display DC | GPIO24 | 18 | out | data/command |
| Display RST | GPIO25 | 22 | out | display reset |
| Internet LED | GPIO21 | 40 | out | blue LED; ON = internet |

> **Backlight** is hard-wired to 3.3 V on this build (no GPIO control).
> **Unused legacy GPIOs** (GPIO5, 6, 17–18, 22–23, 26–27) belong to the
> removed CH224K/e-load hardware and stay unconnected.
