"""Unit tests for the three-band debounced session state machine."""
import unittest

from tests.helpers import make_samples

from src.telemetry.models import Sample
from src.telemetry.session import (
    CHARGED,
    CHARGING,
    FAULT,
    NO_PHONE,
    NO_SOURCE,
    OPEN,
    SessionTracker,
)

CFG = {
    "session": {
        "i_no_phone_max": 0.010,
        "i_fully_charged_min": 0.010,
        "i_fully_charged_max": 0.10,
        "i_no_load": 0.05,
        "i_charge_start": 0.10,
        "debounce_state_s": 3.0,
        "debounce_start_s": 3.0,
        "debounce_end_s": 3.0,
        "debounce_finish_s": 10.0,
        "open_timeout_s": 30.0,
    }
}


def feed(tracker: SessionTracker, samples: list[Sample]):
    for sample in samples:
        tracker.update(sample)
    return tracker


def continuous(samples: list[Sample], start: float) -> list[Sample]:
    for index, sample in enumerate(samples):
        sample.t = start + (index + 1) * 0.5
    return samples


class TestStateMachine(unittest.TestCase):
    def test_no_source_when_voltage_absent(self):
        tr = SessionTracker(CFG, v_target=5.0)
        samples = [Sample(t=k * 0.5, voltage=0.0, current=0.0, power=0.0) for k in range(10)]
        feed(tr, samples)
        self.assertEqual(tr.state, NO_SOURCE)

    def test_no_phone_after_three_seconds_of_leakage(self):
        tr = SessionTracker(CFG, v_target=5.0)
        samples = make_samples(n=10, current=0.005, period=0.5)
        feed(tr, samples)
        self.assertEqual(tr.state, NO_PHONE)
        self.assertTrue(all(s.state == NO_PHONE for s in samples))

    def test_low_current_becomes_charged_after_three_seconds(self):
        tr = SessionTracker(CFG, v_target=5.0)
        samples = make_samples(n=10, current=0.03, period=0.5)
        feed(tr, samples)
        self.assertEqual(tr.state, CHARGED)
        self.assertTrue(tr.ever_charged)
        self.assertTrue(all(s.state == CHARGED for s in samples))

    def test_charging_starts_when_powered_samples_arrive(self):
        tr = SessionTracker(CFG, v_target=5.0)
        samples = make_samples(n=10, current=1.0, period=0.5)
        first_state, first_event = tr.update(samples[0])
        feed(tr, samples[1:])
        self.assertEqual(first_state, CHARGING)
        self.assertEqual(first_event, "charging_start")
        self.assertEqual(tr.state, CHARGING)
        self.assertEqual(tr.started_at, 0.0)
        self.assertTrue(all(s.state == CHARGING for s in samples))

    def test_powered_start_is_not_reported_as_no_source(self):
        tr = SessionTracker(CFG, v_target=5.0)
        samples = make_samples(n=6, current=1.0, period=0.5)  # V is present
        feed(tr, samples)
        self.assertEqual(tr.state, CHARGING)
        self.assertTrue(all(s.state == CHARGING for s in samples))

    def test_charging_to_charged_is_reachable_after_low_current_tail(self):
        tr = SessionTracker(CFG, v_target=5.0)
        charging = make_samples(n=10, current=1.0, period=0.5)
        low_start = charging[-1].t
        low = continuous(make_samples(n=10, current=0.03, period=0.5), low_start)
        feed(tr, charging + low)
        self.assertEqual(tr.state, CHARGED)
        self.assertTrue(tr.ever_charged)
        self.assertTrue(all(s.state == CHARGED for s in low))

    def test_charged_can_return_to_charging_after_three_seconds(self):
        tr = SessionTracker(CFG, v_target=5.0)
        initial = make_samples(n=8, current=0.03, period=0.5)
        charging = continuous(make_samples(n=8, current=1.0, period=0.5), initial[-1].t)
        feed(tr, initial + charging)
        self.assertEqual(tr.state, CHARGING)
        self.assertTrue(all(s.state == CHARGING for s in charging))

    def test_charging_to_no_phone_is_debounced(self):
        tr = SessionTracker(CFG, v_target=5.0)
        charging = make_samples(n=8, current=1.0, period=0.5)
        leakage = continuous(make_samples(n=8, current=0.005, period=0.5), charging[-1].t)
        feed(tr, charging + leakage)
        self.assertEqual(tr.state, NO_PHONE)
        self.assertTrue(all(s.state == NO_PHONE for s in leakage))
        self.assertFalse(tr.ever_charged)

    def test_interrupted_transition_keeps_previous_state(self):
        tr = SessionTracker(CFG, v_target=5.0)
        charging = make_samples(n=8, current=1.0, period=0.5)
        low_candidate = continuous(make_samples(n=4, current=0.03, period=0.5), charging[-1].t)
        recovery = continuous(make_samples(n=8, current=1.0, period=0.5), low_candidate[-1].t)
        feed(tr, charging + low_candidate + recovery)
        self.assertEqual(tr.state, CHARGING)
        self.assertTrue(all(s.state == CHARGING for s in low_candidate))

    def test_no_phone_to_charged_is_debounced(self):
        tr = SessionTracker(CFG, v_target=5.0)
        initial = make_samples(n=8, current=0.005, period=0.5)
        low_current = continuous(make_samples(n=8, current=0.03, period=0.5), initial[-1].t)
        feed(tr, initial + low_current)
        self.assertEqual(tr.state, CHARGED)
        self.assertTrue(all(s.state == CHARGED for s in low_current))

    def test_open_candidate_when_phone_expected(self):
        tr = SessionTracker(CFG, v_target=5.0, phone_expected=True)
        samples = make_samples(n=70, current=0.005, period=0.5)
        feed(tr, samples)
        self.assertEqual(tr.state, NO_PHONE)
        self.assertTrue(tr.open_flag)
        self.assertEqual(tr.last_event, "open_candidate")

    def test_invalid_read_preserves_last_confirmed_state(self):
        tr = SessionTracker(CFG, v_target=5.0)
        candidate = make_samples(n=4, current=1.0, period=0.5)
        feed(tr, candidate)
        invalid = Sample(t=2.0, voltage=float("nan"), current=float("nan"), power=0.0, valid=False)
        tr.update(invalid)
        self.assertEqual(tr.state, CHARGING)
        self.assertEqual(invalid.state, CHARGING)

    def test_loss_of_vbus_is_immediate_no_source(self):
        tr = SessionTracker(CFG, v_target=5.0)
        charging = make_samples(n=8, current=1.0, period=0.5)
        feed(tr, charging)
        self.assertEqual(tr.state, CHARGING)
        sample = Sample(t=4.0, voltage=0.0, current=0.0, power=0.0)
        state, event = tr.update(sample)
        self.assertEqual((state, event), (NO_SOURCE, None))
        self.assertEqual(sample.state, NO_SOURCE)

    def test_fault_clears_candidate_and_remains_terminal(self):
        tr = SessionTracker(CFG, v_target=5.0)
        candidate = make_samples(n=4, current=1.0, period=0.5)
        feed(tr, candidate)
        fault = Sample(t=2.0, voltage=0.2, current=2.5, power=0.5)
        state, event = tr.update(fault)
        self.assertEqual((state, event), (FAULT, "fault"))
        later = Sample(t=3.0, voltage=5.0, current=1.0, power=5.0)
        self.assertEqual(tr.update(later)[0], FAULT)
        self.assertEqual(later.state, FAULT)

    def test_current_band_boundaries(self):
        cases = ((0.009, NO_PHONE), (0.010, CHARGED), (0.099, CHARGED), (0.100, CHARGING))
        for current, expected in cases:
            tr = SessionTracker(CFG, v_target=5.0)
            samples = make_samples(n=8, current=current, period=0.5)
            feed(tr, samples)
            self.assertEqual(tr.state, expected, f"current={current}")

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
        self.assertEqual(tr.state, NO_SOURCE)

    def test_samples_are_stamped_with_current_confirmed_state(self):
        tr = SessionTracker(CFG, v_target=5.0)
        samples = make_samples(n=10, current=1.0, period=0.5)
        for sample in samples:
            tr.update(sample)
        self.assertTrue(all(s.state == CHARGING for s in samples))
        self.assertEqual(OPEN, "OPEN")


if __name__ == "__main__":
    unittest.main()
