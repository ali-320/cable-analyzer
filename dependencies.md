# cable-analyzer — Dependencies & Run Order (read this before running on the Pi)

This file tells you **what must be done before the code runs on the Raspberry
Pi**, **which file to run first**, and **what output to expect** at every step.
Everything below was verified against the code in this folder.

---

## 1. Before running anything — prerequisites

### 1.1 Build the hardware first
Follow `../WIRING_SCHEMATICS.md` **before** powering anything:
1. Wire the power path: `CH224K VOUT → F1 5A PTC → INA219 VIN+ → VIN− → NODE_A → Q1/Q2 → J2`.
2. Pi 5 V comes **only** from the buck (`U2`) tapped from CH224K VOUT **before** the INA219 shunt — never from a 9/12 V rail.
3. Wire I²C: INA219 `SDA→GPIO2`, `SCL→GPIO3`, `VCC→3V3`, `GND→GND`, `A0/A1→GND`.
4. Wire CH224K: `CC1/CC2/VBUS/GND` from J1; `SEL0/1/2 → GPIO22/23/24` (with 10 kΩ pulldowns → safe 5 V default), `PWR_OK → GPIO17`, `EN → GPIO25`.
5. Wire `Q1 → GPIO26`, `Q2 → GPIO27`, e-load PWM `→ GPIO18`, LED `→ GPIO5`.
6. Fit `C4 ≥ 1000 µF` on the buck input (the Pi must survive the VBUS dip during 5→9→12 V re-negotiation).
7. Work through the **Wiring Checklist** in `WIRING_SCHEMATICS.md` §7 with a DMM before connecting a phone.

### 1.2 Raspberry Pi OS setup
```bash
# 1. Flash Raspberry Pi OS Lite (Bookworm 64-bit) with Imager; enable SSH + Wi-Fi.
# 2. Boot, log in over SSH, then:
sudo apt update && sudo apt full-upgrade -y
sudo raspi-config          # -> Interface Options -> I2C -> Enable -> Reboot
sudo apt install -y python3-venv python3-smbus i2c-tools python3-rpi.gpio
#    ^ python3-rpi.gpio (RPi.GPIO) is an apt package, NOT pip-installable.
#    On very new images use python3-lgpio instead and keep the import name RPi.GPIO.
```

### 1.3 Copy the project & install Python deps
```bash
# copy the cable-analyzer/ folder to the Pi (scp, git, USB stick), then:
cd ~/cable-analyzer
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt      # installs pi-ina219 only (tomllib is stdlib on 3.11+)
```

### 1.4 Verify hardware before software
```bash
i2cdetect -y 1        # MUST show 0x40  (the INA219). CH224K is NOT on I2C.
```
- Set CH224K straps to 5 V by hand (all SEL low) and confirm `VOUT ≈ 5 V` on a DMM.
- Confirm INA219 `V` matches your DMM at 5 V.

### 1.5 Configure
Edit `config.toml`:
- `hardware.simulate = false` (it is false by default).
- Check `hardware.ina219_address` (0x40), `shunt_ohms` (0.1), `max_expected_amps` (3.0).
- Check the `[gpio]` and `[ch224k]` sections against your actual wiring (SEL truth table **varies by module** — verify with a DMM).
- `measurement.length_m` — set your cable length (optional, enables per-metre grading).
- All verdict thresholds live in `[rules]` — calibration changes go here, never in code.

> **Fastest safe smoke test without hardware:** `python -m src.main --demo` — see §4.3.

---

## 2. Which file to run FIRST

Run in this exact order (each step must pass before the next):

| Step | Command | Why first |
|---|---|---|
| 0 | `python -m src.main --self-check` | Verifies INA219, 5 V rail, and CH224K PD contract **before** any load is applied |
| 1 | `python -m scripts.calibrate` | Measures the fixture baseline `R_fixture` with a short reference cable — required for `R_cable = R_loop − R_fixture` |
| 2 | `python -m src.main --length 1.0` | Full test: probe (5/9/12 V) → charge at 5 V → verdict |
| 3 (tests) | `python -m unittest discover -s tests -v` | 35 unit tests — run any time, on any machine |

`src/main.py` is the entry point for the product flow. `scripts/calibrate.py`
must be run at least once per rig (and after any wiring change) because its
output `data/calibration.json` is auto-loaded by `main.py`.

---

## 3. Expected output — step by step

