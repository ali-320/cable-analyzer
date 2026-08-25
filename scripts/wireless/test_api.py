#!/usr/bin/env python3
"""Probe the Battery.ai ingest API with different payloads to find what works."""

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

# Load .env
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if line.startswith("export "):
            line = line[7:]
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

BASE = os.environ.get("BASE", "").rstrip("/")
TOKEN = os.environ.get("CABLE_INGEST_TOKEN", "")
DEVICE_ID = os.environ.get("CABLE_INGEST_DEVICE_ID", "pi-zero-2w-01")

if not BASE or not TOKEN:
    print("ERROR: Set BASE and CABLE_INGEST_TOKEN in .env")
    sys.exit(1)

print(f"BASE:     {BASE}")
print(f"TOKEN:    {TOKEN[:8]}...{TOKEN[-4:]}")
print(f"DEVICE:   {DEVICE_ID}")
print()


def post(endpoint, payload):
    url = f"{BASE}/ingest/v1/cable/{endpoint}"
    data = json.dumps(payload, separators=(",", ":")).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            print(f"  OK {resp.status}: {body}")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  FAIL {e.code}: {body}")
        return False


# ── Test 1: Minimal session (null timestamps) ──
print("=== Test 1: Minimal session (null timestamps) ===")
post("sessions", {
    "session_id": "test-minimal-001",
    "device_id": DEVICE_ID,
    "mode": "probe",
    "v_target": 5.0,
    "length_m": 1.0,
    "phone_expected": True,
    "started_at": None,
    "ended_at": None,
    "charging_detected": False,
    "v_present": True,
    "fault_reason": None,
    "probe_json": None,
    "verdict_json": None,
    "created_at": "2026-01-01T00:00:00+00:00",
})

# ── Test 2: Session with epoch timestamps ──
print("\n=== Test 2: Session with epoch timestamps ===")
post("sessions", {
    "session_id": "test-epoch-002",
    "device_id": DEVICE_ID,
    "mode": "auto",
    "v_target": 5.0,
    "length_m": 1.0,
    "phone_expected": True,
    "started_at": 1755002357.0,
    "ended_at": 1755002392.0,
    "charging_detected": True,
    "v_present": True,
    "fault_reason": None,
    "probe_json": None,
    "verdict_json": None,
    "created_at": "2026-01-01T00:00:00+00:00",
})

# ── Test 3: Session with probe_json as object ──
print("\n=== Test 3: Session with probe_json object ===")
post("sessions", {
    "session_id": "test-probe-003",
    "device_id": DEVICE_ID,
    "mode": "auto",
    "v_target": 5.0,
    "length_m": 1.0,
    "phone_expected": True,
    "started_at": 1755002357.0,
    "ended_at": 1755002392.0,
    "charging_detected": True,
    "v_present": True,
    "fault_reason": None,
    "probe_json": {
        "steps": [
            {"v_target": 5, "i": 0.5, "v_load": 4.858, "r_loop_mohm": 260.1, "r_cable_mohm": 210.1}
        ],
        "r_fixture": 0.05,
    },
    "verdict_json": {
        "session_id": "test-probe-003",
        "model": "rules-v1",
        "verdict": "Grade B: Good",
        "grade": "B",
        "tags": [],
        "confidence": 0.9,
    },
    "created_at": "2026-01-01T00:00:00+00:00",
})

# ── Test 4: Sample batch ──
print("\n=== Test 4: Sample batch ===")
post("samples", [
    {
        "session_id": "test-probe-003",
        "device_id": DEVICE_ID,
        "sample_index": 0,
        "t": 0.0,
        "voltage_v": 4.858,
        "current_a": 0.5,
        "power_w": 2.429,
        "state": "CHARGING",
        "valid": True,
    }
])

# ── Test 5: Exactly matching the real data format from remote.txt ──
print("\n=== Test 5: Real data format (from remote.txt) ===")
post("sessions", {
    "session_id": "20260805T154643884186",
    "device_id": DEVICE_ID,
    "mode": "auto",
    "v_target": 5.0,
    "length_m": 1.0,
    "phone_expected": False,
    "started_at": 0.0,
    "ended_at": 84.76,
    "charging_detected": True,
    "v_present": True,
    "fault_reason": None,
    "probe_json": {
        "steps": [
            {"v_target": 5, "i": 0.5, "v_load": 4.858, "r_loop_mohm": 260.1, "r_cable_mohm": 210.1}
        ],
        "pd_blocked": [],
        "r_fixture": 0.05,
        "heat_hold_s": 60.0,
        "dR_dt_mOhm_per_min": 95.92,
        "r_cable_heat_end_mohm": 302.6,
    },
    "verdict_json": {
        "session_id": "20260805T154643884186",
        "model": "rules-v1",
        "verdict": "Grade D: Poor",
        "grade": "D",
        "tags": ["HIGH_LOSS", "SELF_HEATING"],
        "confidence": 0.951,
        "evidence": ["R_mean=368 mΩ (normalized to 1.0 m)", "V_min=4.474 V (target 5.0 V)"],
        "limitations": ["single measurement point"],
    },
    "created_at": "2026-08-05T15:46:43.884186+00:00",
})

# ── Test 6: Single object (not array) for session ──
print("\n=== Test 6: Single object (not wrapped in array) ===")
post("sessions", {
    "session_id": "test-single-006",
    "device_id": DEVICE_ID,
    "mode": "charge",
    "v_target": 5.0,
    "length_m": None,
    "phone_expected": True,
    "started_at": 1755002357.0,
    "ended_at": 1755002392.0,
    "charging_detected": True,
    "v_present": True,
    "fault_reason": None,
    "probe_json": None,
    "verdict_json": None,
    "created_at": "2026-01-01T00:00:00+00:00",
})
