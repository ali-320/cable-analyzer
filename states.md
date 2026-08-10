# Sample-State Reference

Single reference table for every value that can appear in `Sample.state` (and therefore in the CSV `state` column and the SQLite `samples.state` column).

**Code source:** `src/telemetry/session.py::SessionTracker` (settable states), `src/telemetry/sampler.py::Sampler.run` (stamped states), `src/features/metrics.py::BUSY_STATES` (consumer of states).

**Configuration source:** `cable-analyzer/config.toml` under the `[session]` block (lines 63–68) — defaults shown in the "Variable" column.

---

## Master State Table

| #   | State        | What it represents                                                                                                                                                   | Conditions to be in this state                                                                                                         | Transition-time variable                                                                                                                                                                                                             | Where the variable is configured                                                                                                                       |
| --- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | `NO_SOURCE`  | VBUS is absent — charger unplugged, cable VBUS open, or no power reaching the rig                                                                                    | `V_load < v_present_min` (`1.0 V`); `v_present_min` itself                                                                             | n/a — entry from `IDLE` is **immediate** when `V < 1.0 V` (not debounced)                                                                                                                                                            | `src/telemetry/session.py::SessionTracker.v_present_min` (hard-coded `1.0 V` in `__init__`)                                                            |
| 2   | `IDLE`       | VBUS is present, the phone is plugged in or attached, but it isn't yet pulling ≥ `i_charge_start`                                                                    | `V ≥ v_present_min` (`1.0 V`) **AND** `I < i_charge_start` (`0.10 A`); leakage between `i_no_load` (0.05 A) and 0.10 A is still `IDLE` | `IDLE` **→ OPEN candidate:** `open_timeout_s` (`30.0 s`) — strict, not debounced longer                                                                                                                                              | `config.toml [session] open_timeout_s = 30.0` (read in `SessionTracker.__init__` via `cfg["session"]`)                                                 |
| 3   | `CHARGING`   | Phone (or controlled e-load) is actively drawing ≥ `i_charge_start` and the draw has been sustained long enough to latch                                             | `V ≥ v_present_min` (`1.0 V`) **AND** `I ≥ i_charge_start` (`0.10 A`); sustained for `≥ debounce_start_s`                              | `IDLE/UNKNOWN → CHARGING:` `debounce_start_s` (`5.0 s`) — current must stay ≥ 0.10 A for at least this long before latching `CHARGING` (event: `charging_start`)                                                                     | `config.toml [session] debounce_start_s = 5.0`                                                                                                         |
| 4   | `CHARGED`    | Phone finished charging — current has dropped and stayed below `i_no_load` for long enough after a `CHARGING` run; the state is now also retained in the log for a short finish window | `state` was `CHARGING` **AND** `I < i_no_load` (`0.05 A`); sustained for `≥ debounce_end_s` | `CHARGING → CHARGED:` `debounce_end_s` (`60.0 s`); after the transition, charge mode continues sampling while `s.t - ended_at < debounce_finish_s` (`10.0 s`), then stops. The transition sample and subsequent finish-window samples are stamped `CHARGED` and saved. | `config.toml [session] debounce_end_s = 60.0` and `debounce_finish_s = 10.0`; read by `SessionTracker.__init__` |
| 5   | `OPEN`       | An "open candidate" was raised because `phone_expected` was true but no charging ever started within `open_timeout_s` of entering `IDLE`                             | `phone_expected = True` **AND** `ever_charged = False` **AND** state is `IDLE` for `≥ open_timeout_s`                                  | Detected via `_idle_since`; no further time gate (it is a one-shot event flag, **`Tracker.open_flag = True`**, not a runtime state — see row 8)                                                                                      | `config.toml [session] open_timeout_s = 30.0`                                                                                                          |
| 6   | `FAULT`      | Safety fault — latched terminal state, the run is aborted                                                                                                            | `V > 26.0 V` OR `I > 3.2 A` OR `V < 0.5 V` while `I > 1.0 A` (interpreted as a short)                                                  | n/a — latched **immediately** on the first offending valid sample (no debounce); reason stored in `fault_reason`                                                                                                                     | Hard-coded safety bounds in `src/telemetry/session.py::SessionTracker.update`                                                                          |
| 7   | `PROBE`      | The sample was taken during a controlled characterisation of the cable at a fixed PDO voltage/current step — **not** classified by `SessionTracker`                  | Sample was created by `Sampler.run(..., state="PROBE")`; always considered busy regardless of `I` value                                | n/a — written once by the sampler, never changed; no temporal gating                                                                                                                                                                 | Implicit: `state` argument to `src/telemetry/sampler.py::Sampler.run` is set to `"PROBE"` in `src/main.py::run_probe` for both manual and e-load steps |
| 8   | `UNKNOWN`    | Default initial state stamped on a `Sample` (and the loop state used by `Sampler.run` when a session is just starting) before the `SessionTracker` has classified it | `Sample.state` field has its dataclass default `"UNKNOWN"`; also passed by `Sampler.run` defaults                                      | n/a — overwritten to `NO_SOURCE/IDLE/CHARGING/CHARGED/FAULT` on the first valid sample after `tracker.update(s)`                                                                                                                     | Default value of `Sample.state` in `src/telemetry/models.py::Sample` and the default `state=` arg of `src/telemetry/sampler.py::Sampler.run`           |
| 9   | `""` (empty) | Defensive value in `BUSY_STATES`: counts as busy for `compute_features()` so a sample whose `state` was explicitly cleared is not silently dropped                   | `Sample.state` set to `""` (only via tests / explicit clearing — no production path writes the empty string)                           | n/a — written by construction, not by the state machine                                                                                                                                                                              | `BUSY_STATES = {"CHARGING", "PROBE", "UNKNOWN", ""}` in `src/features/metrics.py`                                                                      |