### Step 0 — self-check (`python -m src.main --self-check`)
```
--- SELF-CHECK ---
  [PASS] INA219 on I2C
  [PASS] Bus voltage ~5 V
  [PASS] CH224K PWR_OK (PD contract)
  V_reading = 5.003 V  I = 10.3 mA
  RESULT: ALL PASS - ready for calibrate.py
```
A `[FAIL]` line tells you which subsystem to fix (check the `note:` text).
Exit code is 0 only if all three pass — use it in scripts.

### Step 1 — calibrate (`python -m scripts.calibrate`)
Run with a **short, known-good reference cable** fitted:
```
=== FIXTURE CALIBRATION (use a SHORT known-good reference cable) ===
  I=0.50 A   R_loop(fixture) =   259.8 mΩ   V_load=4.858 V
  I=1.00 A   R_loop(fixture) =   260.1 mΩ   V_load=4.728 V
  I=1.50 A   R_loop(fixture) =   260.1 mΩ   V_load=4.600 V
  I=2.00 A   R_loop(fixture) =   260.2 mΩ   V_load=4.469 V

  R_fixture mean = 260.1 mΩ  ->  saved to data/calibration.json
  main.py picks this up automatically (R_cable = R_loop - R_fixture).
```
On real hardware with a short reference cable `R_fixture` is typically
50–150 mΩ (fixture traces + connectors + CH224K path). The sim example above
shows ~260 mΩ because the simulator models a single "cable" — in real use the
reference cable contributes almost nothing and the value is the fixture only.
Cross-check `V_load` against your DMM at each current.

### Step 2 — full test (`python -m src.main --length 1.0`)
Expected output shape (simulated demo; real numbers differ):
```
--- PROBE (multi-voltage cable characterization) ---
  V_target=  5.0 V  I=0.50 A  V_load=4.858 V  R_loop=  260.1 mΩ  R_cable=  210.1 mΩ
  V_target=  5.0 V  I=2.00 A  V_load=4.441 V  R_loop=  271.9 mΩ  R_cable=  221.9 mΩ
  ... (9 V and 12 V rows follow; a negotiation failure prints:
  !! PD negotiation FAILED at: [9, 12] V  (cable blocks CC/PD signaling) )
  heating dR/dt over 60 s hold: +95.92 mΩ/min

  t=   1.0s  V= 5.001 V  I= 0.005 A  P=  0.02 W  state=IDLE
  t=  10.0s  V= 4.298 V  I= 1.189 A  P=  5.11 W  state=CHARGING
  ... (streams until I -> 0 for the debounce period, then) ...

--- VERDICT ---
  verdict   : Grade D: Poor
  grade     : D
  confidence: 0.951
  tags      : HIGH_LOSS, SELF_HEATING
  evidence  : R_mean=368 mΩ (normalized to 1.0 m)
  evidence  : V_min=4.474 V (target 5.0 V)
  evidence  : eta=90.4%
  evidence  : dR/dt=+34.07 mΩ/min (self-heating)
  note      : single measurement point; loop R includes connectors and the CH224K path (baseline subtracted)
  note      : absolute R biased by charger tolerance (+/-5%); same-current differential calibration mitigates
```
Also written automatically:
- `data/sessions.db` — SQLite history (sessions, samples, verdict JSON).
- `data/processed/session_<id>.csv` — the raw time series.
- Verdict JSON on screen with `--json`.

Grade scale (per metre, after baseline subtraction): **A** <150 mΩ ·
**B** 150–250 · **C** 250–300 · **D** 300–500 · **F** ≥500 mΩ.

### Step 3 — unit tests (`python -m unittest discover -s tests -v`)
```
Ran 35 tests in 0.05s
OK
```

---

## 4. Other entry points

| Command | What it does |
|---|---|
| `python -m src.main --demo` | Simulated end-to-end run, **no hardware needed** (fastest way to see the pipeline work) |
| `python -m src.main --simulate --mode probe` | Probe only, synthetic hardware |
| `python -m src.main --simulate --mode charge` | Charge monitoring only, synthetic phone |
| `python -m src.main --json` | Print the verdict as JSON |
| `python -m src.main --phone-expected` | Treat a present-but-not-charging phone as an OPEN cable after `open_timeout_s` |
| `python -m scripts.calibrate --simulate` | Test the calibration script without hardware |

---

## 5. Safety reminders (from DEVELOPMENT_PLAN.md §10)
- First powered tests use the controlled load, **never a phone**.
- Multi-voltage probing (9/12 V) only with the phone isolated (Q1 off) — the
  software enforces this (`run_probe` disconnects the phone first).
- The Pi is only ever fed 5 V from the buck. Keep 9/12 V rails off the Pi.
- If the verdict says `FAULT/ABORT` or `SHORT`, stop — fix the rig.
