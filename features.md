# Features Reference — `compute_features()`

**Source:** `cable-analyzer/src/features/metrics.py::compute_features(...)`

This file documents every engineered feature produced by the rule-based cable analyzer. The function receives a list of `Sample` objects (each: `t, voltage, current, power, state, valid, flags`) collected from the INA219 over the probe or charging session and returns a flat dictionary of features used by `rules.py::evaluate()` and the verdict pipeline.

The return dict contains **26 keys total** — **22 computed/engineered features** + **4 pass-through metadata fields**. Minimum data requirement: `len(busy_all) ≥ min_busy_samples` (default **5**); below this, the function returns `None` and no grade is produced.

---

## Pre-computation setup (shared across all features)

For every sample in the session, the state labels already include any confirmed retroactive debounce corrections. The function then establishes three working lists:

| Working variable | Definition |
|------------------|-----------|
| `analysis_samples` | all samples except `VERIFICATION`; verification rows remain in the CSV but are excluded from feature calculations | 
| `busy_all` | non-`VERIFICATION` samples where `valid == True` AND `current ≥ i_min_compute` (default `0.10 A`) AND `state ∈ {"IDLE", "CHARGING", "PROBE", "UNKNOWN", ""}` |
| `busy` (steady-only) | subset of `busy_all` where `current ≥ steady_frac · peak_i` (`steady_frac = 0.5`) — used for stability features so the phone's current ramp-up / CC→CV taper-down do not pollute them |
| `valid` | valid non-`VERIFICATION` samples (range-checked I²C reads); verification rows remain available in the CSV but are excluded from features |

