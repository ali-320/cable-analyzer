# Sample-State Reference

`Sample.state` is written to the CSV and SQLite sample record. Current is measured on the phone side of the inline INA219.

## Current bands

| Current band | Meaning | State behavior |
|---|---|---|
| `< i_no_phone_max` (default `< 0.010 A`) | Board/fixture leakage only; no phone/load detected | `NO_PHONE` |
| `i_fully_charged_min ≤ I < i_fully_charged_max` (default `0.010–<0.100 A`) | Phone connected but drawing only low maintenance/fully-charged current | `IDLE` before a charging session; `CHARGED` after a sustained `CHARGING` session and `debounce_end_s` |
| `I ≥ i_charge_start` (default `≥0.100 A`) | Phone actively charging | Charging candidate; becomes `CHARGING` after `debounce_start_s` |

The exact limits are configuration values, not hard-coded measurements. The defaults reflect the observed hardware behavior: active charging in the `0.xxx A` range, full-phone current in the `0.0xx A` range, and no-phone leakage in the `0.00xx A` range.

## Master state table

| State | Meaning | Entry/condition | Timing/configuration |
|---|---|---|---|
| `NO_SOURCE` | VBUS absent | `V < 1.0 V` | Immediate; `v_present_min` is currently a code constant |
| `NO_PHONE` | VBUS present, only leakage current | `V ≥ 1.0 V` and `I < i_no_phone_max` | `open_timeout_s` raises the `open_candidate` event when a phone was expected and no charging ever started |
| `IDLE` | Phone appears connected but is not actively charging | `i_fully_charged_min ≤ I < i_fully_charged_max` before a charging run | Current-band limits in `[session]` |
| `CHARGING` | Phone actively draws charging current | `I ≥ i_charge_start` continuously for `debounce_start_s` | `[session] debounce_start_s` |
| `CHARGED` | A phone that was charging has settled into its low-current band | Previous state was `CHARGING` and `i_fully_charged_min ≤ I < i_fully_charged_max` for `debounce_end_s` | `[session] debounce_end_s`; charge mode logs this state for `debounce_finish_s` before stopping |
| `OPEN` | Event flag indicating expected phone did not begin charging | `phone_expected=True`, `NO_PHONE` persists for `open_timeout_s`, and no charging occurred | Stored as `SessionTracker.open_flag`; the sample state remains `NO_PHONE` |
| `FAULT` | Safety fault | Voltage/current safety limit violated | Immediate and latched |
| `PROBE` | Controlled/manual probe sample | Explicit `Sampler.run(..., state="PROBE")` | Not classified by `SessionTracker` |
| `UNKNOWN` | Initial/default sample state | Before classification | Default in `Sample` and `Sampler` |

## Configuration

All values are under `[session]` in `config.toml`:

```toml
i_no_phone_max = 0.010       # A; below this is NO_PHONE
i_fully_charged_min = 0.010  # A; lower bound of connected/full-phone band
i_fully_charged_max = 0.10   # A; upper bound, exclusive
i_charge_start = 0.10        # A; active charging lower bound
debounce_start_s = 5.0
debounce_end_s = 60.0
debounce_finish_s = 10.0
open_timeout_s = 30.0
```

The implementation validates that the bands are contiguous: `i_no_phone_max == i_fully_charged_min` and `i_fully_charged_max == i_charge_start`. `i_no_load` remains as a legacy analytics threshold; it is no longer the primary state boundary.

## Important distinction

A fall from `CHARGING` into the `0.0xx A` band can become `CHARGED` after the end debounce. A fall into the `0.00xx A` band is classified as `NO_PHONE`, not `CHARGED`, because it is more consistent with disconnect/leakage than a full phone.
