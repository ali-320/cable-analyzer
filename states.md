# Sample-State Reference

`Sample.state` is written to the CSV and SQLite sample record. Current is measured on the phone side of the inline INA219.

## Current bands

| Current band | Meaning | State |
|---|---|---|
| `< i_no_phone_max` (default `< 0.010 A`) | Board/fixture leakage only; no phone/load detected | `NO_PHONE` |
| `i_fully_charged_min ≤ I < i_charge_start` (default `0.010–<0.100 A`) | Phone is connected and drawing low maintenance/full-battery current | `CHARGED` |
| `I ≥ i_charge_start` (default `≥0.100 A`) | Phone is actively charging | `CHARGING` |

The exact limits are configuration values. The defaults represent active charging in the `0.xxx A` range, fully-charged maintenance current in the `0.0xx A` range, and no-phone leakage in the `0.00xx A` range.

## Master state table

| State | Meaning | Entry/condition | Timing/configuration |
|---|---|---|---|
| `NO_SOURCE` | VBUS absent | `V < 1.0 V` | Immediate; `v_present_min` is a code safety constant. A safety `FAULT` takes precedence for a short/out-of-range reading. |
| `NO_PHONE` | VBUS present, only leakage current | `V ≥ 1.0 V` and `I < i_no_phone_max` | Normal entry requires `debounce_state_s`; `open_timeout_s` raises `open_candidate` when a phone was expected |
| `CHARGING` | Phone actively draws charging current | `I ≥ i_charge_start` | Normal entry requires `debounce_state_s`; emits `charging_start` |
| `CHARGED` | Phone is connected but drawing only low maintenance/full-battery current | `i_fully_charged_min ≤ I < i_charge_start` | Normal entry requires `debounce_state_s`; after confirmation charge mode logs the finish window |
| `OPEN` | Event flag indicating expected phone did not begin charging | `phone_expected=True`, `NO_PHONE` persists for `open_timeout_s`, and no charging occurred | Stored as `SessionTracker.open_flag`; the sample state remains `NO_PHONE` |
| `FAULT` | Safety fault | Voltage/current safety limit violated | Immediate and latched |
| `VERIFICATION` | Probe-only voltage-support check | Manual probe support window; current at or above `i_no_phone_max` proves support, whether active or fully charged | Not used in quality calculations |
| `PROBE` | Legacy controlled-load probe sample | Explicit automatic/GPIO probe window | Automatic load-probe label |
| `UNKNOWN` | Initial/default sample state | Before classification | Default in `Sample` and `Sampler` |

## Configuration

All values are under `[session]` in `config.toml`:

```toml
i_no_phone_max = 0.010       # A; below this is NO_PHONE
i_fully_charged_min = 0.010  # A; lower bound of CHARGED band
i_fully_charged_max = 0.10   # A; upper bound, exclusive
i_charge_start = 0.10        # A; lower bound of CHARGING band
debounce_state_s = 3.0       # every normal band transition requires 3 seconds
debounce_start_s = 3.0       # legacy alias
debounce_end_s = 3.0         # legacy alias
debounce_finish_s = 10.0
open_timeout_s = 5.0
```

The implementation validates that the bands are contiguous: `i_no_phone_max == i_fully_charged_min` and `i_fully_charged_max == i_charge_start`. `i_no_load` remains a legacy analytics threshold; it is not the state boundary.

## Debounce labeling

Every normal transition between `NO_PHONE`, `CHARGING`, and `CHARGED` requires three seconds of continuous readings in the destination current band. Candidate samples initially retain the previous confirmed label. If the candidate remains stable for the full debounce duration, those samples are retroactively relabeled to the destination state in memory, CSV, SQLite, and feature calculations. If the candidate is interrupted, the samples retain the previous state. Invalid reads cancel a candidate. Loss of VBUS (`NO_SOURCE`) and safety faults are immediate exceptions.

`CHARGED` is no longer terminal inside `SessionTracker`; a later sustained active-current band can transition back to `CHARGING`. Charge mode nevertheless stops after the configured `debounce_finish_s` logging window following a confirmed `CHARGED` event.

## Probe-mode distinction

In manual probe mode, the short support window is labeled `VERIFICATION`. A voltage is supported when at least half of valid verification samples have current at or above `i_no_phone_max`: this includes active charging (`0.xx A`) and fully-charged maintenance current (`0.0xx A`). Only the no-phone/leakage band (`0.00xx A`, below `i_no_phone_max`) is unsupported. Verification rows remain in the CSV for audit but are excluded from quality features. Longer measurement windows use `CHARGING`, `CHARGED`, `NO_PHONE`, `NO_SOURCE`, or `FAULT`.