Per-sample derived quantities (used in #1–8 below):

```text
peak_i          = max(current[i])    over busy_all
r_loop[i]       = max(0, (v_target − voltage[i]) / current[i])      (per busy sample)
r_cable[i]      = max(0, r_loop[i] − r_fixture)                     (per busy sample)
```

Inputs to the function (from `config.toml` / CLI):

- `v_target`: rail voltage requested this step (`5.0` for the quality dataset)
- `r_fixture`: calibrated board/baseline resistance in **ohms** (from `data/calibration.json` if present, else `measurement.r_fixture_ohm` in `config.toml`)
- `length_m`: cable length in metres (optional — used only for per-metre normalisation in `rules.py`, not in this function)
- `i_min_compute = 0.10 A`
- `i_no_phone_max = 0.010 A` for no-phone leakage; the connected/full-phone band ends at `i_fully_charged_max = 0.100 A`
- `min_busy_samples = 5`
- `steady_frac = 0.5`

---

## A. Resistance statistics (8 features)

### 1. `r_mean` — mean baseline-subtracted cable resistance

```text
r_mean = (1/N) · Σᵢ r_cable[i]                  for i = 1..N over busy samples
        = (1/N) · Σᵢ max(0, (v_target − V[i])/I[i] − r_fixture)
```

Units: Ω (ohms). Reported to the rule engine in mΩ via `r_mean * 1000`.

### 2. `r_std` — resistance standard deviation

```text
r_std = sqrt( (1/(N − 1)) · Σᵢ (r_cable[i] − r_mean)² )
```

Units: Ω.

### 3. `r_max` — worst-case (peak) resistance

```text
r_max = max(r_cable[i])                          over busy samples
```

### 4. `r_p95` — 95th-percentile resistance

```text
r_p95 = percentile(r_cable, 95)

where, for a sorted list s of r_cable values:
    k      = (N − 1) · 0.95
    lo     = floor(k)
    hi     = min(lo + 1, N − 1)
    r_p95  = s[lo] + (s[hi] − s[lo]) · (k − lo)
```

### 5. `r_p5` — 5th-percentile resistance (best-case contact)

```text
r_p5  = percentile(r_cable, 5)                  (same interpolation as r_p95 with p=0.05)
```

### 6. `r_dvdi` — resistance estimated from voltage-vs-current regression

Linear least-squares fit `V = a + b·I` over the busy samples. The slope `b` should be negative for a resistive load (more current → more droop). The code returns the magnitude:

```text
slope_b = Σ((I[i] − Ī)·(V[i] − V̄))  /  Σ((I[i] − Ī)²)
r_dvdi  = max(0, −slope_b)   if slope_b < 0
r_dvdi  = None               if slope_b ≥ 0
```

Units: Ω. Independent of `r_fixture` subtraction — a corroborating measurement of resistance from the V/I slope rather than from a per-sample Ohm's-law division.

### 7. `dV_dI_slope` — raw V-vs-I regression slope

```text
dV_dI_slope = slope_b    (the raw value from #6, may be positive or negative; V/I units ≈ Ω)
```

### 8. `r_loop_mean` — mean loop resistance (R_fixture **not** subtracted)

```text
r_loop_mean = (1/N) · Σᵢ max(0, (v_target − V[i]) / I[i])
```

This is the raw loop value before baseline subtraction; useful when `r_fixture` is unknown.

---

## B. Voltage stability / compliance (3 features)

### 9. `sigma_V` — minimum detrended voltage noise over a sliding ~10 s window

The phone's CC→CV taper is a slow, near-linear V rise; one global fit cannot flatten plateau + curve, so a short sliding window measures pure contact noise on the flat plateau.

```text
period      = (busy[−1].t − busy[0].t) / max(N − 1, 1)     # mean sample interval
win_len     = max(min_busy_samples, round(10 / max(period, 1e-6)))   # ~ 10 s window
step        = max(win_len // 4, 1)                         # 25 % hop between windows

for each window start = 0, step, 2·step, ... while start + win_len ≤ N:
    fit V = c + d·t over the window's (t[i], V[i])
    residuals[i] = V[i] − (c + d · t[i])
    σ = std(residuals)                                     # using the N−1 formula
best_sigma = min(σ over all windows)

sigma_V = best_sigma         if best_sigma is finite
sigma_V = std(vs over busy)  otherwise (fallback)
```

Units: V (volts). Used by the rule engine for "unstable contact" detection (threshold `sigma_v_marginal_mv`, default 25 mV).

### 10. `V_min` — worst sag of VBUS

```text
V_min = min(V[i])          over busy samples
```

Units: V. The deepest dip seen during the probed voltage step.

### 11. `eta` — voltage-delivered / voltage-requested

```text
eta = ((1/N) · Σᵢ V[i]) / v_target
```

Unitless, `[0, 1]`. `eta = 1.0` means no droop at the load; smaller values indicate lossy cables, contact resistance, or charger undershoot.

---

## C. Current / power (4 features)

### 12. `mean_I` — mean current over the busy window

```text
mean_I = (1/N) · Σᵢ I[i]       over busy samples
```

Units: A (amperes).

### 13. `max_I` — peak current seen during the window

```text
max_I = max(I[i])              over busy samples
```

Units: A.

### 14. `mean_P_loss` — mean resistive power loss in the test path

```text
mean_P_loss = (1/N) · Σᵢ I[i] · (v_target − V[i])             over busy samples
```

Units: W (watts). This is the average heat dissipated by the test cable, connectors, CH224K board path, and the INA219 shunt; subtracting the calibrated `r_fixture` baseline in `rules.py` makes it an estimate of the cable's own loss.

### 15. `E_wh` — energy delivered during the session (watt-hours)

Trapezoidal integration of instantaneous power, then unit conversion:

```text
E_J    = Σᵢ ((V[i] · I[i]) · Δt)         over all valid non-VERIFICATION samples
       = Σᵢ (power[i] · (t[i] − t[i−1]))
E_wh   = E_J / 3600
```

Units: Wh (watt-hours). The integrator walks the full non-VERIFICATION timeline, including valid low-current measurement samples but excluding support-check readings.

---

## D. Dynamics / defects (5 features)

### 16. `dR_dt_mOhm_per_min` — resistance drift over time (self-heating)

Linear least-squares slope of `r_cable` vs `t` over the busy samples, scaled to mΩ/min:

```text
slope_r_t = Σ((t[i] − t̄)·(r_cable[i] − r_c̄))  /  Σ((t[i] − t̄)²)     (Ω / s)
dR_dt_mOhm_per_min = slope_r_t · 60 000       (Ω/s  →  mΩ/min)
```

A positive value means the cable is heating up during the test (resistance rises with temperature due to copper's positive temp-coefficient). Threshold for the `SELF_HEATING` tag: `drdt_self_heating_mohm_per_min`, default 5 mΩ/min.

### 17. `interruption_frac` — share of charging samples that are inside intermittent dips

```text
charging = [s for s in analysis_samples if s.valid AND s.state == "CHARGING"]

if charging is non-empty:
    walk charging left-to-right, count every sample that lies in a dip
    where current < i_no_phone_max (default 0.010 A)
    AND the dip recovers back above i_no_phone_max before the run ends
    dips = # such samples
    interruption_frac = dips / len(charging)
else:                                               # probe mode (no CHARGING run)
    interruption_frac = mean(1.0 if current < i_no_phone_max else 0.0)      over valid samples
```

Trailing low-current debounce tail before a CHARGED transition is excluded because the run does not recover.

### 18. `spike_count` — number of voltage / current excursions

Sliding window over `~0.2 s` of valid samples:

```text
period_avg = (samples[−1].t) / max(len(samples), 1)
window     = max(1, round(0.2 / max(period_avg, 1e-6)))               # sample count

for i in range(window, len(valid)):
    a, b = valid[i − window], valid[i]
    dt   = max(b.t − a.t, 1e-6)
    if |b.current − a.current| / dt  >  2.0 A/s :
        spikes += 1
    elif |b.voltage − a.voltage| / dt  >  0.2 V/s :
        spikes += 1
spike_count = spikes
```

Unitless. Genuine arc / bad-contact events are typically 5–50 mV / 0.1–1 A instantaneous jumps; ADC jitter is multi-millisecond noise, not sub-second excursions.

### 19. `idle_I` — mean current of true-IDLE samples

```text
idle       = [s for s in analysis_samples if s.valid AND (s.state == "NO_PHONE" OR (s.state == "IDLE" AND s.current < i_no_phone))]
idle_I     = mean(I[s])        if idle is non-empty
idle_I     = None              otherwise
```

Units: A. Captures the genuine no-load leakage current of the cable once the phone is detected as CHARGED. Threshold for the `LEAKY` tag: `idle_leak_ma`, default 20 mA.

### 20. `n_busy` — number of busy samples that fed the resistance statistics

```text
n_busy = len(busy)
```

Unitless. The dividing line between "trusted grade" and "no grade" is `n_busy ≥ min_busy_samples` (default 5).

---

## E. Quality meta (2 features)

### 21. `valid_frac` — proportion of valid I²C reads

```text
valid_frac = len(valid) / len(analysis_samples)
```

Unitless `[0, 1]`. A `valid` row means `read_ok == True AND validate_sample(...)` returned OK (non-NaN/Inf, V in `[0, 26]` V, I in `[-1.0, 3.5]` A, non-zero period). Used in the confidence formula in `rules.py::evaluate()`.

### 22. `duration_s` — span of valid data (seconds)

```text
duration_s = valid[−1].t − valid[0].t       if len(valid) ≥ 2
duration_s = 0.0                            otherwise
```

Units: s. Used as session-length context; the 10-second sliding window used by `sigma_V` is computed relative to this span (and the sample period derived from it).

---

## Pass-through metadata fields (not computed, included in the dict)

| Key         | Source                           | Purpose                                                             |
| ----------- | -------------------------------- | ------------------------------------------------------------------- |
| `v_target`  | argument to `compute_features()` | Echoed so the verdict can show "target 5.0 V"                       |
| `r_fixture` | argument to `compute_features()` | Echoed so the verdict can show the baseline that was subtracted     |
| `length_m`  | argument to `compute_features()` | Optional per-metre normalisation in `rules.py::evaluate()`          |
| `n_total`   | `len(analysis_samples)`          | Total non-VERIFICATION sample count, valid + invalid — used for confidence weighting |

These four are not engineered features; they are bookkeeping fields the verdict engine expects to see in the same dict.

---

## Confidence formula (consumer of these features)

`rules.py::evaluate()` consumes `r_mean`, `r_std`, `r_dvdi` and `valid_frac` to compute confidence:

```text
conf  = valid_frac · max(0, 1 − r_std / max(r_mean, 1e-6))
if r_dvdi is not None AND r_mean > 0:
    agree = 1 − min(1, |r_mean − r_dvdi| / max(r_mean, 0.02))
    conf  = 0.6 · conf  +  0.4 · max(0, agree)
confidence = clip(conf, 0, 1)
```

So the engineered features 1, 2, 6, 21, and 22 directly drive what `verdict.confidence` reports.

---

## Quick reference table

| # | Key | Formula (short form) | Units |
|---|-----|----------------------|-------|
| 1 | `r_mean` | `mean(max(0, (v_target − V)/I − r_fixture))` | Ω |
| 2 | `r_std` | `std(r_cable)` | Ω |
| 3 | `r_max` | `max(r_cable)` | Ω |
| 4 | `r_p95` | `percentile(r_cable, 95)` | Ω |
| 5 | `r_p5`  | `percentile(r_cable, 5)`  | Ω |
| 6 | `r_dvdi` | `max(0, −slope(V vs I))` | Ω |
| 7 | `dV_dI_slope` | raw slope of V vs I | Ω |
| 8 | `r_loop_mean` | `mean(max(0, (v_target − V)/I))` | Ω |
| 9 | `sigma_V` | `min` std of detrended V over sliding ~10 s windows | V |
| 10 | `V_min` | `min(V)` over busy | V |
| 11 | `eta` | `mean(V) / v_target` | — |
| 12 | `mean_I` | `mean(I)` over busy | A |
| 13 | `max_I`  | `max(I)`  over busy | A |
| 14 | `mean_P_loss` | `mean(I · (v_target − V))` | W |
| 15 | `E_wh` | `(Σ V·I·Δt) / 3600` | Wh |
| 16 | `dR_dt_mOhm_per_min` | `(slope(r_cable vs t) [Ω/s]) · 60 000` | mΩ/min |
| 17 | `interruption_frac` | dips/CHARGING (or fallback over valid) | fraction |
| 18 | `spike_count` | exceedances of 2 A/s or 0.2 V/s over 0.2 s windows | — |
| 19 | `idle_I` | `mean(I)` over `NO_PHONE` samples (or legacy low-current fallback) | A |
| 20 | `n_busy` | `len(busy)` | samples |
| 21 | `valid_frac` | `len(valid) / len(analysis_samples)` | fraction |
| 22 | `duration_s` | `valid[−1].t − valid[0].t` | s |
