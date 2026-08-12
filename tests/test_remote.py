"""Tests for the dependency-free Supabase outbox synchronizer."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import make_samples

from src.telemetry.models import SessionMeta
from src.telemetry.remote import (
    SupabaseRestClient,
    client_from_config,
    load_env_file,
    sync_pending,
)
from src.telemetry.storage import Storage


class RecordingClient:
    def __init__(self):
        self.sessions = []
        self.sample_batches = []

    def insert_session(self, row):
        self.sessions.append(row)

    def insert_samples(self, rows):
        self.sample_batches.append(rows)


class FailingClient:
    def insert_session(self, row):
        raise RuntimeError("network unavailable")

    def insert_samples(self, rows):
        raise AssertionError("sample upload must not follow a failed session upload")


class FakeResponse:
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return b""


class TestEnvLoading(unittest.TestCase):
    def test_loads_dotenv_entries_and_preserves_shell_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "# comment\nexport SUPABASE_URL='https://from-file'\n"
                "SUPABASE_PUBLISHABLE_KEY=file-key\nDEVICE_ID=pi-from-file\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"SUPABASE_URL": "https://from-shell"}, clear=False):
                for name in ("SUPABASE_PUBLISHABLE_KEY", "DEVICE_ID"):
                    os.environ.pop(name, None)
                load_env_file(env_path)
                self.assertEqual(os.environ["SUPABASE_URL"], "https://from-shell")
                self.assertEqual(os.environ["SUPABASE_PUBLISHABLE_KEY"], "file-key")
                self.assertEqual(os.environ["DEVICE_ID"], "pi-from-file")

    def test_client_from_config_loads_dotenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "SUPABASE_URL=https://from-file\nSUPABASE_PUBLISHABLE_KEY=file-key\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True), patch("src.telemetry.remote.Path", return_value=env_path):
                client = client_from_config({"remote": {"enabled": True}})
            self.assertIsNotNone(client)
            self.assertEqual(client.base_url, "https://from-file")
            self.assertEqual(client.api_key, "file-key")


class TestRemoteSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(self.tmp.name)
        self.meta = SessionMeta(
            session_id="remote-session",
            mode="charge",
            v_target=5.0,
            length_m=1.0,
        )
        self.sid = self.storage.new_session(self.meta)
        self.samples = make_samples(n=7)
        self.storage.add_samples(self.sid, self.samples)
        self.storage.save_verdict(self.sid, {"grade": "B"}, self.meta)
        self.storage.enqueue_remote(self.sid)
        self.cfg = {
            "remote": {
                "enabled": True,
                "device_id": "pi-zero-2w-01",
                "batch_size": 3,
                "retry_base_s": 0.0,
            }
        }

    def tearDown(self):
        self.storage.close()
        self.tmp.cleanup()

    def test_sync_uploads_metadata_and_bounded_batches(self):
        client = RecordingClient()
        result = sync_pending(self.storage, self.cfg, client)

        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["pending"], 0)
        self.assertEqual(result["configured"], 1)
        self.assertEqual(len(client.sessions), 1)
        self.assertEqual(client.sessions[0]["device_id"], "pi-zero-2w-01")
        self.assertEqual([len(batch) for batch in client.sample_batches], [3, 3, 1])
        uploaded = [row for batch in client.sample_batches for row in batch]
        self.assertEqual([row["sample_index"] for row in uploaded], list(range(7)))
        self.assertEqual(uploaded[0]["voltage_v"], self.samples[0].voltage)
        self.assertEqual(uploaded[0]["current_a"], self.samples[0].current)
        status = self.storage.conn.execute(
            "SELECT status FROM remote_queue WHERE session_id=?", (self.sid,)
        ).fetchone()[0]
        self.assertEqual(status, "complete")

    def test_failure_remains_pending_for_a_later_run(self):
        result = sync_pending(self.storage, self.cfg, FailingClient())

        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["pending"], 1)
        row = self.storage.conn.execute(
            "SELECT status, attempt_count, last_error FROM remote_queue WHERE session_id=?",
            (self.sid,),
        ).fetchone()
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["attempt_count"], 1)
        self.assertIn("network unavailable", row["last_error"])

    def test_deferred_retry_is_not_reported_as_missing_credentials(self):
        self.cfg["remote"]["retry_base_s"] = 60.0
        first = sync_pending(self.storage, self.cfg, FailingClient())
        self.assertEqual(first["failed"], 1)

        second = sync_pending(self.storage, self.cfg, RecordingClient())
        self.assertEqual(second["attempted"], 0)
        self.assertEqual(second["pending"], 1)
        self.assertEqual(second["configured"], 1)

    def test_rest_client_sends_required_headers_and_json(self):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse()

        client = SupabaseRestClient(
            "https://example.supabase.co",
            "public-test-key",
            opener=opener,
            retry_base_s=0.0,
        )
        client.insert_samples([{
            "session_id": "s1",
            "device_id": "pi-zero-2w-01",
            "sample_index": 0,
            "t": 0.0,
            "voltage_v": 5.0,
            "current_a": 1.0,
            "power_w": 5.0,
            "state": "CHARGING",
            "valid": True,
        }])

        request, timeout = requests[0]
        self.assertEqual(timeout, 10.0)
        self.assertIn("/rest/v1/samples", request.full_url)
        self.assertIn("on_conflict=session_id%2Csample_index", request.full_url)
        self.assertEqual(request.get_header("Apikey"), "public-test-key")
        self.assertEqual(request.get_header("Authorization"), "Bearer public-test-key")
        self.assertIn("ignore-duplicates", request.get_header("Prefer"))
        self.assertEqual(json.loads(request.data.decode("utf-8"))[0]["sample_index"], 0)


if __name__ == "__main__":
    unittest.main()
