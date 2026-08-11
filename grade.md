# Cable Grading Reference — `grade.md`

This document explains the grades produced by the rule-based verdict engine in `cable-analyzer/src/analysis/rules.py`, the meaning of each grade, and which engineered features (numbered as in `cable-analyzer/features.md`) shape the grade letter and the defect tags.

> **No code was changed by this document.** It is a reference compiled from the existing engine and feature definitions.

---

## 1. Final grades given at the end of a verdict

The engine emits one of **five letter grades** when a trustworthy measurement is available. They are defined in `src/analysis/rules.py`:

```python
GRADE_ORDER  = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}
GRADE_LABELS = {"A": "Excellent", "B": "Good", "C": "Marginal", "D": "Poor", "F": "Failed"}
```

| Grade | Label     |
|:-----:|:----------|
| **A** | Excellent |
| **B** | Good      |
| **C** | Marginal  |
| **D** | Poor      |
| **F** | Failed    |

The boundary between letters is the **per-metre cable resistance** (after the calibrated fixture baseline is subtracted). The default thresholds live in `cable-analyzer/config.toml` under `[rules]` and are mirrored as `DEFAULT_LIMITS_MOHM` in `rules.py`:

```text
grade_limits_mohm = [150, 250, 300, 500]   mΩ/m   →   A | B | C | D | F
```

So, for a cable whose length is known:

| Resistance (mΩ/m, calibrated) | Grade |
|------------------------------:|:------|
| `< 150`                       | **A** |
| `< 250`                       | **B** |
| `< 300`                       | **C** |
| `< 500`                       | **D** |
| `≥ 500`                       | **F** |

When the cable length is unknown the result is reported at the **raw loop value** (not per metre) and the verdict's `limitations` list records this explicitly.

In addition to the A–F letters, the engine can return three **non-graded** verdicts for pre-grade special cases. These carry no letter:

| Verdict       | Meaning                                                      |
|---------------|--------------------------------------------------------------|
| `FAULT/ABORT` | Safety guard tripped (over-current, fault flag, …) → `F`.    |
| `NO_SOURCE`   | No bus voltage measured (charger absent or VBUS open).       |
| `OPEN`        | Voltage present but no current ever flowed (phone expected). |
| `NO_CHARGE`   | Voltage present but no charging current (phone not drawing). |

---

## 2. One-line description of what each grade tells about the cable

| Grade | Tells you about the cable |
|:-----:|:--------------------------|
| **A — Excellent** | Cable meets strict, premium-quality expectations: very low calibrated resistance, clean contact, and no defects. |
| **B — Good**      | Cable is above the marginal line and is fine for general use, but it is not quite premium. |
| **C — Marginal**  | Cable just barely passes — borderline quality that warrants caution or downgrade pressure. |
| **D — Poor**      | Cable is below acceptable limits — it still works but with notable loss or instability. |
| **F — Failed**    | Cable is unusable, either because its resistance is excessive or because a hard safety/abort condition was hit. |

---

## 3. Tags and features that play a role in choosing the grade

The grade letter is chosen in `rules.py::evaluate()` from a small subset of features. The numbering is exactly as listed in `cable-analyzer/features.md`.

### 3.1 Features that set the base grade letter

Only one feature, on its own, sets the base letter. Everything else either *downgrades* the letter or attaches a diagnostic tag.

| Step               | Feature # (features.md) | Key                                  | Why it matters |
|--------------------|:-----------------------:|--------------------------------------|----------------|
| Base grade letter  | **#1**                 | `r_mean`                             | Mean baseline-subtracted cable resistance (Ω, converted to mΩ and per-metre normalized if `length_m` is given). Compared against `grade_limits_mohm = [150, 250, 300, 500]`. This is the **only** feature that sets the base letter. |

### 3.2 Features that can override (downgrade) the grade letter

The overrides live in `_force_grade()` and follow a *worst-of* rule: they can only push the grade down, never improve it.

