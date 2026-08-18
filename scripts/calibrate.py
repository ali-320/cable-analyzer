"""Fixture/reference calibration for the cable analyzer.

Two calibration paths are supported:

* ``control_mode = "gpio"``: the protected setup uses the controlled e-load
  and known current steps.
* ``control_mode = "manual"``: the current phone-inline setup uses a short,
  known-good reference cable and the phone's natural charging current. This
  produces a differential reference baseline (fixture + reference-cable path),
  which is the correct baseline to subtract from later tests made with the
  same setup.

Usage::

    python -m scripts.calibrate --simulate
    python -m scripts.calibrate

For manual mode, connect the phone through the short known-good/reference
cable, set CH224K to 5 V, and keep the phone actively charging. The script
cannot command a controlled current in this architecture, so it must not claim
that the phone's current equals one of the configured e-load steps.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from src.features.metrics import compute_features, voltage_class_center
from src.hardware.ch224k import CH224KController
from src.hardware.gpio_map import PinMap
from src.hardware.ina219_reader import INA219Reader
from src.hardware.load_ctrl import LoadController
from src.hardware.sim import SimState
from src.main import _manual_voltage_confirmation
from src.telemetry.sampler import Sampler
from src.ui.cli import force_utf8_stdout


def _save_calibration(cfg: dict, *, method: str, rows: list[dict],
                      mean_mohm: float, note: str, **extra) -> Path:
    """Write the common calibration format consumed by ``src.main``."""
    cal = {
        "date": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "steps": rows,
        "r_fixture_mean_mohm": round(mean_mohm, 1),
        "r_fixture_ohm": round(mean_mohm / 1000.0, 6),
        "note": note,
        **extra,
    }
    out = Path(cfg.get("paths", {}).get("data_dir", "data")) / "calibration.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cal, indent=2))
    return out


def _estimate_confidence_floor(cfg: dict, feature_sets: list[dict]) -> tuple[float, float]:
    """Estimate the confidence resistance floor from one calibration run.

    ``r_std`` is the sample-to-sample resistance noise estimate. When several
    controlled current steps are available, their variances and means are
    pooled before applying the configured sigma multiplier.
    """
    sets = [feature for feature in feature_sets if feature]
    if not sets:
        raise ValueError("at least one feature set is required")
    counts = [max(1, int(feature.get("n_busy", 0))) for feature in sets]
    total = sum(counts)
    r_mean = sum(n * feature["r_mean"] for n, feature in zip(counts, sets)) / total
    variance = sum(
        max(0, n - 1) * feature["r_std"] ** 2
        + n * (feature["r_mean"] - r_mean) ** 2
        for n, feature in zip(counts, sets)
    ) / max(total - 1, 1)
    noise_sigma = variance ** 0.5
    multiplier = float(cfg["calibration"]["confidence_floor_sigma_multiplier"])
    return multiplier * noise_sigma, noise_sigma


def _calibration_report_values(cfg: dict, feature_sets: list[dict]) -> tuple[float, float, float]:
    """Return estimated floor, measured noise sigma, and configured floor."""
    estimated, noise_sigma = _estimate_confidence_floor(cfg, feature_sets)
    configured = float(cfg["rules"]["confidence_resistance_floor_ohm"])
    return estimated, noise_sigma, configured

def _manual_passive_calibration(
    cfg: dict,
    reader: INA219Reader,
    ch224k: CH224KController,
    load: LoadController,
    sim_state: SimState | None,
    rate: float,
    simulate: bool,
    v_target: float,
    i_min: float,
    voltage_mode: bool = False,
) -> tuple[int, float | None, Path | None]:
    """Calibrate from the phone's natural current in manual inline mode.

    The returned baseline is ``R_loop`` measured with the known-good reference
    cable. It intentionally includes the reference cable, connectors, wiring,
    and CH224K path, because the same complete path is present during later
    cable tests.
    """
    pr = cfg.get("probe", {})
    ch_cfg = cfg.get("ch224k", {})
    vcfg = cfg.get("voltage", {})
    manual_hold = float(pr.get("manual_5v_hold_s", pr.get("step_hold_s", 5.0)))
    wait_s = float(ch_cfg.get("renegotiate_wait_s", 1.5))
    class_width = float(vcfg.get("class_width_v", 1.0))
    v_present_min = float(
        cfg.get("session", {}).get("v_present_min_v", 3.0)
    ) if voltage_mode else 0.0

    print("\n--- MANUAL PHONE-INLINE REFERENCE CALIBRATION ---")
    print("  Connect the phone through the SHORT, known-good/reference cable.")
    print("  The phone must be actively charging; its current is not controlled by the Pi.")
    if simulate:
        print(f"  simulation: assuming the reference phone is charging at {v_target:g} V")
    elif not voltage_mode:
        _manual_voltage_confirmation(v_target)
    else:
        print("  voltage mode: no CH224K; the charger's present voltage is used directly.")

    # In simulation, model an actively charging phone. On real hardware the
    # connected phone supplies the current naturally.
    if simulate and sim_state is not None:
        sim_state.phone_on = True
        sim_state.current = float(cfg.get("sim", {}).get("phone_current_a", 1.2))

    if not voltage_mode:
        if not ch224k.set_voltage(v_target):
            print(f"!! CH224K could not select {v_target:g} V.")
            return 1, None, None
        time.sleep(wait_s)

    samples = Sampler(reader, rate, simulate).run(manual_hold, state="PROBE")
    valid = [s for s in samples if s.valid]
    if voltage_mode:
        # The charger's own voltage defines the reference class center.
        v_mean_all = sum(s.voltage for s in valid) / len(valid) if valid else 0.0
        if valid:
            v_target = voltage_class_center(v_mean_all, class_width)
    feat = compute_features(
        valid,
        v_target=v_target,
        r_fixture=0.0,
        i_min=i_min,
    )
    if feat is None:
        print(
            "!! calibration produced no trustworthy resistance data. "
            f"The phone must draw at least {i_min:.2f} A while charging."
        )
        print("   Check that the phone is connected, not fully charged, and the reference cable is good.")
        return 1, None, None

    busy = [s for s in valid if s.current >= i_min]
    if not busy:
        print(
            "!! calibration produced no busy charging samples above "
            f"{i_min:.3f} A."
        )
        return 1, None, None
    mean_i = feat["mean_I"]
    mean_v = sum(s.voltage for s in busy) / len(busy)
    if voltage_mode:
        min_compliance_v = v_present_min
        compliance_label = "source presence"
    else:
        min_compliance_v = float(
            cfg.get("measurement", {}).get("v_min_compliance_5v", 4.75)
        )
        compliance_label = "5 V rail compliance"
    if mean_i < i_min:
        print(
            "!! calibration produced insufficient charging current: "
            f"mean current was {mean_i:.3f} A, minimum is {i_min:.3f} A."
        )
        return 1, None, None
    if mean_v < min_compliance_v:
        print(
            "!! calibration rejected: measured rail is below the "
            f"{compliance_label} limit ({mean_v:.3f} V < {min_compliance_v:.3f} V)."
        )
        print("   Fix the charger, reference cable, or wiring before saving a baseline.")
        return 1, None, None
    r_loop_mohm = feat["r_mean"] * 1000.0
    estimated_floor, noise_sigma, configured_floor = _calibration_report_values(cfg, [feat])
    row = {
        "mode": "manual_phone_load",
        "i_mean_a": round(mean_i, 4),
        "v_mean_v": round(mean_v, 4),
        "r_loop_mohm": round(r_loop_mohm, 1),
        "n": feat["n_busy"],
        "i_min_a": round(min(s.current for s in busy), 4),
        "i_max_a": round(max(s.current for s in busy), 4),
    }
    print(
        f"  passive phone load: I_mean={mean_i:.3f} A   "
        f"R_loop(reference baseline)={r_loop_mohm:.1f} mOhm   "
        f"V_mean={mean_v:.3f} V   n={feat['n_busy']}"
    )

    out = _save_calibration(
        cfg,
        method=(
            "known-good reference cable with passive phone load at the charger's present voltage"
            if voltage_mode
            else "known-good reference cable with passive phone load at 5 V"
        ),
        rows=[row],
        mean_mohm=r_loop_mohm,
        note=(
            "Differential baseline: includes the known-good reference cable, "
            "connectors, wiring, INA219 path, and CH224K path. The phone supplied "
            "an uncontrolled natural charging current; no e-load current steps were used."
        ),
        reference_cable="short known-good cable",
        reference_current_a=round(mean_i, 4),
        reference_current_min_a=round(min(s.current for s in busy), 4),
        reference_current_max_a=round(max(s.current for s in busy), 4),
        reference_voltage_v=round(mean_v, 4),
        resistance_noise_sigma_ohm=round(noise_sigma, 6),
        confidence_resistance_floor_ohm=round(estimated_floor, 6),
        configured_confidence_resistance_floor_ohm=round(configured_floor, 6),
    )
    print(f"\n  R_fixture/reference baseline = {r_loop_mohm:.1f} mOhm  ->  saved to {out}")
    print(f"  resistance noise sigma = {noise_sigma:.6f} Ohm")
    print(f"  confidence_resistance_floor_ohm = {estimated_floor:.6f} Ohm")
    print(f"  configured floor currently used by rules = {configured_floor:.6f} Ohm")
    print("  Set [rules].confidence_resistance_floor_ohm to the calibrated value if desired.")
    print("  main.py will subtract this baseline from later cable measurements automatically.")
    return 0, r_loop_mohm, out


def _controlled_load_calibration(
    cfg: dict,
    reader: INA219Reader,
    ch224k: CH224KController,
    load: LoadController,
    rate: float,
    simulate: bool,
    v_target: float,
    i_min: float,
) -> tuple[int, float | None, Path | None]:
    """Preserve the original protected-wiring/current-step calibration path."""
    steps = [float(s) for s in cfg.get("probe", {}).get("current_steps", [0.5, 1.0, 1.5, 2.0])]
    pr = cfg.get("probe", {})
    hold = float(pr.get("step_hold_s", 5.0))
    drop = float(pr.get("transient_drop_s", 1.0))

    load.phone_switch(False)
    ch224k.set_voltage(v_target)
    time.sleep(float(cfg.get("ch224k", {}).get("renegotiate_wait_s", 1.5)))
    v0, _, _, ok = reader.read_sample(time.monotonic())
    if not ok or abs(v0 - v_target) > 0.25 * v_target:
        print(f"!! expected ~{v_target} V, read {v0:.2f} V. Fix the rig before calibrating.")
        return 1, None, None

    rows = []
    feature_sets = []
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
        feature_sets.append(feat)
        rows.append({"i_a": target_i, "r_loop_mohm": round(r_loop_mohm, 1), "n": feat["n_busy"]})
        print(f"  I={target_i:>4.2f} A   R_loop(reference) = {r_loop_mohm:>7.1f} mOhm   V_load={feat['V_min']:.3f} V")

    if not rows:
        print("!! calibration produced no readings")
        return 1, None, None

    mean_mohm = sum(r["r_loop_mohm"] for r in rows) / len(rows)
    estimated_floor, noise_sigma, configured_floor = _calibration_report_values(cfg, feature_sets)
    out = _save_calibration(
        cfg,
        method="known-good reference cable with controlled e-load steps at 5 V",
        rows=rows,
        mean_mohm=mean_mohm,
        note="verify V_load at each current with a DMM; baseline includes the fitted reference cable and fixture path",
        resistance_noise_sigma_ohm=round(noise_sigma, 6),
        confidence_resistance_floor_ohm=round(estimated_floor, 6),
        configured_confidence_resistance_floor_ohm=round(configured_floor, 6),
    )
    print(f"\n  R_fixture/reference baseline = {mean_mohm:.1f} mOhm  ->  saved to {out}")
    print(f"  resistance noise sigma = {noise_sigma:.6f} Ohm")
    print(f"  confidence_resistance_floor_ohm = {estimated_floor:.6f} Ohm")
    print(f"  configured floor currently used by rules = {configured_floor:.6f} Ohm")
    print("  Set [rules].confidence_resistance_floor_ohm to the calibrated value if desired.")
    print("  main.py will subtract this baseline from later cable measurements automatically.")
    return 0, mean_mohm, out


def main() -> int:
    force_utf8_stdout()
    ap = argparse.ArgumentParser(description="Calibrate reference baseline resistance")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--simulate", action="store_true", help="use synthetic hardware")
    ap.add_argument("--voltage", action="store_true", help="passive mode: no CH224K; use the charger's present voltage directly")
    args = ap.parse_args()

    with open(args.config, "rb") as fh:
        cfg = tomllib.load(fh)
    if args.simulate:
        cfg["hardware"]["simulate"] = True
    if args.voltage:
        cfg["ch224k"]["control_mode"] = "voltage"
        cfg["session"]["v_present_min_v"] = float(
            cfg.get("voltage", {}).get("v_present_min_v", 3.0)
        )

    print("=== REFERENCE CALIBRATION (use a SHORT known-good reference cable) ===")
    sim_state = None
    simulate = bool(cfg["hardware"].get("simulate", False))
    if simulate:
        sim_cfg = cfg.get("sim", {})
        # Calibration simulation represents the fitted short reference cable,
        # not the deliberately lossy cable used by the normal demo scenario.
        sim_state = SimState(
            r_cable_ohm=float(
                sim_cfg.get("calibration_reference_cable_ohm", 0.02)
            ),
            r_fixture_ohm=float(sim_cfg.get("r_fixture_ohm", 0.05)),
            v_noise=float(sim_cfg.get("v_noise", 0.004)),
            i_noise=float(sim_cfg.get("i_noise", 0.008)),
        )

    # Use the effective configuration value, not only --simulate. This fixes
    # config.toml-driven simulation and keeps all three drivers consistent.
    reader = INA219Reader(cfg, simulate=simulate, sim_state=sim_state)
    pins = PinMap.from_config(cfg)
    ch224k = CH224KController(pins, cfg, simulate=simulate, sim_state=sim_state)
    load = LoadController(pins, cfg, simulate=simulate, sim_state=sim_state)
    rate = float(cfg["hardware"].get("sample_rate_hz", 25.0))
    v_target = float(cfg.get("measurement", {}).get("v_target_5v", 5.0))
    i_min = float(cfg.get("measurement", {}).get("i_min_compute", 0.1))
    control_mode = str(cfg.get("ch224k", {}).get("control_mode", "gpio")).lower()
    manual_mode = control_mode == "manual"
    voltage_mode = control_mode == "voltage"

    try:
        if manual_mode or voltage_mode:
            status, _, _ = _manual_passive_calibration(
                cfg, reader, ch224k, load, sim_state, rate, simulate, v_target, i_min,
                voltage_mode=voltage_mode,
            )
        else:
            status, _, _ = _controlled_load_calibration(
                cfg, reader, ch224k, load, rate, simulate, v_target, i_min
            )
        return status
    finally:
        load.close()
        ch224k.close()
        reader.shutdown()


if __name__ == "__main__":
    sys.exit(main())
