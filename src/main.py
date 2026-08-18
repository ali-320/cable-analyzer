"""Orchestrator — DEVELOPMENT_PLAN.md §4 test protocol + §6 verdict.

Run from the cable-analyzer/ folder (Python 3.11+):

    python -m src.main --help
    python -m src.main --self-check            # hardware sanity (run first)
    python -m src.main --length 1.0            # full: probe + charge -> verdict
    python -m src.main --demo                  # no hardware: simulated good-ish cable
    python -m src.main --simulate --mode probe # probe only, simulated

Modes: auto (probe + charge), probe, charge. With ``--manual``, the
operator changes CH224K SEL straps by hand and confirms the measured voltage
at each step; no CH224K GPIO/PWR_OK pins are used.

The manual setup now places the INA219 inline between CH224K VBUS and the
phone. Manual mode first verifies voltage support during VERIFICATION windows,
then allocates the measurement budget across supported 5 V, 9 V, and 12 V
ranges. Measurement samples use the normal charge states (CHARGING, CHARGED,
NO_PHONE, NO_SOURCE, or FAULT).
Unsupported ranges are excluded. All supported voltage measurement datasets
are combined for grading and quality analysis; the lowest supported voltage is
kept as the reference for voltage-specific display and confidence context.

With ``--voltage`` (CH224K removed), the charger's own voltage is used
directly: probe mode starts reading immediately with no voltage selection or
verification, self-check only proves that a source voltage is present, and
readings are bucketed into real-time voltage classes (1 V wide by default).
Each class is calculated separately at its own class-center target and the
per-class feature sets are combined for the final grade. The user is never
asked to change a voltage manually in this mode.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import tomllib
from pathlib import Path

from src.analysis.rules import evaluate as rule_verdict
from src.features.metrics import (
    combine_features,
    compute_features,
    compute_features_by_class,
    voltage_class_center,
)
from src.hardware.ch224k import CH224KController
from src.hardware.gpio_map import PinMap
from src.hardware.ina219_reader import INA219Reader
from src.hardware.load_ctrl import LoadController
from src.hardware.sim import SimState, phone_charge_curve
from src.telemetry.models import Sample, SessionMeta
from src.telemetry.sampler import Sampler
from src.telemetry.session import SessionTracker
from src.telemetry.storage import Storage
from src.telemetry.remote import sync_pending
from src.ui import cli


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="RADWI cable-quality analyzer (rules)")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--simulate", action="store_true", help="use synthetic hardware")
    ap.add_argument("--manual", action="store_true", help="manual CH224K SEL changes; no CH224K GPIO required")
    ap.add_argument("--voltage", action="store_true", help="CH224K removed: use the charger's present voltage directly with real-time voltage classes; never prompts for a manual voltage change")
    ap.add_argument("--mode", choices=["auto", "probe", "charge"], default="auto")
    ap.add_argument("--self-check", action="store_true", help="hardware sanity check only")
    ap.add_argument("--duration", type=float, default=None, help="charge-mode timeout (s)")
    ap.add_argument("--length", type=float, default=None, help="cable length (m) for normalization")
    ap.add_argument("--phone-expected", action="store_true", help="a phone is attached (enables OPEN detection)")
    ap.add_argument("--demo", action="store_true", help="short simulated run with no hardware")
    ap.add_argument("--json", action="store_true", help="print verdict as JSON")
    return ap.parse_args()


def validate_current_bands(cfg: dict) -> None:
    """Reject ambiguous current bands before a probe or charge run starts."""
    s = cfg.get("session", {})
    no_phone = float(s.get("i_no_phone_max", 0.010))
    full_min = float(s.get("i_fully_charged_min", no_phone))
    full_max = float(s.get("i_fully_charged_max", 0.10))
    charge_start = float(s.get("i_charge_start", full_max))
    if not (
        0.0 <= no_phone
        and full_min == no_phone
        and full_max == charge_start
        and full_min < full_max
    ):
        raise ValueError(
            "session current bands must be contiguous and satisfy "
            "0 <= i_no_phone_max == i_fully_charged_min < "
            "i_fully_charged_max == i_charge_start"
        )


def load_config(path: str, simulate: bool, demo: bool, manual: bool = False, voltage: bool = False) -> dict:
    with open(path, "rb") as fh:
        cfg = tomllib.load(fh)
    validate_current_bands(cfg)
    if simulate or demo:
        cfg["hardware"]["simulate"] = True
        # the simulator bakes its own fixture resistance into V_load; keep the
        # R_cable = R_loop - R_fixture subtraction consistent in demo mode
        sim_fixture = float(cfg.get("sim", {}).get("r_fixture_ohm", 0.05))
        cfg["measurement"]["r_fixture_ohm"] = sim_fixture
    if not isinstance(cfg.get("ch224k"), dict):
        cfg["ch224k"] = {}
    if not isinstance(cfg.get("voltage"), dict):
        cfg["voltage"] = {}
    # The controller deliberately keeps simulation's automatic behavior by
    # default. This explicit marker lets ``--manual --simulate`` exercise the
    # same adaptive manual workflow as the real temporary wiring.
    cfg["_manual_requested"] = bool(manual)
    if manual:
        cfg["ch224k"]["control_mode"] = "manual"
    # CH224K-removed architecture: the charger's present voltage is used
    # directly, and the NO_SOURCE threshold is raised to the configured value.
    cfg["_voltage_requested"] = bool(voltage)
    if voltage:
        cfg["ch224k"]["control_mode"] = "voltage"
        cfg["voltage"]["enabled"] = True
        cfg["session"]["v_present_min_v"] = float(
            cfg["voltage"].get("v_present_min_v", 3.0)
        )
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
        if cfg.get("ch224k", {}).get("control_mode") == "voltage":
            # No CH224K to negotiate: the simulated charger simply provides
            # its own (configurable) voltage.
            sim_state.v_target = float(sim_cfg.get("source_voltage_v", 5.0))
    pins = PinMap.from_config(cfg)
    reader = INA219Reader(cfg, simulate=simulate, sim_state=sim_state)
    ch224k = CH224KController(pins, cfg, simulate=simulate, sim_state=sim_state)
    load = LoadController(pins, cfg, simulate=simulate, sim_state=sim_state)
    return reader, ch224k, load, sim_state


def run_self_check(cfg: dict, reader: INA219Reader, ch224k: CH224KController) -> dict:
    manual = bool(getattr(ch224k, "manual", False))
    voltage_mode = bool(getattr(ch224k, "voltage_mode", False))
    results: dict = {
        "ina219": False, "voltage": False, "pwr_ok": manual,
        "manual_mode": manual, "voltage_mode": voltage_mode,
    }
    if not reader.ok and not reader.simulate:
        results["note"] = "INA219 not found on I2C - check wiring (i2cdetect -y 1 should show 0x40)"
        return results
    v, i, p, ok = reader.read_sample(time.monotonic())
    results["ina219"] = ok  # the chip only passes if the I2C read actually worked
    results["v_reading"] = v
    results["i_reading"] = i
    if voltage_mode:
        # No CH224K and no fixed 5 V target: any charger may be attached, so
        # self-check only proves that a source voltage is present, using the
        # same threshold the NO_SOURCE state applies.
        v_present_min = float(cfg.get("session", {}).get("v_present_min_v", 3.0))
        results["voltage"] = ok and v >= v_present_min
        results["v_present_min_v"] = v_present_min
    else:
        v_target = float(cfg.get("measurement", {}).get("v_target_5v", 5.0))
        results["voltage"] = ok and abs(v - v_target) <= 0.25 * v_target
    pwr_ok = ch224k.read_pwr_ok()
    if pwr_ok is not None:
        results["pwr_ok"] = bool(pwr_ok)
    else:
        results["pwr_ok"] = True  # no PWR_OK wire in manual/voltage wiring
        if voltage_mode:
            results["pwr_ok_note"] = "voltage mode: no CH224K present; source voltage is verified from the INA219"
        else:
            results["pwr_ok_note"] = "manual mode: CH224K PWR_OK is not wired; voltage is verified from INA219/DMM"
    if not results["voltage"]:
        if voltage_mode:
            results["note"] = f"no source voltage >= {results.get('v_present_min_v', 3.0):.1f} V detected (read {v:.2f} V) - check charger and cable"
        else:
            results["note"] = f"expected ~{v_target} V but read {v:.2f} V - check charger/cable/CH224K"
    return results


def _manual_voltage_confirmation(target_v: float) -> None:
    """Require an explicit terminal confirmation before a manual voltage step."""
    if target_v > 5.0:
        print(
            "  WARNING: SEL GPIO control and phone isolation are unavailable. "
            "The phone is directly connected; verify that it supports this "
            "voltage before continuing."
        )
    prompt = (
        f"  Set CH224K SEL0/SEL1/SEL2 manually for {target_v:g} V, verify VBUS "
        "with a DMM, then type the target voltage to continue: "
    )
    try:
        confirmation = float(input(prompt).strip())
    except EOFError as exc:
        raise RuntimeError("manual voltage mode needs an interactive terminal") from exc
    except ValueError as exc:
        raise RuntimeError(f"manual voltage {target_v:g} V was not confirmed; stopped safely") from exc
    if abs(confirmation - target_v) > max(0.05, target_v * 0.01):
        raise RuntimeError(f"manual voltage {target_v:g} V was not confirmed; stopped safely")


def _run_manual_probe_adaptive(cfg: dict, reader, ch224k, load) -> dict:
    """Run the inline-phone manual probe with adaptive voltage allocation."""
    pr = cfg.get("probe", {})
    configured = [int(v) for v in pr.get("voltages", [5, 9, 12])]
    voltages = [v for v in (5, 9, 12) if v in configured]
    if not voltages:
        voltages = [5, 9, 12]

    rate = float(cfg.get("hardware", {}).get("sample_rate_hz", 25.0))
    simulate = bool(cfg.get("hardware", {}).get("simulate", False) or getattr(reader, "simulate", False))
    ch_cfg = cfg.get("ch224k", {})
    meas = cfg.get("measurement", {})
    session_cfg = cfg.get("session", {})
    verify_tol = float(ch_cfg.get("verify_tolerance", 0.05))
    manual_verify_tol_v = float(ch_cfg.get("manual_verify_tolerance_v", 1.0))
    manual_verify_min_step_v = float(ch_cfg.get("manual_verify_min_step_v", 0.5))
    # Verification answers a different question from quality measurement:
    # does the phone/load draw anything above board leakage at this voltage?
    # Active charging (>= i_charge_start) and fully-charged maintenance draw
    # (i_fully_charged_min .. < i_charge_start) both prove support. Only the
    # no-phone leakage band (< i_no_phone_max) means no current is present.
    i_support_min = float(session_cfg.get(
        "i_no_phone_max",
        session_cfg.get("i_fully_charged_min", 0.010),
    ))

    support_n = max(1, int(pr.get("manual_support_readings", round(rate * 2.0))))
    total_n = max(1, int(pr.get("manual_total_readings", 3000)))
    min_measure_n = max(1, int(pr.get("manual_min_measurement_readings", 5)))
    r_fixture = float(meas.get("r_fixture_ohm", 0.0))
    session_samples: list[Sample] = []
    manual_readings: list[dict] = []
    support_flags: dict[str, bool] = {}
    support_observations: dict[str, str] = {}
    supported: list[int] = []
    voltage_features: dict[str, dict] = {}
    mismatches: list[int] = []
    rail_mismatch_voltages: list[int] = []
    last_verified_v = 5.0
    current_voltage: int | None = None

    def verify_rail(target_v: int, require_step: bool = True) -> bool:
        nonlocal last_verified_v
        v_check, _, _, ok = reader.read_sample(time.monotonic())
        if not ok or math.isnan(v_check) or v_check < 1.0:
            return False
        # In the inline-phone manual wiring, the phone is already loading
        # VBUS while the rail is verified. Apply the manual loaded-rail
        # tolerance to 5 V as well; the old GPIO-style +/-5% check rejected
        # legitimate loaded readings below 4.75 V and skipped the entire
        # verification window. Quality features still measure the resulting
        # voltage drop and grade the cable, so this is only a presence check.
        verified = abs(v_check - target_v) <= manual_verify_tol_v
        if target_v != 5 and require_step:
            verified = verified and v_check >= last_verified_v + manual_verify_min_step_v
        if verified:
            last_verified_v = float(target_v)
        return verified

    def select_voltage(target_v: int, require_step: bool = True) -> bool:
        nonlocal current_voltage
        _manual_voltage_confirmation(float(target_v))
        if not ch224k.set_voltage(float(target_v)):
            return False
        current_voltage = target_v
        return verify_rail(target_v, require_step=require_step)

    def collect(
        count: int,
        phase_state: str,
        tracker: SessionTracker | None = None,
    ) -> list[Sample]:
        """Collect one probe phase and print a live sample every second.

        VERIFICATION is a probe-only phase. Measurement phases are annotated by
        SessionTracker so their CSV states match charge mode (CHARGING, CHARGED,
        NO_PHONE, NO_SOURCE, or FAULT).
        """
        duration = max(count, 1) / max(rate, 1e-6)
        next_report_s = 1.0
        # Verification is a distinct CSV phase, but it still receives the
        # same hardware safety checks as charge-mode samples. Normal samples
        # retain VERIFICATION; a real safety fault is labeled FAULT.
        safety_tracker = (
            SessionTracker(cfg, v_target=float(current_voltage or 5), phone_expected=False)
            if phase_state == "VERIFICATION" and tracker is None
            else None
        )

        def on_sample(sample: Sample) -> bool:
            nonlocal next_report_s
            if tracker is not None:
                state, _event = tracker.update(sample)
            elif safety_tracker is not None:
                safety_state, _event = safety_tracker.update(sample)
                state = safety_state
                sample.state = "FAULT" if safety_state == "FAULT" else phase_state
            else:
                state = phase_state
                sample.state = phase_state
            if sample.t + 1e-9 >= next_report_s:
                cli.print_live(sample)
                next_report_s = sample.t + 1.0
            # A hardware safety fault is terminal for this phase. Keep the
            # fault sample in the CSV, but do not continue probing the phone.
            return state != "FAULT"

        samples = Sampler(
            reader, rate, simulate
        ).run(duration, on_sample=on_sample, state=phase_state)
        session_samples.extend(samples)
        return samples

    results: dict = {
        "steps": [],
        "pd_blocked": [],
        "manual_voltage_mode": True,
        "measurement_note": (
            "Support flags are determined during VERIFICATION windows. Measurements are "
            "allocated across supported voltages and use normal charge-state "
            "labels; unsupported ranges are excluded."
        ),
        "r_fixture": r_fixture,
        "support_flags": {},
        "support_observations": support_observations,
        "manual_readings": manual_readings,
        "unsupported_voltages": [],
        "recovery_checks": [],
        "voltage_features": voltage_features,
        "measurement_budget_readings": total_n,
        "support_check_readings": support_n,
    }

    for v in voltages:
        record = {"v_target": v, "support_check_state": "VERIFICATION"}
        if not select_voltage(v):
            support_flags[str(v)] = False
            support_observations[str(v)] = "rail_not_verified"
            mismatches.append(v)
            rail_mismatch_voltages.append(v)
            record.update({"supports_voltage": False, "support_reason": "rail_not_verified"})
            manual_readings.append(record)
            continue

        check_samples = collect(support_n, phase_state="VERIFICATION")
        valid = [s for s in check_samples if s.valid]
        # A supported voltage may produce either active charging current or
        # low maintenance current from an already-full phone. The only
        # unsupported case is the very-low no-phone/fixture-leakage band.
        current_present = [s for s in valid if s.current >= i_support_min]
        active_fraction = len(current_present) / len(valid) if valid else 0.0
        supports = bool(valid) and active_fraction >= 0.5
        support_flags[str(v)] = supports
        support_observations[str(v)] = "current_observed" if supports else "no_current_observed"
        record.update({
            "supports_voltage": supports,                "support_reason": "current_in_VERIFICATION" if supports else "no_current_in_VERIFICATION",
            "support_v_mean": round(sum(s.voltage for s in valid) / len(valid), 3) if valid else 0.0,
            "support_i_mean": round(sum(s.current for s in valid) / len(valid), 4) if valid else 0.0,
            "support_current_min_a": i_support_min,
            "support_active_fraction": round(active_fraction, 3),
            "support_n_valid": len(valid),
        })
        manual_readings.append(record)
        if supports:
            supported.append(v)
        else:
            results["unsupported_voltages"].append(v)
            print(f"  No phone current above the leakage threshold was detected in the VERIFICATION check at {v:g} V; support flag = False.")

    results["support_flags"] = support_flags

    if supported:
        base, remainder = divmod(total_n, len(supported))
        allocations = {
            v: max(min_measure_n, base + (1 if index < remainder else 0))
            for index, v in enumerate(supported)
        }
        results["measurement_allocations"] = {str(v): n for v, n in allocations.items()}

        for v in supported:
            if not select_voltage(v, require_step=False):
                support_flags[str(v)] = False
                support_observations[str(v)] = "measurement_rail_not_verified"
                mismatches.append(v)
                rail_mismatch_voltages.append(v)
                continue
            count = allocations[v]
            tracker = SessionTracker(cfg, v_target=float(v), phone_expected=True)
            samples = collect(count, phase_state="UNKNOWN", tracker=tracker)
            valid = [s for s in samples if s.valid]
            i_mean = sum(s.current for s in valid) / len(valid) if valid else 0.0
            record = next(r for r in manual_readings if r["v_target"] == v)
            record.update({
                "measurement_n": len(samples),
                "measurement_n_valid": len(valid),
                "measurement_i_mean": round(i_mean, 4),
                "measurement_quality_dataset": True,
            })
            feat = compute_features(
                samples,
                v_target=float(v),
                r_fixture=r_fixture,
                length_m=meas.get("length_m"),
                i_min=float(meas.get("i_min_compute", 0.10)),
                i_no_load=float(session_cfg.get("i_no_load", 0.05)),
                i_no_phone=float(session_cfg.get("i_no_phone_max", 0.01)),
            )
            if feat is not None:
                voltage_features[str(v)] = feat
            else:
                record["measurement_quality_dataset"] = False
                record["support_reason"] = "current_not_sustained_during_measurement"

    results["support_flags"] = support_flags
    results["support_observations"] = support_observations
    results["no_current_all_voltages"] = (
        not voltage_features
        and bool(voltages)
        and all(
            support_flags.get(str(v)) is False
            and support_observations.get(str(v)) == "no_current_observed"
            for v in voltages
        )
    )
    if results["no_current_all_voltages"]:
        print("  No current is flowing in the 5 V, 9 V, or 12 V ranges.")

    if mismatches:
        results["manual_voltage_mismatch"] = sorted(set(mismatches))
    if rail_mismatch_voltages:
        results["rail_mismatch_voltages"] = sorted(set(rail_mismatch_voltages))

    if voltage_features:
        # Use every supported voltage dataset for grading and quality analysis.
        # Keep the lowest supported voltage as the reference for fields whose
        # display is voltage-specific (for example V_min and v_target).
        reference_v = min(int(v) for v in voltage_features)
        results["quality_reference_voltage"] = reference_v
        results["quality_features"] = combine_features(
            list(voltage_features.values()),
            reference_voltage=float(reference_v),
        )
        results["quality_reference_note"] = (
            "all supported voltage datasets were combined for grading and quality analysis"
        )

    # Never silently leave the phone on a higher rail. If the operator cannot
    # confirm the safe reset, abort the session visibly so the hardware can be
    # disconnected and checked rather than producing a misleading verdict.
    if current_voltage != 5:
        _manual_voltage_confirmation(5.0)
    if not ch224k.set_voltage(5.0):
        raise RuntimeError("could not restore CH224K to safe 5 V; disconnect the phone and inspect the rig")
    load.set_current(0.0, enable=False)
    load.phone_switch(True)
    results["_samples"] = session_samples
    return results

def _run_voltage_probe(cfg: dict, reader, ch224k, load, sim_state) -> dict:
    """Passive probe for the CH224K-removed (--voltage) architecture.

    There is no voltage selection, verification window, or manual
    confirmation: the charger's present voltage is read directly. Samples are
    bucketed into real-time voltage classes (``[voltage].class_width_v``,
    1 V by default); each class is calculated separately at its own
    class-center target and the per-class feature sets are combined for
    grading, exactly like the multi-voltage manual flow.
    """
    pr = cfg.get("probe", {})
    vcfg = cfg.get("voltage", {})
    meas = cfg.get("measurement", {})
    session_cfg = cfg.get("session", {})
    sim_cfg = cfg.get("sim", {})
    rate = float(cfg["hardware"].get("sample_rate_hz", 25.0))
    simulate = bool(cfg.get("hardware", {}).get("simulate", False))
    total_n = max(1, int(vcfg.get("measurement_readings", pr.get("manual_total_readings", 3000))))
    class_width = float(vcfg.get("class_width_v", 1.0))
    r_fixture = float(meas.get("r_fixture_ohm", 0.0))
    v_present_min = float(session_cfg.get("v_present_min_v", 3.0))
    duration = total_n / max(rate, 1e-6)

    session_samples: list[Sample] = []
    next_report_s = [1.0]

    def on_live(sample: Sample) -> None:
        if sample.t + 1e-9 >= next_report_s[0]:
            cli.print_live(sample)
            next_report_s[0] = sample.t + 1.0

    # The phone is the load in this wiring, so normal charge states are
    # stamped on the samples and hardware safety faults stop the run.
    tracker = SessionTracker(cfg, phone_expected=True)

    def on_sample(sample: Sample) -> bool:
        state, _event = tracker.update(sample)
        on_live(sample)
        return state != "FAULT"

    def tick(elapsed: float) -> None:
        if simulate and sim_state is not None:
            sim_state.current = phone_charge_curve(
                elapsed,
                plug_s=float(sim_cfg.get("phone_plug_s", 3.0)),
                current_a=float(sim_cfg.get("phone_current_a", 1.2)),
                taper_s=float(sim_cfg.get("phone_taper_s", 60.0)),
                charged_current_a=float(sim_cfg.get("phone_charged_current_a", 0.03)),
            )

    samples = Sampler(reader, rate, simulate).run(
        duration, on_sample=on_sample, tick=tick, state="UNKNOWN"
    )
    session_samples.extend(samples)

    feature_sets, combined = compute_features_by_class(
        samples,
        class_width,
        r_fixture=r_fixture,
        length_m=meas.get("length_m"),
        i_min=float(meas.get("i_min_compute", 0.10)),
        i_no_load=float(session_cfg.get("i_no_load", 0.05)),
        i_no_phone=float(session_cfg.get("i_no_phone_max", 0.01)),
    )

    class_summaries: dict[str, dict] = {}
    for center, feat in feature_sets.items():
        class_summaries[str(center)] = {
            "v_target": center,
            "n": feat["n_total"],
            "n_busy": feat["n_busy"],
            "v_mean": round(feat["V_min"], 3),
            "i_mean": round(feat["mean_I"], 4),
            "r_mean_ohm": round(feat["r_mean"], 6),
        }

    v_present = any(s.valid and s.voltage >= v_present_min for s in samples)
    results: dict = {
        "steps": [],
        "pd_blocked": [],
        "voltage_mode": True,
        "manual_voltage_mode": False,
        "measurement_note": (
            "No CH224K is present; the charger's own voltage is used directly. "
            "Readings are bucketed into real-time voltage classes and every "
            "class is calculated separately, then combined for the final grade."
        ),
        "r_fixture": r_fixture,
        "voltage_classes": class_summaries,
        "voltage_class_width_v": class_width,
        "v_present": v_present,
        # The source rail exists but no charging current flowed at any class.
        "no_current_all_voltages": bool(v_present) and not feature_sets,
        "_samples": session_samples,
    }
    if combined is not None:
        results["quality_features"] = combined
        results["quality_reference_voltage"] = int(round(float(combined["v_target"])))
        results["quality_reference_note"] = (
            "all real-time voltage classes were combined for grading and quality analysis"
        )
    return results


def run_probe(cfg: dict, reader, ch224k, load, sim_state) -> dict:
    """DEVELOPMENT_PLAN.md §4.2 — automatic, manually confirmed, or passive voltage steps."""
    pr = cfg.get("probe", {})
    voltage_mode = bool(
        getattr(ch224k, "voltage_mode", False) or cfg.get("_voltage_requested", False)
    )
    if voltage_mode:
        return _run_voltage_probe(cfg, reader, ch224k, load, sim_state)
    manual = bool(getattr(ch224k, "manual", False) or cfg.get("_manual_requested", False))
    if manual:
        return _run_manual_probe_adaptive(cfg, reader, ch224k, load)
    configured_voltages = [int(v) for v in pr.get("voltages", [5, 9, 12])]
    if manual:
        # Manual phone testing always starts at 5 V, then checks higher PDOs.
        voltages = [v for v in (5, 9, 12) if v in configured_voltages]
        if 5 not in voltages:
            voltages.insert(0, 5)
    else:
        voltages = configured_voltages
    steps = [float(s) for s in pr.get("current_steps", [0.5, 1.0, 1.5, 2.0])]
    hold = float(pr.get("step_hold_s", 5.0))
    ch = cfg.get("ch224k", {})
    verify_tol = float(ch.get("verify_tolerance", 0.05))  # relative, GPIO mode
    # Manual mode: the phone is the load, so the rail is pulled below the
    # requested PDO (e.g. 9 V -> ~8.4 V on a lossy cable). A tight relative
    # tolerance would wrongly call this a failed negotiation. Instead accept
    # any rail within a few volts of the target as long as it clearly stepped
    # up from the previous rail.
    manual_verify_tol_v = float(ch.get("manual_verify_tolerance_v", 1.0))
    manual_verify_min_step_v = float(ch.get("manual_verify_min_step_v", 0.5))
    drop = float(pr.get("transient_drop_s", 1.0))
    heat_hold = float(pr.get("heat_hold_s", 60.0))
    heat_idx = int(pr.get("heat_step_index", -1))
    rate = float(cfg["hardware"].get("sample_rate_hz", 25.0))
    meas = cfg.get("measurement", {})
    r_fixture = float(meas.get("r_fixture_ohm", 0.0))
    # In any manual compatibility check, phone maintenance current also proves
    # that the selected voltage is usable; only the no-phone leakage band means
    # that no load is present.
    support_current_min = float(cfg.get("session", {}).get("i_no_phone_max", 0.010))
    simulate = cfg["hardware"].get("simulate", False)

    load.phone_switch(False)  # GPIO interlock in legacy wiring; no-op in manual mode
    results: dict = {
        "steps": [], "pd_blocked": [], "manual_voltage_mode": manual,
        "r_fixture": r_fixture, "heat_hold_s": heat_hold,
    }
    if manual:
        results["measurement_note"] = (
            "INA219 is inline with the phone. All supported-voltage readings "
            "are used for quality; unsupported ranges are excluded."
        )
        results["unsupported_voltages"] = []
        results["recovery_checks"] = []

    quality_samples: list[Sample] = []
    # Raw samples from every manual voltage and recovery period. These are
    # exported to the session CSV; the compact probe summary remains JSON-safe.
    session_samples: list[Sample] = []

    last_verified_v = 5.0

    def rail_verified(v_check: float, v_target: float, ok: bool) -> bool:
        """True when the negotiated rail is usable for the current wiring.

        GPIO mode keeps the strict relative tolerance. Manual mode accepts
        a rail pulled down by the phone's load (within ``manual_verify_tolerance_v``
        of the target). The 5 V quality rail does not need a step-up check;
        9 V and 12 V must also clear ``manual_verify_min_step_v`` so a rail
        that stayed at 5 V is not mistaken for a successful higher-voltage step.
        """
        if not ok or math.isnan(v_check) or v_check < 1.0:
            return False
        if not manual:
            return abs(v_check - v_target) <= verify_tol * v_target
        # The phone is inline in manual mode, so 5 V can also sag under load.
        # Keep rail presence separate from cable quality: the later feature
        # calculation measures and grades the voltage drop.
        within_target = abs(v_check - v_target) <= manual_verify_tol_v
        if v_target <= 5.0:
            return within_target
        stepped_up = v_check >= last_verified_v + manual_verify_min_step_v
        return within_target and stepped_up

    def recover_to_5(after_voltage: int, hold_seconds: float) -> bool:
        """Return to 5 V after an unsupported/mismatched higher voltage."""
        _manual_voltage_confirmation(5.0)
        ch224k.set_voltage(5.0)
        recovery_tracker = SessionTracker(cfg, v_target=5.0, phone_expected=True)
        recovery = collect(
            max(1, int(round(hold_seconds * rate))),
            phase_state="UNKNOWN",
            tracker=recovery_tracker,
        )
        recovery_valid = [s for s in recovery if s.valid]
        recovery_i = (
            sum(s.current for s in recovery_valid) / len(recovery_valid)
            if recovery_valid else 0.0
        )
        resumed = recovery_i >= float(cfg.get("session", {}).get("i_charge_start", 0.10))
        results["recovery_checks"].append({
            "after_voltage": after_voltage,
            "v_mean": round(
                sum(s.voltage for s in recovery_valid) / len(recovery_valid), 3
            ) if recovery_valid else None,
            "i_mean": round(recovery_i, 4),
            "charging_resumed": resumed,
            "n_valid": len(recovery_valid),
        })
        if resumed:
            quality_samples.extend(recovery)
        else:
            print("  WARNING: charging did not resume at 5 V.")
        return resumed

    for v in voltages:
        if manual:
            _manual_voltage_confirmation(float(v))
        elif v != 5 and load.phone_on:
            raise RuntimeError("phone interlock: cannot probe >5 V while phone connected")
        if not ch224k.set_voltage(float(v)):
            results["pd_blocked"].append(v)
            continue
        # verify the negotiated rail arrived (tolerant in manual mode: the
        # phone's load pulls VBUS below the requested PDO on a lossy cable)
        v_check, _, _, ok = reader.read_sample(time.monotonic())
        if not rail_verified(v_check, float(v), ok):
            (results.setdefault("manual_voltage_mismatch", []) if manual else results["pd_blocked"]).append(v)
            if manual and v > 5:
                print(f"  Voltage did not reach the requested {v:g} V; checking recovery at 5 V.")
                recover_to_5(v, float(pr.get("recovery_hold_s", pr.get("manual_hold_s", hold))))
            continue
        last_verified_v = float(v)
        if manual:
            # The phone is the load in this wiring. Each supported voltage is
            # measured independently; the adaptive workflow combines all of
            # those voltage-specific datasets for grading.
            manual_hold = float(
                pr.get("manual_5v_hold_s" if v == 5 else "manual_hold_s", hold)
            )
            samples = Sampler(reader, rate, simulate).run(manual_hold, state="PROBE")
            session_samples.extend(samples)
            valid = [s for s in samples if s.valid]
            i_mean = (sum(s.current for s in valid) / len(valid)) if valid else 0.0
            v_mean = (sum(s.voltage for s in valid) / len(valid)) if valid else None
            current_present = i_mean >= support_current_min
            reading = {
                "v_target": v,
                "v_mean": round(v_mean, 3) if v_mean is not None else None,
                "i_mean": round(i_mean, 4),
                "charging_detected": current_present,
                "n_valid": len(valid),
                "quality_dataset": True,
            }
            results.setdefault("manual_readings", []).append(reading)

            if v == 5:
                quality_samples.extend(samples)
            elif not current_present:
                # A phone may reject 9/12 V even though the cable and phone are
                # connected. Record that compatibility result, then return to
                # 5 V and verify that charging resumes before continuing.
                results["unsupported_voltages"].append(v)
                print(
                    f"  No charging current detected at {v:g} V. This phone may not "
                    "support that voltage; the step will not affect cable grading."
                )
                recover_to_5(v, float(pr.get("recovery_hold_s", manual_hold)))
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
                    i_no_phone=float(cfg.get("session", {}).get("i_no_phone_max", 0.01)),
                )
                if hf is not None:
                    results["dR_dt_mOhm_per_min"] = round(hf["dR_dt_mOhm_per_min"], 2)
                    results["r_cable_heat_end_mohm"] = round(hf["r_mean"] * 1000.0 - r_fixture * 1000.0, 1)

    # always end the probe back at the safe 5 V default
    try:
        if manual:
            _manual_voltage_confirmation(5.0)
        ch224k.set_voltage(5.0)
    except ValueError:
        pass
    load.set_current(0.0, enable=False)
    load.phone_switch(True)  # re-enable phone path after probing
    if manual and quality_samples:
        quality = compute_features(
            quality_samples,
            v_target=5.0,
            r_fixture=r_fixture,
            length_m=meas.get("length_m"),
            i_min=float(meas.get("i_min_compute", 0.10)),
            i_no_load=float(cfg.get("session", {}).get("i_no_load", 0.05)),
            i_no_phone=float(cfg.get("session", {}).get("i_no_phone_max", 0.01)),
        )
        results["quality_features"] = quality
    if manual:
        # Keep raw manual-probe readings separate from the JSON summary. The
        # caller exports these through the same CSV path used by charge mode.
        results["_samples"] = session_samples
    return results


def run_charge(cfg: dict, reader, ch224k, load, sim_state, duration: float,
               phone_expected: bool) -> tuple[dict | None, SessionMeta, list[Sample], SessionTracker]:
    """DEVELOPMENT_PLAN.md §4.3 — monitor until CHARGED or timeout.

    Legacy wiring runs at 5 V only. In voltage mode (CH224K removed) the
    charger's present voltage is used and samples are bucketed into real-time
    voltage classes for feature extraction.
    """
    meas = cfg.get("measurement", {})
    rate = float(cfg["hardware"].get("sample_rate_hz", 25.0))
    simulate = cfg["hardware"].get("simulate", False)
    voltage_mode = bool(getattr(ch224k, "voltage_mode", False))
    v_target = float(meas.get("v_target_5v", 5.0))
    r_fixture = float(meas.get("r_fixture_ohm", 0.0))
    length = meas.get("length_m")
    session_cfg = cfg.get("session", {})
    sim_cfg = cfg.get("sim", {})

    if getattr(ch224k, "manual", False):
        _manual_voltage_confirmation(5.0)
        ch224k.set_voltage(5.0)
    load.phone_switch(True)
    tracker = SessionTracker(cfg, v_target=v_target, phone_expected=phone_expected)
    samples: list[Sample] = []

    def on_sample(s: Sample) -> bool:
        state, _event = tracker.update(s)
        if state == "FAULT":
            return False
        if state == "CHARGED":
            # The transition sample is already stamped CHARGED by the tracker.
            # Continue collecting so subsequent CHARGED rows are written to
            # the CSV, then stop after the configured finish interval.
            charged_at = tracker.ended_at if tracker.ended_at is not None else s.t
            return (s.t - charged_at) <= tracker.debounce_finish and s.t < duration
        return s.t < duration

    def tick(elapsed: float) -> None:
        if simulate and sim_state is not None:
            sim_state.current = phone_charge_curve(
                elapsed,
                plug_s=float(sim_cfg.get("phone_plug_s", 3.0)),
                current_a=float(sim_cfg.get("phone_current_a", 1.2)),
                taper_s=float(sim_cfg.get("phone_taper_s", 60.0)),
                charged_current_a=float(sim_cfg.get("phone_charged_current_a", 0.03)),
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

    v_present_min = float(session_cfg.get("v_present_min_v", 1.0))
    if voltage_mode:
        # Bucket the readings into real-time voltage classes and combine them.
        _, features = compute_features_by_class(
            samples,
            float(cfg.get("voltage", {}).get("class_width_v", 1.0)),
            r_fixture=r_fixture,
            length_m=length,
            i_min=float(meas.get("i_min_compute", 0.1)),
            i_no_load=float(session_cfg.get("i_no_load", 0.05)),
            i_no_phone=float(session_cfg.get("i_no_phone_max", 0.01)),
        )
        if features is not None:
            v_target = float(features.get("v_target", v_target))
    else:
        features = compute_features(
            samples, v_target=v_target, r_fixture=r_fixture, length_m=length,
            i_min=float(meas.get("i_min_compute", 0.1)),
            i_no_load=float(session_cfg.get("i_no_load", 0.05)),
            i_no_phone=float(session_cfg.get("i_no_phone_max", 0.01)),
        )
    meta = SessionMeta(
        mode="charge",
        v_target=v_target,
        length_m=length,
        started_at=samples[0].t if samples else 0.0,
        ended_at=samples[-1].t if samples else 0.0,
        charging_detected=any(s.state == "CHARGING" for s in samples) or tracker.ever_charged,
        v_present=any(s.valid and s.voltage >= v_present_min for s in samples),
        phone_expected=phone_expected,
        fault_reason=tracker.fault_reason,
    )
    return features, meta, samples, tracker


def main() -> int:
    args = parse_args()
    cli.force_utf8_stdout()
    cfg = load_config(
        args.config,
        simulate=args.simulate,
        demo=args.demo,
        manual=args.manual,
        voltage=args.voltage,
    )
    simulate = cfg["hardware"].get("simulate", False)

    cli.print_banner()
    cli.setup_display()          # bring up ST7735S if attached

    # calibration file (written by scripts/calibrate.py) overrides config baseline
    cal_path = Path(cfg.get("paths", {}).get("data_dir", "data")) / "calibration.json"
    if cal_path.exists():
        cal = json.loads(cal_path.read_text())
        if "r_fixture_mean_mohm" in cal:
            cfg["measurement"]["r_fixture_ohm"] = float(cal["r_fixture_mean_mohm"]) / 1000.0
            print(f"  using calibration: R_fixture = {cal['r_fixture_mean_mohm']:.1f} mΩ ({cal_path.name})")

    reader, ch224k, load, sim_state = build_hardware(cfg, simulate)

    if getattr(ch224k, "manual", False) and args.mode == "auto" and not args.self_check:
        print(
            "Manual voltage mode cannot run auto (probe + charge): the current "
            "Y-junction has no phone-isolation switch. Use --mode probe, or "
            "restore the protected GPIO wiring for auto mode."
        )
        ch224k.close()
        load.close()
        reader.shutdown()
        return 2

    if args.demo:
        cfg["session"]["debounce_state_s"] = min(float(cfg["session"].get("debounce_state_s", 3.0)), 3.0)
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
        probe_samples: list[Sample] = []
        if args.mode in ("auto", "probe"):
            probe = run_probe(cfg, reader, ch224k, load, sim_state)
            # Raw probe samples are exported to CSV, but must not be embedded
            # in probe_json (which is the compact session summary).
            probe_samples = probe.pop("_samples", [])
            storage.save_probe(sid, probe)
            cli.print_probe(probe)

        features, meta, samples, tracker = (None, meta, probe_samples, None)
        duration = args.duration or float(cfg["session"].get("charge_timeout_s", 7200))
        if args.mode in ("auto", "charge"):
            features, meta, samples, tracker = run_charge(
                cfg, reader, ch224k, load, sim_state, duration, args.phone_expected
            )
            meta.mode = args.mode
            meta.session_id = sid
            meta.probe = probe

        if args.mode == "probe":
            if probe.get("voltage_mode"):
                # Passive mode: voltage presence comes from the measured source
                # rail; quality features come from the combined voltage classes.
                meta.v_present = bool(probe.get("v_present", False))
                if probe.get("quality_features"):
                    features = probe["quality_features"]
                    meta.v_target = float(features.get("v_target", meta.v_target))
            # A manual session can still prove that voltage is present even if
            # the INA219 branch has no current. Do not mislabel that as NO_SOURCE.
            elif probe.get("manual_readings"):
                meta.v_present = True
            # In manual inline-phone mode, grade from all supported-voltage
            # measurement datasets. Unsupported ranges are excluded.
            if probe.get("quality_features"):
                features = probe["quality_features"]
                meta.v_present = True
            # Legacy probe mode grades from the highest-current load step.
            elif probe.get("steps"):
                best = probe["steps"][-1]
                r_ohm = best["r_cable_mohm"] / 1000.0
                features = {
                    "r_mean": r_ohm,
                    "r_std": r_ohm * 0.05, "r_max": r_ohm,
                    "r_p95": r_ohm, "r_p5": r_ohm,  # single-step probe: no spread
                    "r_dvdi": None, "dV_dI_slope": 0.0, "sigma_V": 0.0,
                    "V_min": best["v_load"], "eta": best["v_load"] / max(best["v_target"], 1e-6),
                    "mean_I": best["i"], "max_I": best["i"], "mean_P_loss": 0.0,
                    "E_wh": 0.0, "dR_dt_mOhm_per_min": probe.get("dR_dt_mOhm_per_min", 0.0),
                    "interruption_frac": 0.0, "spike_count": 0, "idle_I": None,
                    "n_busy": 1, "n_total": 1, "valid_frac": 1.0, "duration_s": 0.0,
                    "v_target": best["v_target"], "r_fixture": probe.get("r_fixture", 0.0),
                    "length_m": cfg["measurement"].get("length_m"),
                }
                meta.v_present = True
            elif probe.get("pd_blocked") or probe.get("manual_voltage_mismatch"):
                # power was present but negotiation/measurement verification failed
                meta.v_present = True

        verdict_meta = {
            "session_id": sid,
            "mode": meta.mode,
            "v_present": meta.v_present,
            "charging_detected": meta.charging_detected,
            "phone_expected": meta.phone_expected,
            "fault_reason": meta.fault_reason,
            "probe": probe,
            "manual_voltage_mode": bool(probe.get("manual_voltage_mode", False)),
            "voltage_mode": bool(
                probe.get("voltage_mode", False)
                or getattr(ch224k, "voltage_mode", False)
            ),
            "length_m": meta.length_m,
        }
        verdict = rule_verdict(features, verdict_meta, cfg)

        if samples:
            storage.add_samples(sid, samples)
            storage.export_csv(sid, samples)
        meta.session_id = sid
        storage.save_verdict(sid, verdict, meta)
        storage.enqueue_remote(sid)
        remote_result = (
            sync_pending(storage, cfg)
            if cfg.get("remote", {}).get("sync_on_completion", True)
            else {"attempted": 0, "completed": 0, "failed": 0, "pending": storage.remote_pending_count()}
        )
        if remote_result["attempted"]:
            print(
                "  remote sync: "
                f"{remote_result['completed']} completed, "
                f"{remote_result['pending']} pending"
            )
        cli.print_verdict(verdict, as_json=args.json)
        return 0
    finally:
        cli.close_display()      # turn off backlight and release SPI/GPIO
        ch224k.close()
        load.close()
        reader.shutdown()
        storage.close()


if __name__ == "__main__":
    sys.exit(main())
