# Sample-State Reference — `states.md`

`Sample.state` labels every reading (stored in CSV + SQLite). Current is
measured on the phone side of the inline INA219.

## States

| State | Meaning | Condition |
|---|---|---|
| `NO_SOURCE` | No usable bus voltage | `V < v_present_min_v` (3.0 V in `--voltage` mode) |
| `NO_PHONE` | VBUS present, only leakage | `V ≥ v_present_min_v` and `I < i_no_phone_max` |
| `CHARGED` | Phone connected, maintenance current | `i_fully_charged_min ≤ I < i_charge_start` |
| `CHARGING` | Phone actively charging | `I ≥ i_charge_start` |
| `FAULT` | Safety fault — latched for the session | `V > 26 V` or `I > 3.2 A`, or short (`V < 0.5 V` while `I > 1 A`) |
| `UNKNOWN` | Initial label before classification | Default on every new sample |
| `VERIFICATION` | Manual-probe support window only | Not used by `--voltage --continuous` |
| `PROBE` | Legacy controlled-load label | Not used by `--voltage --continuous` |

## config.toml — the lines that define the states

```toml
[session]
i_no_phone_max = 0.010      # A; strictly below = NO_PHONE
i_fully_charged_min = 0.010 # A; inclusive lower bound of CHARGED
i_fully_charged_max = 0.10  # A; exclusive upper bound (= charge start)
i_charge_start = 0.10       # A; inclusive lower bound of CHARGING
debounce_state_s = 3.0      # every normal band transition needs 3 s
debounce_finish_s = 10.0    # charge mode: keep logging after CHARGED
open_timeout_s = 5.0        # NO_PHONE this long (phone expected) -> OPEN event
min_busy_readings = 3000    # continuous loop: BUSY readings per verdict cycle

[voltage]
v_present_min_v = 3.0       # NO_SOURCE threshold in --voltage mode
```

The code rejects non-contiguous bands:
`i_no_phone_max == i_fully_charged_min < i_fully_charged_max == i_charge_start`.

## Notes

- **Debounce:** normal transitions (`NO_PHONE` ↔ `CHARGING` ↔ `CHARGED`) require
  `debounce_state_s` of continuous residence in the destination band; the
  candidate samples are then retroactively relabeled. Interrupted transitions
  keep the old label. Invalid readings reset any pending transition.
- **Immediate transitions:** `NO_SOURCE` (voltage lost) never waits for
  debounce, and the first valid powered sample leaves the startup
  `NO_SOURCE` immediately. `FAULT` is immediate and latched — a later normal
  reading cannot clear it.
- **OPEN is an event, not a state:** with `phone_expected` set, `NO_PHONE`
  persisting for `open_timeout_s` without any charging raises the
  `open_candidate` event; samples stay labeled `NO_PHONE`.
- **Continuous loop:** BUSY readings (valid, `I ≥ i_min_compute`, in a quality
  state) are counted until `min_busy_readings`; then features → verdict →
  sync, and the cycle restarts.
