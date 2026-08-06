"""Single source of truth for GPIO pin assignments (WIRING_SCHEMATICS.md §4).

Loads pin numbers from the ``[gpio]`` section of config.toml so wiring changes
never require touching driver code.
"""
from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class PinMap:
    """BCM GPIO numbers as wired in WIRING_SCHEMATICS.md §4."""

    sda: int = 2
    scl: int = 3
    pwr_ok: int = 17
    sel0: int = 22
    sel1: int = 23
    sel2: int = 24
    en: int = 25
    q1_phone_switch: int = 26
    q2_load_switch: int = 27
    load_pwm: int = 18
    status_led: int = 5
    phone_present: int = 6

    @property
    def sel_pins(self) -> list[int]:
        """CH224K voltage-selection straps, SEL0 = LSB."""
        return [self.sel0, self.sel1, self.sel2]

    @classmethod
    def from_config(cls, cfg: dict) -> "PinMap":
        known = {f.name for f in fields(cls)}
        values = {k: int(v) for k, v in cfg.get("gpio", {}).items() if k in known}
        return cls(**values)
