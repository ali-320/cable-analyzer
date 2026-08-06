"""scripts/calibrate.py — DEVELOPMENT_PLAN.md §8.1.

Measures the fixture baseline resistance (fixture traces + connectors +
CH224K internal path) using a SHORT, KNOWN-GOOD reference cable, at each
probe current. The result is saved to data/calibration.json and is picked
up automatically by src/main.py (R_cable = R_loop - R_fixture).

Cross-check with a DMM/ammeter while this runs and record the values below
the printed table — the numbers are only trustworthy if the rig is stable.

Usage:
    python -m scripts.calibrate --simulate   # synthetic run (test)
    python -m scripts.calibrate              # real hardware, reference cable fitted
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from src.features.metrics import compute_features
from src.hardware.ch224k import CH224KController
from src.hardware.gpio_map import PinMap
from src.hardware.ina219_reader import INA219Reader
from src.hardware.load_ctrl import LoadController
from src.telemetry.sampler import Sampler
from src.ui.cli import force_utf8_stdout


def main() -> int:
    force_utf8_stdout()
    ap = argparse.ArgumentParser(description="Calibrate fixture baseline resistance (reference cable fitted)")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--simulate", action="store_true")
    args = ap.parse_args()

    with open(args.config, "rb") as fh:
        cfg = tomllib.load(fh)
    if args.simulate:
        cfg["hardware"]["simulate"] = True

    print("=== FIXTURE CALIBRATION (use a SHORT known-good reference cable) ===")
    sim_state = None
    if cfg["hardware"].get("simulate"):
        sim_cfg = cfg.get("sim", {})
        from src.hardware.sim import SimState

        sim_state = SimState(
            r_cable_ohm=float(sim_cfg.get("r_cable_ohm", 0.15)),
            r_fixture_ohm=float(sim_cfg.get("r_fixture_ohm", 0.05)),
            v_noise=float(sim_cfg.get("v_noise", 0.004)),
            i_noise=float(sim_cfg.get("i_noise", 0.008)),
        )
    reader = INA219Reader(cfg, simulate=args.simulate, sim_state=sim_state)
    pins = PinMap.from_config(cfg)
    ch224k = CH224KController(pins, cfg, simulate=args.simulate, sim_state=sim_state)
    load = LoadController(pins, cfg, simulate=args.simulate, sim_state=sim_state)
    rate = float(cfg["hardware"].get("sample_rate_hz", 25.0))
    simulate = cfg["hardware"].get("simulate", False)

    steps = [float(s) for s in cfg.get("probe", {}).get("current_steps", [0.5, 1.0, 1.5, 2.0])]
    hold = float(cfg.get("probe", {}).get("step_hold_s", 5.0))
    drop = float(cfg.get("probe", {}).get("transient_drop_s", 1.0))
    v_target = float(cfg.get("measurement", {}).get("v_target_5v", 5.0))
    i_min = float(cfg.get("measurement", {}).get("i_min_compute", 0.1))

    load.phone_switch(False)
    try:
        ch224k.set_voltage(v_target)
        time.sleep(float(cfg.get("ch224k", {}).get("renegotiate_wait_s", 1.5)))
        v0, _, _, ok = reader.read_sample(time.monotonic())
        if not ok or abs(v0 - v_target) > 0.25 * v_target:
            print(f"!! expected ~{v_target} V, read {v0:.2f} V. Fix the rig before calibrating.")
            return 1

        rows = []
        for target_i in steps:
            if target_i > float(cfg.get("load", {}).get("max_probe_amps", 2.5)):
                continue
            load.set_current(target_i, enable=True)
            samples = Sampler(reader, rate, simulate).run(hold, state="PROBE")
            load.set_current(0.0, enable=False)
            trimmed = [s for s in samples if s.t >= drop and s.valid]
            feat = compute_features(trimmed, v_target=v_target, r_fixture=0.0, i_min=i_min)
            if feat is None:
                print(f"!! not enough valid samples at {target_i} A - check wiring")
                continue
            r_loop_mohm = feat["r_mean"] * 1000.0
            rows.append({"i_a": target_i, "r_loop_mohm": round(r_loop_mohm, 1), "n": feat["n_busy"]})
            print(f"  I={target_i:>4.2f} A   R_loop(fixture) = {r_loop_mohm:>7.1f} mΩ   V_load={feat['V_min']:.3f} V")

        if not rows:
            print("!! calibration produced no readings")
            return 1

        mean_mohm = sum(r["r_loop_mohm"] for r in rows) / len(rows)
        cal = {
            "date": datetime.now(timezone.utc).isoformat(),
            "method": "reference-cable probe steps at 5 V",
            "steps": rows,
            "r_fixture_mean_mohm": round(mean_mohm, 1),
            "note": "verify with DMM: V_load at each current should match your multimeter",
        }
        out = Path(cfg.get("paths", {}).get("data_dir", "data")) / "calibration.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(cal, indent=2))
        print(f"\n  R_fixture mean = {mean_mohm:.1f} mΩ  ->  saved to {out}")
        print("  main.py picks this up automatically (R_cable = R_loop - R_fixture).")
        return 0
    finally:
        load.close()
        ch224k.close()
        reader.shutdown()


if __name__ == "__main__":
    sys.exit(main())
