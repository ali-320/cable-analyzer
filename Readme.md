# RADWI Cable-Quality Analyzer

An inline appliance that grades the quality of a USB-C cable by watching how a phone actually charges through it.

```
Charger → [CABLE UNDER TEST] → Analyzer (Raspberry Pi + sensor) → Phone
```

## What it does

While a phone charges, the analyzer measures the voltage and current flowing through the cable and turns those measurements into a simple verdict: a letter grade (A–F), defect tags (e.g. unstable contact, self-heating, intermittent), a confidence value, and the evidence behind the result. Every session is stored locally and uploaded to a cloud database whenever internet is available.

## Hardware

| Component             | Role                                                       |
| --------------------- | ---------------------------------------------------------- |
| Raspberry Pi Zero 2 W | Headless controller                                        |
| INA219 sensor         | Measures voltage/current inline in the charging path (I²C) |
| ST7735S TFT display   | Shows live V / I / P and the WiFi network name             |
| Blue LED (GPIO 21)    | On = internet connected                                    |

## How a test works (workflow)

1. **Boot** — two systemd services start automatically:
   - `wifi-provision` joins the saved WiFi; if none is reachable it opens a hotspot (`PiHotspot`) with a small web page (port 5000) to pick a network. The blue LED lights once internet works.
   - `radwi-continuous` runs the analyzer loop inside a tmux session (`tmux attach -t radwi` to watch it live).
2. **Sample** — while the phone charges through the cable under test, the analyzer reads the sensor about 25 times per second and classifies the session state (no source / no phone / charging / charged / fault).
3. **Grade** — once enough active-charging data is collected (~3,000 readings ≈ 2 minutes), the software computes the electrical features (resistance, voltage stability, heating, interruptions) and a rule engine produces the verdict.
4. **Store & sync** — the session and verdict are saved to a local SQLite database and a CSV file, then uploaded to the remote database (failed uploads are retried automatically when back online).
5. **Repeat** — the loop starts over, continuously testing whatever cable is plugged in.

## Reading a verdict

- **Grade A–F** — based mainly on cable resistance (lower is better), normalized per metre of cable.
- **Tags** — specific defects found: `HIGH_LOSS`, `UNSTABLE_CONTACT`, `SELF_HEATING`, `INTERMITTENT`, `LEAKY`, `NON_LINEAR`.
- **Confidence** — how much the collected data supports the grade (sample quality plus agreement between independent resistance estimates).

## Repository map

| Folder / file                     | Purpose                                                               |
| --------------------------------- | --------------------------------------------------------------------- |
| `src/main.py`                     | Entry point and orchestrator (all run modes)                          |
| `src/hardware/`                   | Sensor driver, GPIO map, wiring control, simulator                    |
| `src/telemetry/`                  | Sampling loop, session state machine, local storage, remote sync      |
| `src/features/` + `src/analysis/` | Feature math and the rule-based verdict engine                        |
| `src/ui/`                         | Terminal output and the TFT display driver                            |
| `scripts/`                        | Calibration, manual sync, systemd service files + installers          |
| `scripts/wireless/`               | WiFi hotspot provisioning service + web app                           |
| `tests/`                          | Unit tests                                                            |
| `docs/`                           | Detailed documentation (development plan, features, wiring, workflow) |

## Running it

```bash
# Production mode runs automatically at boot (radwi-continuous service).
# Watch it live:
tmux attach -t radwi              # Ctrl+B then D to detach

# Manual run (from the project folder, venv active):
python -m src.main --voltage --self-check     # hardware sanity check
python -m src.main --voltage --continuous     # the production loop

# One-time calibration (writes data/calibration.json):
python -m scripts.calibrate
```

Configuration lives in `config.toml`; secrets (API endpoint and token) live in `.env`.
