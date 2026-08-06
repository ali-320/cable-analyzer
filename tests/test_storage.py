"""Unit tests for src/telemetry/storage.py."""
import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import make_samples

from src.telemetry.models import SessionMeta
from src.telemetry.storage import Storage


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(self.tmp.name)

    def tearDown(self):
        self.storage.close()
        self.tmp.cleanup()

    def test_roundtrip(self):
        meta = SessionMeta(mode="charge", v_target=5.0, length_m=1.0, charging_detected=True)
        sid = self.storage.new_session(meta)
        self.assertTrue(sid)

        samples = make_samples(n=50)
        self.storage.add_samples(sid, samples)

        verdict = {"session_id": sid, "grade": "B", "tags": ["HIGH_LOSS"]}
        self.storage.save_verdict(sid, verdict, meta)

        rows = self.storage.list_sessions()
        self.assertEqual(len(rows), 1)
        stored = json.loads(rows[0]["verdict_json"])
        self.assertEqual(stored["grade"], "B")

        csv_path = self.storage.export_csv(sid, samples)
        self.assertTrue(Path(csv_path).exists())
        self.assertGreater(Path(csv_path).stat().st_size, 0)
        first_line = Path(csv_path).read_text().splitlines()[0]
        self.assertIn("voltage_V", first_line)

    def test_probe_json_saved(self):
        meta = SessionMeta(mode="probe")
        sid = self.storage.new_session(meta)
        self.storage.save_probe(sid, {"steps": [{"i": 1.0, "r_loop_mohm": 200.0}]})
        row = self.storage.conn.execute(
            "SELECT probe_json FROM sessions WHERE session_id=?", (sid,)
        ).fetchone()
        self.assertIn("r_loop_mohm", row["probe_json"])


if __name__ == "__main__":
    unittest.main()
