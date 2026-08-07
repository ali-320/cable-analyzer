# cable-analyzer — Dependencies & Run Order (read this before running on the Pi)

This file tells you **what must be done before the code runs on the Raspberry
Pi**, **which file to run first**, and **what output to expect** at every step.
Everything below was verified against the code in this folder.

---

## 1. Before running anything — prerequisites

### 1.1 Build the hardware first
Follow `../WIRING_SCHEMATICS.md` **before** powering anything:
1. For the current phone-inline setup, wire the power path: `CH224K VOUT/GND → INA219 VIN− → INA219 VIN+ → phone VBUS/GND` (common ground). This direction produces negative raw INA219 current, so `measurement.current_direction = "reverse"` is configured to report positive phone charging current.
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
- Confirm INA219 `V` matches your DMM at 5 V. With source VBUS on VIN− and phone VBUS on VIN+, confirm the program reports positive phone current; if not, check `current_direction` and the wiring.

### 1.5 Configure
Edit `config.toml`:
- `hardware.simulate = false` (it is false by default).
- For the current inline-phone wiring, set `[ch224k] control_mode = "manual"` or pass `--manual`. Do **not** use the GPIO control mode until SEL0/SEL1/SEL2, EN, and PWR_OK are physically wired.
- Check `hardware.ina219_address` (0x40), `shunt_ohms` (0.1), `max_expected_amps` (3.0).
- Check the `[gpio]` and `[ch224k]` sections against your actual wiring (SEL truth table **varies by module** — verify with a DMM).
- `measurement.length_m` — set your cable length (optional, enables per-metre grading).
- All verdict thresholds live in `[rules]` — calibration changes go here, never in code.

> **Fastest safe smoke test without hardware:** `python -m src.main --demo` — see §4.3.

---

## 2. Which file to run FIRST

For the current inline-phone wiring, use the manual-mode sequence below. The
old calibration script and full auto sequence belong to the protected legacy
wiring, not the current manual setup.

Run in this exact order (each step must pass before the next):

| Step | Command | Why first |
|---|---|---|
| 0 | `python -m src.main --manual --self-check` | Verifies INA219 and manually verified 5 V; PWR_OK is intentionally skipped |
| 1 | `python -m src.main --manual --mode probe --length 1.0` | Prompts for manual SEL changes and prints INA219 readings/rule verdict |
| 2 | Set `control_mode = "gpio"`, then run `python -m src.main --length 1.0` | Legacy protected wiring only: automatic probe → 5 V charge → verdict |
| 3 (tests) | `python -m unittest discover -s tests -v` | Unit tests — run any time, on any machine |

`src/main.py` is the entry point for the product flow. For the current manual
phone-inline setup, do not run `scripts/calibrate.py` unless a controlled load
is temporarily placed inline. A phone does not provide the known current steps
needed for fixture calibration. Run that script after restoring the protected
inline wiring with a controlled load; its output `data/calibration.json` is
then auto-loaded by `main.py`.

---

## 3. Expected output — step by step

### Step 0 — self-check (`python -m src.main --manual --self-check` for the temporary setup)
```
--- SELF-CHECK ---
  [PASS] INA219 on I2C
  [PASS] Bus voltage ~5 V
  [PASS] CH224K PWR_OK (PD contract; manual mode bypasses this)
  V_reading = 5.003 V  I = 10.3 mA
  RESULT: ALL PASS - ready for calibrate.py
```
A `[FAIL]` line tells you which subsystem to fix (check the `note:` text).
Exit code is 0 only if all three pass — use it in scripts.

### Step 1 — calibrate (`python -m scripts.calibrate`) — legacy wiring only
Run with a **short, known-good reference cable** fitted. This requires the
controlled load and INA219 to be inline; it is not valid for the temporary
phone Y-junction because the phone current bypasses the shunt.
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

### Step 2 — full test (legacy protected wiring)
`python -m src.main --length 1.0` is only for the wiring with INA219 inline,
Q1 phone isolation, controlled load, and CH224K GPIO. Set
`[ch224k] control_mode = "gpio"` before using it. It must not be used with the
temporary Y-junction; the current default `control_mode = "manual"` refuses
this unsafe combination.

### Current phone-inline manual test
Run:
```bash
python -m src.main --manual --mode probe --length 1.0
```
Keep the phone connected. The program samples 5 V for `manual_5v_hold_s`
(default 60 seconds), then asks you to select 9 V and 12 V manually. The 9/12 V
samples are compatibility checks only. If current is below
`session.i_charge_start`, the program records that the phone did not charge at
that voltage, asks you to return to 5 V, and checks whether charging resumes.
Only the 5 V samples (including successful recovery samples) are used for the
quality grade.

Verify every manual voltage with a DMM. The software cannot isolate the phone
or protect it from an incorrect SEL setting because CH224K GPIO is not wired.

**Voltage verification (manual mode):** because the phone is the load, a
lossy cable pulls the rail below the requested PDO (e.g. 9 V target reads
~8.4 V). The program accepts a rail that stepped up from the previous voltage
within `ch224k.manual_verify_tolerance_v` (±1 V default) — it does **not**
require the old tight ±5 % match. Only if the rail fails to step up at all
(e.g. the phone rejected 12 V and VBUS stayed at ~5 V) does the program run
the recovery-at-5 V check.


### Step 2 — full test (legacy protected wiring)
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
| `python -m src.main --manual --mode probe --length 1.0` | Inline-phone test: 5 V quality readings, then 9/12 V compatibility checks |
| `python -m src.main --simulate --mode charge` | Charge monitoring only, synthetic phone |
| `python -m src.main --json` | Print the verdict as JSON |
| `python -m src.main --phone-expected` | Treat a present-but-not-charging phone as an OPEN cable after `open_timeout_s` |
| `python -m scripts.calibrate --simulate` | Test the calibration script without hardware |

---

## 5. Safety reminders (from DEVELOPMENT_PLAN.md §10)
- The current phone-inline setup measures phone current through the INA219, but it has no CH224K GPIO or phone-isolation protection. It can test 5 V charging quality; 9/12 V are compatibility checks only.
- Never rely on the software prompt as protection when changing to 9/12 V; verify with a DMM and confirm the phone is rated for the selected voltage.
- The current manual phone-inline test intentionally uses the phone as the load; begin at 5 V and stop immediately if voltage/current is abnormal.
- The protected GPIO workflow still requires multi-voltage probing with the phone isolated (Q1 off).
- The Pi is only ever fed 5 V from the buck. Keep 9/12 V rails off the Pi.
- If the verdict says `FAULT/ABORT` or `SHORT`, stop — fix the rig.
