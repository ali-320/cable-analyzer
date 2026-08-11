"""Unit tests for src/telemetry/session.py (DEVELOPMENT_PLAN.md §3 state machine)."""
import unittest

from tests.helpers import make_samples

from src.telemetry.models import Sample
from src.telemetry.session import CHARGED, CHARGING, FAULT, IDLE, NO_PHONE, NO_SOURCE, OPEN, SessionTracker

CFG = {
    "session": {
        "i_no_phone_max": 0.010,
        "i_fully_charged_min": 0.010,
        "i_fully_charged_max": 0.10,
        "i_no_load": 0.05,
        "i_charge_start": 0.10,
        "debounce_start_s": 5.0,
        "debounce_end_s": 60.0,
        "debounce_finish_s": 10.0,
        "open_timeout_s": 30.0,
    }
}


def feed(tracker: SessionTracker, samples: list[Sample]):
    for s in samples:
        tracker.update(s)
    return tracker


class TestStateMachine(unittest.TestCase):
    def test_no_source_when_voltage_absent(self):
        tr = SessionTracker(CFG, v_target=5.0)
        samples = [Sample(t=k * 0.5, voltage=0.0, current=0.0, power=0.0) for k in range(10)]
        feed(tr, samples)
        self.assertEqual(tr.state, NO_SOURCE)

    def test_no_phone_when_only_board_leakage_is_present(self):
        tr = SessionTracker(CFG, v_target=5.0)
        feed(tr, make_samples(n=10, current=0.005))
        self.assertEqual(tr.state, NO_PHONE)  # 0.00x A is the no-phone band

    def test_idle_when_phone_draws_low_current(self):
        tr = SessionTracker(CFG, v_target=5.0)
        feed(tr, make_samples(n=10, current=0.03))
        self.assertEqual(tr.state, IDLE)  # 0.0x A is the connected/full band

    def test_charging_starts_after_debounce(self):
        tr = SessionTracker(CFG, v_target=5.0)
        samples = make_samples(n=20, current=1.0, period=0.5)  # 10 s > 5 s debounce
        feed(tr, samples)
        self.assertEqual(tr.state, CHARGING)
        self.assertEqual(tr.started_at, 5.0)  # debounce_start reached at t=5 s

    def test_charging_does_not_start_early(self):
        tr = SessionTracker(CFG, v_target=5.0)
        samples = make_samples(n=4, current=1.0, period=0.5)  # only 2 s
        feed(tr, samples)
        self.assertEqual(tr.state, IDLE)
        self.assertTrue(all(s.state == IDLE for s in samples))

    def test_charging_debounce_relabels_candidate_samples(self):
        cfg = {"session": {**CFG["session"], "debounce_start_s": 2.0}}
        tr = SessionTracker(cfg, v_target=5.0)
        samples = make_samples(n=7, current=1.0, period=0.5)  # confirmation at t=2.0 s
        feed(tr, samples)
        self.assertEqual(tr.state, CHARGING)
        self.assertEqual(tr.started_at, 2.0)
        # The candidate interval was initially IDLE, but is now part of the
        # confirmed charging interval for CSV and feature calculations.
        self.assertTrue(all(s.state == CHARGING for s in samples))

    def test_charged_after_low_current_tail(self):
        tr = SessionTracker(CFG, v_target=5.0)
        # 10 s charging then 61 s of I~0 -> CHARGED (user rule: I=0 after readings = charged)
        samples = make_samples(n=20, current=1.0, period=0.5)  # 10 s charging
        samples += make_samples(n=122, current=0.03, period=0.5, state="CHARGING")  # 61 s full-phone tail
        feed(tr, samples)
        self.assertEqual(tr.state, CHARGED)
        self.assertTrue(tr.ever_charged)
        self.assertTrue(all(s.state == CHARGED for s in samples[20:]))

    def test_charged_samples_are_stamped_after_transition(self):
        tr = SessionTracker(CFG, v_target=5.0)
        charging = [
            Sample(t=k * 0.5, voltage=4.8, current=1.0, power=4.8)
            for k in range(21)
        ]
        low_start = charging[-1].t + 0.5
        low = [
            Sample(t=low_start + k * 0.5, voltage=5.0, current=0.03, power=0.15)
            for k in range(122)
        ]
        post = [
            Sample(t=low[-1].t + (k + 1) * 0.5, voltage=5.0, current=0.03, power=0.15)
            for k in range(20)
        ]

        for sample in charging + low + post:
            tr.update(sample)

        self.assertEqual(tr.state, CHARGED)
        self.assertIsNotNone(tr.ended_at)
        transition_index = next(i for i, s in enumerate(charging + low + post) if s.state == CHARGED)
        self.assertEqual((charging + low + post)[transition_index].state, CHARGED)
        self.assertTrue(all(s.state == CHARGED for s in (charging + low + post)[transition_index:]))
        # The low-current debounce candidate is also relabeled as CHARGED.
        self.assertTrue(all(s.state == CHARGED for s in low))

    def test_open_candidate_when_phone_expected(self):
        tr = SessionTracker(CFG, v_target=5.0, phone_expected=True)
        samples = make_samples(n=70, current=0.005, period=0.5)  # 35 s NO_PHONE
        feed(tr, samples)
        self.assertEqual(tr.state, NO_PHONE)
        self.assertTrue(tr.open_flag)
        self.assertEqual(tr.last_event, "open_candidate")

    def test_charged_requires_the_low_current_phone_band(self):
        tr = SessionTracker(CFG, v_target=5.0)
        charging = make_samples(n=20, current=1.0, period=0.5)
        disconnected = make_samples(n=122, current=0.005, period=0.5, state="CHARGING")
        feed(tr, charging + disconnected)
        self.assertEqual(tr.state, NO_PHONE)
        self.assertFalse(tr.ever_charged)
        # A confirmed disconnect relabels its debounce candidate as NO_PHONE,
        # rather than leaving a false CHARGING tail in the CSV.
        self.assertTrue(all(s.state == NO_PHONE for s in disconnected))

    def test_debounce_candidate_is_kept_in_previous_state_if_interrupted(self):
        tr = SessionTracker(CFG, v_target=5.0)
        samples = make_samples(n=20, current=1.0, period=0.5)
        low_candidate = make_samples(n=2, current=0.03, period=0.5, state="CHARGING")
        recovery = make_samples(n=4, current=1.0, period=0.5, state="CHARGING")
        feed(tr, samples + low_candidate + recovery)
        self.assertEqual(tr.state, CHARGING)
        self.assertTrue(all(s.state == CHARGING for s in low_candidate))

    def test_idle_to_no_phone_debounce_relabels_candidate_samples(self):
        cfg = {"session": {**CFG["session"], "debounce_end_s": 1.0}}
        tr = SessionTracker(cfg, v_target=5.0)
        initial = make_samples(n=3, current=0.03, period=0.5)
        candidate = make_samples(n=4, current=0.005, period=0.5, state=IDLE)
        # Make timestamps continuous across the state change.
        for index, sample in enumerate(candidate):
            sample.t = initial[-1].t + (index + 1) * 0.5
        feed(tr, initial + candidate)
        self.assertEqual(tr.state, NO_PHONE)
        self.assertTrue(all(s.state == NO_PHONE for s in candidate))

    def test_no_phone_to_idle_debounce_relabels_candidate_samples(self):
        cfg = {"session": {**CFG["session"], "debounce_end_s": 1.0}}
        tr = SessionTracker(cfg, v_target=5.0)
        initial = make_samples(n=3, current=0.005, period=0.5)
        candidate = make_samples(n=4, current=0.03, period=0.5, state=NO_PHONE)
        for index, sample in enumerate(candidate):
            sample.t = initial[-1].t + (index + 1) * 0.5
        feed(tr, initial + candidate)
        self.assertEqual(tr.state, IDLE)
        self.assertTrue(all(s.state == IDLE for s in candidate))

    def test_invalid_read_cancels_charging_candidate(self):
        cfg = {"session": {**CFG["session"], "debounce_start_s": 2.0}}
        tr = SessionTracker(cfg, v_target=5.0)
        candidate = make_samples(n=3, current=1.0, period=0.5)
        feed(tr, candidate)
        invalid = Sample(t=1.5, voltage=float("nan"), current=float("nan"), power=0.0, valid=False)
        tr.update(invalid)
        self.assertEqual(tr.state, IDLE)
        self.assertTrue(all(s.state == IDLE for s in candidate))

    def test_loss_of_vbus_is_immediate_no_source(self):
        tr = SessionTracker(CFG, v_target=5.0)
        charging = make_samples(n=20, current=1.0, period=0.5)
        feed(tr, charging)
        self.assertEqual(tr.state, CHARGING)
        sample = Sample(t=10.0, voltage=0.0, current=0.0, power=0.0)
        state, event = tr.update(sample)
        self.assertEqual((state, event), (NO_SOURCE, None))
        self.assertEqual(sample.state, NO_SOURCE)

    def test_fault_clears_candidate_and_remains_terminal(self):
        cfg = {"session": {**CFG["session"], "debounce_start_s": 2.0}}
        tr = SessionTracker(cfg, v_target=5.0)
        candidate = make_samples(n=2, current=1.0, period=0.5)
        feed(tr, candidate)
        fault = Sample(t=1.0, voltage=0.2, current=2.5, power=0.5)
        state, event = tr.update(fault)
        self.assertEqual((state, event), (FAULT, "fault"))
        later = Sample(t=2.0, voltage=5.0, current=1.0, power=5.0)
        self.assertEqual(tr.update(later)[0], FAULT)
        self.assertEqual(later.state, FAULT)

    def test_current_band_boundaries(self):
        for current, expected in ((0.009, NO_PHONE), (0.010, IDLE), (0.099, IDLE), (0.100, IDLE)):
            tr = SessionTracker(CFG, v_target=5.0)
            sample = make_samples(n=1, current=current)[0]
            tr.update(sample)
            self.assertEqual(tr.state, expected, f"current={current}")
        tr = SessionTracker(CFG, v_target=5.0)
        samples = make_samples(n=12, current=0.100, period=0.5)
        feed(tr, samples)
        self.assertEqual(tr.state, CHARGING)

    def test_invalid_current_band_configuration_is_rejected(self):
        bad_cfg = {
            "session": {
                "i_no_phone_max": 0.02,
                "i_fully_charged_min": 0.01,
                "i_fully_charged_max": 0.10,
                "i_charge_start": 0.10,
            }
        }
        with self.assertRaises(ValueError):
            SessionTracker(bad_cfg)

    def test_fault_on_short_condition(self):
        tr = SessionTracker(CFG, v_target=5.0)
        s = Sample(t=0.0, voltage=0.2, current=2.5, power=0.5, valid=True)
        state, event = tr.update(s)
        self.assertEqual(state, FAULT)
        self.assertEqual(event, "fault")
        self.assertIsNotNone(tr.fault_reason)

    def test_fault_on_out_of_range(self):
        tr = SessionTracker(CFG, v_target=5.0)
        s = Sample(t=0.0, voltage=28.0, current=0.1, power=2.8, valid=True)
        state, _ = tr.update(s)
        self.assertEqual(state, FAULT)

    def test_invalid_samples_do_not_drive_state(self):
        tr = SessionTracker(CFG, v_target=5.0)
        bad = Sample(t=0.0, voltage=float("nan"), current=float("nan"), power=0.0, valid=False)
        tr.update(bad)
        self.assertEqual(tr.state, NO_SOURCE)  # unchanged, no crash

    def test_samples_stamped_with_state(self):
        tr = SessionTracker(CFG, v_target=5.0)
        samples = make_samples(n=20, current=1.0, period=0.5)
        for s in samples:
            tr.update(s)
        self.assertTrue(all(s.state == CHARGING for s in samples[10:]))
        self.assertIn(OPEN, (OPEN, IDLE, CHARGING))  # constants resolve


if __name__ == "__main__":
    unittest.main()
