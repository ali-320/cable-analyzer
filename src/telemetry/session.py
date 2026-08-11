"""Charging-session state machine.

The phone-side INA219 current is classified into three normal bands:

* ``CHARGING`` - active charging current (>= ``i_charge_start``)
* ``CHARGED``  - connected/full-phone maintenance current
  (``i_fully_charged_min <= I < i_charge_start``)
* ``NO_PHONE`` - board/fixture leakage only (``I < i_no_phone_max``)

Every normal band transition is debounced for the configured state debounce
period (3 seconds by default). Samples in a confirmed candidate window are
retroactively relabeled to the destination state. ``NO_SOURCE`` and ``FAULT``
are immediate safety states.
"""
from __future__ import annotations

from src.telemetry.models import Sample

NO_SOURCE = "NO_SOURCE"
NO_PHONE = "NO_PHONE"
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
        self.i_no_phone_max = float(s.get("i_no_phone_max", 0.010))
        self.i_fully_charged_min = float(
            s.get("i_fully_charged_min", self.i_no_phone_max)
        )
        self.i_fully_charged_max = float(
            s.get("i_fully_charged_max", 0.10)
        )
        self.i_no_load = float(s.get("i_no_load", self.i_no_phone_max))
        self.i_start = float(s.get("i_charge_start", self.i_fully_charged_max))
        if not (
            0.0 <= self.i_no_phone_max
            and self.i_fully_charged_min == self.i_no_phone_max
            and self.i_fully_charged_max == self.i_start
            and self.i_fully_charged_min < self.i_fully_charged_max
        ):
            raise ValueError(
                "session current bands must be contiguous and satisfy "
                "0 <= i_no_phone_max == i_fully_charged_min < "
                "i_fully_charged_max == i_charge_start"
            )

        # One debounce duration applies to every normal current-band change.
        # ``debounce_state_s`` is the new explicit setting; the legacy end
        # setting remains a fallback for custom/test configurations.
        self.debounce_s = float(
            s.get("debounce_state_s", s.get("debounce_end_s", 3.0))
        )
        self.debounce_start = self.debounce_s
        self.debounce_end = self.debounce_s
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

        self._pending_since: float | None = None
        self._pending_target: str | None = None
        self._pending_samples: list[Sample] = []
        self._no_phone_since: float | None = None

    # ------------------------------------------------------------------ debounce helpers
    def _begin_pending(self, target: str, sample: Sample) -> None:
        if self._pending_target != target:
            self._pending_samples.clear()
            self._pending_target = target
            self._pending_since = sample.t
        self._pending_samples.append(sample)

    def _clear_pending(self) -> None:
        self._pending_since = None
        self._pending_target = None
        self._pending_samples.clear()

    def _confirm_pending(self, target: str) -> None:
        if self._pending_target == target:
            for pending in self._pending_samples:
                pending.state = target
        self._clear_pending()

    def _reset_candidates(self) -> None:
        self._clear_pending()

    def _desired_state(self, voltage: float, current: float) -> str:
        if voltage < self.v_present_min:
            return NO_SOURCE
        if current >= self.i_start:
            return CHARGING
        if current < self.i_no_phone_max:
            return NO_PHONE
        return CHARGED

    # ------------------------------------------------------------------ API
    def update(self, sample: Sample) -> tuple[str, str | None]:
        """Feed one sample and return ``(confirmed_state, event)``.

        Normal state changes require continuous residence in the destination
        current band for ``debounce_s``. Candidate samples retain the previous
        label until confirmation; then they are relabeled retroactively.
        """
        t, v, i = sample.t, sample.voltage, sample.current

        if sample.valid:
            if v > 26.0 or i > 3.2:
                return self._fault(f"out_of_range V={v:.2f} I={i:.2f}", sample)
            if v < 0.5 and i > 1.0:
                return self._fault("short_condition (V<0.5 V while I>1 A)", sample)

        # FAULT is latched for the remainder of the session. Safety cannot be
        # cleared by a later apparently normal sensor reading.
        if self.state == FAULT:
            sample.state = FAULT
            return FAULT, None

        if not sample.valid:
            self._reset_candidates()
            sample.state = self.state
            return self.state, None

        desired = self._desired_state(v, i)

        # NO_SOURCE is only a voltage condition. On the first valid powered
        # sample, leave the startup sentinel immediately and classify the
        # current band; otherwise a powered phone would be displayed as
        # NO_SOURCE during the first debounce window. Debounce applies to all
        # subsequent CHARGING/CHARGED/NO_PHONE transitions.
        if self.state == NO_SOURCE and desired != NO_SOURCE:
            self._clear_pending()
            self.state = desired
            startup_event: str | None = None
            if desired == CHARGING:
                self.started_at = t
                startup_event = "charging_start"
            elif desired == CHARGED:
                self.ended_at = t
                self.ever_charged = True
                startup_event = "charged"
            elif desired == NO_PHONE:
                self._no_phone_since = t
            if startup_event is not None:
                self.last_event = startup_event
            sample.state = self.state
            return self.state, startup_event

        # VBUS loss is an immediate transition and never waits for debounce.
        if desired == NO_SOURCE:
            self._reset_candidates()
            if self.state != NO_SOURCE:
                self.state = NO_SOURCE
                self._no_phone_since = t
            sample.state = self.state
            return self.state, None

        event: str | None = None
        if desired == self.state:
            self._reset_candidates()
        else:
            self._begin_pending(desired, sample)
            if (
                self._pending_since is not None
                and t - self._pending_since >= self.debounce_s
            ):
                self.state = desired
                self._confirm_pending(desired)
                if desired == CHARGING:
                    self.started_at = t
                    event = "charging_start"
                elif desired == CHARGED:
                    self.ended_at = t
                    self.ever_charged = True
                    event = "charged"

        if self.state == NO_PHONE:
            if self._no_phone_since is None:
                self._no_phone_since = t
            if (
                not self.open_flag
                and self.phone_expected
                and not self.ever_charged
                and t - self._no_phone_since >= self.open_timeout
            ):
                self.open_flag = True
                event = event or "open_candidate"
        else:
            self._no_phone_since = None

        sample.state = self.state
        if event is not None:
            self.last_event = event
        return self.state, event

    # ------------------------------------------------------------------ misc
    def _fault(self, reason: str, sample: Sample) -> tuple[str, str]:
        self._reset_candidates()
        self.state = FAULT
        self.fault_reason = reason
        sample.state = FAULT
        self.last_event = "fault"
        return FAULT, "fault"
