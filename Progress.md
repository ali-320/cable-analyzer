# Progress Check — RADWI Cable Analyzer (Rule-Based Approach)

**Date:** August 2026
**Verified against:**
- `cable-analyzer/src/main.py`
- `cable-analyzer/src/hardware/ch224k.py`
- `cable-analyzer/src/hardware/load_ctrl.py`
- `cable-analyzer/src/telemetry/sampler.py`
- `cable-analyzer/src/telemetry/models.py`
- `cable-analyzer/src/telemetry/storage.py`
- `cable-analyzer/src/features/metrics.py`
- `cable-analyzer/src/analysis/rules.py`

Each row maps one of the seven stated claims to the actual code state.

---

## Claim-by-Claim Verification

| #   | Claim                                                                                                                                                                                 | T/F         | Note                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | First (auto) version with an electronic-load sink and a phone charging path separated by a P-MOSFET and an N-MOSFET, where the code can control CH224K voltage selection from the Pi. | **True**    | `run_probe()` non-manual branch iterates over `current_steps`, calls `load.set_current(...)` / `load.set_current(0.0, enable=False)`, toggles `load.phone_switch(False)` for probing and `load.phone_switch(True)` after; `CH224KController.set_voltage()` writes SEL0/SEL1/SEL2 over RPi.GPIO with the SEL truth table from `config.toml`. The phone/load isolation is implemented by the `LoadController` (P-MOSFET pulls the phone branch high during charging; N-MOSFET pulls the e-load branch in during probe).                                                                                                                                                     |
| 2   | Manual approach: code runs and asks the user to change the CH224K voltages for taking readings.                                                                                       | **True**    | `_manual_voltage_confirmation(target_v)` in `main.py` prints the SEL-pin strap prompt at every step, refuses to continue unless the operator types the target voltage back in, and raises a safe `RuntimeError` on EOF or wrong value. `--manual` switches `ch224k.control_mode = "manual"`; the CH224K controller in manual mode only records the requested voltage — no GPIO writes.                                                                                                                                                                                                                                                                                    |
| 3   | If the provided voltage is not in the range of the requested voltage, the system assumes the charger is not capable of providing that voltage and goes down to recovery 5 V.          | **True**    | `rail_verified()` returns `False` when the post-step INA219 reading is outside the tolerance band; in manual mode the code appends the step to `manual_voltage_mismatch`, prints a warning, and calls `recover_to_5(v, recovery_hold_s)`. I²C negotiation failure (`ch224k.set_voltage` returns False) is handled the same way via the `pd_blocked` branch.                                                                                                                                                                                                                                                                                                               |
| 4   | If the provided voltage is reached but no current flows, the system assumes the phone does not support that voltage and drops to 5 V to verify.                                       | **True**    | Inside `run_probe()` manual branch, when `current_present` is False at a >5 V step the step is appended to `unsupported_voltages`, a message is printed ("No charging current detected at 12 V..."), and `recover_to_5(v, recovery_hold_s)` is invoked to verify behaviour back at 5 V before continuing.                                                                                                                                                                                                                                                                                                                                                                 |
| 5   | If current was flowing at 5 V before but is no longer flowing after returning from 12 V → 5 V, the system assumes the mobile has been fully charged.                                  | **Partial** | `recover_to_5()` records the post-step 5 V window as a `recovery_checks` entry containing `i_mean`, `charging_resumed = (i_mean >= i_charge_start)`, and `n_valid`; if `charging_resumed` is False it prints a `WARNING: charging did not resume at 5 V` and excludes those samples from `quality_samples`. However, **no explicit `FULLY_CHARGED` verdict/tag is produced in manual probe mode** — that verdict only appears in `--mode charge` via `SessionTracker.charged` event. The data is captured but the tag is not.                                                                                                                                             |
| 6   | After taking readings, the live readings are saved in a `.csv` file with a `valid` column.                                                                                            | **True**    | In `main()` after both probe and charge: `storage.add_samples(sid, samples)` and `storage.export_csv(sid, samples)` write every `Sample` dataclass to CSV. The `Sample` schema (`src/telemetry/models.py`) contains `t`, `voltage`, `current`, `power`, `state`, `valid`, `flags`. `valid = read_ok AND validate_sample(v, i, period)` per `src/telemetry/sampler.py`. `valid == True` means the I²C read succeeded AND the reading passed range checks (non-NaN/Inf, V in 0–26 V, I in −1.0..3.5 A, non-zero period). `valid == False` rows are gaps/NaN/out-of-range and are excluded from grading but retained in the CSV for audit (with `flags` showing the reason). |
| 7   | The mathematical calculations are performed on the live data to get other mathematical values.                                                                                        | **True**    | `compute_features()` (`src/features/metrics.py`) derives ~22 features from the `busy` subset of the samples: `R_loop`, `R_cable`, `R_mean`, `R_std`, `R_max`, `R_p95`, `R_p5`, `R_dvdi` (dV/dI least-squares slope), sliding-window detrended `sigma_V`, `dR/dt` (R vs time slope → mΩ/min), `interruption_frac`, `spike_count`, `eta = V_mean / V_target`, `V_min`, `mean_I`, `max_I`, `mean_P_loss`, `E_Wh`, `idle_I`, `valid_frac`, `duration_s`.                                                                                                                                                                                                                      |

---

## Summary Roll-Up

- **Fully-implemented claims:** 1, 2, 3, 4, 6, 7  → **6 of 7**
- **Partially-implemented claims:** 5  → **1 of 7**
- **False claims:** 0

### What is *not* yet there

- An automatic **`FULLY_CHARGED` / `PHONE_FILLED_BEFORE_TEST` verdict** generated from a `recovery_checks` entry where `charging_resumed == False`. Today the warning is printed and the data is logged in `results["recovery_checks"]`, but `rules.py::evaluate()` does not read that signal — it only grades the resistance/spread features. A small addition to `evaluate()` is needed:
  ```text
  for r in meta.get("probe", {}).get("recovery_checks", []):
      if not r["charging_resumed"]:
          tags.append("PHONE_BECAME_FULL")
          verdict = "PHONE_FILLED_DURING_TEST"
  ```

### What is *in place* and stable

- Two-mode architecture (auto / manual) selectable via `--manual`.
- Auto mode runs the full probe + charge sequence with a controlled e-load on the P-MOSFET-disconnected phone branch.
- Manual mode handles every I²C, current, and voltage anomaly with explicit terminal prompts and recovery loops.
- Validated samples are written to `data/raw/<session>.csv` with a full quality column set; invalid/out-of-range rows are kept flagged for audit.
- Feature pipeline runs end-to-end in both simulation and on the Pi.
