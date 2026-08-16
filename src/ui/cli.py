"""Console UI helpers (DEVELOPMENT_PLAN.md §9 ``ui/cli.py``)."""
from __future__ import annotations

import json

from src.telemetry.models import Sample


def force_utf8_stdout() -> None:
    """Make Ω/µ/± printable on Windows consoles (Pi is already UTF-8)."""
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


BANNER = r"""
 RADWI Cable-Quality Analyzer  (rule-based engine v1)
 Charger -> [TEST CABLE] -> analyzer -> phone
"""


def print_banner() -> None:
    print(BANNER)


def print_self_check(results: dict) -> None:
    print("\n--- SELF-CHECK ---")
    ok = True
    for key, label in (
        ("ina219", "INA219 on I2C"),
        ("voltage", "Bus voltage ~5 V"),
        ("pwr_ok", "CH224K PWR_OK (PD contract; manual mode bypasses this)"),
    ):
        passed = bool(results.get(key))
        ok = ok and passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
    if "v_reading" in results:
        print(f"  V_reading = {results['v_reading']:.3f} V  I = {results['i_reading'] * 1000:.1f} mA")
    if results.get("pwr_ok_note"):
        print(f"  note: {results['pwr_ok_note']}")
    if "note" in results:
        print(f"  note: {results['note']}")
    print(f"  RESULT: {'ALL PASS - ready for calibrate.py' if ok else 'FIX BEFORE CONTINUING'}\n")


def print_probe(probe: dict) -> None:
    heading = "manual-voltage readings" if probe.get("manual_voltage_mode") else "multi-voltage cable characterization"
    print(f"\n--- PROBE ({heading}) ---")
    if probe.get("measurement_note"):
        print(f"  NOTE: {probe['measurement_note']}")
    for step in probe.get("steps", []):
        print(
            f"  V_target={step['v_target']:>5.1f} V  I={step['i']:>4.2f} A  "
            f"V_load={step['v_load']:.3f} V  R_loop={step['r_loop_mohm']:>7.1f} mΩ  "
            f"R_cable={step['r_cable_mohm']:>7.1f} mΩ"
        )
    for reading in probe.get("manual_readings", []):
        if "supports_voltage" in reading:
            print(
                f"  support V={reading['v_target']:>5.1f} V  "
                f"flag={reading['supports_voltage']}  "
                f"I_mean={reading.get('support_i_mean', 0.0):.4f} A  "
                f"valid={reading.get('support_n_valid', 0)}  "
                f"reason={reading.get('support_reason', '-')}"
            )
        if "measurement_n" in reading:
            print(
                f"  measure V={reading['v_target']:>5.1f} V  "
                f"I_mean={reading.get('measurement_i_mean', 0.0):.4f} A  "
                f"valid={reading.get('measurement_n_valid', 0)} / {reading['measurement_n']}"
            )
        elif "v_mean" in reading:
            # Backwards-compatible display for legacy manual summaries.
            print(
                f"  manual V_target={reading['v_target']:>5.1f} V  "
                f"V_mean={reading.get('v_mean') if reading.get('v_mean') is not None else 'n/a'} V  "
                f"I_mean={reading.get('i_mean') if reading.get('i_mean') is not None else 'n/a'} A  "
                f"valid={reading.get('n_valid', 0)}"
            )
    if probe.get("measurement_allocations"):
        print(f"  measurement allocation (readings): {probe['measurement_allocations']}")
    if probe.get("quality_reference_voltage"):
        print(f"  quality reference voltage: {probe['quality_reference_voltage']} V")
    if probe.get("quality_reference_note"):
        print(f"  note: {probe['quality_reference_note']}")
    if probe.get("no_current_all_voltages"):
        print("  !! no current flowed in the 5 V, 9 V, or 12 V ranges")
    unsupported = probe.get("unsupported_voltages") or []
    if unsupported:
        print(f"  compatibility: phone drew no current at {unsupported} V; excluded from quality grade")
    for recovery in probe.get("recovery_checks", []):
        print(
            f"  recovery at 5 V after {recovery['after_voltage']} V: "
            f"I_mean={recovery['i_mean']} A  "
            f"charging_resumed={recovery['charging_resumed']}"
        )
    mismatch = probe.get("manual_voltage_mismatch") or []
    if mismatch:
        print(f"  !! measured voltage did not match requested voltage at: {mismatch} V")
    rail_mismatch = probe.get("rail_mismatch_voltages") or []
    if rail_mismatch:
        print(f"  !! rail verification failed at: {rail_mismatch} V (support not determined)")
    blocked = probe.get("pd_blocked") or []
    if blocked:
        print(f"  !! PD negotiation FAILED at: {blocked} V  (cable blocks CC/PD signaling)")
    heat = probe.get("dR_dt_mOhm_per_min")
    if heat is not None:
        print(f"  heating dR/dt over {probe.get('heat_hold_s', 0):.0f} s hold: {heat:+.2f} mΩ/min")
    print()


def print_live(sample: Sample) -> None:
    print(
        f"  t={sample.t:6.1f}s  V={sample.voltage:6.3f} V  "
        f"I={sample.current:6.3f} A  P={sample.power:6.2f} W  state={sample.state}"
    )


def print_verdict(verdict: dict, as_json: bool = False) -> None:
    print("\n--- VERDICT ---")
    if as_json:
        print(json.dumps(verdict, indent=2, default=str))
    else:
        print(f"  verdict   : {verdict.get('verdict', '')}")
        print(f"  grade     : {verdict.get('grade')}")
        print(f"  confidence: {verdict.get('confidence')}")
        confidence_details = verdict.get("confidence_details") or {}
        if confidence_details:
            print("  confidence details:")
            for key in (
                "r_mean", "r_dvdi", "n_busy", "r_std", "scale",
                "confidence_base", "agreement", "voltage_penalty",
            ):
                print(f"    {key}: {confidence_details.get(key)}")
        tags = verdict.get("tags") or []
        print(f"  tags      : {', '.join(tags) if tags else '-'}")
        for line in verdict.get("evidence", []):
            print(f"  evidence  : {line}")
        for line in verdict.get("limitations", []):
            print(f"  note      : {line}")
    print()
