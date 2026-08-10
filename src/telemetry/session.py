"""Charging-session state machine (DEVELOPMENT_PLAN.md §3).

Encodes the user's I=0 rules:

* ``IDLE``    - V present, I ~ 0  ->  phone NOT charging yet
* ``CHARGING``- I >= threshold sustained  ->  charging active
* ``CHARGED`` - after charging, I ~ 0 sustained  ->  phone has been charged

Plus NO_SOURCE, OPEN and FAULT. Every transition is debounced so momentary
dips never split a session. The tracker stamps ``sample.state`` as samples
stream through it.
"""
from __future__ import annotations

from src.telemetry.models import Sample

NO_SOURCE = "NO_SOURCE"
IDLE = "IDLE"
CHARGING = "CHARGING"
CHARGED = "CHARGED"
OPEN = "OPEN"
FAULT = "FAULT"


class SessionTracker:
    def __init__(
        self,
        cfg: dict,
        v_target: float = 5.0,
        phone_expected: bool = False,
    ) -> None:
        s = cfg.get("session", {})
        self.i_no_load = float(s.get("i_no_load", 0.05))
        self.i_start = float(s.get("i_charge_start", 0.10))
        self.debounce_start = float(s.get("debounce_start_s", 5.0))
        self.debounce_end = float(s.get("debounce_end_s", 60.0))
        # Keep collecting and logging CHARGED samples after the transition so
        # the terminal state is visible in the CSV before charge mode exits.
        self.debounce_finish = float(s.get("debounce_finish_s", 10.0))
        self.open_timeout = float(s.get("open_timeout_s", 30.0))
        self.v_present_min = 1.0
        self.v_target = float(v_target)
        self.phone_expected = bool(phone_expected)

        self.state = NO_SOURCE
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.ever_charged = False
        self.open_flag = False
        self.fault_reason: str | None = None
        self.last_event: str | None = None

        self._since_chg: float | None = None   # candidate CHARGING start time
        self._since_low: float | None = None   # time I dropped below i_no_load while CHARGING
        self._idle_since: float | None = None  # time entered IDLE/NO_SOURCE without charging

    # ------------------------------------------------------------------ API
    def update(self, sample: Sample) -> tuple[str, str | None]:
        """Feed one sample; returns ``(state, event)``.

        Events: ``charging_start``, ``charged``, ``open_candidate``, ``fault``
        or None. The sample's ``state`` field is stamped with the current
        state for downstream feature extraction.
        """
        t, v, i = sample.t, sample.voltage, sample.current

        if sample.valid:
            if v > 26.0 or i > 3.2:
                return self._fault(f"out_of_range V={v:.2f} I={i:.2f}", sample)
            if v < 0.5 and i > 1.0:
                return self._fault("short_condition (V<0.5 V while I>1 A)", sample)

        if self.state in (FAULT, CHARGED):  # latched terminal states
            sample.state = self.state
            return self.state, None

        if not sample.valid:
            # bad reads must not drive the state machine
            sample.state = self.state
            return self.state, None

        if v < self.v_present_min:
            desired = NO_SOURCE
        elif i >= self.i_start:
            desired = CHARGING
        else:
            desired = IDLE

        if self.state == NO_SOURCE and desired != NO_SOURCE:
            self.state = IDLE  # power appeared on the bus -> waiting for a load

        event: str | None = None

        if desired == CHARGING:
            self._since_low = None
            self._idle_since = None
            if self._since_chg is None:
                self._since_chg = t
            elif self.state != CHARGING and (t - self._since_chg) >= self.debounce_start:
                self.state = CHARGING
                self.started_at = t
                event = "charging_start"
        else:
            self._since_chg = None
            if self.state == CHARGING:
                # current dropped while charging -> count down to "charged"
                if self._since_low is None:
                    self._since_low = t
                elif (t - self._since_low) >= self.debounce_end:
                    self.state = CHARGED
                    self.ended_at = t
                    self.ever_charged = True
                    event = "charged"
            else:
                # not charging: IDLE <-> NO_SOURCE switch immediately
                if desired != self.state:
                    self.state = desired
                    self._idle_since = t
                if self._idle_since is None:
                    self._idle_since = t
                elif (
                    not self.open_flag
                    and self.phone_expected
                    and not self.ever_charged
                    and desired == IDLE
                    and (t - self._idle_since) >= self.open_timeout
                ):
                    self.open_flag = True
                    event = "open_candidate"

        sample.state = self.state
        if event is not None:
            self.last_event = event
        return self.state, event

    # ------------------------------------------------------------------ misc
    def _fault(self, reason: str, sample: Sample) -> tuple[str, str]:
        self.state = FAULT
        self.fault_reason = reason
        sample.state = FAULT
        self.last_event = "fault"
        return FAULT, "fault"
