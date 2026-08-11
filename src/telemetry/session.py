"""Charging-session state machine (DEVELOPMENT_PLAN.md §3).

Encodes the user's I=0 rules:

* ``NO_PHONE`` - V present, only board/fixture leakage is measured
* ``IDLE``    - V present, a phone is connected but draws only its low-current band
* ``CHARGING``- active phone charging current is sustained
* ``CHARGED`` - after charging, current drops into the low-current band

The current bands are configurable in ``config.toml``. They deliberately
separate a connected/full phone (typically 0.01–0.099 A) from an absent phone
(typically below 0.010 A).

Plus NO_SOURCE, OPEN and FAULT. Every transition is debounced so momentary
dips never split a session. The tracker stamps ``sample.state`` as samples
stream through it. Samples in confirmed debounce windows are relabeled
retroactively to the new state; interrupted candidates retain the previous
confirmed state.
"""
from __future__ import annotations

from src.telemetry.models import Sample

NO_SOURCE = "NO_SOURCE"
NO_PHONE = "NO_PHONE"
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
        # Current bands are measured on the phone side of the INA219.
        # Keep i_no_load as a compatibility alias for feature extraction, but
        # use the explicit bands below for state decisions.
        self.i_no_phone_max = float(s.get("i_no_phone_max", 0.010))
        self.i_fully_charged_min = float(s.get("i_fully_charged_min", self.i_no_phone_max))
        self.i_fully_charged_max = float(s.get("i_fully_charged_max", 0.10))
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
        self._since_low: float | None = None   # time current entered the fully-charged band
        self._since_no_phone: float | None = None  # time current entered the leakage band
        self._since_idle_transition: float | None = None  # IDLE/NO_PHONE candidate
        self._idle_since: float | None = None  # time entered IDLE/NO_PHONE without charging

        # Samples in a debounce candidate initially carry the previously
        # confirmed state. If the candidate is confirmed, they are relabeled
        # retroactively; if it is interrupted, they remain in that previous
        # state. This keeps the debounce interval out of the wrong state's
        # calculations without discarding the raw readings.
        self._pending_samples: list[Sample] = []
        self._pending_target: str | None = None

    # ------------------------------------------------------------------ debounce helpers
    def _begin_pending(self, target: str, sample: Sample) -> None:
        if self._pending_target != target:
            self._pending_samples.clear()
            self._pending_target = target
        self._pending_samples.append(sample)

    def _clear_pending(self, target: str | None = None) -> None:
        if target is None or self._pending_target == target:
            self._pending_samples.clear()
            self._pending_target = None

    def _confirm_pending(self, target: str) -> None:
        if self._pending_target == target:
            for pending in self._pending_samples:
                pending.state = target
        self._clear_pending(target)

    def _reset_candidates(self) -> None:
        self._since_chg = None
        self._since_low = None
        self._since_no_phone = None
        self._since_idle_transition = None
        self._clear_pending()

    # ------------------------------------------------------------------ API
    def update(self, sample: Sample) -> tuple[str, str | None]:
        """Feed one sample; returns ``(state, event)``.

        Events: ``charging_start``, ``charged``, ``open_candidate``, ``fault``
        or None. The sample's ``state`` field is stamped with the current
        confirmed state for downstream feature extraction. A pending
        transition buffers sample references and relabels them if the debounce
        completes.
        """
        t, v, i = sample.t, sample.voltage, sample.current

        if sample.valid:
            if v > 26.0 or i > 3.2:
                return self._fault(f"out_of_range V={v:.2f} I={i:.2f}", sample)
            if v < 0.5 and i > 1.0:
                return self._fault("short_condition (V<0.5 V while I>1 A)", sample)

        if self.state in (FAULT, CHARGED):  # fault/charged are terminal for a run
            sample.state = self.state
            return self.state, None

        if not sample.valid:
            # An invalid read cannot prove continuity through a debounce
            # window, so the candidate is canceled and the sample keeps the
            # previously confirmed state.
            self._reset_candidates()
            sample.state = self.state
            return self.state, None

        if v < self.v_present_min:
            desired = NO_SOURCE
        elif i >= self.i_start:
            # 0.xxx A: active phone charging.
            desired = CHARGING
        elif i < self.i_no_phone_max:
            # 0.00xx A: only board/fixture leakage; no phone is connected.
            desired = NO_PHONE
        else:
            # 0.0xx A: phone is connected but is in its low-current band
            # (normally fully charged or waiting for charge negotiation).
            desired = IDLE

        if self.state == NO_SOURCE and desired != NO_SOURCE:
            # Power appeared. Preserve the existing behavior that low-current
            # presence is immediately classified, while active charging starts
            # a debounce candidate from the IDLE baseline.
            self.state = IDLE if desired == CHARGING else desired

        event: str | None = None

        # Loss of VBUS is an immediate source/safety transition. It must not
        # remain mislabeled as CHARGING while the source is absent.
        if desired == NO_SOURCE:
            self._reset_candidates()
            if self.state != NO_SOURCE:
                self.state = NO_SOURCE
                self._idle_since = t
            sample.state = self.state
            return self.state, None

        if desired == CHARGING:
            self._since_low = None
            self._since_no_phone = None
            self._since_idle_transition = None
            self._idle_since = None
            if self.state != CHARGING:
                self._begin_pending(CHARGING, sample)
                if self._since_chg is None:
                    self._since_chg = t
                elif (t - self._since_chg) >= self.debounce_start:
                    self.state = CHARGING
                    self._confirm_pending(CHARGING)
                    self.started_at = t
                    event = "charging_start"
            else:
                self._since_chg = None
                self._clear_pending()
        else:
            self._since_chg = None
            self._clear_pending(CHARGING)

            if self.state == CHARGING:
                # Current dropped while charging. Only the 0.0xx A phone band
                # can prove CHARGED; the 0.00xx A leakage band indicates a
                # disconnect and must not be mislabeled as a full phone.
                if desired == IDLE:
                    self._since_no_phone = None
                    self._since_idle_transition = None
                    self._begin_pending(CHARGED, sample)
                    if self._since_low is None:
                        self._since_low = t
                    elif (t - self._since_low) >= self.debounce_end:
                        self.state = CHARGED
                        self._confirm_pending(CHARGED)
                        self.ended_at = t
                        self.ever_charged = True
                        event = "charged"
                elif desired == NO_PHONE:
                    self._since_low = None
                    self._since_idle_transition = None
                    self._begin_pending(NO_PHONE, sample)
                    if self._since_no_phone is None:
                        self._since_no_phone = t
                    elif (t - self._since_no_phone) >= self.debounce_end:
                        self.state = NO_PHONE
                        self._confirm_pending(NO_PHONE)
                        self._since_no_phone = None
                else:
                    self._since_low = None
                    self._since_no_phone = None
                    self._since_idle_transition = None
                    self._clear_pending()
            else:
                # IDLE <-> NO_PHONE is also debounced. The candidate samples
                # remain in the old state unless the new state is confirmed.
                if desired in (IDLE, NO_PHONE) and desired != self.state:
                    if self._pending_target != desired:
                        self._since_idle_transition = t
                    self._begin_pending(desired, sample)
                    if (
                        self._since_idle_transition is not None
                        and (t - self._since_idle_transition) >= self.debounce_end
                    ):
                        self.state = desired
                        self._confirm_pending(desired)
                        self._since_idle_transition = None
                        self._idle_since = t
                else:
                    self._since_idle_transition = None
                    self._clear_pending()

                if self._idle_since is None:
                    self._idle_since = t
                elif (
                    not self.open_flag
                    and self.phone_expected
                    and not self.ever_charged
                    and self.state == NO_PHONE
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
        self._reset_candidates()
        self.state = FAULT
        self.fault_reason = reason
        sample.state = FAULT
        self.last_event = "fault"
        return FAULT, "fault"
