"""Orchestrator — DEVELOPMENT_PLAN.md §4 test protocol + §6 verdict.

Run from the cable-analyzer/ folder (Python 3.11+):

    python -m src.main --help
    python -m src.main --self-check            # hardware sanity (run first)
    python -m src.main --length 1.0            # full: probe + charge -> verdict
    python -m src.main --demo                  # no hardware: simulated good-ish cable
    python -m src.main --simulate --mode probe # probe only, simulated

Modes: auto (probe + charge), probe, charge. The phone interlock is
enforced: probe voltages above 5 V are refused while the phone path (Q1)
is enabled. 
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
from pathlib import Path

from src.analysis.rules import evaluate as rule_verdict
from src.features.metrics import compute_features
from src.hardware.ch224k import CH224KController
from src.hardware.gpio_map import PinMap
from src.hardware.ina219_reader import INA219Reader
from src.hardware.load_ctrl import LoadController
from src.hardware.sim import SimState, phone_charge_curve
from src.telemetry.models import Sample, SessionMeta
from src.telemetry.sampler import Sampler
from src.telemetry.session import SessionTracker
from src.telemetry.storage import Storage
from src.ui import cli


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="RADWI cable-quality analyzer (rules)")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--simulate", action="store_true", help="use synthetic hardware")
    ap.add_argument("--mode", choices=["auto", "probe", "charge"], default="auto")
    ap.add_argument("--self-check", action="store_true", help="hardware sanity check only")
    ap.add_argument("--duration", type=float, default=None, help="charge-mode timeout (s)")
    ap.add_argument("--length", type=float, default=None, help="cable length (m) for normalization")
    ap.add_argument("--phone-expected", action="store_true", help="a phone is attached (enables OPEN detection)")
    ap.add_argument("--demo", action="store_true", help="short simulated run with no hardware")
    ap.add_argument("--json", action="store_true", help="print verdict as JSON")
    return ap.parse_args()


def load_config(path: str, simulate: bool, demo: bool) -> dict:
    with open(path, "rb") as fh:
        cfg = tomllib.load(fh)
    if simulate or demo:
        cfg["hardware"]["simulate"] = True
        # the simulator bakes its own fixture resistance into V_load; keep the
        # R_cable = R_loop - R_fixture subtraction consistent in demo mode
        sim_fixture = float(cfg.get("sim", {}).get("r_fixture_ohm", 0.05))
        cfg["measurement"]["r_fixture_ohm"] = sim_fixture
    return cfg


def build_hardware(cfg: dict, simulate: bool):
    sim_state = None
    if simulate or cfg["hardware"].get("simulate"):
        sim_cfg = cfg.get("sim", {})
        sim_state = SimState(
            r_cable_ohm=float(sim_cfg.get("r_cable_ohm", 0.15)),
            r_fixture_ohm=float(sim_cfg.get("r_fixture_ohm", 0.05)),
            v_noise=float(sim_cfg.get("v_noise", 0.004)),
            i_noise=float(sim_cfg.get("i_noise", 0.008)),
            heating_k=float(sim_cfg.get("heating_k", 0.0)),
            pd_blocked_above=sim_cfg.get("pd_blocked_above"),
            short=bool(sim_cfg.get("short", False)),
            open_circuit=bool(sim_cfg.get("open_circuit", False)),
            intermittent=bool(sim_cfg.get("intermittent", False)),
        )
    pins = PinMap.from_config(cfg)
    reader = INA219Reader(cfg, simulate=simulate, sim_state=sim_state)
    ch224k = CH224KController(pins, cfg, simulate=simulate, sim_state=sim_state)
    load = LoadController(pins, cfg, simulate=simulate, sim_state=sim_state)
    return reader, ch224k, load, sim_state


def run_self_check(cfg: dict, reader: INA219Reader, ch224k: CH224KController) -> dict:
    results: dict = {"ina219": False, "voltage": False, "pwr_ok": False}
    if not reader.ok and not reader.simulate:
        results["note"] = "INA219 not found on I2C - check wiring (i2cdetect -y 1 should show 0x40)"
        return results
    v, i, p, ok = reader.read_sample(time.monotonic())
    results["ina219"] = ok  # the chip only passes if the I2C read actually worked
    results["v_reading"] = v
    results["i_reading"] = i
    v_target = float(cfg.get("measurement", {}).get("v_target_5v", 5.0))
    results["voltage"] = ok and abs(v - v_target) <= 0.25 * v_target
    results["pwr_ok"] = bool(ch224k.read_pwr_ok())
    if not results["voltage"]:
        results["note"] = f"expected ~{v_target} V but read {v:.2f} V - check charger/cable/CH224K"
    return results


def run_probe(cfg: dict, reader, ch224k, load, sim_state) -> dict:
    """DEVELOPMENT_PLAN.md §4.2 — phone isolated (Q1 off), multi-voltage steps."""
    pr = cfg.get("probe", {})
    voltages = [int(v) for v in pr.get("voltages", [5, 9, 12])]
    steps = [float(s) for s in pr.get("current_steps", [0.5, 1.0, 1.5, 2.0])]
    hold = float(pr.get("step_hold_s", 5.0))
    drop = float(pr.get("transient_drop_s", 1.0))
    heat_hold = float(pr.get("heat_hold_s", 60.0))
    heat_idx = int(pr.get("heat_step_index", -1))
    rate = float(cfg["hardware"].get("sample_rate_hz", 25.0))
    meas = cfg.get("measurement", {})
    r_fixture = float(meas.get("r_fixture_ohm", 0.0))
    simulate = cfg["hardware"].get("simulate", False)

    load.phone_switch(False)  # interlock: phone isolated during probing
    results: dict = {"steps": [], "pd_blocked": [], "r_fixture": r_fixture, "heat_hold_s": heat_hold}

    for v in voltages:
        if v != 5 and load.phone_on:
            raise RuntimeError("phone interlock: cannot probe >5 V while phone connected")
        if not ch224k.set_voltage(float(v)):
            results["pd_blocked"].append(v)
            continue
        # verify the negotiated rail arrived
        v_check, _, _, ok = reader.read_sample(time.monotonic())
        if not ok or abs(v_check - v) > float(cfg.get("ch224k", {}).get("verify_tolerance", 0.05)) * v:
            results["pd_blocked"].append(v)
            continue
        for idx, target_i in enumerate(steps):
            if target_i > float(cfg.get("load", {}).get("max_probe_amps", 2.5)):
                continue  # software current guard
            load.set_current(target_i, enable=True)
            samples = Sampler(reader, rate, simulate).run(hold, state="PROBE")
            load.set_current(0.0, enable=False)
            trimmed = [s for s in samples if s.t >= drop and s.valid]
            feat = compute_features(
                trimmed, v_target=float(v), r_fixture=0.0, i_min=float(meas.get("i_min_compute", 0.1))
            )
            if feat is None:
                continue
            r_loop = feat["r_mean"] * 1000.0
            r_cable = max(0.0, r_loop - r_fixture * 1000.0)
            results["steps"].append(
                {
                    "v_target": v,
                    "i": target_i,
                    "v_load": round(feat["V_min"], 3),
                    "r_loop_mohm": round(r_loop, 1),
                    "r_cable_mohm": round(r_cable, 1),
                }
            )
            # heating hold: only ONCE, on the last voltage, at the designated
            # step (plan §4.2: hold the longest step 60 s to fit dR/dt)
            if v == voltages[-1] and idx == heat_idx % len(steps) and heat_hold > 0:
                load.set_current(target_i, enable=True)
                heat_samples = Sampler(reader, rate, simulate).run(heat_hold, state="PROBE")
                load.set_current(0.0, enable=False)
                hf = compute_features(
                    heat_samples, v_target=float(v), r_fixture=0.0,
                    i_min=float(meas.get("i_min_compute", 0.1)),
                )
                if hf is not None:
                    results["dR_dt_mOhm_per_min"] = round(hf["dR_dt_mOhm_per_min"], 2)
                    results["r_cable_heat_end_mohm"] = round(hf["r_mean"] * 1000.0 - r_fixture * 1000.0, 1)

    # always end the probe back at the safe 5 V default
    try:
        ch224k.set_voltage(5.0)
    except ValueError:
        pass
    load.set_current(0.0, enable=False)
    load.phone_switch(True)  # re-enable phone path after probing
    return results


def run_charge(cfg: dict, reader, ch224k, load, sim_state, duration: float,
               phone_expected: bool) -> tuple[dict | None, SessionMeta, list[Sample], SessionTracker]:
    """DEVELOPMENT_PLAN.md §4.3 — 5 V only, monitor until CHARGED or timeout."""
    meas = cfg.get("measurement", {})
    rate = float(cfg["hardware"].get("sample_rate_hz", 25.0))
    simulate = cfg["hardware"].get("simulate", False)
    v_target = float(meas.get("v_target_5v", 5.0))
    r_fixture = float(meas.get("r_fixture_ohm", 0.0))
    length = meas.get("length_m")
    sim_cfg = cfg.get("sim", {})

    load.phone_switch(True)
    tracker = SessionTracker(cfg, v_target=v_target, phone_expected=phone_expected)
    samples: list[Sample] = []

    def on_sample(s: Sample) -> bool:
        state, event = tracker.update(s)
        if event == "charged":
            return False
        if state == "FAULT":
            return False
        return s.t < duration

    def tick(elapsed: float) -> None:
        if simulate and sim_state is not None:
            sim_state.current = phone_charge_curve(
                elapsed,
                plug_s=float(sim_cfg.get("phone_plug_s", 3.0)),
                current_a=float(sim_cfg.get("phone_current_a", 1.2)),
                taper_s=float(sim_cfg.get("phone_taper_s", 60.0)),
            )

    sampler = Sampler(reader, rate, simulate)
    live_counter = [0]

    def on_sample_live(s: Sample) -> bool:
        cont = on_sample(s)  # tracker stamps state FIRST
        live_counter[0] += 1
        if live_counter[0] % 25 == 0:
            cli.print_live(s)
        return cont

    samples = sampler.run(duration, on_sample=on_sample_live, tick=tick, state="UNKNOWN")

    features = compute_features(
        samples, v_target=v_target, r_fixture=r_fixture, length_m=length,
        i_min=float(meas.get("i_min_compute", 0.1)),
        i_no_load=float(cfg.get("session", {}).get("i_no_load", 0.05)),
    )
    meta = SessionMeta(
        mode="charge",
        v_target=v_target,
        length_m=length,
        started_at=samples[0].t if samples else 0.0,
        ended_at=samples[-1].t if samples else 0.0,
        charging_detected=any(s.state == "CHARGING" for s in samples) or tracker.ever_charged,
        v_present=any(s.valid and s.voltage >= 1.0 for s in samples),
        phone_expected=phone_expected,
        fault_reason=tracker.fault_reason,
    )
    return features, meta, samples, tracker


def main() -> int:
    args = parse_args()
    cli.force_utf8_stdout()
    cfg = load_config(args.config, simulate=args.simulate, demo=args.demo)
    simulate = cfg["hardware"].get("simulate", False)

    cli.print_banner()

    # calibration file (written by scripts/calibrate.py) overrides config baseline
    cal_path = Path(cfg.get("paths", {}).get("data_dir", "data")) / "calibration.json"
    if cal_path.exists():
        cal = json.loads(cal_path.read_text())
        if "r_fixture_mean_mohm" in cal:
            cfg["measurement"]["r_fixture_ohm"] = float(cal["r_fixture_mean_mohm"]) / 1000.0
            print(f"  using calibration: R_fixture = {cal['r_fixture_mean_mohm']:.1f} mΩ ({cal_path.name})")

    reader, ch224k, load, sim_state = build_hardware(cfg, simulate)

    if args.demo:
        cfg["session"]["debounce_start_s"] = min(float(cfg["session"]["debounce_start_s"]), 3.0)
        cfg["session"]["debounce_end_s"] = min(float(cfg["session"]["debounce_end_s"]), 8.0)
        args.phone_expected = True

    if args.length is not None:
        cfg["measurement"]["length_m"] = args.length

    storage = Storage(cfg.get("paths", {}).get("data_dir", "data"))
    try:
        if args.self_check:
            results = run_self_check(cfg, reader, ch224k)
            cli.print_self_check(results)
            return 0 if all(results.get(k) for k in ("ina219", "voltage", "pwr_ok")) else 1

        meta = SessionMeta(mode=args.mode, length_m=cfg["measurement"].get("length_m"))
        sid = storage.new_session(meta)

        probe: dict = {}
        if args.mode in ("auto", "probe"):
            probe = run_probe(cfg, reader, ch224k, load, sim_state)
            storage.save_probe(sid, probe)
            cli.print_probe(probe)

        features, meta, samples, tracker = (None, meta, [], None)
        duration = args.duration or float(cfg["session"].get("charge_timeout_s", 7200))
        if args.mode in ("auto", "charge"):
            features, meta, samples, tracker = run_charge(
                cfg, reader, ch224k, load, sim_state, duration, args.phone_expected
            )
            meta.mode = args.mode
            meta.session_id = sid
            meta.probe = probe

        if args.mode == "probe":
            # grade from the probe's highest-current step instead of a charge session
            if probe.get("steps"):
                best = probe["steps"][-1]
                r_ohm = best["r_cable_mohm"] / 1000.0
                features = {
                    "r_mean": r_ohm,
                    "r_std": r_ohm * 0.05, "r_max": r_ohm,
                    "r_p95": r_ohm, "r_p5": r_ohm,  # single-step probe: no spread
                    "r_dvdi": None, "dV_dI_slope": 0.0, "sigma_V": 0.0,
                    "V_min": best["v_load"], "eta": best["v_load"] / 5.0,
                    "mean_I": best["i"], "max_I": best["i"], "mean_P_loss": 0.0,
                    "E_wh": 0.0, "dR_dt_mOhm_per_min": probe.get("dR_dt_mOhm_per_min", 0.0),
                    "interruption_frac": 0.0, "spike_count": 0, "idle_I": None,
                    "n_busy": 1, "n_total": 1, "valid_frac": 1.0, "duration_s": 0.0,
                    "v_target": best["v_target"], "r_fixture": probe.get("r_fixture", 0.0),
                    "length_m": cfg["measurement"].get("length_m"),
                }
                meta.v_present = True
            elif probe.get("pd_blocked"):
                # power was present but PD negotiation failed -> surface PD_BLOCKED
                meta.v_present = True

        verdict_meta = {
            "session_id": sid,
            "mode": meta.mode,
            "v_present": meta.v_present,
            "charging_detected": meta.charging_detected,
            "phone_expected": meta.phone_expected,
            "fault_reason": meta.fault_reason,
            "probe": probe,
            "length_m": meta.length_m,
        }
        verdict = rule_verdict(features, verdict_meta, cfg)

        if samples:
            storage.add_samples(sid, samples)
            storage.export_csv(sid, samples)
        meta.session_id = sid
        storage.save_verdict(sid, verdict, meta)
        cli.print_verdict(verdict, as_json=args.json)
        return 0
    finally:
        ch224k.close()
        load.close()
        reader.shutdown()
        storage.close()


if __name__ == "__main__":
    sys.exit(main())
