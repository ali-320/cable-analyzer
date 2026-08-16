# Metrics Output Reference

All metrics are calculated by `src/features/metrics.py` from INA219 samples containing voltage (`V`), current (`I`), power (`P`), timestamp (`t`), state, and validity.

## Resistance metrics

| Metric | Represents | Depends on |
|---|---|---|
| `r_mean` | Average cable resistance in ohms. | Measured `V` and `I`, `v_target`, and `r_fixture`. |
| `r_std` | Variation or spread of calculated resistance values. | All calculated cable-resistance values. |
| `r_max` | Maximum calculated cable resistance. | Calculated cable-resistance values. |
| `r_p95` | 95th-percentile cable resistance. | Calculated cable-resistance values. |
| `r_p5` | 5th-percentile cable resistance. | Calculated cable-resistance values. |
| `r_dvdi` | Resistance estimated from the voltage-versus-current slope. | Per-voltage regression of measured `V` against `I`, fixture subtraction, then combination across supported voltages. |
| `dV_dI_slope` | Slope of voltage versus current; normally negative because voltage drops as current increases. | Linear regression using measured `V` and `I`. |
| `r_loop_mean` | Average total path resistance before fixture subtraction. | `v_target`, measured `V` and `I`. |

Core calculations:

```text
R_loop  = (v_target - V_measured) / I
R_cable = max(0, R_loop - r_fixture)
```

## Voltage and efficiency metrics

| Metric | Represents | Depends on |
|---|---|---|
| `sigma_V` | Voltage fluctuation/noise after local trend removal. | Measured voltage over time and timestamps. |
| `V_min` | Minimum measured load voltage. | Measured voltage samples. |
| `eta` | Voltage efficiency: average measured voltage relative to the target voltage. | Average measured voltage and `v_target`. |

```text
eta = average(V_measured) / v_target
```

## Current and power metrics

| Metric | Represents | Depends on |
|---|---|---|
| `mean_I` | Average current during busy samples. | Current samples selected for the measurement window. |
| `max_I` | Maximum current during busy samples. | Current samples selected for the measurement window. |
| `mean_P_loss` | Average power lost in the cable and measurement path. | `(v_target - V_measured) × I`. |
| `E_wh` | Energy measured during the session, in watt-hours. | INA219 power readings and timestamps. |

```text
P_loss = (v_target - V_measured) × I
E_wh   = integral(P_measured × dt) / 3600
```

## Time and fault-behavior metrics

| Metric | Represents | Depends on |
|---|---|---|
| `dR_dt_mOhm_per_min` | Resistance increase or decrease over time; used as a self-heating indicator. | Calculated resistance values and timestamps using linear regression. |
| `interruption_frac` | Fraction of charging samples belonging to current interruptions that later recover. | Current values, `i_no_load`, and sample states. |
| `spike_count` | Number of sudden voltage or current changes. | Consecutive valid samples, voltage/current differences, and timestamps. |
| `idle_I` | Average board/fixture leakage current when no phone is detected. | Valid `NO_PHONE` samples and `i_no_phone_max`. |

## Data-quality and session metadata

| Metric | Represents | Depends on |
|---|---|---|
| `n_busy` | Number of samples used for resistance and busy-state calculations. | Valid busy samples. |
| `n_total` | Total number of collected samples. | All collected samples. |
| `valid_frac` | Fraction of collected samples considered valid. | Number of valid samples divided by total samples. |
| `duration_s` | Measurement duration in seconds. | Timestamps of the first and last valid samples. |
| `v_target` | Voltage requested for the test. | Function input from the configured test voltage. |
| `r_fixture` | Calibrated resistance of the non-cable measurement path. | Function input/configuration from calibration. |
| `length_m` | Cable length used for resistance normalization. | Function input/configuration. |

## Important notes

- Resistance metrics are calculated only from valid busy samples with current at or above `i_min`.
- `r_fixture` represents connectors, PCB traces, switches, wiring, and the CH224K path; it is subtracted to estimate cable-only resistance.
- The INA219 measures voltage, current, and power. The remaining metrics are calculated by software from those readings.
- A calibrated `r_fixture` value is needed for accurate cable-resistance results; it is not automatically measured separately by the INA219.
