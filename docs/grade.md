# Cable Grading Reference — `grade.md`

How the verdict engine (`src/analysis/rules.py`) turns measurements into a
grade. Thresholds live in `config.toml` under `[rules]`.

---

## 1. Grades

Per-metre cable resistance (fixture baseline subtracted), compared against
`grade_limits_mohm = [150, 250, 300, 500]`:

| Grade | Resistance (mΩ/m) | Meaning |
|:-----:|-------------------|---------|
| **A — Excellent** | < 150 | Premium quality: very low loss, clean contacts, no defects. |
| **B — Good** | 150 – 250 | Fine for general use, not quite premium. |
| **C — Marginal** | 250 – 300 | Barely passes; borderline quality. |
| **D — Poor** | 300 – 500 | Works but with notable loss or instability. |
| **F — Failed** | ≥ 500 | Unusable — excessive resistance or a safety abort. |

Non-graded verdicts (no letter): `FAULT/ABORT`, `NO_SOURCE`, `OPEN`, `NO_CHARGE` —
no usable charging data was produced.

---

## 2. Tags

Diagnostic labels attached to the verdict; they never change the grade letter
(except where noted).

| Tag | Meaning | Trigger (defaults) |
|-----|---------|--------------------|
| `HIGH_LOSS` | Resistance in grade D/F territory | Auto-applied whenever the final grade is D or F |
| `UNSTABLE_CONTACT` | Voltage noise on the bus plateau | `sigma_V ≥ 25 mV` — also forces grade ≥ C |
| `SELF_HEATING` | Resistance rises while charging | `dR/dt > 5 mΩ/min` — also forces grade ≥ D |
| `INTERMITTENT` | Dropouts or current spikes | `interruption_frac ≥ 1%` or `spike_count > 10` |
| `LEAKY` | Current flows with no phone attached | `idle_I > 20 mA` |
| `NON_LINEAR` | Resistance varies with current (bad contact) | `(r_p95 − r_p5) / r_mean > 0.30` |
| `PD_BLOCKED` | Charger could not negotiate a voltage | Probe: PD contract failed (CH224K builds only) |
| `VOLTAGE_MISMATCH` | Set voltage did not match measured rail | Manual-mode check |
| `FAULT` | Safety guard tripped | Over-current / hardware abort |

---

## 3. Features that affect the decision

| Feature | Role in the verdict |
|---------|---------------------|
| `r_mean` | **The only feature that sets the base grade letter** (vs. the four mΩ/m limits). |
| `sigma_V` | Can only downgrade — forces grade ≥ C (`UNSTABLE_CONTACT`). |
| `dR/dt` | Can only downgrade — forces grade ≥ D (`SELF_HEATING`). |
| `interruption_frac`, `spike_count` | Add the `INTERMITTENT` tag only. |
| `idle_I` | Adds the `LEAKY` tag only. |
| `r_p95`, `r_p5` | Add the `NON_LINEAR` tag only. |
| `r_std`, `n_busy`, `valid_frac` | Drive **confidence** (how much we trust the grade), never the letter. |
| `r_dvdi` | Cross-check against `r_mean`; agreement raises confidence. |