| Override | Feature # | Key                              | Threshold (default)                  | Effect                | Tag attached       |
|----------|:---------:|----------------------------------|--------------------------------------|-----------------------|--------------------|
| Downgrade to ≥ **C** | **#9** | `sigma_V`                        | `sigma_v_marginal_mv = 25 mV`         | forces grade to at least **C** | `UNSTABLE_CONTACT` |
| Downgrade to ≥ **D** | **#16** | `dR_dt_mOhm_per_min`            | `drdt_self_heating_mohm_per_min = 5 mΩ/min` | forces grade to at least **D** | `SELF_HEATING`     |

### 3.3 Features that drive defect tags (do not change the letter themselves)

| Tag                              | Feature(s) # | Driving feature(s) | Trigger (from `config.toml` rules) |
|----------------------------------|:------------:|--------------------|--------------------------------------|
| `INTERMITTENT`                   | **#17** and/or **#18** | `interruption_frac`, `spike_count` | either `interruption_frac ≥ interruption_warn_frac` (default **1 %**) **or** `spike_count > spike_count_warn` (default **10**) |
| `LEAKY`                          | **#19**     | `idle_I`           | `idle_I × 1000 > idle_leak_ma` (default **20 mA**) of true-IDLE samples |
| `NON_LINEAR`                     | **#4** and **#5** | `r_p95`, `r_p5` | `(r_p95 − r_p5) / r_mean > nonlinear_ratio` (default **0.30**) |
| `HIGH_LOSS`                      | — (grade-derived) | — | Auto-applied whenever the final grade is **D** or **F** |
| `PD_BLOCKED`                     | — (probe dict) | — | CH224K could not negotiate a USB-PD contract at the listed voltage(s) |
| `VOLTAGE_MISMATCH`               | — (probe dict) | — | A manual voltage step set by the user did not match the INA219 reading |
| `FAULT`                          | — (safety) | — | A hardware safety/abort fault tripped (`fault_reason` set in `meta`) |
| `NO_SOURCE` / `OPEN` / `NO_CHARGE` | — (meta) | — | No usable charging data was produced (no letter is returned) |

### 3.4 Features that drive confidence (only `verdict.confidence`, never the letter)

`rules.py::evaluate()` reports `verdict.confidence` from **features #1 `r_mean`, #2 `r_std`, #6 `r_dvdi`, #21 `valid_frac`** (plus the implicit sample-span check). See the *Confidence formula* section at the bottom of `features.md`:

```text
conf  = valid_frac · max(0, 1 − r_std / max(r_mean, 1e-6))
if r_dvdi is not None and r_mean > 0:
    agree = 1 − min(1, |r_mean − r_dvdi| / max(r_mean, 0.02))
    conf  = 0.6 · conf + 0.4 · max(0, agree)
confidence = clip(conf, 0, 1)
```

---

## Summary in one sentence

**The base letter is set only by feature #1 (`r_mean`) against the four mΩ/m limits in `config.toml`; features #9 (`sigma_V`) and #16 (`dR_dt_mOhm_per_min`) are the only ones that can override it downward; and features #4/#5, #17, #18, and #19 only contribute diagnostic tags, never the grade itself.**

---

## Code references

- **Grading logic:** `cable-analyzer/src/analysis/rules.py`
  - `GRADE_ORDER`, `GRADE_LABELS`, `DEFAULT_LIMITS_MOHM`
  - `grade_from_r(r_mohm, limits)` — base letter selection
  - `_force_grade(current, forced)` — worst-of safety override
  - `evaluate(features, meta, cfg)` — full verdict pipeline (terms: `verdict`, `grade`, `tags`, `confidence`, `evidence`, `limitations`)
- **Thresholds:** `cable-analyzer/config.toml` under `[rules]`
  - `grade_limits_mohm`
  - `sigma_v_marginal_mv`
  - `drdt_self_heating_mohm_per_min`
  - `interruption_warn_frac`
  - `spike_count_warn`
  - `idle_leak_ma`
  - `nonlinear_ratio`
- **Feature definitions & numbering:** `cable-analyzer/features.md` and `cable-analyzer/src/features/metrics.py::compute_features()`
