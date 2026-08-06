"""Unit tests for src/telemetry/session.py (DEVELOPMENT_PLAN.md §3 state machine)."""
import unittest

from tests.helpers import make_samples

from src.telemetry.models import Sample
from src.telemetry.session import CHARGED, CHARGING, FAULT, IDLE, NO_SOURCE, OPEN, SessionTracker

CFG = {
    "session": {
        "i_no_load": 0.05,
        "i_charge_start": 0.10,
        "debounce_start_s": 5.0,
        "debounce_end_s": 60.0,
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

    def test_idle_when_power_present_no_load(self):
        tr = SessionTracker(CFG, v_target=5.0)
        feed(tr, make_samples(n=10, current=0.0))
        self.assertEqual(tr.state, IDLE)  # V~5 V present, I=0 -> not charging

    def test_charging_starts_after_debounce(self):
        tr = SessionTracker(CFG, v_target=5.0)
        samples = make_samples(n=20, current=1.0, period=0.5)  # 10 s > 5 s debounce
        feed(tr, samples)
        self.assertEqual(tr.state, CHARGING)
        self.assertEqual(tr.started_at, 5.0)  # debounce_start reached at t=5 s

    def test_charging_does_not_start_early(self):
        tr = SessionTracker(CFG, v_target=5.0)
        feed(tr, make_samples(n=4, current=1.0, period=0.5))  # only 2 s
        self.assertEqual(tr.state, IDLE)

    def test_charged_after_low_current_tail(self):
        tr = SessionTracker(CFG, v_target=5.0)
        # 10 s charging then 61 s of I~0 -> CHARGED (user rule: I=0 after readings = charged)
        samples = make_samples(n=20, current=1.0, period=0.5)  # 10 s charging
        samples += make_samples(n=122, current=0.01, period=0.5, state="CHARGING")  # 61 s tail
        feed(tr, samples)
        self.assertEqual(tr.state, CHARGED)
        self.assertTrue(tr.ever_charged)

    def test_open_candidate_when_phone_expected(self):
        tr = SessionTracker(CFG, v_target=5.0, phone_expected=True)
        samples = make_samples(n=70, current=0.0, period=0.5)  # 35 s IDLE, no load
        feed(tr, samples)
        self.assertEqual(tr.state, IDLE)
        self.assertTrue(tr.open_flag)
        self.assertEqual(tr.last_event, "open_candidate")

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
