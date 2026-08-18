"""Feature extraction — DEVELOPMENT_PLAN.md §5 (17-feature table).

Pure-Python statistics (no numpy) so the code runs anywhere and the math is
easy to audit against the plan:

    R_loop(s) = max(0, (V_target - V_load)/I)      for I >= i_min_compute
    R_cable   = R_loop - R_fixture                 (calibrated baseline)
    dR/dt     = linear-regression slope of R_cable over time  [mΩ/min]
    dV/dI     = linear-regression slope of V_load vs I         [≈ -R]

Voltage-class mode (CH224K removed) buckets readings into real-time voltage
classes (1 V wide by default) so the charger's own voltage is used directly:
4.67 V falls into the 4.5-5.5 V class (center 5), 5.8 V into 5.5-6.5 V
(center 6), and so on. Each class is calculated separately at its own
class-center target and the per-class feature sets are combined for grading.
"""
from __future__ import annotations

import math

from src.telemetry.models import Sample

# Normal quality samples are active charging/probe samples. CHARGED and
# NO_PHONE are intentionally excluded from resistance calculations by current
# threshold; VERIFICATION is filtered separately below.
BUSY_STATES = {"CHARGING", "PROBE", "UNKNOWN", ""}


def mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def percentile(xs: list[float], p: float) -> float:
    """Linear-interpolated percentile (0-100)."""
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def linreg(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Least-squares (slope, intercept, r2); (0, mean_y, 0) if <2 points."""
    n = len(xs)
    if n < 2:
        return 0.0, mean(ys), 0.0
    mx, my = mean(xs), mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my, 0.0
    slope = sxy / sxx
    intercept = my - slope * mx
    syy = sum((y - my) ** 2 for y in ys)
    r2 = (sxy * sxy / (sxx * syy)) if syy > 0 else 0.0
    return slope, intercept, r2


def _busy(samples: list[Sample], i_min: float) -> list[Sample]:
    return [s for s in samples if s.valid and s.current >= i_min and s.state in BUSY_STATES]


def compute_features(
    samples: list[Sample],
    v_target: float,
    r_fixture: float = 0.0,
    length_m: float | None = None,
    i_min: float = 0.10,
    i_no_load: float = 0.05,
    i_no_phone: float = 0.01,
    min_busy_samples: int = 5,
    steady_frac: float = 0.5,
) -> dict | None:
    """Compute the §5 feature set. Returns None if there is not enough
    busy data to trust any resistance estimate.

    Resistance/stability features use the STEADY portion of the window
    (samples at >= steady_frac of the peak busy current) so the phone's
    current ramp-up and taper-down transients do not pollute sigma_V,
    spike_count or R statistics (DEVELOPMENT_PLAN.md §6.1 excludes
    transients).
    """
    # VERIFICATION is a probe-only support-check phase. It may be present in
    # the exported CSV, but it must never contribute to quality calculations.
    analysis_samples = [s for s in samples if s.state != "VERIFICATION"]
    busy_all = _busy(analysis_samples, i_min)
    valid = [s for s in analysis_samples if s.valid]
    if len(busy_all) < min_busy_samples:
        return None

    peak_i = max(s.current for s in busy_all)
    busy = [s for s in busy_all if s.current >= steady_frac * peak_i]
    if len(busy) < min_busy_samples:
        busy = busy_all  # fall back: too little steady data

    r_loop = [max(0.0, (v_target - s.voltage) / s.current) for s in busy]
    r_cable = [max(0.0, r - r_fixture) for r in r_loop]
    vs = [s.voltage for s in busy]
    is_ = [s.current for s in busy]

    ts = [s.t for s in busy]
    slope_v_i, _, _ = linreg(is_, vs)              # V vs I -> slope ≈ -R_loop
    r_dvdi = max(0.0, -slope_v_i - r_fixture) if slope_v_i < 0 else None

    # sigma_V = min detrended std over a sliding ~10 s sub-window of the busy
    # samples. The phone's CC->CV taper is a slow, near-linear V rise: a single
    # fit over the whole session cannot flatten plateau+curve, but a short
    # window on the flat plateau measures pure contact noise. Genuine arcing /
    # bad contacts spike every window and are still detected.
    period = (busy[-1].t - busy[0].t) / max(len(busy) - 1, 1) if len(busy) > 1 else 0.04
    win_len = max(min_busy_samples, int(10.0 / max(period, 1e-6)))
    step = max(win_len // 4, 1)
    best_sigma = float("inf")
    for start in range(0, len(busy) - win_len + 1, step):
        win = busy[start : start + win_len]
        wts = [s.t for s in win]
        wvs = [s.voltage for s in win]
        v_slope_t, v_inter, _ = linreg(wts, wvs)
        resid = [v - (v_inter + v_slope_t * t) for v, t in zip(wvs, wts)]
        best_sigma = min(best_sigma, stdev(resid))
    sigma_v = best_sigma if best_sigma != float("inf") else stdev(vs)

    # heating trend: R_cable vs time -> mΩ/min
    slope_r_t, _, _ = linreg(ts, r_cable)
    d_r_dt = slope_r_t * 60_000.0  # Ω/s -> mΩ/min

    # interruption fraction: share of CHARGING samples inside dips that RECOVER.
    # (The trailing low-current debounce tail before CHARGED never recovers and
    # must not count as an intermittent contact.) Falls back to all valid
    # samples when there is no CHARGING state (probe mode).
    charging = [s for s in analysis_samples if s.valid and s.state == "CHARGING"]
    inter_frac = 0.0
    if charging:
        total = len(charging)
        dips = 0
        idx = 0
        while idx < total:
            if charging[idx].current < i_no_phone:
                start = idx
                while idx < total and charging[idx].current < i_no_phone:
                    idx += 1
                if idx < total:  # run ended with a recovery -> intermittent dip
                    dips += idx - start
            else:
                idx += 1
        inter_frac = dips / total if total else 0.0
    elif valid:
        inter_frac = mean([1.0 if s.current < i_no_phone else 0.0 for s in valid])

    # spikes: |dI/dt| or |dV/dt| exceedance over a ~0.2 s window (averages
    # out ADC noise; a genuine arc is a real multi-mV jump, not 1-sample noise)
    window = max(1, int(0.2 / max(
        analysis_samples[-1].t / max(len(analysis_samples), 1), 1e-6
    )))
    spikes = 0
    for i in range(window, len(valid)):
        a, b = valid[i - window], valid[i]
        dt = max(b.t - a.t, 1e-6)
        if abs(b.current - a.current) / dt > 2.0 or abs(b.voltage - a.voltage) / dt > 0.2:
            spikes += 1

    # Leakage/no-phone current is distinct from a fully charged phone's
    # 0.0xx A maintenance current. Prefer explicit NO_PHONE samples; the
    # numeric fallback keeps probe/manual datasets compatible.
    idle = [s for s in analysis_samples if s.valid and s.state == "NO_PHONE"]
    idle_i = mean([s.current for s in idle]) if idle else None

    energy_j = 0.0
    prev_t = None
    for s in valid:
        if prev_t is not None and s.t >= prev_t:
            energy_j += s.power * (s.t - prev_t)
        prev_t = s.t

    duration = (valid[-1].t - valid[0].t) if len(valid) >= 2 else 0.0

    return {
        # --- resistance ---
        "r_mean": mean(r_cable), # Mean resistance.
        "r_std": stdev(r_cable), # Resistance standard deviation.
        "r_max": max(r_cable), # Maximum resistance.
        "r_p95": percentile(r_cable, 95), # 95th percentile resistance.
        "r_p5": percentile(r_cable, 5), # 5th percentile resistance.
        "r_dvdi": r_dvdi,
        "dV_dI_slope": slope_v_i, 
        "r_loop_mean": mean(r_loop),
        # --- voltage stability / compliance ---
        "sigma_V": sigma_v,
        "V_min": min(vs),
        "eta": (mean(vs) / v_target) if v_target else 0.0,
        # --- current / power ---
        "mean_I": mean(is_),
        "max_I": max(is_),
        "mean_P_loss": mean(i * (v_target - v) for v, i in zip(vs, is_)),
        "E_wh": energy_j / 3600.0,
        # --- dynamics ---
        "dR_dt_mOhm_per_min": d_r_dt,
        "interruption_frac": inter_frac,
        "spike_count": spikes,
        "idle_I": idle_i,
        # --- quality meta ---
        "n_busy": len(busy),
        "n_total": len(analysis_samples),
        "valid_frac": (len(valid) / len(analysis_samples)) if analysis_samples else 0.0,
        "duration_s": duration,
        "v_target": v_target,
        "r_fixture": r_fixture,
        "length_m": length_m,
    }


def voltage_class_center(voltage: float, class_width: float = 1.0) -> float:
    """Real-time class center for a measured voltage (half-up rounding).

    With the default 1 V width: 4.67 V -> 5.0 (class 4.5-5.5 V),
    5.8 V -> 6.0 (class 5.5-6.5 V), 8.7 V -> 9.0 (class 8.5-9.5 V).
    """
    width = max(float(class_width), 1e-6)
    return math.floor(voltage / width + 0.5) * width


def compute_features_by_class(
    samples: list[Sample],
    class_width: float = 1.0,
    *,
    r_fixture: float = 0.0,
    length_m: float | None = None,
    i_min: float = 0.10,
    i_no_load: float = 0.05,
    i_no_phone: float = 0.01,
) -> tuple[dict[float, dict], dict | None]:
    """Bucket valid samples into real-time voltage classes and compute one
    feature set per class (each at its own class-center ``v_target``), then
    combine the class feature sets for grading.

    Returns ``(per_class_features, combined_features)``. ``combined_features``
    is None when no class produced a trustworthy resistance estimate (for
    example when no charging current flowed).
    """
    classes: dict[float, list[Sample]] = {}
    for s in samples:
        if not s.valid or s.voltage <= 0.0:
            continue
        center = voltage_class_center(s.voltage, class_width)
        classes.setdefault(center, []).append(s)

    feature_sets: dict[float, dict] = {}
    for center, group in sorted(classes.items()):
        feat = compute_features(
            group,
            v_target=float(center),
            r_fixture=r_fixture,
            length_m=length_m,
            i_min=i_min,
            i_no_load=i_no_load,
            i_no_phone=i_no_phone,
        )
        if feat is not None:
            feature_sets[center] = feat

    if not feature_sets:
        return feature_sets, None

    # The dominant class (most busy samples) supplies the voltage-specific
    # display fields (V_min, v_target, ...) after combination.
    dominant = max(feature_sets, key=lambda center: feature_sets[center].get("n_busy", 0))
    combined = combine_features(
        list(feature_sets.values()),
        reference_voltage=float(dominant),
    )
    return feature_sets, combined


def combine_features(
    feature_sets: list[dict],
    reference_voltage: float | None = None,
) -> dict | None:
    """Combine independently calculated feature sets from supported voltages.

    Each set must already have been calculated with its own ``v_target``.
    Resistance and quality statistics are combined with sample-count weights;
    resistance spread uses pooled variance so one voltage does not dominate
    merely because its readings have a different voltage baseline.
    """
    sets = [f for f in feature_sets if f]
    if not sets:
        return None

    counts = [max(1, int(f.get("n_busy", 0))) for f in sets]
    total_busy = sum(counts)
    r_mean = sum(n * f["r_mean"] for n, f in zip(counts, sets)) / total_busy
    pooled_variance = sum(
        max(0, n - 1) * f["r_std"] ** 2 + n * (f["r_mean"] - r_mean) ** 2
        for n, f in zip(counts, sets)
    ) / max(total_busy - 1, 1)

    total_samples = sum(max(0, int(f.get("n_total", 0))) for f in sets)
    valid_samples = sum(
        max(0, int(f.get("n_total", 0))) * f.get("valid_frac", 0.0)
        for f in sets
    )
    weighted = lambda key: sum(n * f[key] for n, f in zip(counts, sets)) / total_busy
    slope_sets = [f for f in sets if f.get("r_dvdi") is not None]
    slope_counts = [max(1, int(f.get("n_busy", 0))) for f in slope_sets]
    slope_total = sum(slope_counts)
    r_dvdi = (
        sum(n * f["r_dvdi"] for n, f in zip(slope_counts, slope_sets)) / slope_total
        if slope_sets else None
    )
    slope_values = [f for f in sets if f.get("dV_dI_slope") is not None]
    d_v_d_i_slope = (
        sum(n * f["dV_dI_slope"] for n, f in zip(
            [max(1, int(f.get("n_busy", 0))) for f in slope_values], slope_values
        )) / sum(max(1, int(f.get("n_busy", 0))) for f in slope_values)
        if slope_values else 0.0
    )
    reference = next(
        (f for f in sets if reference_voltage is not None and f.get("v_target") == reference_voltage),
        sets[0],
    )
    idle_sets = [f for f in sets if f.get("idle_I") is not None]
    idle_counts = [max(1, int(f.get("n_total", 0))) for f in idle_sets]
    idle_i = (
        sum(n * f["idle_I"] for n, f in zip(idle_counts, idle_sets)) / sum(idle_counts)
        if idle_sets else None
    )

    return {
        "r_mean": r_mean,
        "r_std": pooled_variance ** 0.5,
        "r_max": max(f["r_max"] for f in sets),
        "r_p95": max(f["r_p95"] for f in sets),
        "r_p5": min(f["r_p5"] for f in sets),
        "r_dvdi": r_dvdi,
        "dV_dI_slope": d_v_d_i_slope,
        "r_loop_mean": sum(n * f["r_loop_mean"] for n, f in zip(counts, sets)) / total_busy,
        "sigma_V": max(f["sigma_V"] for f in sets),
        "V_min": reference["V_min"],
        "eta": weighted("eta"),
        "mean_I": weighted("mean_I"),
        "max_I": max(f["max_I"] for f in sets),
        "mean_P_loss": weighted("mean_P_loss"),
        "E_wh": sum(f["E_wh"] for f in sets),
        "dR_dt_mOhm_per_min": max(f["dR_dt_mOhm_per_min"] for f in sets),
        "interruption_frac": weighted("interruption_frac"),
        "spike_count": sum(f["spike_count"] for f in sets),
        "idle_I": idle_i,
        "n_busy": total_busy,
        "n_total": total_samples,
        "valid_frac": (valid_samples / total_samples) if total_samples else 0.0,
        "duration_s": sum(f["duration_s"] for f in sets),
        "v_target": reference["v_target"],
        "r_fixture": reference["r_fixture"],
        "length_m": reference["length_m"],
    }
