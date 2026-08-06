"""Rule-based verdict engine — DEVELOPMENT_PLAN.md §6 (Approach A).

Deterministic, explainable, no labels needed. Safety checks run first;
then the cable is graded from its (baseline-subtracted) resistance and
tagged with defects. Every verdict carries evidence strings and a
confidence value.
"""
from __future__ import annotations

GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}
GRADE_LABELS = {"A": "Excellent", "B": "Good", "C": "Marginal", "D": "Poor", "F": "Failed"}

DEFAULT_LIMITS_MOHM = [150.0, 250.0, 300.0, 500.0]  # A | B | C | D | F (per 1 m)


def grade_from_r(r_mohm: float, limits: list[float]) -> tuple[str, str]:
    for idx, limit in enumerate(limits):
        if r_mohm < float(limit):
            letter = "ABCD"[idx] if idx < 4 else "F"
            return letter, GRADE_LABELS[letter]
    return "F", GRADE_LABELS["F"]


def _force_grade(current: str, forced: str) -> str:
    """Worst-of grade override (safety overrides never improve a grade)."""
    if GRADE_ORDER.get(forced, 0) > GRADE_ORDER.get(current, 0):
        return forced
    return current


def evaluate(
    features: dict | None,
    meta: dict,
    cfg: dict,
) -> dict:
    """Produce the verdict dict described in DEVELOPMENT_PLAN.md §6.

    ``meta`` keys: session_id, mode, v_present, charging_detected,
    phone_expected, fault_reason, probe (dict with probe results).
    """
    base = {
        "session_id": meta.get("session_id", ""),
        "model": "rules-v1",
        "verdict": "",
        "grade": None,
        "tags": [],
        "confidence": 0.0,
        "evidence": [],
        "limitations": [
            "single measurement point; loop R includes connectors and the CH224K path (baseline subtracted)",
            "absolute R biased by charger tolerance (+/-5%); same-current differential calibration mitigates",
        ],
    }

    # --- 1. safety ---
    fault = meta.get("fault_reason")
    if fault:
        base.update(
            verdict="FAULT/ABORT",
            grade="F",
            tags=["FAULT"],
            confidence=1.0,
            evidence=[f"safety: {fault}"],
        )
        return base

    # --- 2. no/insufficient charging data ---
    if features is None:
        if not meta.get("v_present"):
            base.update(
                verdict="NO_SOURCE",
                tags=["NO_SOURCE"],
                evidence=["no bus voltage measured - charger absent or cable VBUS open"],
            )
        elif meta.get("phone_expected"):
            base.update(
                verdict="OPEN",
                tags=["OPEN"],
                evidence=["voltage present but current never rose while a phone was expected"],
            )
        else:
            base.update(
                verdict="NO_CHARGE",
                tags=["NO_CHARGE"],
                evidence=["voltage present but no charging current observed (phone not attached or battery full)"],
            )
        return base

    rules = cfg.get("rules", {})
    limits = [float(x) for x in rules.get("grade_limits_mohm", DEFAULT_LIMITS_MOHM)]
    sigma_marginal = float(rules.get("sigma_v_marginal_mv", 25.0))
    drdt_heating = float(rules.get("drdt_self_heating_mohm_per_min", 5.0))
    inter_warn = float(rules.get("interruption_warn_frac", 0.01))
    spike_warn = float(rules.get("spike_count_warn", 10))
    leak_ma = float(rules.get("idle_leak_ma", 20.0))
    nonlinear = float(rules.get("nonlinear_ratio", 0.30))

    # --- 3. grade (per-metre normalization) ---
    length_m = meta.get("length_m") or features.get("length_m")
    r_mean_mohm = features["r_mean"] * 1000.0
    if length_m and length_m > 0:
        r_norm = r_mean_mohm / float(length_m)
        norm_note = f" (normalized to {length_m} m)"
    else:
        r_norm = r_mean_mohm
        norm_note = " (raw loop value, length unknown)"
        if "length unknown - reported at raw loop value (not per metre)" not in base["limitations"]:
            base["limitations"].append("length unknown - reported at raw loop value (not per metre)")

    grade, label = grade_from_r(r_norm, limits)
    evidence = [
        f"R_mean={r_mean_mohm:.0f} mΩ{norm_note}",
        f"V_min={features['V_min']:.3f} V (target {features['v_target']:.1f} V)",
        f"eta={features['eta'] * 100:.1f}%",
    ]
    tags: list[str] = []

    # --- 4. defect tags + stability overrides ---
    sigma_mv = features["sigma_V"] * 1000.0
    if sigma_mv >= sigma_marginal:
        grade = _force_grade(grade, "C")
        tags.append("UNSTABLE_CONTACT")
        evidence.append(f"sigma_V={sigma_mv:.1f} mV >= {sigma_marginal:.0f} mV (unstable contact)")

    drdt = features["dR_dt_mOhm_per_min"]
    if drdt > drdt_heating:
        grade = _force_grade(grade, "D")
        tags.append("SELF_HEATING")
        evidence.append(f"dR/dt=+{drdt:.2f} mΩ/min (self-heating)")

    if features["interruption_frac"] >= inter_warn or features["spike_count"] > spike_warn:
        tags.append("INTERMITTENT")
        evidence.append(
            f"interruptions={features['interruption_frac'] * 100:.2f}%, spikes={features['spike_count']}"
        )

    if grade in ("D", "F"):
        tags.append("HIGH_LOSS")

    probe = meta.get("probe") or {}
    pd_blocked = probe.get("pd_blocked") or []
    if pd_blocked:
        tags.append("PD_BLOCKED")
        evidence.append(f"PD negotiation failed at: {pd_blocked} V (cable blocks CC/PD signaling)")

    idle_i = features.get("idle_I")
    if idle_i is not None and idle_i * 1000.0 > leak_ma:
        tags.append("LEAKY")
        evidence.append(f"idle current={idle_i * 1000:.1f} mA (leakage suspected)")

    r_mean = features["r_mean"]
    if r_mean > 0 and (features["r_p95"] - features["r_p5"]) / r_mean > nonlinear:
        tags.append("NON_LINEAR")
        evidence.append("R spread across load steps > 30% of mean (non-linear contact)")

    # --- 5. confidence ---
    conf = features["valid_frac"] * max(0.0, 1.0 - features["r_std"] / max(r_mean, 1e-6))
    if features.get("r_dvdi") is not None and r_mean > 0:
        agree = 1.0 - min(1.0, abs(r_mean - features["r_dvdi"]) / max(r_mean, 0.02))
        conf = 0.6 * conf + 0.4 * max(0.0, agree)
    confidence = max(0.0, min(1.0, conf))

    base.update(
        verdict=f"Grade {grade}: {label}",
        grade=grade,
        tags=sorted(set(tags)),
        confidence=round(confidence, 3),
        evidence=evidence,
    )
    return base
