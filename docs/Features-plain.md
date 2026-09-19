# Features — Plain-Language Reference

The analyzer turns a session of raw voltage/current readings into **22 features**. They are computed in `src/features/metrics.py` and consumed by the verdict engine in `src/analysis/rules.py`.

Throughout this file, an **active-charging sample** (called "busy" in the code) is a valid reading taken while the phone was actually drawing charging current (at or above the configured charging-current threshold). Features that describe cable quality are only computed from these active samples; idle and verification samples never affect the grade.

*(Formula-level details: see `docs/features.md`.)*

---

## Resistance features

1. **`r_mean`** — The average cable resistance. For every active-charging sample, the resistance of the whole loop is estimated from the measured voltage drop (uses linear regression) and current (Ohm's law), the  fixture  resistance (connectors, wiring, sensor path) is subtracted, negative values are clamped to zero, and the per-sample values are then averaged. This is the main number the grade is based on.

2. **`r_std`** — It shows how much the resistance estimate fluctuates from sample to sample; a loose or failing contact makes it large.

3. **`r_max`** — The highest single-sample cable resistance observed during the session. It captures the worst moment, such as a momentary bad contact.

4. **`r_p95`** — The 95th percentile of the per-sample resistance: the value that 95% of samples fall below. Like `r_max` but insensitive to one-off spikes.

5. **`r_p5`** — The 5th percentile of the per-sample resistance: the best-case resistance seen during the session. Compared against `r_p95` it reveals how non-linear or erratic the contact behaves.

6. **`r_dvdi`** — A second, independent resistance estimate taken from the slope of the voltage-versus-current relationship: as the phone draws more current, the voltage sags, and how fast it sags is the cable resistance. When the phone holds voltage nearly constant (little current variation), this estimate falls back to the `r_mean`-style calculation. The verdict engine uses it to cross-check `r_mean` — when both agree, confidence rises.

7. **`dV_dI_slope`** — The raw slope of voltage versus current, before any baseline subtraction. For a normal resistive cable it is negative (more current → lower voltage). Reported for diagnostics.

8. **`r_loop_mean`** — The mean resistance of the entire charging loop *without* subtracting the fixture baseline. It includes the connectors, wiring, and sensor path, and is useful when no calibration baseline exists.

## Voltage stability features

9. **`sigma_V`** — The smallest voltage noise found in any sliding ~10-second window of active charging. Within each window the slow overall drift is fitted and removed, and the scatter of what remains is measured; the quietest window wins. This isolates pure contact noise from the phone's normal charging behavior — genuine unstable contacts look bad in every window. Large values trigger the `UNSTABLE_CONTACT` tag.

10. **`V_min`** — The lowest voltage measured at the phone during active charging: the deepest sag the cable produced under load.

11. **`eta`** — Voltage delivery efficiency: the average voltage the phone actually received, divided by the source (target) voltage. 1.0 would mean no drop at all; lower values mean more of the charger's voltage is being lost in the cable.

## Current and power features

12. **`mean_I`** — The average current the phone drew during active charging.

13. **`max_I`** — The peak current drawn during the session.

14. **`mean_P_loss`** — The average power wasted as heat in the test path: for each sample, the current multiplied by the missing voltage (target minus delivered), averaged over the session. This is the heat the cable and connectors dissipate while charging.

15. **`E_wh`** — The total electrical energy delivered to the phone during the session, in watt-hours, obtained by adding up the instantaneous power over time.

## Dynamics and defect features

16. **`dR_dt_mOhm_per_min`** — How quickly the cable resistance drifted upward over the session, expressed in milliohms per minute (a straight-line trend fitted through the resistance-over-time data). A strongly positive value means the cable heats up under load — copper resistance rises with temperature — triggering the `SELF_HEATING` tag.

17. **`interruption_frac`** — The fraction of active-charging samples that sit inside a "dip": a brief fall of the current down to the no-phone level *from which the current recovered*. These recovered dips are the signature of an intermittent connection. (The final taper when the battery fills never recovers and is deliberately not counted.)

18. **`spike_count`** — The number of sudden jumps in current or voltage, counted over short (~0.2 s) windows: a current change faster than 2 A per second or a voltage change faster than 0.2 V per second counts as one spike. This is the arcing / bad-contact signature, distinct from ordinary sample-to-sample sensor noise.

19. **`idle_I`** — The average current flowing when the phone is disconnected (no-phone state), i.e. the leakage current of the rig and cable. If the session contains no such samples, this is not computed. Above the configured threshold it triggers the `LEAKY` tag.

## Quality meta features

20. **`n_busy`** — The number of active-charging samples that fed the resistance statistics. A minimum count (5) is required before any resistance-based grade is issued at all.

21. **`valid_frac`** — The fraction of all samples that passed the validity checks (sensor read succeeded, voltage/current within physical range). Directly scales the verdict's confidence.

22. **`duration_s`** — The time span between the first and last valid sample of the session: how long the measurement actually ran.

---

## How the verdict uses them

The grade (A–F) comes primarily from `r_mean` (normalized per metre of cable length, after baseline subtraction). Defect tags come from the stability and dynamics features: `sigma_V` → `UNSTABLE_CONTACT`, `dR_dt_mOhm_per_min` → `SELF_HEATING`, `interruption_frac`/`spike_count` → `INTERMITTENT`, `idle_I` → `LEAKY`, and the `r_p95`–`r_p5` spread → `NON_LINEAR`. The **confidence** blends the data quality (`valid_frac`, sample count) with the agreement between the two independent resistance estimates (`r_mean` vs `r_dvdi`).