---

## Threshold summary (from `config.toml [session]`, lines 63–68)

| Variable | Default | Drives |
|----------|---------|--------|
| `i_no_load` | **0.05 A** | boundary between "true no-load" (`IDLE`/`CHARGED`) and "significant draw" (`CHARGING` candidate) |
| `i_charge_start` | **0.10 A** | minimum current to enter the `CHARGING` candidate window |
| `debounce_start_s` | **5.0 s** | required hold time of `I ≥ i_charge_start` before latching `CHARGING` |
| `debounce_end_s` | **60.0 s** | required hold time of `I < i_no_load` while `CHARGING` before latching `CHARGED` |
| `debounce_finish_s` | **10.0 s** | additional time to keep collecting/logging `CHARGED` samples after the transition before charge mode stops |
| `open_timeout_s` | **30.0 s** | `IDLE` duration with no charging ever seen (and `phone_expected`) → `open_candidate` event |

`v_present_min` is **hard-coded** in `SessionTracker.__init__` to `1.0 V`; it is the boundary between `NO_SOURCE` and `IDLE`.

The safety bounds (`V > 26 V`, `I > 3.2 A`, `V < 0.5 V ∧ I > 1 A`) that trip `FAULT` are also hard-coded in `SessionTracker.update` — they live in code, not in `config.toml`.

---

## Transitions at a glance (text diagram)

```text
        V present?  ─► NO_SOURCE  ── (V rises) ──►  IDLE
                                                      │  I ≥ 0.10 A for debounce_start_s (5 s)
                                                      ▼
                                                  CHARGING
                                                      │  I < 0.05 A for debounce_end_s (60 s)
                                                      ▼
                                                  CHARGED
                                                      │  log for debounce_finish_s (10 s)
                                                      ▼
                                                  STOP / VERDICT

        (any state with V/I safety bound violation)  ──► FAULT (terminal)

        PROBE and UNKNOWN are NOT touched by SessionTracker:
            PROBE   ← written by Sampler.run(state="PROBE") during e-load/manual probe
            UNKNOWN ← default Sample.state before any classification
```
