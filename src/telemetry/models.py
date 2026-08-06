"""Core data models shared across the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Sample:
    """One validated (or flagged) electrical observation.

    ``t`` is seconds since the acquisition started (monotonic ordering).
    ``state`` is annotated by the :class:`SessionTracker` as samples stream
    through it (NO_SOURCE / IDLE / CHARGING / CHARGED / FAULT), or "PROBE"
    for controlled-load probe samples.
    """

    t: float
    voltage: float
    current: float
    power: float
    state: str = "UNKNOWN"
    valid: bool = True
    flags: list = field(default_factory=list)


@dataclass
class SessionMeta:
    """Per-session context used by storage and the rule engine."""

    session_id: str = ""
    mode: str = "auto"  # auto | probe | charge | self-check
    v_target: float = 5.0
    length_m: float | None = None
    started_at: float = 0.0
    ended_at: float = 0.0
    charging_detected: bool = False
    v_present: bool = False
    phone_expected: bool = False
    fault_reason: str | None = None
    probe: dict = field(default_factory=dict)
